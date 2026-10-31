import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api.routes import router as api_router
from app.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("acsa.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler for startup and shutdown routines."""
    logger.info("Initializing ACSA Autonomous Software Agent Backend...")
    # Ensure directories are ready
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    settings.zips_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Storage directories initialized at: {settings.resolved_storage_dir}")
    logger.info(f"Configured LLM model: {settings.groq_model}")
    # Never log the key itself - only where it was loaded from
    logger.info(f"GROQ_API_KEY loaded from: {settings.groq_api_key_source}")
    if not settings.groq_api_key:
        logger.error("GROQ_API_KEY is empty - every run will fail at the Analyst step.")
    yield
    logger.info("ACSA Backend shutting down...")


app = FastAPI(
    title="ACSA - Autonomous Code Writing and Shipping Agent API",
    description="Multi-agent software engineering automation powered by LangGraph, LangChain, and Groq.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for frontend connectivity
origins = settings.cors_origins
if isinstance(origins, list):
    cors_allowed = origins
else:
    cors_allowed = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router)


@app.get("/health", summary="Health check alias")
def health_alias():
    return {"status": "healthy", "service": "ACSA Backend", "version": "1.0.0"}


@app.get("/", summary="ACSA API Root")
def root():
    return {
        "service": "ACSA - Autonomous Code Writing and Shipping Agent",
        "version": "1.0.0",
        "status": "online",
        "docs_url": "/docs",
        "model": settings.groq_model,
        "nodes": [
            "1. Requirements Analyst",
            "2. Code Architect",
            "3. Code Writer",
            "4. Tester",
            "5. Code Reviewer",
        ],
    }


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        reload_dirs=["app"],
        reload_excludes=["storage", "storage/*", "*.zip", "*.json", "storage/**"],
    )
