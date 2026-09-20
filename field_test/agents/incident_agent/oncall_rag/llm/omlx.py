from __future__ import annotations

from openai import OpenAI

from oncall_rag.llm.base import LLMClient


class OmlxClient(LLMClient):
    def __init__(self, endpoint: str, model: str, api_key: str = "", system_prompt: str | None = None):
        super().__init__(system_prompt)
        self.client = OpenAI(base_url=f"{endpoint.rstrip('/')}/v1", api_key=api_key)
        self.model = model

    def generate(self, prompt: str, **kwargs) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=1024,
        )
        return response.choices[0].message.content or ""
