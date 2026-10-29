import time
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.cache import DataCatalogCache
from app.client import UrjaPortalClient, MeterNotFoundError, UpstreamPortalError
from app.models import (
    MeterDetail,
    GeoCoordinates,
    NetworkHierarchy,
    HierarchyEntity,
    DistributionTransformer
)
from app.routers.meters import get_cache as meters_get_cache
from app.routers.hierarchy import get_cache as hierarchy_get_cache


@pytest.fixture
def mock_cache_and_client():
    """
    Sets up a completely isolated, pre-populated offline cache and mock client
    with zero network access and zero credentials.
    """
    mock_client = MagicMock(spec=UrjaPortalClient)
    # Default behavior for single-meter direct lookup fallback
    mock_client.get_meter_server_detail = AsyncMock(
        side_effect=MeterNotFoundError("Meter not found on upstream portal")
    )
    mock_client.get_meter_geo = AsyncMock(return_value={"lat": 26.9, "lng": 75.8})
    mock_client.close = AsyncMock()

    cache = DataCatalogCache(client=mock_client)

    # Pre-populate sample meters
    m1 = MeterDetail(
        meterId="J100001",
        serialNo="SE0001",
        make="HPL",
        phaseType="single",
        installStatus="Installed",
        installType="smart",
        build="legacy",
        dtCode="DT-001",
        geo=GeoCoordinates(latitude=26.9124, longitude=75.7873),
        hierarchy=NetworkHierarchy(
            zone=HierarchyEntity(name="North Zone", code="NZ-01"),
            circle=HierarchyEntity(name="Jaipur Circle", code="JC-01"),
            division=HierarchyEntity(name="City Division", code="CD-01"),
            subdivision=HierarchyEntity(name="Civil Lines", code="CL-01"),
            substation=HierarchyEntity(name="Substation Alpha", code="SS-01"),
            feeder=HierarchyEntity(name="Feeder 1", code="F-01"),
            dt=HierarchyEntity(name="Transformer 1", code="DT-001")
        ),
        nameplate={"Meter ID": "J100001", "Serial No": "SE0001", "Make": "HPL", "Phase Type": "single"}
    )

    m2 = MeterDetail(
        meterId="J100002",
        serialNo="SE0002",
        make="Genus",
        phaseType="three",
        installStatus="Faulty",
        installType="smart",
        build="v2",
        dtCode="DT-001",
        geo=GeoCoordinates(latitude=26.9200, longitude=75.7900),
        nameplate={"Meter ID": "J100002", "Serial No": "SE0002", "Make": "Genus", "Phase Type": "three"}
    )

    m3 = MeterDetail(
        meterId="J100003",
        serialNo="SE0003",
        make="L&T",
        phaseType="single",
        installStatus="Decommissioned",
        installType="smart",
        build="legacy",
        dtCode="DT-002",
        geo=GeoCoordinates(latitude=26.9300, longitude=75.8000),
        nameplate={"Meter ID": "J100003", "Serial No": "SE0003", "Make": "L&T", "Phase Type": "single"}
    )

    cache._meters_by_id = {m1.meterId: m1, m2.meterId: m2, m3.meterId: m3}
    cache._transformers_by_code = {
        "DT-001": DistributionTransformer(
            code="DT-001", name="Transformer 1", feederCode="F-01", capacityKva=100, connectedMetersCount=2
        ),
        "DT-002": DistributionTransformer(
            code="DT-002", name="Transformer 2", feederCode="F-02", capacityKva=200, connectedMetersCount=1
        )
    }
    cache._last_catalog_refresh = time.time()
    cache.last_refresh_method = "offline_mock"
    cache.last_refresh_success = True

    # Attach to app state and override router dependencies
    app.state.client = mock_client
    app.state.cache = cache
    app.dependency_overrides[meters_get_cache] = lambda: cache
    app.dependency_overrides[hierarchy_get_cache] = lambda: cache

    client = TestClient(app, raise_server_exceptions=False)
    yield client, cache, mock_client

    # Teardown
    app.dependency_overrides.clear()


def test_offline_list_meters_default(mock_cache_and_client):
    """Verify GET /api/v1/meters returns all cached meters with pagination metadata."""
    client, _, _ = mock_cache_and_client
    resp = client.get("/api/v1/meters")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["pageSize"] == 20
    assert data["totalPages"] == 1
    assert len(data["data"]) == 3
    ids = [m["meterId"] for m in data["data"]]
    assert "J100001" in ids
    assert "J100002" in ids
    assert "J100003" in ids


def test_offline_list_meters_filtering(mock_cache_and_client):
    """Verify multi-attribute filtering on status, make, and phase."""
    client, _, _ = mock_cache_and_client

    # Filter by status = Installed
    resp = client.get("/api/v1/meters?status=Installed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["data"][0]["meterId"] == "J100001"

    # Filter by make = Genus
    resp = client.get("/api/v1/meters?make=Genus")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["data"][0]["meterId"] == "J100002"

    # Filter by phase = three
    resp = client.get("/api/v1/meters?phase=three")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["data"][0]["phaseType"] == "three"


def test_offline_list_meters_pagination_slices(mock_cache_and_client):
    """Verify pagination slices: page=2, page_size=2 returns remaining meter."""
    client, _, _ = mock_cache_and_client
    resp = client.get("/api/v1/meters?page=2&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["page"] == 2
    assert data["pageSize"] == 2
    assert data["totalPages"] == 2
    assert len(data["data"]) == 1


def test_offline_get_meter_by_id_success(mock_cache_and_client):
    """Verify GET /api/v1/meters/{id} returns comprehensive details for existing meter."""
    client, _, _ = mock_cache_and_client
    resp = client.get("/api/v1/meters/J100001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meterId"] == "J100001"
    assert data["serialNo"] == "SE0001"
    assert data["make"] == "HPL"
    assert data["phaseType"] == "single"
    assert data["installStatus"] == "Installed"
    assert data["dtCode"] == "DT-001"
    assert data["geo"]["latitude"] == 26.9124
    assert data["geo"]["longitude"] == 75.7873
    assert data["hierarchy"]["zone"]["code"] == "NZ-01"
    assert data["hierarchy"]["dt"]["code"] == "DT-001"


def test_offline_get_meter_by_id_not_found(mock_cache_and_client):
    """Verify GET /api/v1/meters/{id} returns 404 when meter is not in cache and upstream 404s."""
    client, _, mock_client = mock_cache_and_client
    mock_client.get_meter_server_detail = AsyncMock(
        side_effect=MeterNotFoundError("Meter 'UNKNOWN_METER' not found")
    )

    resp = client.get("/api/v1/meters/UNKNOWN_METER")
    assert resp.status_code == 404
    data = resp.json()
    assert data.get("error") == "not_found" or "detail" in data


def test_offline_get_meter_by_id_upstream_error(mock_cache_and_client):
    """Verify GET /api/v1/meters/{id} translates upstream 502 into standard error response."""
    client, _, mock_client = mock_cache_and_client
    mock_client.get_meter_server_detail = AsyncMock(
        side_effect=UpstreamPortalError("Upstream gateway timeout", status_code=502)
    )

    resp = client.get("/api/v1/meters/FAILING_METER")
    assert resp.status_code == 502
    data = resp.json()
    assert data.get("error") == "upstream_error" or "detail" in data


def test_offline_get_hierarchy_tree(mock_cache_and_client):
    """Verify GET /api/v1/hierarchy returns reconstructed electrical topology tree."""
    client, _, _ = mock_cache_and_client
    resp = client.get("/api/v1/hierarchy")
    assert resp.status_code == 200
    data = resp.json()
    # Check tree structure
    assert "North Zone" in data
    north_zone = data["North Zone"]
    assert "Jaipur Circle" in north_zone
    circle = north_zone["Jaipur Circle"]
    assert "City Division" in circle
    division = circle["City Division"]
    assert "Civil Lines" in division
    subdivision = division["Civil Lines"]
    assert "Substation Alpha" in subdivision
    substation = subdivision["Substation Alpha"]
    assert "Feeder 1" in substation
    feeder = substation["Feeder 1"]
    assert "Transformer 1" in feeder
    dt = feeder["Transformer 1"]
    assert dt["dtCode"] == "DT-001"
    assert dt["metersCount"] == 1
    meter_ids = [m["meterId"] for m in dt["meters"]]
    assert "J100001" in meter_ids
