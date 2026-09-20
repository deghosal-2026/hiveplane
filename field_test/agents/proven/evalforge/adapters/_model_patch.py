"""Shared model/provider redirection for adapters.

Called by each adapter before building/invoking the agent. Sets environment
defaults (``OPENAI_API_KEY``, ``OPENAI_BASE_URL``, ``ANTHROPIC_API_KEY``,
``GEMINI_API_KEY``) and monkey-patches framework-specific model classes to
inject the configured model so user agents don't need manual plumbing for
``EVALFORGE_FIELD_ENDPOINT``, ``EVALFORGE_FIELD_MODEL``, and
``EVALFORCE_FORCE_MODEL``.
"""

from __future__ import annotations

import os
from typing import Any

_MODEL = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")
_ENDPOINT = os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1")
_FORCE = os.environ.get("EVALFORCE_FORCE_MODEL", "0") in {"1", "true", "yes"}


def apply_model_patch() -> None:
    """Set env defaults and patch framework model classes.

    Safe to call multiple times — each framework patch is applied at most
    once and ``ImportError`` on missing framework packages is swallowed.
    """
# mypy: allow-untyped-defs, ignore-errors
    os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
    os.environ.setdefault("OPENAI_BASE_URL", _ENDPOINT)
    os.environ.setdefault("ANTHROPIC_API_KEY", "omlx-test")
    os.environ.setdefault("GEMINI_API_KEY", "omlx-test")

    if _ENDPOINT:
        os.environ.setdefault("ANTHROPIC_BASE_URL", _ENDPOINT)

    _patch_langchain_openai()
    _patch_smolagents()
    _patch_autogen()
    _patch_llamaindex()
    _patch_claude()
    _patch_adk()


def _patch_langchain_openai() -> None:
    try:
        from langchain_openai import ChatOpenAI as _CO
        if getattr(_CO, "_evalforge_patched", False):
            return
        _orig = _CO.__init__

        def _patched(self: Any, *a: Any, **kw: Any) -> Any:
            if _FORCE:
                kw["model"] = _MODEL
            elif "model" not in kw and "model_name" not in kw:
                kw["model"] = _MODEL
            return _orig(self, *a, **kw)

        _CO.__init__ = _patched  # type: ignore[method-assign,attr-defined]
        _CO._evalforge_patched = True  # type: ignore[method-assign,attr-defined]
    except Exception:  # noqa: S110
        pass


def _patch_smolagents() -> None:
    try:
        from smolagents.models import LiteLLMModel as _LM  # type: ignore[import-untyped]
        if getattr(_LM, "_evalforge_patched", False):
            return
        _orig = _LM.__init__

        def _patched(self: Any, *a: Any, **kw: Any) -> Any:
            if _FORCE:
                kw["model_id"] = "openai/" + _MODEL
            elif "model_id" not in kw and "model" not in kw:
                kw["model_id"] = "openai/" + _MODEL
            return _orig(self, *a, **kw)

        _LM.__init__ = _patched  # type: ignore[method-assign,attr-defined]
        _LM._evalforge_patched = True  # type: ignore[method-assign,attr-defined]
    except Exception:  # noqa: S110
        pass


def _patch_autogen() -> None:
    try:
        from autogen_ext.models.openai import OpenAIChatCompletionClient as _OC
        if getattr(_OC, "_evalforge_patched", False):
            return
        _orig = _OC.__init__

        def _patched(self: Any, *a: Any, **kw: Any) -> Any:
            if _FORCE:
                kw["model"] = _MODEL
            elif "model" not in kw:
                kw["model"] = _MODEL
            return _orig(self, *a, **kw)

        _OC.__init__ = _patched  # type: ignore[method-assign,attr-defined]
        _OC._evalforge_patched = True  # type: ignore[method-assign,attr-defined]
    except Exception:  # noqa: S110
        pass


def _patch_llamaindex() -> None:
    try:
        from llama_index.llms.openai import OpenAI as _OI
        if getattr(_OI, "_evalforge_patched", False):
            return
        _orig = _OI.__init__

        def _patched(self: Any, *a: Any, **kw: Any) -> Any:
            if _FORCE:
                kw["model"] = _MODEL
            elif "model" not in kw:
                kw["model"] = _MODEL
            return _orig(self, *a, **kw)

        _OI.__init__ = _patched  # type: ignore[method-assign,attr-defined]
        _OI._evalforge_patched = True  # type: ignore[method-assign,attr-defined]
    except Exception:  # noqa: S110
        pass


def _patch_claude() -> None:
    try:
        from claude_agent_sdk import ClaudeAgentOptions as _CAO
        from claude_agent_sdk.client import ClaudeSDKClient as _CSC
        if getattr(_CSC, "_evalforge_patched", False):
            return
        _orig = _CSC.__init__

        def _patched(self: Any, *a: Any, **kw: Any) -> Any:
            if "options" in kw:
                opts = kw["options"]
                if hasattr(opts, "model") and _FORCE:
                    opts.model = _MODEL
            elif _FORCE:
                kw["options"] = _CAO(model=_MODEL)
            return _orig(self, *a, **kw)

        _CSC.__init__ = _patched  # type: ignore[method-assign,attr-defined]
        _CSC._evalforge_patched = True  # type: ignore[method-assign,attr-defined]
    except Exception:  # noqa: S110
        pass


def _patch_adk() -> None:
    try:
        from google.adk.models.lite_llm import LiteLlm as _LL
        if getattr(_LL, "_evalforge_patched", False):
            return
        _orig = _LL.__init__

        def _patched(self: Any, *a: Any, **kw: Any) -> Any:
            if _FORCE:
                kw["model"] = _MODEL
            elif "model" not in kw:
                kw["model"] = _MODEL
            return _orig(self, *a, **kw)

        _LL.__init__ = _patched  # type: ignore[method-assign,attr-defined]
        _LL._evalforge_patched = True  # type: ignore[method-assign,attr-defined]
    except Exception:  # noqa: S110
        pass
