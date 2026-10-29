# REFLECTION.md — Engineering Decisions & Self-Review

---

## 1. What assumptions did you make?

**Meter attribute stability:** Physical meter attributes (make, phase type, serial number, GPS location, DT mapping, and grid hierarchy) change only during field installations or maintenance. Interval energy readings are dynamic and volatile. This justified a 5-minute TTL for the catalog cache and a 1-minute TTL for energy data.

**Single-tenancy and known topology:** The portal represents one utility operating in Rajasthan (Jaipur Zone 1 and Zone 2). The hierarchy reconstruction was modeled around Indian utility topology: Zone → Circle → Division → Subdivision → Substation → Feeder → Distribution Transformer → Meter.

**Page size stability:** The `/portal/meters/search` endpoint consistently returned 20 records per page across all observed requests. The pagination loop treats this as a stable behavior but it is not a guaranteed contract. If the portal changes the page size, the completeness check (count vs. `total`) would still catch a mismatch.

**Hierarchy format consistency:** The flat `{Zone: "Name (CODE)"}` format was directly verified for meter `J100000`. Other meters are assumed to follow the same pattern. The parser handles missing levels gracefully (returns `None` for any absent level).

**DT-code source in the cache-miss fallback:** In the single-meter fallback path (when a meter is not in the warm cache), the DT code exists only in `hierarchy["DT"]`. It is not present in the normalized nameplate for either legacy or v2 meters.

**Portal rate tolerance:** The legacy portal may be fragile under concurrent load. The service uses an in-memory cache, connection pooling, and a single re-authentication lock to minimize direct pressure on upstream.

---

## 2. Which part was the most difficult, and how did you get unstuck?

**The CSRF barrier at login.** Direct `POST /login` requests returned `403 Forbidden` with `"Cross-site POST form submissions are forbidden"`. The login page is rendered by SvelteKit rather than a dedicated REST endpoint, so standard HTTP clients hit SvelteKit's default form action origin check. Tracing SvelteKit's open-source `checkOrigin` function revealed it validates `Origin` and `Referer` headers against the host. Adding those headers resolved the 403 immediately.

**Discovering the bulk export path.** The portal UI loads all meter data one page at a time. Finding the high-performance bulk path required decompiling minified JavaScript bundles (`node_4.js`, `node_6.js`) to trace the HMAC-SHA256 signing implementation (`crypto.subtle`) back to the `/portal/export` endpoint and the `/portal/keys` secret retrieval path.

**The SvelteKit `__data.json` hierarchy parsing.** The `__data.json` endpoint returns a nested `nodes` array where the relevant node's `data` field can be either a dict or a packed list depending on the SvelteKit serializer version. The original list-format branch was a stub (`pass`) that extracted nothing. Tracing the actual observed response structure (documented in `meter_detail.html` from portal exploration) revealed the correct extraction path.

**The DT-code bug.** The original fallback path attempted `nameplate.get("DT Code", "")`, which always returns `""` because no nameplate schema contains a `"DT Code"` field. The actual DT code is embedded in the `hierarchy["DT"]` string as a parenthesized suffix, e.g., `"Malviya Nagar DT 1 (DT-001)"`.

---

## 3. If you had another day, what would you improve?

**Persistent cache with Redis:** The current in-memory cache is scoped to a single process. A Redis-backed cache with background refresh would survive restarts, support multi-instance deployments, and allow cache invalidation via webhooks if the portal ever exposes change events.

**Time-series storage for energy readings:** The 337 readings per meter (~7 days at 30-minute intervals) are currently fetched and discarded after each request. Persisting them in a columnar store (TimescaleDB, ClickHouse) would allow time-range slicing, daily peak demand aggregations, and voltage anomaly detection.

**Verify hierarchy format for a larger sample:** The flat `{Zone: "Name (CODE)"}` format was confirmed for one meter. A systematic check across all 403 meters — particularly across different DTs, feeders, and zones — would confirm whether any meters have absent or differently-formatted hierarchy levels.

**CI/CD Automated Secret Scanning & Pre-Commit Hooks:** While the local Git history was safely rebuilt and verified prior to publishing, integrating automated pre-commit scanning (e.g., `gitleaks` or `detect-secrets`) into a continuous integration pipeline would prevent accidental credential commits automatically for future contributors.

---

## 4. What mistake did you make?

**Assumed `/api` routes existed.** The first round of portal probing targeted standard REST paths (`/api/meters`, `/api/v1/meters`), all of which returned `404`. The actual data surfaces are SvelteKit-specific: server-side page data via `/__data.json` and internal portal routes under `/portal/*`. The adjustment came from inspecting the compiled JavaScript bundles rather than guessing URL patterns.

**The list-format `__data.json` parser was a stub.** The original `get_meter_server_detail` implementation had a `pass` inside the list-format branch — it was intended as a placeholder but was never completed. This meant the fallback path always returned empty `raw_detail` and `raw_hierarchy` for meters served in the packed-list node format, causing `hierarchy=null` and `dtCode=""` in those responses.

**DT code looked for in the wrong place.** The original fallback `dtCode=nameplate.get("DT Code", "")` was written without verifying that the nameplate actually contains that field. Checking the normalized nameplate output for both legacy and v2 schemas confirmed it never does. The correct source — `hierarchy["DT"]` — required inspecting the observed portal response.

---

## 5. If you were reviewing your own submission, what would you critique?

**Over-reliance on a single verified meter.** The hierarchy parser was built and tested against `J100000` only. A thorough implementation would systematically verify the format across a sample of meters from multiple zones, feeders, and DTs before treating it as a universal pattern.

**The bulk export path has no graceful degradation test.** The two-tier cache warming (bulk export → paginated search fallback) is implemented and tested offline, but the condition where bulk export returns a partial or malformed payload — rather than failing outright — is not covered.

**Energy cache has no completeness check.** The pagination completeness validation (matching count vs. `total`) was applied to the meter catalog. A similar guard does not exist for the energy endpoint, which is assumed to always return a complete response. This is a gap that should be acknowledged.

**The `build` field (`legacy` / `v2`) is populated during bulk export but not during the single-meter cache-miss fallback.** A meter retrieved via the fallback path would have `build=null` because the `__data.json` response does not include a `build` field. This is a silent inconsistency between cached and uncached responses.

**No rate limiting on our own API.** The service protects the upstream portal by caching aggressively, but it does not protect itself from consumers that call high-cost endpoints (like `/consumption`) in rapid succession.
