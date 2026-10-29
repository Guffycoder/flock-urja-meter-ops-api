import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.cache import DataCatalogCache
from app.client import MeterNotFoundError, UpstreamPortalError
from app.models import (
    EnergyConsumptionResponse,
    GeoCoordinates,
    HierarchyEntity,
    MeterDetail,
    MeterSummary,
    NearbyMeter,
    NetworkHierarchy,
    PaginatedResponse,
)

logger = logging.getLogger("urja_routers.meters")

router = APIRouter(prefix="/api/v1/meters", tags=["Meters"])



def get_cache(request: Request) -> DataCatalogCache:
    return request.app.state.cache


@router.get(
    "",
    response_model=PaginatedResponse[MeterSummary],
    summary="List and search smart meters",
    description="Retrieve a paginated list of smart meters with optional multi-attribute filtering and text search."
)
async def list_meters(
    q: Optional[str] = Query(None, description="Free-text search across Meter ID or Serial Number"),
    status: Optional[str] = Query(None, description="Filter by status: Installed, Faulty, Decommissioned"),
    make: Optional[str] = Query(None, description="Filter by manufacturer: HPL, Genus, L&T, Allied, Secure"),
    phase: Optional[str] = Query(None, description="Filter by phase: single, three"),
    dt_code: Optional[str] = Query(None, description="Filter by Distribution Transformer code (e.g., DT-001)"),
    build: Optional[str] = Query(None, description="Filter by build generation: legacy, v2"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Number of records per page (max 100)"),
    sort_by: str = Query("meterId", pattern="^(meterId|serialNo|make|installStatus|dtCode)$", description="Field to sort by"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$", description="Sort order"),
    cache: DataCatalogCache = Depends(get_cache)
):
    await cache.warm_cache()
    filtered = cache.search_and_filter_meters(
        q=q,
        status=status,
        make=make,
        phase=phase,
        dt_code=dt_code,
        build=build,
        sort_by=sort_by,
        sort_order=sort_order
    )

    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_items = filtered[start_idx:end_idx]

    summaries = [
        MeterSummary(
            meterId=m.meterId,
            serialNo=m.serialNo,
            make=m.make,
            phaseType=m.phaseType,
            installStatus=m.installStatus,
            installType=m.installType,
            build=m.build,
            dtCode=m.dtCode
        )
        for m in page_items
    ]

    return PaginatedResponse[MeterSummary](
        total=total,
        page=page,
        pageSize=page_size,
        totalPages=total_pages,
        data=summaries
    )


@router.get(
    "/nearby",
    response_model=List[NearbyMeter],
    summary="Find smart meters near coordinates",
    description="Spatial proximity search returning smart meters within a given radius (km), sorted by closest distance."
)
async def get_nearby_meters(
    latitude: float = Query(..., ge=-90.0, le=90.0, description="Center latitude in decimal degrees", examples=[26.9389]),
    longitude: float = Query(..., ge=-180.0, le=180.0, description="Center longitude in decimal degrees", examples=[75.8309]),
    radius_km: float = Query(5.0, gt=0, le=50.0, description="Search radius in kilometers (default 5km, max 50km)"),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of meters to return"),
    cache: DataCatalogCache = Depends(get_cache)
):
    await cache.warm_cache()
    nearby = cache.find_nearby_meters(latitude, longitude, radius_km, limit)
    return nearby


@router.get(
    "/{meter_id}",
    response_model=MeterDetail,
    summary="Get single smart meter details",
    description="Fetch comprehensive meter record including normalized nameplate specifications, network hierarchy, and coordinates."
)
async def get_meter_detail(
    meter_id: str,
    cache: DataCatalogCache = Depends(get_cache)
):
    await cache.warm_cache()
    meter = cache.get_meter(meter_id)
    if not meter:
        # Fallback to direct upstream query for meters not present in cache.
        try:
            raw = await cache.client.get_meter_server_detail(meter_id)
            raw_detail = raw.get("raw_detail", {})
            raw_hierarchy = raw.get("raw_hierarchy", {})

            nameplate = cache.client.normalize_nameplate(raw_detail)

            # DT code: the only verified source in the single-meter fallback is
            # the parenthesized code at the end of hierarchy["DT"], e.g.
            # "Malviya Nagar DT 1 (DT-001)" → "DT-001".
            # nameplate does NOT contain a "DT Code" field.
            dt_code = cache.client.parse_dt_code_from_hierarchy(raw_hierarchy)

            # Convert flat portal hierarchy dict → NetworkHierarchy model
            hierarchy = None
            parsed_h = cache.client.parse_hierarchy_from_flat_dict(raw_hierarchy)
            if parsed_h:
                try:
                    hierarchy = NetworkHierarchy(
                        zone=HierarchyEntity(**parsed_h["zone"]) if parsed_h.get("zone") else None,
                        circle=HierarchyEntity(**parsed_h["circle"]) if parsed_h.get("circle") else None,
                        division=HierarchyEntity(**parsed_h["division"]) if parsed_h.get("division") else None,
                        subdivision=HierarchyEntity(**parsed_h["subdivision"]) if parsed_h.get("subdivision") else None,
                        substation=HierarchyEntity(**parsed_h["substation"]) if parsed_h.get("substation") else None,
                        feeder=HierarchyEntity(**parsed_h["feeder"]) if parsed_h.get("feeder") else None,
                        dt=HierarchyEntity(**parsed_h["dt"]) if parsed_h.get("dt") else None,
                    )
                except Exception as h_err:
                    logger.warning("Could not build NetworkHierarchy from flat dict for %s: %s", meter_id, h_err)

            # Geo is returned as a raw dict {latitude, longitude} from get_meter_geo
            geo_raw = await cache.client.get_meter_geo(meter_id)
            geo = GeoCoordinates(**geo_raw) if geo_raw else None

            meter = MeterDetail(
                meterId=meter_id,
                serialNo=nameplate.get("Serial No", ""),
                make=nameplate.get("Make", "Unknown"),
                phaseType=nameplate.get("Phase Type", "single"),
                installStatus=nameplate.get("Installation Status", "Unknown"),
                installType=nameplate.get("Installation Type"),
                dtCode=dt_code,
                geo=geo,
                hierarchy=hierarchy,
                nameplate=nameplate,
            )
        except MeterNotFoundError:
            raise HTTPException(status_code=404, detail=f"Meter '{meter_id}' not found")
        except UpstreamPortalError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))

    return meter



@router.get(
    "/{meter_id}/consumption",
    response_model=EnergyConsumptionResponse,
    summary="Get meter energy consumption and interval readings",
    description="Retrieve chronological 30-minute interval readings (kWh, kVAh, voltage) along with computed consumption summary analytics."
)
async def get_meter_consumption(
    meter_id: str,
    refresh: bool = Query(False, description="Bypass short-term cache and force fresh fetch from upstream"),
    cache: DataCatalogCache = Depends(get_cache)
):
    try:
        response = await cache.get_or_fetch_energy(meter_id, force_refresh=refresh)
        return response
    except MeterNotFoundError:
        raise HTTPException(status_code=404, detail=f"Meter '{meter_id}' not found")
    except UpstreamPortalError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
