"""
Upsell recommendation agent module.
Evaluates remaining budget and purchased items to propose a single complementary add-on.
Recommendations are advisory; accepted upsells route through standard deterministic gate processing.
"""
from pydantic import ValidationError
from app.llm import call_json
from app.catalog import list_products, get_product_by_sku
from app.guardrails.schemas import UpsellSuggestion

SYSTEM_PROMPT = """You are an upsell agent for an online store. Given the item just purchased and a list of
other in-stock products that fit within the remaining budget, decide whether to suggest ONE complementary
add-on. Only suggest something genuinely complementary to what was purchased (e.g. a candle holder for a
candle) — never just the cheapest available item, and never the same item again. If nothing complementary
fits, set suggest=false and leave sku null.
JSON fields exactly: suggest, sku, reason (one short sentence, under 20 words)"""


def run_upsell_agent(purchased_sku: str, remaining_budget: float) -> UpsellSuggestion:
    if remaining_budget <= 0:
        return UpsellSuggestion(suggest=False, reason="no budget headroom remaining")

    purchased = get_product_by_sku(purchased_sku)
    if purchased is None:
        return UpsellSuggestion(suggest=False, reason="purchased product not found")

    # Fetch entire in-stock catalog within budget for broader complementarity evaluation.
    candidates = [
        c for c in list_products(limit=50)
        if c["sku"] != purchased_sku and c["price"] <= remaining_budget and c["stock"] > 0
    ]
    if not candidates:
        return UpsellSuggestion(suggest=False, reason="no complementary items within remaining budget")

    candidates_text = "\n".join(f"- sku={c['sku']} name={c['name']} price={c['price']}" for c in candidates)
    user_prompt = (
        f"Purchased: {purchased['name']} (sku={purchased_sku})\n"
        f"Remaining budget headroom: {remaining_budget}\n"
        f"Other in-stock products:\n{candidates_text}"
    )

    raw = call_json(SYSTEM_PROMPT, user_prompt)
    try:
        suggestion = UpsellSuggestion(**raw)
    except ValidationError:
        return UpsellSuggestion(suggest=False, reason="upsell agent returned invalid output")

    # Guardrail: validate LLM output against the actual candidate set.
    if suggestion.suggest and suggestion.sku not in {c["sku"] for c in candidates}:
        return UpsellSuggestion(suggest=False, reason="suggested sku not in candidate set, overridden")

    return suggestion
