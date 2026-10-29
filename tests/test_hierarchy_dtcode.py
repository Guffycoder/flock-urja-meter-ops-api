"""
Stage 3 — Hierarchy and DT-code fallback regression tests.

All tests are deterministic and offline: no real credentials, no network calls.
Portal observations used as ground truth are documented in PROTOCOL.md and
reproduced from the `meter_detail.html` scratch artifact collected during portal
reverse-engineering.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.client import UrjaPortalClient, UpstreamPortalError, MeterNotFoundError
from app.models import NetworkHierarchy, HierarchyEntity, GeoCoordinates
from app.config import Settings

# ---------------------------------------------------------------------------
# Verified portal data fixtures
# ---------------------------------------------------------------------------

# Observed from GET /meters/J100000/__data.json (meter_detail.html scratch file).
# The hierarchy dict is a flat map of {portal_key: "Display Name (CODE)"}.
OBSERVED_FLAT_HIERARCHY = {
    "Meter ID": "J100000",
    "Installation Status": "Decommissioned",
    "Installation Type": "Whole Current",
    "Zone": "Jaipur Zone 1 (Z-01)",
    "Circle": "Circle 1 (C-01)",
    "Division": "Division 1 (D-01)",
    "Subdivision": "Subdivision 1 (SD-01)",
    "Sub Station": "Substation 1 (SS-01)",
    "Feeder": "Feeder 1 (F-001)",
    "DT": "Malviya Nagar DT 1 (DT-001)"
}

LEGACY_DETAIL = {
    "data": [
        {"parameterName": "Meter ID", "parameterValue": "J100000"},
        {"parameterName": "Serial No", "parameterValue": "SE33962"},
        {"parameterName": "Make", "parameterValue": "HPL"},
        {"parameterName": "Phase Type", "parameterValue": "single"},
        {"parameterName": "Installation Status", "parameterValue": "Decommissioned"},
        {"parameterName": "Installation Type", "parameterValue": "Whole Current"},
    ]
}

V2_DETAIL = {
    "classData": json.dumps({
        "installed_meter": {
            "Meter ID": "J100004",
            "Serial No": "SE65293",
            "Make": "Genus",
            "Phase Type": "single",
            "Installation Status": "Faulty",
        }
    })
}


# ---------------------------------------------------------------------------
# Task 1: parse_hierarchy_from_flat_dict
# ---------------------------------------------------------------------------

def test_parse_hierarchy_from_flat_dict_complete():
    """
    Verify the complete observed flat hierarchy dict is correctly parsed into
    name+code pairs for all 7 levels.
    """
    result = UrjaPortalClient.parse_hierarchy_from_flat_dict(OBSERVED_FLAT_HIERARCHY)
    assert result is not None

    assert result["zone"] == {"name": "Jaipur Zone 1", "code": "Z-01"}
    assert result["circle"] == {"name": "Circle 1", "code": "C-01"}
    assert result["division"] == {"name": "Division 1", "code": "D-01"}
    assert result["subdivision"] == {"name": "Subdivision 1", "code": "SD-01"}
    assert result["substation"] == {"name": "Substation 1", "code": "SS-01"}
    assert result["feeder"] == {"name": "Feeder 1", "code": "F-001"}
    assert result["dt"] == {"name": "Malviya Nagar DT 1", "code": "DT-001"}


def test_parse_hierarchy_from_flat_dict_missing_levels():
    """
    Verify that a partial hierarchy dict (some levels absent) returns None for
    missing levels rather than crashing.
    """
    partial = {
        "Zone": "Jaipur Zone 1 (Z-01)",
        "DT": "Malviya Nagar DT 1 (DT-001)"
    }
    result = UrjaPortalClient.parse_hierarchy_from_flat_dict(partial)
    assert result is not None
    assert result["zone"] == {"name": "Jaipur Zone 1", "code": "Z-01"}
    assert result["dt"] == {"name": "Malviya Nagar DT 1", "code": "DT-001"}
    assert result["circle"] is None
    assert result["feeder"] is None


def test_parse_hierarchy_from_flat_dict_genuinely_absent():
    """
    Verify that an empty or None hierarchy dict returns None without crashing.
    """
    assert UrjaPortalClient.parse_hierarchy_from_flat_dict({}) is None
    assert UrjaPortalClient.parse_hierarchy_from_flat_dict(None) is None
    # Dict with only non-hierarchy keys should also return None
    assert UrjaPortalClient.parse_hierarchy_from_flat_dict({
        "Meter ID": "J100000",
        "Installation Status": "Installed"
    }) is None


def test_parse_hierarchy_no_parenthesized_code():
    """
    Verify that entries lacking a (CODE) suffix are handled gracefully:
    name is set, code is empty string.
    """
    result = UrjaPortalClient.parse_hierarchy_from_flat_dict({
        "Zone": "North Zone",
        "DT": "Local DT"
    })
    assert result is not None
    assert result["zone"] == {"name": "North Zone", "code": ""}
    assert result["dt"] == {"name": "Local DT", "code": ""}


# ---------------------------------------------------------------------------
# Task 2: parse_dt_code_from_hierarchy
# ---------------------------------------------------------------------------

def test_parse_dt_code_from_hierarchy_standard():
    """Verify DT code extraction from the observed portal format."""
    code = UrjaPortalClient.parse_dt_code_from_hierarchy(OBSERVED_FLAT_HIERARCHY)
    assert code == "DT-001"


def test_parse_dt_code_from_hierarchy_missing_dt_key():
    """Verify empty string is returned when DT key is absent."""
    code = UrjaPortalClient.parse_dt_code_from_hierarchy({"Zone": "Zone 1 (Z-01)"})
    assert code == ""


def test_parse_dt_code_from_hierarchy_empty_and_none():
    """Verify empty string is returned for empty or None inputs."""
    assert UrjaPortalClient.parse_dt_code_from_hierarchy({}) == ""
    assert UrjaPortalClient.parse_dt_code_from_hierarchy(None) == ""


def test_parse_dt_code_no_parentheses():
    """Verify empty string is returned when no parenthesized code is present."""
    code = UrjaPortalClient.parse_dt_code_from_hierarchy({"DT": "Local DT"})
    assert code == ""


# ---------------------------------------------------------------------------
# Task 3: get_meter_server_detail — SvelteKit data parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_meter_server_detail_dict_format():
    """
    Verify get_meter_server_detail correctly extracts detail and hierarchy
    when the SvelteKit node's data field is a dict (most common format).
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "token_abc"
    client.token_expiry = 9999999999.0

    # Simulates the actual observed portal response format
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "nodes": [
            None,
            {"type": "data", "data": {"user": {"name": "Ops Desk"}}, "uses": {}},
            {
                "type": "data",
                "data": {
                    "meterId": "J100000",
                    "detail": LEGACY_DETAIL,
                    "hierarchy": OBSERVED_FLAT_HIERARCHY,
                },
                "uses": {"params": ["id"]}
            }
        ]
    }
    client._http_client.request = AsyncMock(return_value=mock_response)

    result = await client.get_meter_server_detail("J100000")

    assert result["meterId"] == "J100000"
    assert result["raw_detail"] == LEGACY_DETAIL
    assert result["raw_hierarchy"]["DT"] == "Malviya Nagar DT 1 (DT-001)"
    assert result["raw_hierarchy"]["Zone"] == "Jaipur Zone 1 (Z-01)"
    await client.close()


@pytest.mark.asyncio
async def test_get_meter_server_detail_list_format():
    """
    Verify get_meter_server_detail correctly extracts detail and hierarchy
    when the SvelteKit node's data field is a packed list (alternate format).
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "token_abc"
    client.token_expiry = 9999999999.0

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "nodes": [
            None,
            {
                "type": "data",
                # Packed list format: list containing dicts among other tokens
                "data": [
                    "some_token",
                    {"meterId": "J100001", "detail": LEGACY_DETAIL, "hierarchy": OBSERVED_FLAT_HIERARCHY},
                    42,
                    "another_token"
                ],
                "uses": {}
            }
        ]
    }
    client._http_client.request = AsyncMock(return_value=mock_response)

    result = await client.get_meter_server_detail("J100001")

    assert result["raw_detail"] == LEGACY_DETAIL
    assert result["raw_hierarchy"]["DT"] == "Malviya Nagar DT 1 (DT-001)"
    await client.close()


@pytest.mark.asyncio
async def test_get_meter_server_detail_v2_meter():
    """
    Verify get_meter_server_detail correctly handles v2-schema meters
    (detail has classData instead of data array).
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "token_abc"
    client.token_expiry = 9999999999.0

    v2_hierarchy = {
        "Zone": "North Zone (NZ-01)",
        "Circle": "Circle 2 (C-02)",
        "DT": "DT Block B (DT-042)"
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "nodes": [
            None,
            {
                "type": "data",
                "data": {
                    "meterId": "J100004",
                    "detail": V2_DETAIL,
                    "hierarchy": v2_hierarchy,
                },
                "uses": {}
            }
        ]
    }
    client._http_client.request = AsyncMock(return_value=mock_response)

    result = await client.get_meter_server_detail("J100004")

    assert result["raw_detail"] == V2_DETAIL
    assert result["raw_hierarchy"]["DT"] == "DT Block B (DT-042)"

    # Also verify nameplate normalization for v2 schema
    nameplate = UrjaPortalClient.normalize_nameplate(result["raw_detail"])
    assert nameplate["Make"] == "Genus"
    assert nameplate["Installation Status"] == "Faulty"

    # And DT code extraction
    dt_code = UrjaPortalClient.parse_dt_code_from_hierarchy(result["raw_hierarchy"])
    assert dt_code == "DT-042"

    await client.close()


@pytest.mark.asyncio
async def test_get_meter_server_detail_absent_hierarchy():
    """
    Verify that when the portal returns detail but no hierarchy key in the node,
    raw_hierarchy is returned as an empty dict (not None, not an error).
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "token_abc"
    client.token_expiry = 9999999999.0

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "nodes": [
            None,
            {
                "type": "data",
                "data": {
                    "meterId": "J100099",
                    "detail": LEGACY_DETAIL,
                    # No "hierarchy" key
                },
                "uses": {}
            }
        ]
    }
    client._http_client.request = AsyncMock(return_value=mock_response)

    result = await client.get_meter_server_detail("J100099")

    assert result["raw_detail"] == LEGACY_DETAIL
    assert result["raw_hierarchy"] == {}  # Absent but not an error

    # parse_hierarchy_from_flat_dict must return None for empty dict
    assert UrjaPortalClient.parse_hierarchy_from_flat_dict(result["raw_hierarchy"]) is None

    # parse_dt_code_from_hierarchy must return "" for empty dict
    assert UrjaPortalClient.parse_dt_code_from_hierarchy(result["raw_hierarchy"]) == ""

    await client.close()


@pytest.mark.asyncio
async def test_get_meter_server_detail_404():
    """Verify MeterNotFoundError is raised on 404."""
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "token_abc"
    client.token_expiry = 9999999999.0

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    client._http_client.request = AsyncMock(return_value=mock_response)

    with pytest.raises(MeterNotFoundError):
        await client.get_meter_server_detail("NOTREAL")

    await client.close()


# ---------------------------------------------------------------------------
# Task 4: End-to-end nameplate normalization (legacy and v2)
# ---------------------------------------------------------------------------

def test_normalize_legacy_nameplate_full():
    """Verify legacy schema produces the expected normalized nameplate dict."""
    nameplate = UrjaPortalClient.normalize_nameplate(LEGACY_DETAIL)
    assert nameplate["Meter ID"] == "J100000"
    assert nameplate["Serial No"] == "SE33962"
    assert nameplate["Make"] == "HPL"
    assert nameplate["Phase Type"] == "single"
    assert nameplate["Installation Status"] == "Decommissioned"
    assert nameplate["Installation Type"] == "Whole Current"
    # Verify 'DT Code' is NOT present — it is not a legacy nameplate field
    assert "DT Code" not in nameplate


def test_normalize_v2_nameplate_full():
    """Verify v2 schema produces the expected normalized nameplate dict."""
    nameplate = UrjaPortalClient.normalize_nameplate(V2_DETAIL)
    assert nameplate["Meter ID"] == "J100004"
    assert nameplate["Make"] == "Genus"
    assert nameplate["Installation Status"] == "Faulty"
    # Verify 'DT Code' is NOT present in v2 nameplate either
    assert "DT Code" not in nameplate


def test_normalize_empty_detail():
    """Verify empty detail dict returns empty nameplate dict without error."""
    assert UrjaPortalClient.normalize_nameplate({}) == {}
    assert UrjaPortalClient.normalize_nameplate(None) == {}
