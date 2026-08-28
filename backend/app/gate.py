"""
Deterministic gate. No LLM call happens anywhere in this file — this is the
one function that actually authorizes money movement, and it's plain,
auditable code so its behavior can be proven, not just described.

Gate tokens (and any OTP challenge tied to them) are persisted to Supabase
when connected, so a backend restart mid-flow doesn't silently drop a
pending approval. Falls back to in-memory storage otherwise.
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
        # Simulated SMS/email gateway. In production this is sent out-of-band
        # and never returned in an API response — for the demo it's printed
        # to the server console so it can be read off during judging.
        print(f"[OTP] amount ₹{verified_total} above auto-approve limit — code: {otp_code}")

    return GateResult(
        approved=True,
        requires_otp=requires_otp,
        reason="within budget, stock confirmed" + (", OTP required above auto-approve limit" if requires_otp else ""),
        gate_token=token,
        verified_total=verified_total,
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


def consume_gate_token(token: str):
    """Order agent calls this — a token can only be used once, must not be
    expired, and if it required OTP, that OTP must already be verified."""
    data = _read_token(token)
    if not data or _is_expired(data):
        _delete_token(token)
        return None
    if data.get("requires_otp") and not data.get("otp_verified"):
        return None
    _delete_token(token)
    return data
