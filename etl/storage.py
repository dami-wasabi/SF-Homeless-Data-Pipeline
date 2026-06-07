"""
etl/storage.py
--------------
DynamoDB persistence layer for the merged homeless-encounter dataset.

Table design
------------
Table name  : e84-pilot-encounters   (set via DYNAMODB_TABLE env var)
Partition key : hid          (string  – "001-15")
Sort key      : encounter_date (string – ISO-8601 "2019-05-01")

This PK+SK combination is unique per person per encounter day, which
matches the business semantics.

Access patterns supported out of the box:
  • Get all encounters for one person    → Query(PK=hid)
  • Get all encounters for a shelter     → GSI shelter-date-index
  • Get all records (full scan for dashboard aggregate queries)
  • Scan with filter on anxiety_level, gender, race

GSI (Global Secondary Index) added in the CDK stack:
  shelter-date-index
    PK: shelter   SK: encounter_date
  – lets the dashboard query "all encounters at shelter X between date A and B"
    without a full table scan.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key, ConditionBase  # noqa: F401


logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "e84-pilot-encounters")


def _table():
    dynamo = boto3.resource("dynamodb")
    return dynamo.Table(TABLE_NAME)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_records(records: list[dict]) -> int:
    """
    Batch-write a list of serialised MergedRecord dicts to DynamoDB.
    Uses batch_writer for efficiency (25 items per request automatically).

    Returns the number of records written.
    """
    table = _table()
    written = 0
    with table.batch_writer() as batch:
        for rec in records:
            # DynamoDB requires non-empty string values; replace empty with None
            item = {k: (v if v not in ("", None) else None) for k, v in rec.items()}
            # Remove None values – DynamoDB doesn't store nulls in put_item
            item = {k: v for k, v in item.items() if v is not None}
            batch.put_item(Item=item)
            written += 1
    logger.info("Upserted %d records into %s", written, TABLE_NAME)
    return written


# ---------------------------------------------------------------------------
# Read helpers (used by the API Lambda)
# ---------------------------------------------------------------------------

def get_encounters_for_hid(hid: str) -> list[dict]:
    """All encounters for a single person, sorted by encounter_date."""
    table = _table()
    response = table.query(
        KeyConditionExpression=Key("hid").eq(hid)
    )
    items = response.get("Items", [])
    return sorted(items, key=lambda x: x.get("encounter_date", ""))


def get_encounters_for_shelter(
    shelter: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> list[dict]:
    """
    All encounters at a shelter, optionally bounded by ISO date strings.
    Uses the shelter-date-index GSI.
    """
    from boto3.dynamodb.conditions import ConditionBase
    table = _table()
    key_expr: ConditionBase = Key("shelter").eq(shelter)
    if from_date and to_date:
        key_expr = key_expr & Key("encounter_date").between(from_date, to_date)
    elif from_date:
        key_expr = key_expr & Key("encounter_date").gte(from_date)

    response = table.query(
        IndexName="shelter-date-index",
        KeyConditionExpression=key_expr,
    )
    return response.get("Items", [])
    """
    All encounters at a shelter, optionally bounded by ISO date strings.
    Uses the shelter-date-index GSI.
    """
    
    table = _table()
    key_expr: ConditionBase = Key("shelter").eq(shelter)
    if from_date and to_date:
        key_expr = key_expr & Key("encounter_date").between(from_date, to_date)
    elif from_date:
        key_expr = key_expr & Key("encounter_date").gte(from_date)

    response = table.query(
        IndexName="shelter-date-index",
        KeyConditionExpression=key_expr,
    )
    return response.get("Items", [])
    """
    All encounters at a shelter, optionally bounded by ISO date strings.
    Uses the shelter-date-index GSI.
    """
    
    table = _table()
    key_expr: ConditionBase = Key("shelter").eq(shelter)
    if from_date and to_date:
        key_expr = key_expr & Key("encounter_date").between(from_date, to_date)
    elif from_date:
        key_expr = key_expr & Key("encounter_date").gte(from_date)

    response = table.query(
        IndexName="shelter-date-index",
        KeyConditionExpression=key_expr,
    )
    return response.get("Items", [])
    """
    All encounters at a shelter, optionally bounded by ISO date strings.
    Uses the shelter-date-index GSI.
    """
    
    table = _table()
    key_expr: ConditionBase = Key("shelter").eq(shelter)
    if from_date and to_date:
        key_expr = key_expr & Key("encounter_date").between(from_date, to_date)
    elif from_date:
        key_expr = key_expr & Key("encounter_date").gte(from_date)

    response = table.query(
        IndexName="shelter-date-index",
        KeyConditionExpression=key_expr,
    )
    return response.get("Items", [])
    """
    All encounters at a shelter, optionally bounded by ISO date strings.
    Uses the shelter-date-index GSI.
    """
    table = _table()
    
    key_expr: ConditionBase = Key("shelter").eq(shelter)
    if from_date and to_date:
        key_expr = key_expr & Key("encounter_date").between(from_date, to_date)
    elif from_date:
        key_expr = key_expr & Key("encounter_date").gte(from_date)

    response = table.query(
        IndexName="shelter-date-index",
        KeyConditionExpression=key_expr,
    )
    return response.get("Items", [])


def scan_all(limit: Optional[int] = None) -> list[dict]:
    """
    Full table scan – used for dashboard aggregate queries.
    For pilot data volumes this is fine; at scale, replace with
    pre-aggregated summary records or an Athena query on S3.
    """
    table = _table()
    kwargs: dict = {}
    if limit:
        kwargs["Limit"] = limit

    items: list[dict] = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    logger.info("Scanned %d total records from %s", len(items), TABLE_NAME)
    return items
