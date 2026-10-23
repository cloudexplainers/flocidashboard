"""Read the CloudTrail log files a trail delivered to its S3 bucket.

CloudTrail (and Floci) writes gzipped JSON files at
``<prefix>/AWSLogs/<account>/CloudTrail/<region>/yyyy/MM/dd/<file>.json.gz``.
Keys sort oldest first, so to list newest first we walk the date folders
(listed with a delimiter) from the latest day back.
"""

from __future__ import annotations

import json
import re
import zlib
from typing import Any

from botocore.exceptions import ClientError

from . import aws as core
from .aws import FlociClientFactory

PAGE_SIZE = 100                       # files returned per request (whole days, so it can be more)
MAX_FILES_PER_DAY = 2000              # per account, Region and day
MAX_COMPRESSED_BYTES = 5 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 25 * 1024 * 1024
_DAY = re.compile(r'^\d{4}/\d{2}/\d{2}$')


def _trail_not_found(name: str) -> ClientError:
    return ClientError({
        'Error': {'Code': 'TrailNotFoundException', 'Message': f'Trail {name} was not found in the active account and Region.'},
        'ResponseMetadata': {'HTTPStatusCode': 404},
    }, 'DescribeTrails')


def _cloudtrail():
    return FlociClientFactory().client('cloudtrail')


def _s3():
    return FlociClientFactory().client('s3')


def trail_location(name: str) -> dict[str, str]:
    """The bucket and the AWSLogs/ prefix a trail delivers to."""
    clean = str(name or '').strip()
    if not clean:
        raise ValueError('Trail name is required')
    trails = _cloudtrail().describe_trails(trailNameList=[clean], includeShadowTrails=False).get('trailList', [])
    trail = next((item for item in trails if clean in (item.get('Name'), item.get('TrailARN'))), None)
    if not trail:
        raise _trail_not_found(clean)
    bucket = trail.get('S3BucketName')
    if not bucket:
        raise ValueError(f'Trail {clean} has no S3 bucket configured')
    prefix = trail.get('S3KeyPrefix') or ''
    if prefix and not prefix.endswith('/'):
        prefix += '/'
    return {'trail': trail.get('Name') or clean, 'bucket': bucket, 'prefix': prefix, 'log_prefix': f'{prefix}AWSLogs/'}


def _folders(s3, bucket: str, prefix: str) -> list[str]:
    """Child "folders" (common prefixes) directly under prefix."""
    folders: list[str] = []
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix, Delimiter='/'):
        folders.extend(item['Prefix'] for item in page.get('CommonPrefixes', []) if item.get('Prefix'))
    return folders


def _day_folders(s3, bucket: str, log_prefix: str) -> dict[str, list[str]]:
    """Map 'yyyy/MM/dd' to the day folders (one per account and Region) that hold it."""
    days: dict[str, list[str]] = {}
    for account in _folders(s3, bucket, log_prefix):
        for region in _folders(s3, bucket, f'{account}CloudTrail/'):
            for year in _folders(s3, bucket, region):
                for month in _folders(s3, bucket, year):
                    for day in _folders(s3, bucket, month):
                        date = day[len(region):].strip('/')
                        if _DAY.match(date):
                            days.setdefault(date, []).append(day)
    return days


def _day_files(s3, bucket: str, folder: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {'Bucket': bucket, 'Prefix': folder}
    while True:
        page = s3.list_objects_v2(**kwargs)
        files.extend(item for item in page.get('Contents', []) if item.get('Key', '').endswith('.json.gz'))
        if len(files) >= MAX_FILES_PER_DAY and page.get('IsTruncated'):
            core.record_truncation('ListObjectsV2', f'log files in {folder} (more exist)', MAX_FILES_PER_DAY, MAX_FILES_PER_DAY + 1)
            return files[:MAX_FILES_PER_DAY]
        if not page.get('IsTruncated') or not page.get('NextContinuationToken'):
            return files
        kwargs['ContinuationToken'] = page['NextContinuationToken']


def _file_entry(item: dict[str, Any], log_prefix: str) -> dict[str, Any]:
    key = item['Key']
    # <account>/CloudTrail/<region>/yyyy/MM/dd/<file>
    parts = key[len(log_prefix):].split('/')
    modified = item.get('LastModified')
    return {
        'key': key,
        'name': parts[-1],
        'account': parts[0] if len(parts) > 2 else None,
        'region': parts[2] if len(parts) > 3 else None,
        'size': item.get('Size'),
        'last_modified': modified.isoformat() if hasattr(modified, 'isoformat') else modified,
    }


def list_log_files(name: str, *, before: str = '') -> dict[str, Any]:
    """Newest-first page of log files, whole days at a time; next_cursor is the day to continue before."""
    if before and not _DAY.match(before):
        raise ValueError('before must be a date in the form yyyy/MM/dd')
    location = trail_location(name)
    s3 = _s3()
    days = _day_folders(s3, location['bucket'], location['log_prefix'])
    ordered = sorted((day for day in days if not before or day < before), reverse=True)
    files: list[dict[str, Any]] = []
    next_cursor = None
    for index, day in enumerate(ordered):
        for folder in days[day]:
            files.extend(_file_entry(item, location['log_prefix']) for item in _day_files(s3, location['bucket'], folder))
        if len(files) >= PAGE_SIZE and index + 1 < len(ordered):
            next_cursor = day
            break
    files.sort(key=lambda item: (str(item.get('last_modified') or ''), item['key']), reverse=True)
    return {**location, 'files': files, 'next_cursor': next_cursor}


def read_log_file(name: str, key: str) -> dict[str, Any]:
    """Gunzip one delivered file and return its Records."""
    location = trail_location(name)
    clean_key = str(key or '')
    if not clean_key.startswith(location['log_prefix']) or '/CloudTrail/' not in clean_key or not clean_key.endswith('.json.gz'):
        raise ValueError(f"Key must be a CloudTrail log file (.json.gz) under {location['log_prefix']} in {location['bucket']}")
    response = _s3().get_object(Bucket=location['bucket'], Key=clean_key)
    body = response['Body']
    try:
        size = response.get('ContentLength')
        if isinstance(size, int) and size > MAX_COMPRESSED_BYTES:
            raise ValueError(f'Log file is {size} bytes; the viewer reads files up to {MAX_COMPRESSED_BYTES} bytes')
        raw = body.read(MAX_COMPRESSED_BYTES + 1)
    finally:
        body.close()
    if len(raw) > MAX_COMPRESSED_BYTES:
        raise ValueError(f'Log file is larger than {MAX_COMPRESSED_BYTES} bytes; the viewer reads files up to that size')
    if raw[:2] != b'\x1f\x8b':
        raise ValueError('Log file is not gzip-compressed')
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        text = decompressor.decompress(raw, MAX_DECOMPRESSED_BYTES + 1)
    except zlib.error as exc:
        raise ValueError(f'Log file could not be decompressed: {exc}') from exc
    if len(text) > MAX_DECOMPRESSED_BYTES or decompressor.unconsumed_tail:
        raise ValueError(f'Log file expands to more than {MAX_DECOMPRESSED_BYTES} bytes; the viewer stops there')
    try:
        document = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Log file is not valid JSON: {exc}') from exc
    records = document.get('Records') if isinstance(document, dict) else None
    if not isinstance(records, list):
        raise ValueError('Log file has no Records list')
    return {**location, 'key': clean_key, 'size': len(raw), 'records': records}
