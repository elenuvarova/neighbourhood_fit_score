"""Nominatim geocoding with SQLite cache."""
from __future__ import annotations

import time

import requests
from sqlmodel import Session, select

from app.models import GeocodeCache

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "neighbourhood-fit-score/1.0 (open-source, brussels-pilot)"

# Brussels WGS84 bounding box — used as a hint (not hard restriction)
_VIEWBOX = "4.23,50.93,4.50,50.77"  # lon_min,lat_max,lon_max,lat_min

_last_call: float = 0.0


def geocode(address: str, db: Session) -> tuple[float, float] | tuple[None, None]:
    """
    Geocode an address to (lat, lng).  Checks cache first; calls Nominatim on miss.
    Returns (None, None) if address is not found.
    """
    cached = db.exec(select(GeocodeCache).where(GeocodeCache.query == address)).first()
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
                "viewbox": _VIEWBOX,
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

    db.add(GeocodeCache(query=address, lat=lat, lng=lng))
    db.commit()

    return lat, lng
