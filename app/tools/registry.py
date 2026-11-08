"""Tool definitions the model sees, and the dispatcher that runs them."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from . import sites

TOOLS: list[dict[str, Any]] = [
    {
        "name": "find_sites",
        "description": (
            "Search the curated GeoChronos records of ancient sites, structures and objects. Filter by free text "
            "(name in English or Hebrew, description, place), country, culture or polity, kind (monument, temple, city, "
            "cave...), function (tomb, temple, fortification...) or period. Returns each match with its location and its "
            "best one or two dating claims. Use it to find places; use site_timeline for the full dating of one site. "
            "If nothing matches, the answer is that it is not in our records."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "free text matched against name, Hebrew name, description, place"},
                "country": {"type": "string", "description": "substring of the country name, e.g. Egypt"},
                "culture": {"type": "string", "description": "culture or polity, e.g. Minoan, Nabataean"},
                "kind": {"type": "string", "description": "type of site, e.g. monument, temple, cave, city"},
                "function": {"type": "string", "description": "what it was for, e.g. tomb, temple, fortification"},
                "period": {"type": "string", "description": "named period, e.g. Old Kingdom, Bronze Age"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 15},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "site_timeline",
        "description": (
            "Everything we hold about ONE site: description, location, and ALL of its dating claims, including alternative "
            "and disputed ones, each with its range, dating method, status, confidence and note, plus citations. Always use "
            "it before stating when something was built or used. Pass the site name or slug. If several sites match it "
            "returns candidates to choose from; if none, it says the site is not in our records."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"site": {"type": "string", "description": "site name (English or Hebrew) or slug, e.g. 'Great Pyramid of Giza'"}},
            "required": ["site"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sites_in_time_window",
        "description": (
            "Find sites that have a dating claim whose possible range touches a window of years. Years are ordinary signed "
            "years: BCE is negative with no year 0 (2500 BCE = -2500, 1 CE = 1, 1200 CE = 1200). relation: 'built' = "
            "construction or creation claims, 'in_use' = existence, use or occupation claims, 'any' = both. A match is not "
            "proof the site stood at that time, and sites built earlier with no recorded end are not listed; say so when "
            "you answer. Optionally filter by country, culture or kind."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year_from": {"type": "integer", "description": "start of the window, BCE negative"},
                "year_to": {"type": "integer", "description": "end of the window; omit for a single year"},
                "relation": {"type": "string", "enum": ["any", "built", "in_use"], "default": "any"},
                "country": {"type": "string"},
                "culture": {"type": "string"},
                "kind": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 25},
            },
            "required": ["year_from"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_visit",
        "description": (
            "Validate a requested visit: does the year the user wants fit the recorded dating of ONE site? Call it whenever "
            "the user names both a site and a year (for example 'I want to visit the Great Pyramid around 2550 BC'). Years "
            "are ordinary signed years, BCE negative with no year 0 (2550 BCE = -2550). Returns a verdict: fits, "
            "fits_only_alternative_dating, too_early, too_late or no_dating_in_records, the recorded range, a suggested "
            "year, and for too early/late also other sites dated to the requested time. If the verdict is not 'fits', "
            "point out the mismatch and propose the suggestion; never confirm an invalid trip."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "description": "site name (English or Hebrew) or slug"},
                "year": {"type": "integer", "description": "requested year, BCE negative, e.g. -2550"},
            },
            "required": ["site", "year"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_trip_request",
        "description": (
            "Save a demo trip request, but ONLY after the user has agreed to a specific site and year (after check_visit). "
            "It validates the date itself and stores the request as 'confirmed' if it fits the records, or 'needs_a_look' "
            "if it does not. The same request twice is one row. Nothing is booked or paid. Never call it for a year the "
            "user has not accepted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "site": {"type": "string", "description": "site name or slug"},
                "year": {"type": "integer", "description": "agreed year, BCE negative"},
                "traveller": {"type": "string", "description": "name the user gave for the trip, optional"},
            },
            "required": ["site", "year"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}
MAX_RESULT_CHARS = 12000


def execute(name: str, tool_input: dict[str, Any], db: Session, *, user_id: int | None = None) -> str:
    """Run one tool and return its result as a JSON string for the model."""
    try:
        if name == "find_sites":
            result = sites.find_sites(db, **_clean(tool_input))
        elif name == "site_timeline":
            result = sites.site_timeline(db, site=str(tool_input.get("site", "")))
        elif name == "sites_in_time_window":
            result = sites.sites_in_time_window(db, **_clean(tool_input))
        elif name == "check_visit":
            result = sites.check_visit(db, site=str(tool_input.get("site", "")), year=tool_input.get("year"))
        elif name == "save_trip_request":
            result = sites.save_trip_request(db, site=str(tool_input.get("site", "")), year=tool_input.get("year"),
                                             traveller=tool_input.get("traveller"), logged_by=user_id)
        else:
            result = {"error": f"unknown tool '{name}'"}
    except Exception as exc:  # the loop must survive a bad input; the model sees the error
        result = {"error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + '... (truncated)"}'
    return text


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}
