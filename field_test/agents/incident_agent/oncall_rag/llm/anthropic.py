from __future__ import annotations

from anthropic import Anthropic

from oncall_rag.llm.base import LLMClient


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str, model: str, system_prompt: str | None = None):
        super().__init__(system_prompt)
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def generate(self, prompt: str, **kwargs) -> str:
        response = self.client.messages.create(
            model=self.model,
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1024,
        )
        block = response.content[0] if response.content else None
        if block and hasattr(block, "text"):
            return block.text
        return ""
