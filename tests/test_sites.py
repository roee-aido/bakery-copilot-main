"""Unit tests for the GeoChronos tools: same input, same output, and no invented answers."""
import pytest

from app.db import Site, SiteDate, SiteSource, TripRequest, init_db, engine
from app.tools.registry import execute
from app.tools.sites import (check_visit, coverage, recent_trip_requests, save_trip_request, find_sites, fmt_year, site_timeline, sites_in_time_window,
                             to_astronomical)

import json


@pytest.mark.parametrize("signed,astro,shown", [(-2500, -2499, "2500 BCE"), (-1, 0, "1 BCE"), (1, 1, "1 CE"), (1200, 1200, "1200 CE")])
def test_year_conversion_and_display(signed, astro, shown):
    assert to_astronomical(signed) == astro
    assert fmt_year(astro) == shown


def test_seed_loaded_the_geochronos_export(client, db):
    assert db.query(Site).count() == 95
    assert db.query(SiteDate).count() == 280
    assert db.query(SiteSource).count() == 245
    assert {d.status for d in db.query(SiteDate)} <= {"accepted", "alternative", "disputed", "needs_verification"}


def test_seeding_twice_adds_nothing(client, db):
    report = init_db(engine)
    assert report["sites_seeded"] == 0
    assert db.query(Site).count() == 95


def test_coverage_reports_counts_and_range(client, db):
    cov = coverage(db)
    assert cov["sites"] == 95 and cov["from"] and cov["to"]


def test_find_sites_by_country_returns_only_that_country(client, db):
    res = find_sites(db, country="Egypt", limit=50)
    assert res["total_matches"] > 0
    assert all("Egypt" in (s["country"] or "") for s in res["sites"])


def test_find_sites_matches_the_hebrew_name(client, db):
    res = find_sites(db, query="הפירמידה הגדולה")
    assert any(s["slug"] == "great-pyramid-of-giza" for s in res["sites"])


def test_find_sites_with_no_match_says_not_in_records(client, db):
    res = find_sites(db, query="zzz-no-such-place")
    assert res["total_matches"] == 0 and res["sites"] == []
    assert "not in our records" in res["note"].lower() or "no site" in res["note"].lower()


def test_site_timeline_keeps_every_dating_claim_with_status(client, db):
    res = site_timeline(db, site="Great Pyramid of Giza")
    assert res["found"] is True and res["site"]["slug"] == "great-pyramid-of-giza"
    assert res["has_alternative_or_disputed"] is True
    labels = " | ".join(c["label"] or "" for c in res["date_claims"])
    assert "2700 and 2500 BC" in labels and "calibrated" in labels.lower()
    assert res["date_claims"][0]["preferred"] is True  # the preferred claim comes first
    assert all(c["confidence"] for c in res["date_claims"])
    assert res["citations"] and "BCE" in " ".join(str(c["start_earliest"]) for c in res["date_claims"] if c["start_earliest"])


def test_site_timeline_for_an_unknown_site_is_not_invented(client, db):
    res = site_timeline(db, site="Atlantis")
    assert res == {"found": False, "reason": "not_in_records",
                   "note": "This site is not in our records. Say so; do not describe it from general knowledge."}


def test_site_timeline_asks_which_one_when_ambiguous(client, db):
    res = site_timeline(db, site="temple")
    assert res["found"] is False and res["reason"] == "ambiguous" and len(res["candidates"]) > 1


def test_window_finds_the_pyramid_when_built_and_not_in_modern_times(client, db):
    hit = sites_in_time_window(db, year_from=-2600, year_to=-2500, relation="built")
    assert "great-pyramid-of-giza" in [s["slug"] for s in hit["sites"]]
    assert hit["window"] == {"from": "2600 BCE", "to": "2500 BCE"}
    assert "not proof" in hit["note"] or "does not prove" in hit["note"].lower() or "not that the site" in hit["note"]
    modern = sites_in_time_window(db, year_from=2000, relation="built")
    assert "great-pyramid-of-giza" not in [s["slug"] for s in modern["sites"]]


def test_window_rejects_an_unknown_relation(client, db):
    assert sites_in_time_window(db, year_from=-500, relation="bogus")["error"].startswith("unknown relation")


def test_dispatcher_returns_json_and_survives_bad_input(client, db):
    ok = json.loads(execute("site_timeline", {"site": "Great Pyramid of Giza"}, db))
    assert ok["found"] is True
    assert "error" in json.loads(execute("no_such_tool", {}, db))
    assert "error" in json.loads(execute("find_sites", {"limit": "not-a-number"}, db))


def test_check_visit_fits_and_mismatch(client, db):
    ok = check_visit(db, site="Great Pyramid of Giza", year=-2550)
    assert ok["verdict"] in ("fits", "fits_only_alternative_dating")
    early = check_visit(db, site="Great Pyramid of Giza", year=-25500)
    assert early["verdict"] == "too_early"
    assert early["suggested_year"].endswith("BCE")
    late = check_visit(db, site="Great Pyramid of Giza", year=2200)
    assert late["verdict"] == "too_late"
    assert check_visit(db, site="Atlantis", year=-2500)["reason"] == "not_in_records"
    assert "error" in check_visit(db, site="Great Pyramid of Giza", year=0)


def test_save_trip_request_validates_and_dedupes(client, db):
    db.query(TripRequest).delete(); db.commit()
    bad = save_trip_request(db, site="Great Pyramid of Giza", year=-25500, traveller="Dana")
    assert bad["saved"] is True and bad["status"] == "needs_a_look" and bad["verdict"] == "too_early"
    again = save_trip_request(db, site="Great Pyramid of Giza", year=-25500, traveller="Dana")
    assert again["saved"] is False and again["reason"] == "duplicate"
    good = save_trip_request(db, site="Great Pyramid of Giza", year=-2550)
    assert good["saved"] is True and good["status"] in ("confirmed", "needs_a_look")
    assert save_trip_request(db, site="Atlantis", year=-2500)["reason"] == "not_in_records"
    assert len(recent_trip_requests(db)) == 2


def test_csv_values_fit_the_column_lengths():
    """SQLite ignores String(n); Postgres rejects values that are too long. Check the CSVs against the model."""
    import csv
    from pathlib import Path
    data = Path(__file__).resolve().parent.parent / "data"
    pairs = [(Site, "geochronos_sites.csv", {"slug": "site_id"}),
             (SiteDate, "geochronos_site_dates.csv", {"site_slug": "site_id"}),
             (SiteSource, "geochronos_site_sources.csv", {"site_slug": "site_id"})]
    for model, fname, rename in pairs:
        rows = list(csv.DictReader(open(data / fname, encoding="utf-8")))
        for col in model.__table__.columns:
            limit = getattr(col.type, "length", None)
            key = rename.get(col.name, col.name)
            if limit and key in rows[0]:
                longest = max(len(r[key] or "") for r in rows)
                assert longest <= limit, f"{model.__name__}.{col.name}: {longest} > {limit}"
