from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    def __init__(self, system_prompt: str | None = None):
        self.system_prompt = system_prompt or (
            "You are an on-call assistant. Answer the question using ONLY the context below. "
            "If the context doesn't contain the answer, say you don't have a runbook for this."
        )

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        ...
