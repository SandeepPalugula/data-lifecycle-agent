"""
services/scorer.py
Calls the Anthropic API to semantically score a conversation.
Returns uniqueness, utility, recommended action, and reasoning.
"""
import json
import httpx
from ..config import settings

SYSTEM_PROMPT = """You are a data lifecycle classification agent.
Analyse the conversation snippet and return ONLY a valid JSON object — no markdown, no explanation, no backticks.

Required format:
{"uniqueness_score":0.0,"utility_value":0.0,"recommended_action":"keep","reasoning":"explanation"}

Rules:
- uniqueness_score: float 0.0-1.0 — how rare or irreplaceable is this content
- utility_value: float 0.0-1.0 — how useful to the user in future
- recommended_action: exactly one of "keep", "compress", or "delete"
- reasoning: 2-3 sentences explaining your scores"""

async def score_conversation(text: str) -> dict:
    """
    Score a conversation via the Anthropic API.
    Returns dict with uniqueness_score, utility_value, recommended_action, reasoning,
    plus token usage for introspection.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.SCORER_MODEL,
                "max_tokens": settings.SCORER_MAX_TOKENS,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": f"Score this conversation:\n\n{text[:1500]}"}],
            },
        )
        response.raise_for_status()
        data = response.json()

    raw = "".join(b["text"] for b in data.get("content", []) if b["type"] == "text")
    raw = raw.replace("```json", "").replace("```", "").strip()

    import re
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        raw = match.group(0)

    parsed = json.loads(raw)
    parsed["input_tokens"]  = data.get("usage", {}).get("input_tokens", 0)
    parsed["output_tokens"] = data.get("usage", {}).get("output_tokens", 0)
    return parsed
