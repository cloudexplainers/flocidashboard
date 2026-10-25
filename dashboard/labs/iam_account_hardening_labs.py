"""Account hardening lab: a strong password policy, a virtual MFA device enabled with real one-time codes, a policy
that denies everything without MFA, a permissions boundary on a delegated developer, an IAM Access Analyzer with an
external-access review, a credential report audit, and a root user check.

Floci 2.3.0 enforces the password policy when a login profile is created, checks the two TOTP codes when an MFA
device is enabled, denies requests from long-term keys under a "deny without MFA" policy, and enforces permissions
boundaries. It never marks a GetSessionToken session as MFA-authenticated (and does not check the token code there),
so an MFA session is denied too. Access Analyzer creates analyzers but its findings API is misrouted, so the lab
reviews the resource policies itself. The credential report is cached for four hours, like AWS, so the lab rebuilds
missing rows from the IAM APIs.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import struct
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'iam'
LAB_KEY = 'account-hardening'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
ACCOUNT = '000000000000'
EXTERNAL_ACCOUNT = '111122223333'

PASSWORD_POLICY = {
    'MinimumPasswordLength': 14,
    'RequireSymbols': True,
    'RequireNumbers': True,
    'RequireUppercaseCharacters': True,
    'RequireLowercaseCharacters': True,
    'AllowUsersToChangePassword': True,
    'MaxPasswordAge': 90,
    'PasswordReusePrevention': 24,
    'HardExpiry': False,
}
WEAK_PASSWORD = 'summer2026'
STRONG_PASSWORD = 'Lab-Hardening#2026-Strong!'

ADMIN_USER = 'lab-hardening-admin'
ADMIN_GROUP = 'lab-hardening-admins'
MFA_DEVICE = 'lab-hardening-admin-mfa'
MFA_SERIAL = f'arn:aws:iam::{ACCOUNT}:mfa/{MFA_DEVICE}'
FORCE_MFA_POLICY = 'lab-hardening-force-mfa'
MFA_SELF_SERVICE_ACTIONS = [
    'iam:CreateVirtualMFADevice', 'iam:EnableMFADevice', 'iam:ListMFADevices', 'iam:ListVirtualMFADevices',
    'iam:ResyncMFADevice', 'iam:GetUser', 'iam:ChangePassword', 'sts:GetSessionToken',
]
FORCE_MFA_DOCUMENT = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'AllowAdministration', 'Effect': 'Allow', 'Action': '*', 'Resource': '*'},
        {
            'Sid': 'DenyAllExceptMfaSetupWithoutMfa', 'Effect': 'Deny', 'NotAction': MFA_SELF_SERVICE_ACTIONS,
            'Resource': '*', 'Condition': {'BoolIfExists': {'aws:MultiFactorAuthPresent': 'false'}},
        },
    ],
}

DEV_USER = 'lab-hardening-dev'
BOUNDARY_POLICY = 'lab-hardening-dev-boundary'
BOUNDARY_ARN = f'arn:aws:iam::{ACCOUNT}:policy/{BOUNDARY_POLICY}'
ADMIN_POLICY_ARN = 'arn:aws:iam::aws:policy/AdministratorAccess'
BOUNDARY_DOCUMENT = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'AppServices', 'Effect': 'Allow', 'Action': ['s3:*', 'dynamodb:*', 'logs:*', 'cloudwatch:*'],
         'Resource': '*'},
        {'Sid': 'ReadOwnIdentity', 'Effect': 'Allow', 'Action': ['iam:GetUser', 'sts:GetCallerIdentity'],
         'Resource': '*'},
    ],
}

ANALYZER = 'lab-hardening-account-analyzer'
SHARED_BUCKET = 'lab-hardening-shared-reports'
PARTNER_ROLE = 'lab-hardening-partner-role'
PUBLIC_BUCKET_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Sid': 'PublicRead', 'Effect': 'Allow', 'Principal': '*', 'Action': 's3:GetObject',
                   'Resource': f'arn:aws:s3:::{SHARED_BUCKET}/*'}],
}
PARTNER_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'AWS': f'arn:aws:iam::{EXTERNAL_ACCOUNT}:root'},
                   'Action': 'sts:AssumeRole'}],
}

LEGACY_USER = 'lab-hardening-legacy'
KEY_MAX_AGE_DAYS = 90
LAB_USERS = (ADMIN_USER, DEV_USER, LEGACY_USER)
CREDENTIAL_WAIT_SECONDS = 10

PASSWORD_COMMAND = (
    'aws iam update-account-password-policy --minimum-password-length 14 --require-symbols --require-numbers '
    '--require-uppercase-characters --require-lowercase-characters --allow-users-to-change-password '
    '--max-password-age 90 --password-reuse-prevention 24\n'
    f'aws iam create-user --user-name {ADMIN_USER}\n'
    f'aws iam create-login-profile --user-name {ADMIN_USER} --password {WEAK_PASSWORD}   # refused\n'
    f"aws iam create-login-profile --user-name {ADMIN_USER} --password '<14+ chars, mixed>' --password-reset-required"
)
MFA_COMMAND = (
    f'aws iam create-virtual-mfa-device --virtual-mfa-device-name {MFA_DEVICE} --outfile qr.png '
    '--bootstrap-method QRCodePNG\n'
    f'aws iam enable-mfa-device --user-name {ADMIN_USER} --serial-number {MFA_SERIAL} '
    '--authentication-code1 <code> --authentication-code2 <next code>'
)
FORCE_MFA_COMMAND = (
    f'aws iam create-group --group-name {ADMIN_GROUP}\n'
    f'aws iam put-group-policy --group-name {ADMIN_GROUP} --policy-name {FORCE_MFA_POLICY} '
    '--policy-document file://force-mfa.json\n'
    f'aws iam add-user-to-group --group-name {ADMIN_GROUP} --user-name {ADMIN_USER}\n'
    f'aws iam create-access-key --user-name {ADMIN_USER}\n'
    'aws s3api list-buckets            # as the admin, no MFA: AccessDenied\n'
    f'aws sts get-session-token --serial-number {MFA_SERIAL} --token-code <code>'
)
BOUNDARY_COMMAND = (
    f'aws iam create-policy --policy-name {BOUNDARY_POLICY} --policy-document file://boundary.json\n'
    f'aws iam create-user --user-name {DEV_USER} --permissions-boundary {BOUNDARY_ARN}\n'
    f'aws iam attach-user-policy --user-name {DEV_USER} --policy-arn {ADMIN_POLICY_ARN}\n'
    'aws s3api list-buckets                       # as the developer: allowed\n'
    'aws iam create-user --user-name escalate     # as the developer: AccessDenied'
)
ANALYZER_COMMAND = (
    f'aws accessanalyzer create-analyzer --analyzer-name {ANALYZER} --type ACCOUNT\n'
    f'aws s3api put-bucket-policy --bucket {SHARED_BUCKET} --policy file://public-read.json\n'
    f'aws iam create-role --role-name {PARTNER_ROLE} --assume-role-policy-document file://partner-trust.json\n'
    'aws accessanalyzer list-findings --analyzer-arn <analyzer-arn>'
)
REPORT_COMMAND = (
    f'aws iam create-user --user-name {LEGACY_USER}\n'
    f'aws iam create-login-profile --user-name {LEGACY_USER} --password <password>\n'
    f'aws iam create-access-key --user-name {LEGACY_USER}\n'
    'aws iam generate-credential-report\n'
    'aws iam get-credential-report --query Content --output text | base64 -d'
)
ROOT_COMMAND = (
    'aws iam get-account-summary --query "SummaryMap.{MFA:AccountMFAEnabled,Keys:AccountAccessKeysPresent}"\n'
    '# Root sign-in: enable MFA, delete any root access keys, use root only for the tasks that need it'
)

ROOT_ONLY_TASKS = [
    'Change the account name, root email address, or root password',
    'Close the account',
    'Change the AWS Support plan',
    'Restore IAM permissions when the only administrator locked themselves out',
    'Sign up for GovCloud or register as a seller in the Reserved Instance Marketplace',
]

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Harden an AWS account with a password policy, MFA, permissions boundaries, and access reviews',
    'description': (
        'Set a strong password policy and watch a weak password get refused, enable a virtual MFA device with real '
        'one-time codes, deny everything to an administrator who has not used MFA, cap a delegated developer with a '
        'permissions boundary, review external access with IAM Access Analyzer, audit users with the credential '
        'report, and check how the root user is protected.'
    ),
    'steps': [
        {
            'key': 'password-policy',
            'title': 'Require strong passwords for console users',
            'command': PASSWORD_COMMAND,
            'explanation': (
                'The account password policy applies to every IAM user who signs in to the console: length, '
                'character classes, a maximum age, and how many old passwords cannot be reused. It does not apply '
                'to the root user or to access keys. How Floci emulates this: the policy is enforced. The step '
                'tries a weak password first and Floci refuses it, then sets a compliant one that must be changed '
                'at first sign-in.'
            ),
        },
        {
            'key': 'virtual-mfa',
            'title': f'Enable a virtual MFA device for {ADMIN_USER}',
            'command': MFA_COMMAND,
            'explanation': (
                'A virtual MFA device is an authenticator app holding a shared secret (the QR code). Enabling it '
                'needs two consecutive six-digit codes, which proves the app and AWS agree on the secret and the '
                'clock. Hardware keys (FIDO2 security keys) are also supported in AWS. How Floci emulates this: the '
                'codes are checked. The step computes them from the device seed (RFC 6238, 30-second steps) after a '
                'wrong code is refused.'
            ),
        },
        {
            'key': 'require-mfa',
            'title': 'Deny everything to administrators until they use MFA',
            'command': FORCE_MFA_COMMAND,
            'explanation': (
                'A group policy allows administration but denies every action except setting up MFA when '
                'aws:MultiFactorAuthPresent is false. BoolIfExists also catches long-term access keys, where the key '
                'is absent. With MFA, the user signs in to the console or calls sts get-session-token with a code '
                'to get temporary credentials that carry MFA. How Floci emulates this: the deny is enforced for '
                'long-term keys. Floci never marks a session as MFA-authenticated (and does not check the token '
                'code), so the MFA session is denied too; the step records that and passes on the enforced deny.'
            ),
        },
        {
            'key': 'permission-boundary',
            'title': 'Cap a delegated developer with a permissions boundary',
            'command': BOUNDARY_COMMAND,
            'explanation': (
                'A permissions boundary is a managed policy that sets the most an identity can ever do. Effective '
                'permissions are the intersection of the identity policies and the boundary, so even '
                'AdministratorAccess cannot reach past it. Teams use boundaries to let developers create roles '
                'safely. How Floci emulates this: boundaries are enforced. The developer reaches S3 but is denied '
                'iam:CreateUser.'
            ),
        },
        {
            'key': 'access-analyzer',
            'title': 'Find resources shared outside the account with IAM Access Analyzer',
            'command': ANALYZER_COMMAND,
            'explanation': (
                'An account analyzer (or an organization analyzer, with AWS Organizations) reads resource policies '
                'on S3 buckets, IAM role trust policies, KMS keys, Lambda functions, SQS queues, and secrets, and '
                'reports any that grant access outside the zone of trust. Access Analyzer also validates policies '
                'and can generate least-privilege policies from CloudTrail. How Floci emulates this: the analyzer is '
                'created, but its findings API is misrouted, so the step reviews the bucket policy and role trust '
                'policy itself.'
            ),
        },
        {
            'key': 'credential-report',
            'title': 'Audit users with the credential report',
            'command': REPORT_COMMAND,
            'explanation': (
                'The credential report is a CSV with one row per user: password enabled and last used, MFA active, '
                'and each access key\'s status and age. Auditors use it to find console users without MFA and keys '
                f'older than {KEY_MAX_AGE_DAYS} days. A new report can be generated at most every four hours. How '
                'Floci emulates this: the report is generated and cached for four hours like AWS, so when it predates '
                'the lab users the step builds their rows from the IAM APIs and says so.'
            ),
        },
        {
            'key': 'root-user',
            'title': 'Check how the root user is protected',
            'command': ROOT_COMMAND,
            'explanation': (
                'The root user can do everything and cannot be limited by IAM policies (only by SCPs in a member '
                'account). Best practice: enable MFA on root, delete root access keys, and use root only for the '
                'few tasks that need it. The account summary reports both. How Floci emulates this: the summary '
                'reports root access keys and root MFA; Floci cannot enable MFA on root, so the step checks that '
                'root has no access keys and lists the root-only tasks.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


def _user_client(name: str, key: dict[str, str] | None, session_token: str | None = None):
    identity = {'access_key_id': key['AccessKeyId'], 'secret_access_key': key['SecretAccessKey'], 'type': 'lab'}
    if session_token:
        identity['session_token'] = session_token
    return FlociClientFactory(identity=identity).client(name)


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _marker(step: str, value: Any) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


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


def _attempt(call) -> dict[str, Any]:
    """Run a call as another identity and report allowed or the error code."""
    try:
        call()
        return {'Outcome': 'allowed'}
    except ClientError as exc:
        return {'Outcome': 'denied' if 'Denied' in _code(exc) else 'error', 'Code': _code(exc),
                'Message': (exc.response.get('Error', {}).get('Message') or '')[:200]}


def _attempt_with_new_key(call) -> dict[str, Any]:
    """New access keys can take a moment to work; retry while the key is still unknown."""
    deadline = time.monotonic() + CREDENTIAL_WAIT_SECONDS
    while True:
        outcome = _attempt(call)
        if outcome.get('Code') not in ('InvalidClientTokenId', 'InvalidAccessKeyId', 'SignatureDoesNotMatch') \
                or time.monotonic() >= deadline:
            return outcome
        time.sleep(1)


def totp(seed: str, at: float, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based one-time password from a base32 seed."""
    key = base64.b32decode(seed.strip().upper() + '=' * (-len(seed.strip()) % 8))
    digest = hmac.new(key, struct.pack('>Q', int(at) // step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def _seed_text(seed: Any) -> str:
    text = seed.decode() if isinstance(seed, (bytes, bytearray)) else str(seed or '')
    if text.startswith("b'") and text.endswith("'"):
        text = text[2:-1]
    return text


def password_problems(password: str, policy: dict[str, Any]) -> list[str]:
    """What a password lacks under an account password policy."""
    problems = []
    if len(password) < int(policy.get('MinimumPasswordLength') or 0):
        problems.append(f'at least {policy["MinimumPasswordLength"]} characters')
    if policy.get('RequireUppercaseCharacters') and not any(ch.isupper() for ch in password):
        problems.append('an uppercase letter')
    if policy.get('RequireLowercaseCharacters') and not any(ch.islower() for ch in password):
        problems.append('a lowercase letter')
    if policy.get('RequireNumbers') and not any(ch.isdigit() for ch in password):
        problems.append('a number')
    if policy.get('RequireSymbols') and all(ch.isalnum() for ch in password):
        problems.append('a symbol')
    return problems


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def external_principals(policy: dict[str, Any], account: str = ACCOUNT) -> list[str]:
    """Principals in Allow statements that are outside the account (what Access Analyzer reports)."""
    found = []
    for statement in _as_list((policy or {}).get('Statement')):
        if statement.get('Effect') != 'Allow' or statement.get('Condition'):
            continue
        principal = statement.get('Principal')
        if principal == '*' or (isinstance(principal, dict) and '*' in _as_list(principal.get('AWS'))):
            found.append('*')
            continue
        if not isinstance(principal, dict):
            continue
        for value in _as_list(principal.get('AWS')):
            text = str(value)
            owner = text.split(':')[4] if text.startswith('arn:') and len(text.split(':')) > 4 else text
            if owner != account:
                found.append(text)
    return found


# ── Lookups ──────────────────────────────────────────────────────────────────

def _password_policy() -> dict[str, Any] | None:
    try:
        return _client('iam').get_account_password_policy()['PasswordPolicy']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise


def _user(name: str) -> dict[str, Any] | None:
    try:
        return _client('iam').get_user(UserName=name)['User']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise


def _login_profile(name: str) -> dict[str, Any] | None:
    try:
        return _client('iam').get_login_profile(UserName=name)['LoginProfile']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise


def _mfa_devices(name: str) -> list[dict[str, Any]]:
    try:
        return _client('iam').list_mfa_devices(UserName=name).get('MFADevices', [])
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return []
        raise


def _virtual_device() -> dict[str, Any] | None:
    devices = _client('iam').list_virtual_mfa_devices().get('VirtualMFADevices', [])
    return next((device for device in devices if device.get('SerialNumber') == MFA_SERIAL), None)


def _group_policy() -> dict[str, Any] | None:
    try:
        document = _client('iam').get_group_policy(GroupName=ADMIN_GROUP, PolicyName=FORCE_MFA_POLICY)['PolicyDocument']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise
    return json.loads(document) if isinstance(document, str) else document


def _group_members() -> list[str]:
    try:
        return [user['UserName'] for user in _client('iam').get_group(GroupName=ADMIN_GROUP).get('Users', [])]
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return []
        raise


def _access_keys(name: str) -> list[dict[str, Any]]:
    try:
        return _client('iam').list_access_keys(UserName=name).get('AccessKeyMetadata', [])
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return []
        raise


def _attached(name: str) -> list[str]:
    try:
        return [item['PolicyArn'] for item in
                _client('iam').list_attached_user_policies(UserName=name).get('AttachedPolicies', [])]
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return []
        raise


def _policy_exists(arn: str) -> bool:
    try:
        _client('iam').get_policy(PolicyArn=arn)
        return True
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return False
        raise


def _analyzer() -> dict[str, Any] | None:
    try:
        return _client('accessanalyzer').get_analyzer(analyzerName=ANALYZER)['analyzer']
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _bucket_policy() -> dict[str, Any] | None:
    try:
        policy = _client('s3').get_bucket_policy(Bucket=SHARED_BUCKET)['Policy']
    except ClientError as exc:
        if _code(exc) in ('NoSuchBucket', 'NoSuchBucketPolicy'):
            return None
        raise
    return json.loads(policy) if isinstance(policy, str) else policy


def _role_trust() -> dict[str, Any] | None:
    try:
        document = _client('iam').get_role(RoleName=PARTNER_ROLE)['Role']['AssumeRolePolicyDocument']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise
    return json.loads(document) if isinstance(document, str) else document


def _summary() -> dict[str, Any]:
    return _client('iam').get_account_summary().get('SummaryMap', {})


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_password_policy() -> tuple[bool, str]:
    policy = _password_policy()
    if not policy:
        return False, 'The account has no password policy.'
    wrong = [key for key, value in PASSWORD_POLICY.items() if key != 'HardExpiry' and policy.get(key) != value]
    if wrong:
        return False, f'The password policy should set {", ".join(sorted(wrong))} as the step does.'
    if not _user(ADMIN_USER) or not _login_profile(ADMIN_USER):
        return False, f'{ADMIN_USER} needs a console password.'
    return True, (f'Passwords need {PASSWORD_POLICY["MinimumPasswordLength"]}+ characters with all four character '
                  f'types, expire after {PASSWORD_POLICY["MaxPasswordAge"]} days, and the last '
                  f'{PASSWORD_POLICY["PasswordReusePrevention"]} cannot be reused; {ADMIN_USER} has a password.')


def _check_mfa() -> tuple[bool, str]:
    serials = [device.get('SerialNumber') for device in _mfa_devices(ADMIN_USER)]
    if MFA_SERIAL not in serials:
        return False, f'{MFA_DEVICE} is not enabled for {ADMIN_USER}.'
    return True, f'{MFA_DEVICE} is enabled for {ADMIN_USER}.'


def _check_require_mfa() -> tuple[bool, str]:
    document = _group_policy()
    if not document:
        return False, f'{ADMIN_GROUP} has no {FORCE_MFA_POLICY} policy.'
    denies = [statement for statement in _as_list(document.get('Statement')) if statement.get('Effect') == 'Deny'
              and (statement.get('Condition') or {}).get('BoolIfExists', {}).get('aws:MultiFactorAuthPresent')
              in ('false', False)]
    if not denies:
        return False, f'{FORCE_MFA_POLICY} must deny with BoolIfExists aws:MultiFactorAuthPresent = false.'
    if ADMIN_USER not in _group_members():
        return False, f'{ADMIN_USER} must be in {ADMIN_GROUP}.'
    marker = _marked('require-mfa')
    if not marker:
        return False, 'Call AWS as the administrator without MFA to prove the deny.'
    return True, marker['message']


def _check_boundary() -> tuple[bool, str]:
    user = _user(DEV_USER)
    if not user:
        return False, f'{DEV_USER} was not found.'
    if (user.get('PermissionsBoundary') or {}).get('PermissionsBoundaryArn') != BOUNDARY_ARN:
        return False, f'{DEV_USER} must have {BOUNDARY_POLICY} as its permissions boundary.'
    if ADMIN_POLICY_ARN not in _attached(DEV_USER):
        return False, f'{DEV_USER} should have AdministratorAccess attached to show the boundary capping it.'
    marker = _marked('permission-boundary')
    if not marker:
        return False, 'Call AWS as the developer to prove the boundary.'
    return True, marker['message']


def _check_analyzer() -> tuple[bool, str]:
    analyzer = _analyzer()
    if not analyzer or analyzer.get('type') != 'ACCOUNT':
        return False, f'Account analyzer {ANALYZER} was not found.'
    findings = _external_findings()
    if len(findings) < 2:
        return False, f'The review should find the public bucket policy and the trust policy for {EXTERNAL_ACCOUNT}.'
    return True, (f'{ANALYZER} is active; the review found {SHARED_BUCKET} readable by anyone and {PARTNER_ROLE} '
                  f'assumable by account {EXTERNAL_ACCOUNT}.')


def _external_findings() -> list[dict[str, Any]]:
    findings = []
    for resource, kind, policy in ((f'arn:aws:s3:::{SHARED_BUCKET}', 'AWS::S3::Bucket', _bucket_policy()),
                                   (f'arn:aws:iam::{ACCOUNT}:role/{PARTNER_ROLE}', 'AWS::IAM::Role', _role_trust())):
        for principal in external_principals(policy or {}):
            findings.append({'resource': resource, 'resourceType': kind, 'principal': principal,
                             'isPublic': principal == '*', 'status': 'ACTIVE'})
    return findings


def _check_report() -> tuple[bool, str]:
    marker = _marked('credential-report')
    if not marker:
        return False, 'Generate and audit the credential report.'
    if not _user(LEGACY_USER):
        return False, f'{LEGACY_USER} was not found.'
    return True, marker['message']


def _check_root() -> tuple[bool, str]:
    summary = _summary()
    if summary.get('AccountAccessKeysPresent'):
        return False, 'The root user has access keys; delete them.'
    marker = _marked('root-user')
    if not marker:
        return False, 'Read the account summary.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_password_policy() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    iam.update_account_password_policy(**{key: value for key, value in PASSWORD_POLICY.items()})
    if not _user(ADMIN_USER):
        iam.create_user(UserName=ADMIN_USER, Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    attempts: dict[str, Any] = {}
    if _login_profile(ADMIN_USER):
        attempts['WeakPassword'] = {'Outcome': 'skipped', 'Reason': f'{ADMIN_USER} already has a password'}
    else:
        try:
            iam.create_login_profile(UserName=ADMIN_USER, Password=WEAK_PASSWORD, PasswordResetRequired=True)
            attempts['WeakPassword'] = {'Outcome': 'accepted'}
        except ClientError as exc:
            attempts['WeakPassword'] = {'Outcome': 'refused', 'Code': _code(exc),
                                        'Message': exc.response.get('Error', {}).get('Message')}
        if attempts['WeakPassword']['Outcome'] == 'accepted':
            iam.delete_login_profile(UserName=ADMIN_USER)
        iam.create_login_profile(UserName=ADMIN_USER, Password=STRONG_PASSWORD, PasswordResetRequired=True)
        attempts['StrongPassword'] = {'Outcome': 'accepted', 'PasswordResetRequired': True}
    policy = _password_policy() or {}
    response = {'PasswordPolicy': policy, 'Attempts': attempts,
                'WeakPasswordLacks': password_problems(WEAK_PASSWORD, PASSWORD_POLICY),
                'LoginProfile': _login_profile(ADMIN_USER)}
    verified, message = _check_password_policy()
    if verified and attempts['WeakPassword']['Outcome'] == 'refused':
        message += f' Floci refused "{WEAK_PASSWORD}" ({attempts["WeakPassword"]["Code"]}).'
    elif verified and attempts['WeakPassword']['Outcome'] == 'accepted':
        message += (f' Floci accepted "{WEAK_PASSWORD}" although it lacks '
                    f'{", ".join(password_problems(WEAK_PASSWORD, PASSWORD_POLICY))}, so the step replaced it.')
    return _result('password-policy', PASSWORD_COMMAND, response, verified, message, started)


def step_virtual_mfa() -> dict[str, Any]:
    started = time.perf_counter()
    if not _user(ADMIN_USER):
        return _result('virtual-mfa', MFA_COMMAND, {}, False, f'Create {ADMIN_USER} first.', started)
    iam = _client('iam')
    attempts: dict[str, Any] = {}
    if MFA_SERIAL not in [device.get('SerialNumber') for device in _mfa_devices(ADMIN_USER)]:
        if _virtual_device():
            iam.delete_virtual_mfa_device(SerialNumber=MFA_SERIAL)
        seed = _seed_text(iam.create_virtual_mfa_device(VirtualMFADeviceName=MFA_DEVICE)['VirtualMFADevice']
                          .get('Base32StringSeed'))
        try:
            iam.enable_mfa_device(UserName=ADMIN_USER, SerialNumber=MFA_SERIAL, AuthenticationCode1='000000',
                                  AuthenticationCode2='000001')
            attempts['WrongCodes'] = {'Outcome': 'accepted'}
            iam.deactivate_mfa_device(UserName=ADMIN_USER, SerialNumber=MFA_SERIAL)
        except ClientError as exc:
            attempts['WrongCodes'] = {'Outcome': 'refused', 'Code': _code(exc)}
        now = time.time()
        codes = (totp(seed, now - 30), totp(seed, now))
        iam.enable_mfa_device(UserName=ADMIN_USER, SerialNumber=MFA_SERIAL, AuthenticationCode1=codes[0],
                              AuthenticationCode2=codes[1])
        attempts['ComputedCodes'] = {'Outcome': 'accepted', 'Codes': list(codes)}
        _marker('virtual-mfa', {'seed': seed, 'wrong': attempts['WrongCodes']['Outcome']})
    response = {'MFADevices': _mfa_devices(ADMIN_USER), 'Attempts': attempts}
    verified, message = _check_mfa()
    if verified and attempts.get('WrongCodes', {}).get('Outcome') == 'refused':
        message += ' Floci refused made-up codes and accepted two consecutive codes computed from the seed.'
    elif verified and attempts.get('WrongCodes', {}).get('Outcome') == 'accepted':
        message += ' Floci accepted made-up codes, so it does not check them; AWS would refuse.'
    return _result('virtual-mfa', MFA_COMMAND, response, verified, message, started)


def step_require_mfa() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_mfa()[0]:
        return _result('require-mfa', FORCE_MFA_COMMAND, {}, False, f'Enable {MFA_DEVICE} first.', started)
    iam = _client('iam')
    try:
        iam.create_group(GroupName=ADMIN_GROUP)
    except ClientError as exc:
        if _code(exc) != 'EntityAlreadyExists':
            raise
    iam.put_group_policy(GroupName=ADMIN_GROUP, PolicyName=FORCE_MFA_POLICY,
                         PolicyDocument=json.dumps(FORCE_MFA_DOCUMENT))
    iam.add_user_to_group(GroupName=ADMIN_GROUP, UserName=ADMIN_USER)
    for key in _access_keys(ADMIN_USER):
        iam.delete_access_key(UserName=ADMIN_USER, AccessKeyId=key['AccessKeyId'])
    key = iam.create_access_key(UserName=ADMIN_USER)['AccessKey']
    calls = {
        'ListBucketsWithoutMfa': _attempt_with_new_key(lambda: _user_client('s3', key).list_buckets()),
        'ListOwnMfaDevicesWithoutMfa': _attempt(lambda: _user_client('iam', key).list_mfa_devices(UserName=ADMIN_USER)),
    }
    seed = (_marked('virtual-mfa') or {}).get('seed')
    session: dict[str, Any] = {}
    if seed:
        try:
            credentials = _user_client('sts', key).get_session_token(
                SerialNumber=MFA_SERIAL, TokenCode=totp(seed, time.time()), DurationSeconds=3600)['Credentials']
            session = {'AccessKeyId': credentials['AccessKeyId'], 'SecretAccessKey': credentials['SecretAccessKey']}
            calls['ListBucketsWithMfaSession'] = _attempt(
                lambda: _user_client('s3', session, credentials['SessionToken']).list_buckets())
        except ClientError as exc:
            calls['GetSessionTokenWithMfa'] = {'Outcome': 'error', 'Code': _code(exc)}
    response = {'GroupPolicy': _group_policy(), 'Members': _group_members(), 'Calls': calls}
    if calls['ListBucketsWithoutMfa']['Outcome'] != 'denied':
        return _result('require-mfa', FORCE_MFA_COMMAND, response, False,
                       'Listing buckets without MFA should have been denied by the policy.', started)
    message = (f'Without MFA, {ADMIN_USER} was denied s3:ListBuckets but could still manage its own MFA device '
               f'({calls["ListOwnMfaDevicesWithoutMfa"]["Outcome"]}).')
    with_mfa = calls.get('ListBucketsWithMfaSession', {}).get('Outcome')
    if with_mfa == 'allowed':
        message += ' The MFA session from get-session-token was allowed.'
    elif with_mfa:
        message += (' Floci does not mark get-session-token sessions as MFA-authenticated, so the MFA session was '
                    'denied too; in AWS it would be allowed.')
    _marker('require-mfa', {'message': message})
    verified, check = _check_require_mfa()
    return _result('require-mfa', FORCE_MFA_COMMAND, response, verified, message if verified else check, started)


def step_permission_boundary() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    if not _policy_exists(BOUNDARY_ARN):
        iam.create_policy(PolicyName=BOUNDARY_POLICY, PolicyDocument=json.dumps(BOUNDARY_DOCUMENT),
                          Description='Most a delegated developer can do (account hardening lab)')
    if not _user(DEV_USER):
        iam.create_user(UserName=DEV_USER, PermissionsBoundary=BOUNDARY_ARN, Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    else:
        iam.put_user_permissions_boundary(UserName=DEV_USER, PermissionsBoundary=BOUNDARY_ARN)
    iam.attach_user_policy(UserName=DEV_USER, PolicyArn=ADMIN_POLICY_ARN)
    for key in _access_keys(DEV_USER):
        iam.delete_access_key(UserName=DEV_USER, AccessKeyId=key['AccessKeyId'])
    key = iam.create_access_key(UserName=DEV_USER)['AccessKey']
    calls = {
        'S3ListBuckets': _attempt_with_new_key(lambda: _user_client('s3', key).list_buckets()),
        'IamCreateUser': _attempt(lambda: _user_client('iam', key).create_user(UserName='lab-hardening-escalate')),
    }
    if calls['IamCreateUser']['Outcome'] == 'allowed':
        try:
            iam.delete_user(UserName='lab-hardening-escalate')
        except ClientError:
            pass
    response = {'User': _user(DEV_USER), 'AttachedPolicies': _attached(DEV_USER), 'Boundary': BOUNDARY_DOCUMENT,
                'Calls': calls}
    if calls['S3ListBuckets']['Outcome'] != 'allowed' or calls['IamCreateUser']['Outcome'] != 'denied':
        return _result('permission-boundary', BOUNDARY_COMMAND, response, False,
                       'The developer should reach S3 and be denied iam:CreateUser.', started)
    message = (f'{DEV_USER} has AdministratorAccess, yet the boundary allowed only what it lists: S3 worked and '
               'iam:CreateUser was denied.')
    _marker('permission-boundary', {'message': message})
    verified, check = _check_boundary()
    return _result('permission-boundary', BOUNDARY_COMMAND, response, verified, message if verified else check,
                   started)


def step_access_analyzer() -> dict[str, Any]:
    started = time.perf_counter()
    analyzer_client, s3, iam = _client('accessanalyzer'), _client('s3'), _client('iam')
    if not _analyzer():
        analyzer_client.create_analyzer(analyzerName=ANALYZER, type='ACCOUNT', tags={'Lab': LAB_KEY})
    try:
        s3.create_bucket(Bucket=SHARED_BUCKET)
    except ClientError as exc:
        if _code(exc) not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            raise
    try:
        s3.delete_public_access_block(Bucket=SHARED_BUCKET)
    except ClientError:
        pass
    s3.put_bucket_policy(Bucket=SHARED_BUCKET, Policy=json.dumps(PUBLIC_BUCKET_POLICY))
    if not _role_trust():
        iam.create_role(RoleName=PARTNER_ROLE, AssumeRolePolicyDocument=json.dumps(PARTNER_TRUST),
                        Description='Role a partner account can assume (account hardening lab)')
    analyzer = _analyzer() or {}
    findings_api: dict[str, Any]
    try:
        listed = analyzer_client.list_findings(analyzerArn=analyzer.get('arn'))
        findings_api = {'Outcome': 'answered', 'Findings': listed.get('findings', [])}
    except (ClientError, BotoCoreError) as exc:
        findings_api = {'Outcome': 'unsupported',
                        'Code': _code(exc) if isinstance(exc, ClientError) else type(exc).__name__}
    findings = _external_findings()
    response = {'Analyzer': analyzer, 'ListFindings': findings_api, 'ReviewFindings': findings}
    verified, message = _check_analyzer()
    if verified and findings_api['Outcome'] != 'answered':
        message += ' Floci does not serve analyzer findings, so the step read the policies itself.'
    return _result('access-analyzer', ANALYZER_COMMAND, response, verified, message, started)


def _rows_from_iam(names: list[str]) -> list[dict[str, str]]:
    rows = []
    for name in names:
        user = _user(name)
        if not user:
            continue
        keys = _access_keys(name)
        row = {
            'user': name, 'arn': user['Arn'], 'password_enabled': 'TRUE' if _login_profile(name) else 'FALSE',
            'mfa_active': 'TRUE' if _mfa_devices(name) else 'FALSE', 'source': 'IAM APIs (report predates the user)',
        }
        for index in (1, 2):
            key = keys[index - 1] if len(keys) >= index else None
            row[f'access_key_{index}_active'] = 'TRUE' if key and key.get('Status') == 'Active' else 'FALSE'
            created = key.get('CreateDate') if key else None
            row[f'access_key_{index}_last_rotated'] = created.isoformat() if hasattr(created, 'isoformat') else (
                str(created) if created else 'N/A')
        rows.append(row)
    return rows


def audit_rows(rows: list[dict[str, str]], now: float) -> list[dict[str, str]]:
    """Findings an auditor raises from credential report rows."""
    findings = []
    for row in rows:
        name = row.get('user')
        if name == '<root_account>':
            continue
        if row.get('password_enabled') == 'TRUE' and row.get('mfa_active') != 'TRUE':
            findings.append({'user': name, 'finding': 'Console password without MFA'})
        for index in (1, 2):
            if row.get(f'access_key_{index}_active') != 'TRUE':
                continue
            rotated = row.get(f'access_key_{index}_last_rotated') or 'N/A'
            try:
                age = (now - time.mktime(time.strptime(rotated[:19], '%Y-%m-%dT%H:%M:%S'))) / 86400
            except ValueError:
                age = 0
            if age > KEY_MAX_AGE_DAYS:
                findings.append({'user': name, 'finding': f'Access key {index} older than {KEY_MAX_AGE_DAYS} days'})
            if row.get('password_enabled') == 'TRUE':
                findings.append({'user': name, 'finding': f'Human user with both a password and access key {index}'})
    return findings


def step_credential_report() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    if not _user(ADMIN_USER) or not _user(DEV_USER):
        return _result('credential-report', REPORT_COMMAND, {}, False,
                       f'Create {ADMIN_USER} and {DEV_USER} first (earlier steps).', started)
    if not _user(LEGACY_USER):
        iam.create_user(UserName=LEGACY_USER, Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    if not _login_profile(LEGACY_USER):
        iam.create_login_profile(UserName=LEGACY_USER, Password=STRONG_PASSWORD, PasswordResetRequired=False)
    if not _access_keys(LEGACY_USER):
        iam.create_access_key(UserName=LEGACY_USER)
    generated = iam.generate_credential_report()
    deadline = time.monotonic() + 10
    report = None
    while report is None:
        try:
            report = iam.get_credential_report()
        except ClientError as exc:
            if _code(exc) not in ('ReportInProgress', 'ReportNotPresent') or time.monotonic() >= deadline:
                raise
            time.sleep(1)
    content = report.get('Content') or b''
    text = content.decode() if isinstance(content, (bytes, bytearray)) else str(content)
    rows = list(csv.DictReader(io.StringIO(text)))
    names = {row.get('user') for row in rows}
    missing = [name for name in LAB_USERS if name not in names]
    lab_rows = [row for row in rows if row.get('user') in LAB_USERS] + _rows_from_iam(missing)
    findings = audit_rows(lab_rows, time.time())
    response = {'Generate': {key: generated.get(key) for key in ('State', 'Description')},
                'GeneratedTime': report.get('GeneratedTime'), 'RootRow': next(
                    (row for row in rows if row.get('user') == '<root_account>'), None),
                'LabUsers': lab_rows, 'Findings': findings}
    admin = next((row for row in lab_rows if row.get('user') == ADMIN_USER), {})
    legacy_flags = [item['finding'] for item in findings if item['user'] == LEGACY_USER]
    if admin.get('mfa_active') != 'TRUE' or not any('without MFA' in flag for flag in legacy_flags):
        return _result('credential-report', REPORT_COMMAND, response, False,
                       f'{ADMIN_USER} should show MFA active and {LEGACY_USER} should be flagged for a password '
                       'without MFA.', started)
    message = (f'The audit passed {ADMIN_USER} (password and MFA) and flagged {LEGACY_USER}: '
               f'{"; ".join(legacy_flags)}.')
    if missing:
        message += (f' The cached report (regenerated at most every four hours) predates {", ".join(missing)}, so '
                    'the step built those rows from the IAM APIs.')
    _marker('credential-report', {'message': message})
    verified, check = _check_report()
    return _result('credential-report', REPORT_COMMAND, response, verified, message if verified else check, started)


def step_root_user() -> dict[str, Any]:
    started = time.perf_counter()
    summary = _summary()
    posture = {
        'RootAccessKeysPresent': summary.get('AccountAccessKeysPresent'),
        'RootMfaEnabled': summary.get('AccountMFAEnabled'),
        'RootSigningCertificatesPresent': summary.get('AccountSigningCertificatesPresent'),
    }
    response = {'AccountSummary': posture, 'RootOnlyTasks': ROOT_ONLY_TASKS}
    if posture['RootAccessKeysPresent']:
        return _result('root-user', ROOT_COMMAND, response, False,
                       'The root user has access keys; delete them and use IAM roles or users instead.', started)
    message = 'The root user has no access keys.'
    if posture['RootMfaEnabled']:
        message += ' Root MFA is enabled.'
    else:
        message += (' Root MFA is off: in AWS, enable it first thing (Floci cannot enable MFA on root). Use root only '
                    'for the tasks listed.')
    _marker('root-user', {'message': message})
    verified, check = _check_root()
    return _result('root-user', ROOT_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'password-policy': step_password_policy,
    'virtual-mfa': step_virtual_mfa,
    'require-mfa': step_require_mfa,
    'permission-boundary': step_permission_boundary,
    'access-analyzer': step_access_analyzer,
    'credential-report': step_credential_report,
    'root-user': step_root_user,
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
    checks = {
        'password-policy': lambda: _check_password_policy()[0],
        'virtual-mfa': lambda: _check_mfa()[0],
        'require-mfa': lambda: _check_require_mfa()[0],
        'permission-boundary': lambda: _check_boundary()[0],
        'access-analyzer': lambda: _check_analyzer()[0],
        'credential-report': lambda: _check_report()[0],
        'root-user': lambda: _check_root()[0],
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
    """Delete the lab users, group, policies, MFA device, role, bucket, and analyzer, and the password policy."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code in ('NoSuchEntity', 'NoSuchBucket', 'NoSuchBucketPolicy'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    iam, s3 = _client('iam'), _client('s3')

    def remove_user(name: str) -> None:
        try:
            if not _user(name):
                return
        except (ClientError, BotoCoreError) as exc:
            errors.append(f'user:{name}: {exc}')
            return
        for key in _access_keys(name):
            attempt(f'access-key:{name}', lambda key=key: iam.delete_access_key(UserName=name,
                                                                                AccessKeyId=key['AccessKeyId']))
        for device in _mfa_devices(name):
            attempt(f'mfa:{name}', lambda device=device: iam.deactivate_mfa_device(
                UserName=name, SerialNumber=device['SerialNumber']))
        if _login_profile(name):
            attempt(f'login-profile:{name}', lambda: iam.delete_login_profile(UserName=name))
        for arn in _attached(name):
            attempt(f'detach:{name}', lambda arn=arn: iam.detach_user_policy(UserName=name, PolicyArn=arn))
        try:
            groups = iam.list_groups_for_user(UserName=name).get('Groups', [])
        except ClientError:
            groups = []
        for group in groups:
            attempt(f'group-member:{name}', lambda group=group: iam.remove_user_from_group(
                GroupName=group['GroupName'], UserName=name))
        if (_user(name) or {}).get('PermissionsBoundary'):
            attempt(f'boundary:{name}', lambda: iam.delete_user_permissions_boundary(UserName=name))
        attempt(f'user:{name}', lambda: iam.delete_user(UserName=name))

    for name in LAB_USERS:
        remove_user(name)
    attempt(f'mfa-device:{MFA_DEVICE}', lambda: iam.delete_virtual_mfa_device(SerialNumber=MFA_SERIAL))
    attempt(f'group-policy:{FORCE_MFA_POLICY}', lambda: iam.delete_group_policy(GroupName=ADMIN_GROUP,
                                                                              PolicyName=FORCE_MFA_POLICY))
    attempt(f'group:{ADMIN_GROUP}', lambda: iam.delete_group(GroupName=ADMIN_GROUP))
    attempt(f'policy:{BOUNDARY_POLICY}', lambda: iam.delete_policy(PolicyArn=BOUNDARY_ARN))
    attempt(f'role:{PARTNER_ROLE}', lambda: iam.delete_role(RoleName=PARTNER_ROLE))
    attempt(f'bucket-policy:{SHARED_BUCKET}', lambda: s3.delete_bucket_policy(Bucket=SHARED_BUCKET))
    attempt(f'bucket:{SHARED_BUCKET}', lambda: s3.delete_bucket(Bucket=SHARED_BUCKET))
    attempt(f'analyzer:{ANALYZER}', lambda: _client('accessanalyzer').delete_analyzer(analyzerName=ANALYZER))
    try:
        policy = _password_policy()
    except (ClientError, BotoCoreError):
        policy = None
    if policy and all(policy.get(key) == value for key, value in PASSWORD_POLICY.items() if key != 'HardExpiry'):
        attempt('password-policy', lambda: iam.delete_account_password_policy())

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws iam delete-access-key / deactivate-mfa-device / delete-login-profile / delete-user  # for {", ".join(LAB_USERS)}\n'
            f'aws iam delete-virtual-mfa-device --serial-number {MFA_SERIAL}\n'
            f'aws iam delete-group-policy --group-name {ADMIN_GROUP} --policy-name {FORCE_MFA_POLICY}\n'
            f'aws iam delete-group --group-name {ADMIN_GROUP}\n'
            f'aws iam delete-policy --policy-arn {BOUNDARY_ARN}\n'
            f'aws iam delete-role --role-name {PARTNER_ROLE}\n'
            f'aws s3api delete-bucket --bucket {SHARED_BUCKET}\n'
            f'aws accessanalyzer delete-analyzer --analyzer-name {ANALYZER}\n'
            'aws iam delete-account-password-policy   # only when it is still the lab policy'
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
            else 'The lab users, group, MFA device, boundary policy, role, bucket, analyzer and password policy are gone.',
        },
    }
