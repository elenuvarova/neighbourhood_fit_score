"""Smoke tests for the Neighbourhood Fit Score API.

These run against the seeded local SQLite database (backend/data.sqlite) via the
in-process FastAPI TestClient. They cover the happy path plus one failure path
for the public read endpoints. They never hit the live Groq endpoint.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_weights():
    r = client.get("/api/weights")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"family", "senior", "remote"}
    for scenario, weights in body.items():
        total = sum(weights.values())
        assert abs(total - 100.0) < 0.5, f"{scenario} weights sum to {total}, expected ~100"
    # A known category is present in at least one scenario.
    assert "park" in body["family"]


def test_sectors_geojson_etag_and_304():
    r = client.get("/api/sectors.geojson?scenario=family")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) > 0
    etag = r.headers.get("etag")
    assert etag is not None

    r2 = client.get(
        "/api/sectors.geojson?scenario=family",
        headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304


def test_sectors_geojson_invalid_scenario():
    r = client.get("/api/sectors.geojson?scenario=bogus")
    assert r.status_code == 422


def test_filter_valid_categories():
    r = client.get("/api/filter?scenario=family&categories=school,park")
    assert r.status_code == 200
    assert "matching" in r.json()


def test_filter_unknown_category():
    r = client.get("/api/filter?scenario=family&categories=school,not_a_category")
    assert r.status_code == 400


def test_sector_score():
    # Pick a real sector id from the geojson feature set.
    geo = client.get("/api/sectors.geojson?scenario=family").json()
    sector_id = geo["features"][0]["properties"]["id"]
    r = client.get(f"/api/sector/{sector_id}?scenario=senior")
    assert r.status_code == 200
    assert "score" in r.json()


def test_explain_without_api_key_returns_503(monkeypatch):
    # Only assert the unconfigured-key behaviour; never call the live provider.
    import app.main as main

    monkeypatch.setattr(main, "_GROQ_API_KEY", "")
    geo = client.get("/api/sectors.geojson?scenario=family").json()
    sector_id = geo["features"][0]["properties"]["id"]
    r = client.post(
        "/api/explain",
        json={"sector_id": sector_id, "scenario": "family"},
    )
    assert r.status_code == 503
