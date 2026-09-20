"""Agent adapter: watch decorator, inject context managers, framework adapters."""

from cauterule.adapter.decorator import watch
from cauterule.adapter.inject import ainject, inject

__all__ = ["ainject", "inject", "watch"]
