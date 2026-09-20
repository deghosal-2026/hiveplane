from __future__ import annotations

from openai import OpenAI

from oncall_rag.llm.base import LLMClient


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str, model: str, system_prompt: str | None = None):
        super().__init__(system_prompt)
        self.client = OpenAI(api_key=api_key)
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
