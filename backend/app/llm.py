"""
Thin wrapper around the Groq API. Every call here asks for JSON-only
output matching a specific guardrail schema — the agent functions are
responsible for validating the response before trusting it.
"""
import json
from groq import Groq
from app.config import GROQ_API_KEY, LLM_MODEL

_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


class LLMNotConfigured(Exception):
    pass


class LLMParseError(Exception):
    """Raised when the LLM returns invalid or truncated JSON — includes the
    raw output and finish reason so it's debuggable instead of a bare
    json.JSONDecodeError bubbling up to the user."""
    pass


# Alias: main.py imports LLMResponseError. Keeping both names means you
# don't have to touch main.py's import line to use this file.
LLMResponseError = LLMParseError


def call_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.15,
    response_schema: dict = None,  # left in for compatibility; Groq relies on JSON mode + prompt
    max_retries: int = 2,
) -> dict:
    """
    Calls Groq with JSON-only output mode, then parses it.

    temperature defaults LOW (0.15), not high. These calls are structured
    extraction against real data (category, price, quantities) — there is
    exactly one correct answer for a given input, so we want the model's
    most confident, most repeatable output, not creative variation.
    """
    if _client is None:
        raise LLMNotConfigured(
            "GROQ_API_KEY is not set. Add it to backend/.env."
        )

    raw_output = None
    finish_reason = None

    for attempt in range(max_retries):
        try:
            response = _client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt + "\n\nYou MUST output your response in JSON format.",
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                # This enforces valid JSON generation on Groq's end
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_tokens,
            )

            choice = response.choices[0]
            raw_output = choice.message.content
            finish_reason = choice.finish_reason
            break

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[LLM Error] Retrying... (Attempt {attempt + 1}/{max_retries}) | Error: {e}")
                continue
            raise e

    if finish_reason == "length":
        raise LLMParseError(
            f"Groq response was truncated (finish_reason=length, max_tokens={max_tokens}). "
            f"Raise max_tokens for this call. Partial output: {(raw_output or '')[:300]!r}"
        )

    try:
        return json.loads(raw_output or "{}")
    except json.JSONDecodeError as e:
        raise LLMParseError(
            f"Failed to parse LLM output as JSON. Error: {e}. Raw Output: {raw_output}"
        )