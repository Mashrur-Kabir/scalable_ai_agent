import os
import asyncio
import httpx
from typing import List, Dict, Any

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# semaphore to limit concurrent outgoing LLM requests (protects local CPU & network)
MAX_INFLIGHT = int(os.getenv("MAX_INFLIGHT", "2"))
_llm_sem = asyncio.Semaphore(MAX_INFLIGHT)

async def call_groq_single(client: httpx.AsyncClient, prompt: str, temperature: float = 0.0) -> str:
    async with _llm_sem:
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "You are a Research-Paper Analyzer. Output valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": 1200
        }
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        r = await client.post(GROQ_API_URL, json=payload, headers=headers, timeout=60.0)
        r.raise_for_status()
        j = r.json()
        # openai-compatible response path
        return j["choices"][0]["message"]["content"]

async def call_groq_batch(client: httpx.AsyncClient, prompts: List[str], ids: List[str]) -> List[str]:
    """
    Send a single chat completion which includes multiple user messages (one per item).
    We instruct the model to respond with a JSON array whose order matches the order of inputs.
    If the model fails to produce parseable JSON, the caller should handle fallback.
    """
    async with _llm_sem:
        system_msg = (
            "You are a concise Research Paper Analyzer. You will be given multiple inputs.\n"
            "Produce a single JSON array where each element is an object with keys: id, summary, key_points (array), recommendation."
            "Return only the JSON array — nothing else. The order must match the inputs."
        )
        messages = [{"role": "system", "content": system_msg}]
        for i, text in enumerate(prompts):
            messages.append({"role": "user", "content": f"ID:{ids[i]}\n{text}"})

        payload = {"model": MODEL, "messages": messages, "temperature": 0.0, "max_tokens": 1600}
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        r = await client.post(GROQ_API_URL, json=payload, headers=headers, timeout=120.0)
        r.raise_for_status()
        j = r.json()
        content = j["choices"][0]["message"]["content"]
        return [content]