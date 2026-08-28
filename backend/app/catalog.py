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


def search_products(category: str, attribute: str | None, limit: int = 10):
    """
    Structured search — this is the only thing the Fetch agent is allowed
    to call. Returns raw rows; guardrail validation of each row happens in
    the fetch agent, not here.

    Matches against category, name, AND attribute using whole-word/phrase
    matching (see _word_in). Two things this fixes at once:
      - "Wired Earphones" (a product's NAME, not its category taxonomy
        value) still finds EAR-02, because name is checked too.
      - "phones" (a broadened search term) does NOT falsely match "Wired
        EARPHONES", because word-boundary matching won't treat "phones" as
        found inside "earphones" — only as its own distinct word.
    """
    term = (category or "").strip()

    if is_connected():
        # broad candidate fetch (ilike, cheap on the DB side), then a
        # precise word-boundary filter in Python narrows it correctly
        q = table("products").select("*").or_(
            f"category.ilike.%{term}%,name.ilike.%{term}%,attribute.ilike.%{term}%"
        )
        res = q.limit(limit * 5).execute()
        candidates = res.data or []
    else:
        candidates = _load_seed()

    rows = [
        r for r in candidates
        if _word_in(term, r["category"]) or _word_in(term, r["name"]) or _word_in(term, r.get("attribute") or "")
    ]

    if attribute:
        narrowed = [
            r for r in rows
            if _word_in(attribute, r.get("attribute") or "") or _word_in(attribute, r["name"])
        ]
        if narrowed:
            rows = narrowed

    rows = rows[:limit]

    # sanitize any free-text fields before they can reach an LLM prompt downstream
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