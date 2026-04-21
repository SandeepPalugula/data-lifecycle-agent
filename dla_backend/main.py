"""
main.py
FastAPI application entry point.
Registers all routers, middleware, and startup events.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from .config import settings
from .database import engine
from .models import Base
from .routers import auth, conversations, scheduler, decisions, audit, costs

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables if they don't exist (dev only — use Alembic in prod)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown: dispose connection pool
    await engine.dispose()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Data Lifecycle Agent — API backend for cost-aware conversation storage optimisation.",
    lifespan=lifespan,
)

# CORS — restrict origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(scheduler.router)
app.include_router(decisions.router)
app.include_router(audit.router)
app.include_router(costs.router)

@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": settings.VERSION}

@app.get("/", tags=["system"])
async def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
        "redoc": "/redoc",
    }
