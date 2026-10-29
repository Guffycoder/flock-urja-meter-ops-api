import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.client import UrjaPortalClient, UpstreamPortalError
from app.config import Settings


@pytest.mark.asyncio
async def test_missing_credentials_fails_fast():
    # Settings with no username or password
    settings = Settings(portal_username="", portal_password="")
    client = UrjaPortalClient(settings=settings)
    
    with pytest.raises(UpstreamPortalError) as exc_info:
        await client._login()
    
    assert "credentials not configured" in str(exc_info.value).lower()
    await client.close()


@pytest.mark.asyncio
async def test_concurrent_401_single_login_call():
    """
    Verify that when multiple concurrent requests encounter HTTP 401 with the same token,
    only ONE login request is dispatched upstream. Subsequent concurrent requests reuse
    the refreshed token via the token re-check synchronization mechanism.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "stale_token_123"
    client.token_expiry = 9999999999.0

    login_call_count = 0

    async def mock_login():
        nonlocal login_call_count
        login_call_count += 1
        await asyncio.sleep(0.05)  # Simulate network latency
        client.session_token = f"fresh_token_{login_call_count}"
        client.token_expiry = 9999999999.0
        client.last_login_time = 1000.0
        return client.session_token

    client._login = mock_login

    # Simulate 5 concurrent coroutines hitting 401 with the stale token
    failed_token = "stale_token_123"
    results = await asyncio.gather(
        client.refresh_authentication(failed_token=failed_token),
        client.refresh_authentication(failed_token=failed_token),
        client.refresh_authentication(failed_token=failed_token),
        client.refresh_authentication(failed_token=failed_token),
        client.refresh_authentication(failed_token=failed_token)
    )

    # Exactly 1 login should have been executed!
    assert login_call_count == 1
    # All 5 callers should receive the exact same fresh token
    assert all(token == "fresh_token_1" for token in results)
    await client.close()


@pytest.mark.asyncio
async def test_bounded_retry_on_persistent_401():
    """
    Verify that if the upstream continues to return 401 even after re-authenticating,
    the client does not enter an infinite retry loop and retries at most once.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "token_abc"
    client.token_expiry = 9999999999.0

    client.refresh_authentication = AsyncMock(return_value="token_xyz")

    # Mock HTTP response that always returns 401
    mock_resp_401 = MagicMock(spec=httpx.Response)
    mock_resp_401.status_code = 401
    mock_resp_401.headers = {}
    mock_resp_401.text = '{"error": "unauthorized"}'

    client._http_client.request = AsyncMock(return_value=mock_resp_401)

    final_resp = await client.request("GET", "/portal/meters/search")

    # Request should be sent exactly twice: 1 original + 1 retry
    assert client._http_client.request.call_count == 2
    assert client.refresh_authentication.call_count == 1
    assert final_resp.status_code == 401
    await client.close()

@pytest.mark.asyncio
async def test_upstream_timeout_error_handling():
    """Verify that network exceptions are cleanly translated into UpstreamPortalError."""
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "token_abc"
    client.token_expiry = 9999999999.0

    client._http_client.request = AsyncMock(side_effect=httpx.ConnectTimeout("Connection timed out"))

    with pytest.raises(UpstreamPortalError) as exc_info:
        await client.request("GET", "/portal/meters/search")

    assert "network error contacting upstream" in str(exc_info.value).lower()
    await client.close()


@pytest.mark.asyncio
async def test_recently_issued_token_rejected_triggers_reauth():
    """
    Verify that if a token was acquired < 2s ago, but receives a 401 upstream,
    the cooldown does NOT suppress re-authentication because failed_token matches
    the active token.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "bad_recent_token"
    client.token_expiry = 9999999999.0
    import time
    client.last_login_time = time.time()  # Issued right now (< 2s ago)

    login_executed = False

    async def mock_login():
        nonlocal login_executed
        login_executed = True
        client.session_token = "new_good_token"
        client.token_expiry = 9999999999.0
        return client.session_token

    client._login = mock_login

    # A 401 occurred for bad_recent_token
    token = await client.refresh_authentication(failed_token="bad_recent_token")

    # It must NOT be suppressed by cooldown:
    assert login_executed is True
    assert token == "new_good_token"
    await client.close()


@pytest.mark.asyncio
async def test_proactive_refresh_cooldown_prevents_duplicate_login():
    """
    Verify that when failed_token is None (proactive refresh rather than 401 recovery),
    the 2-second cooldown correctly prevents redundant logins if called repeatedly.
    """
    settings = Settings(portal_username="test_user", portal_password="test_password")
    client = UrjaPortalClient(settings=settings)
    client.session_token = "active_valid_token"
    client.token_expiry = 9999999999.0
    import time
    client.last_login_time = time.time()  # < 2s ago

    login_call_count = 0

    async def mock_login():
        nonlocal login_call_count
        login_call_count += 1
        return "another_token"

    client._login = mock_login

    # Call refresh_authentication without failed_token (proactive)
    token = await client.refresh_authentication(failed_token=None)

    # Cooldown should engage and reuse active token without dispatching login
    assert login_call_count == 0
    assert token == "active_valid_token"
    await client.close()

