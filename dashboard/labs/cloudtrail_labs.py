"""Governance services tour: a CloudTrail trail delivering to a locked-down S3 bucket, S3 data events on a sensitive
bucket, an AWS Config recorder and a managed rule, a GuardDuty detector with an EventBridge rule for high-severity
findings, Security Hub with its default standards, and a "which service answers which question" chooser.

Floci 2.3.0 stores all of this but simulates little: trails report IsLogging but deliver no log files and
LookupEvents returns nothing; the Config recorder records nothing and rules stay INSUFFICIENT_DATA; GuardDuty creates
detectors but its findings API is misrouted; Security Hub enables but its standards API is misrouted; and EventBridge
does not support numeric matching. The lab checks the bucket policy, evaluates the Config rule against the lab
buckets, and matches sample findings against the rule's pattern itself.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'cloudtrail'
LAB_KEY = 'governance-tour'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

TRAIL_BUCKET = 'lab-governance-trail-logs'
PAYROLL_BUCKET = 'lab-governance-payroll'
TRAIL = 'lab-governance-trail'
TRAIL_ARN = f'arn:aws:cloudtrail:{REGION}:{ACCOUNT}:trail/{TRAIL}'
TRAIL_PREFIX = f'AWSLogs/{ACCOUNT}/'
CONFIG_PREFIX = 'config'
TRAIL_BUCKET_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'AWSCloudTrailAclCheck', 'Effect': 'Allow', 'Principal': {'Service': 'cloudtrail.amazonaws.com'},
         'Action': 's3:GetBucketAcl', 'Resource': f'arn:aws:s3:::{TRAIL_BUCKET}',
         'Condition': {'StringEquals': {'aws:SourceArn': TRAIL_ARN}}},
        {'Sid': 'AWSCloudTrailWrite', 'Effect': 'Allow', 'Principal': {'Service': 'cloudtrail.amazonaws.com'},
         'Action': 's3:PutObject', 'Resource': f'arn:aws:s3:::{TRAIL_BUCKET}/{TRAIL_PREFIX}*',
         'Condition': {'StringEquals': {'s3:x-amz-acl': 'bucket-owner-full-control', 'aws:SourceArn': TRAIL_ARN}}},
        {'Sid': 'AWSConfigWrite', 'Effect': 'Allow', 'Principal': {'Service': 'config.amazonaws.com'},
         'Action': ['s3:GetBucketAcl', 's3:PutObject'],
         'Resource': [f'arn:aws:s3:::{TRAIL_BUCKET}', f'arn:aws:s3:::{TRAIL_BUCKET}/{CONFIG_PREFIX}/*']},
        {'Sid': 'DenyInsecureTransport', 'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
         'Resource': [f'arn:aws:s3:::{TRAIL_BUCKET}', f'arn:aws:s3:::{TRAIL_BUCKET}/*'],
         'Condition': {'Bool': {'aws:SecureTransport': 'false'}}},
    ],
}

CONFIG_ROLE = 'lab-governance-config-role'
CONFIG_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{CONFIG_ROLE}'
CONFIG_POLICY_ARN = 'arn:aws:iam::aws:policy/service-role/AWS_ConfigRole'
CONFIG_TRUST = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {
    'Service': 'config.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
RECORDER = 'default'
CHANNEL = 'default'
CONFIG_RULE = 'lab-governance-s3-versioning'
CONFIG_SOURCE = 'S3_BUCKET_VERSIONING_ENABLED'
EXPECTED_COMPLIANCE = {TRAIL_BUCKET: 'COMPLIANT', PAYROLL_BUCKET: 'NON_COMPLIANT'}

TOPIC = 'lab-governance-alerts'
FINDINGS_RULE = 'lab-governance-guardduty-high'
MIN_SEVERITY = 7
FINDINGS_PATTERN = {'source': ['aws.guardduty'], 'detail-type': ['GuardDuty Finding'],
                    'detail': {'severity': [{'numeric': ['>=', MIN_SEVERITY]}]}}
HUB_RULE = 'lab-governance-securityhub-critical'
HUB_PATTERN = {'source': ['aws.securityhub'], 'detail-type': ['Security Hub Findings - Imported'],
               'detail': {'findings': {'Severity': {'Label': ['CRITICAL', 'HIGH']},
                                       'Workflow': {'Status': ['NEW']}}}}
SAMPLE_FINDINGS = [
    ('CryptoCurrency:EC2/BitcoinTool.B!DNS', 8.0),
    ('UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration.OutsideAWS', 8.0),
    ('Recon:EC2/PortProbeUnprotectedPort', 2.0),
    ('Policy:S3/BucketBlockPublicAccessDisabled', 5.0),
]

QUESTIONS = [
    ('Who deleted the payroll bucket policy last night, and from which IP address?', 'CloudTrail', 'trail'),
    ('Which buckets have versioning turned off, and when did that change?', 'AWS Config', 'config'),
    ('Is an EC2 instance talking to a known cryptocurrency mining domain?', 'GuardDuty', 'guardduty'),
    ('Where can I see findings from GuardDuty, Inspector, Macie and Config scored against the CIS benchmark?',
     'Security Hub', 'securityhub'),
    ('Does any S3 bucket hold credit card numbers?', 'Macie', None),
    ('Which EC2 instances and container images have unpatched CVEs?', 'Inspector', None),
    ('Where do I download the AWS SOC 2 report for my auditor?', 'AWS Artifact', None),
    ('Is CPU above 80% on the web tier right now?', 'CloudWatch', None),
]

BUCKET_COMMAND = (
    f'aws s3api create-bucket --bucket {TRAIL_BUCKET}\n'
    f'aws s3api put-public-access-block --bucket {TRAIL_BUCKET} --public-access-block-configuration '
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true\n'
    f'aws s3api put-bucket-versioning --bucket {TRAIL_BUCKET} --versioning-configuration Status=Enabled\n'
    f'aws s3api put-bucket-policy --bucket {TRAIL_BUCKET} --policy file://trail-bucket-policy.json'
)
TRAIL_COMMAND = (
    f'aws cloudtrail create-trail --name {TRAIL} --s3-bucket-name {TRAIL_BUCKET} --is-multi-region-trail '
    '--include-global-service-events --enable-log-file-validation\n'
    f'aws cloudtrail start-logging --name {TRAIL}\n'
    'aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=CreateBucket'
)
DATA_EVENTS_COMMAND = (
    f'aws s3api create-bucket --bucket {PAYROLL_BUCKET}\n'
    f'aws cloudtrail put-event-selectors --trail-name {TRAIL} --event-selectors '
    f"'[{{\"ReadWriteType\":\"All\",\"IncludeManagementEvents\":true,\"DataResources\":[{{\"Type\":\"AWS::S3::Object\","
    f"\"Values\":[\"arn:aws:s3:::{PAYROLL_BUCKET}/\"]}}]}}]'"
)
RECORDER_COMMAND = (
    f'aws iam create-role --role-name {CONFIG_ROLE} --assume-role-policy-document file://config-trust.json\n'
    f'aws iam attach-role-policy --role-name {CONFIG_ROLE} --policy-arn {CONFIG_POLICY_ARN}\n'
    f'aws configservice put-configuration-recorder --configuration-recorder name={RECORDER},roleARN={CONFIG_ROLE_ARN} '
    '--recording-group allSupported=true,includeGlobalResourceTypes=true\n'
    f'aws configservice put-delivery-channel --delivery-channel name={CHANNEL},s3BucketName={TRAIL_BUCKET},'
    f's3KeyPrefix={CONFIG_PREFIX}\n'
    f'aws configservice start-configuration-recorder --configuration-recorder-name {RECORDER}'
)
RULE_COMMAND = (
    f'aws configservice put-config-rule --config-rule \'{{"ConfigRuleName":"{CONFIG_RULE}","Source":{{"Owner":"AWS",'
    f'"SourceIdentifier":"{CONFIG_SOURCE}"}},"Scope":{{"ComplianceResourceTypes":["AWS::S3::Bucket"]}}}}\'\n'
    f'aws configservice start-config-rules-evaluation --config-rule-names {CONFIG_RULE}\n'
    f'aws configservice get-compliance-details-by-config-rule --config-rule-name {CONFIG_RULE}'
)
GUARDDUTY_COMMAND = (
    'aws guardduty create-detector --enable --finding-publishing-frequency FIFTEEN_MINUTES\n'
    f'aws sns create-topic --name {TOPIC}\n'
    f'aws events put-rule --name {FINDINGS_RULE} --event-pattern file://guardduty-high.json\n'
    f'aws events put-targets --rule {FINDINGS_RULE} --targets Id=alerts,Arn=<topic-arn>\n'
    'aws guardduty create-sample-findings --detector-id <id> --finding-types CryptoCurrency:EC2/BitcoinTool.B!DNS'
)
SECURITYHUB_COMMAND = (
    'aws securityhub enable-security-hub --enable-default-standards\n'
    'aws securityhub describe-hub\n'
    f'aws events put-rule --name {HUB_RULE} --event-pattern file://securityhub-critical.json\n'
    f'aws events put-targets --rule {HUB_RULE} --targets Id=alerts,Arn=<topic-arn>\n'
    'aws securityhub get-enabled-standards'
)
CHOOSER_COMMAND = '# Match each question to the service that answers it, then check each one is running'

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Audit and secure an account with CloudTrail, Config, GuardDuty, and Security Hub',
    'description': (
        'Deliver a multi-Region CloudTrail trail with log file validation to a locked-down bucket, log S3 data '
        'events for a payroll bucket, record resource changes with AWS Config and check bucket versioning with a '
        'managed rule, route high-severity GuardDuty findings to SNS, enable Security Hub and alert on its critical '
        'findings, and match governance '
        'questions to the service that answers each one.'
    ),
    'steps': [
        {
            'key': 'trail-bucket',
            'title': 'Prepare a locked-down bucket for audit logs',
            'command': BUCKET_COMMAND,
            'explanation': (
                'CloudTrail and Config write to S3 only when the bucket policy lets their service principals do so, '
                'scoped with aws:SourceArn so no other trail can write there. Audit logs also need Block Public '
                'Access, versioning, and HTTPS-only access (often in a separate log archive account). How Floci '
                'emulates this: the bucket settings are real; Floci never checks the policy when a trail is '
                'created, so the step checks the required statements itself.'
            ),
        },
        {
            'key': 'cloudtrail-trail',
            'title': 'Record every API call with a multi-Region trail',
            'command': TRAIL_COMMAND,
            'explanation': (
                'Event history keeps 90 days of management events for free; a trail keeps them in S3 for as long as '
                'you like, from every Region, including global services such as IAM. Log file validation adds signed '
                'digest files that prove no log was changed or deleted. How Floci emulates this: the trail is stored '
                'and reports IsLogging, but Floci delivers no log files and LookupEvents returns no events; the step '
                'checks the trail settings and records that.'
            ),
        },
        {
            'key': 'data-events',
            'title': 'Log object-level access to the payroll bucket',
            'command': DATA_EVENTS_COMMAND,
            'explanation': (
                'Management events cover control plane calls such as CreateBucket. Data events cover object-level '
                'calls such as GetObject and PutObject; they are off by default and charged per event, so turn them '
                'on only for sensitive buckets. How Floci emulates this: the event selector is stored and read back.'
            ),
        },
        {
            'key': 'config-recorder',
            'title': 'Record configuration changes with AWS Config',
            'command': RECORDER_COMMAND,
            'explanation': (
                'AWS Config records each supported resource\'s configuration and every change to it, delivering '
                'snapshots and history to S3. CloudTrail tells you who made a call; Config tells you what the '
                'resource looked like before and after. How Floci emulates this: the recorder, role, and delivery '
                'channel are stored and the recorder reports recording, but nothing is recorded or delivered.'
            ),
        },
        {
            'key': 'config-rule',
            'title': 'Flag buckets without versioning with a managed Config rule',
            'command': RULE_COMMAND,
            'explanation': (
                'Config rules evaluate recorded resources and mark them COMPLIANT or NON_COMPLIANT; AWS provides '
                'hundreds of managed rules, conformance packs bundle them, and remediation can fix resources with '
                'SSM Automation. Control Tower calls these detective controls. How Floci emulates this: the rule is '
                'stored but stays INSUFFICIENT_DATA, so the step evaluates the lab buckets\' versioning itself.'
            ),
        },
        {
            'key': 'guardduty',
            'title': 'Detect threats with GuardDuty and alert on high-severity findings',
            'command': GUARDDUTY_COMMAND,
            'explanation': (
                'GuardDuty analyzes CloudTrail events, VPC flow logs, and DNS logs with threat intelligence and '
                'machine learning, with no agents to install. Findings have a severity from 1 to 10 and arrive in '
                'EventBridge, where a rule can page the team for the high ones. How Floci emulates this: the '
                'detector is created, but the findings API is misrouted and EventBridge does not support numeric '
                'matching, so the step matches sample findings against the rule\'s pattern itself.'
            ),
        },
        {
            'key': 'security-hub',
            'title': 'Aggregate findings and score posture with Security Hub',
            'command': SECURITYHUB_COMMAND,
            'explanation': (
                'Security Hub collects findings from GuardDuty, Inspector, Macie, Config, IAM Access Analyzer, and '
                'partner tools in one format, and runs standards such as AWS Foundational Security Best Practices '
                'and CIS AWS Foundations. An EventBridge rule on "Security Hub Findings - Imported" pages the team for '
                'new CRITICAL and HIGH findings from every product at once. How Floci emulates this: the hub is '
                'enabled and described and the rule is stored, but the standards and findings APIs are misrouted, '
                'so the step matches four sample findings against the rule itself.'
            ),
        },
        {
            'key': 'service-chooser',
            'title': 'Match each governance question to the service that answers it',
            'command': CHOOSER_COMMAND,
            'explanation': (
                'Exam questions describe a need and ask for the service. CloudTrail answers who did what; Config '
                'answers what changed and whether it complies; GuardDuty finds threats; Security Hub aggregates '
                'findings; Macie finds sensitive data in S3; Inspector finds vulnerabilities; Artifact holds AWS '
                'compliance reports; CloudWatch watches metrics and logs. How Floci emulates this: the step checks '
                'that the four services this lab set up are on.'
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


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _unsupported(call) -> dict[str, Any]:
    try:
        response = call()
        if isinstance(response, dict):
            response.pop('ResponseMetadata', None)
        return {'Outcome': 'answered', 'Response': response}
    except ClientError as exc:
        return {'Outcome': 'unsupported', 'Code': _code(exc)}
    except BotoCoreError as exc:
        return {'Outcome': 'unsupported', 'Code': type(exc).__name__}


def missing_trail_statements(policy: dict[str, Any] | None) -> list[str]:
    """Which statements CloudTrail needs that the bucket policy lacks."""
    allowed = set()
    for statement in _as_list((policy or {}).get('Statement')):
        principal = statement.get('Principal')
        services = _as_list(principal.get('Service')) if isinstance(principal, dict) else []
        if statement.get('Effect') != 'Allow' or 'cloudtrail.amazonaws.com' not in services:
            continue
        for action in _as_list(statement.get('Action')):
            if action == 's3:PutObject':
                condition = (statement.get('Condition') or {}).get('StringEquals') or {}
                if condition.get('s3:x-amz-acl') != 'bucket-owner-full-control':
                    continue
                if not any(str(resource).startswith(f'arn:aws:s3:::{TRAIL_BUCKET}/') for resource in
                           _as_list(statement.get('Resource'))):
                    continue
            allowed.add(action)
    return [action for action in ('s3:GetBucketAcl', 's3:PutObject') if action not in allowed]


def _pattern_matches(pattern: Any, value: Any) -> bool:
    if isinstance(value, list):
        # Arrays in an event match when any element matches.
        return any(_pattern_matches(pattern, item) for item in value)
    if isinstance(pattern, dict):
        if not isinstance(value, dict):
            return False
        return all(_pattern_matches(sub, value.get(key)) for key, sub in pattern.items())
    for option in _as_list(pattern):
        if isinstance(option, dict) and 'numeric' in option:
            rules = option['numeric']
            if not isinstance(value, (int, float)):
                continue
            ok = True
            for index in range(0, len(rules), 2):
                operator, bound = rules[index], rules[index + 1]
                ok = ok and {'>': value > bound, '>=': value >= bound, '<': value < bound, '<=': value <= bound,
                             '=': value == bound}[operator]
            if ok:
                return True
        elif option == value:
            return True
    return False


def event_matches(pattern: dict[str, Any], event: dict[str, Any]) -> bool:
    """EventBridge content matching for exact values, numeric ranges and arrays."""
    return _pattern_matches(pattern, event)


def _floci_event(event: dict[str, Any]) -> dict[str, Any]:
    """TestEventPattern needs the full envelope."""
    return {'version': '0', 'id': 'sample', 'account': ACCOUNT, 'region': REGION,
            'time': '2026-10-25T00:00:00Z', 'resources': [], **event}


def _sample_event(finding_type: str, severity: float) -> dict[str, Any]:
    return {'version': '0', 'id': f'sample-{finding_type}', 'source': 'aws.guardduty',
            'detail-type': 'GuardDuty Finding', 'account': ACCOUNT, 'region': REGION,
            'time': '2026-10-25T00:00:00Z', 'resources': [],
            'detail': {'type': finding_type, 'severity': severity, 'title': f'Sample {finding_type}'}}


# ── Lookups ──────────────────────────────────────────────────────────────────

def _bucket_exists(bucket: str) -> bool:
    try:
        _client('s3').head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        if _code(exc) in ('404', 'NoSuchBucket', 'NotFound', '403'):
            return False
        raise


def _bucket_policy(bucket: str) -> dict[str, Any] | None:
    try:
        policy = _client('s3').get_bucket_policy(Bucket=bucket)['Policy']
    except ClientError as exc:
        if _code(exc) in ('NoSuchBucket', 'NoSuchBucketPolicy'):
            return None
        raise
    return json.loads(policy) if isinstance(policy, str) else policy


def _versioning(bucket: str) -> str:
    try:
        return _client('s3').get_bucket_versioning(Bucket=bucket).get('Status') or 'Off'
    except ClientError as exc:
        if _code(exc) == 'NoSuchBucket':
            return 'Missing'
        raise


def _public_access_block(bucket: str) -> dict[str, Any]:
    try:
        return _client('s3').get_public_access_block(Bucket=bucket)['PublicAccessBlockConfiguration']
    except ClientError as exc:
        if _code(exc) in ('NoSuchPublicAccessBlockConfiguration', 'NoSuchBucket'):
            return {}
        raise


def _trail() -> dict[str, Any] | None:
    trails = _client('cloudtrail').describe_trails(trailNameList=[TRAIL]).get('trailList', [])
    return next((trail for trail in trails if trail.get('Name') == TRAIL), None)


def _trail_status() -> dict[str, Any]:
    try:
        status = _client('cloudtrail').get_trail_status(Name=TRAIL)
    except ClientError as exc:
        if _code(exc) == 'TrailNotFoundException':
            return {}
        raise
    status.pop('ResponseMetadata', None)
    return status


def _selectors() -> list[dict[str, Any]]:
    try:
        return _client('cloudtrail').get_event_selectors(TrailName=TRAIL).get('EventSelectors', [])
    except ClientError as exc:
        if _code(exc) == 'TrailNotFoundException':
            return []
        raise


def _role() -> dict[str, Any] | None:
    try:
        return _client('iam').get_role(RoleName=CONFIG_ROLE)['Role']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise


def _recorders() -> list[dict[str, Any]]:
    return _client('config').describe_configuration_recorders().get('ConfigurationRecorders', [])


def _recorder() -> dict[str, Any] | None:
    recorders = _recorders()
    return next((item for item in recorders if item.get('name') == RECORDER), recorders[0] if recorders else None)


def _recorder_status(name: str) -> dict[str, Any]:
    statuses = _client('config').describe_configuration_recorder_status().get('ConfigurationRecordersStatus', [])
    return next((item for item in statuses if item.get('name') == name), {})


def _channels() -> list[dict[str, Any]]:
    return _client('config').describe_delivery_channels().get('DeliveryChannels', [])


def _rule() -> dict[str, Any] | None:
    try:
        rules = _client('config').describe_config_rules(ConfigRuleNames=[CONFIG_RULE]).get('ConfigRules', [])
    except ClientError as exc:
        if _code(exc) == 'NoSuchConfigRuleException':
            return None
        raise
    return rules[0] if rules else None


def _detector_id() -> str | None:
    ids = _client('guardduty').list_detectors().get('DetectorIds', [])
    return ids[0] if ids else None


def _detector() -> dict[str, Any] | None:
    detector_id = _detector_id()
    if not detector_id:
        return None
    detector = _client('guardduty').get_detector(DetectorId=detector_id)
    detector.pop('ResponseMetadata', None)
    return {'DetectorId': detector_id, **detector}


def _topic_arn() -> str | None:
    topics = _client('sns').list_topics().get('Topics', [])
    return next((item['TopicArn'] for item in topics if item['TopicArn'].endswith(f':{TOPIC}')), None)


def _findings_rule(name: str = FINDINGS_RULE) -> dict[str, Any] | None:
    try:
        return _client('events').describe_rule(Name=name)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _rule_targets(name: str = FINDINGS_RULE) -> list[str]:
    try:
        return [target['Arn'] for target in _client('events').list_targets_by_rule(Rule=name).get('Targets', [])]
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return []
        raise


def _hub() -> dict[str, Any] | None:
    try:
        hub = _client('securityhub').describe_hub()
    except ClientError as exc:
        if _code(exc) in ('ResourceNotFoundException', 'InvalidAccessException'):
            return None
        raise
    hub.pop('ResponseMetadata', None)
    return hub


def evaluate_versioning(buckets: list[str]) -> dict[str, str]:
    """What S3_BUCKET_VERSIONING_ENABLED reports for each bucket."""
    return {bucket: 'COMPLIANT' if _versioning(bucket) == 'Enabled' else 'NON_COMPLIANT'
            for bucket in buckets if _versioning(bucket) != 'Missing'}


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_bucket() -> tuple[bool, str]:
    if not _bucket_exists(TRAIL_BUCKET):
        return False, f'Bucket {TRAIL_BUCKET} was not found.'
    block = _public_access_block(TRAIL_BUCKET)
    if not all(block.get(key) for key in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy',
                                          'RestrictPublicBuckets')):
        return False, f'{TRAIL_BUCKET} must block all public access.'
    if _versioning(TRAIL_BUCKET) != 'Enabled':
        return False, f'{TRAIL_BUCKET} must have versioning enabled.'
    missing = missing_trail_statements(_bucket_policy(TRAIL_BUCKET))
    if missing:
        return False, f'The bucket policy must allow cloudtrail.amazonaws.com {", ".join(missing)}.'
    return True, (f'{TRAIL_BUCKET} blocks public access, keeps versions, allows only CloudTrail (scoped to {TRAIL}) '
                  'and Config to write, and denies plain HTTP.')


def _check_trail() -> tuple[bool, str]:
    trail = _trail()
    if not trail:
        return False, f'Trail {TRAIL} was not found.'
    if trail.get('S3BucketName') != TRAIL_BUCKET:
        return False, f'{TRAIL} must deliver to {TRAIL_BUCKET}.'
    for key, label in (('IsMultiRegionTrail', 'multi-Region'), ('LogFileValidationEnabled', 'log file validation'),
                       ('IncludeGlobalServiceEvents', 'global service events')):
        if not trail.get(key):
            return False, f'{TRAIL} needs {label}.'
    if not _trail_status().get('IsLogging'):
        return False, f'{TRAIL} is not logging.'
    return True, f'{TRAIL} logs every Region and global services to {TRAIL_BUCKET} with log file validation.'


def _check_data_events() -> tuple[bool, str]:
    values = [value for selector in _selectors() if selector.get('IncludeManagementEvents')
              and selector.get('ReadWriteType') == 'All'
              for resource in selector.get('DataResources', []) if resource.get('Type') == 'AWS::S3::Object'
              for value in resource.get('Values', [])]
    if f'arn:aws:s3:::{PAYROLL_BUCKET}/' not in values:
        return False, f'{TRAIL} must log management events and all S3 data events for {PAYROLL_BUCKET}.'
    if not _bucket_exists(PAYROLL_BUCKET):
        return False, f'Bucket {PAYROLL_BUCKET} was not found.'
    return True, f'{TRAIL} logs management events plus reads and writes of objects in {PAYROLL_BUCKET}.'


def _check_recorder() -> tuple[bool, str]:
    recorder = _recorder()
    if not recorder:
        return False, 'No configuration recorder exists.'
    if recorder.get('roleARN') != CONFIG_ROLE_ARN or not _role():
        return False, f'The recorder must use {CONFIG_ROLE}.'
    group = recorder.get('recordingGroup') or {}
    if not group.get('allSupported') or not group.get('includeGlobalResourceTypes'):
        return False, 'The recorder must record all supported resources, including global ones.'
    if not any(channel.get('s3BucketName') == TRAIL_BUCKET for channel in _channels()):
        return False, f'A delivery channel must send snapshots to {TRAIL_BUCKET}.'
    if not _recorder_status(recorder['name']).get('recording'):
        return False, f'Recorder {recorder["name"]} is not recording.'
    return True, f'Recorder {recorder["name"]} records all resources as {CONFIG_ROLE} and delivers to {TRAIL_BUCKET}.'


def _check_rule() -> tuple[bool, str]:
    rule = _rule()
    if not rule or (rule.get('Source') or {}).get('SourceIdentifier') != CONFIG_SOURCE:
        return False, f'Config rule {CONFIG_RULE} ({CONFIG_SOURCE}) was not found.'
    evaluation = evaluate_versioning(list(EXPECTED_COMPLIANCE))
    if evaluation != EXPECTED_COMPLIANCE:
        return False, f'Expected {TRAIL_BUCKET} compliant and {PAYROLL_BUCKET} noncompliant, got {evaluation}.'
    return True, (f'{CONFIG_RULE} marks {TRAIL_BUCKET} COMPLIANT and {PAYROLL_BUCKET} NON_COMPLIANT (versioning '
                  'off).')


def _check_guardduty() -> tuple[bool, str]:
    detector = _detector()
    if not detector or detector.get('Status') != 'ENABLED':
        return False, 'No enabled GuardDuty detector.'
    rule = _findings_rule()
    if not rule or json.loads(rule.get('EventPattern') or '{}') != FINDINGS_PATTERN:
        return False, f'EventBridge rule {FINDINGS_RULE} must match GuardDuty findings with severity {MIN_SEVERITY}+.'
    topic = _topic_arn()
    if not topic or topic not in _rule_targets():
        return False, f'{FINDINGS_RULE} must notify {TOPIC}.'
    return True, (f'GuardDuty detector {detector["DetectorId"]} is enabled and {FINDINGS_RULE} sends findings with '
                  f'severity {MIN_SEVERITY}+ to {TOPIC}.')


def _check_hub() -> tuple[bool, str]:
    hub = _hub()
    if not hub:
        return False, 'Security Hub is not enabled.'
    rule = _findings_rule(HUB_RULE)
    if not rule or json.loads(rule.get('EventPattern') or '{}') != HUB_PATTERN:
        return False, f'EventBridge rule {HUB_RULE} must match new CRITICAL and HIGH Security Hub findings.'
    topic = _topic_arn()
    if not topic or topic not in _rule_targets(HUB_RULE):
        return False, f'{HUB_RULE} must notify {TOPIC}.'
    return True, (f'Security Hub is enabled ({hub.get("HubArn")}) and {HUB_RULE} sends new CRITICAL and HIGH '
                  f'findings from every product to {TOPIC}.')


def _check_chooser() -> tuple[bool, str]:
    for check in (_check_trail, _check_recorder, _check_guardduty, _check_hub):
        ok, problem = check()
        if not ok:
            return False, problem
    marker = _marked('service-chooser')
    if not marker:
        return False, 'Run the chooser.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def _create_bucket(bucket: str) -> None:
    try:
        _client('s3').create_bucket(Bucket=bucket)
    except ClientError as exc:
        if _code(exc) not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            raise
    _client('s3').put_bucket_tagging(Bucket=bucket, Tagging={'TagSet': [{'Key': 'Lab', 'Value': LAB_KEY}]})


def step_trail_bucket() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    _create_bucket(TRAIL_BUCKET)
    s3.put_public_access_block(Bucket=TRAIL_BUCKET, PublicAccessBlockConfiguration={
        'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True})
    s3.put_bucket_versioning(Bucket=TRAIL_BUCKET, VersioningConfiguration={'Status': 'Enabled'})
    s3.put_bucket_policy(Bucket=TRAIL_BUCKET, Policy=json.dumps(TRAIL_BUCKET_POLICY))
    response = {'PublicAccessBlock': _public_access_block(TRAIL_BUCKET), 'Versioning': _versioning(TRAIL_BUCKET),
                'Policy': _bucket_policy(TRAIL_BUCKET)}
    verified, message = _check_bucket()
    return _result('trail-bucket', BUCKET_COMMAND, response, verified, message, started)


def step_cloudtrail_trail() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_bucket()[0]:
        return _result('cloudtrail-trail', TRAIL_COMMAND, {}, False, f'Prepare {TRAIL_BUCKET} first.', started)
    cloudtrail = _client('cloudtrail')
    settings = {'S3BucketName': TRAIL_BUCKET, 'IsMultiRegionTrail': True, 'IncludeGlobalServiceEvents': True,
                'EnableLogFileValidation': True}
    if _trail():
        cloudtrail.update_trail(Name=TRAIL, **settings)
    else:
        cloudtrail.create_trail(Name=TRAIL, TagsList=[{'Key': 'Lab', 'Value': LAB_KEY}], **settings)
    cloudtrail.start_logging(Name=TRAIL)
    _client('s3').put_object(Bucket=TRAIL_BUCKET, Key='probe/hello.txt', Body=b'audit me')
    time.sleep(1)
    lookup = _unsupported(lambda: cloudtrail.lookup_events(MaxResults=5))
    events = (lookup.get('Response') or {}).get('Events') or []
    try:
        delivered = _client('s3').list_objects_v2(Bucket=TRAIL_BUCKET, Prefix=TRAIL_PREFIX).get('KeyCount', 0)
    except ClientError:
        delivered = 0
    response = {'Trail': _trail(), 'Status': _trail_status(), 'LookupEventsCount': len(events),
                'LogFilesDelivered': delivered}
    verified, message = _check_trail()
    if verified and not events and not delivered:
        message += ' Floci delivers no log files and LookupEvents returned no events, so nothing can be audited here.'
    return _result('cloudtrail-trail', TRAIL_COMMAND, response, verified, message, started)


def step_data_events() -> dict[str, Any]:
    started = time.perf_counter()
    if not _trail():
        return _result('data-events', DATA_EVENTS_COMMAND, {}, False, f'Create {TRAIL} first.', started)
    _create_bucket(PAYROLL_BUCKET)
    _client('cloudtrail').put_event_selectors(TrailName=TRAIL, EventSelectors=[{
        'ReadWriteType': 'All', 'IncludeManagementEvents': True,
        'DataResources': [{'Type': 'AWS::S3::Object', 'Values': [f'arn:aws:s3:::{PAYROLL_BUCKET}/']}]}])
    response = {'EventSelectors': _selectors()}
    verified, message = _check_data_events()
    return _result('data-events', DATA_EVENTS_COMMAND, response, verified, message, started)


def step_config_recorder() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_bucket()[0]:
        return _result('config-recorder', RECORDER_COMMAND, {}, False, f'Prepare {TRAIL_BUCKET} first.', started)
    iam, config = _client('iam'), _client('config')
    if not _role():
        iam.create_role(RoleName=CONFIG_ROLE, AssumeRolePolicyDocument=json.dumps(CONFIG_TRUST),
                        Description='AWS Config recorder role (governance lab)')
    iam.attach_role_policy(RoleName=CONFIG_ROLE, PolicyArn=CONFIG_POLICY_ARN)
    existing = _recorder()
    name = existing['name'] if existing else RECORDER
    config.put_configuration_recorder(ConfigurationRecorder={
        'name': name, 'roleARN': CONFIG_ROLE_ARN,
        'recordingGroup': {'allSupported': True, 'includeGlobalResourceTypes': True}})
    channels = _channels()
    config.put_delivery_channel(DeliveryChannel={
        'name': channels[0]['name'] if channels else CHANNEL, 's3BucketName': TRAIL_BUCKET,
        's3KeyPrefix': CONFIG_PREFIX})
    config.start_configuration_recorder(ConfigurationRecorderName=name)
    discovered = _unsupported(lambda: config.list_discovered_resources(resourceType='AWS::S3::Bucket'))
    response = {'Recorder': _recorder(), 'Status': _recorder_status(name), 'DeliveryChannels': _channels(),
                'ListDiscoveredResources': {key: discovered.get(key) for key in ('Outcome', 'Code')}}
    verified, message = _check_recorder()
    if verified and existing:
        message += ' The account already had a recorder (one per Region), so the step updated it.'
    if verified and discovered['Outcome'] != 'answered':
        message += ' Floci records nothing: ListDiscoveredResources is unsupported.'
    return _result('config-recorder', RECORDER_COMMAND, response, verified, message, started)


def step_config_rule() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_recorder()[0] or not _bucket_exists(PAYROLL_BUCKET):
        return _result('config-rule', RULE_COMMAND, {}, False,
                       f'Start the recorder and create {PAYROLL_BUCKET} first (earlier steps).', started)
    config = _client('config')
    config.put_config_rule(ConfigRule={
        'ConfigRuleName': CONFIG_RULE, 'Description': 'Checks that S3 buckets keep object versions',
        'Source': {'Owner': 'AWS', 'SourceIdentifier': CONFIG_SOURCE},
        'Scope': {'ComplianceResourceTypes': ['AWS::S3::Bucket']}})
    config.start_config_rules_evaluation(ConfigRuleNames=[CONFIG_RULE])
    floci = config.describe_compliance_by_config_rule(ConfigRuleNames=[CONFIG_RULE]).get('ComplianceByConfigRules', [])
    floci_type = ((floci[0] if floci else {}).get('Compliance') or {}).get('ComplianceType')
    evaluation = evaluate_versioning(list(EXPECTED_COMPLIANCE))
    response = {'Rule': _rule(), 'FlociCompliance': floci_type, 'LabEvaluation': evaluation}
    verified, message = _check_rule()
    if verified and floci_type in (None, 'INSUFFICIENT_DATA'):
        message += ' Floci left the rule INSUFFICIENT_DATA, so the step evaluated the buckets itself.'
    return _result('config-rule', RULE_COMMAND, response, verified, message, started)


def step_guardduty() -> dict[str, Any]:
    started = time.perf_counter()
    guardduty, events, sns = _client('guardduty'), _client('events'), _client('sns')
    detector_id = _detector_id()
    if detector_id:
        guardduty.update_detector(DetectorId=detector_id, Enable=True, FindingPublishingFrequency='FIFTEEN_MINUTES')
    else:
        detector_id = guardduty.create_detector(Enable=True, FindingPublishingFrequency='FIFTEEN_MINUTES',
                                                Tags={'Lab': LAB_KEY})['DetectorId']
    topic = sns.create_topic(Name=TOPIC, Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])['TopicArn']
    events.put_rule(Name=FINDINGS_RULE, EventPattern=json.dumps(FINDINGS_PATTERN), State='ENABLED',
                    Description=f'GuardDuty findings with severity {MIN_SEVERITY} or higher')
    events.put_targets(Rule=FINDINGS_RULE, Targets=[{'Id': 'alerts', 'Arn': topic}])
    samples = _unsupported(lambda: guardduty.create_sample_findings(
        DetectorId=detector_id, FindingTypes=[SAMPLE_FINDINGS[0][0]]))
    rows = []
    for finding_type, severity in SAMPLE_FINDINGS:
        event = _sample_event(finding_type, severity)
        floci = _unsupported(lambda event=event: events.test_event_pattern(
            EventPattern=json.dumps(FINDINGS_PATTERN), Event=json.dumps(event)))
        rows.append({'Finding': finding_type, 'Severity': severity, 'Alerts': event_matches(FINDINGS_PATTERN, event),
                     'FlociTestEventPattern': (floci.get('Response') or {}).get('Result')})
    response = {'Detector': _detector(), 'Rule': FINDINGS_PATTERN, 'Topic': topic,
                'CreateSampleFindings': {key: samples.get(key) for key in ('Outcome', 'Code')}, 'SampleFindings': rows}
    alerting = [row['Finding'] for row in rows if row['Alerts']]
    if len(alerting) != 2:
        return _result('guardduty', GUARDDUTY_COMMAND, response, False,
                       f'Exactly the two severity-8 findings should alert, got {alerting}.', started)
    verified, message = _check_guardduty()
    if verified:
        message += ' The two severity-8 sample findings would page the team; the port probe (2) and the S3 policy (5) would not.'
        if samples['Outcome'] != 'answered':
            message += ' Floci cannot create sample findings'
            if any(row['FlociTestEventPattern'] is False and row['Alerts'] for row in rows):
                message += ' and does not support numeric matching in EventBridge'
            message += ', so the step matched the findings itself.'
    return _result('guardduty', GUARDDUTY_COMMAND, response, verified, message, started)


def step_security_hub() -> dict[str, Any]:
    started = time.perf_counter()
    hub, events = _client('securityhub'), _client('events')
    if not _hub():
        hub.enable_security_hub(EnableDefaultStandards=True, Tags={'Lab': LAB_KEY})
    topic = _topic_arn()
    if not topic:
        return _result('security-hub', SECURITYHUB_COMMAND, {'Hub': _hub()}, False,
                       f'Create {TOPIC} first (GuardDuty step).', started)
    events.put_rule(Name=HUB_RULE, EventPattern=json.dumps(HUB_PATTERN), State='ENABLED',
                    Description='New CRITICAL and HIGH findings aggregated by Security Hub')
    events.put_targets(Rule=HUB_RULE, Targets=[{'Id': 'alerts', 'Arn': topic}])
    standards = _unsupported(lambda: hub.get_enabled_standards())
    findings = _unsupported(lambda: hub.get_findings(MaxResults=5))
    rows = []
    for product, label in (('GuardDuty', 'HIGH'), ('Inspector', 'CRITICAL'), ('Config', 'MEDIUM'), ('Macie', 'LOW')):
        event = {'source': 'aws.securityhub', 'detail-type': 'Security Hub Findings - Imported',
                 'detail': {'findings': [{'ProductName': product, 'Severity': {'Label': label},
                                          'Workflow': {'Status': 'NEW'}}]}}
        floci = _unsupported(lambda event=event: events.test_event_pattern(
            EventPattern=json.dumps(HUB_PATTERN), Event=json.dumps(_floci_event(event))))
        rows.append({'Product': product, 'Severity': label, 'Alerts': event_matches(HUB_PATTERN, event),
                     'FlociTestEventPattern': (floci.get('Response') or {}).get('Result')})
    response = {'Hub': _hub(), 'Rule': HUB_PATTERN, 'SampleFindings': rows,
                'GetEnabledStandards': {key: standards.get(key) for key in ('Outcome', 'Code')},
                'GetFindings': {key: findings.get(key) for key in ('Outcome', 'Code')}}
    alerting = [row['Product'] for row in rows if row['Alerts']]
    if alerting != ['GuardDuty', 'Inspector']:
        return _result('security-hub', SECURITYHUB_COMMAND, response, False,
                       f'Only the HIGH GuardDuty and CRITICAL Inspector findings should alert, got {alerting}.', started)
    verified, message = _check_hub()
    if verified:
        message += ' The HIGH GuardDuty and CRITICAL Inspector findings would alert; MEDIUM and LOW ones would not.'
    if verified and standards['Outcome'] != 'answered':
        message += (' Floci misroutes the standards and findings APIs, so default standards (AWS Foundational '
                    'Security Best Practices, CIS) cannot be listed here.')
    return _result('security-hub', SECURITYHUB_COMMAND, response, verified, message, started)


def step_service_chooser() -> dict[str, Any]:
    started = time.perf_counter()
    states = {'trail': _check_trail()[0], 'config': _check_recorder()[0], 'guardduty': _check_guardduty()[0],
              'securityhub': _check_hub()[0]}
    rows = [{'Question': question, 'Service': service,
             'RunningInThisLab': states[key] if key else 'not part of this lab'}
            for question, service, key in QUESTIONS]
    response = {'Answers': rows}
    off = [service for _question, service, key in QUESTIONS if key and not states[key]]
    if off:
        return _result('service-chooser', CHOOSER_COMMAND, response, False,
                       f'Turn on {", ".join(off)} first (earlier steps).', started)
    message = ('CloudTrail answers who did what, Config what changed and whether it complies, GuardDuty whether '
               'something is attacking you, and Security Hub where all findings meet; Macie, Inspector, Artifact '
               'and CloudWatch answer the other four. All four services this lab set up are running.')
    _marker('service-chooser', {'message': message})
    verified, check = _check_chooser()
    return _result('service-chooser', CHOOSER_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'trail-bucket': step_trail_bucket,
    'cloudtrail-trail': step_cloudtrail_trail,
    'data-events': step_data_events,
    'config-recorder': step_config_recorder,
    'config-rule': step_config_rule,
    'guardduty': step_guardduty,
    'security-hub': step_security_hub,
    'service-chooser': step_service_chooser,
}


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check())
    except (ClientError, BotoCoreError, ValueError):
        return False


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    checks = {
        'trail-bucket': lambda: _check_bucket()[0],
        'cloudtrail-trail': lambda: _check_trail()[0],
        'data-events': lambda: _check_data_events()[0],
        'config-recorder': lambda: _check_recorder()[0],
        'config-rule': lambda: _check_rule()[0],
        'guardduty': lambda: _check_guardduty()[0],
        'security-hub': lambda: _check_hub()[0],
        'service-chooser': lambda: _check_chooser()[0],
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


def reset(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    """Delete the trail, Config rule, recorder and channel, detector, rule, topic, role and buckets; disable Security
    Hub."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call, quiet: tuple[str, ...] = ()) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code.startswith('NoSuch') or code in quiet or any(q in str(exc) for q in quiet):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cloudtrail, config, guardduty = _client('cloudtrail'), _client('config'), _client('guardduty')
    events, sns, iam, s3 = _client('events'), _client('sns'), _client('iam'), _client('s3')
    attempt(f'trail:{TRAIL}', lambda: cloudtrail.delete_trail(Name=TRAIL))
    attempt(f'config-rule:{CONFIG_RULE}', lambda: config.delete_config_rule(ConfigRuleName=CONFIG_RULE))
    try:
        recorder = _recorder()
        channels = _channels()
    except (ClientError, BotoCoreError) as exc:
        recorder, channels = None, []
        errors.append(f'config lookup: {exc}')
    if recorder and recorder.get('roleARN') == CONFIG_ROLE_ARN:
        attempt(f'recorder-stop:{recorder["name"]}', lambda: config.stop_configuration_recorder(
            ConfigurationRecorderName=recorder['name']))
        for channel in channels:
            if channel.get('s3BucketName') == TRAIL_BUCKET:
                attempt(f'delivery-channel:{channel["name"]}', lambda channel=channel: config.delete_delivery_channel(
                    DeliveryChannelName=channel['name']))
        attempt(f'recorder:{recorder["name"]}', lambda: config.delete_configuration_recorder(
            ConfigurationRecorderName=recorder['name']))
    attempt(f'role-policy:{CONFIG_ROLE}', lambda: iam.detach_role_policy(RoleName=CONFIG_ROLE, PolicyArn=CONFIG_POLICY_ARN))
    attempt(f'role:{CONFIG_ROLE}', lambda: iam.delete_role(RoleName=CONFIG_ROLE))
    attempt(f'targets:{FINDINGS_RULE}', lambda: events.remove_targets(Rule=FINDINGS_RULE, Ids=['alerts']))
    attempt(f'rule:{FINDINGS_RULE}', lambda: events.delete_rule(Name=FINDINGS_RULE))
    try:
        topic = _topic_arn()
    except (ClientError, BotoCoreError):
        topic = None
    if topic:
        attempt(f'topic:{TOPIC}', lambda: sns.delete_topic(TopicArn=topic))
    try:
        detector = _detector()
    except (ClientError, BotoCoreError):
        detector = None
    if detector and (detector.get('Tags') or {}).get('Lab') in (None, LAB_KEY):
        attempt(f'detector:{detector["DetectorId"]}', lambda: guardduty.delete_detector(DetectorId=detector['DetectorId']))
    attempt(f'targets:{HUB_RULE}', lambda: events.remove_targets(Rule=HUB_RULE, Ids=['alerts']))
    attempt(f'rule:{HUB_RULE}', lambda: events.delete_rule(Name=HUB_RULE))
    attempt('security-hub', lambda: _client('securityhub').disable_security_hub(),
            quiet=('AuthorizationHeaderMalformed', 'InvalidAccessException'))
    for bucket in (TRAIL_BUCKET, PAYROLL_BUCKET):
        try:
            versions = s3.list_object_versions(Bucket=bucket)
        except ClientError as exc:
            if _code(exc) == 'NoSuchBucket':
                continue
            errors.append(f'bucket:{bucket}: {exc}')
            continue
        for item in [*versions.get('Versions', []), *versions.get('DeleteMarkers', [])]:
            attempt(f'object:{bucket}/{item["Key"]}', lambda item=item, bucket=bucket: s3.delete_object(
                Bucket=bucket, Key=item['Key'], VersionId=item['VersionId']))
        attempt(f'bucket:{bucket}', lambda bucket=bucket: s3.delete_bucket(Bucket=bucket))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws cloudtrail delete-trail --name {TRAIL}\n'
            f'aws configservice delete-config-rule --config-rule-name {CONFIG_RULE}\n'
            f'aws configservice stop-configuration-recorder --configuration-recorder-name {RECORDER}\n'
            f'aws configservice delete-delivery-channel --delivery-channel-name {CHANNEL}\n'
            f'aws configservice delete-configuration-recorder --configuration-recorder-name {RECORDER}\n'
            f'aws iam detach-role-policy --role-name {CONFIG_ROLE} --policy-arn {CONFIG_POLICY_ARN}\n'
            f'aws iam delete-role --role-name {CONFIG_ROLE}\n'
            f'aws events remove-targets --rule {FINDINGS_RULE} --ids alerts && aws events delete-rule --name {FINDINGS_RULE}\n'
            f'aws events remove-targets --rule {HUB_RULE} --ids alerts && aws events delete-rule --name {HUB_RULE}\n'
            f'aws sns delete-topic --topic-arn <{TOPIC}-arn>\n'
            'aws guardduty delete-detector --detector-id <id>\n'
            'aws securityhub disable-security-hub\n'
            f'aws s3 rb s3://{TRAIL_BUCKET} --force && aws s3 rb s3://{PAYROLL_BUCKET} --force'
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
            else 'The trail, Config recorder and rule, GuardDuty detector, alert rule, topic, role and buckets are gone.',
        },
    }
