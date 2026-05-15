from loguru import logger
from functools import lru_cache
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from app.core.config import settings
from app.models.schemas import SourceDocument


class QdrantRetriever:
    """Client Qdrant + modèle d'embedding avec initialisation paresseuse.

    L'instance évite de charger le modèle d'embedding au moment de l'import
    du module (ce qui provoquait des OOM au démarrage). Le modèle est
    chargé à la première requête de recherche.
    """

    def __init__(self):
        logger.info("Initialisation du client Qdrant (Retrieval)...")
        if settings.qdrant_url and settings.qdrant_api_key:
            self.client = QdrantClient(
                url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        else:
            self.client = QdrantClient(
                host=settings.qdrant_host, port=settings.qdrant_port)

        self.collection_name = settings.qdrant_collection_name
        self.model = None

    def _ensure_model(self):
        if self.model is None:
            logger.info(
                f"Chargement du modèle d'embedding : {settings.embedding_model_name}")
            self.model = SentenceTransformer(
                settings.embedding_model_name, trust_remote_code=True)

    def _encode(self, text: str) -> list[float]:
        self._ensure_model()
        return self.model.encode(text, normalize_embeddings=True).tolist()

    def search(self, query: str, top_k: int = 5) -> list[SourceDocument]:
        """Convertit la question en vecteur et cherche les chunks les plus proches."""
        vector = self._encode(f"search_query: {query}")

        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=top_k,
            score_threshold=settings.similarity_threshold
        )

        sources = []
        for res in response.points:
            payload = res.payload or {}
            sources.append(SourceDocument(
                url=payload.get("url", ""),
                title=payload.get("title", ""),
                content=payload.get("text", ""),
                score=res.score
            ))

        return sources


@lru_cache()
def get_retriever() -> QdrantRetriever:
    """Retourne une instance singleton de `QdrantRetriever` (lazy)."""
    return QdrantRetriever()
