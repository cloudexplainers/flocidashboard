"""Access troubleshooting lab: an analyst denied on a report, the fix tested in the policy simulator before it is
applied, a Region guardrail, a KMS decrypt that fails twice for two different reasons, a TLS certificate on a load
balancer, a bucket shared with an unknown account, and a conformance pack evaluated and fixed.

Floci 2.4.0 enforces IAM for these calls: identity policies scoped to object ARNs and KMS key ARNs, an
aws:RequestedRegion deny, and an explicit deny in a bucket policy all decide real requests. A disabled KMS key refuses
to decrypt, ACM issues certificates at once, and a certificate attached to a listener cannot be deleted. What Floci
does not do: CloudTrail records no denied calls, it ignores a deny in a KMS key policy and the aws:SecureTransport
condition, IAM Access Analyzer creates analyzers but has no findings API, and a conformance pack is stored without
creating or running its rules. The lab finds the external principal and evaluates the rules itself, then records the
results on custom Config rules with PutEvaluations.
"""

from __future__ import annotations

import base64
import datetime
import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'iam'
LAB_KEY = 'access-troubleshooting'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
OTHER_REGION = 'us-west-2'
ACCOUNT = '000000000000'

ANALYST = 'lab-access-analyst'
ANALYST_ARN = f'arn:aws:iam::{ACCOUNT}:user/{ANALYST}'
S3_POLICY = 'lab-access-reports'
REGION_POLICY = 'lab-access-regions'
KMS_POLICY = 'lab-access-kms'
BUCKET = 'lab-access-reports'
BUCKET_ARN = f'arn:aws:s3:::{BUCKET}'
REPORT_KEY = 'reports/q3.csv'
PAYROLL_KEY = 'payroll/salaries.csv'
UPLOAD_KEY = 'reports/q4-draft.csv'
QUEUE = 'lab-access-jobs'
KEY_ALIAS = 'alias/lab-access-reports'
SECRET_TEXT = b'reports-db-password'
DOMAIN = 'reports.lab.example'
LOAD_BALANCER = 'lab-access-alb'
TARGET_GROUP = 'lab-access-web'
TLS_POLICY = 'ELBSecurityPolicy-TLS13-1-2-2021-06'
ANALYZER = 'lab-access-analyzer'
PARTNER_ACCOUNT = '111122223333'
APPROVED_ACCOUNTS = ['444455556666']
PACK = 'lab-access-s3-baseline'
RULES = {
    'lab-access-s3-versioning': 'S3_BUCKET_VERSIONING_ENABLED',
    'lab-access-s3-ssl-only': 'S3_BUCKET_SSL_REQUESTS_ONLY',
}

FLOCI = 'How Floci emulates this: '


def _doc(*statements: dict[str, Any]) -> dict[str, Any]:
    return {'Version': '2012-10-17', 'Statement': list(statements)}


LIST_ONLY_POLICY = _doc({'Sid': 'ListReports', 'Effect': 'Allow', 'Action': 's3:ListBucket', 'Resource': BUCKET_ARN})
FIXED_POLICY = _doc(
    {'Sid': 'ListReports', 'Effect': 'Allow', 'Action': 's3:ListBucket', 'Resource': BUCKET_ARN},
    {'Sid': 'ReadReports', 'Effect': 'Allow', 'Action': 's3:GetObject', 'Resource': f'{BUCKET_ARN}/reports/*'},
)
REGION_GUARDRAIL = _doc(
    {'Sid': 'TeamQueues', 'Effect': 'Allow',
     'Action': ['sqs:CreateQueue', 'sqs:GetQueueUrl', 'sqs:SendMessage', 'sqs:DeleteQueue'],
     'Resource': f'arn:aws:sqs:*:{ACCOUNT}:lab-access-*'},
    {'Sid': 'OnlyApprovedRegions', 'Effect': 'Deny', 'NotAction': ['iam:*', 'sts:*', 'cloudfront:*', 'route53:*'],
     'Resource': '*', 'Condition': {'StringNotEquals': {'aws:RequestedRegion': [REGION]}}},
)
PARTNER_STATEMENT = {
    'Sid': 'PartnerReadReports', 'Effect': 'Allow', 'Principal': {'AWS': f'arn:aws:iam::{PARTNER_ACCOUNT}:root'},
    'Action': 's3:GetObject', 'Resource': f'{BUCKET_ARN}/reports/*',
}
TLS_STATEMENT = {
    'Sid': 'DenyInsecureTransport', 'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
    'Resource': [BUCKET_ARN, f'{BUCKET_ARN}/*'], 'Condition': {'Bool': {'aws:SecureTransport': 'false'}},
}
PACK_TEMPLATE = {
    'Resources': {
        name.replace('-', '').title(): {
            'Type': 'AWS::Config::ConfigRule',
            'Properties': {'ConfigRuleName': name, 'Source': {'Owner': 'AWS', 'SourceIdentifier': identifier},
                           'Scope': {'ComplianceResourceTypes': ['AWS::S3::Bucket']}},
        }
        for name, identifier in RULES.items()
    },
}

DENIED_COMMAND = (
    f'aws iam create-user --user-name {ANALYST}\n'
    f'aws iam put-user-policy --user-name {ANALYST} --policy-name {S3_POLICY} --policy-document file://list-only.json\n'
    f'aws iam create-access-key --user-name {ANALYST}\n'
    f'# as {ANALYST}:\n'
    f'aws s3api list-objects-v2 --bucket {BUCKET}            # works\n'
    f'aws s3api get-object --bucket {BUCKET} --key {REPORT_KEY} q3.csv   # AccessDenied\n'
    'aws cloudtrail lookup-events --lookup-attributes AttributeKey=Username,AttributeValue=' + ANALYST
)
SIMULATE_COMMAND = (
    f'aws iam simulate-principal-policy --policy-source-arn {ANALYST_ARN} \\\n'
    f'  --action-names s3:GetObject --resource-arns {BUCKET_ARN}/{REPORT_KEY}\n'
    'aws iam simulate-custom-policy --policy-input-list file://fixed.json \\\n'
    '  --action-names s3:GetObject s3:PutObject \\\n'
    f'  --resource-arns {BUCKET_ARN}/{REPORT_KEY} {BUCKET_ARN}/{PAYROLL_KEY}\n'
    f'aws iam put-user-policy --user-name {ANALYST} --policy-name {S3_POLICY} --policy-document file://fixed.json\n'
    f'# as {ANALYST}: get {REPORT_KEY} works; {PAYROLL_KEY} and a put stay denied'
)
REGION_COMMAND = (
    f'aws iam put-user-policy --user-name {ANALYST} --policy-name {REGION_POLICY} '
    '--policy-document file://region-guardrail.json\n'
    f'# as {ANALYST}:\n'
    f'aws sqs create-queue --queue-name {QUEUE} --region {REGION}        # works\n'
    f'aws sqs create-queue --queue-name {QUEUE} --region {OTHER_REGION}  # AccessDenied (explicit deny)'
)
KMS_COMMAND = (
    f'aws kms create-key --description "Reports database password"\n'
    f'aws kms create-alias --alias-name {KEY_ALIAS} --target-key-id <key-id>\n'
    f'aws kms encrypt --key-id {KEY_ALIAS} --plaintext fileb://password.txt\n'
    f'# as {ANALYST}: aws kms decrypt --ciphertext-blob fileb://password.enc   # AccessDeniedException\n'
    f'aws iam put-user-policy --user-name {ANALYST} --policy-name {KMS_POLICY} --policy-document file://kms.json\n'
    'aws kms disable-key --key-id <key-id>   # decrypt now fails with DisabledException\n'
    'aws kms enable-key --key-id <key-id>'
)
TLS_COMMAND = (
    f'aws acm request-certificate --domain-name {DOMAIN} --validation-method DNS\n'
    f'aws elbv2 create-load-balancer --name {LOAD_BALANCER} --subnets <subnet-a> <subnet-b>\n'
    f'aws elbv2 create-listener --load-balancer-arn <alb> --protocol HTTPS --port 443 \\\n'
    f'  --certificates CertificateArn=<cert> --ssl-policy {TLS_POLICY} \\\n'
    '  --default-actions Type=forward,TargetGroupArn=<tg>\n'
    'aws elbv2 create-listener --load-balancer-arn <alb> --protocol HTTP --port 80 \\\n'
    '  --default-actions \'Type=redirect,RedirectConfig={Protocol=HTTPS,Port=443,StatusCode=HTTP_301}\'\n'
    'aws acm delete-certificate --certificate-arn <cert>   # ResourceInUseException'
)
EXTERNAL_COMMAND = (
    f'aws accessanalyzer create-analyzer --analyzer-name {ANALYZER} --type ACCOUNT\n'
    f'aws s3api put-bucket-policy --bucket {BUCKET} --policy file://partner.json\n'
    'aws accessanalyzer list-findings --analyzer-arn <analyzer>\n'
    f'aws s3api put-bucket-policy --bucket {BUCKET} --policy file://no-partner.json'
)
COMPLIANCE_COMMAND = (
    f'aws configservice put-conformance-pack --conformance-pack-name {PACK} --template-body file://s3-baseline.yaml\n'
    'aws configservice describe-compliance-by-config-rule\n'
    f'aws s3api put-bucket-versioning --bucket {BUCKET} --versioning-configuration Status=Enabled\n'
    f'aws s3api put-bucket-policy --bucket {BUCKET} --policy file://tls-only.json\n'
    'aws configservice start-config-rules-evaluation --config-rule-names ' + ' '.join(RULES)
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Troubleshoot access: denied calls, the policy simulator, KMS, TLS, external access, and compliance',
    'description': (
        'Find why an analyst is denied a report and test the fix in the policy simulator before applying it, keep '
        'work in approved Regions with an explicit deny, fix a KMS decrypt that fails for two different reasons, '
        'put a TLS certificate on a load balancer, find a bucket shared with an unknown account, and fix the bucket '
        'until a conformance pack reports it compliant.'
    ),
    'steps': [
        {
            'key': 'denied-call',
            'title': 'Reproduce the denied request',
            'command': DENIED_COMMAND,
            'explanation': (
                f'{ANALYST} can list {BUCKET} but is denied {REPORT_KEY}. Listing a bucket is s3:ListBucket on the '
                'bucket ARN, and reading an object is s3:GetObject on the object ARN, so a policy that allows only '
                'the first fails on the second. On AWS, the next move is CloudTrail: the event for the call carries '
                'errorCode AccessDenied, the action, and the principal, and the error message names the policy type '
                'that denied it. '
                + FLOCI + 'the denial is real, decided by Floci\'s IAM evaluator. CloudTrail LookupEvents returns no '
                'events for denied calls, so the lab records the error from the response itself.'
            ),
        },
        {
            'key': 'simulate-fix',
            'title': 'Test the fix in the policy simulator, then apply it',
            'command': SIMULATE_COMMAND,
            'explanation': (
                'SimulatePrincipalPolicy confirms the current policy gives implicitDeny for s3:GetObject: nothing '
                'allows it, and nothing explicitly denies it. SimulateCustomPolicy then tests a least-privilege '
                'policy before anyone applies it: GetObject on reports/* only. It allows the report and denies '
                'payroll and every write. After applying it, the analyst reads the report and is still denied '
                f'{PAYROLL_KEY} and a put. '
                + FLOCI + 'both simulator calls evaluate the policies, and the requests that follow are enforced '
                'by object ARN.'
            ),
        },
        {
            'key': 'region-guardrail',
            'title': 'Keep work in approved Regions',
            'command': REGION_COMMAND,
            'explanation': (
                f'An explicit deny on every action except global services, when aws:RequestedRegion is not {REGION}, '
                f'lets the analyst create {QUEUE} in {REGION} and blocks the same call in {OTHER_REGION}. An explicit '
                'deny wins over any allow, so the same statement in a service control policy guards a whole '
                'account. NotAction leaves out IAM, STS, CloudFront, and Route 53 because their requests are '
                'served from us-east-1 no matter where the caller works. '
                + FLOCI + 'the aws:RequestedRegion condition key is evaluated on real requests.'
            ),
        },
        {
            'key': 'kms-troubleshoot',
            'title': 'Fix a KMS decrypt that fails for two reasons',
            'command': KMS_COMMAND,
            'explanation': (
                f'A password encrypted under {KEY_ALIAS} fails to decrypt for the analyst with '
                'AccessDeniedException: no policy grants kms:Decrypt. An identity policy that allows kms:Decrypt on '
                'the key ARN fixes it, because the default key policy lets IAM policies in the account grant access. '
                'Later the key is disabled and the same call fails with DisabledException, which no policy change '
                'fixes. Enabling the key again does. The two errors point to different fixes, so read the code. '
                + FLOCI + 'IAM grants on the key ARN and the disabled key are enforced. Floci ignores a deny in a '
                'key policy, so on Floci only IAM policies decide who can use a key.'
            ),
        },
        {
            'key': 'tls-certificate',
            'title': 'Serve the app over TLS with an ACM certificate',
            'command': TLS_COMMAND,
            'explanation': (
                f'ACM issues a certificate for {DOMAIN} once its DNS validation record exists, and renews it on its '
                f'own while that record stays. The load balancer {LOAD_BALANCER} gets an HTTPS listener with the '
                f'certificate and {TLS_POLICY}, which allows only TLS 1.2 and 1.3, and an HTTP listener that '
                'redirects to HTTPS with a 301. Deleting the certificate now fails with ResourceInUseException, '
                'because the listener uses it. '
                + FLOCI + 'the certificate is ISSUED at once, the listeners are stored with their policy and '
                'redirect, InUseBy names the load balancer, and the delete is refused. Floci does not terminate TLS.'
            ),
        },
        {
            'key': 'external-access',
            'title': 'Find and remove access from an unknown account',
            'command': EXTERNAL_COMMAND,
            'explanation': (
                f'An IAM Access Analyzer analyzer with the account as its zone of trust reports any resource policy '
                f'that grants access to a principal outside it. A bucket policy statement gives account '
                f'{PARTNER_ACCOUNT} read access to reports/*. That account is not on the approved list, so the '
                'finding is unintended: the statement is removed and the analyzer\'s rescan resolves it. An approved '
                'partner\'s finding would be archived instead. '
                + FLOCI + 'the analyzer is created and ACTIVE, but ListFindings is not available, so the lab scans '
                'the bucket policy for principals outside the account itself.'
            ),
        },
        {
            'key': 'compliance-pack',
            'title': 'Make the bucket pass a conformance pack',
            'command': COMPLIANCE_COMMAND,
            'explanation': (
                f'The conformance pack {PACK} deploys two AWS Config rules as one unit: versioning must be on, and '
                'the bucket policy must deny requests without TLS (aws:SecureTransport false). The bucket fails '
                'both. Turning on versioning and adding the TLS deny statement makes both rules COMPLIANT on the '
                'next evaluation. '
                + FLOCI + 'the pack is stored but its rules are not created or run, and Floci does not evaluate '
                'aws:SecureTransport. The lab creates the two rules as custom rules, evaluates the bucket itself, '
                'and records each result with PutEvaluations, which Config then reports.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str, region: str | None = None):
    factory = FlociClientFactory()
    if region:
        factory.region = region
    return factory.client(name)


def _analyst_client(name: str, region: str | None = None):
    key = _marked('access-key')
    if not key:
        raise ValueError('The analyst has no access key yet. Run the first step.')
    factory = FlociClientFactory(identity={
        'access_key_id': key['AccessKeyId'], 'secret_access_key': key['SecretAccessKey'], 'type': 'lab'})
    if region:
        factory.region = region
    return factory.client(name)


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


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError, IndexError):
        return default


def _attempt(call) -> dict[str, Any]:
    """Run one request and report whether it was allowed, and the error code when it was not."""
    try:
        call()
        return {'allowed': True}
    except ClientError as exc:
        return {'allowed': False, 'error': _code(exc)}


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Pure helpers ─────────────────────────────────────────────────────────────

def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def external_principals(policy: dict[str, Any] | None, account: str = ACCOUNT) -> list[str]:
    """Accounts outside the zone of trust that an Allow statement grants access to, the way Access Analyzer reads
    a resource policy. A wildcard principal counts as public."""
    found: list[str] = []
    for statement in (policy or {}).get('Statement', []):
        if statement.get('Effect') != 'Allow':
            continue
        principal = statement.get('Principal')
        values = ['*'] if principal == '*' else _as_list((principal or {}).get('AWS'))
        for value in values:
            if value == '*':
                outside = '*'
            elif value.isdigit():
                outside = value
            else:
                parts = value.split(':')
                outside = parts[4] if len(parts) > 4 else value
            if outside != account and outside not in found:
                found.append(outside)
    return found


def unintended(principals: list[str], approved: list[str] | None = None) -> list[str]:
    approved = APPROVED_ACCOUNTS if approved is None else approved
    return [item for item in principals if item not in approved]


def denies_insecure_transport(policy: dict[str, Any] | None) -> bool:
    """S3_BUCKET_SSL_REQUESTS_ONLY: a Deny for every principal when aws:SecureTransport is false."""
    for statement in (policy or {}).get('Statement', []):
        condition = (statement.get('Condition') or {}).get('Bool') or {}
        if (statement.get('Effect') == 'Deny' and statement.get('Principal') in ('*', {'AWS': '*'})
                and str(condition.get('aws:SecureTransport', '')).lower() == 'false'):
            return True
    return False


def evaluate_bucket(versioning: str | None, policy: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """The two conformance pack rules, evaluated the way AWS Config's managed rules decide them."""
    return {
        'lab-access-s3-versioning': {
            'compliance': 'COMPLIANT' if versioning == 'Enabled' else 'NON_COMPLIANT',
            'annotation': f'Versioning is {versioning or "off"}.',
        },
        'lab-access-s3-ssl-only': {
            'compliance': 'COMPLIANT' if denies_insecure_transport(policy) else 'NON_COMPLIANT',
            'annotation': 'The bucket policy denies requests without TLS.' if denies_insecure_transport(policy)
            else 'No statement denies aws:SecureTransport false.',
        },
    }


def decisions(results: list[dict[str, Any]]) -> dict[str, str]:
    """Simulator results keyed by action and resource."""
    return {f'{item["EvalActionName"]} {item.get("EvalResourceName", "*")}': item['EvalDecision']
            for item in results}


# ── Floci lookups ────────────────────────────────────────────────────────────

def _user() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('iam').get_user(UserName=ANALYST)['User'])


def _user_policy(name: str) -> dict[str, Any] | None:
    document = _safe_call(lambda: _client('iam').get_user_policy(UserName=ANALYST, PolicyName=name)['PolicyDocument'])
    if isinstance(document, str):
        document = json.loads(document)
    return document


def _bucket_exists() -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=BUCKET), None) is not None


def _bucket_policy() -> dict[str, Any] | None:
    text = _safe_call(lambda: _client('s3').get_bucket_policy(Bucket=BUCKET)['Policy'])
    return json.loads(text) if text else None


def _versioning() -> str | None:
    return _safe_call(lambda: _client('s3').get_bucket_versioning(Bucket=BUCKET).get('Status'))


def _key_id() -> str | None:
    return _safe_call(lambda: _client('kms').describe_key(KeyId=KEY_ALIAS)['KeyMetadata']['KeyId'])


def _key_state() -> str | None:
    return _safe_call(lambda: _client('kms').describe_key(KeyId=KEY_ALIAS)['KeyMetadata']['KeyState'])


def _certificate_arn() -> str | None:
    for item in _safe_call(lambda: _client('acm').list_certificates()['CertificateSummaryList'], []):
        if item.get('DomainName') == DOMAIN:
            return item['CertificateArn']
    return None


def _load_balancer() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('elbv2').describe_load_balancers(Names=[LOAD_BALANCER])['LoadBalancers'][0])


def _target_group() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('elbv2').describe_target_groups(Names=[TARGET_GROUP])['TargetGroups'][0])


def _listeners() -> list[dict[str, Any]]:
    balancer = _load_balancer()
    if not balancer:
        return []
    return _safe_call(lambda: _client('elbv2').describe_listeners(
        LoadBalancerArn=balancer['LoadBalancerArn'])['Listeners'], [])


def _analyzer() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('accessanalyzer').get_analyzer(analyzerName=ANALYZER)['analyzer'])


def _pack() -> dict[str, Any] | None:
    packs = _safe_call(lambda: _client('config').describe_conformance_packs(
        ConformancePackNames=[PACK])['ConformancePackDetails'], [])
    return packs[0] if packs else None


def _compliance() -> dict[str, str]:
    items = _safe_call(lambda: _client('config').describe_compliance_by_config_rule(
        ConfigRuleNames=list(RULES))['ComplianceByConfigRules'], [])
    return {item['ConfigRuleName']: item.get('Compliance', {}).get('ComplianceType') for item in items}


def _queue_exists(region: str) -> bool:
    return _safe_call(lambda: _client('sqs', region).get_queue_url(QueueName=QUEUE)['QueueUrl']) is not None


def _ciphertext() -> bytes | None:
    blob = _marked('ciphertext')
    return base64.b64decode(blob) if blob else None


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_denied() -> tuple[bool, str]:
    record = _marked('denied-call') or {}
    if not _user() or not _bucket_exists():
        return False, f'{ANALYST} or {BUCKET} is missing.'
    if not record.get('list', {}).get('allowed') or record.get('get', {}).get('error') != 'AccessDenied':
        return False, f'The analyst should list {BUCKET} and be denied {REPORT_KEY}.'
    return True, f'{ANALYST} lists {BUCKET} and is denied {REPORT_KEY} with AccessDenied.'


def _check_simulate() -> tuple[bool, str]:
    record = _marked('simulate-fix') or {}
    if record.get('before') != 'implicitDeny':
        return False, 'The simulator has not shown the implicit deny for the current policy.'
    if _user_policy(S3_POLICY) != FIXED_POLICY:
        return False, 'The least-privilege policy is not applied.'
    analyst = _analyst_client('s3')
    report = _attempt(lambda: analyst.get_object(Bucket=BUCKET, Key=REPORT_KEY))
    payroll = _attempt(lambda: analyst.get_object(Bucket=BUCKET, Key=PAYROLL_KEY))
    upload = _attempt(lambda: analyst.put_object(Bucket=BUCKET, Key=UPLOAD_KEY, Body=b'draft'))
    if not report['allowed'] or payroll['allowed'] or upload['allowed']:
        return False, f'Expected the report allowed and payroll and put denied: {report}, {payroll}, {upload}.'
    return True, 'The simulator predicted the fix, and the analyst reads reports only.'


def _check_region() -> tuple[bool, str]:
    if _user_policy(REGION_POLICY) != REGION_GUARDRAIL:
        return False, 'The Region guardrail policy is not attached to the analyst.'
    if not _queue_exists(REGION):
        return False, f'{QUEUE} does not exist in {REGION}.'
    west = _attempt(lambda: _analyst_client('sqs', OTHER_REGION).get_queue_url(QueueName=QUEUE))
    if west['allowed'] or _queue_exists(OTHER_REGION):
        return False, f'The analyst can still work in {OTHER_REGION}.'
    return True, f'The analyst works in {REGION} and is denied {OTHER_REGION}.'


def _check_kms() -> tuple[bool, str]:
    record = _marked('kms-troubleshoot') or {}
    blob = _ciphertext()
    if not blob or _key_state() != 'Enabled':
        return False, f'{KEY_ALIAS} is missing or not enabled.'
    if record.get('before', {}).get('error') != 'AccessDeniedException' or \
            record.get('disabled', {}).get('error') != 'DisabledException':
        return False, 'Both decrypt failures have not been reproduced.'
    try:
        plaintext = _analyst_client('kms').decrypt(CiphertextBlob=blob)['Plaintext']
    except ClientError as exc:
        return False, f'The analyst still cannot decrypt: {_code(exc)}.'
    if plaintext != SECRET_TEXT:
        return False, 'The decrypted value does not match.'
    return True, 'Access was granted on the key ARN, the key is enabled again, and the analyst decrypts.'


def _check_tls() -> tuple[bool, str]:
    arn = _certificate_arn()
    if not arn:
        return False, f'There is no certificate for {DOMAIN}.'
    certificate = _client('acm').describe_certificate(CertificateArn=arn)['Certificate']
    listeners = _listeners()
    https = [item for item in listeners if item.get('Protocol') == 'HTTPS' and item.get('SslPolicy') == TLS_POLICY
             and any(cert.get('CertificateArn') == arn for cert in item.get('Certificates', []))]
    redirect = [item for item in listeners if item.get('Protocol') == 'HTTP' and any(
        action.get('Type') == 'redirect' and action.get('RedirectConfig', {}).get('Protocol') == 'HTTPS'
        for action in item.get('DefaultActions', []))]
    if certificate.get('Status') != 'ISSUED' or not https or not redirect:
        return False, 'The certificate, the HTTPS listener, or the HTTP redirect is missing.'
    if (_marked('tls-certificate') or {}).get('delete', {}).get('error') != 'ResourceInUseException':
        return False, 'Deleting the certificate in use has not been tried.'
    return True, f'{LOAD_BALANCER} serves HTTPS with {TLS_POLICY} and redirects HTTP; the certificate is in use.'


def _check_external() -> tuple[bool, str]:
    analyzer = _analyzer()
    if not analyzer or analyzer.get('status') != 'ACTIVE':
        return False, f'The analyzer {ANALYZER} is not active.'
    if PARTNER_ACCOUNT not in ((_marked('external-access') or {}).get('found') or []):
        return False, f'The finding for account {PARTNER_ACCOUNT} has not been reported.'
    remaining = unintended(external_principals(_bucket_policy()))
    if remaining:
        return False, f'The bucket policy still grants access to {", ".join(remaining)}.'
    return True, f'Access for {PARTNER_ACCOUNT} was found and removed; no unintended external access remains.'


def _check_compliance() -> tuple[bool, str]:
    if not _pack():
        return False, f'The conformance pack {PACK} is missing.'
    if (_marked('compliance-pack') or {}).get('before') != ['NON_COMPLIANT', 'NON_COMPLIANT']:
        return False, 'The first evaluation has not recorded the bucket as noncompliant.'
    live = evaluate_bucket(_versioning(), _bucket_policy())
    recorded = _compliance()
    failing = [name for name in RULES if live[name]['compliance'] != 'COMPLIANT' or recorded.get(name) != 'COMPLIANT']
    if failing:
        return False, f'Not compliant yet: {", ".join(failing)}.'
    return True, f'Both rules in {PACK} report {BUCKET} COMPLIANT.'


CHECKS = {
    'denied-call': _check_denied,
    'simulate-fix': _check_simulate,
    'region-guardrail': _check_region,
    'kms-troubleshoot': _check_kms,
    'tls-certificate': _check_tls,
    'external-access': _check_external,
    'compliance-pack': _check_compliance,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _ensure_bucket() -> None:
    s3 = _client('s3')
    if not _bucket_exists():
        s3.create_bucket(Bucket=BUCKET)
    s3.put_object(Bucket=BUCKET, Key=REPORT_KEY, Body=b'region,revenue\nus,1200\neu,900\n')
    s3.put_object(Bucket=BUCKET, Key=PAYROLL_KEY, Body=b'name,salary\nana,100\n')


def _ensure_access_key() -> None:
    iam = _client('iam')
    key = _marked('access-key')
    live = [item['AccessKeyId'] for item in _safe_call(
        lambda: iam.list_access_keys(UserName=ANALYST)['AccessKeyMetadata'], [])]
    if key and key['AccessKeyId'] in live:
        return
    for key_id in live:
        iam.delete_access_key(UserName=ANALYST, AccessKeyId=key_id)
    created = iam.create_access_key(UserName=ANALYST)['AccessKey']
    _marker('access-key', {'AccessKeyId': created['AccessKeyId'], 'SecretAccessKey': created['SecretAccessKey']})


def step_denied_call() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    _ensure_bucket()
    if not _user():
        iam.create_user(UserName=ANALYST)
    iam.put_user_policy(UserName=ANALYST, PolicyName=S3_POLICY, PolicyDocument=json.dumps(LIST_ONLY_POLICY))
    _ensure_access_key()
    analyst = _analyst_client('s3')
    listed = _attempt(lambda: analyst.list_objects_v2(Bucket=BUCKET))
    if not listed['allowed']:
        # A new access key can take a moment to be accepted.
        _wait(lambda: _attempt(lambda: analyst.list_objects_v2(Bucket=BUCKET))['allowed'], 10)
        listed = _attempt(lambda: analyst.list_objects_v2(Bucket=BUCKET))
    denied = _attempt(lambda: analyst.get_object(Bucket=BUCKET, Key=REPORT_KEY))
    events = _safe_call(lambda: _client('cloudtrail').lookup_events(
        LookupAttributes=[{'AttributeKey': 'Username', 'AttributeValue': ANALYST}])['Events'], [])
    record = {'list': listed, 'get': denied, 'cloudTrailEvents': len(events)}
    _marker('denied-call', record)
    response = {'Policy': LIST_ONLY_POLICY, 'ListObjectsV2': listed, 'GetObject': denied,
                'CloudTrail': {'EventsForAnalyst': len(events),
                               'Note': 'Floci records no denied calls; the error above is the evidence.'}}
    verified, message = _check_denied()
    return _result('denied-call', DENIED_COMMAND, response, verified, message, started)


def step_simulate_fix() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    report_arn, payroll_arn = f'{BUCKET_ARN}/{REPORT_KEY}', f'{BUCKET_ARN}/{PAYROLL_KEY}'
    iam.put_user_policy(UserName=ANALYST, PolicyName=S3_POLICY, PolicyDocument=json.dumps(LIST_ONLY_POLICY))
    current = iam.simulate_principal_policy(PolicySourceArn=ANALYST_ARN, ActionNames=['s3:GetObject'],
                                            ResourceArns=[report_arn])['EvaluationResults']
    proposed = iam.simulate_custom_policy(PolicyInputList=[json.dumps(FIXED_POLICY)],
                                          ActionNames=['s3:GetObject', 's3:PutObject'],
                                          ResourceArns=[report_arn, payroll_arn])['EvaluationResults']
    iam.put_user_policy(UserName=ANALYST, PolicyName=S3_POLICY, PolicyDocument=json.dumps(FIXED_POLICY))
    before = current[0]['EvalDecision'] if current else None
    _marker('simulate-fix', {'before': before, 'proposed': decisions(proposed)})
    analyst = _analyst_client('s3')
    response = {
        'CurrentPolicy': decisions(current),
        'ProposedPolicy': decisions(proposed),
        'AppliedPolicy': FIXED_POLICY,
        'AfterFix': {
            REPORT_KEY: _attempt(lambda: analyst.get_object(Bucket=BUCKET, Key=REPORT_KEY)),
            PAYROLL_KEY: _attempt(lambda: analyst.get_object(Bucket=BUCKET, Key=PAYROLL_KEY)),
            f'put {UPLOAD_KEY}': _attempt(lambda: analyst.put_object(Bucket=BUCKET, Key=UPLOAD_KEY, Body=b'x')),
        },
    }
    verified, message = _check_simulate()
    return _result('simulate-fix', SIMULATE_COMMAND, response, verified, message, started)


def step_region_guardrail() -> dict[str, Any]:
    started = time.perf_counter()
    _client('iam').put_user_policy(UserName=ANALYST, PolicyName=REGION_POLICY,
                                   PolicyDocument=json.dumps(REGION_GUARDRAIL))
    east = _attempt(lambda: _analyst_client('sqs', REGION).create_queue(QueueName=QUEUE))
    west = _attempt(lambda: _analyst_client('sqs', OTHER_REGION).create_queue(QueueName=QUEUE))
    response = {'Policy': REGION_GUARDRAIL, REGION: east, OTHER_REGION: west}
    verified, message = _check_region()
    return _result('region-guardrail', REGION_COMMAND, response, verified, message, started)


def step_kms_troubleshoot() -> dict[str, Any]:
    started = time.perf_counter()
    kms, iam = _client('kms'), _client('iam')
    key_id = _key_id()
    if not key_id:
        key_id = kms.create_key(Description='Reports database password')['KeyMetadata']['KeyId']
        kms.create_alias(AliasName=KEY_ALIAS, TargetKeyId=key_id)
    if _key_state() == 'Disabled':
        kms.enable_key(KeyId=key_id)
    key_arn = kms.describe_key(KeyId=key_id)['KeyMetadata']['Arn']
    blob = kms.encrypt(KeyId=KEY_ALIAS, Plaintext=SECRET_TEXT)['CiphertextBlob']
    _marker('ciphertext', base64.b64encode(blob).decode())
    _safe_call(lambda: iam.delete_user_policy(UserName=ANALYST, PolicyName=KMS_POLICY))
    before = _attempt(lambda: _analyst_client('kms').decrypt(CiphertextBlob=blob))
    grant = _doc({'Sid': 'DecryptReportsSecret', 'Effect': 'Allow', 'Action': 'kms:Decrypt', 'Resource': key_arn})
    iam.put_user_policy(UserName=ANALYST, PolicyName=KMS_POLICY, PolicyDocument=json.dumps(grant))
    granted = _attempt(lambda: _analyst_client('kms').decrypt(CiphertextBlob=blob))
    kms.disable_key(KeyId=key_id)
    disabled = _attempt(lambda: _analyst_client('kms').decrypt(CiphertextBlob=blob))
    kms.enable_key(KeyId=key_id)
    enabled = _attempt(lambda: _analyst_client('kms').decrypt(CiphertextBlob=blob))
    _marker('kms-troubleshoot', {'before': before, 'disabled': disabled})
    response = {'KeyArn': key_arn, 'NoPermission': before, 'GrantOnKeyArn': grant, 'AfterGrant': granted,
                'KeyDisabled': disabled, 'KeyEnabledAgain': enabled}
    verified, message = _check_kms()
    return _result('kms-troubleshoot', KMS_COMMAND, response, verified, message, started)


def _default_subnets() -> list[str]:
    subnets = _client('ec2').describe_subnets(Filters=[{'Name': 'default-for-az', 'Values': ['true']}])['Subnets']
    by_zone: dict[str, str] = {}
    for subnet in subnets:
        by_zone.setdefault(subnet['AvailabilityZone'], subnet['SubnetId'])
    return list(by_zone.values())[:2]


def step_tls_certificate() -> dict[str, Any]:
    started = time.perf_counter()
    acm, elb = _client('acm'), _client('elbv2')
    arn = _certificate_arn() or acm.request_certificate(DomainName=DOMAIN, ValidationMethod='DNS')['CertificateArn']
    _wait(lambda: acm.describe_certificate(CertificateArn=arn)['Certificate'].get('Status') == 'ISSUED', 30)
    balancer = _load_balancer() or elb.create_load_balancer(
        Name=LOAD_BALANCER, Subnets=_default_subnets(), Scheme='internet-facing', Type='application')[
        'LoadBalancers'][0]
    group = _target_group() or elb.create_target_group(
        Name=TARGET_GROUP, Protocol='HTTP', Port=80, VpcId=balancer['VpcId'])['TargetGroups'][0]
    protocols = {item.get('Protocol') for item in _listeners()}
    if 'HTTPS' not in protocols:
        elb.create_listener(LoadBalancerArn=balancer['LoadBalancerArn'], Protocol='HTTPS', Port=443,
                            Certificates=[{'CertificateArn': arn}], SslPolicy=TLS_POLICY,
                            DefaultActions=[{'Type': 'forward', 'TargetGroupArn': group['TargetGroupArn']}])
    if 'HTTP' not in protocols:
        elb.create_listener(LoadBalancerArn=balancer['LoadBalancerArn'], Protocol='HTTP', Port=80,
                            DefaultActions=[{'Type': 'redirect', 'RedirectConfig': {
                                'Protocol': 'HTTPS', 'Port': '443', 'StatusCode': 'HTTP_301'}}])
    delete = _attempt(lambda: acm.delete_certificate(CertificateArn=arn))
    _marker('tls-certificate', {'delete': delete})
    certificate = acm.describe_certificate(CertificateArn=arn)['Certificate']
    response = {
        'Certificate': {key: certificate.get(key) for key in (
            'CertificateArn', 'DomainName', 'Status', 'NotAfter', 'InUseBy', 'RenewalEligibility')},
        'DnsValidation': [option.get('ResourceRecord') for option in certificate.get('DomainValidationOptions', [])],
        'Listeners': [{key: item.get(key) for key in ('Protocol', 'Port', 'SslPolicy', 'DefaultActions')}
                      for item in _listeners()],
        'DeleteCertificateInUse': delete,
    }
    verified, message = _check_tls()
    return _result('tls-certificate', TLS_COMMAND, response, verified, message, started)


def _put_bucket_policy(statements: list[dict[str, Any]]) -> None:
    s3 = _client('s3')
    if statements:
        s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(_doc(*statements)))
    else:
        _safe_call(lambda: s3.delete_bucket_policy(Bucket=BUCKET))


def _statements_without(sid: str) -> list[dict[str, Any]]:
    return [item for item in (_bucket_policy() or {}).get('Statement', []) if item.get('Sid') != sid]


def step_external_access() -> dict[str, Any]:
    started = time.perf_counter()
    analyzer = _analyzer() or _client('accessanalyzer').create_analyzer(analyzerName=ANALYZER, type='ACCOUNT')
    _put_bucket_policy(_statements_without(PARTNER_STATEMENT['Sid']) + [PARTNER_STATEMENT])
    found = external_principals(_bucket_policy())
    flagged = unintended(found)
    _marker('external-access', {'found': found, 'unintended': flagged})
    _put_bucket_policy(_statements_without(PARTNER_STATEMENT['Sid']))
    response = {
        'Analyzer': {'name': ANALYZER, 'type': 'ACCOUNT', 'status': (_analyzer() or analyzer).get('status')},
        'Finding': {'resource': BUCKET_ARN, 'principals': found, 'unintended': flagged,
                    'statement': PARTNER_STATEMENT, 'action': 'removed'},
        'RescanFindings': external_principals(_bucket_policy()),
    }
    verified, message = _check_external()
    return _result('external-access', EXTERNAL_COMMAND, response, verified, message, started)


def _record(results: dict[str, dict[str, str]]) -> None:
    config = _client('config')
    now = datetime.datetime.now(datetime.timezone.utc)
    for name, outcome in results.items():
        config.put_evaluations(ResultToken=name, Evaluations=[{
            'ComplianceResourceType': 'AWS::S3::Bucket', 'ComplianceResourceId': BUCKET,
            'ComplianceType': outcome['compliance'], 'Annotation': outcome['annotation'],
            'OrderingTimestamp': now}])


def step_compliance_pack() -> dict[str, Any]:
    started = time.perf_counter()
    config, s3 = _client('config'), _client('s3')
    config.put_conformance_pack(ConformancePackName=PACK, TemplateBody=json.dumps(PACK_TEMPLATE))
    for name, identifier in RULES.items():
        config.put_config_rule(ConfigRule={
            'ConfigRuleName': name, 'Description': f'Lab evaluation of {identifier}',
            'Scope': {'ComplianceResourceTypes': ['AWS::S3::Bucket']},
            'Source': {'Owner': 'CUSTOM_LAMBDA',
                       'SourceIdentifier': f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:lab-access-evaluator',
                       'SourceDetails': [{'EventSource': 'aws.config',
                                          'MessageType': 'ConfigurationItemChangeNotification'}]}})
    if _versioning() == 'Enabled':
        s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={'Status': 'Suspended'})
    _put_bucket_policy(_statements_without(TLS_STATEMENT['Sid']))
    before = evaluate_bucket(_versioning(), _bucket_policy())
    _record(before)
    recorded_before = _compliance()
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={'Status': 'Enabled'})
    _put_bucket_policy(_statements_without(TLS_STATEMENT['Sid']) + [TLS_STATEMENT])
    after = evaluate_bucket(_versioning(), _bucket_policy())
    _record(after)
    _marker('compliance-pack', {'before': [before[name]['compliance'] for name in RULES]})
    response = {'ConformancePack': _pack(), 'Before': before, 'RecordedBefore': recorded_before,
                'Fixes': {'Versioning': 'Enabled', 'BucketPolicyStatement': TLS_STATEMENT},
                'After': after, 'RecordedAfter': _compliance()}
    verified, message = _check_compliance()
    return _result('compliance-pack', COMPLIANCE_COMMAND, response, verified, message, started)


RUNNERS = {
    'denied-call': step_denied_call,
    'simulate-fix': step_simulate_fix,
    'region-guardrail': step_region_guardrail,
    'kms-troubleshoot': step_kms_troubleshoot,
    'tls-certificate': step_tls_certificate,
    'external-access': step_external_access,
    'compliance-pack': step_compliance_pack,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check()[0])
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError):
        return False


def status() -> dict[str, Any]:
    steps = {}
    for step in STEP_KEYS:
        verified = _safe(CHECKS[step])
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
    """Remove the conformance pack and rules, analyzer, load balancer, certificate, key, queues, bucket, and user."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or 'NonExistent' in code:
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    config = _client('config')
    if _pack():
        attempt(f'conformance-pack:{PACK}', lambda: config.delete_conformance_pack(ConformancePackName=PACK))
    existing_rules = {item['ConfigRuleName'] for item in _safe_call(
        lambda: config.describe_config_rules()['ConfigRules'], [])}
    for name in RULES:
        if name in existing_rules:
            attempt(f'config-rule:{name}', lambda name=name: config.delete_config_rule(ConfigRuleName=name))
    if _analyzer():
        attempt(f'analyzer:{ANALYZER}', lambda: _client('accessanalyzer').delete_analyzer(analyzerName=ANALYZER))

    elb = _client('elbv2')
    for listener in _listeners():
        attempt(f'listener:{listener.get("Protocol")}', lambda listener=listener: elb.delete_listener(
            ListenerArn=listener['ListenerArn']))
    balancer = _load_balancer()
    if balancer:
        attempt(f'load-balancer:{LOAD_BALANCER}', lambda: elb.delete_load_balancer(
            LoadBalancerArn=balancer['LoadBalancerArn']))
    group = _target_group()
    if group:
        attempt(f'target-group:{TARGET_GROUP}', lambda: elb.delete_target_group(
            TargetGroupArn=group['TargetGroupArn']))
    certificate = _certificate_arn()
    if certificate:
        attempt(f'certificate:{DOMAIN}', lambda: _client('acm').delete_certificate(CertificateArn=certificate))

    kms = _client('kms')
    key_id = _key_id()
    if key_id:
        attempt(f'alias:{KEY_ALIAS}', lambda: kms.delete_alias(AliasName=KEY_ALIAS))
        attempt(f'key:{key_id}', lambda: kms.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=7))

    for region in (REGION, OTHER_REGION):
        sqs = _client('sqs', region)
        url = _safe_call(lambda sqs=sqs: sqs.get_queue_url(QueueName=QUEUE)['QueueUrl'])
        if url:
            attempt(f'queue:{region}/{QUEUE}', lambda sqs=sqs, url=url: sqs.delete_queue(QueueUrl=url))

    s3 = _client('s3')
    if _bucket_exists():
        versions = _safe_call(lambda: s3.list_object_versions(Bucket=BUCKET), {}) or {}
        for item in versions.get('Versions', []) + versions.get('DeleteMarkers', []):
            attempt(f'object:{item["Key"]}@{item["VersionId"]}', lambda item=item: s3.delete_object(
                Bucket=BUCKET, Key=item['Key'], VersionId=item['VersionId']))
        for item in _safe_call(lambda: s3.list_objects_v2(Bucket=BUCKET).get('Contents', []), []):
            attempt(f'object:{item["Key"]}', lambda item=item: s3.delete_object(Bucket=BUCKET, Key=item['Key']))
        _safe_call(lambda: s3.delete_bucket_policy(Bucket=BUCKET))
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))

    iam = _client('iam')
    if _user():
        for policy in _safe_call(lambda: iam.list_user_policies(UserName=ANALYST)['PolicyNames'], []):
            attempt(f'user-policy:{policy}', lambda policy=policy: iam.delete_user_policy(
                UserName=ANALYST, PolicyName=policy))
        for key in _safe_call(lambda: iam.list_access_keys(UserName=ANALYST)['AccessKeyMetadata'], []):
            attempt(f'access-key:{key["AccessKeyId"]}', lambda key=key: iam.delete_access_key(
                UserName=ANALYST, AccessKeyId=key['AccessKeyId']))
        attempt(f'user:{ANALYST}', lambda: iam.delete_user(UserName=ANALYST))

    for step in STEP_KEYS + ['access-key', 'ciphertext']:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws configservice delete-conformance-pack --conformance-pack-name {PACK}\n'
            + ''.join(f'aws configservice delete-config-rule --config-rule-name {name}\n' for name in RULES)
            + f'aws accessanalyzer delete-analyzer --analyzer-name {ANALYZER}\n'
            f'aws elbv2 delete-load-balancer --load-balancer-arn <{LOAD_BALANCER}>   # after its listeners\n'
            f'aws elbv2 delete-target-group --target-group-arn <{TARGET_GROUP}>\n'
            'aws acm delete-certificate --certificate-arn <cert>\n'
            f'aws kms delete-alias --alias-name {KEY_ALIAS}\n'
            'aws kms schedule-key-deletion --key-id <key-id> --pending-window-in-days 7\n'
            f'aws sqs delete-queue --queue-url <{QUEUE}>\n'
            f'aws s3 rb s3://{BUCKET} --force\n'
            f'aws iam delete-user --user-name {ANALYST}   # after its policies and access keys'
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
            else 'The pack, rules, analyzer, load balancer, certificate, key, queues, bucket, and user are gone.',
        },
    }
