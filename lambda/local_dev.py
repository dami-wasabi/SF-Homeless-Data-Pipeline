"""
lambda/local_dev.py
-------------------
Thin local development server that wraps api_handler.handler so you can
run the API locally against the real CSV files (no AWS needed).

Usage
-----
1.  Run the ETL locally to build a local SQLite-backed "DynamoDB":
        python lambda/local_etl.py

2.  Start this server:
        cd lambda
        pip install uvicorn fastapi
        uvicorn local_dev:app --port 3001 --reload

3.  In another terminal, start the React dev server:
        cd dashboard && npm run start

The Vite proxy forwards /summary, /encounters, /shelters to :3001.

How it bypasses AWS
-------------------
We monkey-patch etl.storage to use a local in-memory store populated from
the real CSV files on startup, so no DynamoDB or AWS credentials are needed.
"""

from __future__ import annotations

import io
import json
import os
import sys

# Make project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Populate local store from the real CSVs ───────────────────────────────────
import etl.storage as storage_module
from etl.transform import run_pipeline

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DEMO_PATH    = os.path.join(DATA_DIR, "SF_HOMELESS_DEMOGRAPHICS.csv")
ANXIETY_PATH = os.path.join(DATA_DIR, "SF_HOMELESS_ANXIETY.csv")

_LOCAL_STORE: list[dict] = []

if os.path.exists(DEMO_PATH) and os.path.exists(ANXIETY_PATH):
    _merged, _report = run_pipeline(DEMO_PATH, ANXIETY_PATH)
    _LOCAL_STORE = [r.to_dict() for r in _merged]
    # Expand Nones for local scan
    _LOCAL_STORE = [{k: v for k, v in r.items() if v is not None} for r in _LOCAL_STORE]
    print(f"[local_dev] Loaded {len(_LOCAL_STORE)} merged records from local CSVs")
else:
    print("[local_dev] WARNING: CSV files not found – store is empty")


# Monkey-patch the storage module so the API handler reads local data
def _local_scan_all(limit=None):
    return _LOCAL_STORE[:limit] if limit else _LOCAL_STORE

def _local_get_hid(hid):
    return [r for r in _LOCAL_STORE if r.get("hid") == hid]

def _local_get_shelter(shelter, from_date=None, to_date=None):
    results = [r for r in _LOCAL_STORE if r.get("shelter") == shelter]
    if from_date:
        results = [r for r in results if r.get("encounter_date", "") >= from_date]
    if to_date:
        results = [r for r in results if r.get("encounter_date", "") <= to_date]
    return results

storage_module.scan_all                = _local_scan_all
storage_module.get_encounters_for_hid     = _local_get_hid
storage_module.get_encounters_for_shelter = _local_get_shelter

# ── FastAPI app ───────────────────────────────────────────────────────────────
import importlib
_api_mod = importlib.import_module("lambda.api_handler")
api_handler = _api_mod.handler   # noqa: E402  (after monkey-patch)

app = FastAPI(title="E84 Pilot – Local Dev API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.api_route("/{path:path}", methods=["GET", "OPTIONS"])
async def proxy(request: Request, path: str):
    """Forward every request to the Lambda handler as if API Gateway called it."""
    event = {
        "rawPath": f"/{path}",
        "queryStringParameters": dict(request.query_params) or None,
        "requestContext": {
            "http": {"method": request.method}
        },
    }
    response = api_handler(event, None)
    body = json.loads(response.get("body", "{}"))
    return JSONResponse(content=body, status_code=response.get("statusCode", 200))
