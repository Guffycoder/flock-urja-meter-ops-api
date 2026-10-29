# Urja Meter Ops API Service

A clean, resilient, and well-documented RESTful API service built in front of the legacy **Urja Meter Ops** utility portal (`https://urja-ops.flockenergy.tech`). It exposes smart-meter metadata, network hierarchy topology, interval consumption telemetry, and spatial proximity queries for downstream engineers and product teams.

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Offline%20Tests-44%20Passing-brightgreen.svg)](#running-tests)
[![OpenAPI 3.1](https://img.shields.io/badge/OpenAPI-3.1-blue.svg)](openapi.json)

---

## Quick Navigation

* **Protocol Reverse-Engineering Report:** [`PROTOCOL.md`](PROTOCOL.md)
* **OpenAPI 3.1 Specification:** [`openapi.json`](openapi.json)
* **Engineering Reflections & Self-Review:** [`REFLECTION.md`](REFLECTION.md)

---

## Architecture & Project Structure

The service is built as a non-blocking asynchronous proxy and indexing engine using **FastAPI** and **HTTPX**:

```
Consumer / Product / Data Teams
        │  REST / JSON
        ▼
Urja API Service (FastAPI)
        │
        ├── Routers  (/api/v1/meters, /api/v1/transformers, /api/v1/hierarchy, /api/v1/health)
        │
        ├── In-Memory Catalog Cache  (multi-attribute filter, Haversine spatial query, stale cache preservation)
        │
        └── UrjaPortalClient  (CSRF-compliant auth, bounded 401 re-auth & lazy renewal, HMAC bulk export)
                │
                └── Legacy Urja Portal  (SvelteKit + Better-Auth)
```

### Directory Structure

```text
.
├── app/
│   ├── main.py              # Application entrypoint & lifespan management
│   ├── config.py            # Environment configuration (Pydantic Settings)
│   ├── client.py            # Upstream HTTP client, auth & HMAC signer
│   ├── cache.py             # In-memory index, spatial search & analytics
│   ├── models.py            # Pydantic schemas & response models
│   └── routers/
│       ├── meters.py        # /api/v1/meters (search, details, consumption, nearby)
│       ├── transformers.py  # /api/v1/transformers (DT inventory & meter mappings)
│       ├── hierarchy.py     # /api/v1/hierarchy (reconstructed grid tree)
│       └── health.py        # /api/v1/health (health & upstream status)
├── tests/
│   ├── test_client.py            # HMAC signing, normalizers & distance tests
│   ├── test_client_resilience.py # Auth lock, bounded retry, cooldown tests
│   ├── test_cache_fallback.py    # Cache warming tiers & stale-while-revalidate
│   ├── test_pagination_offline.py # Pagination completeness & error validation
│   ├── test_router_offline.py    # Offline router tests for meters & hierarchy
│   ├── test_hierarchy_dtcode.py  # Hierarchy parsing & DT-code extraction tests
│   └── test_api.py               # Live integration tests (require portal credentials)
├── PROTOCOL.md              # Technical reverse-engineering report
├── REFLECTION.md            # Self-review, mistakes & improvements
├── openapi.json             # Generated OpenAPI 3.1 specification
├── requirements.txt         # Project dependencies
├── pytest.ini               # pytest configuration (asyncio mode, integration marker)
└── README.md                # Documentation front door
```

---

## Reviewer Quick Start (Evaluation in 60 Seconds)

To evaluate this assignment without needing upstream portal credentials:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the 44-test hermetic test suite (runs 100% offline in ~15s)
python -m pytest -v -m "not integration"

# 3. View the generated OpenAPI 3.1 schema
cat openapi.json

# 4. Core evaluation deliverables:
# - PROTOCOL.md: Upstream portal reverse-engineering findings & data formats
# - REFLECTION.md: Self-review answering all 5 required reflection questions
```

---

## Installation & Getting Started

### 1. Clone the Repository
```bash
git clone <repository-url>
cd <repository-directory>
```

### 2. Prerequisites
* Python 3.10+ (tested on Python 3.11)
* `pip` package manager

### 3. Setup Virtual Environment
```bash
python -m venv venv

# On Linux / macOS:
source venv/bin/activate
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Configuration

Create a `.env` file from the example template:
```bash
cp .env.example .env
```

Edit `.env` and fill in valid portal credentials:
```env
PORTAL_BASE_URL=https://urja-ops.flockenergy.tech
PORTAL_USERNAME=your_username_here
PORTAL_PASSWORD=your_password_here
CACHE_TTL_SECONDS=300
```

> **Note:** `.env` is listed in `.gitignore` and must never be committed to version control.

### 6. Run the Service
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Once started, explore the service in your browser:
* **Interactive Web Dashboard:** http://localhost:8000/
* **Interactive Swagger UI:** http://localhost:8000/docs
* **ReDoc Documentation:** http://localhost:8000/redoc
* **Raw OpenAPI 3.1 Spec:** http://localhost:8000/openapi.json

On startup, the service authenticates with the upstream portal and warms the in-memory cache. If cache warming fails (e.g. no valid `.env` credentials configured), the service starts in offline/fallback mode and retries lazily on incoming requests.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/meters` | List and filter smart meters (pagination, multi-attribute filter, sort) |
| `GET` | `/api/v1/meters/nearby` | Spatial proximity search (Haversine distance) |
| `GET` | `/api/v1/meters/{meter_id}` | Get single meter details (nameplate, hierarchy, coordinates) |
| `GET` | `/api/v1/meters/{meter_id}/consumption` | Interval energy readings and analytics |
| `GET` | `/api/v1/transformers` | List all Distribution Transformers |
| `GET` | `/api/v1/transformers/{code}` | Get single DT by code |
| `GET` | `/api/v1/transformers/{code}/meters` | Get meters connected under a DT |
| `GET` | `/api/v1/hierarchy` | Full reconstructed electrical grid tree |
| `GET` | `/api/v1/health` | Service health and cache status |

Full schema: see [`openapi.json`](openapi.json) or `/docs` when the service is running.

---

## Sample API Requests

### List and Filter Meters
```bash
curl "http://localhost:8000/api/v1/meters?status=Installed&make=HPL&page=1&page_size=2"
```

```json
{
  "total": 52,
  "page": 1,
  "pageSize": 2,
  "totalPages": 26,
  "data": [
    {
      "meterId": "J100007",
      "serialNo": "SE20963",
      "make": "HPL",
      "phaseType": "single",
      "installStatus": "Installed",
      "installType": "Whole Current",
      "build": "legacy",
      "dtCode": "DT-008"
    }
  ]
}
```

### Get Single Meter Details
```bash
curl "http://localhost:8000/api/v1/meters/J100000"
```

```json
{
  "meterId": "J100000",
  "serialNo": "SE33962",
  "make": "HPL",
  "phaseType": "single",
  "installStatus": "Decommissioned",
  "installType": "Whole Current",
  "build": "legacy",
  "dtCode": "DT-001",
  "geo": { "latitude": 26.938961, "longitude": 75.830957 },
  "hierarchy": {
    "zone":        { "name": "Jaipur Zone 1",      "code": "Z-01"   },
    "circle":      { "name": "Circle 1",           "code": "C-01"   },
    "division":    { "name": "Division 1",         "code": "D-01"   },
    "subdivision": { "name": "Subdivision 1",      "code": "SD-01"  },
    "substation":  { "name": "Substation 1",       "code": "SS-01"  },
    "feeder":      { "name": "Feeder 1",           "code": "F-001"  },
    "dt":          { "name": "Malviya Nagar DT 1", "code": "DT-001" }
  },
  "nameplate": {
    "Meter ID": "J100000",
    "Serial No": "SE33962",
    "Make": "HPL",
    "Phase Type": "single",
    "Installation Status": "Decommissioned",
    "Installation Type": "Whole Current"
  }
}
```

### Interval Energy Telemetry
```bash
curl "http://localhost:8000/api/v1/meters/J100000/consumption"
```

```json
{
  "meterId": "J100000",
  "analytics": {
    "totalReadings": 337,
    "windowStart": "23/06/2026 23:30",
    "windowEnd": "30/06/2026 23:30",
    "latestKwh": 48512.10,
    "netKwhConsumption": 73.36,
    "averageVoltageVolts": 229.4,
    "minVoltageVolts": 215.0,
    "maxVoltageVolts": 248.0
  },
  "readings": [
    { "timestamp": "23/06/2026 23:30", "kwh": 48438.74, "kvah": 52313.84, "voltR": 226.0 }
  ]
}
```

### Spatial Proximity Search
```bash
curl "http://localhost:8000/api/v1/meters/nearby?latitude=26.9389&longitude=75.8309&radius_km=2.0"
```

```json
[
  {
    "meter": {
      "meterId": "J100000",
      "serialNo": "SE33962",
      "make": "HPL",
      "phaseType": "single",
      "installStatus": "Decommissioned",
      "installType": "Whole Current",
      "build": "legacy",
      "dtCode": "DT-001"
    },
    "distanceKm": 0.0,
    "geo": {
      "latitude": 26.938961,
      "longitude": 75.830957
    }
  }
]
```

> *(Note: Sample payloads shown above are illustrative examples conforming to the service's typed Pydantic models and observed portal structures)*

---

## Cache Behavior & Limitations

| Behavior | Detail |
|----------|--------|
| Primary warm path | HMAC-signed bulk export (`/portal/export`) — all 403 meters in one request (internal portal interface) |
| Fallback warm path | Paginated `/portal/meters/search` — used defensively if bulk export fails or is rejected |
| Stale cache preservation on failure | Cache refresh runs synchronously on-demand upon TTL expiration on request arrival. If the upstream refresh fails, previously cached data is preserved and returned; the failure is logged without failing consumer requests |
| Spatial query execution | In-memory linear scan over cached meter coordinates calculating great-circle Haversine distance; avoids spatial tree indexing overhead for small catalogs (~403 records) |
| Meter catalog TTL | 300 seconds (5 minutes). Configurable via `CACHE_TTL_SECONDS` |
| Energy reading TTL | 60 seconds. Configurable via `ENERGY_CACHE_TTL_SECONDS` |
| Pagination validation | If fewer records than the reported `total` are retrieved, the refresh is aborted and the existing cache is preserved |
| Cold start failure | If no valid credentials are configured and cache is empty, endpoint requests will return a `502` error |
| Hierarchy for cached meters | Hierarchy is pre-populated during bulk export warm. For cache-miss fallbacks, hierarchy is parsed from `GET /meters/{id}/__data.json` |

---

## Design Decisions & Trade-offs

### 1. In-Memory Catalog vs Direct Proxy
**Decision:** Index all 403 meters on startup with a 5-minute TTL.  
**Trade-off:** Removes per-request latency against the upstream portal and allows complex multi-attribute filtering and spatial queries the portal cannot natively perform. Data can be up to 5 minutes stale — acceptable for physical device installations where attributes rarely change.

### 2. In-Memory Haversine Spatial Query vs Spatial Tree Index
**Decision:** Calculate great-circle Haversine distance dynamically across cached meter coordinates rather than building a complex spatial index (such as an R-tree or k-d tree).  
**Trade-off:** For a dataset of ~403 meters, a direct in-memory Haversine scan executes in <1ms. This eliminates extra third-party dependencies and spatial indexing complexity while providing accurate proximity queries.

### 3. Dual-Schema Normalization
**Decision:** Handle two device generations transparently in a single adapter layer.  
**Trade-off:** Consumers always receive a consistent typed schema regardless of device firmware (`legacy` vs `v2`). The complexity of the upstream formats is fully encapsulated.

### 4. Resilient Concurrent Session Refresh
**Decision:** Use an `asyncio.Lock` to serialize token refresh when multiple concurrent requests hit a 401. Only one coroutine sends a new login request; others reuse the refreshed token.  
**Trade-off:** Eliminates thundering-herd login storms against the upstream portal.

### 5. Strict Pagination Completeness Validation
**Decision:** If any page returns fewer records than expected, or the final count does not match the portal's reported `total`, the refresh raises an error and the existing cache is preserved.  
**Trade-off:** Prevents partial data from silently replacing a complete cache.

---

## What Was Intentionally Left Out

* **Write / Mutation Operations:** The upstream portal is treated as strictly read-only.
* **External Database (PostgreSQL, Redis):** Kept zero external runtime dependencies — the service can be run with `pip install -r requirements.txt` and `uvicorn` only.
* **Multi-Phase Voltage:** Only R-phase voltage (`voltR`) is exposed by the portal. B-phase and Y-phase are not fabricated.
* **Authentication Proxy:** The service does not expose upstream portal credentials or sessions to API consumers.

---

## Running Tests

### Offline Tests (no portal credentials required)
```bash
python -m pytest -v -m "not integration"
```

These 44 tests use mocked clients, `AsyncMock`, and `TestClient` with dependency overrides. No network requests are made.

```
tests/test_cache_fallback.py          (4 tests)
tests/test_client.py                  (4 tests)
tests/test_client_resilience.py       (6 tests)
tests/test_hierarchy_dtcode.py        (16 tests)
tests/test_pagination_offline.py      (7 tests)
tests/test_router_offline.py          (7 tests)
44 passed in ~13s
```

### Live Integration Tests (requires valid `.env` credentials)
```bash
python -m pytest -v
```

These 10 integration tests (`tests/test_api.py`) make real requests to the live portal.

---

## Known Limitations & Assumptions

1. **Page size assumed to be 20:** The `/portal/meters/search` endpoint was consistently observed returning 20 records per page. This is treated as a stable behavior, not a guaranteed contract.
2. **Hierarchy format verified for one meter:** The flat `{Zone: "Name (CODE)"}` format was observed for `J100000`. Other meters are assumed to follow the same pattern; the parser handles missing levels gracefully.
3. **DT-code source in cache-miss path:** When a meter is not in the warm cache, the DT code is extracted from `hierarchy["DT"]` in the `__data.json` response. This is the only verified source in the single-meter fallback path.
4. **Portal API stability:** Internal portal endpoints (`/portal/*`, `/__data.json`) are not public APIs. They may change without notice.
5. **Session token lifecycle:** Observed as 3600 seconds from the `Max-Age` cookie attribute. The client checks token age lazily on incoming requests and renews it if older than 3300 seconds (5-minute buffer). In addition, any upstream 401 response triggers reactive, concurrency-locked re-authentication with a bounded single retry.

