"""
ingest.py — Pipeline d'ingestion : fichiers JSONL scrapés → Qdrant

Étapes :
  1. Lecture des fichiers JSONL (produits par scraper.py)
  2. Découpe en chunks (par section ou taille fixe)
  3. Génération des embeddings (nomic-embed-text, local)
  4. Indexation dans Qdrant (vecteurs dense + index BM25 sparse)

Usage :
  python backend/scripts/ingest.py
  python backend/scripts/ingest.py --source airflow       # une seule source
  python backend/scripts/ingest.py --reset                # vide la collection avant
"""

import argparse
import json
import re
import time
import uuid
from pathlib import Path
from typing import Generator

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    HnswConfigDiff,
)
from sentence_transformers import SentenceTransformer

# ── Chemins ────────────────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent       # backend/scripts/
_BACKEND_DIR = _SCRIPTS_DIR.parent                   # backend/
_DATA_DIR = _BACKEND_DIR / "data" / "scraped"        # backend/data/scraped/

import sys
sys.path.insert(0, str(_BACKEND_DIR))

# ── Configuration (peut être surchargée via .env) ─────────────────────────────
try:
    from app.core.config import settings
    QDRANT_HOST          = settings.qdrant_host
    QDRANT_PORT          = settings.qdrant_port
    QDRANT_URL           = settings.qdrant_url
    QDRANT_API_KEY       = settings.qdrant_api_key
    COLLECTION_NAME      = settings.qdrant_collection_name
    EMBEDDING_MODEL_NAME = settings.embedding_model_name
    EMBEDDING_DIM        = settings.embedding_dimension
    CHUNK_SIZE           = settings.chunk_size
    CHUNK_OVERLAP        = settings.chunk_overlap
except Exception as e:
    logger.warning(f"Impossible de charger app.core.config: {e}")
    # Valeurs par défaut si le module config n'est pas accessible
    QDRANT_HOST          = "localhost"
    QDRANT_PORT          = 6333
    QDRANT_URL           = None
    QDRANT_API_KEY       = None
    COLLECTION_NAME      = "airflow_docs"
    EMBEDDING_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
    EMBEDDING_DIM        = 768
    CHUNK_SIZE           = 512
    CHUNK_OVERLAP        = 64

BATCH_SIZE = 64        # nombre de chunks envoyés à Qdrant par batch
MIN_CHUNK_LENGTH = 100 # ignorer les chunks trop courts


# ── 1. Lecture des fichiers JSONL ──────────────────────────────────────────────

def load_jsonl(source: str | None = None) -> list[dict]:
    """
    Charge les pages scrapées depuis les fichiers JSONL.
    Si source est spécifié, ne charge que ce fichier.
    """
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
    """
    Découpe le texte aux titres de sections.
    Détecte les patterns Markdown (##) et les lignes en majuscules.
    """
    pattern = r'\n(?=#{1,3} |\n[A-Z][A-Z ]{10,}\n)'
    sections = re.split(pattern, text)
    return [s.strip() for s in sections if s.strip()]


def split_by_tokens(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Découpe par nombre de mots approximatif (1 token ≈ 0.75 mot).
    Utilisé quand les sections sont trop grandes ou absentes.
    """
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
    """
    Découpe une page en chunks et attache les métadonnées.
    Stratégie :
      - D'abord découper par sections
      - Si une section est trop grande → re-découper par tokens
    """
    chunks = []
    sections = split_by_sections(page["content"])

    for section in sections:
        word_count = len(section.split())
        max_words = int(CHUNK_SIZE * 0.75)

        if word_count <= max_words:
            # Section de taille raisonnable → 1 chunk
            if len(section) >= MIN_CHUNK_LENGTH:
                chunks.append(section)
        else:
            # Section trop grande → re-découper
            sub_chunks = split_by_tokens(section, CHUNK_SIZE, CHUNK_OVERLAP)
            chunks.extend(sub_chunks)

    # Attacher les métadonnées à chaque chunk
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
    """Découpe toutes les pages en chunks."""
    all_chunks = []
    for page in pages:
        page_chunks = chunk_page(page)
        all_chunks.extend(page_chunks)

    logger.info(
        f"{len(pages)} pages → {len(all_chunks)} chunks "
        f"(moyenne : {len(all_chunks) // max(len(pages), 1)} chunks/page)"
    )
    return all_chunks


# ── 3. Génération des embeddings ──────────────────────────────────────────────

def load_embedding_model() -> SentenceTransformer:
    """Charge le modèle d'embedding en local (téléchargé à la première utilisation)."""
    logger.info(f"Chargement du modèle d'embedding : {EMBEDDING_MODEL_NAME}")
    model = SentenceTransformer(
        EMBEDDING_MODEL_NAME,
        trust_remote_code=True,      # requis pour nomic-embed-text
    )
    logger.success("Modèle d'embedding chargé")
    return model


def generate_embeddings(
    model: SentenceTransformer,
    chunks: list[dict],
) -> Generator[list[dict], None, None]:
    """
    Génère les embeddings par batch.
    Yields des batches de chunks enrichis avec leur vecteur.
    """
    texts = [chunk["text"] for chunk in chunks]

    for i in range(0, len(texts), BATCH_SIZE):
        batch_texts = texts[i : i + BATCH_SIZE]
        batch_chunks = chunks[i : i + BATCH_SIZE]

        # nomic-embed-text nécessite un préfixe selon la tâche
        prefixed = [f"search_document: {t}" for t in batch_texts]

        vectors = model.encode(
            prefixed,
            normalize_embeddings=True,  # cosine similarity
            show_progress_bar=False,
        ).tolist()

        for chunk, vector in zip(batch_chunks, vectors):
            chunk["vector"] = vector

        logger.debug(
            f"Embeddings batch {i // BATCH_SIZE + 1}"
            f"/{(len(texts) + BATCH_SIZE - 1) // BATCH_SIZE}"
        )
        yield batch_chunks


# ── 4. Indexation dans Qdrant ─────────────────────────────────────────────────

def setup_qdrant(client: QdrantClient, reset: bool = False) -> None:
    """
    Crée (ou recrée) la collection Qdrant.
    HNSW pour la recherche dense approximative.
    """
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
                m=16,                # connexions par nœud (précision vs mémoire)
                ef_construct=100,    # qualité de construction de l'index
            ),
        )
        logger.success(f"Collection '{COLLECTION_NAME}' créée")
    else:
        logger.info(f"Collection '{COLLECTION_NAME}' existante — ajout des nouveaux chunks")


def index_batch(client: QdrantClient, batch: list[dict]) -> None:
    """Envoie un batch de chunks dans Qdrant."""
    points = [
        PointStruct(
            id=chunk["id"],
            vector=chunk["vector"],
            payload={
                "text":  chunk["text"],
                **chunk["metadata"],
            },
        )
        for chunk in batch
    ]
    client.upsert(collection_name=COLLECTION_NAME, points=points)


# ── Pipeline complet ──────────────────────────────────────────────────────────

def run_ingestion(source: str | None = None, reset: bool = False) -> None:
    """
    Orchestre tout le pipeline :
      JSONL → chunks → embeddings → Qdrant
    """
    start_time = time.time()

    # ── Connexion Qdrant ──────────────────────────────────────
    if QDRANT_URL and QDRANT_API_KEY:
        logger.info(f"Connexion à Qdrant Cloud : {QDRANT_URL}")
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60.0)
    else:
        logger.info(f"Connexion à Qdrant Local : {QDRANT_HOST}:{QDRANT_PORT}")
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=30.0)
    setup_qdrant(client, reset=reset)

    # ── Chargement des pages ──────────────────────────────────
    pages = load_jsonl(source)
    if not pages:
        logger.error("Aucune page chargée. Vérifiez vos fichiers JSONL.")
        return

    # ── Chunking ──────────────────────────────────────────────
    logger.info("Découpe des pages en chunks...")
    chunks = chunk_all_pages(pages)

    # ── Embedding + Indexation ────────────────────────────────
    model = load_embedding_model()

    logger.info(f"Indexation de {len(chunks)} chunks dans Qdrant...")
    indexed = 0

    for batch in generate_embeddings(model, chunks):
        index_batch(client, batch)
        indexed += len(batch)
        logger.info(f"  Indexé : {indexed}/{len(chunks)} chunks")

    # ── Résumé ────────────────────────────────────────────────
    elapsed = time.time() - start_time
    collection_info = client.get_collection(COLLECTION_NAME)

    logger.success("\n─── INGESTION TERMINÉE ────────────────────")
    logger.success(f"  Durée          : {elapsed:.1f}s")
    logger.success(f"  Pages traitées : {len(pages)}")
    logger.success(f"  Chunks indexés : {indexed}")
    logger.success(f"  Points Qdrant  : {collection_info.points_count}")
    logger.success(f"  Collection     : {COLLECTION_NAME}")
    logger.success("───────────────────────────────────────────")


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