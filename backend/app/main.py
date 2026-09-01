from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import CORS_ORIGINS, RAZORPAY_KEY_ID, BUDGET_AUTO_APPROVE_LIMIT, SHOW_OTP_IN_RESPONSE
from app import orchestrator
from app.agents.order_agent import run_order_agent
from app.agents.payment_agent import run_payment_agent
from app.agents.upsell_agent import run_upsell_agent
from app.gate import verify_otp, resend_otp
from app.catalog import get_product_by_sku, list_products
from app.guardrails.schemas import PaymentVerification
from app.audit import log_event, get_trail

app = FastAPI(title="Agentic Commerce Buildathon API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    # Indicates if the message originated from a relaxation prompt.
    # Used by the orchestrator to enforce relaxation attempt limits.
    from_relaxation: bool = False


class ConfirmOrderRequest(BaseModel):
    session_id: str


class VerifyOtpRequest(BaseModel):
    session_id: str
    gate_token: str
    code: str


class ResendOtpRequest(BaseModel):
    session_id: str
    gate_token: str


class UpsellRespondRequest(BaseModel):
    session_id: str
    sku: str
    accepted: bool


class CancelPaymentRequest(BaseModel):
    session_id: str
    razorpay_order_id: str | None = None


@app.get("/api/config")
def get_config():
    """Retrieves the public Razorpay key ID for the frontend checkout widget."""
    return {"razorpay_key_id": RAZORPAY_KEY_ID}


@app.get("/api/catalog")
def catalog(category: str | None = None, max_price: float | None = None):
    """
    Agent-readable catalog endpoint.
    Allows external AI buyer agents to access product availability and pricing without navigating the chat interface.
    """
    return {"products": list_products(category=category, max_price=max_price)}


@app.get("/.well-known/agent-manifest.json")
def agent_manifest():
    """
    Merchant discovery document.
    Provides external agents with integration details such as catalog endpoints, checkout flows, and store policies.
    """
    return {
        "merchant": "Wick & Wax (Razorpay test-mode demo)",
        "protocol_version": "0.1",
        "catalog": {
            "url": "/api/catalog",
            "method": "GET",
            "query_params": ["category", "max_price"],
        },
        "checkout": {
            "start": {"url": "/api/chat", "method": "POST", "body": {"session_id": "string|null", "message": "string"}},
            "confirm": {"url": "/api/confirm-order", "method": "POST", "body": {"session_id": "string"}},
            "otp_verify": {"url": "/api/verify-otp", "method": "POST", "body": {"session_id": "string", "gate_token": "string", "code": "string"}},
            "payment_verify": {"url": "/api/verify-payment", "method": "POST", "body": {"session_id": "string", "razorpay_order_id": "string", "razorpay_payment_id": "string", "razorpay_signature": "string"}},
        },
        "policy": {
            "currency": "INR",
            "auto_approve_limit": BUDGET_AUTO_APPROVE_LIMIT,
            "otp_required_above_auto_approve_limit": True,
            "every_transaction_audited": True,
            "audit_trail_url": "/api/audit/{session_id}",
        },
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        session_id = orchestrator.start_or_continue_session(req.session_id, req.message)
        result = orchestrator.run_search_pipeline(session_id, req.message, from_relaxation=req.from_relaxation)
        result["session_id"] = session_id
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/confirm-order")
def confirm_order(req: ConfirmOrderRequest):
    """
    Initial confirmation checkpoint invoking the deterministic gate.
    Returns a gate_token for orders exceeding the auto-approve limit, deferring order creation until OTP verification.
    """
    try:
        gate_result = orchestrator.confirm_and_gate(req.session_id)
        if not gate_result["approved"]:
            return {"approved": False, "reason": gate_result["reason"]}

        if gate_result["requires_otp"]:
            log_event(req.session_id, "otp_challenge_issued", {"amount": gate_result["verified_total"]})
            resp = {
                "approved": True,
                "requires_otp": True,
                "gate_token": gate_result["gate_token"],
            }
            if SHOW_OTP_IN_RESPONSE:
                resp["otp_code"] = gate_result["otp_code"]
            return resp

        order = run_order_agent(req.session_id, gate_result["gate_token"])
        log_event(req.session_id, "order_agent", order.model_dump())
        return {
            "approved": True,
            "requires_otp": False,
            "razorpay_order_id": order.razorpay_order_id,
            "amount": order.amount,
            "sku": order.sku,
            "quantity": order.quantity,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/verify-otp")
def verify_otp_endpoint(req: VerifyOtpRequest):
    """
    Secondary checkpoint for high-value transactions.
    Order creation is deferred until OTP validation is successful.
    """
    ok = verify_otp(req.gate_token, req.code)
    log_event(req.session_id, "otp_verification", {"success": ok})
    if not ok:
        return {"approved": False, "reason": "Incorrect or expired code."}

    try:
        order = run_order_agent(req.session_id, req.gate_token)
        log_event(req.session_id, "order_agent", order.model_dump())
        return {
            "approved": True,
            "requires_otp": False,
            "razorpay_order_id": order.razorpay_order_id,
            "amount": order.amount,
            "sku": order.sku,
            "quantity": order.quantity,
        }
    except PermissionError as e:
        return {"approved": False, "reason": str(e)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/resend-otp")
def resend_otp_endpoint(req: ResendOtpRequest):
    """
    Reissues an OTP code for an existing gate_token.
    Used when the initial code expires, maintaining the approved amount and SKU without triggering a new gate evaluation.
    """
    result = resend_otp(req.gate_token)
    log_event(req.session_id, "otp_resent", {"success": result["success"]})
    if not result["success"]:
        return {"success": False, "reason": result["reason"]}

    resp = {"success": True}
    if SHOW_OTP_IN_RESPONSE:
        resp["otp_code"] = result["otp_code"]
    return resp


@app.post("/api/verify-payment")
def verify_payment(verification: PaymentVerification):
    """
    Server-side payment verification endpoint.
    Validates the Razorpay signature, confirms payment capture, and initiates the upsell agent for revenue growth opportunities.
    """
    try:
        receipt = run_payment_agent(verification)
        log_event(verification.session_id, "payment_agent", receipt)

        budget_cap = orchestrator.get_budget_cap(verification.session_id)
        remaining = max(budget_cap - receipt["amount"], 0)
        suggestion = run_upsell_agent(receipt["sku"], remaining)
        log_event(verification.session_id, "upsell_agent", suggestion.model_dump())

        upsell = suggestion.model_dump()
        if upsell["suggest"] and upsell["sku"]:
            product = get_product_by_sku(upsell["sku"])
            if product:
                upsell["name"] = product["name"]
                upsell["price"] = product["price"]
            else:
                upsell["suggest"] = False
        receipt["upsell"] = upsell

        return receipt
    except ValueError as e:
        log_event(verification.session_id, "payment_failed", {"error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upsell-respond")
def upsell_respond(req: UpsellRespondRequest):
    """
    Logs upsell accept/decline actions.
    If accepted, initiates a secondary order via the deterministic gate and order agent.
    """
    log_event(req.session_id, "upsell_response", {"sku": req.sku, "accepted": req.accepted})
    if not req.accepted:
        return {"accepted": False}

    try:
        result = orchestrator.gate_upsell_order(req.session_id, req.sku)
        if result["requires_otp"]:
            resp = {"accepted": True, "requires_otp": True, "gate_token": result["gate_token"]}
            if SHOW_OTP_IN_RESPONSE:
                resp["otp_code"] = result["otp_code"]
            return resp

        order = result["order"]
        return {
            "accepted": True,
            "requires_otp": False,
            "razorpay_order_id": order.razorpay_order_id,
            "amount": order.amount,
            "sku": order.sku,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/cancel-payment")
def cancel_payment(req: CancelPaymentRequest):
    """
    Handles payment cancellation initiated by the user.
    Updates the order status and logs the event for audit traceability.
    """
    try:
        return orchestrator.cancel_payment(req.session_id, req.razorpay_order_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/audit/{session_id}")
def audit_trail(session_id: str):
    return {"session_id": session_id, "events": get_trail(session_id)}


@app.get("/api/health")
def health():
    return {"status": "ok"}