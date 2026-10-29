from typing import Any, Dict
from fastapi import APIRouter, Depends, Request

from app.cache import DataCatalogCache

router = APIRouter(prefix="/api/v1/hierarchy", tags=["Network Hierarchy"])


def get_cache(request: Request) -> DataCatalogCache:
    return request.app.state.cache


@router.get(
    "",
    response_model=Dict[str, Any],
    summary="Get reconstructed utility network topology tree",
    description="Returns the full hierarchical electrical network structure: Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT -> Connected Meters."
)
async def get_network_hierarchy(
    cache: DataCatalogCache = Depends(get_cache)
):
    await cache.warm_cache()
    return cache.build_network_hierarchy_tree()
