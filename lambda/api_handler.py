"""
lambda/api_handler.py
---------------------
API Gateway Lambda — serves the React dashboard.

Routes (set in CDK via HttpApi integration)
-------------------------------------------
GET  /encounters                   → all records (full scan, paginated)
GET  /encounters/{hid}             → all encounters for one person
GET  /shelters                     → distinct shelter names
GET  /shelters/{shelter}/encounters → encounters for one shelter + optional date range
GET  /summary                      → aggregate stats for dashboard KPI cards

Query params
  from_date  ISO-8601 date string  e.g. 2019-01-01
  to_date    ISO-8601 date string  e.g. 2019-12-31
  limit      integer               default 100, max 500

All responses include CORS headers so the React app on CloudFront
can call this API without a proxy.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.storage import (
    get_encounters_for_hid,
    get_encounters_for_shelter,
    scan_all,
)

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
logger = logging.getLogger(__name__)

_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type":                 "application/json",
}

_DEFAULT_LIMIT = 100
_MAX_LIMIT     = 500


# ---------------------------------------------------------------------------
# Handler entry point
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    logger.info("API event: method=%s path=%s",
                event.get("requestContext", {}).get("http", {}).get("method"),
                event.get("rawPath"))

    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    # Handle CORS pre-flight
    if method == "OPTIONS":
        return _ok({})

    raw_path   = event.get("rawPath", "/")
    params     = event.get("queryStringParameters") or {}
    path_parts = [p for p in raw_path.split("/") if p]

    try:
        # GET /summary
        if path_parts == ["summary"]:
            return _ok(_build_summary())

        # GET /shelters
        if path_parts == ["shelters"]:
            return _ok(_list_shelters())

        # GET /shelters/{shelter}/encounters
        if len(path_parts) == 3 and path_parts[0] == "shelters" and path_parts[2] == "encounters":
            shelter   = path_parts[1]
            from_date = params.get("from_date")
            to_date   = params.get("to_date")
            items = get_encounters_for_shelter(shelter, from_date, to_date)
            return _ok({"shelter": shelter, "encounters": _clean(items)})

        # GET /encounters/{hid}
        if len(path_parts) == 2 and path_parts[0] == "encounters":
            hid   = path_parts[1]
            items = get_encounters_for_hid(hid)
            return _ok({"hid": hid, "encounters": _clean(items)})

        # GET /encounters
        if path_parts == ["encounters"] or not path_parts:
            limit = min(int(params.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT)
            items = scan_all(limit=limit)
            return _ok({"count": len(items), "encounters": _clean(items)})

        return _error(404, f"Unknown route: {raw_path}")

    except Exception as exc:
        logger.exception("Unhandled error in API handler")
        return _error(500, str(exc))


# ---------------------------------------------------------------------------
# Business logic helpers
# ---------------------------------------------------------------------------

def _build_summary() -> dict:
    """
    Compute KPI stats used by the dashboard cards and charts.
    Operates on a full scan — fine at pilot scale (~hundreds of records).
    """
    all_records = scan_all()

    total_encounters = len(all_records)
    if total_encounters == 0:
        return {"total_encounters": 0, "unique_individuals": 0, "shelters": [],
                "avg_anxiety": None, "anxiety_over_time": [], "by_shelter": []}

    unique_hids     = {r["hid"] for r in all_records}
    anxiety_values  = [int(r["anxiety_level"]) for r in all_records if r.get("anxiety_level") is not None]
    avg_anxiety     = round(sum(anxiety_values) / len(anxiety_values), 2) if anxiety_values else None

    # Encounters per shelter + average anxiety
    shelter_data: dict[str, list] = defaultdict(list)
    for r in all_records:
        shelter = r.get("shelter", "Unknown")
        if r.get("anxiety_level") is not None:
            shelter_data[shelter].append(int(r["anxiety_level"]))

    by_shelter = [
        {
            "shelter": s,
            "encounter_count": len(levels),
            "avg_anxiety": round(sum(levels) / len(levels), 2),
        }
        for s, levels in sorted(shelter_data.items())
    ]

    # Anxiety trend over time — group by month
    monthly: dict[str, list] = defaultdict(list)
    for r in all_records:
        date_str = r.get("encounter_date", "")
        if date_str and r.get("anxiety_level") is not None:
            month = date_str[:7]                        # "2019-05"
            monthly[month].append(int(r["anxiety_level"]))

    anxiety_over_time = [
        {
            "month": m,
            "avg_anxiety": round(sum(v) / len(v), 2),
            "encounter_count": len(v),
        }
        for m, v in sorted(monthly.items())
    ]

    return {
        "total_encounters":    total_encounters,
        "unique_individuals":  len(unique_hids),
        "avg_anxiety":         avg_anxiety,
        "shelters":            list(shelter_data.keys()),
        "by_shelter":          by_shelter,
        "anxiety_over_time":   anxiety_over_time,
    }


def _list_shelters() -> dict:
    items = scan_all()
    shelters = sorted({r.get("shelter", "") for r in items if r.get("shelter")})
    return {"shelters": shelters}


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _ok(body: Any) -> dict:
    return {
        "statusCode": 200,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, default=_json_serial),
    }


def _error(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps({"error": message}),
    }


def _clean(items: list[dict]) -> list[dict]:
    """Recursively convert Decimal → float for JSON serialisation."""
    return [
        {k: (float(v) if isinstance(v, Decimal) else v) for k, v in item.items()}
        for item in items
    ]


def _json_serial(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serialisable")
