"""
Reasoning agent — chooses the best product for the user from the fetched
candidates and explains why. Its output is advisory: the orchestrator
independently recomputes price/total AND a deterministic match score, then
decides proceed-vs-relax itself (see recompute_and_verify). The LLM's product
*choice* is trusted; its numbers and its match claim are not.

Outcome, decided deterministically after the LLM:
  - proceed : match_score >= 90 AND within budget AND enough stock. Still goes
              to the user for explicit confirmation before any charge.
  - relax   : a product exists but scored < 90, or is over budget / short on
              stock. Instead of failing or silently substituting, we hand back
              specific, per-request relaxation options (drop the brand, raise
              the budget to the real cheapest price, reduce quantity to what's
              in stock).
  - retry_broader : nothing matched the category at all — signals the
              orchestrator's automatic broaden-and-retry loop.
"""
import math
from pydantic import ValidationError
from app.llm import call_json
from app.catalog import _word_in
from app.guardrails.schemas import ProductQuery, FetchedProduct, ReasoningDecision, RelaxationOption

PROCEED_THRESHOLD = 90.0

SYSTEM_PROMPT = """You are a shopping reasoning agent. You are given the user's requested
category/brand/attribute/quantity/budget and TWO candidate lists: PRIMARY (products that match the
requested brand) and ALTERNATIVES (same category, brand/attribute may differ).

Your job is to pick the single best product for the user and explain why. You do NOT make the final
buy-vs-adjust call and your arithmetic does not matter — the orchestrator recomputes price, total, stock
and a match score deterministically after you. So focus purely on choosing the right product.

How to choose:
- Prefer a PRIMARY candidate that has enough stock and fits the budget.
- If no PRIMARY fits, you may pick the closest ALTERNATIVE as a substitute.
- When two candidates are close in price, prefer the one with faster delivery and SAY SO in the note
  (e.g. "picked the ₹50-more option since it arrives in 1 day vs 4").

Output fields:
- decision: "proceed" if you found any reasonable product to propose; "retry_broader" ONLY if NOTHING in
  either list matches the category at all.
- matched_sku: the sku you chose (or null if retry_broader).
- matched_quantity: the full quantity the user asked for.
- total_amount: your estimate of price*quantity (advisory only; it will be recomputed).
- exact_match: true only if the chosen product matches the requested brand/attribute exactly.
- next_search_hint: only when decision="retry_broader" — a specific instruction on what to relax
  (e.g. "drop the brand 'sony', keep category=earbuds"), not a generic restatement.
- reasoning_note: one short sentence explaining the choice, including any delivery/price trade-off.
JSON fields exactly: decision, matched_sku, matched_quantity, total_amount, exact_match, next_search_hint, reasoning_note"""


def _candidates(primary: list[FetchedProduct], fallback: list[FetchedProduct]) -> list[FetchedProduct]:
    seen = {p.sku for p in primary}
    return primary + [f for f in fallback if f.sku not in seen]


def _brand_matches(brand: str | None, p: FetchedProduct) -> bool:
    if not brand:
        return True
    return _word_in(brand, p.brand or "") or _word_in(brand, p.name) or _word_in(brand, p.attribute or "")


def _attr_matches(attr: str | None, p: FetchedProduct) -> bool:
    if not attr:
        return True
    return _word_in(attr, p.name) or _word_in(attr, p.attribute or "")


def _cat_matches(cat: str, p: FetchedProduct) -> bool:
    return _word_in(cat, p.category or "") or _word_in(cat, p.name)


def _score(query: ProductQuery, p: FetchedProduct, qty: int) -> float:
    """Deterministic match score (0-100) across the dimensions the user asked
    about: category (always), brand (if requested), attribute (if requested),
    and stock sufficiency (always)."""
    comps: list[float] = [1.0 if _cat_matches(query.category, p) else 0.0]
    if query.brand:
        comps.append(1.0 if _brand_matches(query.brand, p) else 0.0)
    if query.attribute:
        comps.append(1.0 if _attr_matches(query.attribute, p) else 0.0)
    comps.append(min(p.stock / qty, 1.0) if qty > 0 else 0.0)
    return round(100.0 * sum(comps) / len(comps), 1)


def _best(query: ProductQuery, cands: list[FetchedProduct], qty: int) -> FetchedProduct:
    """Deterministic tie-break used only when the LLM names a sku that isn't in
    the candidate set: highest score, then in-budget, then fastest delivery,
    then cheapest."""
    def key(p: FetchedProduct):
        total = p.price * qty
        return (-_score(query, p, qty), 0 if total <= query.budget_cap else 1, p.delivery_days, p.price)
    return sorted(cands, key=key)[0]


def _build_query(q: ProductQuery, drop_brand: bool = False, budget: float | None = None,
                 quantity: int | None = None) -> str:
    qty = quantity if quantity is not None else q.quantity
    parts = [str(qty)]
    if q.brand and not drop_brand:
        parts.append(q.brand)
    if q.attribute:
        parts.append(q.attribute)
    parts.append(q.category)
    b = budget if budget is not None else q.budget_cap
    return f"{' '.join(parts)} under ₹{int(round(b))}"


def _relaxations(query: ProductQuery, primary: list[FetchedProduct],
                 fallback: list[FetchedProduct], matched: FetchedProduct | None) -> list[RelaxationOption]:
    """Build per-request relaxation options from the ACTUAL gap — never
    hardcoded. Each option is a rephrased query the user can re-run."""
    cands = _candidates(primary, fallback)
    in_stock = [c for c in cands if c.stock > 0]
    qty = query.quantity
    opts: list[RelaxationOption] = []

    # brand blocker: brand was asked for but the matched product doesn't match it
    if query.brand and (matched is None or not _brand_matches(query.brand, matched)):
        if fallback or in_stock:
            opts.append(RelaxationOption(
                label=f"Drop the '{query.brand}' brand requirement",
                query=_build_query(query, drop_brand=True),
            ))

    # price blocker: the cheapest otherwise-qualifying option is over budget
    qualifying = [c for c in in_stock
                  if c.stock >= qty and _brand_matches(query.brand, c) and _attr_matches(query.attribute, c)]
    pool = qualifying or [c for c in in_stock if c.stock >= qty] or in_stock
    if pool:
        cheapest = min(pool, key=lambda c: c.price)
        cheapest_total = round(cheapest.price * qty, 2)
        if cheapest_total > query.budget_cap:
            target = int(math.ceil(cheapest_total))
            opts.append(RelaxationOption(
                label=f"Increase budget to ₹{target}",
                query=_build_query(query, budget=target),
            ))

    # stock blocker: something matched but there aren't enough units
    if matched is not None and 0 < matched.stock < qty:
        opts.append(RelaxationOption(
            label=f"Reduce quantity to {matched.stock}",
            query=_build_query(query, quantity=matched.stock),
        ))

    # de-dup, cap at 3, and always leave at least one way forward
    seen, uniq = set(), []
    for o in opts:
        if o.label not in seen:
            seen.add(o.label)
            uniq.append(o)
    if not uniq:
        uniq.append(RelaxationOption(
            label="Search more broadly",
            query=f"{qty} {query.category} under ₹{int(round(query.budget_cap))}",
        ))
    return uniq[:3]


def run_reasoning_agent(query: ProductQuery, primary: list[FetchedProduct],
                        fallback: list[FetchedProduct]) -> ReasoningDecision:
    def fmt(items: list[FetchedProduct]) -> str:
        return "\n".join(
            f"- sku={p.sku} name={p.name} price={p.price} stock={p.stock} "
            f"delivery_days={p.delivery_days} attribute={p.attribute or '-'}"
            for p in items
        ) or "(none)"

    user_prompt = (
        f"User wants: quantity={query.quantity}, budget_cap={query.budget_cap}, "
        f"category={query.category}, brand={query.brand}, attribute={query.attribute}\n\n"
        f"PRIMARY candidates (match the requested brand):\n{fmt(primary)}\n\n"
        f"ALTERNATIVE candidates (category match, brand/attribute may differ):\n{fmt(fallback)}"
    )

    raw = call_json(SYSTEM_PROMPT, user_prompt, temperature=0.15)
    try:
        return ReasoningDecision(**raw)
    except ValidationError as e:
        raise ValueError(f"Reasoning agent produced invalid output: {e}")


def recompute_and_verify(decision: ReasoningDecision, query: ProductQuery,
                         primary: list[FetchedProduct], fallback: list[FetchedProduct]) -> ReasoningDecision:
    """
    Deterministic double-check. Never trusts the LLM's arithmetic or its match
    claim: it recomputes the total from catalog price, scores the match itself,
    and decides proceed (>=90, in budget, in stock) vs relax (with concrete
    options) vs retry_broader (nothing in category). This function's output —
    not the raw LLM output — is what the gate and the user see.
    """
    cands = _candidates(primary, fallback)

    if not cands:
        decision.decision = "retry_broader"
        decision.matched_sku = None
        decision.match_score = 0.0
        decision.exact_match = False
        decision.relaxations = _relaxations(query, primary, fallback, None)
        return decision

    matched = next((p for p in cands if p.sku == decision.matched_sku), None)
    if matched is None:
        matched = _best(query, cands, decision.matched_quantity or query.quantity)
        decision.matched_sku = matched.sku
        decision.reasoning_note = (
            decision.reasoning_note + " [guardrail: LLM sku not offered; picked best deterministically]"
        )[:300]

    qty = decision.matched_quantity or query.quantity
    decision.matched_quantity = qty

    decision.match_score = _score(query, matched, qty)
    decision.total_amount = round(matched.price * qty, 2)

    within_budget = decision.total_amount <= query.budget_cap
    stock_ok = matched.stock >= qty
    decision.exact_match = decision.match_score >= 100.0 and within_budget and stock_ok

    if decision.match_score >= PROCEED_THRESHOLD and within_budget and stock_ok:
        decision.decision = "proceed"
        decision.relaxations = []
    else:
        decision.decision = "relax"
        decision.relaxations = _relaxations(query, primary, fallback, matched)

    return decision
