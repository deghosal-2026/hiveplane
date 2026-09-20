from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic import BaseModel


OMLX_AVAILABLE_MODELS = [
    "Llama-3.2-3B-Instruct-4bit",
    "DeepSeek-R1-Distill-Qwen-14B-4bit",
    "Qwen3.5-9B-MLX-4bit",
]


class Config(BaseModel):
    llm_provider: str = Field(default="omlx")
    omlx_endpoint: str = Field(default="http://localhost:11434")
    omlx_model: str = Field(default="qwen2.5-coder:7b")
    omlx_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)
    openai_model: str = Field(default="gpt-4o-mini")
    anthropic_api_key: str | None = Field(default=None)
    anthropic_model: str = Field(default="claude-3-haiku-20240307")
    chroma_path: str = Field(default="./data/chroma_db")
    chunk_size: int = Field(default=500)
    chunk_overlap: int = Field(default=50)
    top_k: int = Field(default=5)
    embedding_model: str = Field(default="all-MiniLM-L6-v2")
    discord_bot_token: str | None = Field(default=None)
    chunking_strategy: str = Field(default="header-split")

    @field_validator("chunking_strategy")
    @classmethod
    def validate_chunking(cls, v: str) -> str:
        if v not in ("recursive", "header-split", "fixed-size"):
            raise ValueError(f"Invalid chunking strategy: {v}")
        return v

    @field_validator("llm_provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if v not in ("omlx", "openai", "anthropic"):
            raise ValueError(f"Invalid LLM provider: {v}")
        return v

    @field_validator("chunk_overlap")
    @classmethod
    def validate_overlap(cls, v: int) -> int:
        if v < 0:
            raise ValueError("chunk_overlap must be >= 0")
        return v

    def model_post_init(self, __context) -> None:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be < chunk_size ({self.chunk_size})"
            )


_ENV_MAP = {
    "LLM_PROVIDER": "llm_provider",
    "OMLX_ENDPOINT": "omlx_endpoint",
    "OMLX_MODEL": "omlx_model",
    "OMLX_API_KEY": "omlx_api_key",
    "OPENAI_API_KEY": "openai_api_key",
    "OPENAI_MODEL": "openai_model",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "ANTHROPIC_MODEL": "anthropic_model",
    "CHROMA_PATH": "chroma_path",
    "CHUNK_SIZE": "chunk_size",
    "CHUNK_OVERLAP": "chunk_overlap",
    "TOP_K": "top_k",
    "EMBEDDING_MODEL": "embedding_model",
    "CHUNKING_STRATEGY": "chunking_strategy",
    "DISCORD_BOT_TOKEN": "discord_bot_token",
}


def load_config(env_file: str = ".env") -> Config:
    env_path = Path(env_file)
    overrides = {}
    if env_path.exists():
        from dotenv import dotenv_values
        raw = dotenv_values(env_path)
        for env_key, field_name in _ENV_MAP.items():
            val = raw.get(env_key)
            if val is not None:
                overrides[field_name] = val
    return Config(**overrides)
