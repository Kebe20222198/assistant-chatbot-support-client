"""
Centralized configuration — loads every environment variable the app needs.

Uses pydantic-settings so that:
 • Values come from the .env file at the project root  (or real env vars in Docker).
 • Each field is validated and typed at startup — no silent mis-configs.
 • A single `settings` instance is importable anywhere:
       from app.core.config import settings
"""

from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Resolve project root (.env lives two levels above this file) ─────────
_THIS_DIR = Path(__file__).resolve().parent            # backend/app/core/
_BACKEND_DIR = _THIS_DIR.parent.parent                 # backend/
_PROJECT_ROOT = _BACKEND_DIR.parent                    # project root


class Settings(BaseSettings):
    """All application settings, grouped by concern."""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",                     # silently drop unknown vars
    )

    # ── General ──────────────────────────────────────────────────────────
    app_name: str = "Airflow Support Chatbot"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"

    # ── FastAPI / Uvicorn ────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000"]   # Next.js dev server

    # ── Groq ─────────────────────────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-70b-versatile"
    groq_max_tokens: int = 1024
    groq_temperature: float = 0.1          # low → factual answers

    # ── Cohere (Reranker) ───────────────────────────────────────────────
    cohere_api_key: str = ""

    # ── Embedding Model ──────────────────────────────────────────────────
    # Loaded locally via HuggingFace — no API key needed.
    embedding_model_name: str = "nomic-ai/nomic-embed-text-v1.5"
    embedding_dimension: int = 768              # nomic-embed-text output dim

    # ── Qdrant Vector Store ──────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_collection_name: str = "airflow_docs"
    qdrant_url: str | None = None               # for Qdrant Cloud
    qdrant_api_key: str | None = None           # for Qdrant Cloud

    # ── Ingestion Pipeline ───────────────────────────────────────────────
    # Absolute path to the locally-cloned Airflow docs directory.
    docs_path: str = ""
    chunk_size: int = 512                       # tokens per chunk
    chunk_overlap: int = 64                     # overlap between chunks

    # ── RAG Retrieval ────────────────────────────────────────────────────
    retrieval_top_k: int = 50       # candidats avant reranking  ← changer 5 → 50
    rerank_top_n: int = 5           # passages envoyés au LLM    ← ajouter cette ligne
    similarity_threshold: float = 0.35

@lru_cache()
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed once at startup)."""
    return Settings()


# Convenience alias — import this anywhere:
#   from app.core.config import settings
settings = get_settings()
