"""
Cross-cutting guardrail: strip instruction-like text out of anything that
came from the catalog (product titles/descriptions) before it's placed into
an LLM prompt. This is the defense against prompt injection hidden inside
product data (e.g. a listing titled 'ignore previous instructions...').
"""
import re

_SUSPICIOUS_PATTERNS = [
    r"ignore (all|previous|above) instructions",
    r"disregard (the|your|all) (rules|instructions|policy)",
    r"system prompt",
    r"you are now",
    r"act as",
    r"</?(system|user|assistant)>",
]

_compiled = [re.compile(p, re.IGNORECASE) for p in _SUSPICIOUS_PATTERNS]


def sanitize_catalog_text(text: str) -> str:
    if not text:
        return text
    cleaned = text
    for pattern in _compiled:
        cleaned = pattern.sub("[redacted]", cleaned)
    # hard cap length so a single field can't blow out the prompt
    return cleaned[:300]
