from groq import Groq
from loguru import logger
import cohere

from app.core.config import settings
from app.models.schemas import ChatRequest, ChatResponse, TicketCreate
from app.services.vector_store import retriever
from app.services.ticket_service import TicketService

# Initialisation du client Groq
groq_client = Groq(api_key=settings.groq_api_key)
# Initialisation du client Cohere
cohere_client = cohere.Client(api_key=settings.cohere_api_key) if settings.cohere_api_key else None

SYSTEM_PROMPT = """Tu es l'assistant de support officiel d'Apache Airflow et Astronomer.
Tu dois répondre à la question de l'utilisateur EN UTILISANT UNIQUEMENT le contexte fourni ci-dessous.
Si la réponse ne se trouve pas dans le contexte, n'invente rien. Réponds EXACTEMENT par ce mot-clé et rien d'autre : ESCALATE
Si tu trouves la réponse, réponds de manière claire, professionnelle et précise en français.

CONTEXTE :
{context}
"""

def generate_rag_response(request: ChatRequest) -> ChatResponse:
    user_query = request.messages[-1].content
    logger.info(f"Question reçue : {user_query}")
    
    # 1. Recherche dans Qdrant
    logger.info("Recherche de documents pertinents dans Qdrant...")
    sources = retriever.search(user_query, top_k=settings.retrieval_top_k)
    
    if not sources:
        logger.warning("Aucun document pertinent trouvé dans la base.")
        context_str = "Aucun document trouvé."
    else:
        # 1.5 Reranking avec Cohere
        if cohere_client:
            logger.info("Reranking des documents avec Cohere...")
            docs_content = [s.content for s in sources]
            
            rerank_results = cohere_client.rerank(
                model="rerank-multilingual-v3.0",
                query=user_query,
                documents=docs_content,
                top_n=settings.rerank_top_n
            )
            
            # Conserver uniquement les N meilleurs documents réordonnés
            reranked_sources = []
            for result in rerank_results.results:
                original_source = sources[result.index]
                original_source.score = result.relevance_score
                reranked_sources.append(original_source)
            
            sources = reranked_sources
        else:
            # Fallback si Cohere n'est pas configuré
            sources = sources[:settings.rerank_top_n]
            
        context_str = "\n\n".join([f"Source: {s.title}\n{s.content}" for s in sources])
        
    # 2. Construction du prompt pour Groq
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(context=context_str)}
    ]
    
    # On ajoute l'historique de l'utilisateur
    for msg in request.messages:
        messages.append({"role": msg.role, "content": msg.content})
        
    # 3. Appel à Groq
    logger.info(f"Appel au modèle LLM ({settings.groq_model})...")
    completion = groq_client.chat.completions.create(
        model=settings.groq_model,
        messages=messages,
        temperature=settings.groq_temperature,
        max_tokens=settings.groq_max_tokens
    )
    
    answer = completion.choices[0].message.content.strip()
    
    # 4. Logique d'escalade (Human Handoff)
    if "ESCALATE" in answer:
        logger.warning("Le bot ne connait pas la réponse -> Escalade vers un humain.")
        
        # Format de l'historique pour le ticket
        history_str = "\n".join([f"{m.role}: {m.content}" for m in request.messages])
        
        # Création du ticket
        ticket_data = TicketCreate(
            user_email=request.user_email or "Anonyme",
            conversation_history=history_str
        )
        ticket_id = TicketService.create_ticket(ticket_data)
        
        response_msg = (
            f"Je suis désolé, mais je ne trouve pas la réponse exacte dans ma documentation.\n\n"
            f"J'ai créé un ticket (N° `{ticket_id}`) pour l'équipe de support technique. "
            f"Un expert humain va prendre le relais et vous recontacter rapidement."
        )
        
        return ChatResponse(
            answer=response_msg,
            sources=sources, # On peut retourner les sources trouvées quand même ou les vider
            is_escalated=True,
            ticket_id=ticket_id
        )

    # Réponse standard
    logger.info("Réponse générée avec succès.")
    return ChatResponse(
        answer=answer,
        sources=sources,
        is_escalated=False
    )
