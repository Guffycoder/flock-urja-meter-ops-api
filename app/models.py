from __future__ import annotations
from typing import List, Optional, Dict, Any, Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class GeoCoordinates(BaseModel):
    latitude: float = Field(..., description="Latitude in decimal degrees", examples=[26.938961])
    longitude: float = Field(..., description="Longitude in decimal degrees", examples=[75.830957])


class HierarchyEntity(BaseModel):
    name: str = Field(..., description="Entity display name", examples=["Jaipur Zone 1"])
    code: str = Field(..., description="Entity code", examples=["Z-01"])


class NetworkHierarchy(BaseModel):
    zone: Optional[HierarchyEntity] = Field(None, description="Zone level")
    circle: Optional[HierarchyEntity] = Field(None, description="Circle level")
    division: Optional[HierarchyEntity] = Field(None, description="Division level")
    subdivision: Optional[HierarchyEntity] = Field(None, description="Subdivision level")
    substation: Optional[HierarchyEntity] = Field(None, description="Substation level")
    feeder: Optional[HierarchyEntity] = Field(None, description="Feeder line level")
    dt: Optional[HierarchyEntity] = Field(None, description="Distribution Transformer level")


class MeterSummary(BaseModel):
    meterId: str = Field(..., description="Unique alphanumeric Meter ID", examples=["J100000"])
    serialNo: str = Field(..., description="Hardware manufacturer serial number", examples=["SE33962"])
    make: str = Field(..., description="Meter manufacturer (e.g., HPL, L&T, Genus)", examples=["HPL"])
    phaseType: str = Field(..., description="Phase configuration (single or three)", examples=["single"])
    installStatus: str = Field(..., description="Operational status (Installed, Faulty, Decommissioned)", examples=["Installed"])
    installType: Optional[str] = Field(None, description="Installation type (Whole Current, CT Operated)", examples=["Whole Current"])
    build: Optional[str] = Field(None, description="Firmware/Device generation (legacy or v2)", examples=["legacy"])
    dtCode: str = Field(..., description="Connected Distribution Transformer code", examples=["DT-001"])


class MeterDetail(MeterSummary):
    geo: Optional[GeoCoordinates] = Field(None, description="Geographic coordinates of installation")
    hierarchy: Optional[NetworkHierarchy] = Field(None, description="Full electrical grid hierarchy path")
    nameplate: Dict[str, str] = Field(
        default_factory=dict,
        description="Normalized key-value attributes from meter nameplate specifications"
    )


class NearbyMeter(BaseModel):
    meter: MeterSummary = Field(..., description="Meter summary data")
    distanceKm: float = Field(..., description="Great-circle distance from query point in kilometers", examples=[0.45])
    geo: GeoCoordinates = Field(..., description="Meter coordinates")


class EnergyReading(BaseModel):
    timestamp: str = Field(..., description="Reading timestamp in DD/MM/YYYY HH:MM format", examples=["23/06/2026 23:30"])
    kwh: Optional[float] = Field(None, description="Active energy in kilowatt-hours (kWh)", examples=[48438.74])
    kvah: Optional[float] = Field(None, description="Apparent energy in kilovolt-ampere-hours (kVAh)", examples=[52313.84])
    voltR: Optional[float] = Field(None, description="R-phase voltage in Volts (V)", examples=[226.0])


class EnergyAnalytics(BaseModel):
    totalReadings: int = Field(..., description="Number of interval readings available", examples=[337])
    windowStart: Optional[str] = Field(None, description="Earliest recorded reading timestamp", examples=["23/06/2026 23:30"])
    windowEnd: Optional[str] = Field(None, description="Latest recorded reading timestamp", examples=["30/06/2026 23:30"])
    latestKwh: Optional[float] = Field(None, description="Most recent kWh reading", examples=[48512.10])
    netKwhConsumption: Optional[float] = Field(None, description="Net active energy consumed across recorded window", examples=[73.36])
    averageVoltageVolts: Optional[float] = Field(None, description="Average recorded voltage in Volts", examples=[229.4])
    minVoltageVolts: Optional[float] = Field(None, description="Minimum recorded voltage in Volts", examples=[215.0])
    maxVoltageVolts: Optional[float] = Field(None, description="Maximum recorded voltage in Volts", examples=[248.0])


class EnergyConsumptionResponse(BaseModel):
    meterId: str = Field(..., description="Meter ID", examples=["J100000"])
    analytics: EnergyAnalytics = Field(..., description="Computed consumption and voltage analytics")
    readings: List[EnergyReading] = Field(..., description="Chronological interval energy readings")


class DistributionTransformer(BaseModel):
    code: str = Field(..., description="Unique Distribution Transformer code", examples=["DT-001"])
    name: str = Field(..., description="Human-readable DT name", examples=["Malviya Nagar DT 1"])
    feederCode: str = Field(..., description="Parent Feeder line code", examples=["F-001"])
    capacityKva: int = Field(..., description="Rated transformer capacity in kVA", examples=[100])
    connectedMetersCount: Optional[int] = Field(0, description="Total smart meters connected under this DT", examples=[12])


class PaginatedResponse(BaseModel, Generic[T]):
    total: int = Field(..., description="Total items matching query filter", examples=[403])
    page: int = Field(..., description="Current page number (1-indexed)", examples=[1])
    pageSize: int = Field(..., description="Items returned per page", examples=[20])
    totalPages: int = Field(..., description="Total number of pages available", examples=[21])
    data: List[T] = Field(..., description="List of items for current page")


class HealthResponse(BaseModel):
    status: str = Field(..., description="API operational health (ok, degraded)", examples=["ok"])
    version: str = Field(..., description="API service semantic version", examples=["1.0.0"])
    upstreamConnected: bool = Field(..., description="Whether upstream portal authentication is verified", examples=[True])
    cachedMetersCount: int = Field(..., description="Number of meters currently indexed in fast memory cache", examples=[403])
    cacheAgeSeconds: Optional[float] = Field(None, description="Age of meter catalog cache in seconds", examples=[45.2])
