import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.database import init_db
from app.core.logging_config import logger
from app.routers.estimation import router as estimation_router
from app.routers.refinement import router as refinement_router
from app.routers.analysis import router as analysis_router
from app.routers.chat import router as chat_router
from app.routers.projects import router as projects_router
from app.routers.payments import router as payments_router
from app.services.gemini_service import gemini_service
from app.services.firebase_service import firebase_service

# ── Rate Limiter ──
limiter = Limiter(key_func=get_remote_address)


# ── Lifespan (startup/shutdown) ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Project Builder API...")
    await init_db()
    logger.info("Database initialized.")
    firebase_service.initialize()
    logger.info("Firebase initialized.")
    yield
    # Shutdown
    logger.info("Shutting down Project Builder API...")
    await gemini_service.close()
    logger.info("Gemini service closed.")


# ── App ──
app = FastAPI(
    title="Project Builder Cost Estimator API",
    version="2.0.0",
    description=(
        "Comprehensive software project analysis platform powered by Gemini AI. "
        "Estimates costs, generates roadmaps, forecasts revenue, analyzes competitors, "
        "assesses risks, builds teams, compares tech stacks, and provides expert chat."
    ),
    lifespan=lifespan,
)

# ── Rate Limiting ──
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──
try:
    origins = json.loads(settings.cors_origins)
except (json.JSONDecodeError, TypeError):
    origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global Exception Handler (no internal detail leaking) ──
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s | Path: %s", exc, request.url.path, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred. Please try again later."},
    )


# ── Routers ──
app.include_router(estimation_router, prefix="/api/v2", tags=["Estimation"])
app.include_router(refinement_router, prefix="/api/v2", tags=["Idea Refinement"])
app.include_router(analysis_router, prefix="/api/v2", tags=["Analysis"])
app.include_router(chat_router, prefix="/api/v2", tags=["Expert Chat"])
app.include_router(projects_router, prefix="/api/v2", tags=["Projects"])
app.include_router(payments_router, prefix="/api/v2", tags=["Payments"])

# Keep v1 backward compatibility
app.include_router(estimation_router, prefix="/api/v1", tags=["Estimation (v1)"])


# ── Health Check ──
@app.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "version": "2.0.0"}
