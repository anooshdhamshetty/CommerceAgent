"""
Orchestrator: Central state machine routing all agent calls.
Manages step transitions, retry limits, and audit logging to maintain an explainable, centralized execution pipeline.
"""
from app.agents.query_agent import run_query_agent
from app.agents.fetch_agent import run_fetch_agent
from app.agents.reasoning_agent import run_reasoning_agent, recompute_and_verify
from app.gate import gate_check
from app.audit import log_event
from app.config import MAX_QUERY_RETRIES, MAX_RELAX_ATTEMPTS
from app.supabase_client import fallback_store, new_id, table, is_connected

FRIENDLY_ERROR = (
    "I had trouble understanding that request. Could you rephrase it? "
    "For example: 'a pair of Sony earbuds under ₹3000'."
)


def _session_store():
    return fallback_store()["sessions"]


def start_or_continue_session(session_id: str | None, user_message: str) -> str:
    sessions = _session_store()
    if not session_id or session_id not in sessions:
        session_id = session_id or new_id()
        sessions[session_id] = {"history": []}
    sessions[session_id]["history"].append({"role": "user", "message": user_message})
    return session_id


def run_search_pipeline(session_id: str, user_message: str, from_relaxation: bool = False) -> dict:
    """
    Executes the search pipeline (query -> fetch -> reasoning) with automatic retry logic.
    Returns the search outcome:
      - 'proposal': Successful match pending user confirmation.
      - 'relax': Partial match with suggested adjustments.
      - 'error': Guardrail failure encountered during execution.

    Args:
        session_id: Active session identifier.
        user_message: Input text from the user.
        from_relaxation: Boolean indicating if the request originated from a relaxation prompt.
                         Caps consecutive relaxation loops to MAX_RELAX_ATTEMPTS.
    """
    log_event(session_id, "user_instruction", {"message": user_message, "from_relaxation": from_relaxation})

    try:
        # Track relaxation attempts per session to enforce retry limits.
        session = _session_store().setdefault(session_id, {"history": []})
        if from_relaxation:
            session["relax_attempts"] = session.get("relax_attempts", 0) + 1
        else:
            session["relax_attempts"] = 0
        relax_attempts = session["relax_attempts"]

        broaden_hint = None
        decision = None
        for attempt in range(1, MAX_QUERY_RETRIES + 1):
            query = run_query_agent(user_message, broaden_hint=broaden_hint)
            log_event(session_id, "query_agent", {"attempt": attempt, "query": query.model_dump()})

            primary, fallback = run_fetch_agent(query)
            log_event(session_id, "fetch_agent", {
                "attempt": attempt,
                "primary": [p.model_dump() for p in primary],
                "fallback": [p.model_dump() for p in fallback],
            })

            decision = run_reasoning_agent(user_message, query, primary, fallback)
            decision = recompute_and_verify(decision, query, primary, fallback)
            log_event(session_id, "reasoning_agent", {
                "attempt": attempt,
                "match_score": decision.match_score,
                "decision": decision.model_dump(),
            })

            if decision.decision == "proceed":
                # Reset relaxation counter upon successful proposal generation.
                session["relax_attempts"] = 0
                candidates = primary + [f for f in fallback if f.sku not in {p.sku for p in primary}]
                matched = next(p for p in candidates if p.sku == decision.matched_sku)
                _session_store()[session_id]["pending_proposal"] = {
                    "query": query.model_dump(),
                    "decision": decision.model_dump(),
                    "product": matched.model_dump(),
                }
                return {
                    "status": "proposal",
                    "sku": matched.sku,
                    "name": matched.name,
                    "quantity": decision.matched_quantity,
                    "unit_price": matched.price,
                    "total_amount": decision.total_amount,
                    "delivery_days": matched.delivery_days,
                    "exact_match": decision.exact_match,
                    "match_score": decision.match_score,
                    "note": decision.reasoning_note,
                }

            if decision.decision == "retry_broader" and attempt < MAX_QUERY_RETRIES:
                broaden_hint = decision.next_search_hint or "search more broadly, drop specific qualifiers"
                continue

            break

        # Handle non-proceeding states: provide relaxation options or terminate with a final message if attempts are exhausted.
        if from_relaxation and relax_attempts >= MAX_RELAX_ATTEMPTS:
            reason = (decision.reasoning_note if decision else "").strip()
            msg = ("I tried a few different options but still couldn't find something "
                   "that matches everything you asked for.")
            if reason:
                msg = f"{reason} {msg}"
            msg += " Feel free to start a new search with different requirements."
            log_event(session_id, "relaxation_exhausted", {
                "attempts": relax_attempts,
                "match_score": decision.match_score if decision else 0,
            })
            return {
                "status": "relax",
                "exhausted": True,
                "message": msg,
                "note": reason,
                "match_score": decision.match_score if decision else 0,
                "relaxations": [],
            }

        relaxations = [r.model_dump() for r in (decision.relaxations if decision else [])]
        log_event(session_id, "relaxation_offered", {
            "attempt": relax_attempts,
            "match_score": decision.match_score if decision else 0,
            "relaxations": relaxations,
        })
        return {
            "status": "relax",
            "exhausted": False,
            "message": "I couldn't confirm a match that meets everything you asked for. Here are ways to adjust:",
            "note": decision.reasoning_note if decision else "",
            "match_score": decision.match_score if decision else 0,
            "relaxations": relaxations,
        }

    except Exception as e:
        # Catch and log all pipeline errors (e.g., validation or parsing failures).
        # Surface only a friendly generic message to the user.
        log_event(session_id, "guardrail_error", {"error_type": type(e).__name__, "error": str(e)})
        print(f"[guardrail_error] {type(e).__name__}: {e}")
        return {"status": "error", "message": FRIENDLY_ERROR}


def confirm_and_gate(session_id: str) -> dict:
    """First confirmation checkpoint invoking deterministic gate."""
    session = _session_store().get(session_id)
    if not session or "pending_proposal" not in session:
        raise ValueError("No pending proposal for this session.")

    proposal = session["pending_proposal"]
    from app.guardrails.schemas import ReasoningDecision, FetchedProduct

    decision = ReasoningDecision(**proposal["decision"])
    product = FetchedProduct(**proposal["product"])
    budget_cap = proposal["query"]["budget_cap"]

    log_event(session_id, "user_confirmation_order", {"confirmed": True})

    result = gate_check(decision, product, budget_cap)
    log_event(session_id, "deterministic_gate", result.model_dump())

    if result.approved:
        session["gate_result"] = result.model_dump()
    return result.model_dump()


def get_budget_cap(session_id: str) -> float:
    session = _session_store().get(session_id, {})
    proposal = session.get("pending_proposal")
    return proposal["query"]["budget_cap"] if proposal else 0


def gate_upsell_order(session_id: str, sku: str) -> dict:
    """
    Passes an accepted upsell through the standard deterministic gate.
    Returns standard gate result (requires_otp, gate_token) so the frontend
    can seamlessly reuse the existing OTP verification flow if the upsell
    exceeds the auto-approve limit.
    """
    from app.catalog import get_product_by_sku
    from app.guardrails.schemas import FetchedProduct, ReasoningDecision
    from app.agents.order_agent import run_order_agent

    row = get_product_by_sku(sku)
    if row is None:
        raise ValueError("Upsell product not found in catalog.")

    product = FetchedProduct(**row)
    decision = ReasoningDecision(
        decision="proceed",
        matched_sku=sku,
        matched_quantity=1,
        total_amount=product.price,
        reasoning_note="upsell accepted by user",
    )

    result = gate_check(decision, product, budget_cap=product.price)
    log_event(session_id, "deterministic_gate_upsell", result.model_dump())
    if not result.approved:
        raise ValueError(result.reason)

    if result.requires_otp:
        log_event(session_id, "otp_challenge_issued", {"amount": result.verified_total, "context": "upsell"})
        return {"requires_otp": True, "gate_token": result.gate_token, "otp_code": result.otp_code}

    order = run_order_agent(session_id, result.gate_token)
    log_event(session_id, "order_agent_upsell", order.model_dump())
    return {"requires_otp": False, "order": order}

def cancel_payment(session_id: str, razorpay_order_id: str | None = None) -> dict:
    """
    Handles Razorpay checkout cancellation.
    Updates existing order status to 'cancelled' and logs the event to the audit trail.
    """
    if razorpay_order_id:
        if is_connected():
            table("orders").update({"status": "cancelled"}).eq("razorpay_order_id", razorpay_order_id).execute()
        else:
            order = fallback_store()["orders"].get(razorpay_order_id)
            if order:
                order["status"] = "cancelled"

    log_event(session_id, "payment_cancelled", {"razorpay_order_id": razorpay_order_id})
    return {"status": "cancelled", "razorpay_order_id": razorpay_order_id}
