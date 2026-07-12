from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    pinecone_api_key: str = Field(alias="PINECONE_API_KEY")
    langchain_api_key: str | None = Field(default=None, alias="LANGCHAIN_API_KEY")
    langchain_tracing_v2: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    langchain_project: str | None = Field(default=None, alias="LANGCHAIN_PROJECT")
    cohere_api_key: str | None = Field(default=None, alias="COHERE_API_KEY")
    index_name: str = Field(default="equip-docs-rag", alias="INDEX_NAME")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    # Retrieval namespace — default = shipped v4 config (semantic). Override via RETRIEVAL_NAMESPACE
    # for the eval A/B (e.g. fixed_500_50 for the v1/v2 baselines).
    retrieval_namespace: str = Field(default="semantic", alias="RETRIEVAL_NAMESPACE")
    # Retrieval depth (top-k) — default = shipped v4 config (10). Override via RETRIEVAL_K for the
    # eval A/B (e.g. 5 for the v1/v2/v3 runs).
    retrieval_k: int = Field(default=10, alias="RETRIEVAL_K")


@lru_cache
def get_settings() -> "Settings":
    return Settings()


def _mask(value: str | None) -> str:
    if not value:
        return "<unset>"
    return f"set (len={len(value)}, …{value[-4:]})" if len(value) > 4 else "set"


if __name__ == "__main__":
    s = get_settings()
    print("Resolved settings:")
    print(f"  OPENAI_API_KEY       = {_mask(s.openai_api_key)}")
    print(f"  PINECONE_API_KEY     = {_mask(s.pinecone_api_key)}")
    print(f"  LANGCHAIN_API_KEY    = {_mask(s.langchain_api_key)}")
    print(f"  LANGCHAIN_TRACING_V2 = {s.langchain_tracing_v2}")
    print(f"  LANGCHAIN_PROJECT    = {s.langchain_project or '<unset>'}")
    print(f"  COHERE_API_KEY       = {_mask(s.cohere_api_key)}")
    print(f"  INDEX_NAME           = {s.index_name}")
    print(f"  LLM_MODEL            = {s.llm_model}")
    print(f"  RETRIEVAL_NAMESPACE  = {s.retrieval_namespace}")
    print(f"  RETRIEVAL_K          = {s.retrieval_k}")

    langsmith_ok = bool(s.langchain_api_key) and bool(s.langchain_project)
    print(
        f"\nLangSmith env detected: {langsmith_ok} "
        f"(tracing_v2={s.langchain_tracing_v2}, project={s.langchain_project or '<unset>'})"
    )
