"""
etl/s3_sync.py
--------------
Utilities for reading CSVs from S3 and detecting when a source file
has been updated since the last pipeline run.

Design
------
- Uses boto3 – the AWS SDK already available in every Lambda runtime.
- The "last processed" timestamp is persisted in AWS Systems Manager
  Parameter Store so it survives Lambda cold starts and restarts.
- Intentionally has NO side effects at import time; all AWS calls are
  deferred to function bodies so the module is testable without credentials.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# SSM parameter that stores the ISO-8601 timestamp of the last successful
# ETL run for each source dataset.
_SSM_PREFIX = "/e84-pilot/last-processed"


# ---------------------------------------------------------------------------
# Reading CSVs from S3
# ---------------------------------------------------------------------------

def read_csv_from_s3(bucket: str, key: str) -> io.StringIO:
    """
    Download an S3 object and return it as a StringIO ready for csv.DictReader.

    Parameters
    ----------
    bucket : e.g. "my-partner-bucket"
    key    : e.g. "data/SF_HOMELESS_ANXIETY.csv"
    """
    s3 = boto3.client("s3")
    logger.info("Downloading s3://%s/%s", bucket, key)
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read().decode("utf-8-sig")
    return io.StringIO(body)


def get_s3_last_modified(bucket: str, key: str) -> Optional[datetime]:
    """
    Return the LastModified timestamp of an S3 object, or None if it
    doesn't exist (allows the first run to always proceed).
    """
    s3 = boto3.client("s3")
    try:
        meta = s3.head_object(Bucket=bucket, Key=key)
        return meta["LastModified"]     # timezone-aware UTC datetime
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "404":
            logger.warning("s3://%s/%s not found", bucket, key)
            return None
        raise


# ---------------------------------------------------------------------------
# "Has this file changed?" check using SSM Parameter Store
# ---------------------------------------------------------------------------

def get_last_processed_time(dataset_name: str) -> Optional[datetime]:
    """
    Retrieve the timestamp of the last successful ETL run for `dataset_name`.
    Returns None on the very first run (parameter doesn't exist yet).
    """
    ssm = boto3.client("ssm")
    param_name = f"{_SSM_PREFIX}/{dataset_name}"
    try:
        result = ssm.get_parameter(Name=param_name)
        ts_str = result["Parameter"]["Value"]
        return datetime.fromisoformat(ts_str)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            logger.info("No previous run recorded for %s – treating as first run", dataset_name)
            return None
        raise


def set_last_processed_time(dataset_name: str, ts: Optional[datetime] = None) -> None:
    """
    Persist the current UTC time as the last-processed timestamp for
    `dataset_name`.  Pass an explicit `ts` in tests to control the value.
    """
    ssm = boto3.client("ssm")
    param_name = f"{_SSM_PREFIX}/{dataset_name}"
    value = (ts or datetime.now(tz=timezone.utc)).isoformat()
    ssm.put_parameter(
        Name=param_name,
        Value=value,
        Type="String",
        Overwrite=True,
    )
    logger.info("Updated last-processed time for %s → %s", dataset_name, value)


def source_has_changed(bucket: str, key: str, dataset_name: str) -> bool:
    """
    Return True if the S3 object is newer than the last recorded ETL run,
    or if this is the first run.

    This is the gating check used by the scheduled Lambda to avoid
    re-processing unchanged partner data.
    """
    last_run = get_last_processed_time(dataset_name)
    if last_run is None:
        logger.info("First run detected for %s – will process", dataset_name)
        return True

    last_modified = get_s3_last_modified(bucket, key)
    if last_modified is None:
        logger.warning("Cannot determine last-modified for s3://%s/%s", bucket, key)
        return False

    changed = last_modified > last_run
    logger.info(
        "Source check: last_modified=%s  last_run=%s  changed=%s",
        last_modified.isoformat(), last_run.isoformat(), changed,
    )
    return changed
