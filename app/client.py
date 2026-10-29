import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger("urja_client")


class UpstreamPortalError(Exception):
    """Raised when upstream portal fails or returns an unexpected error."""
    def __init__(self, message: str, status_code: int = 502, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class MeterNotFoundError(Exception):
    """Raised when a meter is not found upstream."""
    pass


class UrjaPortalClient:
    """
    Robust HTTP client for reverse-engineered Urja Meter Ops legacy portal.
    Features:
      - SvelteKit CSRF compliant authentication.
      - Automatic session token capture & refresh upon 401s.
      - HMAC-SHA256 request signing for bulk data exports.
      - Dual-schema normalization (legacy vs v2 hardware generations).
    """

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.portal_base_url.rstrip("/")
        self.session_token: Optional[str] = None
        self.token_expiry: float = 0.0
        self.last_login_time: float = 0.0
        self.signing_secret: Optional[str] = None
        self._auth_lock = asyncio.Lock()
        self._http_client = httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
        )

    async def close(self):
        """Close the underlying HTTP client session."""
        await self._http_client.aclose()

    async def _login(self) -> str:
        """
        Authenticate against Urja Meter Ops portal.
        Note: SvelteKit enforces CSRF check requiring Origin and Referer.
        """
        login_url = f"{self.base_url}/login"
        headers = {
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/login",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        if not self.settings.portal_username or not self.settings.portal_password:
            raise UpstreamPortalError(
                "Upstream credentials not configured. Please set PORTAL_USERNAME and PORTAL_PASSWORD in .env or environment.",
                status_code=500
            )

        data = {
            "email": self.settings.portal_username,
            "password": self.settings.portal_password
        }

        try:
            logger.info("Authenticating with upstream Urja portal at %s", login_url)
            resp = await self._http_client.post(login_url, data=data, headers=headers)
        except Exception as exc:
            raise UpstreamPortalError(f"Failed to connect to upstream portal login: {exc}")

        if resp.status_code not in (200, 302, 303):
            raise UpstreamPortalError(
                f"Authentication failed with status {resp.status_code}: {resp.text}",
                status_code=502
            )

        # Extract session token from Set-Cookie header
        token = None
        for cookie_header in resp.headers.get_list("set-cookie"):
            if "__Secure-better-auth.session_token=" in cookie_header:
                parts = cookie_header.split(";")
                for p in parts:
                    p = p.strip()
                    if p.startswith("__Secure-better-auth.session_token="):
                        token = p.split("=", 1)[1]
                        break

        if not token:
            # Check if cookie was automatically saved in client cookies
            token = self._http_client.cookies.get("__Secure-better-auth.session_token")

        if not token:
            raise UpstreamPortalError("Login response did not contain session token cookie", status_code=502)

        self.session_token = token
        self.last_login_time = time.time()
        # Token valid for 3600 seconds; set refresh buffer 5 minutes before expiration
        self.token_expiry = time.time() + 3300
        logger.info("Successfully authenticated with upstream Urja portal")
        return token

    async def ensure_authenticated(self, force: bool = False) -> str:
        """Ensure a valid session token is available, re-authenticating if needed."""
        if not force and self.session_token and time.time() < self.token_expiry:
            return self.session_token

        async with self._auth_lock:
            # Double check inside lock
            if not force and self.session_token and time.time() < self.token_expiry:
                return self.session_token
            return await self._login()

    async def refresh_authentication(self, failed_token: Optional[str] = None) -> str:
        """
        Safely re-authenticate when receiving a 401 Unauthorized.
        Synchronized with an async lock:
        - If another concurrent request already refreshed the token (self.session_token != failed_token),
          reuse the new token without sending another redundant login request to the portal.
        - If failed_token matches self.session_token, the active session is genuinely invalid;
          we immediately re-authenticate without suppressing it via a generic cooldown.
        """
        async with self._auth_lock:
            # Check if another concurrent coroutine already refreshed the token
            if failed_token and self.session_token and self.session_token != failed_token and time.time() < self.token_expiry:
                logger.info("Session token already refreshed by another concurrent request; reusing new token")
                return self.session_token

            # Cooldown guard: ONLY apply if failed_token is None (proactive refresh)
            # If a confirmed 401 occurred against the active token, we must NOT suppress login!
            if failed_token is None and self.session_token and (time.time() - self.last_login_time) < 2.0 and time.time() < self.token_expiry:
                logger.info("Recent session refresh occurred < 2s ago; reusing active token")
                return self.session_token

            return await self._login()

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        retry_on_401: bool = True
    ) -> httpx.Response:
        """
        Execute an authenticated request against the upstream portal.
        Handles transparent, bounded re-authentication on 401 Unauthorized.
        """
        token = await self.ensure_authenticated()
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        
        req_headers = {
            "Cookie": f"__Secure-better-auth.session_token={token}",
            "Referer": f"{self.base_url}/meters",
            "Accept": "application/json, text/plain, */*"
        }
        if headers:
            req_headers.update(headers)

        try:
            resp = await self._http_client.request(
                method=method,
                url=url,
                params=params,
                headers=req_headers
            )
        except Exception as exc:
            raise UpstreamPortalError(f"Network error contacting upstream {path}: {exc}")

        # If session expired or unauthorized upstream, refresh once safely and retry exactly once
        if resp.status_code == 401 and retry_on_401:
            logger.warning("Upstream returned 401 on %s. Attempting bounded session re-authentication...", path)
            await self.refresh_authentication(failed_token=token)
            return await self.request(method, path, params, headers, retry_on_401=False)

        return resp

    # --------------------------------------------------------------------------
    # Core Upstream Operations
    # --------------------------------------------------------------------------

    async def get_signing_secret(self) -> str:
        """Fetch the cryptographic HMAC signing secret for bulk operations."""
        if self.signing_secret:
            return self.signing_secret

        resp = await self.request("GET", "/portal/keys")
        if resp.status_code != 200:
            raise UpstreamPortalError(f"Failed to fetch keys: {resp.text}", status_code=resp.status_code)

        data = resp.json().get("data", {})
        secret = data.get("signingSecret")
        if not secret:
            raise UpstreamPortalError("Upstream keys endpoint missing signingSecret", status_code=502)

        self.signing_secret = secret
        return secret

    def create_hmac_signature(self, method: str, path: str, query: str, timestamp: str, secret: str) -> str:
        """
        Generate HMAC-SHA256 signature for bulk export.
        Payload format: "{method}\n{path}\n{query}\n{timestamp}"
        """
        message = f"{method}\n{path}\n{query}\n{timestamp}".encode("utf-8")
        sig = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return sig

    async def export_all_meters_raw(self) -> List[Dict[str, Any]]:
        """
        Bulk fetch all meters using the HMAC-signed export endpoint.
        Returns the full dataset of 403 meters with parsed hierarchies & geos.
        """
        secret = await self.get_signing_secret()
        method = "GET"
        path = "/portal/export"
        query = "page=1"
        ts = str(int(time.time()))
        sig = self.create_hmac_signature(method, path, query, ts, secret)

        resp = await self.request(
            method=method,
            path=f"{path}?{query}",
            headers={
                "x-timestamp": ts,
                "x-signature": sig
            }
        )
        if resp.status_code != 200:
            raise UpstreamPortalError(f"Bulk export failed ({resp.status_code}): {resp.text}", status_code=resp.status_code)

        return resp.json().get("data", [])

    async def search_meters(self, q: str = "", page: int = 1) -> Dict[str, Any]:
        """Call the upstream paginated meter search endpoint."""
        resp = await self.request("GET", "/portal/meters/search", params={"q": q, "page": page})
        if resp.status_code != 200:
            raise UpstreamPortalError(f"Search meters failed ({resp.status_code}): {resp.text}", status_code=resp.status_code)
        return resp.json()

    async def fetch_all_meters_paginated(self) -> List[Dict[str, Any]]:
        """
        Standard authenticated, read-only paginated meter search workflow.
        Iterates through all pages of /portal/meters/search?q=&page={p}
        to assemble the complete meter catalog without depending on HMAC bulk export.

        Strict validation guarantees:
        1. Validates upstream response schema and metadata.
        2. Raises UpstreamPortalError if any required page fails, drops, or is empty.
        3. Never returns a partial dataset as a successful result.
        4. Deduplicates by unique meterId and asserts len(unique_meters) == total.
        """
        first_page = await self.search_meters(q="", page=1)
        if not isinstance(first_page, dict):
            raise UpstreamPortalError("Malformed response structure from meter search: expected JSON object.")

        if "total" not in first_page or not isinstance(first_page.get("data"), list):
            raise UpstreamPortalError("Malformed pagination metadata from meter search: missing 'total' or 'data'.")

        try:
            total = int(first_page["total"])
        except (ValueError, TypeError):
            raise UpstreamPortalError(f"Invalid 'total' count in meter search metadata: {first_page.get('total')}")

        if total < 0:
            raise UpstreamPortalError(f"Negative 'total' count in meter search metadata: {total}")

        # If portal genuinely reports 0 meters, return empty list cleanly
        if total == 0:
            if first_page.get("data"):
                raise UpstreamPortalError("Reported total is 0 but page 1 contained data records.")
            return []

        meters_data = list(first_page["data"])
        if total > 0 and not meters_data:
            raise UpstreamPortalError(
                f"Pagination failure: page 1 returned 0 records when reported total was {total}."
            )

        total_pages = max(1, (total + 19) // 20)

        for p in range(2, total_pages + 1):
            page_data = await self.search_meters(q="", page=p)
            if not isinstance(page_data, dict) or not isinstance(page_data.get("data"), list):
                raise UpstreamPortalError(f"Malformed response on meter search page {p} of {total_pages}.")

            items = page_data["data"]
            if not items:
                raise UpstreamPortalError(
                    f"Pagination failure: page {p} of {total_pages} returned 0 records before reported total ({total}) was reached."
                )

            meters_data.extend(items)

        # Deduplicate and validate completeness
        seen_ids = set()
        unique_meters: List[Dict[str, Any]] = []
        for m in meters_data:
            if not isinstance(m, dict):
                raise UpstreamPortalError(f"Malformed meter record encountered during pagination: expected dict, got {type(m)}.")
            m_id = m.get("meterId")
            if not m_id:
                raise UpstreamPortalError("Malformed meter record missing required 'meterId'.")
            if m_id not in seen_ids:
                seen_ids.add(m_id)
                unique_meters.append(m)

        if len(meters_data) != len(unique_meters):
            raise UpstreamPortalError(
                f"Duplicate meter records detected during pagination: {len(meters_data)} records collected but only {len(unique_meters)} unique IDs."
            )

        if len(unique_meters) != total:
            raise UpstreamPortalError(
                f"Incomplete dataset retrieved: expected {total} unique meters, retrieved {len(unique_meters)}."
            )

        return unique_meters

    async def get_meter_geo(self, meter_id: str) -> Dict[str, float]:
        """Fetch latitude and longitude coordinates for a meter."""
        resp = await self.request("GET", f"/portal/meters/{meter_id}/geo")
        if resp.status_code == 404:
            raise MeterNotFoundError(f"Meter {meter_id} not found")
        if resp.status_code != 200:
            raise UpstreamPortalError(f"Fetch geo failed ({resp.status_code}): {resp.text}", status_code=resp.status_code)

        geo_data = resp.json().get("data", {})
        try:
            return {
                "latitude": float(geo_data.get("latitude", 0.0)),
                "longitude": float(geo_data.get("longitude", 0.0))
            }
        except (ValueError, TypeError):
            return {"latitude": 0.0, "longitude": 0.0}

    async def get_meter_energy(self, meter_id: str) -> List[Dict[str, Any]]:
        """Fetch chronological interval energy readings for a meter."""
        resp = await self.request("GET", f"/portal/meters/{meter_id}/energy")
        if resp.status_code == 404:
            raise MeterNotFoundError(f"Meter {meter_id} not found")
        if resp.status_code != 200:
            raise UpstreamPortalError(f"Fetch energy failed ({resp.status_code}): {resp.text}", status_code=resp.status_code)

        return resp.json().get("data", [])

    async def get_transformers_page(self, page: int = 1) -> Dict[str, Any]:
        """Fetch a page of distribution transformers from upstream."""
        resp = await self.request("GET", "/portal/dts", params={"page": page})
        if resp.status_code != 200:
            raise UpstreamPortalError(f"Fetch DTs failed ({resp.status_code}): {resp.text}", status_code=resp.status_code)
        return resp.json()

    async def get_all_transformers(self) -> List[Dict[str, Any]]:
        """Fetch all distribution transformers across all pages."""
        first_page = await self.get_transformers_page(page=1)
        total = first_page.get("total", 0)
        dts = list(first_page.get("data", []))
        total_pages = max(1, (total + 19) // 20)

        for p in range(2, total_pages + 1):
            next_page = await self.get_transformers_page(page=p)
            dts.extend(next_page.get("data", []))

        return dts

    async def get_meter_server_detail(self, meter_id: str) -> Dict[str, Any]:
        """
        Fetch server-side SvelteKit data for a single meter.

        The portal serves SvelteKit SSR data via `GET /meters/{id}/__data.json`.
        The response is a JSON object with a `nodes` array. Each node has:
          {"type": "data", "data": { ... }}

        The meter-data node contains:
          {
            "meterId": str,
            "detail": {"data": [{parameterName, parameterValue}]}  # legacy
                     OR {"classData": "..."}                         # v2
            "hierarchy": {"Zone": "Name (CODE)", "Circle": ..., "DT": ...}
          }

        In some portal versions the node's `data` field is a packed list
        (SvelteKit serializer). The parser handles both dict and list forms.
        """
        resp = await self.request("GET", f"/meters/{meter_id}/__data.json")
        if resp.status_code == 404:
            raise MeterNotFoundError(f"Meter {meter_id} not found")
        if resp.status_code != 200:
            raise UpstreamPortalError(
                f"Fetch meter detail failed ({resp.status_code}): {resp.text}",
                status_code=resp.status_code
            )

        payload = resp.json()
        nodes = payload.get("nodes", [])
        detail_dict: Dict[str, Any] = {}
        hierarchy_dict: Dict[str, str] = {}

        for node in nodes:
            if not node or node.get("type") != "data":
                continue
            data = node.get("data", {})

            # Branch A: dict-format (most common, directly embeds meterId / detail / hierarchy)
            if isinstance(data, dict):
                if "detail" in data and not detail_dict:
                    detail_dict = data.get("detail") or {}
                if "hierarchy" in data and not hierarchy_dict:
                    raw_h = data.get("hierarchy")
                    if isinstance(raw_h, dict):
                        hierarchy_dict = raw_h

            # Branch B: SvelteKit packed-list serialization
            # The list contains a mix of string tokens and dicts; walk and extract
            # the first dict that looks like a meter data node.
            elif isinstance(data, list):
                # Collect all dicts from the list
                candidates = [item for item in data if isinstance(item, dict)]
                for cand in candidates:
                    if "detail" in cand and not detail_dict:
                        detail_dict = cand.get("detail") or {}
                    if "hierarchy" in cand and not hierarchy_dict:
                        raw_h = cand.get("hierarchy")
                        if isinstance(raw_h, dict):
                            hierarchy_dict = raw_h
                    # Both found — stop scanning
                    if detail_dict and hierarchy_dict:
                        break

        return {
            "meterId": meter_id,
            "raw_detail": detail_dict,
            "raw_hierarchy": hierarchy_dict,
        }

    # --------------------------------------------------------------------------
    # Data Normalization Utilities
    # --------------------------------------------------------------------------

    @staticmethod
    def normalize_nameplate(raw_detail: Dict[str, Any]) -> Dict[str, str]:
        """
        Normalize nameplate attributes across legacy and v2 device generations.
        - Legacy: detail has array of {'parameterName': ..., 'parameterValue': ...}
        - V2: detail has 'classData' string containing JSON {'installed_meter': {...}}
        """
        nameplate: Dict[str, str] = {}
        if not raw_detail:
            return nameplate

        if "classData" in raw_detail:
            try:
                parsed = json.loads(raw_detail["classData"])
                installed = parsed.get("installed_meter", {})
                for k, v in installed.items():
                    nameplate[str(k)] = str(v)
                return nameplate
            except Exception:
                pass

        if "data" in raw_detail and isinstance(raw_detail["data"], list):
            for item in raw_detail["data"]:
                name = item.get("parameterName")
                val = item.get("parameterValue")
                if name:
                    nameplate[str(name)] = str(val if val is not None else "")

        return nameplate

    @staticmethod
    def parse_hierarchy_from_flat_dict(
        raw_hierarchy: Dict[str, str]
    ) -> Optional[Dict[str, Optional[Dict[str, str]]]]:
        """
        Convert the flat string hierarchy dict observed from the Urja portal's
        `__data.json` endpoint into a structured dict compatible with NetworkHierarchy.

        Observed portal format (verified from `/meters/{id}/__data.json`):
          {
            "Zone": "Jaipur Zone 1 (Z-01)",
            "Circle": "Circle 1 (C-01)",
            "Division": "Division 1 (D-01)",
            "Subdivision": "Subdivision 1 (SD-01)",
            "Sub Station": "Substation 1 (SS-01)",
            "Feeder": "Feeder 1 (F-001)",
            "DT": "Malviya Nagar DT 1 (DT-001)"
          }

        Each value has the form "Display Name (CODE)". This parser extracts both
        the display name and the parenthesized code.

        Returns None if the dict is empty or missing all hierarchy keys.
        Returns a dict keyed by NetworkHierarchy field names (zone, circle, etc.)
        with {name, code} sub-dicts, or None for individual levels that are absent.
        """
        if not raw_hierarchy or not isinstance(raw_hierarchy, dict):
            return None

        # Map from portal key → NetworkHierarchy field name
        KEY_MAP: Dict[str, str] = {
            "Zone": "zone",
            "Circle": "circle",
            "Division": "division",
            "Subdivision": "subdivision",
            "Sub Division": "subdivision",  # alternate spelling seen in portal
            "Sub Station": "substation",
            "SubStation": "substation",
            "Substation": "substation",
            "Feeder": "feeder",
            "DT": "dt",
        }

        result: Dict[str, Optional[Dict[str, str]]] = {
            "zone": None, "circle": None, "division": None,
            "subdivision": None, "substation": None, "feeder": None, "dt": None
        }
        found_any = False

        for portal_key, field_name in KEY_MAP.items():
            raw_val = raw_hierarchy.get(portal_key)
            if not raw_val or not isinstance(raw_val, str):
                continue
            raw_val = raw_val.strip()

            # Extract "Display Name (CODE)" — last parenthesized token is the code
            import re as _re
            m = _re.search(r"\(([^)]+)\)\s*$", raw_val)
            if m:
                code = m.group(1).strip()
                name = raw_val[: m.start()].strip()
            else:
                # No code in parentheses — use whole string as name, code unknown
                name = raw_val
                code = ""

            if name or code:
                result[field_name] = {"name": name, "code": code}
                found_any = True

        return result if found_any else None

    @staticmethod
    def parse_dt_code_from_hierarchy(raw_hierarchy: Dict[str, str]) -> str:
        """
        Extract the Distribution Transformer code from the portal's flat hierarchy dict.

        The observed `hierarchy["DT"]` value is:
          "Malviya Nagar DT 1 (DT-001)"

        This method extracts the last parenthesized token, e.g. "DT-001".
        Returns an empty string if "DT" is absent or the pattern does not match.
        """
        if not raw_hierarchy or not isinstance(raw_hierarchy, dict):
            return ""
        dt_val = raw_hierarchy.get("DT", "")
        if not dt_val:
            return ""
        import re as _re
        m = _re.search(r"\(([^)]+)\)\s*$", dt_val.strip())
        return m.group(1).strip() if m else ""
