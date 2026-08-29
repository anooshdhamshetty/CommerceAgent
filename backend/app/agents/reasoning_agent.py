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
from app.catalog import _word_in, _significant_tokens, _word_in_flex
from app.guardrails.schemas import ProductQuery, FetchedProduct, ReasoningDecision, RelaxationOption, RelaxationHint

PROCEED_THRESHOLD = 90.0

SYSTEM_PROMPT = """You are a shopping reasoning agent. You are given the user's ORIGINAL RAW REQUEST (their
actual sentence), their requested category/product_type/brand/attribute/quantity/budget, and TWO candidate
lists: PRIMARY (products that match the requested brand) and ALTERNATIVES (same category, brand/attribute
may differ).

Your job is to pick the single best product for the user and explain why. You do NOT make the final
buy-vs-adjust call and your arithmetic does not matter — the orchestrator recomputes price, total, stock
and a match score deterministically after you. So focus purely on choosing the right product.

CRITICAL: before picking anything, re-read the user's original raw request. category is only a broad
taxonomy bucket used to narrow the search — candidates in the same category are NOT automatically the same
product. A charging cable and a laptop can both be "Computers & Accessories"; a mouse is not a laptop even
though both live in that bucket. Only pick a candidate whose actual NAME genuinely matches product_type. If
nothing in either list actually resembles product_type, do not force-pick the closest category match —
treat it the same as nothing being found (decision="retry_broader" if truly nothing fits, or pick the
closest real substitute and mark exact_match=false with a clear reasoning_note explaining the gap).

How to choose:
- Prefer a PRIMARY candidate that has enough stock and fits the budget.
- If no PRIMARY fits, you may pick the closest ALTERNATIVE as a substitute — but only if it is still
  genuinely the same kind of product the user asked for.
- When two candidates are close in price, prefer the one with faster delivery and SAY SO in the note
  (e.g. "picked the ₹50-more option since it arrives in 1 day vs 4").

Output fields:
- decision: "proceed" if you found any reasonable product to propose; "retry_broader" ONLY if NOTHING in
  either list matches the category at all.
- matched_sku: the sku you chose (or null if retry_broader).
- matched_quantity: the full quantity the user asked for.
- total_amount: your estimate of price*quantity (advisory only; it will be recomputed).
- exact_match: true only if the chosen product matches the requested product_type AND brand/attribute
  exactly.
- next_search_hint: only when decision="retry_broader" — a specific instruction on what to relax
  (e.g. "drop the brand 'sony', keep category=earbuds"), not a generic restatement.
- reasoning_note: one short sentence explaining the choice, including any delivery/price trade-off, and
  explicitly noting if the match isn't a true product_type match.
- relaxation_hints: when the match is NOT exact (over budget, short on stock, wrong brand, or only a
  near-substitute was found), suggest 2-3 ways the shopper could adjust — each as {"type": ..., "label": ...}.
  Pick ONLY the levers that genuinely help THIS gap, ordered most-helpful first. Allowed types:
    * "drop_brand"      — they named a brand but the best option is a different (or no) brand.
    * "increase_budget" — the right product exists but costs more than their cap.
    * "reduce_quantity" — the product is right but there isn't enough stock for the quantity asked.
    * "broaden_type"    — loosen brand + attribute qualifiers to see more of the same product type.
  The label is a short, friendly call to action (e.g. "See similar fans from other brands"). Do NOT put any
  ₹ amounts, prices, or stock counts in the label — the system fills the exact numbers in. Omit or leave
  relaxation_hints empty when decision="proceed".
JSON fields exactly: decision, matched_sku, matched_quantity, total_amount, exact_match, next_search_hint, reasoning_note, relaxation_hints"""


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


def _product_type_matches(product_type: str, p: FetchedProduct) -> bool:
    """
    Is this candidate actually the product the user named? Checked against the
    candidate's real NAME (and attribute) — never against category. Fetch
    already filtered by category, so comparing product_type to category would
    trivially pass for almost everything in the bucket (a charging cable and a
    laptop can both sit under "Computers & Accessories"); checking the actual
    product name is what catches a same-category-different-product false match.

    Matches the exact phrase OR the head noun, and the head-noun check tolerates
    simple singular/plural spelling — so 'smartphone' matches a row named
    'Samsung Smartphones Series A', while 'phones' still won't leak into an
    unrelated 'Earphones'.
    """
    if not product_type:
        return True
    toks = _significant_tokens(product_type)
    if not toks:
        return True
    hay = f"{p.name} {p.attribute or ''}"
    if _word_in(product_type, hay):          # exact phrase, e.g. 'led tv'
        return True
    return _word_in_flex(toks[-1], hay)      # head noun, plural-tolerant


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _attr_fraction(attr: str | None, p: FetchedProduct) -> float:
    """Graded attribute fit: the fraction of the requested attribute's
    significant words that actually appear in the product name/attribute. A
    request for "wireless noise cancelling" fits a product satisfying both
    words better than one satisfying only one."""
    toks = _significant_tokens(attr or "")
    if not toks:
        return 1.0
    hay = f"{p.name} {p.attribute or ''}"
    hits = sum(1 for t in toks if _word_in(t, hay))
    return hits / len(toks)


def _score(query: ProductQuery, p: FetchedProduct, qty: int) -> float:
    """Deterministic, GRADED match score (0-100).

    Every dimension is a (weight, value) pair; only the dimensions that apply
    to THIS request are included, and the weights are renormalised over them.
    Values are graded (not just 0/1) so two products that both technically
    qualify still score differently based on how well they fit:

      - product_type (0.35): is this actually the product the user named?
      - category     (0.15): does it sit in the requested bucket?
      - brand        (0.20): only when a brand was requested.
      - attribute    (0.20): only when an attribute was requested; graded by
                             how many of its words match.
      - budget       (0.10): 1.0 within budget, scaled down as it goes over.
      - stock        (0.10): how much of the requested quantity is in stock.
      - price/delivery (0.03/0.02): low-weight tie-breakers so equally
                             qualified products don't all pin at 100 — cheaper
                             and faster ranks marginally higher.

    A product that satisfies everything asked still lands in the low-to-high
    90s (auto-proceed); any missing or weaker dimension pulls it down.

    product_type is ALSO a hard floor: if the candidate's name doesn't contain
    the product the user named at all, the score is capped at 20 no matter how
    well it does on brand/price/stock (a mouse is not a laptop, even though
    both are "Computers & Accessories")."""
    per_unit_budget = (query.budget_cap / qty) if qty > 0 else query.budget_cap
    total = p.price * qty

    dims: list[tuple[float, float]] = [
        (0.35, 1.0 if _product_type_matches(query.product_type, p) else 0.0),
        (0.15, 1.0 if _cat_matches(query.category, p) else 0.0),
    ]
    if query.brand:
        dims.append((0.20, 1.0 if _brand_matches(query.brand, p) else 0.0))
    if query.attribute:
        dims.append((0.20, _attr_fraction(query.attribute, p)))
    dims.append((0.10, 1.0 if total <= query.budget_cap
                 else _clamp01(1 - (total - query.budget_cap) / query.budget_cap)))
    dims.append((0.10, min(p.stock / qty, 1.0) if qty > 0 else 0.0))
    dims.append((0.03, _clamp01(1 - p.price / per_unit_budget) if per_unit_budget > 0 else 0.0))
    dims.append((0.02, _clamp01(1 - min(p.delivery_days, 10) / 10)))

    wsum = sum(w for w, _ in dims)
    base = 100.0 * sum(w * v for w, v in dims) / wsum if wsum else 0.0

    if not _product_type_matches(query.product_type, p):
        base = min(base, 20.0)

    return round(base, 1)


def _best(query: ProductQuery, cands: list[FetchedProduct], qty: int) -> FetchedProduct:
    """Deterministic tie-break used only when the LLM names a sku that isn't in
    the candidate set: highest score, then in-budget, then fastest delivery,
    then cheapest."""
    def key(p: FetchedProduct):
        total = p.price * qty
        return (-_score(query, p, qty), 0 if total <= query.budget_cap else 1, p.delivery_days, p.price)
    return sorted(cands, key=key)[0]


def _build_query(q: ProductQuery, drop_brand: bool = False, drop_attribute: bool = False,
                 budget: float | None = None, quantity: int | None = None) -> str:
    """Rebuild the user's request as a natural-language query with EXACTLY one
    thing relaxed — this backs the relaxation buttons. Everything the user
    asked for is preserved except the constraint being dropped; crucially the
    product_type (their real product noun) is ALWAYS kept, so 'drop brand' on
    'Samsung laptop 16GB under 50000' yields 'laptop 16GB under 50000', never
    the bare 'Computers & Accessories' category."""
    qty = quantity if quantity is not None else q.quantity
    parts = [str(qty)]
    if q.brand and not drop_brand:
        parts.append(q.brand)
    parts.append(q.product_type or q.category)
    if q.attribute and not drop_attribute:
        parts.append(q.attribute)
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

    if query.brand and (matched is None or not _brand_matches(query.brand, matched)):
        if fallback or in_stock:
            opts.append(RelaxationOption(
                label=f"Drop the '{query.brand}' brand requirement",
                query=_build_query(query, drop_brand=True),
            ))

    qualifying = [c for c in in_stock
                  if c.stock >= qty and _brand_matches(query.brand, c)
                  and _attr_matches(query.attribute, c)
                  and _product_type_matches(query.product_type, c)]
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

    if matched is not None and 0 < matched.stock < qty:
        opts.append(RelaxationOption(
            label=f"Reduce quantity to {matched.stock}",
            query=_build_query(query, quantity=matched.stock),
        ))

    seen, uniq = set(), []
    for o in opts:
        if o.label not in seen:
            seen.add(o.label)
            uniq.append(o)
    if not uniq:
        # Broaden by dropping the brand/attribute qualifiers but KEEP the
        # product_type — never collapse to a bare category search.
        uniq.append(RelaxationOption(
            label="Search more broadly",
            query=_build_query(query, drop_brand=True, drop_attribute=True),
        ))
    return uniq[:3]


def _hint_fields(h) -> tuple[str, str]:
    """Read (type, label) from a RelaxationHint whether it arrived as a model
    instance or a plain dict (defensive — real pydantic hands us the model)."""
    if isinstance(h, dict):
        return (h.get("type", "") or "", h.get("label", "") or "")
    return (getattr(h, "type", "") or "", getattr(h, "label", "") or "")


def _ground_one(htype: str, label: str, query: ProductQuery,
                cands: list[FetchedProduct], matched: FetchedProduct | None) -> RelaxationOption | None:
    """Turn ONE reasoning-agent hint into a concrete, correctly-numbered option,
    or None if that lever doesn't actually apply. Every number (budget target,
    stock) is computed here from real catalog data — the LLM's label is used
    only as the button text, never its arithmetic."""
    in_stock = [c for c in cands if c.stock > 0]
    qty = query.quantity

    if htype == "drop_brand":
        if not query.brand:
            return None
        text = label or f"Drop the '{query.brand}' brand requirement"
        return RelaxationOption(label=text[:120], query=_build_query(query, drop_brand=True))

    if htype == "increase_budget":
        # Cheapest in-type, in-stock, brand/attr-consistent candidate we can't
        # afford yet — the real figure the budget would need to reach.
        pool = [c for c in in_stock if c.stock >= qty
                and _brand_matches(query.brand, c) and _attr_matches(query.attribute, c)
                and _product_type_matches(query.product_type, c)]
        pool = pool or [c for c in in_stock if c.stock >= qty] or in_stock
        if not pool:
            return None
        cheapest = min(pool, key=lambda c: c.price)
        target = int(math.ceil(cheapest.price * qty))
        if target <= query.budget_cap:
            return None  # already affordable — nothing to raise
        base = label or "Increase the budget"
        return RelaxationOption(label=f"{base} (to ₹{target})"[:120],
                                query=_build_query(query, budget=target))

    if htype == "reduce_quantity":
        target_stock = matched.stock if (matched and matched.stock > 0) else max(
            (c.stock for c in in_stock), default=0)
        if not (0 < target_stock < qty):
            return None  # enough stock (or none at all) — reducing wouldn't help
        base = label or "Reduce the quantity"
        return RelaxationOption(label=f"{base} (to {target_stock})"[:120],
                                query=_build_query(query, quantity=target_stock))

    if htype == "broaden_type":
        if not (query.brand or query.attribute):
            return None  # nothing left to broaden
        text = label or "Search more broadly"
        return RelaxationOption(label=text[:120],
                                query=_build_query(query, drop_brand=True, drop_attribute=True))

    return None


def _ground_relaxations(query: ProductQuery, primary: list[FetchedProduct],
                        fallback: list[FetchedProduct], matched: FetchedProduct | None,
                        hints: list) -> list[RelaxationOption]:
    """Build the final relaxation menu.

    The Reasoning agent's hints decide WHICH levers appear, in what order, and
    supply the labels; this grounds each into a real query with correct numbers
    and drops any that don't apply. It then merges in the deterministic,
    gap-based options (_relaxations) so the essential lever is ALWAYS present and
    correctly numbered even if the LLM missed it or returned nothing. Two buttons
    that would run the identical search are collapsed (dedupe by query); capped
    at 4."""
    grounded: list[RelaxationOption] = []
    cands = _candidates(primary, fallback)
    for h in hints or []:
        htype, label = _hint_fields(h)
        opt = _ground_one(htype, label, query, cands, matched)
        if opt:
            grounded.append(opt)

    # Safety net: guarantee the deterministic gap-based options are represented,
    # so a weak or empty LLM response never leaves the shopper worse off.
    grounded.extend(_relaxations(query, primary, fallback, matched))

    seen_q, uniq = set(), []
    for o in grounded:
        if o.query not in seen_q:
            seen_q.add(o.query)
            uniq.append(o)
    return uniq[:4]


def run_reasoning_agent(user_message: str, query: ProductQuery, primary: list[FetchedProduct],
                        fallback: list[FetchedProduct]) -> ReasoningDecision:
    def fmt(items: list[FetchedProduct]) -> str:
        return "\n".join(
            f"- sku={p.sku} name={p.name} price={p.price} stock={p.stock} "
            f"delivery_days={p.delivery_days} attribute={p.attribute or '-'}"
            for p in items
        ) or "(none)"

    user_prompt = (
        f"User's original raw request: \"{user_message}\"\n\n"
        f"Structured: quantity={query.quantity}, budget_cap={query.budget_cap}, "
        f"category={query.category}, product_type={query.product_type}, brand={query.brand}, "
        f"attribute={query.attribute}\n\n"
        f"PRIMARY candidates (match the requested brand):\n{fmt(primary)}\n\n"
        f"ALTERNATIVE candidates (category match, brand/attribute may differ):\n{fmt(fallback)}"
    )

    raw = call_json(SYSTEM_PROMPT, user_prompt, temperature=0.3)
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
        decision.relaxations = _ground_relaxations(
            query, primary, fallback, None, decision.relaxation_hints)
        return decision

    matched = next((p for p in cands if p.sku == decision.matched_sku), None)
    if matched is None:
        matched = _best(query, cands, decision.matched_quantity or query.quantity)
        decision.matched_sku = matched.sku
        decision.reasoning_note = (
            decision.reasoning_note + " [guardrail: LLM sku not offered; picked best deterministically]"
        )[:300]

    # The user's requested quantity is authoritative. The LLM is NOT allowed to
    # silently shrink it to whatever happens to be in stock — that would let an
    # under-stocked item look fully available. Stock is judged against what the
    # user actually asked for; if it falls short we relax (offering a "reduce
    # quantity to N" option), we never quietly reduce it for them.
    qty = query.quantity
    decision.matched_quantity = qty

    if not _product_type_matches(query.product_type, matched):
        decision.reasoning_note = (
            decision.reasoning_note +
            f" [guardrail: requested '{query.product_type}' has no lexical relation to matched "
            f"product name '{matched.name}' — category-only false match, score overridden]"
        )[:300]

    decision.match_score = _score(query, matched, qty)
    decision.total_amount = round(matched.price * qty, 2)

    within_budget = decision.total_amount <= query.budget_cap
    stock_ok = matched.stock >= qty
    # exact_match is judged from the ACTUAL requirements, not from score == 100
    # (the score now carries graded price/delivery tie-breakers, so a genuine
    # exact match can sit in the 90s rather than exactly 100).
    decision.exact_match = (
        _product_type_matches(query.product_type, matched)
        and _cat_matches(query.category, matched)
        and _brand_matches(query.brand, matched)
        and _attr_matches(query.attribute, matched)
        and within_budget and stock_ok
    )

    if decision.match_score >= PROCEED_THRESHOLD and within_budget and stock_ok:
        decision.decision = "proceed"
        decision.relaxations = []
    else:
        decision.decision = "relax"
        # Deterministic, user-facing explanation of the biggest gap — stated
        # plainly and FIRST, ahead of the LLM's own note. A stock shortfall in
        # particular must be explicit ("Only 4 units are available"), never
        # presented as though the full quantity could be fulfilled.
        gap = ""
        if matched.stock <= 0:
            gap = f"'{matched.name}' is out of stock."
        elif matched.stock < qty:
            gap = (f"Only {matched.stock} unit(s) of '{matched.name}' are available — "
                   f"you asked for {qty}.")
        elif not within_budget:
            gap = (f"The closest match, '{matched.name}', comes to ₹{int(round(decision.total_amount))} "
                   f"for {qty} — over your ₹{int(round(query.budget_cap))} budget.")
        if gap:
            decision.reasoning_note = f"{gap} {decision.reasoning_note or ''}".strip()[:300]
        decision.relaxations = _ground_relaxations(
            query, primary, fallback, matched, decision.relaxation_hints)

    return decision