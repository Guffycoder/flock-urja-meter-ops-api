from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request

from app.cache import DataCatalogCache
from app.models import DistributionTransformer, MeterSummary

router = APIRouter(prefix="/api/v1/transformers", tags=["Transformers"])


def get_cache(request: Request) -> DataCatalogCache:
    return request.app.state.cache


@router.get(
    "",
    response_model=List[DistributionTransformer],
    summary="List distribution transformers",
    description="Retrieve all Distribution Transformers (DTs) with ratings, parent feeders, and connected smart meter counts."
)
async def list_transformers(
    cache: DataCatalogCache = Depends(get_cache)
):
    await cache.warm_cache()
    return cache.get_all_transformers()


@router.get(
    "/{code}",
    response_model=DistributionTransformer,
    summary="Get single distribution transformer",
    description="Retrieve specific transformer specifications by DT code (e.g., DT-001)."
)
async def get_transformer(
    code: str,
    cache: DataCatalogCache = Depends(get_cache)
):
    await cache.warm_cache()
    dt = cache.get_transformer(code)
    if not dt:
        raise HTTPException(status_code=404, detail=f"Distribution Transformer '{code}' not found")
    return dt


@router.get(
    "/{code}/meters",
    response_model=List[MeterSummary],
    summary="Get smart meters connected to a transformer",
    description="List all smart meters physically connected down-circuit from the specified Distribution Transformer."
)
async def get_transformer_meters(
    code: str,
    cache: DataCatalogCache = Depends(get_cache)
):
    await cache.warm_cache()
    dt = cache.get_transformer(code)
    if not dt:
        raise HTTPException(status_code=404, detail=f"Distribution Transformer '{code}' not found")
    return cache.get_meters_for_transformer(code)
