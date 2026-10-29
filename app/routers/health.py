import logging
from fastapi import APIRouter, Depends, Request

from app.cache import DataCatalogCache
from app.config import get_settings
from app.models import HealthResponse

logger = logging.getLogger("urja_health")
router = APIRouter(prefix="/api/v1/health", tags=["Health & Status"])


def get_cache(request: Request) -> DataCatalogCache:
    return request.app.state.cache


@router.get(
    "",
    response_model=HealthResponse,
    summary="Service health and upstream connectivity status",
    description="Check operational status, upstream Urja portal session validity, and in-memory cache status."
)
async def check_health(
    cache: DataCatalogCache = Depends(get_cache)
):
    settings = get_settings()
    upstream_ok = False
    try:
        # Verify upstream session token validity
        await cache.client.ensure_authenticated()
        upstream_ok = True
    except Exception as exc:
        logger.warning("Upstream health check probe failed: %s", exc)

    status = "ok" if upstream_ok and cache.is_warmed else "degraded"

    return HealthResponse(
        status=status,
        version=settings.app_version,
        upstreamConnected=upstream_ok,
        cachedMetersCount=cache.total_meters,
        cacheAgeSeconds=round(cache.cache_age_seconds, 1) if cache.cache_age_seconds else None
    )
