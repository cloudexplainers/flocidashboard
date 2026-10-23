"""S3 data protection and storage tiers lab: Object Lock, legal holds, storage classes,
Intelligent-Tiering, version lifecycle rules, cross-Region replication, and Requester Pays.

Floci 2.3.0 enforces Object Lock (GOVERNANCE, COMPLIANCE, legal holds) for real. It stores
storage classes, lifecycle rules, Intelligent-Tiering, replication, and Requester Pays
without acting on them, and each step says so.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

from .monolith import AWS_ACCOUNT_ID

LAB_KEY = 'data-protection-tiers'
CACHE_PREFIX = f'floci-lab:s3:{LAB_KEY}:'

VAULT_BUCKET = 'lab-records-vault'
ARCHIVE_BUCKET = 'lab-records-archive'
DR_BUCKET = 'lab-records-dr'
DR_REGION = 'us-west-2'

RECORD_KEY = 'audit/2026-q3-ledger.csv'
RECORD_TEXT = 'entry,amount\n1001,42.00\n1002,17.50\n'
COMPLIANCE_KEY = 'audit/2026-q3-signed-report.txt'
COMPLIANCE_TEXT = 'Q3 2026 audit report, signed and final.\n'
# AWS retention is usually years. The lab keeps it short so Reset can delete the version.
COMPLIANCE_SECONDS = 60
# Reset waits this long at most for a COMPLIANCE retention to run out.
RESET_RETENTION_WAIT_SECONDS = 75
DEEP_ARCHIVE_KEY = 'exports/2019-statements.csv'
DEEP_ARCHIVE_TEXT = 'statement,total\n2019-12,1250.00\n'
TIERING_KEY = 'datasets/clickstream.json'
TIERING_TEXT = '{"page": "/pricing", "visits": 412}\n'

DEFAULT_RETENTION_DAYS = 1
OBJECT_LOCK_CONFIGURATION = {
    'ObjectLockEnabled': 'Enabled',
    'Rule': {'DefaultRetention': {'Mode': 'GOVERNANCE', 'Days': DEFAULT_RETENTION_DAYS}},
}
TIERING_ID = 'ArchiveColdDatasets'
TIERING_CONFIGURATION = {
    'Id': TIERING_ID,
    'Status': 'Enabled',
    'Filter': {'Prefix': 'datasets/'},
    'Tierings': [
        {'Days': 90, 'AccessTier': 'ARCHIVE_ACCESS'},
        {'Days': 180, 'AccessTier': 'DEEP_ARCHIVE_ACCESS'},
    ],
}
LIFECYCLE_RULE_ID = 'TierThenExpireOldVersions'
LIFECYCLE_CONFIGURATION = {
    'Rules': [
        {
            'ID': LIFECYCLE_RULE_ID,
            'Status': 'Enabled',
            'Filter': {'Prefix': ''},
            'NoncurrentVersionTransitions': [{'NoncurrentDays': 30, 'StorageClass': 'GLACIER_IR'}],
            'NoncurrentVersionExpiration': {'NoncurrentDays': 365, 'NewerNoncurrentVersions': 3},
            'Expiration': {'ExpiredObjectDeleteMarker': True},
            'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 7},
        },
    ],
}
REPLICATION_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/lab-s3-replication'
REPLICATION_RULE_ID = 'ReplicateToUsWest2'
REPLICATION_CONFIGURATION = {
    'Role': REPLICATION_ROLE_ARN,
    'Rules': [
        {
            'ID': REPLICATION_RULE_ID,
            'Priority': 1,
            'Status': 'Enabled',
            'Filter': {},
            'DeleteMarkerReplication': {'Status': 'Disabled'},
            'Destination': {'Bucket': f'arn:aws:s3:::{DR_BUCKET}', 'StorageClass': 'STANDARD_IA'},
        },
    ],
}

VERSION_PLACEHOLDER = '<version-id>'
RETAIN_UNTIL_PLACEHOLDER = '<now-plus-60-seconds>'

CREATE_VAULT_COMMAND = f'aws s3api create-bucket --bucket {VAULT_BUCKET} --object-lock-enabled-for-bucket'
DEFAULT_RETENTION_COMMAND = (
    f'aws s3api put-object-lock-configuration --bucket {VAULT_BUCKET} '
    '--object-lock-configuration file://object-lock.json'
)
UPLOAD_RECORD_COMMAND = f'aws s3api put-object --bucket {VAULT_BUCKET} --key {RECORD_KEY} --body ledger.csv'
DELETE_RECORD_COMMAND = (
    f'aws s3api delete-object --bucket {VAULT_BUCKET} --key {RECORD_KEY} --version-id {VERSION_PLACEHOLDER}'
)
LEGAL_HOLD_COMMAND = (
    f'aws s3api put-object-legal-hold --bucket {VAULT_BUCKET} --key {RECORD_KEY} '
    f'--version-id {VERSION_PLACEHOLDER} --legal-hold Status=ON'
)
COMPLIANCE_COMMAND = (
    f'aws s3api put-object --bucket {VAULT_BUCKET} --key {COMPLIANCE_KEY} --body signed-report.txt '
    f'--object-lock-mode COMPLIANCE --object-lock-retain-until-date {RETAIN_UNTIL_PLACEHOLDER}'
)
CREATE_ARCHIVE_COMMAND = (
    f'aws s3api create-bucket --bucket {ARCHIVE_BUCKET}\n'
    f'aws s3api put-bucket-versioning --bucket {ARCHIVE_BUCKET} --versioning-configuration Status=Enabled'
)
DEEP_ARCHIVE_COMMAND = (
    f'aws s3api put-object --bucket {ARCHIVE_BUCKET} --key {DEEP_ARCHIVE_KEY} --body statements.csv '
    '--storage-class DEEP_ARCHIVE'
)
TIERING_COMMAND = (
    f'aws s3api put-object --bucket {ARCHIVE_BUCKET} --key {TIERING_KEY} --body clickstream.json '
    '--storage-class INTELLIGENT_TIERING\n'
    f'aws s3api put-bucket-intelligent-tiering-configuration --bucket {ARCHIVE_BUCKET} --id {TIERING_ID} '
    '--intelligent-tiering-configuration file://intelligent-tiering.json'
)
LIFECYCLE_COMMAND = (
    f'aws s3api put-bucket-lifecycle-configuration --bucket {ARCHIVE_BUCKET} '
    '--lifecycle-configuration file://lifecycle.json'
)
CREATE_DR_COMMAND = (
    f'aws s3api create-bucket --bucket {DR_BUCKET} --region {DR_REGION} '
    f'--create-bucket-configuration LocationConstraint={DR_REGION}\n'
    f'aws s3api put-bucket-versioning --bucket {DR_BUCKET} --region {DR_REGION} '
    '--versioning-configuration Status=Enabled'
)
REPLICATION_COMMAND = (
    f'aws s3api put-bucket-replication --bucket {ARCHIVE_BUCKET} '
    '--replication-configuration file://replication.json'
)
REQUESTER_PAYS_COMMAND = (
    f'aws s3api put-bucket-request-payment --bucket {ARCHIVE_BUCKET} '
    '--request-payment-configuration Payer=Requester'
)

LAB = {
    'service': 's3',
    'key': LAB_KEY,
    'title': 'Protect and tier S3 data with Object Lock, storage classes, lifecycle rules, and replication',
    'description': (
        'Make audit records undeletable with Object Lock (governance mode, a legal hold, and compliance mode), then '
        'keep an archive bucket cheap and recoverable: storage classes chosen by access pattern, Intelligent-Tiering '
        'archive tiers, a lifecycle rule for old versions, cross-Region replication, and Requester Pays.'
    ),
    'steps': [
        {
            'key': 'create-vault-bucket',
            'title': 'Create a bucket with Object Lock enabled',
            'command': CREATE_VAULT_COMMAND,
            'explanation': (
                'Object Lock stores objects with a write-once-read-many (WORM) model: a locked object version cannot '
                'be overwritten or deleted until its retention ends. Object Lock works on object versions, so turning '
                'it on also turns on versioning, and versioning can then never be suspended. You can enable Object Lock '
                'when you create a bucket or later on a versioned bucket, but never turn it off.'
            ),
        },
        {
            'key': 'set-default-retention',
            'title': 'Set a default GOVERNANCE retention for new objects',
            'command': DEFAULT_RETENTION_COMMAND,
            'explanation': (
                'A default retention applies to every new object version that is uploaded without its own settings. '
                'GOVERNANCE mode stops ordinary users from deleting or changing a version, but a user with '
                's3:BypassGovernanceRetention can override it by sending --bypass-governance-retention. Use it to '
                'protect against mistakes while keeping an escape hatch, or to test settings before COMPLIANCE.'
            ),
            'artifact_label': 'object-lock.json',
            'artifact': json.dumps(OBJECT_LOCK_CONFIGURATION, indent=2),
        },
        {
            'key': 'upload-governed-record',
            'title': 'Upload an audit record that inherits the default retention',
            'command': UPLOAD_RECORD_COMMAND,
            'explanation': (
                'The upload carries no lock settings, so S3 applies the bucket default: GOVERNANCE until one day from '
                'now. The step checks the version reports ObjectLockMode and ObjectLockRetainUntilDate.'
            ),
            'artifact_label': 'ledger.csv',
            'artifact': RECORD_TEXT,
        },
        {
            'key': 'delete-governed-version',
            'title': 'Try to delete the locked version',
            'command': DELETE_RECORD_COMMAND,
            'explanation': (
                'Deleting a specific version under GOVERNANCE retention is refused with AccessDenied, even for an '
                'administrator who does not send --bypass-governance-retention. The step passes when the delete is '
                'refused and the version is still there. A delete without --version-id would only add a delete marker '
                'on top: the locked version stays, which is how versioning plus Object Lock protects against '
                'ransomware and accidental deletes.'
            ),
        },
        {
            'key': 'place-legal-hold',
            'title': 'Place a legal hold that even a governance bypass cannot override',
            'command': LEGAL_HOLD_COMMAND,
            'explanation': (
                'A legal hold has no end date: it protects the version until someone with s3:PutObjectLegalHold '
                'removes it, independently of any retention period. Use it for litigation or an investigation. The '
                'step then tries to delete the version with --bypass-governance-retention and passes when the hold '
                'still blocks it.'
            ),
        },
        {
            'key': 'upload-compliance-record',
            'title': 'Upload a record under COMPLIANCE retention',
            'command': COMPLIANCE_COMMAND,
            'explanation': (
                'COMPLIANCE mode is the strictest: until the retain-until date passes, no user can delete the version, '
                'shorten the retention, or switch it to GOVERNANCE, including the account root user. It is what '
                'regulations such as SEC 17a-4 call for. The step tries a delete with --bypass-governance-retention '
                'and passes when it is refused. The lab retains the record for 60 seconds so Reset can clean up; '
                'production retention is usually years. (S3 Glacier Vault Lock is the equivalent for Glacier vaults.)'
            ),
            'artifact_label': 'signed-report.txt',
            'artifact': COMPLIANCE_TEXT,
        },
        {
            'key': 'create-archive-bucket',
            'title': 'Create a versioned archive bucket',
            'command': CREATE_ARCHIVE_COMMAND,
            'explanation': (
                'The rest of the lab is about cost and recovery. Versioning keeps every overwrite and delete '
                'recoverable, and replication requires it on both buckets.'
            ),
        },
        {
            'key': 'upload-deep-archive',
            'title': 'Upload old statements straight to S3 Glacier Deep Archive',
            'command': DEEP_ARCHIVE_COMMAND,
            'explanation': (
                'Pick a storage class by access pattern. Standard: frequent access. Standard-IA: infrequent access, '
                'millisecond reads, a per-GB retrieval fee and a 30-day minimum. One Zone-IA: the same in one '
                'Availability Zone, for data you can re-create. Glacier Instant Retrieval: quarterly access with '
                'millisecond reads. Glacier Flexible Retrieval: minutes to 12 hours. Glacier Deep Archive: the lowest '
                'price, restores in 12 to 48 hours, 180-day minimum, for data kept for compliance and rarely read. '
                'Objects in the two Glacier archive classes must be restored with restore-object before a GET works. '
                'How Floci emulates this: Floci records the storage class but stores every object the same way, so a '
                'GET returns a Deep Archive object at once and nothing is charged.'
            ),
            'artifact_label': 'statements.csv',
            'artifact': DEEP_ARCHIVE_TEXT,
        },
        {
            'key': 'configure-intelligent-tiering',
            'title': 'Store a dataset in Intelligent-Tiering with archive tiers',
            'command': TIERING_COMMAND,
            'explanation': (
                'S3 Intelligent-Tiering is for data with unknown or changing access patterns. For a small monthly '
                'monitoring fee per object, S3 moves each object between Frequent, Infrequent (30 days without access) '
                'and Archive Instant Access (90 days) tiers, with no retrieval fees. The optional configuration adds '
                'the Archive Access and Deep Archive Access tiers for objects under datasets/ that go unread for 90 '
                'and 180 days; those need a restore before reading. '
                'How Floci emulates this: Floci stores the class and the configuration but never moves objects.'
            ),
            'artifact_label': 'intelligent-tiering.json',
            'artifact': json.dumps(TIERING_CONFIGURATION, indent=2),
        },
        {
            'key': 'expire-old-versions',
            'title': 'Tier and expire old versions with a lifecycle rule',
            'command': LIFECYCLE_COMMAND,
            'explanation': (
                'Versioning keeps paying for every old version. This rule moves versions to Glacier Instant Retrieval '
                '30 days after they become noncurrent, deletes them after 365 days while keeping the 3 newest, removes '
                'delete markers that no longer hide anything, and aborts multipart uploads left incomplete for 7 days '
                '(their parts are billed but invisible in listings). Lifecycle rules are the main S3 cost tool on the '
                'exam. How Floci emulates this: Floci stores the rule but does not move or delete anything.'
            ),
            'artifact_label': 'lifecycle.json',
            'artifact': json.dumps(LIFECYCLE_CONFIGURATION, indent=2),
        },
        {
            'key': 'create-replica-bucket',
            'title': f'Create a versioned disaster recovery bucket in {DR_REGION}',
            'command': CREATE_DR_COMMAND,
            'explanation': (
                'Cross-Region Replication (CRR) needs a destination bucket in another Region, with versioning '
                'enabled. CRR serves disaster recovery, compliance rules about distance, and lower latency for users '
                'in another Region; Same-Region Replication (SRR) serves log aggregation and copies between accounts.'
            ),
        },
        {
            'key': 'configure-replication',
            'title': 'Replicate new objects to the other Region',
            'command': REPLICATION_COMMAND,
            'explanation': (
                'The rule replicates every new object version to the disaster recovery bucket and stores the copies in '
                'Standard-IA to save money. Replication is asynchronous; S3 Replication Time Control adds a 15-minute '
                'SLA. Objects that existed before the rule are not copied (use S3 Batch Replication), delete markers '
                'are only copied when DeleteMarkerReplication is on, and version deletes are never replicated. S3 '
                'assumes the IAM role to read the source and write the destination. '
                'How Floci emulates this: Floci stores the rule but copies nothing, and it does not check the role '
                'exists. The step reports what the destination bucket holds.'
            ),
            'artifact_label': 'replication.json',
            'artifact': json.dumps(REPLICATION_CONFIGURATION, indent=2),
        },
        {
            'key': 'enable-requester-pays',
            'title': 'Make requesters pay for downloads from the archive',
            'command': REQUESTER_PAYS_COMMAND,
            'explanation': (
                'With Requester Pays, the account that downloads data pays the request and data transfer charges; the '
                'owner still pays for storage. Requesters must be authenticated and send --request-payer requester, '
                'so anonymous access stops working. It fits large shared datasets. '
                'How Floci emulates this: Floci stores the setting; nothing is billed and the header is not required.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, and results ────────────────────────────────────────────

def _s3(region: str | None = None):
    factory = FlociClientFactory()
    if region:
        factory.region = region
    return factory.client('s3')


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _message(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Message') or _code(exc)


def _marker(step: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    if verified:
        _marker(step)
    clean = _clean_response(response)
    return {
        'service': 's3',
        'lab': LAB_KEY,
        'step': step,
        'command': command,
        'exit_code': 0,
        'stdout': json.dumps(clean, indent=2, default=str),
        'stderr': '',
        'json': clean,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verified,
        'verification': {'status': 'passed' if verified else 'failed', 'message': message},
    }


def _error_response(exc: ClientError) -> dict[str, Any]:
    return {'Error': {'Code': _code(exc), 'Message': _message(exc)}}


def _ignore(call, codes: set[str]) -> Any:
    try:
        return call()
    except ClientError as exc:
        if _code(exc) in codes:
            return None
        raise


MISSING_BUCKET = {'NoSuchBucket'}


def _bucket_exists(bucket: str, region: str | None = None) -> bool:
    try:
        _s3(region).head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        if _code(exc) in {'404', 'NoSuchBucket', 'NotFound'}:
            return False
        raise


def _create_bucket(bucket: str, region: str | None = None, **kwargs) -> Any:
    try:
        return _s3(region).create_bucket(Bucket=bucket, **kwargs)
    except ClientError as exc:
        if _code(exc) in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            return {'Location': f'/{bucket}', 'Note': 'The bucket already exists.'}
        raise


def _versioning(bucket: str, region: str | None = None) -> str | None:
    return _s3(region).get_bucket_versioning(Bucket=bucket).get('Status')


def _versions(bucket: str, key: str) -> list[dict[str, Any]]:
    response = _s3().list_object_versions(Bucket=bucket, Prefix=key)
    return [item for item in response.get('Versions') or [] if item.get('Key') == key]


def _head(bucket: str, key: str, version_id: str | None = None, region: str | None = None) -> dict[str, Any] | None:
    kwargs = {'VersionId': version_id} if version_id else {}
    try:
        return _s3(region).head_object(Bucket=bucket, Key=key, **kwargs)
    except ClientError as exc:
        if _code(exc) in {'404', 'NoSuchKey', 'NoSuchVersion', 'NotFound', 'NoSuchBucket', '405', 'MethodNotAllowed'}:
            return None
        raise


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


# ── Object Lock steps ────────────────────────────────────────────────────────

def _check_vault() -> tuple[bool, str]:
    if not _bucket_exists(VAULT_BUCKET):
        return False, f'Bucket {VAULT_BUCKET} not found.'
    status = _versioning(VAULT_BUCKET)
    lock = _ignore(lambda: _s3().get_object_lock_configuration(Bucket=VAULT_BUCKET),
                   {'ObjectLockConfigurationNotFoundError'}) or {}
    enabled = (lock.get('ObjectLockConfiguration') or {}).get('ObjectLockEnabled')
    if enabled != 'Enabled':
        return False, f'{VAULT_BUCKET} exists but Object Lock is not enabled. Delete it and create it with --object-lock-enabled-for-bucket.'
    if status != 'Enabled':
        return False, f'{VAULT_BUCKET} has Object Lock, but versioning is {status or "off"}.'
    return True, f'{VAULT_BUCKET} has Object Lock enabled, and S3 turned on versioning with it.'


def step_create_vault() -> dict[str, Any]:
    started = time.perf_counter()
    response = _create_bucket(VAULT_BUCKET, ObjectLockEnabledForBucket=True)
    verified, message = _check_vault()
    return _result('create-vault-bucket', CREATE_VAULT_COMMAND, response, verified, message, started)


def _check_default_retention() -> tuple[bool, str]:
    lock = _ignore(lambda: _s3().get_object_lock_configuration(Bucket=VAULT_BUCKET),
                   {'ObjectLockConfigurationNotFoundError', 'NoSuchBucket'}) or {}
    retention = ((lock.get('ObjectLockConfiguration') or {}).get('Rule') or {}).get('DefaultRetention') or {}
    if retention.get('Mode') != 'GOVERNANCE' or retention.get('Days') != DEFAULT_RETENTION_DAYS:
        return False, f'{VAULT_BUCKET} has no default GOVERNANCE retention of {DEFAULT_RETENTION_DAYS} day.'
    return True, f'New versions in {VAULT_BUCKET} get GOVERNANCE retention for {DEFAULT_RETENTION_DAYS} day by default.'


def step_default_retention() -> dict[str, Any]:
    started = time.perf_counter()
    if not _bucket_exists(VAULT_BUCKET):
        return _result('set-default-retention', DEFAULT_RETENTION_COMMAND, {}, False, f'Bucket {VAULT_BUCKET} not found. Create it first.', started)
    response = _s3().put_object_lock_configuration(Bucket=VAULT_BUCKET, ObjectLockConfiguration=OBJECT_LOCK_CONFIGURATION)
    verified, message = _check_default_retention()
    return _result('set-default-retention', DEFAULT_RETENTION_COMMAND, response, verified, message, started)


def _record_version() -> str | None:
    """The oldest version of the governed record: the one the lab locked and holds."""
    if not _bucket_exists(VAULT_BUCKET):
        return None
    versions = _versions(VAULT_BUCKET, RECORD_KEY)
    if not versions:
        return None
    versions.sort(key=lambda item: _as_utc(item.get('LastModified')) or datetime.min.replace(tzinfo=timezone.utc))
    return versions[0].get('VersionId')


def _check_record() -> tuple[bool, str]:
    version_id = _record_version()
    if not version_id:
        return False, f'{RECORD_KEY} not found in {VAULT_BUCKET}.'
    head = _head(VAULT_BUCKET, RECORD_KEY, version_id) or {}
    if head.get('ObjectLockMode') != 'GOVERNANCE' or not head.get('ObjectLockRetainUntilDate'):
        return False, f'Version {version_id} of {RECORD_KEY} has no GOVERNANCE retention. Set the default retention before uploading.'
    return True, (
        f"Version {version_id} of {RECORD_KEY} inherited GOVERNANCE retention until {head['ObjectLockRetainUntilDate']}."
    )


def step_upload_record() -> dict[str, Any]:
    started = time.perf_counter()
    if not _bucket_exists(VAULT_BUCKET):
        return _result('upload-governed-record', UPLOAD_RECORD_COMMAND, {}, False, f'Bucket {VAULT_BUCKET} not found. Create it first.', started)
    version_id = _record_version()
    if version_id:
        response = {'VersionId': version_id, 'Note': 'The record was already uploaded; S3 keeps the locked version.'}
    else:
        response = _s3().put_object(Bucket=VAULT_BUCKET, Key=RECORD_KEY, Body=RECORD_TEXT.encode(), ContentType='text/csv')
    verified, message = _check_record()
    return _result('upload-governed-record', UPLOAD_RECORD_COMMAND, response, verified, message, started)


def _attempt_delete(bucket: str, key: str, version_id: str, bypass: bool) -> tuple[bool, dict[str, Any]]:
    """Try to delete a version. Returns (refused, response)."""
    kwargs = {'BypassGovernanceRetention': True} if bypass else {}
    try:
        response = _s3().delete_object(Bucket=bucket, Key=key, VersionId=version_id, **kwargs)
    except ClientError as exc:
        return _code(exc) == 'AccessDenied', _error_response(exc)
    return False, response


def step_delete_record() -> dict[str, Any]:
    started = time.perf_counter()
    version_id = _record_version()
    command = DELETE_RECORD_COMMAND.replace(VERSION_PLACEHOLDER, version_id or VERSION_PLACEHOLDER)
    if not version_id:
        return _result('delete-governed-version', command, {}, False, f'{RECORD_KEY} not found. Upload it first.', started)
    if not (_head(VAULT_BUCKET, RECORD_KEY, version_id) or {}).get('ObjectLockMode'):
        return _result('delete-governed-version', command, {}, False,
                       f'Version {version_id} is not locked, so the delete would succeed. Upload the record after setting the default retention.', started)
    refused, response = _attempt_delete(VAULT_BUCKET, RECORD_KEY, version_id, bypass=False)
    if refused and _head(VAULT_BUCKET, RECORD_KEY, version_id):
        verified, message = True, f"S3 refused the delete ({response['Error']['Message']}); version {version_id} is still there."
        if _legal_hold(version_id) != 'ON':
            message += ' Only a user allowed s3:BypassGovernanceRetention who sends --bypass-governance-retention could delete it.'
    else:
        verified, message = False, f'The delete was not refused: version {version_id} was removed. Object Lock did not protect it.'
    return _result('delete-governed-version', command, response, verified, message, started)


def _legal_hold(version_id: str) -> str | None:
    hold = _ignore(
        lambda: _s3().get_object_legal_hold(Bucket=VAULT_BUCKET, Key=RECORD_KEY, VersionId=version_id),
        {'NoSuchObjectLockConfiguration', 'NoSuchKey', 'NoSuchVersion', 'InvalidRequest'},
    ) or {}
    return (hold.get('LegalHold') or {}).get('Status')


def step_legal_hold() -> dict[str, Any]:
    started = time.perf_counter()
    version_id = _record_version()
    command = LEGAL_HOLD_COMMAND.replace(VERSION_PLACEHOLDER, version_id or VERSION_PLACEHOLDER)
    if not version_id:
        return _result('place-legal-hold', command, {}, False, f'{RECORD_KEY} not found. Upload it first.', started)
    response = _s3().put_object_legal_hold(Bucket=VAULT_BUCKET, Key=RECORD_KEY, VersionId=version_id, LegalHold={'Status': 'ON'})
    if _legal_hold(version_id) != 'ON':
        return _result('place-legal-hold', command, response, False, f'Version {version_id} does not report a legal hold.', started)
    refused, attempt = _attempt_delete(VAULT_BUCKET, RECORD_KEY, version_id, bypass=True)
    response = {'PutObjectLegalHold': response, 'DeleteWithBypassGovernanceRetention': attempt}
    if refused and _head(VAULT_BUCKET, RECORD_KEY, version_id):
        verified, message = True, (
            f"The legal hold is ON, and a delete with --bypass-governance-retention was refused ({attempt['Error']['Message']})."
        )
    else:
        verified, message = False, f'A delete with --bypass-governance-retention was not refused; version {version_id} is gone.'
    return _result('place-legal-hold', command, response, verified, message, started)


def _compliance_versions() -> list[dict[str, Any]]:
    """Versions of the compliance record with their lock settings, newest first."""
    if not _bucket_exists(VAULT_BUCKET):
        return []
    items = []
    for version in _versions(VAULT_BUCKET, COMPLIANCE_KEY):
        head = _head(VAULT_BUCKET, COMPLIANCE_KEY, version.get('VersionId')) or {}
        items.append({
            'VersionId': version.get('VersionId'),
            'Mode': head.get('ObjectLockMode'),
            'RetainUntil': _as_utc(head.get('ObjectLockRetainUntilDate')),
            'LastModified': _as_utc(version.get('LastModified')),
        })
    items.sort(key=lambda item: item['LastModified'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items


def step_compliance_record() -> dict[str, Any]:
    started = time.perf_counter()
    if not _bucket_exists(VAULT_BUCKET):
        return _result('upload-compliance-record', COMPLIANCE_COMMAND, {}, False, f'Bucket {VAULT_BUCKET} not found. Create it first.', started)
    now = datetime.now(timezone.utc)
    # Reuse a version that is still locked for a while; otherwise upload a new one.
    current = next(
        (item for item in _compliance_versions()
         if item['Mode'] == 'COMPLIANCE' and item['RetainUntil'] and item['RetainUntil'] > now + timedelta(seconds=10)),
        None,
    )
    if current:
        retain_until = current['RetainUntil']
        response = {'VersionId': current['VersionId'], 'Note': 'This version is still under COMPLIANCE retention.'}
    else:
        retain_until = (now + timedelta(seconds=COMPLIANCE_SECONDS)).replace(microsecond=0)
        response = _s3().put_object(
            Bucket=VAULT_BUCKET,
            Key=COMPLIANCE_KEY,
            Body=COMPLIANCE_TEXT.encode(),
            ContentType='text/plain',
            ObjectLockMode='COMPLIANCE',
            ObjectLockRetainUntilDate=retain_until,
        )
    command = COMPLIANCE_COMMAND.replace(RETAIN_UNTIL_PLACEHOLDER, retain_until.strftime('%Y-%m-%dT%H:%M:%SZ'))
    version_id = response.get('VersionId')
    head = _head(VAULT_BUCKET, COMPLIANCE_KEY, version_id) or {}
    if head.get('ObjectLockMode') != 'COMPLIANCE':
        return _result('upload-compliance-record', command, response, False,
                       f'Version {version_id} of {COMPLIANCE_KEY} does not report COMPLIANCE retention.', started)
    refused, attempt = _attempt_delete(VAULT_BUCKET, COMPLIANCE_KEY, version_id, bypass=True)
    response = {'PutObject': response, 'DeleteWithBypassGovernanceRetention': attempt}
    if refused and _head(VAULT_BUCKET, COMPLIANCE_KEY, version_id):
        verified, message = True, (
            f"Version {version_id} is under COMPLIANCE retention until {head.get('ObjectLockRetainUntilDate')}; a delete "
            f"with --bypass-governance-retention was refused ({attempt['Error']['Message']})."
        )
    else:
        verified, message = False, f'A delete with --bypass-governance-retention was not refused; version {version_id} is gone.'
    return _result('upload-compliance-record', command, response, verified, message, started)


# ── Storage tiers, lifecycle, replication, Requester Pays ────────────────────

def _check_archive() -> tuple[bool, str]:
    if not _bucket_exists(ARCHIVE_BUCKET):
        return False, f'Bucket {ARCHIVE_BUCKET} not found.'
    status = _versioning(ARCHIVE_BUCKET)
    if status != 'Enabled':
        return False, f'{ARCHIVE_BUCKET} versioning is {status or "off"}, not Enabled.'
    return True, f'{ARCHIVE_BUCKET} exists with versioning enabled.'


def step_create_archive() -> dict[str, Any]:
    started = time.perf_counter()
    created = _create_bucket(ARCHIVE_BUCKET)
    versioning = _s3().put_bucket_versioning(Bucket=ARCHIVE_BUCKET, VersioningConfiguration={'Status': 'Enabled'})
    verified, message = _check_archive()
    return _result('create-archive-bucket', CREATE_ARCHIVE_COMMAND, {'CreateBucket': created, 'PutBucketVersioning': versioning},
                   verified, message, started)


def _check_storage_class(key: str, expected: str) -> tuple[bool, str]:
    if not _bucket_exists(ARCHIVE_BUCKET):
        return False, f'Bucket {ARCHIVE_BUCKET} not found.'
    head = _head(ARCHIVE_BUCKET, key)
    if not head:
        return False, f'{key} not found in {ARCHIVE_BUCKET}.'
    storage_class = head.get('StorageClass') or 'STANDARD'
    if storage_class != expected:
        return False, f'{key} is stored as {storage_class}, not {expected}.'
    return True, f'{key} is stored as {expected}.'


def step_deep_archive() -> dict[str, Any]:
    started = time.perf_counter()
    if not _bucket_exists(ARCHIVE_BUCKET):
        return _result('upload-deep-archive', DEEP_ARCHIVE_COMMAND, {}, False, f'Bucket {ARCHIVE_BUCKET} not found. Create it first.', started)
    response = _s3().put_object(Bucket=ARCHIVE_BUCKET, Key=DEEP_ARCHIVE_KEY, Body=DEEP_ARCHIVE_TEXT.encode(),
                                ContentType='text/csv', StorageClass='DEEP_ARCHIVE')
    verified, message = _check_storage_class(DEEP_ARCHIVE_KEY, 'DEEP_ARCHIVE')
    if verified:
        try:
            _s3().get_object(Bucket=ARCHIVE_BUCKET, Key=DEEP_ARCHIVE_KEY)['Body'].read()
            message += (' Floci returned it to a GET at once; in AWS the GET answers InvalidObjectState until '
                        'restore-object has made a temporary copy.')
        except ClientError as exc:
            message += f' A GET was refused ({_code(exc)}): it needs restore-object first, as in AWS.'
    return _result('upload-deep-archive', DEEP_ARCHIVE_COMMAND, response, verified, message, started)


def _check_tiering() -> tuple[bool, str]:
    verified, message = _check_storage_class(TIERING_KEY, 'INTELLIGENT_TIERING')
    if not verified:
        return verified, message
    config = _ignore(
        lambda: _s3().get_bucket_intelligent_tiering_configuration(Bucket=ARCHIVE_BUCKET, Id=TIERING_ID),
        {'NoSuchConfiguration', 'NoSuchBucket'},
    ) or {}
    tierings = (config.get('IntelligentTieringConfiguration') or {}).get('Tierings') or []
    found = {(item.get('AccessTier'), item.get('Days')) for item in tierings}
    expected = {(item['AccessTier'], item['Days']) for item in TIERING_CONFIGURATION['Tierings']}
    if found != expected:
        return False, f'{ARCHIVE_BUCKET} has no Intelligent-Tiering configuration {TIERING_ID} with the archive tiers.'
    return True, (
        f'{TIERING_KEY} is in Intelligent-Tiering, and {TIERING_ID} adds Archive Access at 90 days and '
        'Deep Archive Access at 180 days without access.'
    )


def step_tiering() -> dict[str, Any]:
    started = time.perf_counter()
    if not _bucket_exists(ARCHIVE_BUCKET):
        return _result('configure-intelligent-tiering', TIERING_COMMAND, {}, False, f'Bucket {ARCHIVE_BUCKET} not found. Create it first.', started)
    s3 = _s3()
    put = s3.put_object(Bucket=ARCHIVE_BUCKET, Key=TIERING_KEY, Body=TIERING_TEXT.encode(),
                        ContentType='application/json', StorageClass='INTELLIGENT_TIERING')
    config = s3.put_bucket_intelligent_tiering_configuration(
        Bucket=ARCHIVE_BUCKET, Id=TIERING_ID, IntelligentTieringConfiguration=TIERING_CONFIGURATION,
    )
    verified, message = _check_tiering()
    return _result('configure-intelligent-tiering', TIERING_COMMAND,
                   {'PutObject': put, 'PutBucketIntelligentTieringConfiguration': config}, verified, message, started)


def _check_lifecycle() -> tuple[bool, str]:
    config = _ignore(lambda: _s3().get_bucket_lifecycle_configuration(Bucket=ARCHIVE_BUCKET),
                     {'NoSuchLifecycleConfiguration', 'NoSuchBucket'}) or {}
    rule = next((item for item in config.get('Rules') or [] if item.get('ID') == LIFECYCLE_RULE_ID), None)
    if not rule:
        return False, f'{ARCHIVE_BUCKET} has no lifecycle rule {LIFECYCLE_RULE_ID}.'
    expected = LIFECYCLE_CONFIGURATION['Rules'][0]
    problems = []
    if rule.get('Status') != 'Enabled':
        problems.append('the rule is not enabled')
    if [(item.get('NoncurrentDays'), item.get('StorageClass')) for item in rule.get('NoncurrentVersionTransitions') or []] != [(30, 'GLACIER_IR')]:
        problems.append('old versions do not move to GLACIER_IR after 30 days')
    expiration = rule.get('NoncurrentVersionExpiration') or {}
    if expiration.get('NoncurrentDays') != 365 or expiration.get('NewerNoncurrentVersions') != 3:
        problems.append('old versions do not expire after 365 days keeping the 3 newest')
    if (rule.get('Expiration') or {}).get('ExpiredObjectDeleteMarker') is not True:
        problems.append('expired delete markers are not removed')
    if (rule.get('AbortIncompleteMultipartUpload') or {}).get('DaysAfterInitiation') != expected['AbortIncompleteMultipartUpload']['DaysAfterInitiation']:
        problems.append('incomplete multipart uploads are not aborted after 7 days')
    if problems:
        return False, f'{LIFECYCLE_RULE_ID}: ' + '; '.join(problems) + '.'
    return True, (
        f'{LIFECYCLE_RULE_ID} moves old versions to Glacier Instant Retrieval at 30 days, expires them at 365 days '
        'keeping the 3 newest, removes expired delete markers, and aborts incomplete multipart uploads after 7 days.'
    )


def step_lifecycle() -> dict[str, Any]:
    started = time.perf_counter()
    if not _bucket_exists(ARCHIVE_BUCKET):
        return _result('expire-old-versions', LIFECYCLE_COMMAND, {}, False, f'Bucket {ARCHIVE_BUCKET} not found. Create it first.', started)
    response = _s3().put_bucket_lifecycle_configuration(Bucket=ARCHIVE_BUCKET, LifecycleConfiguration=LIFECYCLE_CONFIGURATION)
    verified, message = _check_lifecycle()
    return _result('expire-old-versions', LIFECYCLE_COMMAND, response, verified, message, started)


def _check_dr_bucket() -> tuple[bool, str]:
    if not _bucket_exists(DR_BUCKET, DR_REGION):
        return False, f'Bucket {DR_BUCKET} not found.'
    location = _s3(DR_REGION).get_bucket_location(Bucket=DR_BUCKET).get('LocationConstraint') or 'us-east-1'
    if location != DR_REGION:
        return False, f'{DR_BUCKET} is in {location}, not {DR_REGION}. Delete it and create it again in {DR_REGION}.'
    status = _versioning(DR_BUCKET, DR_REGION)
    if status != 'Enabled':
        return False, f'{DR_BUCKET} versioning is {status or "off"}, not Enabled.'
    return True, f'{DR_BUCKET} is in {DR_REGION} with versioning enabled.'


def step_create_dr() -> dict[str, Any]:
    started = time.perf_counter()
    created = _create_bucket(DR_BUCKET, DR_REGION, CreateBucketConfiguration={'LocationConstraint': DR_REGION})
    versioning = _s3(DR_REGION).put_bucket_versioning(Bucket=DR_BUCKET, VersioningConfiguration={'Status': 'Enabled'})
    verified, message = _check_dr_bucket()
    return _result('create-replica-bucket', CREATE_DR_COMMAND, {'CreateBucket': created, 'PutBucketVersioning': versioning},
                   verified, message, started)


def _check_replication() -> tuple[bool, str]:
    config = _ignore(lambda: _s3().get_bucket_replication(Bucket=ARCHIVE_BUCKET),
                     {'ReplicationConfigurationNotFoundError', 'NoSuchBucket'}) or {}
    replication = config.get('ReplicationConfiguration') or {}
    rule = next((item for item in replication.get('Rules') or [] if item.get('ID') == REPLICATION_RULE_ID), None)
    if not rule:
        return False, f'{ARCHIVE_BUCKET} has no replication rule {REPLICATION_RULE_ID}.'
    destination = rule.get('Destination') or {}
    if rule.get('Status') != 'Enabled' or destination.get('Bucket') != f'arn:aws:s3:::{DR_BUCKET}':
        return False, f'{REPLICATION_RULE_ID} does not replicate to {DR_BUCKET}.'
    if destination.get('StorageClass') != 'STANDARD_IA':
        return False, f'{REPLICATION_RULE_ID} does not store replicas in STANDARD_IA.'
    if replication.get('Role') != REPLICATION_ROLE_ARN:
        return False, f'{REPLICATION_RULE_ID} does not use the role {REPLICATION_ROLE_ARN}.'
    return True, f'{ARCHIVE_BUCKET} replicates new versions to {DR_BUCKET} in {DR_REGION} as STANDARD_IA.'


def step_replication() -> dict[str, Any]:
    started = time.perf_counter()
    archive_ok, archive_message = _check_archive()
    dr_ok, dr_message = _check_dr_bucket() if archive_ok else (False, '')
    if not archive_ok or not dr_ok:
        return _result('configure-replication', REPLICATION_COMMAND, {}, False,
                       (archive_message if not archive_ok else dr_message) + ' Replication needs versioning on both buckets.', started)
    response = _s3().put_bucket_replication(Bucket=ARCHIVE_BUCKET, ReplicationConfiguration=REPLICATION_CONFIGURATION)
    verified, message = _check_replication()
    if verified:
        listed = _s3(DR_REGION).list_objects_v2(Bucket=DR_BUCKET)
        message += (
            f' {DR_BUCKET} holds {listed.get("KeyCount", 0)} objects. In AWS only objects written after the rule '
            'replicate; Floci does not copy objects at all.'
        )
    return _result('configure-replication', REPLICATION_COMMAND, response, verified, message, started)


def _check_requester_pays() -> tuple[bool, str]:
    payer = (_ignore(lambda: _s3().get_bucket_request_payment(Bucket=ARCHIVE_BUCKET), MISSING_BUCKET) or {}).get('Payer')
    if payer != 'Requester':
        return False, f'{ARCHIVE_BUCKET} is paid by {payer or "nobody"}, not the requester.'
    return True, f'Requesters now pay for requests and downloads from {ARCHIVE_BUCKET}; the owner still pays for storage.'


def step_requester_pays() -> dict[str, Any]:
    started = time.perf_counter()
    if not _bucket_exists(ARCHIVE_BUCKET):
        return _result('enable-requester-pays', REQUESTER_PAYS_COMMAND, {}, False, f'Bucket {ARCHIVE_BUCKET} not found. Create it first.', started)
    response = _s3().put_bucket_request_payment(Bucket=ARCHIVE_BUCKET, RequestPaymentConfiguration={'Payer': 'Requester'})
    verified, message = _check_requester_pays()
    return _result('enable-requester-pays', REQUESTER_PAYS_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-vault-bucket': step_create_vault,
    'set-default-retention': step_default_retention,
    'upload-governed-record': step_upload_record,
    'delete-governed-version': step_delete_record,
    'place-legal-hold': step_legal_hold,
    'upload-compliance-record': step_compliance_record,
    'create-archive-bucket': step_create_archive,
    'upload-deep-archive': step_deep_archive,
    'configure-intelligent-tiering': step_tiering,
    'expire-old-versions': step_lifecycle,
    'create-replica-bucket': step_create_dr,
    'configure-replication': step_replication,
    'enable-requester-pays': step_requester_pays,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def status() -> dict[str, Any]:
    """Live state for every step; the two refused deletes also need their marker."""
    done = lambda step: bool(_marked(step))  # noqa: E731
    vault = _safe(lambda: _bucket_exists(VAULT_BUCKET))
    archive = _safe(lambda: _bucket_exists(ARCHIVE_BUCKET))

    def record_held() -> bool:
        version_id = _record_version()
        return bool(version_id) and _legal_hold(version_id) == 'ON'

    checks = {
        'create-vault-bucket': lambda: vault and _check_vault()[0],
        'set-default-retention': lambda: vault and _check_default_retention()[0],
        'upload-governed-record': lambda: vault and _check_record()[0],
        'delete-governed-version': lambda: vault and done('delete-governed-version') and _check_record()[0],
        'place-legal-hold': lambda: vault and record_held(),
        'upload-compliance-record': lambda: vault and done('upload-compliance-record')
        and any(item['Mode'] == 'COMPLIANCE' for item in _compliance_versions()),
        'create-archive-bucket': lambda: archive and _check_archive()[0],
        'upload-deep-archive': lambda: archive and _check_storage_class(DEEP_ARCHIVE_KEY, 'DEEP_ARCHIVE')[0],
        'configure-intelligent-tiering': lambda: archive and _check_tiering()[0],
        'expire-old-versions': lambda: archive and _check_lifecycle()[0],
        'create-replica-bucket': lambda: _check_dr_bucket()[0],
        'configure-replication': lambda: archive and _check_replication()[0],
        'enable-requester-pays': lambda: archive and _check_requester_pays()[0],
    }
    steps = {}
    for step, check in checks.items():
        verified = _safe(check)
        steps[step] = {
            'verified': verified,
            'verification': {'status': 'passed', 'message': 'Verified against Floci.'} if verified else None,
        }
    passed = sum(1 for item in steps.values() if item['verified'])
    complete = passed == len(steps)
    return {
        'service': 's3',
        'lab': LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def _all_versions(s3, bucket: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in s3.get_paginator('list_object_versions').paginate(Bucket=bucket):
        items.extend(
            {'Key': item['Key'], 'VersionId': item['VersionId']}
            for item in [*page.get('Versions', []), *page.get('DeleteMarkers', [])]
            if item.get('Key') and item.get('VersionId')
        )
    return items


def _wait_for_compliance(s3, versions: list[dict[str, Any]]) -> str | None:
    """Release legal holds and wait out short COMPLIANCE retention. Returns an error, or None."""
    latest: datetime | None = None
    for item in versions:
        _ignore(lambda item=item: s3.put_object_legal_hold(Bucket=VAULT_BUCKET, Key=item['Key'], VersionId=item['VersionId'],
                                                           LegalHold={'Status': 'OFF'}),
                {'NoSuchKey', 'NoSuchVersion', 'InvalidRequest', 'MethodNotAllowed', 'NoSuchObjectLockConfiguration'})
        retention = _ignore(lambda item=item: s3.get_object_retention(Bucket=VAULT_BUCKET, Key=item['Key'], VersionId=item['VersionId']),
                            {'NoSuchKey', 'NoSuchVersion', 'InvalidRequest', 'MethodNotAllowed', 'NoSuchObjectLockConfiguration'}) or {}
        retention = retention.get('Retention') or {}
        until = _as_utc(retention.get('RetainUntilDate'))
        if retention.get('Mode') == 'COMPLIANCE' and until and (latest is None or until > latest):
            latest = until
    if latest is None:
        return None
    remaining = (latest - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        return None
    if remaining > RESET_RETENTION_WAIT_SECONDS:
        return (f'{COMPLIANCE_KEY} is under COMPLIANCE retention until {latest.isoformat()}; no one can delete it before '
                'then. Reset again after that time.')
    time.sleep(remaining + 1)
    return None


def reset() -> dict[str, Any]:
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []
    waited = False

    def empty_and_delete(bucket: str, region: str | None = None, before=None) -> None:
        s3 = _s3(region)
        try:
            if not _bucket_exists(bucket, region):
                return
            versions = _all_versions(s3, bucket)
            if before:
                error = before(s3, versions)
                if error:
                    errors.append(f'{bucket}: {error}')
                    return
            failures = 0
            for index in range(0, len(versions), 1000):
                response = s3.delete_objects(Bucket=bucket, Delete={'Objects': versions[index:index + 1000], 'Quiet': True},
                                             BypassGovernanceRetention=True)
                for failure in response.get('Errors') or []:
                    failures += 1
                    errors.append(f"{bucket}/{failure.get('Key')}: {failure.get('Message') or failure.get('Code')}")
            if failures:
                return
            s3.delete_bucket(Bucket=bucket)
            deleted.append(f'bucket:{bucket} ({len(versions)} versions)')
        except ClientError as exc:
            if _code(exc) not in MISSING_BUCKET:
                errors.append(f'{bucket}: {_message(exc)}')
        except BotoCoreError as exc:
            errors.append(f'{bucket}: {exc}')

    def release_vault(s3, versions):
        nonlocal waited
        before = time.perf_counter()
        error = _wait_for_compliance(s3, versions)
        waited = time.perf_counter() - before > 1
        return error

    _ignore(lambda: _s3().delete_bucket_replication(Bucket=ARCHIVE_BUCKET), MISSING_BUCKET)
    empty_and_delete(ARCHIVE_BUCKET)
    empty_and_delete(DR_BUCKET, DR_REGION)
    empty_and_delete(VAULT_BUCKET, before=release_vault)
    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    message = 'S3 data protection lab buckets deleted.'
    if waited:
        message += ' Reset waited for the COMPLIANCE retention to end before deleting the record.'
    return {
        'service': 's3',
        'lab': LAB_KEY,
        'command': (
            f'aws s3api put-object-legal-hold --bucket {VAULT_BUCKET} --key {RECORD_KEY} --version-id <version-id> --legal-hold Status=OFF\n'
            f'aws s3api delete-objects --bucket {VAULT_BUCKET} --delete file://versions.json --bypass-governance-retention\n'
            f'aws s3api delete-bucket --bucket {VAULT_BUCKET} ... # cleanup of all three buckets'
        ),
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'errors': errors,
        'verification': {
            'status': 'failed' if errors else 'passed',
            'message': 'Some lab resources could not be deleted: ' + '; '.join(errors) if errors else message,
        },
    }
