"""
Guardrail schemas — one per agent boundary in the pipeline.

Every agent that touches an LLM must produce output that validates against
one of these models before the orchestrator will accept it. Anything that
fails validation is rejected and retried/escalated — it never silently
flows downstream malformed.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class ProductQuery(BaseModel):
    """Guardrail on the Query agent's output."""
    category: str = Field(min_length=1, max_length=50)
    # The user's own literal product noun, kept as-is — "laptop", "led tv",
    # "usb c cable", "running shoes". category is a broad taxonomy bucket
    # used only to narrow the database search; product_type is what the
    # reasoning agent actually checks a candidate's NAME against, because a
    # mouse, a cable, and a laptop can all share the same category bucket.
    product_type: str = Field(min_length=1, max_length=80)
    attribute: Optional[str] = Field(default=None, max_length=50)
    # brand is its OWN field, never merged into attribute/category. Populated
    # only when the user explicitly names a brand (Nike, Apple, Sony); stays
    # null otherwise — the query agent never guesses one.
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
    # Carried through from the catalog row so the reasoning agent can score
    # category / brand / attribute fit deterministically. Optional: rows
    # always have category, usually have attribute, and rarely carry an
    # explicit brand (brand is then matched against name/attribute instead).
    category: Optional[str] = None
    attribute: Optional[str] = None
    brand: Optional[str] = None


class RelaxationOption(BaseModel):
    """
    One concrete, user-triggerable way to loosen a request that didn't fully
    match. `query` is a rephrased natural-language request the frontend
    re-sends through /api/chat as a brand-new pipeline run — NOT an internal
    retry. Every option is generated per-request from the actual gap, never
    hardcoded.
    """
    label: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=200)


class RelaxationHint(BaseModel):
    """
    The Reasoning agent's SUGGESTION of a relaxation lever — it picks which
    adjustments make sense for this specific gap and writes the human-facing
    label, but it does NOT (and must not) compute the ₹ numbers or the query
    string. The orchestrator grounds each hint into a real, correctly-numbered
    RelaxationOption in `_ground_relaxations` (dropping any that don't actually
    apply). This is the split that lets the menu be reasoning-agent-driven
    without ever surfacing a hallucinated budget/stock figure to the shopper.

    type is a fixed vocabulary so grounding is deterministic:
      - drop_brand      : re-search the same product type, any brand
      - increase_budget : raise the cap to the real cheapest qualifying total
      - reduce_quantity : drop the quantity to what's actually in stock
      - broaden_type    : drop brand + attribute qualifiers, keep product type
    """
    type: Literal["drop_brand", "increase_budget", "reduce_quantity", "broaden_type"]
    label: str = Field(min_length=1, max_length=120)


class ReasoningDecision(BaseModel):
    """
    Guardrail on the Reasoning agent's output.

    IMPORTANT: total_amount and match_score here are the values the
    orchestrator writes AFTER deterministically recomputing them from catalog
    data (see recompute_and_verify). The LLM's product *choice* is trusted;
    its arithmetic and its own match claim are not.

    decision:
      - "proceed": match_score >= 90 AND within budget AND enough stock. Still
        goes to the user for explicit confirmation — never auto-charged.
      - "relax": a product was found but scored < 90, or is over budget / short
        on stock. Instead of failing or silently substituting, `relaxations`
        carries specific adjustments the user can trigger.
      - "retry_broader": nothing matched the category at all — internal signal
        for the orchestrator's automatic broaden-and-retry loop (with
        next_search_hint).

    exact_match: True only when the matched product satisfies everything asked
    (category + brand + attribute) exactly and fits budget/stock.
    """
    decision: Literal["proceed", "relax", "retry_broader", "insufficient_stock", "no_match"]
    matched_sku: Optional[str] = None
    matched_quantity: int = Field(default=0, ge=0, le=20)
    total_amount: float = Field(default=0, ge=0)
    exact_match: bool = True
    match_score: float = Field(default=0, ge=0, le=100)
    relaxations: list[RelaxationOption] = Field(default_factory=list)
    # The LLM's SUGGESTED relaxation levers (which adjustments to offer + their
    # labels). These are grounded into the final `relaxations` above by the
    # orchestrator — the LLM never sets the query/numbers itself.
    relaxation_hints: list[RelaxationHint] = Field(default_factory=list)
    next_search_hint: Optional[str] = Field(default=None, max_length=150)
    reasoning_note: str = Field(max_length=300)


class GateResult(BaseModel):
    """Output of the deterministic gate — never produced by an LLM."""
    approved: bool
    requires_otp: bool = False
    reason: str
    gate_token: Optional[str] = None
    verified_total: Optional[float] = None


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
