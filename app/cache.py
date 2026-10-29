from __future__ import annotations
import asyncio
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from app.client import UrjaPortalClient
from app.config import Settings, get_settings
from app.models import (
    DistributionTransformer,
    EnergyAnalytics,
    EnergyConsumptionResponse,
    EnergyReading,
    GeoCoordinates,
    HierarchyEntity,
    MeterDetail,
    MeterSummary,
    NearbyMeter,
    NetworkHierarchy,
)

logger = logging.getLogger("urja_cache")


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points on Earth in kilometers."""
    r = 6371.0  # Earth's mean radius in kilometers
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


class DataCatalogCache:
    """
    High-performance in-memory index for smart meter and network topology data.
    Provides sub-millisecond multi-attribute filtering, spatial search,
    and network tree reconstruction.
    """

    def __init__(self, client: UrjaPortalClient, settings: Optional[Settings] = None):
        self.client = client
        self.settings = settings or get_settings()
        self._meters_by_id: Dict[str, MeterDetail] = {}
        self._transformers_by_code: Dict[str, DistributionTransformer] = {}
        self._energy_cache: Dict[str, Tuple[float, EnergyConsumptionResponse]] = {}
        self._last_catalog_refresh: float = 0.0
        self.last_refresh_method: str = "none"
        self.last_refresh_success: bool = False
        self._refresh_lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None

    @property
    def is_warmed(self) -> bool:
        return len(self._meters_by_id) > 0

    @property
    def cache_age_seconds(self) -> Optional[float]:
        if self._last_catalog_refresh == 0.0:
            return None
        return time.time() - self._last_catalog_refresh

    @property
    def total_meters(self) -> int:
        return len(self._meters_by_id)

    async def warm_cache(self, force: bool = False):
        """
        Warm or refresh meter and transformer catalog from upstream.
        Employs a resilient multi-tier data loading strategy:
          Tier 1: High-performance HMAC-signed bulk export.
          Tier 2: Defensive fallback to authenticated, read-only paginated search (/portal/meters/search).
          Tier 3: Graceful preservation of previously cached data (stale-while-revalidate).
        """
        now = time.time()
        if not force and self.is_warmed and (now - self._last_catalog_refresh) < self.settings.cache_ttl_seconds:
            return

        async with self._refresh_lock:
            # Double check within lock
            if not force and self.is_warmed and (time.time() - self._last_catalog_refresh) < self.settings.cache_ttl_seconds:
                return

            logger.info("Refreshing Urja Meter Ops data catalog from upstream...")
            refresh_method = "bulk_export"
            try:
                # 1. Primary: Attempt HMAC bulk export
                raw_meters: List[Dict[str, Any]] = []
                try:
                    raw_meters = await self.client.export_all_meters_raw()
                    if not raw_meters or not isinstance(raw_meters, list):
                        raise UpstreamPortalError("Bulk export returned empty or invalid data format.")
                except Exception as export_err:
                    logger.warning(
                        "Primary bulk export unavailable or rejected (%s). Engaging verified paginated fallback...",
                        export_err
                    )
                    refresh_method = "paginated_fallback"
                    raw_meters = await self.client.fetch_all_meters_paginated()

                if not raw_meters:
                    raise UpstreamPortalError("Both bulk export and paginated fallback failed to retrieve meter data.")

                # 2. Transformers fetch (with fallback to existing cache on transient failure)
                raw_dts: List[Dict[str, Any]] = []
                try:
                    raw_dts = await self.client.get_all_transformers()
                except Exception as dt_err:
                    logger.warning("Transformer list fetch failed (%s); retaining existing DT cache.", dt_err)

                new_meters: Dict[str, MeterDetail] = {}
                dt_meter_counts: Dict[str, int] = {}

                for m in raw_meters:
                    meter_id = m.get("meterId", "")
                    if not meter_id:
                        continue
                    dt_code = m.get("dtCode", "")
                    if dt_code:
                        dt_meter_counts[dt_code] = dt_meter_counts.get(dt_code, 0) + 1

                    # Parse coordinates
                    geo = None
                    geo_raw = m.get("geo")
                    if geo_raw and isinstance(geo_raw, dict):
                        lat_val = geo_raw.get("lat") or geo_raw.get("latitude")
                        lng_val = geo_raw.get("lng") or geo_raw.get("longitude")
                        if lat_val is not None and lng_val is not None:
                            try:
                                geo = GeoCoordinates(
                                    latitude=float(lat_val),
                                    longitude=float(lng_val)
                                )
                            except (ValueError, TypeError):
                                pass

                    # Parse hierarchy
                    hierarchy = None
                    h_raw = m.get("hierarchy")
                    if h_raw and isinstance(h_raw, dict):
                        hierarchy = NetworkHierarchy(
                            zone=HierarchyEntity(**h_raw["zone"]) if "zone" in h_raw and h_raw["zone"] else None,
                            circle=HierarchyEntity(**h_raw["circle"]) if "circle" in h_raw and h_raw["circle"] else None,
                            division=HierarchyEntity(**h_raw["division"]) if "division" in h_raw and h_raw["division"] else None,
                            subdivision=HierarchyEntity(**h_raw["subdivision"]) if "subdivision" in h_raw and h_raw["subdivision"] else None,
                            substation=HierarchyEntity(**h_raw["substation"]) if "substation" in h_raw and h_raw["substation"] else None,
                            feeder=HierarchyEntity(**h_raw["feeder"]) if "feeder" in h_raw and h_raw["feeder"] else None,
                            dt=HierarchyEntity(**h_raw["dt"]) if "dt" in h_raw and h_raw["dt"] else None,
                        )

                    # Construct nameplate dict
                    nameplate = {
                        "Meter ID": meter_id,
                        "Serial No": str(m.get("serialNo", "")),
                        "Make": str(m.get("make", "")),
                        "Phase Type": str(m.get("phaseType", "")),
                        "Installation Status": str(m.get("installStatus", "")),
                        "Installation Type": str(m.get("installType", "") or ""),
                        "Build": str(m.get("build", "") or "")
                    }

                    detail = MeterDetail(
                        meterId=meter_id,
                        serialNo=str(m.get("serialNo", "")),
                        make=str(m.get("make", "")),
                        phaseType=str(m.get("phaseType", "")),
                        installStatus=str(m.get("installStatus", "")),
                        installType=m.get("installType"),
                        build=m.get("build"),
                        dtCode=dt_code,
                        geo=geo,
                        hierarchy=hierarchy,
                        nameplate=nameplate
                    )
                    new_meters[meter_id] = detail

                # Parse transformers if newly retrieved
                if raw_dts:
                    new_dts: Dict[str, DistributionTransformer] = {}
                    for d in raw_dts:
                        code = d.get("code", "")
                        if not code:
                            continue
                        new_dts[code] = DistributionTransformer(
                            code=code,
                            name=str(d.get("name", "")),
                            feederCode=str(d.get("feederCode", "")),
                            capacityKva=int(d.get("capacityKva", 0)),
                            connectedMetersCount=dt_meter_counts.get(code, 0)
                        )
                    self._transformers_by_code = new_dts

                if not new_meters:
                    raise UpstreamPortalError("Refresh produced 0 valid meter records; retaining existing cache.")

                # Atomically swap active meter index
                self._meters_by_id = new_meters
                self._last_catalog_refresh = time.time()
                self.last_refresh_method = refresh_method
                self.last_refresh_success = True

                logger.info(
                    "Catalog refreshed successfully via %s: %d meters, %d distribution transformers",
                    refresh_method,
                    len(new_meters),
                    len(self._transformers_by_code)
                )

            except Exception as exc:
                self.last_refresh_success = False
                logger.error("Failed to refresh catalog from upstream: %s", exc)
                # If we have existing cached data, keep serving it gracefully (stale-while-revalidate pattern)
                if not self.is_warmed:
                    raise exc

    # --------------------------------------------------------------------------
    # Meter Retrieval & Filtering
    # --------------------------------------------------------------------------

    def get_meter(self, meter_id: str) -> Optional[MeterDetail]:
        return self._meters_by_id.get(meter_id)

    def search_and_filter_meters(
        self,
        q: Optional[str] = None,
        status: Optional[str] = None,
        make: Optional[str] = None,
        phase: Optional[str] = None,
        dt_code: Optional[str] = None,
        build: Optional[str] = None,
        sort_by: str = "meterId",
        sort_order: str = "asc"
    ) -> List[MeterDetail]:
        """Filter meters by multi-dimensional criteria with case-insensitivity."""
        results = list(self._meters_by_id.values())

        # Free text search across meterId and serialNo
        if q:
            term = q.strip().lower()
            results = [
                m for m in results
                if term in m.meterId.lower() or term in m.serialNo.lower()
            ]

        # Status filter
        if status:
            s_val = status.strip().lower()
            results = [m for m in results if m.installStatus.lower() == s_val]

        # Make filter
        if make:
            m_val = make.strip().lower()
            results = [m for m in results if m.make.lower() == m_val]

        # Phase filter
        if phase:
            p_val = phase.strip().lower()
            results = [m for m in results if m.phaseType.lower() == p_val]

        # DT code filter
        if dt_code:
            d_val = dt_code.strip().upper()
            results = [m for m in results if m.dtCode.upper() == d_val]

        # Build filter
        if build:
            b_val = build.strip().lower()
            results = [m for m in results if (m.build or "").lower() == b_val]

        # Sorting
        reverse = sort_order.lower() == "desc"
        if sort_by == "serialNo":
            results.sort(key=lambda m: m.serialNo, reverse=reverse)
        elif sort_by == "make":
            results.sort(key=lambda m: m.make, reverse=reverse)
        elif sort_by == "installStatus":
            results.sort(key=lambda m: m.installStatus, reverse=reverse)
        elif sort_by == "dtCode":
            results.sort(key=lambda m: m.dtCode, reverse=reverse)
        else:
            results.sort(key=lambda m: m.meterId, reverse=reverse)

        return results

    def find_nearby_meters(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 5.0,
        limit: int = 50
    ) -> List[NearbyMeter]:
        """Find smart meters within a geographic radius ordered by proximity."""
        nearby: List[NearbyMeter] = []

        for m in self._meters_by_id.values():
            if not m.geo:
                continue
            dist = haversine_distance_km(latitude, longitude, m.geo.latitude, m.geo.longitude)
            if dist <= radius_km:
                nearby.append(
                    NearbyMeter(
                        meter=MeterSummary(
                            meterId=m.meterId,
                            serialNo=m.serialNo,
                            make=m.make,
                            phaseType=m.phaseType,
                            installStatus=m.installStatus,
                            installType=m.installType,
                            build=m.build,
                            dtCode=m.dtCode
                        ),
                        distanceKm=round(dist, 3),
                        geo=m.geo
                    )
                )

        nearby.sort(key=lambda x: x.distanceKm)
        return nearby[:limit]

    # --------------------------------------------------------------------------
    # Transformers & Hierarchy Tree
    # --------------------------------------------------------------------------

    def get_all_transformers(self) -> List[DistributionTransformer]:
        return sorted(list(self._transformers_by_code.values()), key=lambda d: d.code)

    def get_transformer(self, code: str) -> Optional[DistributionTransformer]:
        return self._transformers_by_code.get(code.upper())

    def get_meters_for_transformer(self, code: str) -> List[MeterSummary]:
        code_upper = code.upper()
        return [
            MeterSummary(
                meterId=m.meterId,
                serialNo=m.serialNo,
                make=m.make,
                phaseType=m.phaseType,
                installStatus=m.installStatus,
                installType=m.installType,
                build=m.build,
                dtCode=m.dtCode
            )
            for m in self._meters_by_id.values()
            if m.dtCode.upper() == code_upper
        ]

    def build_network_hierarchy_tree(self) -> Dict[str, Any]:
        """
        Reconstruct the full electrical network topology:
        Zone -> Circle -> Division -> Subdivision -> Substation -> Feeder -> DT -> Meters count & list
        """
        tree: Dict[str, Any] = {}

        for m in self._meters_by_id.values():
            if not m.hierarchy:
                continue
            h = m.hierarchy
            zone_key = h.zone.name if h.zone else "Unknown Zone"
            circle_key = h.circle.name if h.circle else "Unknown Circle"
            div_key = h.division.name if h.division else "Unknown Division"
            subdiv_key = h.subdivision.name if h.subdivision else "Unknown Subdivision"
            substation_key = h.substation.name if h.substation else "Unknown Substation"
            feeder_key = h.feeder.name if h.feeder else "Unknown Feeder"
            dt_key = h.dt.name if h.dt else m.dtCode

            z = tree.setdefault(zone_key, {})
            c = z.setdefault(circle_key, {})
            d = c.setdefault(div_key, {})
            sd = d.setdefault(subdiv_key, {})
            ss = sd.setdefault(substation_key, {})
            f = ss.setdefault(feeder_key, {})
            dt_node = f.setdefault(dt_key, {
                "dtCode": m.dtCode,
                "metersCount": 0,
                "meters": []
            })

            dt_node["metersCount"] += 1
            dt_node["meters"].append({
                "meterId": m.meterId,
                "serialNo": m.serialNo,
                "make": m.make,
                "status": m.installStatus
            })

        return tree

    # --------------------------------------------------------------------------
    # Consumption & Energy Analytics
    # --------------------------------------------------------------------------

    async def get_or_fetch_energy(self, meter_id: str, force_refresh: bool = False) -> EnergyConsumptionResponse:
        """Fetch energy readings, compute analytics, and cache with short TTL."""
        now = time.time()
        if not force_refresh and meter_id in self._energy_cache:
            cached_time, cached_resp = self._energy_cache[meter_id]
            if (now - cached_time) < self.settings.energy_cache_ttl_seconds:
                return cached_resp

        raw_readings = await self.client.get_meter_energy(meter_id)

        readings_list: List[EnergyReading] = []
        valid_kwh: List[float] = []
        valid_volts: List[float] = []

        for item in raw_readings:
            ts = item.get("timestamp", "")
            kwh = None
            kvah = None
            volt_r = None

            try:
                if item.get("kwh"):
                    kwh = float(item["kwh"])
                    valid_kwh.append(kwh)
            except (ValueError, TypeError):
                pass

            try:
                if item.get("kvah"):
                    kvah = float(item["kvah"])
            except (ValueError, TypeError):
                pass

            try:
                if item.get("voltR"):
                    volt_r = float(item["voltR"])
                    valid_volts.append(volt_r)
            except (ValueError, TypeError):
                pass

            readings_list.append(
                EnergyReading(
                    timestamp=ts,
                    kwh=kwh,
                    kvah=kvah,
                    voltR=volt_r
                )
            )

        # Compute analytics
        total_readings = len(readings_list)
        window_start = readings_list[0].timestamp if readings_list else None
        window_end = readings_list[-1].timestamp if readings_list else None
        latest_kwh = valid_kwh[-1] if valid_kwh else None
        net_kwh = round(valid_kwh[-1] - valid_kwh[0], 2) if len(valid_kwh) >= 2 else None
        avg_v = round(sum(valid_volts) / len(valid_volts), 1) if valid_volts else None
        min_v = min(valid_volts) if valid_volts else None
        max_v = max(valid_volts) if valid_volts else None

        analytics = EnergyAnalytics(
            totalReadings=total_readings,
            windowStart=window_start,
            windowEnd=window_end,
            latestKwh=latest_kwh,
            netKwhConsumption=net_kwh,
            averageVoltageVolts=avg_v,
            minVoltageVolts=min_v,
            maxVoltageVolts=max_v
        )

        response = EnergyConsumptionResponse(
            meterId=meter_id,
            analytics=analytics,
            readings=readings_list
        )

        self._energy_cache[meter_id] = (now, response)
        return response
