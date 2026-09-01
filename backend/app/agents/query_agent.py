"""
Query extraction agent module.
Translates unstructured user prompts into structured ProductQuery representations, guarded by strict Pydantic validation.
"""
from pydantic import ValidationError
from app.llm import call_json
from app.catalog import get_categories
from app.guardrails.schemas import ProductQuery

SYSTEM_PROMPT_TEMPLATE = """You convert a shopper's request into a structured product search.

This store's actual catalog categories are: {categories}

Extract:
- category: if the request plausibly matches one of the categories above, use that exact category value.
  If the request is for something this store clearly doesn't sell at all, still output the single closest
  generic noun from the user's own words — do NOT invent a brand name or an unrelated category, and do NOT
  guess a category from the list above just to force a match if it genuinely isn't a fit. This field is
  ONLY used to narrow the database search — it is a broad bucket, not the actual product.
- product_type: the user's own literal product noun, kept EXACTLY as they said it (or the closest direct
  translation of it) — e.g. "laptop", "led tv", "usb c cable", "running shoes". Do NOT generalize this into
  a category bucket. This is the specific thing being checked for relevance later, so it must stay concrete
  and specific, never abstracted upward (e.g. if they say "laptop", product_type is "laptop", NOT "computer"
  or "electronics" or "computers & accessories").
- brand: the specific brand or maker name the user EXPLICITLY named (e.g. "Nike", "Apple", "Sony", "Boat").
  Extract it only when the user actually names a brand. Never invent one, never move a scent/color/material
  here, and never duplicate the brand into the attribute field. If no brand is named, set brand to null.
- attribute: ANY OTHER descriptive qualifier the user gave that is NOT the brand and NOT the product type —
  scent, color, material, size, "wireless" vs "wired", etc. Capture it even if you're not sure the store
  carries that exact variant; it is the reasoning agent's job to check that later, not yours. Leave this
  null if the user gave no non-brand, non-product-type qualifier at all.
- quantity: integer, default 1 if not stated.
- budget_cap: a number in rupees, interpreted as the TOTAL budget for the whole order (all units combined).
  Treat a bare amount as a total: "under 1500", "budget 1500", "for 1500" all mean budget_cap=1500 regardless
  of quantity. ONLY multiply by quantity when the user explicitly marks the amount as per-item — i.e. they use
  a word like "each", "per", "apiece", or "a piece" (e.g. "3 candles at 500 each" -> budget_cap=1500). If no
  budget is mentioned at all, use 100000.

JSON fields exactly: category, product_type, attribute, brand, quantity, budget_cap"""

def run_query_agent(user_message: str, broaden_hint: str | None = None) -> ProductQuery:
    categories = get_categories()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(categories=", ".join(categories) or "(none)")

    prompt = user_message
    if broaden_hint:
        prompt += f"\n\n(Previous search found no results. Adjust the search as follows: {broaden_hint})"

    raw = call_json(system_prompt, prompt, temperature=0.15)
    try:
        return ProductQuery(**raw)
    except ValidationError as e:
        raise ValueError(f"Query agent produced invalid output: {e}")
