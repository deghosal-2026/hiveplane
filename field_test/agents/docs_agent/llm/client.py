from __future__ import annotations
import json
import logging
import sys
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemma-3-12b-it-qat-4bit"


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        import os
        self.base_url = base_url or os.environ.get("LLM_BASE_URL") or "http://127.0.0.1:8000/v1"
        self.api_key = api_key or os.environ.get("LLM_API_KEY") or ""
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL

    @property
    def _client(self) -> OpenAI:
        key = self.api_key if self.api_key else None
        return OpenAI(base_url=self.base_url, api_key=key)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        model_name = model or self.model
        print(f"  → LLM [{model_name}] (max_tokens={max_tokens})...", file=sys.stderr, flush=True)
        try:
            resp = self._client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            raise LLMError(f"LLM API unreachable ({self.base_url}): {e}") from e

        content = resp.choices[0].message.content
        if not content:
            raise LLMError("LLM returned empty content")
        token_count = resp.usage.total_tokens if resp.usage else "?"
        print(f"  ✓ LLM response ({token_count} tokens)", file=sys.stderr, flush=True)
        return content

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        retries: int = 2,
    ) -> dict[str, Any]:
        model_name = model or self.model
        last_error: Exception | None = None
        raw = ""
        for attempt in range(retries + 1):
            print(f"  → LLM [{model_name}] (structured, attempt {attempt + 1}/{retries + 1}, max_tokens={max_tokens})...", file=sys.stderr, flush=True)
            raw = self.complete(system_prompt, user_prompt, model=model_name, temperature=temperature, max_tokens=max_tokens)
            cleaned = raw.strip()
            first_brace = cleaned.find("{")
            last_brace = cleaned.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                cleaned = cleaned[first_brace : last_brace + 1]
            else:
                if cleaned.startswith("```json"):
                    cleaned = cleaned.split("```json", 1)[1]
                    if "```" in cleaned:
                        cleaned = cleaned.split("```", 1)[0]
                elif cleaned.startswith("```"):
                    cleaned = cleaned.split("```", 1)[1]
                    if "```" in cleaned:
                        cleaned = cleaned.split("```", 1)[0]
            cleaned = cleaned.strip()
            try:
                parsed = json.loads(cleaned)
                print(f"  ✓ JSON valid ({len(parsed)} keys)", file=sys.stderr, flush=True)
                return parsed
            except json.JSONDecodeError as e:
                last_error = e
                print(f"  ✗ JSON invalid (attempt {attempt + 1}): {e}", file=sys.stderr, flush=True)
                print(f"    Raw preview: {raw[:200]}", file=sys.stderr, flush=True)
                if attempt < retries:
                    print("    Retrying with stricter JSON instruction...", file=sys.stderr, flush=True)
                    system_prompt += "\n\nCRITICAL: You MUST respond with ONLY valid JSON. No markdown, no code fences, no explanation."
                    user_prompt += "\n\nCRITICAL: Respond with ONLY valid JSON. No markdown, no code fences, no explanation."
        raise LLMError(f"LLM returned invalid JSON after {retries + 1} attempts: {last_error}\nRaw: {raw[:500]}") from last_error

    def count_tokens(self, text: str) -> int:
        return len(text) // 4