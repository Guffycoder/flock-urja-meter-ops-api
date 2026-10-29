# PROTOCOL.md — Reverse-Engineering the Urja Meter Ops Portal

This document records the observed behavior, discovered endpoints, data formats, and implementation quirks of the **Urja Meter Ops** internal utility portal (`https://urja-ops.flockenergy.tech`).

Each section distinguishes between:
- **Verified (observed):** Directly confirmed by inspecting HTTP responses, HTML payloads, or JavaScript bundles.
- **Inferred:** Reasonably deduced from observed behavior but not directly confirmed by source code.
- **Assumed:** A working assumption that has not been independently verified.

> **Stability Note:** The endpoints documented here are internal portal mechanisms, not public APIs. They may change without notice.

---

## 1. Technology Stack Detection

**Verified** from HTTP response headers, HTML structure, and JS bundle paths:

| Component | Value | Evidence |
|-----------|-------|----------|
| Application framework | SvelteKit | `X-Sveltekit-Page: true` header; `__sveltekit_*` init runtime in HTML; `/_app/immutable/` bundle paths |
| Styling | TailwindCSS | Utility classes in SSR-rendered DOM nodes |
| Authentication subsystem | Better Auth | Cookie name: `__Secure-better-auth.session_token` |
| Client bundles | Modular immutable chunks | `/_app/immutable/nodes/` and `/_app/immutable/chunks/` paths |

---

## 2. Authentication & Session Management

### 2.1 Login Endpoint

**Verified:**
- **Endpoint:** `POST /login`
- **Content-Type:** `application/x-www-form-urlencoded`
- **Form fields:** `email=<value>&password=<value>`
- **Successful response status:** `200 OK` (SvelteKit form action; body contains `{"type":"redirect","status":303,"location":"/meters"}`)
- **Session cookie issued:**
  ```
  Set-Cookie: __Secure-better-auth.session_token=<token>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax
  ```
- **Session lifetime:** 3600 seconds (1 hour), observed from `Max-Age` attribute.
- **Implementation:** The client checks token age lazily on incoming requests and renews it if older than 3300s (5 minutes before expiration). If an active token is rejected with a 401, the client re-authenticates reactively under an async lock with a bounded retry.

### 2.2 SvelteKit CSRF Origin Check

**Verified:**
- Direct `POST /login` without `Origin` and `Referer` headers returns:
  ```
  HTTP/1.1 403 Forbidden
  Cross-site POST form submissions are forbidden
  ```
- **Resolution:** SvelteKit's built-in form action security validates `Origin` and `Referer` headers against the host. The client sends:
  ```http
  Origin: https://urja-ops.flockenergy.tech
  Referer: https://urja-ops.flockenergy.tech/login
  ```

### 2.3 Unauthenticated Request Response

**Verified:**
- Calling any authenticated endpoint without a cookie or with an expired token returns:
  ```
  HTTP/1.1 401 Unauthorized
  {"error": "unauthorized", "message": "A valid session is required."}
  ```

### 2.4 Concurrent 401 Handling (Implementation)

When multiple concurrent requests encounter a 401 with the same stale token, the client uses an `asyncio.Lock` to ensure only one coroutine re-authenticates. Others wait and reuse the refreshed token. A cooldown guard (2 seconds) prevents duplicate re-authentications during rapid sequential checks, but is **bypassed** whenever a confirmed 401 occurs against the active token.

---

## 3. Discovered Endpoints

**Verified** from HTTP probing and JavaScript bundle inspection:

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/login` | `POST` | No (CSRF headers required) | Establishes session cookie |
| `/portal/meters/search?q={q}&page={p}` | `GET` | Session cookie | Paginated meter search |
| `/meters/{meter_id}/__data.json` | `GET` | Session cookie | SvelteKit SSR data: nameplate + flat hierarchy |
| `/portal/meters/{meter_id}/geo` | `GET` | Session cookie | Meter geographic coordinates |
| `/portal/meters/{meter_id}/energy` | `GET` | Session cookie | 337 interval energy readings |
| `/portal/dts?page={p}` | `GET` | Session cookie | Paginated Distribution Transformers |
| `/portal/keys` | `GET` | Session cookie | Retrieves HMAC signing secret |
| `/portal/export?page=1` | `GET` | Session cookie + HMAC signature | Bulk export of all meters |

---

## 4. Meter Search Pagination

**Verified (observed):**
- **Endpoint:** `GET /portal/meters/search?q=&page={p}`
- **Records per page:** 20 (consistently observed across multiple pages)
- **Total meters:** 403 (observed from `total` field in response)
- **Response shape:**
  ```json
  {
    "data": [ { "meterId": "...", "serialNo": "...", ... } ],
    "total": 403,
    "page": 1,
    "limit": 20
  }
  ```

**Assumed:** Page size of 20 is stable behavior, not a guaranteed API contract.

**Implementation note:** The client validates that every page returns records, that no page is unexpectedly empty before the `total` is reached, and that the final deduplicated count matches `total`. If any check fails, the refresh is aborted and the existing cache is preserved.

---

## 5. Meter Detail Endpoint (`__data.json`)

**Verified (observed from `J100000`):**
- **Endpoint:** `GET /meters/{meter_id}/__data.json`
- **Response:** SvelteKit server-side rendering data payload
- **Structure:**
  ```json
  {
    "nodes": [
      null,
      { "type": "data", "data": { "user": { "name": "...", "email": "..." } }, "uses": {} },
      {
        "type": "data",
        "data": {
          "meterId": "J100000",
          "detail": { ... },
          "hierarchy": { ... }
        },
        "uses": { "params": ["id"] }
      }
    ]
  }
  ```
- The relevant node has `"type": "data"` and its `data` dict contains `meterId`, `detail`, and `hierarchy`.
- **Alternate format (inferred):** In some SvelteKit versions the `data` field may be a packed list rather than a dict. The parser handles both.

### 5.1 Legacy Device Nameplate (`build: legacy`, ~238 meters)

**Verified:**
```json
"detail": {
  "data": [
    { "parameterName": "Meter ID",             "parameterValue": "J100000" },
    { "parameterName": "Serial No",            "parameterValue": "SE33962" },
    { "parameterName": "Make",                 "parameterValue": "HPL" },
    { "parameterName": "Phase Type",           "parameterValue": "single" },
    { "parameterName": "Installation Status",  "parameterValue": "Decommissioned" },
    { "parameterName": "Installation Type",    "parameterValue": "Whole Current" }
  ]
}
```

### 5.2 V2 Device Nameplate (`build: v2`, ~165 meters)

**Verified:**
```json
"detail": {
  "classData": "{\"installed_meter\": {\"Meter ID\": \"J100004\", \"Serial No\": \"SE65293\", \"Make\": \"Genus\", \"Phase Type\": \"single\", \"Installation Status\": \"Faulty\"}}"
}
```

### 5.3 Flat Hierarchy Dictionary

**Verified (observed from `J100000`):**
```json
"hierarchy": {
  "Meter ID": "J100000",
  "Installation Status": "Decommissioned",
  "Installation Type": "Whole Current",
  "Zone":         "Jaipur Zone 1 (Z-01)",
  "Circle":       "Circle 1 (C-01)",
  "Division":     "Division 1 (D-01)",
  "Subdivision":  "Subdivision 1 (SD-01)",
  "Sub Station":  "Substation 1 (SS-01)",
  "Feeder":       "Feeder 1 (F-001)",
  "DT":           "Malviya Nagar DT 1 (DT-001)"
}
```

Each hierarchy entry follows the pattern `"Display Name (CODE)"`. The code is extracted from the last parenthesized token.

**Assumed:** All meters follow this format. Missing levels are handled gracefully (None returned for absent levels).

**Important:** The `hierarchy` dict does **not** contain a `"DT Code"` field. The DT code must be extracted from `hierarchy["DT"]` using the parenthesized suffix parser.

---

## 6. Geo Endpoint

**Verified:**
- **Endpoint:** `GET /portal/meters/{meter_id}/geo`
- **Response:**
  ```json
  { "data": { "latitude": "26.938961002479868", "longitude": "75.83095696146852" } }
  ```
- **Note:** Coordinates are returned as **strings** from this endpoint. The client coerces them to `float`.

---

## 7. Energy Endpoint

**Verified:**
- **Endpoint:** `GET /portal/meters/{meter_id}/energy`
- **Response:** `{ "data": [ { "timestamp": "23/06/2026 23:30", "kwh": 48438.74, "kvah": 52313.84, "voltR": 226.0 }, ... ] }`
- **Observation:** 337 readings per meter, at 30-minute intervals (~7 days of data).
- **Timestamp format:** `DD/MM/YYYY HH:MM` (Indian date format).
- Only R-phase voltage (`voltR`) is provided. B-phase and Y-phase voltage are not present.

---

## 8. Distribution Transformers

**Verified:**
- **Endpoint:** `GET /portal/dts?page={p}`
- **Total:** 40 DTs across 2 pages (observed).
- **Response shape:** `{ "data": [ { "code": "DT-001", "name": "...", "feederCode": "F-001", "capacityKva": 100 } ], "total": 40 }`

---

## 9. Bulk Export (HMAC-Signed)

**Verified** (by decompiling `node_4.js` and `node_6.js` JS bundles):

### 9.1 Signing Secret Retrieval
```
GET /portal/keys
→ { "data": { "signingSecret": "<secret>" } }
```

### 9.2 Signature Algorithm
String-to-sign format (newline-delimited):
```
GET
/portal/export
page=1
<unix_timestamp_seconds>
```

Signature: `HMAC-SHA256(signing_secret, string_to_sign)` as lowercase hex.

Required headers:
```
x-timestamp: <unix_timestamp>
x-signature: <hex_signature>
```

### 9.3 Response & Architectural Trade-off
Returns all 403 meters in a single response, each with geo coordinates (numeric `lat`/`lng`), hierarchy objects, and nameplate attributes. **This is the primary cache-warming path.**

**Trade-off & Defensive Degradation:** While `/portal/export` is significantly faster than 21 sequential HTTP requests, it is an internal SvelteKit endpoint rather than a formal public API. To protect against upstream deprecation or breaking changes, the service implements an automatic fallback to paginated search (`/portal/meters/search`) with strict completeness validation.

**Note:** The signing secret is an internal credential. Exposure in Git history or documentation would allow unauthorized access to bulk data.

### 9.4 Geo format difference from single-meter endpoint
The bulk export uses numeric coordinates with keys `lat`/`lng`. The single-meter `/geo` endpoint uses string coordinates with keys `latitude`/`longitude`. Both are normalized to `float` by the client.

---

## 10. Known Quirks & Edge Cases

| Quirk | Detail |
|-------|--------|
| Dual nameplate schemas | Legacy: array of `{parameterName, parameterValue}`. V2: stringified JSON in `classData`. Both normalized by `normalize_nameplate()`. |
| Geo coordinate types | Strings from `/geo`, floats from `/export`. Both coerced to `float`. |
| DT code location | Only in `hierarchy["DT"]` string (e.g., `"Name (DT-001)"`), not in nameplate. |
| Indian date format | Energy timestamps use `DD/MM/YYYY HH:MM`. Not ISO 8601. |
| SvelteKit CSRF | `POST /login` requires matching `Origin` and `Referer` headers. |
| 401 on expired session | All portal endpoints return `401` when session expires. The client retries once with a fresh token. |
| Page size | 20 records per search page. Assumed stable, not guaranteed. |
