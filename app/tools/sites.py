"""Read-only tools over the GeoChronos records: find_sites, site_timeline, sites_in_time_window.

Same rule as the bakery's sales_query: the model never writes SQL and never answers from memory. It
picks a tool and parameters; the backend reads the tables. Nothing here guesses: a missing value stays
missing, an uncertain date keeps its range, status and confidence, and a site that is not in the
records is reported as not found.

Years in the database are astronomical (1 BCE = 0, 2500 BCE = -2499). The tools talk to the model in
ordinary signed years (BCE negative, no year 0: 2500 BCE = -2500, 1 CE = 1) and convert at the edge.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import Site, SiteDate, SiteSource, TripRequest

BUILT_ROLES = {"construction", "creation"}
IN_USE_ROLES = {"existence", "use", "occupation"}
STATUS_ORDER = {"accepted": 0, "alternative": 1, "disputed": 2, "needs_verification": 3}
YEAR_NOTE = ("Years shown as BCE/CE. Dates are scholarly estimates with a range; always report the range, "
             "its status and its confidence, never a single day.")


# --- year helpers ----------------------------------------------------------------------------------

def to_astronomical(year: int) -> int:
    """Signed calendar year (BCE negative, no year 0) -> astronomical year (1 BCE = 0)."""
    return year + 1 if year < 0 else year


def fmt_year(astro: float | None) -> str | None:
    if astro is None:
        return None
    y = int(astro)
    return f"{1 - y} BCE" if y <= 0 else f"{y} CE"


def _claim_view(d: SiteDate) -> dict:
    return {
        "role": d.role, "label": d.label,
        "start_earliest": fmt_year(d.start_earliest), "start_latest": fmt_year(d.start_latest),
        "end_earliest": fmt_year(d.end_earliest), "end_latest": fmt_year(d.end_latest),
        "precision": d.precision, "qualifier": d.qualifier, "dating_method": d.dating_method,
        "status": d.status, "confidence": d.confidence, "preferred": d.rank == "preferred",
        "dispute_group": d.dispute_group, "note": d.note,
    }


def _claim_sort_key(d: SiteDate):
    return (0 if d.rank == "preferred" else 1, STATUS_ORDER.get(d.status, 9), d.id)


def _span(d: SiteDate) -> tuple[float, float] | None:
    """Widest range the claim allows: earliest possible start to latest possible end (astronomical years)."""
    starts = [v for v in (d.start_earliest, d.start_latest) if v is not None]
    ends = [v for v in (d.end_latest, d.end_earliest, d.start_latest, d.start_earliest) if v is not None]
    if not starts or not ends:
        return None
    return min(starts), max(ends)


# --- filters ---------------------------------------------------------------------------------------

def _like(column, text: str):
    return func.lower(column).like(f"%{text.lower()}%")


def _site_filters(*, query=None, country=None, culture=None, kind=None, function=None, period=None) -> list:
    where = []
    if query:
        where.append(or_(_like(Site.name, query), _like(Site.name_he, query), _like(Site.description, query),
                         _like(Site.slug, query), _like(Site.places, query)))
    if country:
        where.append(_like(Site.country, country))
    if culture:
        where.append(or_(_like(Site.culture, culture), _like(Site.polity, culture)))
    if kind:
        where.append(_like(Site.kind, kind))
    if function:
        where.append(_like(Site.function, function))
    if period:
        where.append(_like(Site.period, period))
    return where


def _site_row(site: Site, claims: list[SiteDate]) -> dict:
    best = sorted(claims, key=_claim_sort_key)[:2]
    return {
        "slug": site.slug, "name": site.name, "name_he": site.name_he, "country": site.country, "places": site.places,
        "kind": site.kind, "culture": site.culture, "period": site.period,
        "lat": site.lat, "lon": site.lon, "location_precision": site.location_precision,
        "top_dates": [{"role": c.role, "label": c.label, "status": c.status, "confidence": c.confidence} for c in best],
    }


def _claims_by_site(db: Session, slugs: list[str]) -> dict[str, list[SiteDate]]:
    out: dict[str, list[SiteDate]] = {s: [] for s in slugs}
    if slugs:
        for d in db.scalars(select(SiteDate).where(SiteDate.site_slug.in_(slugs))):
            out[d.site_slug].append(d)
    return out


# --- tools -----------------------------------------------------------------------------------------

def find_sites(db: Session, *, query: str | None = None, country: str | None = None, culture: str | None = None,
               kind: str | None = None, function: str | None = None, period: str | None = None,
               limit: int = 15) -> dict:
    limit = max(1, min(int(limit or 15), 50))
    where = _site_filters(query=query, country=country, culture=culture, kind=kind, function=function, period=period)
    total = db.scalar(select(func.count()).select_from(Site).where(*where)) or 0
    sites = db.scalars(select(Site).where(*where).order_by(Site.name).limit(limit)).all()
    claims = _claims_by_site(db, [s.slug for s in sites])
    result = {"filters": {k: v for k, v in dict(query=query, country=country, culture=culture, kind=kind,
                                                  function=function, period=period).items() if v},
              "total_matches": total, "returned": len(sites), "sites": [_site_row(s, claims[s.slug]) for s in sites]}
    if total == 0:
        result["note"] = "No site in our records matches. Say so; do not suggest sites from general knowledge."
    elif total > len(sites):
        result["note"] = f"Showing {len(sites)} of {total}. Narrow the filters to see the rest."
    return result


def _resolve_site(db: Session, name: str) -> tuple[Site | None, list[Site]]:
    needle = (name or "").strip()
    if not needle:
        return None, []
    exact = db.scalar(select(Site).where(or_(func.lower(Site.slug) == needle.lower(),
                                             func.lower(Site.name) == needle.lower(),
                                             Site.name_he == needle)))
    if exact:
        return exact, []
    matches = db.scalars(select(Site).where(or_(_like(Site.name, needle), _like(Site.name_he, needle),
                                                _like(Site.slug, needle.replace(" ", "-")))).order_by(Site.name).limit(8)).all()
    if len(matches) == 1:
        return matches[0], []
    return None, list(matches)


def site_timeline(db: Session, *, site: str) -> dict:
    found, candidates = _resolve_site(db, site)
    if found is None:
        if candidates:
            return {"found": False, "reason": "ambiguous",
                    "candidates": [{"slug": c.slug, "name": c.name, "country": c.country} for c in candidates],
                    "note": "Ask the user which one they mean."}
        return {"found": False, "reason": "not_in_records",
                "note": "This site is not in our records. Say so; do not describe it from general knowledge."}

    claims = sorted(db.scalars(select(SiteDate).where(SiteDate.site_slug == found.slug)).all(), key=_claim_sort_key)
    cites = db.scalars(select(SiteSource).where(SiteSource.site_slug == found.slug).order_by(SiteSource.id)).all()
    return {
        "found": True,
        "site": {"slug": found.slug, "name": found.name, "name_he": found.name_he, "description": found.description,
                 "kind": found.kind, "function": found.function, "form": found.form, "culture": found.culture,
                 "polity": found.polity, "period": found.period, "country": found.country, "places": found.places,
                 "lat": found.lat, "lon": found.lon, "location_precision": found.location_precision},
        "date_claims": [_claim_view(c) for c in claims],
        "date_claim_count": len(claims),
        "has_alternative_or_disputed": any(c.status in ("alternative", "disputed") for c in claims),
        "citations": [{"source": c.source, "url": c.url, "licence": c.licence} for c in cites[:8]],
        "citation_count": len(cites),
        "note": YEAR_NOTE,
    }


def sites_in_time_window(db: Session, *, year_from: int, year_to: int | None = None, relation: str = "any",
                         country: str | None = None, culture: str | None = None, kind: str | None = None,
                         limit: int = 25) -> dict:
    """Sites that have a dating claim whose possible range touches the window.

    This is not "everything that stood at that time": a site built long before the window with no recorded
    end date is not listed, and the result says so.
    """
    if relation not in {"any", "built", "in_use"}:
        return {"error": f"unknown relation '{relation}'", "allowed": ["any", "built", "in_use"]}
    try:
        a = to_astronomical(int(year_from))
        b = to_astronomical(int(year_to if year_to is not None else year_from))
    except (TypeError, ValueError):
        return {"error": "year_from and year_to must be whole years (BCE negative, e.g. -2500)"}
    if a > b:
        a, b = b, a
    limit = max(1, min(int(limit or 25), 50))

    where = _site_filters(country=country, culture=culture, kind=kind)
    sites = {s.slug: s for s in db.scalars(select(Site).where(*where))}
    roles = BUILT_ROLES if relation == "built" else IN_USE_ROLES if relation == "in_use" else BUILT_ROLES | IN_USE_ROLES
    claims = db.scalars(select(SiteDate).where(SiteDate.site_slug.in_(list(sites)), SiteDate.role.in_(roles))).all() if sites else []

    per_site: dict[str, list[tuple[SiteDate, str]]] = {}
    for c in claims:
        span = _span(c)
        if span is None or span[1] < a or span[0] > b:
            continue
        per_site.setdefault(c.site_slug, []).append((c, "built_or_created" if c.role in BUILT_ROLES else "in_use"))

    rows = []
    for slug, items in per_site.items():
        items.sort(key=lambda x: _claim_sort_key(x[0]))
        c, rel = items[0]
        rows.append({"slug": slug, "name": sites[slug].name, "country": sites[slug].country, "relation": rel,
                     "matched_claim": _claim_view(c), "other_matching_claims": len(items) - 1})
    rows.sort(key=lambda r: (r["matched_claim"]["status"] != "accepted", r["name"]))
    return {
        "window": {"from": fmt_year(a), "to": fmt_year(b)}, "relation": relation,
        "filters": {k: v for k, v in dict(country=country, culture=culture, kind=kind).items() if v},
        "total_matches": len(rows), "returned": min(len(rows), limit), "sites": rows[:limit],
        "note": ("A match means a dating claim's possible range touches the window, not that the site is proven to "
                 "have existed then. Sites built earlier with no recorded end date are not listed. " + YEAR_NOTE),
    }


def _from_astro(a: float) -> int:
    """Astronomical year -> signed calendar year (BCE negative, no year 0)."""
    a = int(a)
    return a - 1 if a <= 0 else a


def _suggest(lo: float, hi: float) -> str | None:
    """Middle of the main range, rounded to 10 years, shown as a calendar year (2600 BCE, not 2601 BCE)."""
    mid = round((_from_astro(lo) + _from_astro(hi)) / 2 / 10) * 10
    return fmt_year(to_astronomical(mid) if mid else 0)


def check_visit(db: Session, *, site: str, year: int) -> dict:
    """Does a requested visit year fit the recorded dating of a site? If not, say how and suggest a valid year.

    The span is built from construction/creation and existence/use/occupation claims. Preferred claims decide
    the main verdict; a year that fits only an alternative or disputed claim is reported as such.
    """
    found, candidates = _resolve_site(db, site)
    if found is None:
        return site_timeline(db, site=site)  # same ambiguous / not_in_records answers
    try:
        astro = to_astronomical(int(year))
    except (TypeError, ValueError):
        return {"error": "year must be a whole year (BCE negative, e.g. -2550)"}
    if int(year) == 0:
        return {"error": "there is no year 0; use -1 for 1 BCE and 1 for 1 CE"}

    claims = [c for c in db.scalars(select(SiteDate).where(SiteDate.site_slug == found.slug,
                                                         SiteDate.role.in_(BUILT_ROLES | IN_USE_ROLES))).all()
              if _span(c) is not None]
    base = {"site": {"slug": found.slug, "name": found.name, "name_he": found.name_he, "country": found.country},
            "requested_year": fmt_year(astro)}
    if not claims:
        return {**base, "verdict": "no_dating_in_records",
                "note": "We hold no usable construction or use dates for this site. Say so; do not guess."}

    preferred = [c for c in claims if c.rank == "preferred"] or [c for c in claims if c.status == "accepted"] or claims
    p_lo = min(_span(c)[0] for c in preferred)
    p_hi = max(_span(c)[1] for c in preferred)
    a_lo = min(_span(c)[0] for c in claims)
    a_hi = max(_span(c)[1] for c in claims)

    result = {**base,
              "main_dating_range": {"from": fmt_year(p_lo), "to": fmt_year(p_hi)},
              "widest_range_any_claim": {"from": fmt_year(a_lo), "to": fmt_year(a_hi)},
              "has_alternative_or_disputed": any(c.status in ("alternative", "disputed") for c in claims)}
    if p_lo <= astro <= p_hi:
        result["verdict"] = "fits"
        result["note"] = "The year falls inside the main recorded range. It may still be during construction; check site_timeline."
    elif a_lo <= astro <= a_hi:
        result["verdict"] = "fits_only_alternative_dating"
        result["suggested_year"] = _suggest(p_lo, p_hi)
        result["note"] = ("The year fits only an alternative or disputed dating, not the main one. Say that, and offer "
                          "the suggested year inside the main range.")
    else:
        too_early = astro < a_lo
        result["verdict"] = "too_early" if too_early else "too_late"
        result["suggested_year"] = _suggest(p_lo, p_hi)
        near = sites_in_time_window(db, year_from=_from_astro(astro), relation="any", limit=3)
        result["other_sites_at_requested_time"] = [
            {"slug": r["slug"], "name": r["name"], "country": r["country"]} for r in near.get("sites", [])]
        result["note"] = ("Tell the user the requested year is outside our records for this site, name the recorded range, "
                          "and offer the suggested year for this site, or one of the other sites listed for the requested time "
                          "(a dating match, not proof it stood then). Do not accept the request as is.")
    return result


def save_trip_request(db: Session, *, site: str, year: int, traveller: str | None = None,
                      logged_by: int | None = None) -> dict:
    """Save a demo trip request after the user agreed to it. The date is validated here, not by the model.

    A request that fits the records is 'confirmed'. One that does not (or an unknown site) is still saved, as
    'needs_a_look', with the verdict. The same user asking for the same site and year twice makes one row.
    """
    check = check_visit(db, site=site, year=year)
    if "error" in check or check.get("found") is False:
        return check
    slug = check["site"]["slug"]
    verdict = check["verdict"]
    status = "confirmed" if verdict == "fits" else "needs_a_look"
    existing = db.scalar(select(TripRequest).where(TripRequest.site_slug == slug, TripRequest.requested_year == int(year),
                                                   TripRequest.logged_by == logged_by))
    if existing is not None:
        return {"saved": False, "reason": "duplicate", **_trip_row(existing)}
    row = TripRequest(site_slug=slug, site_name=check["site"]["name"], requested_year=int(year), verdict=verdict,
                      suggested_year=check.get("suggested_year"), status=status,
                      traveller=(traveller or "").strip()[:80] or None, logged_by=logged_by)
    db.add(row)
    db.commit()
    return {"saved": True, **_trip_row(row),
            "note": "Demo request only: nothing is booked or paid. If status is needs_a_look, say the date did not fit and what was suggested."}


def _trip_row(r: TripRequest) -> dict:
    return {"id": r.id, "site": r.site_name, "requested_year": fmt_year(to_astronomical(r.requested_year)),
            "verdict": r.verdict, "suggested_year": r.suggested_year, "status": r.status, "traveller": r.traveller}


def recent_trip_requests(db: Session, limit: int = 50) -> list[dict]:
    rows = db.scalars(select(TripRequest).order_by(TripRequest.id.desc()).limit(limit)).all()
    return [{**_trip_row(r), "created_at": r.created_at.isoformat()} for r in rows]


def coverage(db: Session) -> dict:
    """What the records cover, for the system prompt."""
    n_sites = db.scalar(select(func.count()).select_from(Site)) or 0
    lo, hi = db.execute(select(func.min(SiteDate.start_earliest), func.max(SiteDate.end_latest))).one()
    return {"sites": n_sites, "from": fmt_year(lo), "to": fmt_year(hi)}
