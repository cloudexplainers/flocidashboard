"""KMS key management lab: a customer managed key with a key policy, automatic and on-demand rotation, a
grant, envelope encryption with a data key and encryption context, and an SSE-KMS bucket default.

Floci 2.3.0 does the cryptography for real: data keys are genuine, the encryption context must match on
decrypt, rotation adds new key material while old ciphertext still decrypts, and a disabled key or a key
pending deletion refuses requests. It stores key policies and grants without using them (KMS calls are
authorized from the caller's IAM policies alone), and S3 records an SSE-KMS default without calling KMS.
Each step checks what Floci enforces live, checks the rest of the AWS rules itself, and says which is which.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

from .monolith import AWS_ACCOUNT_ID

SERVICE = 'kms'
LAB_KEY = 'key-management'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'

ADMIN_ROLE = 'lab-kms-key-admin'
APP_ROLE = 'lab-kms-orders-app'
ROOT_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:root'
ADMIN_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{ADMIN_ROLE}'
APP_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{APP_ROLE}'
ALIAS = 'alias/lab-orders-cmk'
KEY_DESCRIPTION = 'Orders records key (customer managed, KMS key management lab)'
KEY_TAGS = [{'TagKey': 'Lab', 'TagValue': LAB_KEY}, {'TagKey': 'DataClass', 'TagValue': 'confidential'}]
ROTATION_DAYS = 180
GRANT_NAME = 'lab-orders-app-grant'
GRANT_OPERATIONS = ['Encrypt', 'Decrypt', 'GenerateDataKey']
CONTEXT = {'app': 'orders', 'purpose': 'customer-records'}
WRONG_CONTEXT = {'app': 'billing', 'purpose': 'customer-records'}
BUCKET = 'lab-kms-orders-records'
OBJECT_KEY = 'records/2026/order-1001.json'
RECORD = {'order_id': 1001, 'customer': 'A. Rivera', 'card_last4': '4242', 'total': '129.90'}
RECORD_TEXT = json.dumps(RECORD, separators=(',', ':'))

ADMIN_ACTIONS = [
    'kms:Create*', 'kms:Describe*', 'kms:Enable*', 'kms:List*', 'kms:Put*', 'kms:Update*', 'kms:Revoke*',
    'kms:Disable*', 'kms:Get*', 'kms:Delete*', 'kms:TagResource', 'kms:UntagResource',
    'kms:ScheduleKeyDeletion', 'kms:CancelKeyDeletion', 'kms:RotateKeyOnDemand',
]
USER_ACTIONS = ['kms:Encrypt', 'kms:Decrypt', 'kms:ReEncrypt*', 'kms:GenerateDataKey*', 'kms:DescribeKey']
CRYPTO_ACTION_PREFIXES = ('kms:encrypt', 'kms:decrypt', 'kms:reencrypt', 'kms:generatedatakey')
KEY_POLICY = {
    'Version': '2012-10-17',
    'Id': 'lab-orders-key-policy',
    'Statement': [
        {
            'Sid': 'EnableIAMPolicies',
            'Effect': 'Allow',
            'Principal': {'AWS': ROOT_ARN},
            'Action': 'kms:*',
            'Resource': '*',
        },
        {
            'Sid': 'KeyAdministrators',
            'Effect': 'Allow',
            'Principal': {'AWS': ADMIN_ROLE_ARN},
            'Action': ADMIN_ACTIONS,
            'Resource': '*',
        },
        {
            'Sid': 'KeyUsers',
            'Effect': 'Allow',
            'Principal': {'AWS': APP_ROLE_ARN},
            'Action': USER_ACTIONS,
            'Resource': '*',
        },
    ],
}
ADMIN_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'AWS': ROOT_ARN}, 'Action': 'sts:AssumeRole'}],
}
APP_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'ec2.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}

CONTEXT_CLI = ','.join(f'{key}={value}' for key, value in CONTEXT.items())
CREATE_ROLES_COMMAND = (
    f'aws iam create-role --role-name {ADMIN_ROLE} --assume-role-policy-document file://admin-trust.json\n'
    f'aws iam create-role --role-name {APP_ROLE} --assume-role-policy-document file://app-trust.json'
)
CREATE_KEY_COMMAND = (
    f'aws kms create-key --description "{KEY_DESCRIPTION}" --key-usage ENCRYPT_DECRYPT --key-spec SYMMETRIC_DEFAULT '
    '--policy file://key-policy.json --tags TagKey=Lab,TagValue=key-management TagKey=DataClass,TagValue=confidential\n'
    f'aws kms create-alias --alias-name {ALIAS} --target-key-id <key-id>\n'
    f'aws kms get-key-policy --key-id <key-id> --policy-name default --output text'
)
ROTATION_COMMAND = (
    f'aws kms enable-key-rotation --key-id <key-id> --rotation-period-in-days {ROTATION_DAYS}\n'
    'aws kms get-key-rotation-status --key-id <key-id>\n'
    f'aws kms encrypt --key-id {ALIAS} --plaintext fileb://record.json --encryption-context {CONTEXT_CLI} '
    '--query CiphertextBlob --output text | base64 --decode > before-rotation.bin\n'
    'aws kms rotate-key-on-demand --key-id <key-id>\n'
    f'aws kms decrypt --ciphertext-blob fileb://before-rotation.bin --encryption-context {CONTEXT_CLI}'
)
GRANT_COMMAND = (
    f'aws kms create-grant --key-id <key-id> --name {GRANT_NAME} --grantee-principal {APP_ROLE_ARN} '
    f'--retiring-principal {ADMIN_ROLE_ARN} --operations {" ".join(GRANT_OPERATIONS)} '
    f"--constraints 'EncryptionContextSubset={{app=orders,purpose=customer-records}}'\n"
    'aws kms list-grants --key-id <key-id>'
)
ENVELOPE_ENCRYPT_COMMAND = (
    f'aws kms generate-data-key --key-id {ALIAS} --key-spec AES_256 --encryption-context {CONTEXT_CLI} > data-key.json\n'
    '# Encrypt record.json locally with the Plaintext data key (AES-256-GCM, for example through the AWS Encryption SDK),\n'
    '# store the result next to CiphertextBlob (the encrypted data key), then discard the plaintext key.'
)
ENVELOPE_DECRYPT_COMMAND = (
    f'aws kms decrypt --ciphertext-blob fileb://data-key.enc --encryption-context {CONTEXT_CLI} '
    '--query Plaintext --output text\n'
    '# Decrypt record.enc locally with the returned data key, then discard it.\n'
    'aws kms decrypt --ciphertext-blob fileb://data-key.enc --encryption-context app=billing,purpose=customer-records'
    '   # refused: wrong context'
)
SSE_KMS_COMMAND = (
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    f'aws s3api put-bucket-encryption --bucket {BUCKET} --server-side-encryption-configuration '
    "'{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"aws:kms\",\"KMSMasterKeyID\":\"<key-arn>\"},"
    "\"BucketKeyEnabled\":true}]}'\n"
    f'aws s3api put-object --bucket {BUCKET} --key {OBJECT_KEY} --body record.json\n'
    f'aws s3api head-object --bucket {BUCKET} --key {OBJECT_KEY}'
)
DISABLE_DELETE_COMMAND = (
    'aws kms disable-key --key-id <key-id>\n'
    f'aws kms encrypt --key-id {ALIAS} --plaintext fileb://record.json   # refused: DisabledException\n'
    'aws kms enable-key --key-id <key-id>\n'
    'aws kms schedule-key-deletion --key-id <key-id> --pending-window-in-days 30\n'
    f'aws kms encrypt --key-id {ALIAS} --plaintext fileb://record.json   # refused: KMSInvalidStateException\n'
    'aws kms cancel-key-deletion --key-id <key-id>\n'
    'aws kms enable-key --key-id <key-id>   # a cancelled key comes back Disabled'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Manage a customer managed KMS key with a key policy, rotation, grants, and envelope encryption',
    'description': (
        'Create a customer managed key whose key policy separates key administrators from key users, turn on '
        'automatic rotation and rotate once on demand, delegate use to an application role with a constrained grant, '
        'encrypt a record with a data key and an encryption context, make the key the SSE-KMS default for an S3 '
        'bucket, and see a disabled key and a key pending deletion refuse requests.'
    ),
    'steps': [
        {
            'key': 'create-roles',
            'title': 'Create the key administrator and application roles',
            'command': CREATE_ROLES_COMMAND,
            'explanation': (
                f'The key policy in the next step names two IAM roles: {ADMIN_ROLE} manages the key, and {APP_ROLE} '
                '(assumed by EC2 instances) uses it to encrypt and decrypt. AWS checks that every principal in a key '
                'policy exists and refuses the policy with MalformedPolicyDocumentException if one does not, so the '
                'roles come first. How Floci emulates this: Floci accepts a key policy that names a principal that '
                'does not exist.'
            ),
            'artifact_label': 'app-trust.json',
            'artifact': json.dumps(APP_TRUST, indent=2),
        },
        {
            'key': 'create-key-with-policy',
            'title': 'Create a customer managed key with a key policy',
            'command': CREATE_KEY_COMMAND,
            'explanation': (
                'There are three kinds of KMS key. AWS owned keys are invisible to you. AWS managed keys '
                '(alias/aws/s3, alias/aws/rds) are created by a service, rotate every year, and their policy cannot be '
                'changed. Customer managed keys cost $1 a month and you control the key policy, rotation, and deletion; '
                'pick one when the question asks for control, separation of duties, or cross-account use. Every KMS key '
                'has exactly one key policy, and it is the primary control: an IAM policy grants nothing on a key unless '
                'the key policy allows it. The EnableIAMPolicies statement (the account root principal) is what lets '
                'IAM policies work, and it keeps the key manageable; AWS refuses a policy that would lock out the '
                'caller unless you bypass the lockout safety check. KeyAdministrators can manage the key but cannot '
                'encrypt or decrypt with it; KeyUsers can use it but not change it. How Floci emulates this: Floci '
                'stores the key policy but does not evaluate it. It authorizes KMS calls from the caller\'s IAM '
                'policies only, so a Deny in the key policy is not enforced, and it accepts a policy without the root '
                'statement. The step checks the policy itself.'
            ),
            'artifact_label': 'key-policy.json',
            'artifact': json.dumps(KEY_POLICY, indent=2),
        },
        {
            'key': 'rotate-key',
            'title': 'Turn on automatic rotation and rotate once on demand',
            'command': ROTATION_COMMAND,
            'explanation': (
                f'Automatic rotation creates new key material every {ROTATION_DAYS} days here (any period from 90 to '
                '2,560 days; 365 is the default). The key ID, ARN, alias, and policy stay the same, KMS encrypts with '
                'the newest material, and it keeps every older version to decrypt what they protected, so nothing has '
                'to be re-encrypted. Rotate-key-on-demand does the same at once, up to 10 times per key. Rotation '
                'does not protect data already exposed; for that, create a new key and move the alias. Asymmetric '
                'keys, HMAC keys, and keys with imported material cannot rotate automatically: rotate those by '
                'pointing the alias at a new key. How Floci emulates this: on-demand rotation is real. Floci adds new '
                'key material, and ciphertext from before the rotation still decrypts. Automatic rotation is only '
                'recorded: Floci drops the period and never rotates on a schedule. GetKeyRotationStatus does not '
                'return the period or the next rotation date, and ListKeyRotations is unsupported.'
            ),
        },
        {
            'key': 'create-grant',
            'title': 'Delegate key use to the application with a constrained grant',
            'command': GRANT_COMMAND,
            'explanation': (
                'A grant gives a principal permission to use one key for listed operations without editing the key '
                'policy. AWS services use grants under the hood (EBS, RDS, and Secrets Manager create them for you). '
                'This grant only works when the request carries the encryption context app=orders and '
                'purpose=customer-records, and the administrator role can retire it. A new grant can take a few '
                'seconds to take effect; a grant token makes it usable at once. Revoke a grant to take it away '
                'immediately. How Floci emulates this: Floci stores and lists grants, including constraints and the '
                'retiring principal, but does not use them to authorize. A user whose only permission came from a '
                'grant was still refused, because Floci checks IAM policies only.'
            ),
        },
        {
            'key': 'envelope-encrypt',
            'title': 'Encrypt a record with a data key (envelope encryption)',
            'command': ENVELOPE_ENCRYPT_COMMAND,
            'explanation': (
                'KMS Encrypt takes at most 4 KB, and every call is a network request with a quota. Envelope encryption '
                'solves both: GenerateDataKey returns a 256-bit data key twice, in plaintext and encrypted under the KMS '
                'key. You encrypt the data locally with the plaintext key, store the encrypted data key next to the '
                'ciphertext, and discard the plaintext key. Only the small data key ever goes back to KMS. S3 SSE-KMS, '
                'EBS, and the AWS Encryption SDK all work this way; GenerateDataKeyWithoutPlaintext suits a component '
                'that stores keys but does not encrypt. The encryption context is not secret: it is bound to the '
                'ciphertext, and it is written to CloudTrail. How Floci emulates this: Floci returns real data keys. '
                'Python\'s standard library has no AES, so the lab encrypts the record locally with an HMAC-SHA256 '
                'keystream and an authentication tag as a stand-in for AES-256-GCM.'
            ),
            'artifact_label': 'record.json',
            'artifact': json.dumps(RECORD, indent=2),
        },
        {
            'key': 'envelope-decrypt',
            'title': 'Decrypt the data key, and see a wrong encryption context refused',
            'command': ENVELOPE_DECRYPT_COMMAND,
            'explanation': (
                'Decrypt needs no key ID for a symmetric key: the ciphertext says which key and which key material made '
                'it. It does need exactly the same encryption context. A different context fails with '
                'InvalidCiphertextException, so a record copied into another application cannot be decrypted there. '
                'Key policies, IAM policies, and grants can require a context with the kms:EncryptionContext:app '
                'condition key. How Floci emulates this: the context is enforced. A wrong or missing context is '
                'refused just as on AWS.'
            ),
        },
        {
            'key': 'sse-kms-bucket',
            'title': 'Make the key the default SSE-KMS key for an S3 bucket',
            'command': SSE_KMS_COMMAND,
            'explanation': (
                'Since January 2023 every new S3 object is encrypted with SSE-S3 (S3-managed keys) by default. SSE-KMS '
                'adds what SSE-S3 cannot: a key policy you control, a CloudTrail record of every use, and separation of '
                'duties, because reading an object then needs kms:Decrypt as well as s3:GetObject (uploading needs '
                'kms:GenerateDataKey). An S3 Bucket Key makes S3 reuse a bucket-level data key for a while and cuts KMS '
                'requests and their cost by up to 99%. DSSE-KMS adds a second layer for compliance, and SSE-C uses '
                'keys you send with each request. How Floci emulates this: Floci stores the bucket default but S3 '
                'never calls KMS. Objects uploaded without encryption headers are not reported as SSE-KMS, an object '
                'stays readable after its key is disabled, and a reader without kms:Decrypt can download it.'
            ),
        },
        {
            'key': 'disable-and-schedule-deletion',
            'title': 'Disable the key, schedule its deletion, and cancel',
            'command': DISABLE_DELETE_COMMAND,
            'explanation': (
                'Deleting a KMS key is permanent, and everything encrypted under it becomes unreadable, so AWS makes you '
                'schedule deletion with a waiting period of 7 to 30 days (30 by default). While a key is pending '
                'deletion every cryptographic request fails, and a CloudWatch alarm on CloudTrail can catch anything '
                'that still uses it. Disabling the key first is the reversible way to find those users. Cancelling the '
                'deletion leaves the key Disabled; enable it again to use it. How Floci emulates this: both states are '
                'enforced (Encrypt is refused while disabled and while pending deletion), but a cancelled key comes '
                'back Enabled in Floci; the step enables it either way.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _marker(step: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _b64(value: Any) -> str:
    return base64.b64encode(bytes(value or b'')).decode('ascii')


def _result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': SERVICE,
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


# ── Lookups by the lab's fixed names ─────────────────────────────────────────

def _role(name: str) -> dict[str, Any] | None:
    try:
        return _client('iam').get_role(RoleName=name).get('Role')
    except ClientError as exc:
        if _code(exc) in {'NoSuchEntity', 'NoSuchEntityException'}:
            return None
        raise


def _usable(key_id: str) -> dict[str, Any] | None:
    try:
        metadata = _client('kms').describe_key(KeyId=key_id).get('KeyMetadata', {})
    except ClientError:
        return None
    return metadata if metadata.get('KeyId') and metadata.get('KeyState') != 'PendingDeletion' else None


def _key() -> dict[str, Any] | None:
    """The lab key's metadata: the alias target, or a tagged key the alias was lost from. Keys pending deletion
    from an earlier run are ignored, except while the last step has the key itself pending deletion."""
    kms = _client('kms')
    aliases = kms.list_aliases().get('Aliases', [])
    target = next((alias.get('TargetKeyId') for alias in aliases if alias.get('AliasName') == ALIAS), None)
    if target:
        try:
            metadata = kms.describe_key(KeyId=target).get('KeyMetadata', {})
        except ClientError:
            metadata = {}
        if metadata.get('KeyId'):
            return metadata
    wanted = {(tag['TagKey'], tag['TagValue']) for tag in KEY_TAGS}
    for key in kms.list_keys().get('Keys', []):
        metadata = _usable(key.get('KeyId') or '')
        if not metadata or metadata.get('KeyManager') != 'CUSTOMER':
            continue
        try:
            tags = kms.list_resource_tags(KeyId=metadata['KeyId']).get('Tags', [])
        except ClientError:
            continue
        if wanted.issubset({(tag.get('TagKey'), tag.get('TagValue')) for tag in tags}):
            return metadata
    return None


def _need_key() -> tuple[dict[str, Any] | None, str]:
    key = _key()
    return key, '' if key else 'The lab key was not found. Run "Create a customer managed key with a key policy" first.'


def _key_policy(key_id: str) -> dict[str, Any] | None:
    text = _client('kms').get_key_policy(KeyId=key_id, PolicyName='default').get('Policy') or ''
    try:
        policy = json.loads(text)
    except json.JSONDecodeError:
        return None
    return policy if isinstance(policy, dict) else None


def _grant(key_id: str) -> dict[str, Any] | None:
    grants = _client('kms').list_grants(KeyId=key_id).get('Grants', [])
    return next((grant for grant in grants if grant.get('Name') == GRANT_NAME), None)


def _bucket_exists() -> bool:
    try:
        _client('s3').head_bucket(Bucket=BUCKET)
        return True
    except ClientError as exc:
        if _code(exc) in {'404', 'NoSuchBucket', 'NotFound'}:
            return False
        raise


# ── Local encryption with the data key ───────────────────────────────────────
# Python's standard library has no AES. This stand-in keeps the shape of AES-GCM (nonce, ciphertext,
# authentication tag) with HMAC-SHA256: one derived key makes the keystream, another signs the ciphertext.

def _subkey(data_key: bytes, label: bytes) -> bytes:
    return hmac.new(data_key, label, hashlib.sha256).digest()


def _keystream(data_key: bytes, nonce: bytes, length: int) -> bytes:
    stream_key = _subkey(data_key, b'lab-envelope-encrypt')
    blocks = (hmac.new(stream_key, nonce + counter.to_bytes(4, 'big'), hashlib.sha256).digest()
              for counter in range((length + 31) // 32))
    return b''.join(blocks)[:length]


def _tag(data_key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(_subkey(data_key, b'lab-envelope-mac'), nonce + ciphertext, hashlib.sha256).digest()[:16]


def _seal(data_key: bytes, plaintext: bytes) -> dict[str, str]:
    nonce = secrets.token_bytes(12)
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, _keystream(data_key, nonce, len(plaintext))))
    return {'Nonce': _b64(nonce), 'Ciphertext': _b64(ciphertext), 'Tag': _b64(_tag(data_key, nonce, ciphertext))}


def _open(data_key: bytes, sealed: dict[str, str]) -> bytes | None:
    nonce, ciphertext = base64.b64decode(sealed['Nonce']), base64.b64decode(sealed['Ciphertext'])
    if not hmac.compare_digest(_tag(data_key, nonce, ciphertext), base64.b64decode(sealed['Tag'])):
        return None
    return bytes(a ^ b for a, b in zip(ciphertext, _keystream(data_key, nonce, len(ciphertext))))


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_roles() -> tuple[bool, str]:
    missing = [name for name in (ADMIN_ROLE, APP_ROLE) if not _role(name)]
    if missing:
        return False, f"Role {', '.join(missing)} not found."
    return True, f'Roles {ADMIN_ROLE} (key administrator) and {APP_ROLE} (key user, trusted by EC2) exist.'


def _actions(statement: dict[str, Any]) -> list[str]:
    actions = statement.get('Action') or []
    return [actions] if isinstance(actions, str) else list(actions)


def _principals(statement: dict[str, Any]) -> list[str]:
    principal = statement.get('Principal')
    if principal == '*':
        return ['*']
    values = (principal or {}).get('AWS') if isinstance(principal, dict) else None
    return [values] if isinstance(values, str) else list(values or [])


def _allows(policy: dict[str, Any], principal: str) -> list[dict[str, Any]]:
    statements = policy.get('Statement') or []
    if isinstance(statements, dict):
        statements = [statements]
    return [item for item in statements if item.get('Effect') == 'Allow' and principal in _principals(item)]


def _is_crypto(action: str) -> bool:
    lowered = action.lower()
    return lowered in {'kms:*', '*'} or lowered.startswith(CRYPTO_ACTION_PREFIXES)


def _check_policy(policy: dict[str, Any] | None) -> tuple[bool, str]:
    """The rules AWS applies or recommends and Floci does not check."""
    if policy is None:
        return False, 'The key policy is not valid JSON. AWS refuses that; Floci stored it.'
    if not any('kms:*' in _actions(item) for item in _allows(policy, ROOT_ARN)):
        return False, ('The key policy has no statement allowing kms:* to the account root. Without it IAM policies '
                       'grant nothing on the key and the key can become unmanageable; AWS refuses such a policy unless '
                       'the lockout safety check is bypassed. Floci accepted it.')
    admin = [action for item in _allows(policy, ADMIN_ROLE_ARN) for action in _actions(item)]
    if not admin:
        return False, f'The key policy does not name {ADMIN_ROLE} as a key administrator.'
    if crypto := [action for action in admin if _is_crypto(action)]:
        return False, (f"{ADMIN_ROLE} can also use the key ({', '.join(crypto)}). Separate duties: administrators "
                       'manage the key, users encrypt and decrypt.')
    users = {action.lower() for item in _allows(policy, APP_ROLE_ARN) for action in _actions(item)}
    if extra := sorted(action for action in users if action in {'*', 'kms:*', 'kms:putkeypolicy', 'kms:schedulekeydeletion'}):
        return False, f"{APP_ROLE} can also administer the key ({', '.join(extra)})."
    for needed in ('kms:encrypt', 'kms:decrypt', 'kms:generatedatakey*'):
        if needed not in users:
            return False, f'{APP_ROLE} is missing {needed} in the key policy.'
    return True, ''


def _check_key() -> tuple[bool, str]:
    key, error = _need_key()
    if not key:
        return False, error
    if key.get('KeyManager') != 'CUSTOMER' or key.get('KeySpec') != 'SYMMETRIC_DEFAULT' or key.get('KeyUsage') != 'ENCRYPT_DECRYPT':
        return False, 'The lab key must be a customer managed symmetric encrypt/decrypt key.'
    if key.get('KeyState') == 'PendingDeletion':
        return False, 'The lab key is pending deletion. Cancel the deletion or reset the lab.'
    aliases = _client('kms').list_aliases(KeyId=key['KeyId']).get('Aliases', [])
    if not any(alias.get('AliasName') == ALIAS for alias in aliases):
        return False, f'{ALIAS} does not point at the lab key.'
    ok, message = _check_policy(_key_policy(key['KeyId']))
    if not ok:
        return False, message
    return True, (f"Customer managed key {key['KeyId']} ({ALIAS}) has a key policy with the root statement, "
                  f'{ADMIN_ROLE} as administrator without crypto use, and {APP_ROLE} as user without admin rights.')


def _check_rotation() -> tuple[bool, str]:
    key, error = _need_key()
    if not key:
        return False, error
    status = _client('kms').get_key_rotation_status(KeyId=key['KeyId'])
    if not status.get('KeyRotationEnabled'):
        return False, 'Automatic rotation is off for the lab key.'
    period = status.get('RotationPeriodInDays')
    if period and period != ROTATION_DAYS:
        return False, f'The rotation period is {period} days; this lab uses {ROTATION_DAYS}.'
    proof = _marked('rotate-key')
    if not proof or proof.get('KeyId') != key['KeyId']:
        return False, 'Automatic rotation is on, but the on-demand rotation has not been checked yet.'
    return True, ('Automatic rotation is on, the key was rotated on demand, and ciphertext from before the rotation '
                  'still decrypts under the same key ID.')


def _check_grant() -> tuple[bool, str]:
    key, error = _need_key()
    if not key:
        return False, error
    grant = _grant(key['KeyId'])
    if not grant:
        return False, f'Grant {GRANT_NAME} not found on the lab key.'
    if grant.get('GranteePrincipal') != APP_ROLE_ARN:
        return False, f"{GRANT_NAME} is for {grant.get('GranteePrincipal')}, expected {APP_ROLE_ARN}."
    if sorted(grant.get('Operations') or []) != sorted(GRANT_OPERATIONS):
        return False, f"{GRANT_NAME} allows {grant.get('Operations')}; expected {GRANT_OPERATIONS}."
    if (grant.get('Constraints') or {}).get('EncryptionContextSubset') != CONTEXT:
        return False, f'{GRANT_NAME} must require the encryption context {CONTEXT_CLI}.'
    if grant.get('RetiringPrincipal') != ADMIN_ROLE_ARN:
        return False, f'{GRANT_NAME} should name {ADMIN_ROLE} as its retiring principal.'
    return True, (f"Grant {grant.get('GrantId')} lets {APP_ROLE} use {', '.join(GRANT_OPERATIONS)} only with the "
                  f'context {CONTEXT_CLI}, and {ADMIN_ROLE} can retire it. Floci stores it but does not authorize with it.')


def _check_envelope(step: str) -> tuple[bool, str]:
    key, error = _need_key()
    if not key:
        return False, error
    record = _marked(step)
    if not record or record.get('KeyId') != key['Arn']:
        return False, 'Run this step to record its result for the current lab key.'
    if step == 'envelope-encrypt':
        return True, ('The record is encrypted locally with a 256-bit data key; only the encrypted data key '
                      f"({record.get('EncryptedDataKeyBytes')} bytes) is stored with it.")
    return True, ('KMS decrypted the data key with the right context and the record matched; the wrong context was '
                  'refused with InvalidCiphertextException.')


def _bucket_rule() -> dict[str, Any] | None:
    try:
        rules = _client('s3').get_bucket_encryption(Bucket=BUCKET)['ServerSideEncryptionConfiguration'].get('Rules', [])
    except ClientError as exc:
        if _code(exc) in {'ServerSideEncryptionConfigurationNotFoundError', 'NoSuchBucket', '404'}:
            return None
        raise
    return rules[0] if rules else None


def _check_bucket() -> tuple[bool, str]:
    key, error = _need_key()
    if not key:
        return False, error
    if not _bucket_exists():
        return False, f'Bucket {BUCKET} not found.'
    rule = _bucket_rule()
    default = (rule or {}).get('ApplyServerSideEncryptionByDefault') or {}
    if default.get('SSEAlgorithm') != 'aws:kms':
        return False, f'{BUCKET} does not default to SSE-KMS.'
    if default.get('KMSMasterKeyID') not in {key['KeyId'], key['Arn'], ALIAS}:
        return False, f"{BUCKET} defaults to key {default.get('KMSMasterKeyID') or 'alias/aws/s3'}, not the lab key."
    if not rule.get('BucketKeyEnabled'):
        return False, f'Turn on the S3 Bucket Key for {BUCKET} to cut KMS requests.'
    try:
        _client('s3').head_object(Bucket=BUCKET, Key=OBJECT_KEY)
    except ClientError:
        return False, f'Object {OBJECT_KEY} not found in {BUCKET}.'
    return True, f'{BUCKET} defaults to SSE-KMS with the lab key and an S3 Bucket Key, and holds {OBJECT_KEY}.'


def _check_lifecycle() -> tuple[bool, str]:
    key, error = _need_key()
    if not key:
        return False, error
    proof = _marked('disable-and-schedule-deletion')
    if not proof or proof.get('KeyId') != key['KeyId']:
        return False, 'Run this step to see the disabled and pending-deletion states refuse requests.'
    if key.get('KeyState') != 'Enabled':
        return False, f"The lab key is {key.get('KeyState')}; enable it again."
    return True, ('Encrypt was refused while the key was disabled and while it was pending deletion; the deletion '
                  'is cancelled and the key is enabled again.')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_roles() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    roles = {}
    for name, trust, description in (
        (ADMIN_ROLE, ADMIN_TRUST, 'Manages the KMS lab key; cannot encrypt or decrypt with it'),
        (APP_ROLE, APP_TRUST, 'Orders application; encrypts and decrypts with the KMS lab key'),
    ):
        role = _role(name)
        if not role:
            role = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description,
                                   Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])['Role']
        roles[name] = {'RoleName': role.get('RoleName'), 'Arn': role.get('Arn')}
    verified, message = _check_roles()
    return _result('create-roles', CREATE_ROLES_COMMAND, {'Roles': list(roles.values())}, verified, message, started)


def step_create_key() -> dict[str, Any]:
    started = time.perf_counter()
    if not (_role(ADMIN_ROLE) and _role(APP_ROLE)):
        return _result('create-key-with-policy', CREATE_KEY_COMMAND, {}, False,
                       'Create the two roles first: the key policy names them.', started)
    kms = _client('kms')
    key = _key()
    if key and key.get('KeyState') == 'PendingDeletion':
        kms.cancel_key_deletion(KeyId=key['KeyId'])
        key = _key()
    if key and key.get('KeyState') == 'Disabled':
        kms.enable_key(KeyId=key['KeyId'])
    if not key:
        key = kms.create_key(Description=KEY_DESCRIPTION, KeyUsage='ENCRYPT_DECRYPT', KeySpec='SYMMETRIC_DEFAULT',
                             Policy=json.dumps(KEY_POLICY), Tags=KEY_TAGS)['KeyMetadata']
    elif not _check_policy(_key_policy(key['KeyId']))[0]:
        kms.put_key_policy(KeyId=key['KeyId'], PolicyName='default', Policy=json.dumps(KEY_POLICY))
    aliases = kms.list_aliases().get('Aliases', [])
    current = next((alias for alias in aliases if alias.get('AliasName') == ALIAS), None)
    if not current:
        kms.create_alias(AliasName=ALIAS, TargetKeyId=key['KeyId'])
    elif current.get('TargetKeyId') != key['KeyId']:
        kms.update_alias(AliasName=ALIAS, TargetKeyId=key['KeyId'])
    verified, message = _check_key()
    response = {
        'KeyMetadata': _key() or key,
        'AliasName': ALIAS,
        'Policy': _key_policy(key['KeyId']),
    }
    return _result('create-key-with-policy', CREATE_KEY_COMMAND, response, verified, message, started)


def step_rotate_key() -> dict[str, Any]:
    started = time.perf_counter()
    key, error = _need_key()
    if not key:
        return _result('rotate-key', ROTATION_COMMAND, {}, False, error, started)
    kms = _client('kms')
    key_id = key['KeyId']
    kms.enable_key_rotation(KeyId=key_id, RotationPeriodInDays=ROTATION_DAYS)
    status = kms.get_key_rotation_status(KeyId=key_id)
    before = kms.encrypt(KeyId=ALIAS, Plaintext=RECORD_TEXT.encode(), EncryptionContext=CONTEXT)
    rotation_error = None
    try:
        kms.rotate_key_on_demand(KeyId=key_id)
    except ClientError as exc:
        rotation_error = f'{_code(exc)}: {exc}'
    after = kms.decrypt(CiphertextBlob=before['CiphertextBlob'], EncryptionContext=CONTEXT)
    same_key = after.get('KeyId') == key['Arn']
    decrypted = bytes(after.get('Plaintext') or b'').decode('utf-8', 'replace') == RECORD_TEXT
    response = {
        'RotationStatus': {k: v for k, v in status.items() if k != 'ResponseMetadata'},
        'RotateKeyOnDemand': rotation_error or 'accepted',
        'CiphertextBeforeRotation': _b64(before.get('CiphertextBlob'))[:48] + '...',
        'DecryptAfterRotation': {'KeyId': after.get('KeyId'), 'PlaintextMatches': decrypted},
    }
    if rotation_error:
        return _result('rotate-key', ROTATION_COMMAND, response, False,
                       f'Rotate-key-on-demand failed: {rotation_error}', started)
    if not (same_key and decrypted):
        return _result('rotate-key', ROTATION_COMMAND, response, False,
                       'Ciphertext from before the rotation did not decrypt to the record under the same key.', started)
    _marker('rotate-key', {'KeyId': key_id})
    verified, message = _check_rotation()
    if verified and 'RotationPeriodInDays' not in status:
        message += (f' Floci does not report the rotation period (AWS returns RotationPeriodInDays: {ROTATION_DAYS} '
                    'and NextRotationDate).')
    return _result('rotate-key', ROTATION_COMMAND, response, verified, message, started)


def step_create_grant() -> dict[str, Any]:
    started = time.perf_counter()
    key, error = _need_key()
    if not key:
        return _result('create-grant', GRANT_COMMAND, {}, False, error, started)
    kms = _client('kms')
    grant = _grant(key['KeyId'])
    if grant and not _check_grant()[0]:
        kms.revoke_grant(KeyId=key['KeyId'], GrantId=grant['GrantId'])
        grant = None
    created = None
    if not grant:
        created = kms.create_grant(
            KeyId=key['KeyId'], Name=GRANT_NAME, GranteePrincipal=APP_ROLE_ARN, RetiringPrincipal=ADMIN_ROLE_ARN,
            Operations=GRANT_OPERATIONS, Constraints={'EncryptionContextSubset': CONTEXT},
        )
    verified, message = _check_grant()
    response = {'Grant': _grant(key['KeyId'])}
    if created:
        response['GrantToken'] = (created.get('GrantToken') or '')[:12] + '...'
    return _result('create-grant', GRANT_COMMAND, response, verified, message, started)


def step_envelope_encrypt() -> dict[str, Any]:
    started = time.perf_counter()
    key, error = _need_key()
    if not key:
        return _result('envelope-encrypt', ENVELOPE_ENCRYPT_COMMAND, {}, False, error, started)
    data_key = _client('kms').generate_data_key(KeyId=ALIAS, KeySpec='AES_256', EncryptionContext=CONTEXT)
    plaintext_key = bytes(data_key.get('Plaintext') or b'')
    encrypted_key = bytes(data_key.get('CiphertextBlob') or b'')
    if len(plaintext_key) != 32 or not encrypted_key:
        return _result('envelope-encrypt', ENVELOPE_ENCRYPT_COMMAND, {'KeyId': data_key.get('KeyId')}, False,
                       f'GenerateDataKey returned a {len(plaintext_key)}-byte key; AES_256 is 32 bytes.', started)
    envelope = {
        'KeyId': data_key.get('KeyId'),
        'EncryptionContext': CONTEXT,
        'EncryptedDataKey': _b64(encrypted_key),
        'EncryptedDataKeyBytes': len(encrypted_key),
        **_seal(plaintext_key, RECORD_TEXT.encode()),
    }
    del plaintext_key
    _marker('envelope-encrypt', envelope)
    verified, message = _check_envelope('envelope-encrypt')
    shown = {**envelope, 'PlaintextDataKey': 'discarded after encrypting'}
    return _result('envelope-encrypt', ENVELOPE_ENCRYPT_COMMAND, shown, verified, message, started)


def step_envelope_decrypt() -> dict[str, Any]:
    started = time.perf_counter()
    envelope = _marked('envelope-encrypt')
    if not envelope:
        return _result('envelope-decrypt', ENVELOPE_DECRYPT_COMMAND, {}, False,
                       'Run "Encrypt a record with a data key" first.', started)
    kms = _client('kms')
    encrypted_key = base64.b64decode(envelope['EncryptedDataKey'])
    wrong = 'accepted'
    try:
        kms.decrypt(CiphertextBlob=encrypted_key, EncryptionContext=WRONG_CONTEXT)
    except ClientError as exc:
        wrong = _code(exc)
    response = kms.decrypt(CiphertextBlob=encrypted_key, EncryptionContext=CONTEXT)
    opened = _open(bytes(response.get('Plaintext') or b''), envelope)
    record = json.loads(opened) if opened else None
    shown = {
        'KeyId': response.get('KeyId'),
        'DecryptedRecord': record,
        'WrongContext': {'EncryptionContext': WRONG_CONTEXT, 'Result': wrong},
    }
    if record != RECORD:
        return _result('envelope-decrypt', ENVELOPE_DECRYPT_COMMAND, shown, False,
                       'The decrypted data key did not open the record.', started)
    if wrong != 'InvalidCiphertextException':
        return _result('envelope-decrypt', ENVELOPE_DECRYPT_COMMAND, shown, False,
                       f'A wrong encryption context should fail with InvalidCiphertextException; got {wrong}.', started)
    _marker('envelope-decrypt', {'KeyId': response.get('KeyId')})
    verified, message = _check_envelope('envelope-decrypt')
    return _result('envelope-decrypt', ENVELOPE_DECRYPT_COMMAND, shown, verified, message, started)


def step_sse_kms_bucket() -> dict[str, Any]:
    started = time.perf_counter()
    key, error = _need_key()
    if not key:
        return _result('sse-kms-bucket', SSE_KMS_COMMAND, {}, False, error, started)
    s3 = _client('s3')
    if not _bucket_exists():
        s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_encryption(Bucket=BUCKET, ServerSideEncryptionConfiguration={'Rules': [{
        'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'aws:kms', 'KMSMasterKeyID': key['Arn']},
        'BucketKeyEnabled': True,
    }]})
    s3.put_object(Bucket=BUCKET, Key=OBJECT_KEY, Body=RECORD_TEXT.encode(), ContentType='application/json')
    head = s3.head_object(Bucket=BUCKET, Key=OBJECT_KEY)
    response = {
        'ServerSideEncryptionConfiguration': {'Rules': [_bucket_rule()]},
        'HeadObject': {name: head.get(name) for name in ('ServerSideEncryption', 'SSEKMSKeyId', 'BucketKeyEnabled', 'ContentLength')},
    }
    verified, message = _check_bucket()
    if verified and head.get('ServerSideEncryption') != 'aws:kms':
        message += (' Floci did not report the object as SSE-KMS (AWS shows ServerSideEncryption: aws:kms and the key '
                    'ARN on every object uploaded under this default).')
    return _result('sse-kms-bucket', SSE_KMS_COMMAND, response, verified, message, started)


def _refusal(call) -> str:
    try:
        call()
    except ClientError as exc:
        return _code(exc)
    return 'accepted'


def step_disable_and_delete() -> dict[str, Any]:
    started = time.perf_counter()
    key, error = _need_key()
    if not key:
        return _result('disable-and-schedule-deletion', DISABLE_DELETE_COMMAND, {}, False, error, started)
    kms = _client('kms')
    key_id = key['KeyId']

    def encrypt() -> Any:
        return kms.encrypt(KeyId=ALIAS, Plaintext=b'probe')

    if key.get('KeyState') == 'PendingDeletion':
        kms.cancel_key_deletion(KeyId=key_id)
    kms.disable_key(KeyId=key_id)
    disabled = _refusal(encrypt)
    kms.enable_key(KeyId=key_id)
    scheduled = kms.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=30)
    state = (_key() or {}).get('KeyState')
    pending = _refusal(encrypt)
    kms.cancel_key_deletion(KeyId=key_id)
    after_cancel = (_key() or {}).get('KeyState')
    if after_cancel != 'Enabled':
        kms.enable_key(KeyId=key_id)
    response = {
        'Disabled': {'Encrypt': disabled},
        'ScheduledDeletion': {'DeletionDate': scheduled.get('DeletionDate'), 'KeyState': state, 'Encrypt': pending},
        'StateAfterCancel': after_cancel,
        'StateNow': (_key() or {}).get('KeyState'),
    }
    if disabled != 'DisabledException' or pending != 'KMSInvalidStateException' or state != 'PendingDeletion':
        return _result('disable-and-schedule-deletion', DISABLE_DELETE_COMMAND, response, False,
                       'A disabled key and a key pending deletion should both refuse Encrypt.', started)
    _marker('disable-and-schedule-deletion', {'KeyId': key_id})
    verified, message = _check_lifecycle()
    if verified and after_cancel == 'Enabled':
        message += ' Floci put the cancelled key straight back to Enabled; AWS leaves it Disabled until you enable it.'
    return _result('disable-and-schedule-deletion', DISABLE_DELETE_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-roles': step_create_roles,
    'create-key-with-policy': step_create_key,
    'rotate-key': step_rotate_key,
    'create-grant': step_create_grant,
    'envelope-encrypt': step_envelope_encrypt,
    'envelope-decrypt': step_envelope_decrypt,
    'sse-kms-bucket': step_sse_kms_bucket,
    'disable-and-schedule-deletion': step_disable_and_delete,
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
    """Configuration steps are checked live; the crypto proofs also need their recorded result for this key."""
    checks = {
        'create-roles': lambda: _check_roles()[0],
        'create-key-with-policy': lambda: _check_key()[0],
        'rotate-key': lambda: _check_rotation()[0],
        'create-grant': lambda: _check_grant()[0],
        'envelope-encrypt': lambda: _check_envelope('envelope-encrypt')[0],
        'envelope-decrypt': lambda: _check_envelope('envelope-decrypt')[0],
        'sse-kms-bucket': lambda: _check_bucket()[0],
        'disable-and-schedule-deletion': lambda: _check_lifecycle()[0],
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
        'service': SERVICE,
        'lab': LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def reset() -> dict[str, Any]:
    """Empty and delete the bucket, revoke the grant, remove the alias, schedule the key for deletion, delete the roles."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code in {'404', 'KMSInvalidStateException'}:
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    s3 = _client('s3')
    if _safe(_bucket_exists):
        for item in s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
            attempt(f"object:{item['Key']}", lambda item=item: s3.delete_object(Bucket=BUCKET, Key=item['Key']))
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))

    kms = _client('kms')
    key = _key() if _safe(_key) else None
    if key:
        key_id = key['KeyId']
        for grant in kms.list_grants(KeyId=key_id).get('Grants', []):
            attempt(f"grant:{grant.get('Name') or grant['GrantId']}",
                    lambda grant=grant: kms.revoke_grant(KeyId=key_id, GrantId=grant['GrantId']))
    attempt(f'alias:{ALIAS}', lambda: kms.delete_alias(AliasName=ALIAS))
    if key and key.get('KeyState') != 'PendingDeletion':
        attempt(f"key:{key['KeyId']} (pending deletion, 7 days)",
                lambda: kms.schedule_key_deletion(KeyId=key['KeyId'], PendingWindowInDays=7))

    iam = _client('iam')
    for name in (ADMIN_ROLE, APP_ROLE):
        attempt(f'role:{name}', lambda name=name: iam.delete_role(RoleName=name))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws s3 rm s3://{BUCKET} --recursive && aws s3api delete-bucket --bucket {BUCKET}\n'
            'aws kms revoke-grant --key-id <key-id> --grant-id <grant-id>\n'
            f'aws kms delete-alias --alias-name {ALIAS}\n'
            'aws kms schedule-key-deletion --key-id <key-id> --pending-window-in-days 7\n'
            f'aws iam delete-role --role-name {ADMIN_ROLE} && aws iam delete-role --role-name {APP_ROLE}'
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
            'message': 'Some lab resources could not be removed: ' + '; '.join(errors) if errors
            else 'Bucket deleted, grant revoked, alias removed, lab key scheduled for deletion, and roles deleted.',
        },
    }
