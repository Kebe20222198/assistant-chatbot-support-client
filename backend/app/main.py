from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.core.config import settings
from app.api.chat import router as chat_router

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="API du Chatbot de Support (Airflow/Astronomer)"
)

# Configuration CORS (Cross-Origin Resource Sharing)
# Permet au frontend (Next.js) de communiquer avec cette API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclusion des routes
app.include_router(chat_router, prefix="/api", tags=["Chat"])

@app.on_event("startup")
async def startup_event():
    logger.info(f"Démarrage de {settings.app_name} (v{settings.app_version})")

@app.get("/health", tags=["Système"])
def health_check():
    """Endpoint de statut pour vérifier si l'API est en ligne."""
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}

if __name__ == "__main__":
    import uvicorn
    # Ceci est utilisé seulement si le script est lancé directement
    uvicorn.run("app.main:app", host=settings.api_host, port=settings.api_port, reload=True)
