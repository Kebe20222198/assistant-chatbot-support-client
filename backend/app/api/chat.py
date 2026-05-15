from fastapi import APIRouter, HTTPException
from loguru import logger

from app.models.schemas import ChatRequest, ChatResponse
from app.services.rag_pipeline import generate_rag_response

router = APIRouter()

@router.post("/chat", response_model=ChatResponse, summary="Générer une réponse avec le RAG")
def chat_endpoint(request: ChatRequest):
    """
    Reçoit l'historique de la conversation et la nouvelle question.
    Génère une réponse via Qdrant + Groq.
    Si la réponse n'est pas connue, génère automatiquement un ticket pour l'escalade humaine.
    """
    if not request.messages:
        raise HTTPException(status_code=400, detail="La liste des messages ne peut pas être vide.")
        
    try:
        response = generate_rag_response(request)
        return response
    except Exception as e:
        logger.error(f"Erreur lors de la génération de la réponse RAG : {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {str(e)}")
