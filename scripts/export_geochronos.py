"""Export the GeoChronos knowledge base into three flat CSVs the app can seed from.

Usage (from the repo root):
    python scripts/export_geochronos.py path/to/geochronos.sqlite data/

Reads the SQLite database built by the GeoChronos repo
(https://github.com/roee-aido/GeoChronos, `python scripts/build_db.py --profile full`)
and writes, into the output folder:

    geochronos_sites.csv         one row per item (site, structure or object)
    geochronos_site_dates.csv    one row per dating claim, with status and confidence
    geochronos_site_sources.csv  citations only (source, link, licence); no source text

Rejected claims are left out. Years are astronomical years (1 BCE = 0, 2500 BCE = -2499),
exactly as stored in GeoChronos. Nothing is guessed or filled in here: an empty cell means
the source database has no value.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

LOCATION_ORDER = ("primary", "find_spot", "gazetteer_point")


def _num(v):
    if v is None:
        return ""
    return int(v) if float(v).is_integer() else v


def _names(conn, entity_id: str, predicate: str) -> list[str]:
    rows = conn.execute(
        "SELECT o.canonical_name FROM relation r JOIN entity o ON o.entity_id = r.object_id "
        "JOIN claim c ON c.claim_id = r.claim_id "
        "WHERE r.subject_id = ? AND r.predicate_id = ? AND c.status != 'rejected' ORDER BY r.relation_id",
        (entity_id, predicate),
    ).fetchall()
    seen, out = set(), []
    for (n,) in rows:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def export(db_path: Path, out_dir: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = conn.execute(
        "SELECT entity_id, canonical_name, description FROM entity "
        "WHERE kind = 'item' AND record_status = 'active' ORDER BY entity_id"
    ).fetchall()

    sites, dates, sources = [], [], []
    for entity_id, name, description in items:
        site_id = entity_id.split(":", 1)[1]

        he = conn.execute(
            "SELECT name FROM entity_name WHERE entity_id = ? AND lang = 'he' ORDER BY name_id LIMIT 1", (entity_id,)
        ).fetchone()

        loc = None
        for role in LOCATION_ORDER:
            loc = conn.execute(
                "SELECT lat, lon, precision, role FROM location WHERE entity_id = ? AND role = ? AND lat IS NOT NULL "
                "ORDER BY location_id LIMIT 1", (entity_id, role)
            ).fetchone()
            if loc:
                break

        located = conn.execute(
            "SELECT o.entity_id, o.canonical_name FROM relation r JOIN entity o ON o.entity_id = r.object_id "
            "JOIN claim c ON c.claim_id = r.claim_id WHERE r.subject_id = ? AND r.predicate_id = 'located_in' "
            "AND c.status != 'rejected' ORDER BY r.relation_id", (entity_id,)
        ).fetchall()
        country = "; ".join(dict.fromkeys(n for i, n in located if i.startswith("place:country:")))
        places = "; ".join(dict.fromkeys(n for i, n in located if not i.startswith("place:country:")))

        sites.append({
            "site_id": site_id,
            "name": name,
            "name_he": he[0] if he else "",
            "description": description or "",
            "kind": "; ".join(_names(conn, entity_id, "instance_of")),
            "function": "; ".join(_names(conn, entity_id, "has_function")),
            "form": "; ".join(_names(conn, entity_id, "has_form")),
            "culture": "; ".join(_names(conn, entity_id, "culture")),
            "polity": "; ".join(_names(conn, entity_id, "polity")),
            "period": "; ".join(_names(conn, entity_id, "period")),
            "country": country,
            "places": places,
            "lat": loc[0] if loc else "",
            "lon": loc[1] if loc else "",
            "location_precision": loc[2] if loc else "",
            "location_role": loc[3] if loc else "",
        })

        for r in conn.execute(
            "SELECT t.role, t.earliest_start, t.latest_start, t.earliest_end, t.latest_end, t.precision, t.qualifier, "
            "t.label, t.dating_method, t.source_calendar, c.status, c.confidence, c.rank, c.dispute_group, c.note "
            "FROM temporal_extent t JOIN claim c ON c.claim_id = t.claim_id "
            "WHERE t.entity_id = ? AND c.status != 'rejected' ORDER BY t.extent_id", (entity_id,)
        ):
            dates.append({
                "site_id": site_id, "role": r[0],
                "start_earliest": _num(r[1]), "start_latest": _num(r[2]),
                "end_earliest": _num(r[3]), "end_latest": _num(r[4]),
                "precision": r[5], "qualifier": r[6] or "", "label": r[7] or "",
                "dating_method": r[8] or "", "calendar": r[9] or "",
                "status": r[10], "confidence": r[11], "rank": r[12],
                "dispute_group": r[13] or "", "note": r[14] or "",
            })

        seen = set()
        for title, url, src_url, licence, locator in conn.execute(
            "SELECT s.title, ev.url, s.url, s.license, ev.locator FROM claim c "
            "JOIN claim_evidence ev ON ev.claim_id = c.claim_id JOIN source s ON s.source_id = ev.source_id "
            "WHERE c.entity_id = ? AND c.status != 'rejected' ORDER BY ev.evidence_id", (entity_id,)
        ):
            key = (title, url or src_url or "")
            if key in seen:
                continue
            seen.add(key)
            sources.append({"site_id": site_id, "source": title, "url": url or src_url or "",
                            "locator": locator or "", "licence": licence or ""})

    def write(name: str, rows: list[dict]):
        with (out_dir / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    write("geochronos_sites.csv", sites)
    write("geochronos_site_dates.csv", dates)
    write("geochronos_site_sources.csv", sources)
    return {"sites": len(sites), "dates": len(dates), "sources": len(sources)}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(export(Path(sys.argv[1]), Path(sys.argv[2])))
