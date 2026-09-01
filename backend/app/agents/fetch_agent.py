"""
Fetch agent module.
Retrieves and validates product rows from the catalog service based on structured queries.
Implements brand-aware dual retrieval (primary vs fallback sets) for downstream reasoning.
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
            # Discard malformed catalog rows to prevent downstream parsing failures.
            continue
    return valid


def _brand_matches(brand: str | None, p: FetchedProduct) -> bool:
    """
    Validates brand presence against explicit brand fields or product name/attribute properties.
    """
    if not brand:
        return True
    return (
        _word_in(brand, p.brand or "")
        or _word_in(brand, p.name)
        or _word_in(brand, p.attribute or "")
    )


def run_fetch_agent(query: ProductQuery) -> tuple[list[FetchedProduct], list[FetchedProduct]]:
    """
    Executes a product search returning a primary match set and a broader fallback set.
    Filters primarily by product_type and category, isolating brand matches into the primary set
    while retaining unbranded alternatives as fallbacks for relaxation.
    """
    if query.brand:
        base = _validate(search_products(query.category, query.product_type, query.attribute, limit=20))
        primary = [p for p in base if _brand_matches(query.brand, p)][:5]
        fallback = _validate(search_products(query.category, query.product_type, None, limit=5))
        return primary, fallback

    primary = _validate(search_products(query.category, query.product_type, query.attribute, limit=5))
    return primary, []
