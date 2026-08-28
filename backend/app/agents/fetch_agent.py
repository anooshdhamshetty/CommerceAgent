"""
Fetch agent — calls the catalog service with a structured query and returns
validated product rows. Pure data retrieval, no reasoning happens here.

Brand-aware retrieval: when the user named a brand, this returns TWO groups —
a primary set (category + brand) and a fallback set (category alone, capped) —
and passes both downstream. The fetch agent does NOT decide the winner; the
reasoning agent scores them.

Guardrail: every row must validate as FetchedProduct; malformed rows are
dropped rather than passed downstream.
"""
from pydantic import ValidationError
from app.catalog import search_products, _word_in
from app.guardrails.schemas import ProductQuery, FetchedProduct


def _validate(rows) -> list[FetchedProduct]:
    valid: list[FetchedProduct] = []
    for row in rows:
        try:
            valid.append(FetchedProduct(**row))
        except ValidationError:
            # malformed catalog row — dropped, never reaches reasoning agent
            continue
    return valid


def _brand_matches(brand: str | None, p: FetchedProduct) -> bool:
    """Brand is matched against an explicit brand field if the catalog has one,
    otherwise against the product name / attribute (whole-word)."""
    if not brand:
        return True
    return (
        _word_in(brand, p.brand or "")
        or _word_in(brand, p.name)
        or _word_in(brand, p.attribute or "")
    )


def run_fetch_agent(query: ProductQuery) -> tuple[list[FetchedProduct], list[FetchedProduct]]:
    """
    Returns (primary, fallback):
      - primary : products matching category (+ attribute) AND the requested brand
      - fallback: category-only alternatives (brand dropped), capped at 5
    When no brand was requested, primary is the normal category(+attribute)
    top-5 and fallback is empty.
    """
    if query.brand:
        base = _validate(search_products(query.category, query.attribute, limit=20))
        primary = [p for p in base if _brand_matches(query.brand, p)][:5]
        fallback = _validate(search_products(query.category, None, limit=5))
        return primary, fallback

    primary = _validate(search_products(query.category, query.attribute, limit=5))
    return primary, []
