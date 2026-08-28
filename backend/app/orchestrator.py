"""
Orchestrator — the single state machine every agent call routes through.
No agent calls another agent directly; they only ever return to this layer,
which decides the next step, enforces the retry cap, and writes every step
to the audit log. This is what makes the pipeline explainable: one place
to look for "what happened and why" instead of scattered agent-to-agent
handoffs.
"""
from app.agents.query_agent import run_query_agent
from app.agents.fetch_agent import run_fetch_agent
from app.agents.reasoning_agent import run_reasoning_agent, recompute_and_verify
from app.gate import gate_check
from app.audit import log_event
from app.config import MAX_QUERY_RETRIES
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


def run_search_pipeline(session_id: str, user_message: str) -> dict:
    """
    Runs query -> fetch -> reasoning, auto-broadening up to MAX_QUERY_RETRIES
    times. Returns one of:
      - status="proposal": a match ready for user confirmation,
      - status="relax": no full match, plus concrete per-request adjustments
        the user can trigger as a fresh search (never a flat failure),
      - status="error": a guardrail rejection anywhere in the pipeline, shown
        to the user as one friendly message (the raw error is logged, not shown).
    """
    log_event(session_id, "user_instruction", {"message": user_message})

    try:
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

            decision = run_reasoning_agent(query, primary, fallback)
            decision = recompute_and_verify(decision, query, primary, fallback)
            log_event(session_id, "reasoning_agent", {
                "attempt": attempt,
                "match_score": decision.match_score,
                "decision": decision.model_dump(),
            })

            if decision.decision == "proceed":
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

        # Not proceeding: never a flat failure. Hand the user concrete,
        # per-request relaxation options they can trigger as a fresh search.
        relaxations = [r.model_dump() for r in (decision.relaxations if decision else [])]
        log_event(session_id, "relaxation_offered", {
            "match_score": decision.match_score if decision else 0,
            "relaxations": relaxations,
        })
        return {
            "status": "relax",
            "message": "I couldn't confirm a match that meets everything you asked for. Here are ways to adjust:",
            "note": decision.reasoning_note if decision else "",
            "match_score": decision.match_score if decision else 0,
            "relaxations": relaxations,
        }

    except Exception as e:
        # Graceful guardrail handling: any pydantic ValidationError, LLM JSON
        # parse failure, or other rejection in query/fetch/reasoning is logged
        # raw (server console + audit) but shown to the user as one friendly
        # message — the pipeline never leaks a stack trace to the shopper.
        log_event(session_id, "guardrail_error", {"error_type": type(e).__name__, "error": str(e)})
        print(f"[guardrail_error] {type(e).__name__}: {e}")
        return {"status": "error", "message": FRIENDLY_ERROR}


def confirm_and_gate(session_id: str) -> dict:
    """First confirmation checkpoint -> deterministic gate."""
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


def create_upsell_order(session_id: str, sku: str):
    """
    Accepted upsell -> runs the SAME gate + order agent as any other
    purchase, just for a single item whose budget cap is its own price
    (an upsell is, by definition, already within the user's remaining
    headroom — the upsell agent already checked that before suggesting it).
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
        # shouldn't happen for a small upsell item, but never silently skip the check
        raise ValueError("Upsell amount unexpectedly requires OTP — refusing to auto-approve.")

    order = run_order_agent(session_id, result.gate_token)
    log_event(session_id, "order_agent_upsell", order.model_dump())
    return order


def cancel_payment(session_id: str, razorpay_order_id: str | None = None) -> dict:
    """
    User dismissed/cancelled the Razorpay checkout before it completed.
    If an order was already created, mark it 'cancelled', and always record
    the cancellation in the audit trail so the abandoned attempt is explainable
    rather than silently disappearing.
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
