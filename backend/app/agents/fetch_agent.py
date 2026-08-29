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
    Returns (primary, fallback). Retrieval is now by PRODUCT TYPE + category,
    not category alone — so "boat earbuds" no longer pulls back every TV and
    cable in the "Electronics" bucket.
      - primary : products that ARE the requested product_type (+ attribute)
                  AND, when a brand was named, match that brand.
      - fallback: the same product_type with the brand dropped — the "same
                  thing, other brand" alternatives the relax flow offers. When
                  no brand was named there is nothing to drop, so it's empty.
    """
    if query.brand:
        base = _validate(search_products(query.category, query.product_type, query.attribute, limit=20))
        primary = [p for p in base if _brand_matches(query.brand, p)][:5]
        fallback = _validate(search_products(query.category, query.product_type, None, limit=5))
        return primary, fallback

    primary = _validate(search_products(query.category, query.product_type, query.attribute, limit=5))
    return primary, []
