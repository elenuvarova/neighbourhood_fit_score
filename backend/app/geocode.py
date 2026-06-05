"""Nominatim geocoding with SQLite cache."""
from __future__ import annotations

import os
import time

import requests
from sqlmodel import Session, select

from app.models import GeocodeCache

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Nominatim asks every client to send an identifying User-Agent; honour the
# deploy-configured value (the NOMINATIM_USER_AGENT env var) when present.
_USER_AGENT = os.getenv(
    "NOMINATIM_USER_AGENT",
    "neighbourhood-fit-score/1.0 (open-source, brussels-pilot)",
)

# Per-city WGS84 bounding boxes — used as a hint (not hard restriction).
# Format: lon_min,lat_max,lon_max,lat_min. Default to Brussels for unknown cities.
_VIEWBOXES: dict[str, str] = {
    "brussels": "4.23,50.93,4.50,50.77",
    "antwerp":  "4.28,51.30,4.53,51.13",
}
_DEFAULT_CITY = "brussels"

_last_call: float = 0.0


def geocode(
    address: str, db: Session, city: str = "brussels"
) -> tuple[float, float] | tuple[None, None]:
    """
    Geocode an address to (lat, lng).  Checks cache first; calls Nominatim on miss.
    Returns (None, None) if address is not found.

    The cache key is prefixed with `city` so Brussels and Antwerp results for the
    same street name do not collide. The Nominatim viewbox is chosen per city.
    """
    viewbox = _VIEWBOXES.get(city, _VIEWBOXES[_DEFAULT_CITY])
    cache_key = f"{city}:{address}"

    cached = db.exec(select(GeocodeCache).where(GeocodeCache.query == cache_key)).first()
    if cached:
        return cached.lat, cached.lng

    global _last_call
    elapsed = time.monotonic() - _last_call
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_call = time.monotonic()

    try:
        resp = requests.get(
            _NOMINATIM,
            params={
                "q": address,
                "format": "json",
                "limit": 1,
                "viewbox": viewbox,
                "addressdetails": 0,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception:
        return None, None

    if not results:
        return None, None

    lat = float(results[0]["lat"])
    lng = float(results[0]["lon"])

    db.add(GeocodeCache(query=cache_key, lat=lat, lng=lng))
    db.commit()

    return lat, lng
