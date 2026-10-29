import pytest
from fastapi.testclient import TestClient
from app.main import app

# Mark all tests in this module as live integration tests
pytestmark = pytest.mark.integration

client = TestClient(app)


@pytest.fixture(scope="session", autouse=True)
def warm_session():
    # Trigger lifespan startup by entering context
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "upstreamConnected" in data
        assert data["cachedMetersCount"] > 0


def test_list_meters_pagination():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/meters?page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 403
        assert data["page"] == 1
        assert data["pageSize"] == 10
        assert len(data["data"]) == 10
        first_meter = data["data"][0]
        assert "meterId" in first_meter
        assert "serialNo" in first_meter
        assert "make" in first_meter


def test_filter_meters_by_status():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/meters?status=Installed&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for m in data["data"]:
            assert m["installStatus"] == "Installed"


def test_filter_meters_by_make():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/meters?make=HPL&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for m in data["data"]:
            assert m["make"] == "HPL"


def test_get_meter_detail():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/meters/J100000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meterId"] == "J100000"
        assert data["serialNo"] == "SE33962"
        assert "geo" in data
        assert "latitude" in data["geo"]
        assert "hierarchy" in data
        assert data["hierarchy"]["zone"]["code"] == "Z-01"


def test_get_meter_not_found():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/meters/NON_EXISTENT_999")
        assert resp.status_code == 404
        data = resp.json()
        assert data.get("error") == "not_found" or "detail" in data


def test_meter_consumption():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/meters/J100000/consumption")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meterId"] == "J100000"
        assert "analytics" in data
        assert data["analytics"]["totalReadings"] > 0
        assert "readings" in data
        assert len(data["readings"]) > 0
        assert "kwh" in data["readings"][0]


def test_list_transformers():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/transformers")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 40
        assert data[0]["code"] == "DT-001"
        assert data[0]["connectedMetersCount"] > 0


def test_network_hierarchy():
    with TestClient(app) as tc:
        resp = tc.get("/api/v1/hierarchy")
        assert resp.status_code == 200
        tree = resp.json()
        assert isinstance(tree, dict)
        # Jaipur Zone 1 should be a top-level key
        assert any("Jaipur" in k for k in tree.keys())


def test_nearby_meters():
    with TestClient(app) as tc:
        # Search around center of Jaipur
        resp = tc.get("/api/v1/meters/nearby?latitude=26.9389&longitude=75.8309&radius_km=10.0&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        assert "distanceKm" in data[0]
        assert data[0]["distanceKm"] >= 0.0
