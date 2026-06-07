"""
lambda/etl_handler.py
---------------------
AWS Lambda entry point for the ETL pipeline.

Trigger sources
---------------
1. S3 Event Notification  – fires when a new CSV is uploaded to the
   internal S3 bucket (demographics or anxiety file updated by the team).

2. EventBridge Scheduler  – fires on a cron schedule (e.g. nightly at
   02:00 UTC) to poll the partner's PUBLIC S3 bucket for updates.

Environment variables (set in CDK stack / SAM template)
---------------------------------------------------------
INTERNAL_BUCKET      – name of the team's own S3 bucket
DEMOGRAPHICS_KEY     – S3 key of the demographics CSV
ANXIETY_KEY          – S3 key of the anxiety CSV
PARTNER_BUCKET       – partner's public S3 bucket name
PARTNER_KEY          – S3 key of the partner dataset
DYNAMODB_TABLE       – name of the DynamoDB table
LOG_LEVEL            – optional, defaults to INFO
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse

# Add the project root to the path so Lambda can find the etl package.
# In the deployment package the etl/ directory sits alongside this file.
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etl.s3_sync import (
    read_csv_from_s3,
    source_has_changed,
    set_last_processed_time,
)
from etl.transform import parse_demographics, parse_anxiety, merge
from etl.storage import upsert_records

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
INTERNAL_BUCKET  = os.environ.get("INTERNAL_BUCKET", "e84-pilot-data")
DEMOGRAPHICS_KEY = os.environ.get("DEMOGRAPHICS_KEY", "raw/SF_HOMELESS_DEMOGRAPHICS.csv")
ANXIETY_KEY      = os.environ.get("ANXIETY_KEY",      "raw/SF_HOMELESS_ANXIETY.csv")
PARTNER_BUCKET   = os.environ.get("PARTNER_BUCKET",   "partner-public-bucket")
PARTNER_KEY      = os.environ.get("PARTNER_KEY",      "data/partner_dataset.csv")
DYNAMODB_TABLE   = os.environ.get("DYNAMODB_TABLE",   "e84-pilot-encounters")


# ---------------------------------------------------------------------------
# Core pipeline runner
# ---------------------------------------------------------------------------

def run_etl(bucket: str, demographics_key: str, anxiety_key: str) -> dict:
    """
    Download both CSVs from S3, run the transform, write to DynamoDB.
    Returns a summary dict suitable for logging or the Lambda response body.
    """
    logger.info("Starting ETL: bucket=%s demo=%s anxiety=%s",
                bucket, demographics_key, anxiety_key)

    demo_stream    = read_csv_from_s3(bucket, demographics_key)
    anxiety_stream = read_csv_from_s3(bucket, anxiety_key)

    demo_records      = parse_demographics(demo_stream)
    encounter_records = parse_anxiety(anxiety_stream)

    merged, report = merge(demo_records, encounter_records)

    serialised = [r.to_dict() for r in merged]
    written    = upsert_records(serialised)

    return {**report, "dynamodb_rows_written": written}


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    """
    Unified Lambda handler.

    • If `event` contains Records[].s3  → triggered by an S3 Put event.
      We infer which file changed and re-run the full pipeline.

    • If `event` contains source == "aws.scheduler"  → triggered by
      EventBridge Scheduler (nightly partner-bucket poll).
      We check if the partner file has changed before running.

    • Any other event shape → manual invocation; run pipeline unconditionally.
    """
    logger.info("Event received: %s", json.dumps(event, default=str))

    # ------------------------------------------------------------------
    # Path 1: S3 event – one of our own files was updated
    # ------------------------------------------------------------------
    records = event.get("Records", [])
    if records and "s3" in records[0]:
        bucket  = records[0]["s3"]["bucket"]["name"]
        key     = urllib.parse.unquote_plus(records[0]["s3"]["object"]["key"])
        logger.info("S3 trigger: s3://%s/%s", bucket, key)

        # Regardless of WHICH file changed, re-run the full pipeline so
        # the merged output always reflects both files' current state.
        result = run_etl(bucket, DEMOGRAPHICS_KEY, ANXIETY_KEY)
        set_last_processed_time("internal-dataset")

        return _response(200, result)

    # ------------------------------------------------------------------
    # Path 2: EventBridge Scheduler – check partner bucket for updates
    # ------------------------------------------------------------------
    if event.get("source") == "aws.scheduler" or event.get("detail-type") == "Scheduled Event":
        logger.info("Scheduler trigger: checking partner bucket for changes")

        if not source_has_changed(PARTNER_BUCKET, PARTNER_KEY, "partner-dataset"):
            logger.info("Partner dataset unchanged – skipping ETL")
            return _response(200, {"status": "skipped", "reason": "no_changes_detected"})

        logger.info("Partner dataset has changed – running ETL")
        # For the partner dataset we read it alongside the internal CSVs.
        # Adjust the keys as needed once the real partner schema is known.
        result = run_etl(INTERNAL_BUCKET, DEMOGRAPHICS_KEY, ANXIETY_KEY)
        set_last_processed_time("partner-dataset")

        return _response(200, {**result, "partner_sync": True})

    # ------------------------------------------------------------------
    # Path 3: Manual / unknown invocation
    # ------------------------------------------------------------------
    logger.info("Manual invocation – running pipeline unconditionally")
    result = run_etl(INTERNAL_BUCKET, DEMOGRAPHICS_KEY, ANXIETY_KEY)
    set_last_processed_time("internal-dataset")
    return _response(200, result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _response(status: int, body: dict) -> dict:
    logger.info("Pipeline result: %s", json.dumps(body, default=str))
    return {"statusCode": status, "body": json.dumps(body, default=str)}
