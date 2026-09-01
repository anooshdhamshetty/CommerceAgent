"""
Guardrail schemas defining agent I/O boundaries.
LLM outputs must strictly validate against these Pydantic models before orchestrator ingestion, ensuring malformed data is rejected immediately.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class ProductQuery(BaseModel):
    """Guardrail on the Query agent's output."""
    category: str = Field(min_length=1, max_length=50)
    # Specific product noun (e.g., "laptop", "usb c cable"), retained as extracted.
    # Evaluated against candidate names for precise matching, independent of broader category taxonomy.
    product_type: str = Field(min_length=1, max_length=80)
    attribute: Optional[str] = Field(default=None, max_length=50)
    # Explicitly named brand parameter. Retains null unless specified by the user.
    brand: Optional[str] = Field(default=None, max_length=50)
    quantity: int = Field(gt=0, le=20)
    budget_cap: float = Field(gt=0, le=1_000_000_000)

    @field_validator("category", "product_type", "attribute", "brand")
    @classmethod
    def no_control_chars(cls, v):
        if v and any(ord(c) < 32 for c in v):
            raise ValueError("control characters not allowed")
        return v
class FetchedProduct(BaseModel):
    """Guardrail on each item the Fetch agent returns from the catalog."""
    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price: float = Field(gt=0)
    stock: int = Field(ge=0)
    delivery_days: int = Field(ge=0, le=60)
    # Original catalog attributes preserved for deterministic scoring by the reasoning agent.
    category: Optional[str] = None
    attribute: Optional[str] = None
    brand: Optional[str] = None


class RelaxationOption(BaseModel):
    """
    Defines a specific, user-actionable search relaxation parameter.
    Dynamically generated per-request to handle partial matches via the /api/chat endpoint.
    """
    label: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=200)


class RelaxationHint(BaseModel):
    """
    Reasoning agent's recommendation for search relaxation.
    The orchestrator translates these hints into concrete RelaxationOption objects,
    ensuring budget/stock calculations remain strictly deterministic.
    """
    type: Literal["drop_brand", "increase_budget", "reduce_quantity", "broaden_type"]
    label: str = Field(min_length=1, max_length=120)


class ReasoningDecision(BaseModel):
    """
    Reasoning agent decision payload.
    Note: total_amount and match_score are subject to deterministic recomputation by the orchestrator.
    """
    decision: Literal["proceed", "relax", "retry_broader", "insufficient_stock", "no_match"]
    matched_sku: Optional[str] = None
    matched_quantity: int = Field(default=0, ge=0, le=20)
    total_amount: float = Field(default=0, ge=0)
    exact_match: bool = True
    match_score: float = Field(default=0, ge=0, le=100)
    relaxations: list[RelaxationOption] = Field(default_factory=list)
    relaxation_hints: list[RelaxationHint] = Field(default_factory=list)
    next_search_hint: Optional[str] = Field(default=None, max_length=150)
    reasoning_note: str = Field(max_length=300)

    @field_validator("matched_quantity", "total_amount", "exact_match", "match_score", mode="before")
    @classmethod
    def _none_becomes_default(cls, v, info):
        """
        Substitutes defaults for LLM-generated null values on primitive fields
        to prevent downstream Pydantic type validation errors.
        """
        if v is None:
            defaults = {
                "matched_quantity": 0,
                "total_amount": 0.0,
                "exact_match": True,
                "match_score": 0.0,
            }
            return defaults[info.field_name]
        return v

class GateResult(BaseModel):
    """Output of the deterministic gate — never produced by an LLM."""
    approved: bool
    requires_otp: bool = False
    reason: str
    gate_token: Optional[str] = None
    verified_total: Optional[float] = None
    otp_code: Optional[str] = None  # only ever surfaced to the API layer when SHOW_OTP_IN_RESPONSE is on


class OrderRecord(BaseModel):
    session_id: str
    gate_token: str
    sku: str
    quantity: int
    amount: float
    razorpay_order_id: Optional[str] = None
    status: Literal["created", "paid", "failed", "cancelled"] = "created"


class UpsellSuggestion(BaseModel):
    """Guardrail on the Upsell agent's output."""
    suggest: bool
    sku: Optional[str] = None
    reason: str = Field(max_length=200)


class PaymentVerification(BaseModel):
    """What the frontend sends back after Razorpay Checkout completes."""
    session_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
