"""
Query extraction agent module.
Translates unstructured user prompts into structured ProductQuery representations, guarded by strict Pydantic validation.
"""
from pydantic import ValidationError
from app.llm import call_json
from app.catalog import get_categories
from app.guardrails.schemas import ProductQuery

SYSTEM_PROMPT_TEMPLATE = """You are an AI query extraction agent. Your role is to translate unstructured shopper requests into precise, structured product search parameters.

AVAILABLE CATEGORIES: {categories}

EXTRACTION RULES:
- category: Select the most plausible match from the AVAILABLE CATEGORIES. If the request falls entirely outside these categories, extract the closest generic noun from the user's prompt. Do NOT hallucinate categories, force irrelevant matches, or use brand names here. This serves strictly as a high-level taxonomy filter.
- product_type: Extract the exact literal product noun used by the shopper (e.g., "laptop", "led tv", "usb c cable"). Do NOT abstract or generalize this into a broader category (e.g., do not convert "laptop" to "computer" or "electronics"). This field drives downstream relevance matching and must remain highly specific.
- brand: Extract the manufacturer or brand ONLY if explicitly stated (e.g., "Apple", "Sony"). Do not invent brands, infer them from attributes, or duplicate them into other fields. Use null if unspecified.
- attribute: Capture any supplementary descriptive qualifiers (e.g., color, material, size, "wireless"). Exclude the primary product type and brand. Retain these qualifiers even if product availability is uncertain. Use null if no additional attributes are provided.
- quantity: Extract as an integer. Default to 1 if not specified.
- budget_cap: Determine the TOTAL order budget in rupees. A standalone figure (e.g., "under 1500") represents the total cap regardless of quantity. ONLY multiply by quantity if the user explicitly specifies a per-item cost (e.g., "500 each"). Default to 10000 if unspecified.

You MUST output valid JSON containing exactly these keys: category, product_type, attribute, brand, quantity, budget_cap."""

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
