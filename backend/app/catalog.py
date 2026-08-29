"""
Catalog service. Reads from Supabase 'products' table when connected,
otherwise falls back to seed_data/products.json so the app is runnable
before Supabase is wired up.
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
    Whole-word / whole-phrase match, not a raw substring check. This is
    what stops a broadened search term like "phones" from matching inside
    an unrelated product name like "Wired Earphones" — \\b boundaries mean
    "phones" only matches as its own word, not as a fragment of another.
    """
    if not term or not text:
        return False
    pattern = r"\b" + re.escape(term.strip().lower()) + r"\b"
    return re.search(pattern, text.lower()) is not None


def _plural_singular_variants(term: str) -> set[str]:
    """Simple singular/plural spellings of a SINGLE word token, so a product
    noun matches whether the catalog writes it singular or plural —
    'smartphone' <-> 'smartphones', 'watch' <-> 'watches', 'battery' <->
    'batteries'. Punctuation/spaces are stripped, so only pass one token."""
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
    """Whole-word match that also accepts a simple singular/plural variant of a
    single-word term, so a product noun matches regardless of how the catalog
    pluralises it ('smartphone' matches 'Samsung Smartphones Series A'). Like
    _word_in it uses \\b boundaries, so a variant still cannot match inside an
    unrelated word ('phones' won't match within 'earphones')."""
    if not term or not text:
        return False
    low = text.lower()
    for variant in _plural_singular_variants(term):
        if re.search(r"\b" + re.escape(variant) + r"\b", low):
            return True
    return False


# Tokens too generic to prove a product-type match on their own.
_PRODUCT_TYPE_STOPWORDS = {
    "the", "a", "an", "of", "for", "with", "and", "in", "to", "new", "set",
    "pack", "pair", "piece", "pieces", "unit", "units",
}


def _significant_tokens(phrase: str) -> list[str]:
    """Word tokens from a phrase worth matching on — lowercased, de-noised of
    stopwords and 1-character fragments. 'usb c cable' -> ['usb', 'cable'];
    'led tv' -> ['led', 'tv']; '16 GB RAM' -> ['16', 'gb', 'ram']."""
    toks = re.findall(r"[a-z0-9]+", (phrase or "").lower())
    return [t for t in toks if len(t) >= 2 and t not in _PRODUCT_TYPE_STOPWORDS]


def _product_type_matches_row(product_type: str, row: dict) -> bool:
    """True when a catalog row's NAME or attribute actually contains the
    product the user named. Multi-word product types match on their head noun
    (the last significant token) OR the full phrase, so 'usb c cable' matches
    'Tizum USB to VGA Cable' via 'cable', but 'earbuds' will NOT match a TV.
    The head-noun check is singular/plural tolerant, so 'smartphone' matches a
    row named 'Samsung Smartphones Series A'.
    Returns True when no product_type is given (i.e. no narrowing requested)."""
    toks = _significant_tokens(product_type)
    if not toks:
        return True
    hay = f"{row.get('name', '')} {row.get('attribute') or ''}"
    if _word_in(product_type, hay):          # exact phrase, e.g. 'led tv'
        return True
    return _word_in_flex(toks[-1], hay)      # head noun, plural-tolerant


def get_categories() -> list[str]:
    """Distinct category values actually in the catalog — given to the
    query agent so it grounds its guesses in what you really sell."""
    if is_connected():
        res = table("products").select("category").execute()
        rows = res.data or []
        return sorted({r["category"] for r in rows})
    return sorted({r["category"] for r in _load_seed()})


def list_products(category: str | None = None, max_price: float | None = None, limit: int = 50):
    """
    Public, agent-facing catalog listing — no internal query object required.
    This is what backs GET /api/catalog, so any external AI agent can browse
    the merchant's inventory cold, without going through the chat pipeline.
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
                # category + product_type together found nothing — the query
                # agent's category guess may be off. Fall back to searching the
                # product name across the WHOLE catalog, ignoring category, since
                # "is this literally named what the user asked for" matters more
                # than which taxonomy bucket it happens to sit in.
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