import pytest
from unittest.mock import AsyncMock, MagicMock

from app.cache import DataCatalogCache
from app.client import UrjaPortalClient, UpstreamPortalError
from app.config import Settings


@pytest.mark.asyncio
async def test_cache_warming_via_bulk_export():
    """Verify primary cache warming tier succeeds with bulk export."""
    client = MagicMock(spec=UrjaPortalClient)
    client.export_all_meters_raw = AsyncMock(return_value=[
        {
            "meterId": "J100001",
            "serialNo": "SE12345",
            "make": "HPL",
            "phaseType": "single",
            "installStatus": "Installed",
            "dtCode": "DT-001",
            "geo": {"lat": 26.9, "lng": 75.8},
            "hierarchy": {
                "zone": {"name": "Zone 1", "code": "Z-01"},
                "circle": {"name": "Circle 1", "code": "C-01"},
                "division": {"name": "Div 1", "code": "D-01"},
                "subdivision": {"name": "Sub 1", "code": "SD-01"},
                "substation": {"name": "SS 1", "code": "SS-01"},
                "feeder": {"name": "F 1", "code": "F-001"},
                "dt": {"name": "DT 1", "code": "DT-001"}
            }
        }
    ])
    client.get_all_transformers = AsyncMock(return_value=[
        {"code": "DT-001", "name": "DT 1", "feederCode": "F-001", "capacityKva": 100}
    ])

    cache = DataCatalogCache(client=client)
    await cache.warm_cache(force=True)

    assert cache.is_warmed is True
    assert cache.total_meters == 1
    assert cache.last_refresh_method == "bulk_export"
    assert cache.last_refresh_success is True
    meter = cache.get_meter("J100001")
    assert meter is not None
    assert meter.serialNo == "SE12345"
    assert meter.geo.latitude == 26.9


@pytest.mark.asyncio
async def test_cache_fallback_to_paginated_search():
    """
    Verify that if bulk export fails (e.g. 502 or signing key rejection),
    the cache defensively falls back to the verified paginated search workflow.
    """
    client = MagicMock(spec=UrjaPortalClient)
    # Bulk export raises an error
    client.export_all_meters_raw = AsyncMock(side_effect=UpstreamPortalError("Export endpoint rejected signature", status_code=502))
    # Paginated fallback succeeds
    client.fetch_all_meters_paginated = AsyncMock(return_value=[
        {
            "meterId": "J100002",
            "serialNo": "SE99999",
            "make": "Genus",
            "phaseType": "three",
            "installStatus": "Faulty",
            "dtCode": "DT-002"
        }
    ])
    client.get_all_transformers = AsyncMock(return_value=[
        {"code": "DT-002", "name": "DT 2", "feederCode": "F-002", "capacityKva": 200}
    ])

    cache = DataCatalogCache(client=client)
    await cache.warm_cache(force=True)

    assert cache.is_warmed is True
    assert cache.total_meters == 1
    assert cache.last_refresh_method == "paginated_fallback"
    assert cache.last_refresh_success is True
    meter = cache.get_meter("J100002")
    assert meter is not None
    assert meter.make == "Genus"
    assert meter.installStatus == "Faulty"


@pytest.mark.asyncio
async def test_cache_preservation_on_subsequent_failure():
    """
    Verify stale-while-revalidate pattern: when an already warmed cache encounters
    a failure during background re-warming, it does NOT discard existing valid data.
    """
    client = MagicMock(spec=UrjaPortalClient)
    client.export_all_meters_raw = AsyncMock(return_value=[
        {
            "meterId": "J100003",
            "serialNo": "SE55555",
            "make": "L&T",
            "phaseType": "single",
            "installStatus": "Installed",
            "dtCode": "DT-003"
        }
    ])
    client.get_all_transformers = AsyncMock(return_value=[])

    cache = DataCatalogCache(client=client)
    await cache.warm_cache(force=True)
    assert cache.total_meters == 1

    # Now simulate a subsequent refresh where upstream is completely down
    client.export_all_meters_raw = AsyncMock(side_effect=UpstreamPortalError("Portal Down", status_code=503))
    client.fetch_all_meters_paginated = AsyncMock(side_effect=UpstreamPortalError("Portal Down", status_code=503))

    # Does not raise because is_warmed is True
    await cache.warm_cache(force=True)

    # Previously cached data is preserved!
    assert cache.total_meters == 1
    assert cache.get_meter("J100003") is not None
    assert cache.last_refresh_success is False


@pytest.mark.asyncio
async def test_cold_cache_failure_raises():
    """Verify that if a cold cache fails both bulk and fallback, it raises cleanly."""
    client = MagicMock(spec=UrjaPortalClient)
    client.export_all_meters_raw = AsyncMock(side_effect=UpstreamPortalError("Export Failed"))
    client.fetch_all_meters_paginated = AsyncMock(side_effect=UpstreamPortalError("Search Failed"))

    cache = DataCatalogCache(client=client)

    with pytest.raises(UpstreamPortalError):
        await cache.warm_cache(force=True)

    assert cache.is_warmed is False
    assert cache.total_meters == 0
