"""
Deterministic authorization gate.
Contains auditable, non-LLM logic for money movement and transaction approval.
Persists gate tokens and OTP challenges to Supabase (or in-memory fallback) to prevent mid-flow data loss.
"""
import random
import datetime
from app.config import BUDGET_AUTO_APPROVE_LIMIT
from app.guardrails.schemas import ReasoningDecision, FetchedProduct, GateResult
from app.supabase_client import table, is_connected, fallback_store, new_id

TOKEN_TTL_MINUTES = 10


def _store_token(token: str, data: dict):
    row = {**data, "token": token}
    if is_connected():
        table("gate_tokens").insert(row).execute()
    else:
        fallback_store()["gate_tokens"][token] = data


def _read_token(token: str):
    if is_connected():
        res = table("gate_tokens").select("*").eq("token", token).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    return fallback_store()["gate_tokens"].get(token)


def _update_token(token: str, updates: dict):
    if is_connected():
        table("gate_tokens").update(updates).eq("token", token).execute()
    else:
        d = fallback_store()["gate_tokens"].get(token)
        if d:
            d.update(updates)


def _delete_token(token: str):
    if is_connected():
        table("gate_tokens").delete().eq("token", token).execute()
    else:
        fallback_store()["gate_tokens"].pop(token, None)


def _is_expired(data: dict) -> bool:
    exp = data.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.datetime.fromisoformat(exp.replace("Z", "")) < datetime.datetime.utcnow()
    except Exception:
        return False


def gate_check(decision: ReasoningDecision, product: FetchedProduct, budget_cap: float) -> GateResult:
    if decision.decision != "proceed":
        return GateResult(approved=False, reason=f"reasoning agent did not propose proceeding: {decision.decision}")

    if decision.matched_quantity <= 0:
        return GateResult(approved=False, reason="quantity must be positive")

    verified_total = round(product.price * decision.matched_quantity, 2)

    if product.stock < decision.matched_quantity:
        return GateResult(approved=False, reason="stock check failed at gate", verified_total=verified_total)

    if verified_total > budget_cap:
        return GateResult(approved=False, reason="exceeds user budget cap", verified_total=verified_total)

    requires_otp = verified_total > BUDGET_AUTO_APPROVE_LIMIT
    otp_code = f"{random.randint(0, 999999):06d}" if requires_otp else None
    token = new_id()
    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(minutes=TOKEN_TTL_MINUTES)).isoformat()

    _store_token(token, {
        "sku": product.sku,
        "quantity": decision.matched_quantity,
        "amount": verified_total,
        "requires_otp": requires_otp,
        "otp_code": otp_code,
        "otp_verified": not requires_otp,
        "expires_at": expires_at,
    })

    if requires_otp:
        # Simulated out-of-band OTP delivery for demo purposes.
        print(f"[OTP] amount ₹{verified_total} above auto-approve limit — code: {otp_code}")

    return GateResult(
        approved=True,
        requires_otp=requires_otp,
        reason="within budget, stock confirmed" + (", OTP required above auto-approve limit" if requires_otp else ""),
        gate_token=token,
        verified_total=verified_total,
        otp_code=otp_code,
    )


def verify_otp(token: str, code: str) -> bool:
    data = _read_token(token)
    if not data or _is_expired(data):
        return False
    if data.get("otp_verified"):
        return True
    if data.get("otp_code") == code:
        _update_token(token, {"otp_verified": True})
        return True
    return False


def resend_otp(token: str) -> dict:
    """
    Regenerates OTP for an existing gate_token, extending expiry by TOKEN_TTL_MINUTES.
    Reissues the challenge without altering the originally approved amount, SKU, or quantity.
    """
    data = _read_token(token)
    if not data:
        # Token missing or consumed by a completed order.
        return {"success": False, "reason": "This order session has expired. Please confirm the order again."}
    if not data.get("requires_otp"):
        return {"success": False, "reason": "This order does not require an OTP."}
    if data.get("otp_verified"):
        return {"success": False, "reason": "This code was already verified."}

    new_code = f"{random.randint(0, 999999):06d}"
    new_expiry = (datetime.datetime.utcnow() + datetime.timedelta(minutes=TOKEN_TTL_MINUTES)).isoformat()
    _update_token(token, {"otp_code": new_code, "expires_at": new_expiry})

    print(f"[OTP] resent for token {token} — new code: {new_code}")
    return {"success": True, "otp_code": new_code}


def consume_gate_token(token: str):
    """
    Validates and consumes a gate token.
    Ensures token is single-use, unexpired, and OTP-verified if required.
    """
    data = _read_token(token)
    if not data or _is_expired(data):
        _delete_token(token)
        return None
    if data.get("requires_otp") and not data.get("otp_verified"):
        return None
    _delete_token(token)
    return data