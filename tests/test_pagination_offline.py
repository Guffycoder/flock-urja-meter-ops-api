import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx

from app.cache import DataCatalogCache
from app.client import UrjaPortalClient, UpstreamPortalError
from app.config import Settings
from app.models import MeterDetail, GeoCoordinates


@pytest.mark.asyncio
async def test_pagination_complete_multipage_fetch():
    """
    Verify complete multi-page pagination:
    Total = 35 meters across 2 pages (limit 20 per page).
    Page 1 returns 20 meters, Page 2 returns 15 meters.
    The method must return all 35 unique records without error.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)

    page_1_data = [{"meterId": f"M{i:03d}", "serialNo": f"SN{i:03d}"} for i in range(1, 21)]
    page_2_data = [{"meterId": f"M{i:03d}", "serialNo": f"SN{i:03d}"} for i in range(21, 36)]

    async def mock_search(q: str = "", page: int = 1):
        if page == 1:
            return {"data": page_1_data, "total": 35, "page": 1, "limit": 20}
        elif page == 2:
            return {"data": page_2_data, "total": 35, "page": 2, "limit": 20}
        return {"data": [], "total": 35, "page": page, "limit": 20}

    client.search_meters = mock_search

    result = await client.fetch_all_meters_paginated()

    assert len(result) == 35
    assert result[0]["meterId"] == "M001"
    assert result[-1]["meterId"] == "M035"
    await client.close()


@pytest.mark.asyncio
async def test_pagination_empty_page_before_total_reached():
    """
    Verify that if a subsequent page returns an empty list before the reported total
    is reached (e.g. page 1 returned 20 out of 40, page 2 returns []),
    an UpstreamPortalError is raised and no partial data is returned.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)

    page_1_data = [{"meterId": f"M{i:03d}"} for i in range(1, 21)]

    async def mock_search(q: str = "", page: int = 1):
        if page == 1:
            return {"data": page_1_data, "total": 40, "page": 1, "limit": 20}
        elif page == 2:
            return {"data": [], "total": 40, "page": 2, "limit": 20}  # Unexpectedly empty!
        return {"data": [], "total": 40}

    client.search_meters = mock_search

    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()

    assert "page 2 of 2 returned 0 records before reported total" in str(exc_info.value).lower()
    await client.close()


@pytest.mark.asyncio
async def test_pagination_later_page_network_failure():
    """
    Verify that if a later page encounters an HTTP or network error,
    it raises an UpstreamPortalError rather than returning partial records.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)

    page_1_data = [{"meterId": f"M{i:03d}"} for i in range(1, 21)]

    async def mock_search(q: str = "", page: int = 1):
        if page == 1:
            return {"data": page_1_data, "total": 40, "page": 1, "limit": 20}
        raise UpstreamPortalError("Network connection reset on page 2", status_code=502)

    client.search_meters = mock_search

    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()

    assert "network connection reset" in str(exc_info.value).lower()
    await client.close()


@pytest.mark.asyncio
async def test_pagination_malformed_metadata():
    """
    Verify that malformed pagination metadata (missing total, non-dict, non-numeric total)
    immediately raises an UpstreamPortalError.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)

    # Subcase 1: Response is not a dict
    client.search_meters = AsyncMock(return_value=["malformed", "list"])
    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()
    assert "malformed response structure" in str(exc_info.value).lower()

    # Subcase 2: Missing 'total' key
    client.search_meters = AsyncMock(return_value={"data": [{"meterId": "M001"}]})
    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()
    assert "missing 'total' or 'data'" in str(exc_info.value).lower()

    # Subcase 3: Non-numeric total
    client.search_meters = AsyncMock(return_value={"total": "not_a_number", "data": []})
    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()
    assert "invalid 'total' count" in str(exc_info.value).lower()

    # Subcase 4: Negative total
    client.search_meters = AsyncMock(return_value={"total": -5, "data": []})
    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()
    assert "negative 'total' count" in str(exc_info.value).lower()

    await client.close()


@pytest.mark.asyncio
async def test_pagination_duplicate_records_and_count_mismatch():
    """
    Verify that duplicate records across pages or a final count mismatch
    against the reported total raises an UpstreamPortalError.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)

    # Subcase 1: Total reported is 20, 20 items returned, but M001 is duplicated (only 19 unique)
    duplicate_data = [{"meterId": "M001"}] + [{"meterId": f"M{i:03d}"} for i in range(1, 20)]
    client.search_meters = AsyncMock(return_value={"data": duplicate_data, "total": 20, "page": 1, "limit": 20})

    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()
    assert "duplicate meter records detected" in str(exc_info.value).lower()

    # Subcase 2: Total reported is 20, but only 18 items returned on page 1 and no more pages
    mismatch_data = [{"meterId": f"M{i:03d}"} for i in range(1, 19)]
    client.search_meters = AsyncMock(return_value={"data": mismatch_data, "total": 20, "page": 1, "limit": 20})

    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()
    assert "incomplete dataset retrieved" in str(exc_info.value).lower()

    await client.close()


@pytest.mark.asyncio
async def test_cache_preservation_after_incomplete_refresh():
    """
    Verify that an incomplete or failed paginated refresh does NOT overwrite
    an existing valid cache (stale-while-revalidate pattern).
    """
    client = MagicMock(spec=UrjaPortalClient)
    cache = DataCatalogCache(client=client)

    # Pre-populate cache with 2 valid meters
    cache._meters_by_id = {
        "M001": MeterDetail(meterId="M001", serialNo="SN001", make="HPL", phaseType="single", installStatus="Installed", dtCode="DT-001"),
        "M002": MeterDetail(meterId="M002", serialNo="SN002", make="Genus", phaseType="three", installStatus="Installed", dtCode="DT-002")
    }
    cache._last_catalog_refresh = 1000.0

    assert cache.is_warmed is True
    assert cache.total_meters == 2

    # Simulate refresh where bulk export fails and paginated search raises incomplete error
    client.export_all_meters_raw = AsyncMock(side_effect=UpstreamPortalError("Bulk export endpoint down", status_code=503))
    client.fetch_all_meters_paginated = AsyncMock(side_effect=UpstreamPortalError("Incomplete dataset: page 2 missing records"))

    # warm_cache should catch the error and retain existing cache
    await cache.warm_cache(force=True)

    # Validate cache is preserved completely
    assert cache.is_warmed is True
    assert cache.total_meters == 2
    assert cache.get_meter("M001") is not None
    assert cache.get_meter("M002") is not None
    assert cache.last_refresh_success is False


@pytest.mark.asyncio
async def test_pagination_genuinely_empty_dataset():
    """
    Verify that when the portal genuinely reports 0 meters (total = 0 and data = []),
    fetch_all_meters_paginated returns an empty list cleanly without error.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)

    client.search_meters = AsyncMock(return_value={"data": [], "total": 0, "page": 1, "limit": 20})

    result = await client.fetch_all_meters_paginated()
    assert result == []

    # If portal reports total 0 but returns data items, it must raise UpstreamPortalError
    client.search_meters = AsyncMock(return_value={"data": [{"meterId": "M001"}], "total": 0})
    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.fetch_all_meters_paginated()
    assert "reported total is 0 but page 1 contained data" in str(exc_info.value).lower()

    await client.close()
