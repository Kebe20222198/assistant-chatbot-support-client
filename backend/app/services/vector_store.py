from loguru import logger
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from app.core.config import settings
from app.models.schemas import SourceDocument

class QdrantRetriever:
    def __init__(self):
        logger.info("Initialisation du client Qdrant (Retrieval)...")
        if settings.qdrant_url and settings.qdrant_api_key:
            self.client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        else:
            self.client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
            
        self.collection_name = settings.qdrant_collection_name
        
        logger.info(f"Chargement du modèle d'embedding : {settings.embedding_model_name}")
        self.model = SentenceTransformer(
            settings.embedding_model_name,
            trust_remote_code=True
        )

    def search(self, query: str, top_k: int = 5) -> list[SourceDocument]:
        """
        Convertit la question en vecteur et cherche les chunks les plus proches.
        """
        # Prefix "search_query:" for nomic-embed-text when querying
        vector = self.model.encode(f"search_query: {query}", normalize_embeddings=True).tolist()
        
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

# Instance globale
retriever = QdrantRetriever()
