from typing import Optional, Any
from sqlalchemy import Column, JSON, Text
from sqlmodel import Field, SQLModel


class Sector(SQLModel, table=True):
    """One Brussels statistical sector (Statbel CD_SECTOR)."""
    id: str = Field(primary_key=True)        # CD_SECTOR
    name_fr: Optional[str] = None
    name_nl: Optional[str] = None
    cd_munty_refnis: Optional[str] = None    # municipality NIS code
    population: Optional[int] = None
    area_ha: Optional[float] = None
    geometry: Optional[Any] = Field(default=None, sa_column=Column(JSON))   # GeoJSON polygon
    centroid_lon: Optional[float] = None
    centroid_lat: Optional[float] = None
    osm_coverage: Optional[float] = None     # 0..1 completeness vs reference register


class SectorScore(SQLModel, table=True):
    __tablename__ = "sector_score"
    id: Optional[int] = Field(default=None, primary_key=True)
    sector_id: str = Field(foreign_key="sector.id", index=True)
    scenario: str                            # 'family' | 'senior' | 'remote_work'
    score: int                               # 0..100 absolute
    percentile: int                          # 0..100 Hazen rank across Brussels sectors
    breakdown: Optional[Any] = Field(default=None, sa_column=Column(JSON))  # per-category sub-scores
    pros: Optional[Any] = Field(default=None, sa_column=Column(JSON))       # list of strings
    cons: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    narrative: Optional[str] = Field(default=None, sa_column=Column(Text))  # precomputed prose
    highlights: Optional[Any] = Field(default=None, sa_column=Column(JSON)) # [{label, kind}]


class Poi(SQLModel, table=True):
    """Point of interest for map rendering."""
    id: Optional[int] = Field(default=None, primary_key=True)
    sector_id: Optional[str] = Field(default=None, foreign_key="sector.id", index=True)
    category: str   # 'school'|'park'|'pharmacy'|'transit'|'cafe'|...
    name: Optional[str] = None
    lat: float
    lng: float


class Improvement(SQLModel, table=True):
    """'How to improve' suggestion: add one POI → score delta."""
    id: Optional[int] = Field(default=None, primary_key=True)
    sector_id: str = Field(foreign_key="sector.id", index=True)
    scenario: str
    rank: int
    title: str                               # '+1 pharmacy within 800m'
    category: str
    score_delta: int                         # +9
    from_score: int                          # 62
    to_score: int                            # 71
    suggested_lat: Optional[float] = None   # where the gap is (for map marker)
    suggested_lng: Optional[float] = None
