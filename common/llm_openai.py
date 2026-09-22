import os
import json
import re
import httpx
from typing import Any, Dict

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://llm:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")
LLM_ENABLED = os.getenv("LLM_ENABLED", "true").lower() in {"1", "true", "yes"}


def _extract_json(text: str) -> Dict[str, Any]:
    # 가장 단순하지만 실무에서 꽤 잘 버팀: 첫 { ~ 마지막 } 사이를 JSON으로 파싱
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError(f"No JSON object found in: {text[:200]}")
    return json.loads(text[first : last + 1])


async def chat_completion(
    system: str, user: str, temperature: float = 0.0, max_tokens: int = 512
) -> str:
    if not LLM_ENABLED:
        raise RuntimeError("LLM disabled; using deterministic fallback")
    url = f"{LLM_BASE_URL}/chat/completions"
    payload = {
        "model": LLM_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"]


async def chat_json(
    system: str,
    user: str,
    schema_hint: str,
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> Dict[str, Any]:
    prompt = (
        f"{user}\n\n"
        f"Return ONLY valid JSON. No markdown. No extra text.\n"
        f"JSON schema/shape:\n{schema_hint}\n"
    )
    out = await chat_completion(
        system, prompt, temperature=temperature, max_tokens=max_tokens
    )
    return _extract_json(out)
