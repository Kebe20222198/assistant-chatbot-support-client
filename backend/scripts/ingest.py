"""
ingest.py — Pipeline d'ingestion : fichiers JSONL scrapés → Qdrant

Étapes :
  1. Lecture des fichiers JSONL (produits par scraper.py)
  2. Découpe en chunks (par section ou taille fixe)
  3. Génération des embeddings (Cohere embed-multilingual-v3.0, API)
  4. Indexation dans Qdrant (vecteurs dense HNSW)

Usage :
  python backend/scripts/ingest.py
  python backend/scripts/ingest.py --source airflow       # une seule source
  python backend/scripts/ingest.py --reset                # vide la collection avant
"""

import os
import sys
import argparse
import json
import re
import time
import uuid
from pathlib import Path

import cohere
from cohere.errors.too_many_requests_error import TooManyRequestsError
from loguru import logger
from tenacity import retry, wait_fixed, stop_after_attempt, retry_if_exception_type
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    HnswConfigDiff,
)

# ── Chemins ────────────────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
_DATA_DIR = _BACKEND_DIR / "data" / "scraped"

sys.path.insert(0, str(_BACKEND_DIR))

# ── Configuration ──────────────────────────────────────────────────────────────
try:
    from app.core.config import settings
    QDRANT_URL = settings.qdrant_url
    QDRANT_API_KEY = settings.qdrant_api_key
    QDRANT_HOST = settings.qdrant_host
    QDRANT_PORT = settings.qdrant_port
    COLLECTION_NAME = settings.qdrant_collection_name
    EMBEDDING_DIM = 1024
    CHUNK_SIZE = settings.chunk_size
    CHUNK_OVERLAP = settings.chunk_overlap
    COHERE_API_KEY = settings.cohere_api_key
except Exception as e:
    logger.warning(f"Impossible de charger app.core.config : {e}")
    QDRANT_URL = os.getenv("QDRANT_URL")
    QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
    QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
    COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "airflow_docs")
    EMBEDDING_DIM = 1024
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "512"))
    CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "64"))
    COHERE_API_KEY = os.getenv("COHERE_API_KEY")

COHERE_EMBEDDING_MODEL = "embed-multilingual-v3.0"
BATCH_SIZE = 96    # max Cohere par appel
BATCH_SLEEP_SECONDS = 35    # pause entre batches (limite 100k tokens/min)
MIN_CHUNK_LENGTH = 100

# ── Client Cohere ──────────────────────────────────────────────────────────────
co = cohere.Client(api_key=COHERE_API_KEY)


# ── 1. Lecture des fichiers JSONL ──────────────────────────────────────────────

def load_jsonl(source: str | None = None) -> list[dict]:
    files = (
        [_DATA_DIR / f"{source}.jsonl"]
        if source
        else list(_DATA_DIR.glob("*.jsonl"))
    )

    if not files:
        raise FileNotFoundError(
            f"Aucun fichier JSONL trouvé dans {_DATA_DIR}.\n"
            "Lancez d'abord : python backend/scripts/scraper.py"
        )

    pages = []
    for file in files:
        if not file.exists():
            logger.warning(f"Fichier introuvable : {file}")
            continue
        with open(file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    pages.append(json.loads(line))
        logger.info(f"Chargé : {file.name} ({len(pages)} pages au total)")

    return pages


# ── 2. Découpe en chunks ───────────────────────────────────────────────────────

def split_by_sections(text: str) -> list[str]:
    pattern = r'\n(?=#{1,3} |\n[A-Z][A-Z ]{10,}\n)'
    sections = re.split(pattern, text)
    return [s.strip() for s in sections if s.strip()]


def split_by_tokens(text: str, chunk_size: int, overlap: int) -> list[str]:
    max_words = int(chunk_size * 0.75)
    overlap_words = int(overlap * 0.75)
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        end = start + max_words
        chunk = " ".join(words[start:end])
        if len(chunk) >= MIN_CHUNK_LENGTH:
            chunks.append(chunk)
        start += max_words - overlap_words

    return chunks


def chunk_page(page: dict) -> list[dict]:
    sections = split_by_sections(page["content"])
    max_words = int(CHUNK_SIZE * 0.75)
    chunks = []

    for section in sections:
        if len(section.split()) <= max_words:
            if len(section) >= MIN_CHUNK_LENGTH:
                chunks.append(section)
        else:
            chunks.extend(split_by_tokens(section, CHUNK_SIZE, CHUNK_OVERLAP))

    result = []
    for i, chunk_text in enumerate(chunks):
        result.append({
            "id": str(uuid.uuid4()),
            "text": chunk_text,
            "metadata": {
                "url":          page["url"],
                "title":        page["title"],
                "source":       page["source"],
                "chunk_index":  i,
                "total_chunks": len(chunks),
                "scraped_at":   page.get("scraped_at", ""),
            },
        })

    return result


def chunk_all_pages(pages: list[dict]) -> list[dict]:
    all_chunks = []
    for page in pages:
        all_chunks.extend(chunk_page(page))

    logger.info(
        f"{len(pages)} pages → {len(all_chunks)} chunks "
        f"(moyenne : {len(all_chunks) // max(len(pages), 1)} chunks/page)"
    )
    return all_chunks


# ── 3. Génération des embeddings via Cohere ────────────────────────────────────

@retry(
    retry=retry_if_exception_type(TooManyRequestsError),
    wait=wait_fixed(61),           # attend 61s avant de réessayer
    stop=stop_after_attempt(5),    # abandonne après 5 tentatives
    before_sleep=lambda rs: logger.warning(
        f"Rate limit Cohere — attente 61s (tentative {rs.attempt_number}/5)..."
    ),
)
def _embed_with_retry(texts: list[str]):
    """Appel Cohere avec retry automatique sur rate limit."""
    return co.embed(
        texts=texts,
        model=COHERE_EMBEDDING_MODEL,
        input_type="search_document",
    )


def generate_embeddings(chunks: list[dict]):
    """
    Génère les embeddings par batch via l'API Cohere.
    - Modèle  : embed-multilingual-v3.0 (anglais + français)
    - Dim     : 1024
    - Batch   : 96 textes max par appel
    - Pause   : 35s entre chaque batch (limite 100k tokens/min)
    - Retry   : automatique si rate limit atteint
    """
    total_batches = (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE
    eta_minutes = (total_batches * BATCH_SLEEP_SECONDS) // 60

    logger.info(
        f"Durée estimée : ~{eta_minutes} minutes ({total_batches} batchs)")

    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i: i + BATCH_SIZE]
        texts = [c["text"] for c in batch]
        batch_num = i // BATCH_SIZE + 1

        logger.debug(
            f"Embedding batch {batch_num}/{total_batches} ({len(texts)} textes)")

        response = _embed_with_retry(texts)

        for chunk, vector in zip(batch, response.embeddings):
            chunk["vector"] = vector

        yield batch

        # Pause pour rester sous la limite de 100k tokens/minute
        # Sauf pour le dernier batch
        if i + BATCH_SIZE < len(chunks):
            logger.debug(
                f"Pause {BATCH_SLEEP_SECONDS}s (rate limit Cohere)...")
            time.sleep(BATCH_SLEEP_SECONDS)


# ── 4. Indexation dans Qdrant ─────────────────────────────────────────────────

def setup_qdrant(client: QdrantClient, reset: bool = False) -> None:
    existing = [c.name for c in client.get_collections().collections]

    if reset and COLLECTION_NAME in existing:
        client.delete_collection(COLLECTION_NAME)
        logger.warning(f"Collection '{COLLECTION_NAME}' supprimée (--reset)")
        existing.remove(COLLECTION_NAME)

    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=EMBEDDING_DIM,
                distance=Distance.COSINE,
            ),
            hnsw_config=HnswConfigDiff(
                m=16,
                ef_construct=100,
            ),
        )
        logger.success(
            f"Collection '{COLLECTION_NAME}' créée (dim={EMBEDDING_DIM})")
    else:
        logger.info(
            f"Collection '{COLLECTION_NAME}' existante — ajout des chunks")


def index_batch(client: QdrantClient, batch: list[dict]) -> None:
    points = [
        PointStruct(
            id=chunk["id"],
            vector=chunk["vector"],
            payload={
                "text": chunk["text"],
                **chunk["metadata"],
            },
        )
        for chunk in batch
    ]
    client.upsert(collection_name=COLLECTION_NAME, points=points)


# ── Pipeline complet ──────────────────────────────────────────────────────────

def run_ingestion(source: str | None = None, reset: bool = False) -> None:
    start_time = time.time()

    # ── Connexion Qdrant ──────────────────────────────────────
    if QDRANT_URL and QDRANT_API_KEY:
        logger.info(f"Connexion à Qdrant Cloud : {QDRANT_URL}")
        client = QdrantClient(
            url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60.0)
    else:
        logger.info(f"Connexion à Qdrant Local : {QDRANT_HOST}:{QDRANT_PORT}")
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=30.0)

    setup_qdrant(client, reset=reset)

    # ── Chargement + Chunking ─────────────────────────────────
    pages = load_jsonl(source)
    if not pages:
        logger.error("Aucune page chargée. Vérifiez vos fichiers JSONL.")
        return

    logger.info("Découpe des pages en chunks...")
    chunks = chunk_all_pages(pages)

    # ── Embedding + Indexation ────────────────────────────────
    logger.info(
        f"Indexation de {len(chunks)} chunks "
        f"via Cohere ({COHERE_EMBEDDING_MODEL})..."
    )
    indexed = 0

    for batch in generate_embeddings(chunks):
        index_batch(client, batch)
        indexed += len(batch)
        logger.info(f"  Indexé : {indexed}/{len(chunks)} chunks")

    # ── Résumé ────────────────────────────────────────────────
    elapsed = time.time() - start_time
    collection_info = client.get_collection(COLLECTION_NAME)

    logger.success("\n─── INGESTION TERMINÉE ─────────────────────")
    logger.success(f"  Durée           : {elapsed / 60:.1f} min")
    logger.success(f"  Pages traitées  : {len(pages)}")
    logger.success(f"  Chunks indexés  : {indexed}")
    logger.success(f"  Points Qdrant   : {collection_info.points_count}")
    logger.success(f"  Collection      : {COLLECTION_NAME}")
    logger.success(f"  Modèle embed    : {COHERE_EMBEDDING_MODEL}")
    logger.success("────────────────────────────────────────────")


# ── Point d'entrée ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline d'ingestion RAG")
    parser.add_argument(
        "--source",
        choices=["airflow", "astronomer_docs", "astronomer_blog"],
        default=None,
        help="Ingérer une seule source (défaut : toutes)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Vider la collection Qdrant avant l'ingestion",
    )
    args = parser.parse_args()

    run_ingestion(source=args.source, reset=args.reset)
