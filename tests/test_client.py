import json
import pytest
from app.client import UrjaPortalClient
from app.cache import haversine_distance_km


def test_hmac_signature_generation():
    client = UrjaPortalClient()
    secret = "test_signing_secret_123"
    method = "GET"
    path = "/portal/export"
    query = "page=1"
    timestamp = "1700000000"

    sig = client.create_hmac_signature(method, path, query, timestamp, secret)
    assert isinstance(sig, str)
    assert len(sig) == 64  # SHA-256 hex length
    # Verify deterministic output
    sig2 = client.create_hmac_signature(method, path, query, timestamp, secret)
    assert sig == sig2


def test_normalize_legacy_nameplate():
    client = UrjaPortalClient()
    raw = {
        "data": [
            {"parameterName": "Meter ID", "parameterValue": "J100000"},
            {"parameterName": "Serial No", "parameterValue": "SE33962"},
            {"parameterName": "Make", "parameterValue": "HPL"},
            {"parameterName": "Phase Type", "parameterValue": "single"}
        ]
    }
    normalized = client.normalize_nameplate(raw)
    assert normalized["Meter ID"] == "J100000"
    assert normalized["Serial No"] == "SE33962"
    assert normalized["Make"] == "HPL"
    assert normalized["Phase Type"] == "single"


def test_normalize_v2_nameplate():
    client = UrjaPortalClient()
    raw = {
        "classData": json.dumps({
            "installed_meter": {
                "Meter ID": "J100004",
                "Serial No": "SE65293",
                "Make": "Genus",
                "Phase Type": "single",
                "Installation Status": "Faulty"
            }
        })
    }
    normalized = client.normalize_nameplate(raw)
    assert normalized["Meter ID"] == "J100004"
    assert normalized["Serial No"] == "SE65293"
    assert normalized["Make"] == "Genus"
    assert normalized["Installation Status"] == "Faulty"


def test_haversine_distance():
    # Distance between Jaipur coordinates (approx 10km apart)
    dist = haversine_distance_km(26.9124, 75.7873, 26.9200, 75.8800)
    assert 8.0 < dist < 12.0
    # Zero distance to self
    dist_zero = haversine_distance_km(26.9124, 75.7873, 26.9124, 75.7873)
    assert dist_zero == 0.0
