import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.cache import DataCatalogCache
from app.client import MeterNotFoundError, UpstreamPortalError, UrjaPortalClient
from app.config import get_settings
from app.routers import health, hierarchy, meters, transformers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("urja_app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes upstream portal client, warms in-memory cache,
    and handles graceful shutdown.
    """
    settings = get_settings()
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    
    # Initialize client and cache
    client = UrjaPortalClient(settings=settings)
    cache = DataCatalogCache(client=client, settings=settings)
    
    app.state.client = client
    app.state.cache = cache
    
    # Background cache pre-warming
    try:
        logger.info("Pre-warming smart meter catalog...")
        await cache.warm_cache(force=True)
        logger.info("Cache successfully warmed: %d meters indexed", cache.total_meters)
    except Exception as exc:
        logger.warning("Initial cache warming failed (will retry lazily on request): %s", exc)

    yield

    # Clean shutdown
    logger.info("Shutting down %s...", settings.app_name)
    await client.close()


settings = get_settings()

app = FastAPI(
    title="Urja Meter Ops API Service",
    description=(
        "A clean, documented RESTful API service reverse-engineered and built over the "
        "legacy Urja Meter Ops utility portal. Provides programmatic access to smart-meter "
        "nameplates, network topology hierarchy, interval consumption readings, and geospatial queries."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# CORS middleware for downstream consumer access (stateless read-only REST proxy)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# Global Exception Handlers
@app.exception_handler(MeterNotFoundError)
async def meter_not_found_handler(request: Request, exc: MeterNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "message": str(exc)}
    )


@app.exception_handler(UpstreamPortalError)
async def upstream_portal_error_handler(request: Request, exc: UpstreamPortalError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "upstream_error", "message": str(exc), "detail": exc.detail}
    )


# Mount API Routers
app.include_router(meters.router)
app.include_router(transformers.router)
app.include_router(hierarchy.router)
app.include_router(health.router)

# Mount Static Files & Modern Web Client Dashboard
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_dashboard():
        return FileResponse(str(static_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
