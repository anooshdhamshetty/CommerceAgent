"""
Catalog service module.
Interfaces with Supabase 'products' table, falling back to local JSON seed data if disconnected.
"""
import json
import os
import re
from app.supabase_client import table, is_connected, fallback_store
from app.sanitize import sanitize_catalog_text

_SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "seed_data", "products.json")


def _load_seed():
    store = fallback_store()
    if store["products"]:
        return store["products"]
    with open(_SEED_PATH) as f:
        store["products"] = json.load(f)
    return store["products"]


def _word_in(term: str, text: str) -> bool:
    """
    Performs whole-word or phrase matching instead of raw substring checks.
    Ensures bounded term matching (e.g., 'phones' won't match 'earphones').
    """
    if not term or not text:
        return False
    pattern = r"\b" + re.escape(term.strip().lower()) + r"\b"
    return re.search(pattern, text.lower()) is not None


def _plural_singular_variants(term: str) -> set[str]:
    """
    Generates simple singular/plural spelling variants for a single token.
    Strips punctuation/spaces; supports basic English pluralization rules.
    """
    t = re.sub(r"[^a-z0-9]", "", (term or "").lower())
    if len(t) < 2:
        return set()
    v = {t}
    # singular -> plural
    if t.endswith("y") and t[-2:-1] not in ("a", "e", "i", "o", "u"):
        v.add(t[:-1] + "ies")
    if t.endswith(("s", "x", "z", "ch", "sh")):
        v.add(t + "es")
    else:
        v.add(t + "s")
    # plural -> singular
    if t.endswith("ies") and len(t) > 3:
        v.add(t[:-3] + "y")
    if t.endswith("es") and len(t) > 2:
        v.add(t[:-2])
    if t.endswith("s") and len(t) > 1:
        v.add(t[:-1])
    return {x for x in v if len(x) >= 2}


def _word_in_flex(term: str, text: str) -> bool:
    """
    Performs whole-word matching supporting singular/plural token variants.
    Maintains word boundary constraints to prevent partial substring matches.
    """
    if not term or not text:
        return False
    low = text.lower()
    for variant in _plural_singular_variants(term):
        if re.search(r"\b" + re.escape(variant) + r"\b", low):
            return True
    return False


# Stopwords excluded during product-type extraction.
_PRODUCT_TYPE_STOPWORDS = {
    "the", "a", "an", "of", "for", "with", "and", "in", "to", "new", "set",
    "pack", "pair", "piece", "pieces", "unit", "units",
}


def _significant_tokens(phrase: str) -> list[str]:
    """
    Extracts significant lowercase alphanumeric tokens from a phrase.
    Filters out predefined stopwords and single-character fragments.
    """
    toks = re.findall(r"[a-z0-9]+", (phrase or "").lower())
    return [t for t in toks if len(t) >= 2 and t not in _PRODUCT_TYPE_STOPWORDS]


def _product_type_matches_row(product_type: str, row: dict) -> bool:
    """
    Validates if a catalog row's name or attribute satisfies the specified product_type.
    Evaluates against full exact phrases or plural-tolerant head nouns (last significant token).
    """
    toks = _significant_tokens(product_type)
    if not toks:
        return True
    hay = f"{row.get('name', '')} {row.get('attribute') or ''}"
    if _word_in(product_type, hay):          # exact phrase, e.g. 'led tv'
        return True
    return _word_in_flex(toks[-1], hay)      # head noun, plural-tolerant


def get_categories() -> list[str]:
    """
    Retrieves distinct category values available in the active catalog.
    Used to ground query agent predictions.
    """
    if is_connected():
        res = table("products").select("category").execute()
        rows = res.data or []
        return sorted({r["category"] for r in rows})
    return sorted({r["category"] for r in _load_seed()})


def list_products(category: str | None = None, max_price: float | None = None, limit: int = 50):
    """
    Exposes a public catalog listing for agent or client consumption.
    Supports filtering by category and maximum price limit.
    """
    if is_connected():
        q = table("products").select("*")
        if category:
            q = q.ilike("category", f"%{category}%")
        if max_price is not None:
            q = q.lte("price", max_price)
        res = q.limit(limit).execute()
        rows = res.data or []
    else:
        rows = _load_seed()
        if category:
            rows = [r for r in rows if category.lower() in r["category"].lower()]
        if max_price is not None:
            rows = [r for r in rows if r["price"] <= max_price]
        rows = rows[:limit]

    for r in rows:
        r["name"] = sanitize_catalog_text(r.get("name", ""))
    return rows


def search_products(category: str, product_type: str | None = None,
                    attribute: str | None = None, limit: int = 10):
    term = (category or "").strip()

    if is_connected():
        if product_type:
            pt_term = product_type.strip()
            q = (
                table("products")
                .select("*")
                .ilike("category", f"%{term}%")
                .ilike("name", f"%{pt_term}%")
            )
            res = q.limit(max(limit * 5, 30)).execute()
            candidates = res.data or []

            if not candidates:
                # Fallback: search product name globally across all categories if primary search yields no results.
                res = table("products").select("*").ilike("name", f"%{pt_term}%").limit(max(limit * 5, 30)).execute()
                candidates = res.data or []
        else:
            q = table("products").select("*").or_(
                f"category.ilike.%{term}%,name.ilike.%{term}%,attribute.ilike.%{term}%"
            )
            res = q.limit(max(limit * 10, 50)).execute()
            candidates = res.data or []
    else:
        candidates = _load_seed()

    rows = [
        r for r in candidates
        if _word_in(term, r["category"]) or _word_in(term, r["name"]) or _word_in(term, r.get("attribute") or "")
    ]

    if product_type:
        narrowed = [r for r in rows if _product_type_matches_row(product_type, r)]
        if narrowed:
            rows = narrowed

    if attribute:
        narrowed = [
            r for r in rows
            if _word_in(attribute, r.get("attribute") or "") or _word_in(attribute, r["name"])
        ]
        if narrowed:
            rows = narrowed

    rows = rows[:limit]
    for r in rows:
        r["name"] = sanitize_catalog_text(r.get("name", ""))
    return rows
def get_product_by_sku(sku: str):
    if is_connected():
        res = table("products").select("*").eq("sku", sku).limit(1).execute()
        rows = res.data or []
        return rows[0] if rows else None
    for r in _load_seed():
        if r["sku"] == sku:
            return r
    return None