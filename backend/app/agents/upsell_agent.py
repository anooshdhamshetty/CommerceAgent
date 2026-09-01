"""
Upsell recommendation agent module.
Evaluates remaining budget and purchased items to propose a single complementary add-on.
Recommendations are advisory; accepted upsells route through standard deterministic gate processing.
"""
from pydantic import ValidationError
from app.llm import call_json
from app.catalog import list_products, get_product_by_sku
from app.guardrails.schemas import UpsellSuggestion

SYSTEM_PROMPT = """You are an AI upsell recommendation agent for an online store. Your objective is to evaluate a recently purchased item against a list of in-stock products and remaining budget to propose ONE optimal complementary add-on.

RECOMMENDATION CONSTRAINTS:
- Complementary Relevance: The suggested item MUST be genuinely complementary to the purchased product (e.g., a candle holder for a candle, a case for a phone).
- Exclusions: Do NOT simply recommend the cheapest available item. Do NOT recommend the identical item just purchased.
- No Forced Upsell: If no products in the candidate list are logically complementary, you MUST decline to suggest an upsell (set `suggest=false` and `sku=null`).

OUTPUT REQUIREMENTS:
- suggest: boolean indicating if a complementary upsell was found.
- sku: The SKU of the recommended product (or null if suggest is false).
- reason: A single, concise sentence justifying the recommendation (maximum 20 words).

You MUST output valid JSON containing exactly these keys: suggest, sku, reason."""


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
