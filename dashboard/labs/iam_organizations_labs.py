"""Multi-account guardrails lab: an AWS Organization with all features, an OU structure (Security, Workloads with Prod
and Dev), three member accounts, guardrail SCPs, an SCP evaluation of sample requests, a tag policy, and a delegated
security administrator with a consolidated billing estimate.

Floci 2.3.0 runs the Organizations control plane: OUs, accounts (created at once), moves, policy types, policies and
attachments, effective tag policies, trusted access, and delegated administrators. It does not validate SCP content
(invalid JSON is accepted) and has no identities inside member accounts (AssumeRole into
OrganizationAccountAccessRole is denied), so SCPs are never enforced. The lab validates the SCPs and evaluates sample
requests through the OU tree itself.
"""

from __future__ import annotations

import fnmatch
import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'iam'
LAB_KEY = 'multi-account-guardrails'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
MANAGEMENT_ACCOUNT = '000000000000'
FULL_ACCESS_POLICY = 'p-FullAWSAccess'
ALLOWED_REGIONS = ['us-east-1', 'us-west-2']

SECURITY_OU, WORKLOADS_OU, PROD_OU, DEV_OU = 'lab-security', 'lab-workloads', 'lab-prod', 'lab-dev'
OU_TREE = [(SECURITY_OU, None), (WORKLOADS_OU, None), (PROD_OU, WORKLOADS_OU), (DEV_OU, WORKLOADS_OU)]
ACCOUNTS = {
    'lab-security-tooling': {'email': 'lab-security-tooling@example.com', 'ou': SECURITY_OU},
    'lab-app-prod': {'email': 'lab-app-prod@example.com', 'ou': PROD_OU},
    'lab-app-dev': {'email': 'lab-app-dev@example.com', 'ou': DEV_OU},
}
SECURITY_ACCOUNT, PROD_ACCOUNT, DEV_ACCOUNT = 'lab-security-tooling', 'lab-app-prod', 'lab-app-dev'

PROTECT_SCP, REGION_SCP, DEV_SCP = 'lab-protect-baseline', 'lab-region-allowlist', 'lab-dev-instance-types'
GLOBAL_SERVICE_ACTIONS = ['iam:*', 'organizations:*', 'sts:*', 'support:*', 'budgets:*', 'cloudfront:*',
                          'route53:*', 'waf:*']
SCPS = {
    PROTECT_SCP: {
        'target': WORKLOADS_OU,
        'description': 'Members cannot leave the organization or turn off CloudTrail',
        'document': {'Version': '2012-10-17', 'Statement': [
            {'Sid': 'DenyLeavingOrganization', 'Effect': 'Deny', 'Action': 'organizations:LeaveOrganization',
             'Resource': '*'},
            {'Sid': 'ProtectCloudTrail', 'Effect': 'Deny',
             'Action': ['cloudtrail:StopLogging', 'cloudtrail:DeleteTrail', 'cloudtrail:UpdateTrail'],
             'Resource': '*'},
        ]},
    },
    REGION_SCP: {
        'target': WORKLOADS_OU,
        'description': 'Regional services only in the approved Regions',
        'document': {'Version': '2012-10-17', 'Statement': [
            {'Sid': 'DenyOutsideApprovedRegions', 'Effect': 'Deny', 'NotAction': GLOBAL_SERVICE_ACTIONS,
             'Resource': '*', 'Condition': {'StringNotEquals': {'aws:RequestedRegion': ALLOWED_REGIONS}}},
        ]},
    },
    DEV_SCP: {
        'target': DEV_OU,
        'description': 'Development accounts launch only small burstable instances',
        'document': {'Version': '2012-10-17', 'Statement': [
            {'Sid': 'OnlySmallInstances', 'Effect': 'Deny', 'Action': 'ec2:RunInstances',
             'Resource': 'arn:aws:ec2:*:*:instance/*',
             'Condition': {'StringNotLike': {'ec2:InstanceType': ['t3.*', 't4g.*']}}},
        ]},
    },
}
INVALID_SCP_CONTENT = '{"Version": "2012-10-17", "Statement": [{"Effect": "Deny", "Action": "*"'

TAG_POLICY = 'lab-cost-center-tags'
COST_CENTERS = ['platform', 'data', 'web']
TAG_POLICY_DOCUMENT = {'tags': {'costcenter': {
    'tag_key': {'@@assign': 'CostCenter'},
    'tag_value': {'@@assign': COST_CENTERS},
    'enforced_for': {'@@assign': ['ec2:instance', 's3:bucket']},
}}}

DELEGATED_SERVICES = ['guardduty.amazonaws.com', 'securityhub.amazonaws.com']
TRUSTED_SERVICES = ['cloudtrail.amazonaws.com', 'config.amazonaws.com', *DELEGATED_SERVICES]

# Sample requests for the SCP evaluation: (label, account, action, region, context)
REQUESTS = [
    ('Dev launches an m5.24xlarge', DEV_ACCOUNT, 'ec2:RunInstances', 'us-east-1', {'ec2:InstanceType': 'm5.24xlarge'}),
    ('Dev launches a t3.micro', DEV_ACCOUNT, 'ec2:RunInstances', 'us-east-1', {'ec2:InstanceType': 't3.micro'}),
    ('Prod creates a bucket in eu-west-1', PROD_ACCOUNT, 's3:CreateBucket', 'eu-west-1', {}),
    ('Prod creates an IAM role from eu-west-1', PROD_ACCOUNT, 'iam:CreateRole', 'eu-west-1', {}),
    ('Prod stops the CloudTrail trail', PROD_ACCOUNT, 'cloudtrail:StopLogging', 'us-east-1', {}),
    ('Prod leaves the organization', PROD_ACCOUNT, 'organizations:LeaveOrganization', 'us-east-1', {}),
    ('Security tooling account works in eu-west-1', SECURITY_ACCOUNT, 'guardduty:CreateDetector', 'eu-west-1', {}),
    ('Management account stops CloudTrail', 'management', 'cloudtrail:StopLogging', 'us-east-1', {}),
]
EXPECTED = {
    'Dev launches an m5.24xlarge': 'denied', 'Dev launches a t3.micro': 'allowed',
    'Prod creates a bucket in eu-west-1': 'denied', 'Prod creates an IAM role from eu-west-1': 'allowed',
    'Prod stops the CloudTrail trail': 'denied', 'Prod leaves the organization': 'denied',
    'Security tooling account works in eu-west-1': 'allowed', 'Management account stops CloudTrail': 'allowed',
}

# Consolidated billing: monthly S3 Standard GB-months per account, priced on the S3 tiers.
S3_TIERS = [(51_200, 0.023), (460_800, 0.022), (None, 0.021)]
S3_USAGE_GB = {'lab-app-prod': 40_000, 'lab-app-dev': 12_000, 'lab-security-tooling': 30_000}

ORG_COMMAND = (
    'aws organizations create-organization --feature-set ALL\n'
    'aws organizations enable-policy-type --root-id <root-id> --policy-type SERVICE_CONTROL_POLICY'
)
OU_COMMAND = (
    f'aws organizations create-organizational-unit --parent-id <root-id> --name {SECURITY_OU}\n'
    f'aws organizations create-organizational-unit --parent-id <root-id> --name {WORKLOADS_OU}\n'
    f'aws organizations create-organizational-unit --parent-id <{WORKLOADS_OU}-id> --name {PROD_OU}\n'
    f'aws organizations create-organizational-unit --parent-id <{WORKLOADS_OU}-id> --name {DEV_OU}'
)
ACCOUNTS_COMMAND = '\n'.join(
    f'aws organizations create-account --account-name {name} --email {info["email"]}   # then move-account to {info["ou"]}'
    for name, info in ACCOUNTS.items())
SCP_COMMAND = '\n'.join(
    f'aws organizations create-policy --type SERVICE_CONTROL_POLICY --name {name} --content file://{name}.json\n'
    f'aws organizations attach-policy --policy-id <{name}-id> --target-id <{info["target"]}-id>'
    for name, info in SCPS.items())
EVAL_COMMAND = (
    f'aws sts assume-role --role-arn arn:aws:iam::<{DEV_ACCOUNT}-id>:role/OrganizationAccountAccessRole '
    '--role-session-name guardrail-test\n'
    'aws ec2 run-instances --instance-type m5.24xlarge ...   # as the dev account: denied by the SCP'
)
TAG_COMMAND = (
    'aws organizations enable-policy-type --root-id <root-id> --policy-type TAG_POLICY\n'
    f'aws organizations create-policy --type TAG_POLICY --name {TAG_POLICY} --content file://cost-center.json\n'
    f'aws organizations attach-policy --policy-id <{TAG_POLICY}-id> --target-id <root-id>\n'
    f'aws organizations describe-effective-policy --policy-type TAG_POLICY --target-id <{PROD_ACCOUNT}-id>'
)
DELEGATE_COMMAND = (
    '\n'.join(f'aws organizations enable-aws-service-access --service-principal {service}'
              for service in TRUSTED_SERVICES) + '\n'
    + '\n'.join(f'aws organizations register-delegated-administrator --account-id <{SECURITY_ACCOUNT}-id> '
                f'--service-principal {service}' for service in DELEGATED_SERVICES)
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Govern multiple accounts with AWS Organizations, OUs, SCP guardrails, and tag policies',
    'description': (
        'Create an organization with all features, Security and Workloads OUs with Prod and Dev below, and three '
        'member accounts; attach SCPs that protect CloudTrail, allow only approved Regions, and limit dev instance '
        'types; evaluate sample requests through the OU tree; enforce a CostCenter tag policy; and delegate security '
        'services to a tooling account while estimating what consolidated billing saves.'
    ),
    'steps': [
        {
            'key': 'organization',
            'title': 'Create an organization with all features',
            'command': ORG_COMMAND,
            'explanation': (
                'The account that creates the organization becomes the management account and pays for every member '
                '(consolidated billing). "All features" adds SCPs, tag policies, and trusted access for services; '
                'consolidated-billing-only organizations cannot use them. How Floci emulates this: the organization '
                'and its root are created (or reused if one exists) and SCPs are enabled on the root.'
            ),
        },
        {
            'key': 'ou-structure',
            'title': 'Group accounts into organizational units',
            'command': OU_COMMAND,
            'explanation': (
                'OUs group accounts by function so one policy covers many accounts. A common layout keeps security '
                'tooling and log archive accounts in a Security OU and applications in a Workloads OU split into '
                'Prod and Dev. Policies attached to an OU apply to every OU and account below it. How Floci emulates '
                'this: the OUs are created and nested as in AWS.'
            ),
        },
        {
            'key': 'member-accounts',
            'title': 'Create member accounts and place them in OUs',
            'command': ACCOUNTS_COMMAND,
            'explanation': (
                'Accounts are the strongest isolation boundary in AWS: separate resources, quotas, and bills. '
                'CreateAccount makes a member with an OrganizationAccountAccessRole the management account can '
                'assume. AWS Control Tower automates this as Account Factory, inside a landing zone with log archive '
                'and audit accounts. How Floci emulates this: accounts are created at once and moved into OUs, but '
                'Floci has no identities inside them.'
            ),
        },
        {
            'key': 'guardrail-scps',
            'title': 'Attach guardrail SCPs to the Workloads and Dev OUs',
            'command': SCP_COMMAND,
            'explanation': (
                'Service control policies set the maximum permissions for every principal in member accounts, '
                'including their root users; they never grant anything, and they do not affect the management '
                'account. Deny-list SCPs keep the default FullAWSAccess attached and add Deny statements. Control '
                'Tower calls these preventive guardrails (controls). How Floci emulates this: the policies are '
                'stored and attached, but Floci does not validate their content (an invalid one is accepted), so the '
                'step checks each document itself.'
            ),
        },
        {
            'key': 'scp-evaluation',
            'title': 'Evaluate sample requests through the OU tree',
            'command': EVAL_COMMAND,
            'explanation': (
                'For a request in a member account, every level from the root down to the account must allow the '
                'action (FullAWSAccess usually does) and no attached SCP may deny it; then IAM policies decide. '
                'NotAction lets global services like IAM work from any Region. How Floci emulates this: Floci '
                'cannot act inside member accounts and does not enforce SCPs, so the step evaluates eight requests '
                'with the same rules and records the denied AssumeRole.'
            ),
        },
        {
            'key': 'tag-policy',
            'title': 'Standardize the CostCenter tag with a tag policy',
            'command': TAG_COMMAND,
            'explanation': (
                'Tag policies standardize tag keys and allowed values across accounts and can block noncompliant '
                'tagging on chosen resource types. Consistent cost allocation tags make Cost Explorer and budgets '
                'useful per team. How Floci emulates this: the policy type, policy, attachment, and the effective '
                'policy for an account work; Floci does not block noncompliant tags.'
            ),
        },
        {
            'key': 'delegated-admin',
            'title': 'Delegate security services and share one bill',
            'command': DELEGATE_COMMAND,
            'explanation': (
                'Trusted access lets services such as CloudTrail (organization trails) and Config work across all '
                'accounts. A delegated administrator runs GuardDuty and Security Hub for the organization from a '
                'security account, so day-to-day work stays out of the management account. Consolidated billing '
                'adds usage from every account for volume tiers and shares Reserved Instance and Savings Plans '
                'discounts. How Floci emulates this: trusted access and delegation are stored; the step computes '
                'the S3 tier savings itself because Floci has no billing data.'
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


# ── SCP validation and evaluation ────────────────────────────────────────────

def scp_problems(content: str) -> list[str]:
    """What AWS would reject in an SCP document (a subset of the real checks)."""
    try:
        document = json.loads(content)
    except (TypeError, ValueError):
        return ['not valid JSON']
    problems = []
    if document.get('Version') != '2012-10-17':
        problems.append('Version must be 2012-10-17')
    statements = _as_list(document.get('Statement'))
    if not statements:
        problems.append('no statements')
    for index, statement in enumerate(statements):
        if statement.get('Effect') not in ('Allow', 'Deny'):
            problems.append(f'statement {index} has no Effect')
        if not statement.get('Action') and not statement.get('NotAction'):
            problems.append(f'statement {index} has no Action or NotAction')
        if 'Principal' in statement or 'NotPrincipal' in statement:
            problems.append(f'statement {index} names a Principal, which SCPs do not support')
        if statement.get('Effect') == 'Allow' and statement.get('Condition'):
            problems.append(f'statement {index} uses a Condition in an Allow, which SCPs do not support')
    if len(content) > 5120:
        problems.append('longer than 5,120 characters')
    return problems


def _matches(patterns: list[str], value: str) -> bool:
    return any(fnmatch.fnmatchcase(value.lower(), str(pattern).lower()) for pattern in patterns)


def _condition_holds(condition: dict[str, Any], context: dict[str, str]) -> bool:
    for operator, pairs in (condition or {}).items():
        for key, expected in pairs.items():
            actual = context.get(key)
            values = [str(item) for item in _as_list(expected)]
            if operator == 'StringEquals' and actual not in values:
                return False
            if operator == 'StringNotEquals' and (actual is None or actual in values):
                return False
            if operator == 'StringLike' and (actual is None or not _matches(values, actual)):
                return False
            if operator == 'StringNotLike' and (actual is None or _matches(values, actual)):
                return False
    return True


def _statement_applies(statement: dict[str, Any], action: str, context: dict[str, str]) -> bool:
    if 'Action' in statement and not _matches(_as_list(statement['Action']), action):
        return False
    if 'NotAction' in statement and _matches(_as_list(statement['NotAction']), action):
        return False
    return _condition_holds(statement.get('Condition') or {}, context)


def evaluate_scps(levels: list[tuple[str, list[dict[str, Any]]]], action: str, context: dict[str, str]) -> dict[str, Any]:
    """Evaluate one request against the SCPs at each level from the root down to the account."""
    for name, documents in levels:
        statements = [statement for document in documents for statement in _as_list(document.get('Statement'))]
        denied = [statement.get('Sid') or 'Deny' for statement in statements
                  if statement.get('Effect') == 'Deny' and _statement_applies(statement, action, context)]
        if denied:
            return {'Decision': 'denied', 'Level': name, 'Reason': f'explicit deny {denied[0]}'}
        if not any(statement.get('Effect') == 'Allow' and _statement_applies(statement, action, context)
                   for statement in statements):
            return {'Decision': 'denied', 'Level': name, 'Reason': 'no SCP at this level allows it'}
    return {'Decision': 'allowed', 'Level': None, 'Reason': 'every level allows it; IAM policies decide next'}


def tiered_cost(gb: float, tiers: list[tuple[int | None, float]] = S3_TIERS) -> float:
    remaining, cost = gb, 0.0
    for size, price in tiers:
        portion = remaining if size is None else min(remaining, size)
        cost += portion * price
        remaining -= portion
        if remaining <= 0:
            break
    return round(cost, 2)


# ── Lookups ──────────────────────────────────────────────────────────────────

def _organization() -> dict[str, Any] | None:
    try:
        return _client('organizations').describe_organization()['Organization']
    except ClientError as exc:
        if _code(exc) in ('AWSOrganizationsNotInUseException', 'ResourceNotFoundException'):
            return None
        raise


def _root() -> dict[str, Any] | None:
    try:
        roots = _client('organizations').list_roots().get('Roots', [])
    except ClientError as exc:
        if _code(exc) == 'AWSOrganizationsNotInUseException':
            return None
        raise
    return roots[0] if roots else None


def _child_ous(parent_id: str) -> dict[str, dict[str, Any]]:
    units = _client('organizations').list_organizational_units_for_parent(ParentId=parent_id).get(
        'OrganizationalUnits', [])
    return {unit['Name']: unit for unit in units}


def _ous() -> dict[str, dict[str, Any]]:
    root = _root()
    if not root:
        return {}
    found: dict[str, dict[str, Any]] = {}
    top = _child_ous(root['Id'])
    for name in (SECURITY_OU, WORKLOADS_OU):
        if name in top:
            found[name] = top[name]
    if WORKLOADS_OU in found:
        below = _child_ous(found[WORKLOADS_OU]['Id'])
        for name in (PROD_OU, DEV_OU):
            if name in below:
                found[name] = below[name]
    return found


def _accounts() -> dict[str, dict[str, Any]]:
    try:
        listed = _client('organizations').list_accounts().get('Accounts', [])
    except ClientError as exc:
        if _code(exc) == 'AWSOrganizationsNotInUseException':
            return {}
        raise
    return {account['Name']: account for account in listed
            if account.get('Name') in ACCOUNTS and account.get('Status') == 'ACTIVE'}


def _lab_accounts_any_status() -> dict[str, dict[str, Any]]:
    try:
        listed = _client('organizations').list_accounts().get('Accounts', [])
    except ClientError as exc:
        if _code(exc) == 'AWSOrganizationsNotInUseException':
            return {}
        raise
    return {f'{account["Name"]}:{account["Id"]}': account for account in listed if account.get('Name') in ACCOUNTS}


def _parent(account_id: str) -> str | None:
    parents = _client('organizations').list_parents(ChildId=account_id).get('Parents', [])
    return parents[0]['Id'] if parents else None


def _policies(kind: str = 'SERVICE_CONTROL_POLICY') -> dict[str, dict[str, Any]]:
    try:
        listed = _client('organizations').list_policies(Filter=kind).get('Policies', [])
    except ClientError as exc:
        if _code(exc) == 'AWSOrganizationsNotInUseException':
            return {}
        raise
    return {policy['Name']: policy for policy in listed}


def _policy_content(policy_id: str) -> str:
    return _client('organizations').describe_policy(PolicyId=policy_id)['Policy'].get('Content') or ''


def _targets(policy_id: str) -> list[str]:
    return [target['TargetId'] for target in
            _client('organizations').list_targets_for_policy(PolicyId=policy_id).get('Targets', [])]


def _policies_for(target_id: str) -> list[dict[str, Any]]:
    return _client('organizations').list_policies_for_target(
        TargetId=target_id, Filter='SERVICE_CONTROL_POLICY').get('Policies', [])


def _enabled_types(root: dict[str, Any] | None) -> set[str]:
    return {item['Type'] for item in (root or {}).get('PolicyTypes', []) if item.get('Status') == 'ENABLED'}


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_organization() -> tuple[bool, str]:
    organization = _organization()
    if not organization:
        return False, 'No organization exists.'
    if organization.get('FeatureSet') != 'ALL':
        return False, 'The organization must have all features enabled.'
    if 'SERVICE_CONTROL_POLICY' not in _enabled_types(_root()):
        return False, 'SCPs are not enabled on the root.'
    return True, f'Organization {organization["Id"]} has all features and SCPs enabled on the root.'


def _check_ous() -> tuple[bool, str]:
    ous = _ous()
    missing = [name for name, _parent_name in OU_TREE if name not in ous]
    if missing:
        return False, f'Missing OUs: {", ".join(missing)} ({PROD_OU} and {DEV_OU} go under {WORKLOADS_OU}).'
    return True, f'{SECURITY_OU} and {WORKLOADS_OU} are under the root, with {PROD_OU} and {DEV_OU} under {WORKLOADS_OU}.'


def _check_accounts() -> tuple[bool, str]:
    ous, accounts = _ous(), _accounts()
    for name, info in ACCOUNTS.items():
        account = accounts.get(name)
        if not account:
            return False, f'Account {name} was not found.'
        if info['ou'] not in ous or _parent(account['Id']) != ous[info['ou']]['Id']:
            return False, f'{name} must be in {info["ou"]}.'
    return True, ', '.join(f'{name} is in {info["ou"]}' for name, info in ACCOUNTS.items()) + '.'


def _check_scps() -> tuple[bool, str]:
    ous, policies = _ous(), _policies()
    for name, info in SCPS.items():
        policy = policies.get(name)
        if not policy:
            return False, f'SCP {name} was not found.'
        problems = scp_problems(_policy_content(policy['Id']))
        if problems:
            return False, f'{name} is not a valid SCP: {"; ".join(problems)}.'
        target = ous.get(info['target'])
        if not target or target['Id'] not in _targets(policy['Id']):
            return False, f'{name} must be attached to {info["target"]}.'
    for name in (WORKLOADS_OU, DEV_OU):
        if name in ous and FULL_ACCESS_POLICY not in [item['Id'] for item in _policies_for(ous[name]['Id'])]:
            return False, f'{name} lost FullAWSAccess; deny-list SCPs need it to allow everything else.'
    return True, (f'{PROTECT_SCP} and {REGION_SCP} are on {WORKLOADS_OU} and {DEV_SCP} is on {DEV_OU}, each a valid '
                  'deny-list SCP next to FullAWSAccess.')


def _check_marked(step: str, prerequisite) -> tuple[bool, str]:
    ok, problem = prerequisite()
    if not ok:
        return False, problem
    marker = _marked(step)
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_tag_policy() -> tuple[bool, str]:
    root, policy = _root(), _policies('TAG_POLICY').get(TAG_POLICY) if _root() else None
    if 'TAG_POLICY' not in _enabled_types(root):
        return False, 'Tag policies are not enabled on the root.'
    if not policy:
        return False, f'Tag policy {TAG_POLICY} was not found.'
    if root['Id'] not in _targets(policy['Id']):
        return False, f'{TAG_POLICY} must be attached to the root.'
    return True, f'{TAG_POLICY} on the root makes CostCenter one of {", ".join(COST_CENTERS)} in every account.'


def _check_delegation() -> tuple[bool, str]:
    accounts = _accounts()
    security = accounts.get(SECURITY_ACCOUNT)
    if not security:
        return False, f'Account {SECURITY_ACCOUNT} was not found.'
    organizations = _client('organizations')
    enabled = {item['ServicePrincipal'] for item in
               organizations.list_aws_service_access_for_organization().get('EnabledServicePrincipals', [])}
    missing = [service for service in TRUSTED_SERVICES if service not in enabled]
    if missing:
        return False, f'Enable trusted access for {", ".join(missing)}.'
    delegated = {item['ServicePrincipal'] for item in organizations.list_delegated_services_for_account(
        AccountId=security['Id']).get('DelegatedServices', [])}
    missing = [service for service in DELEGATED_SERVICES if service not in delegated]
    if missing:
        return False, f'{SECURITY_ACCOUNT} must be the delegated administrator for {", ".join(missing)}.'
    marker = _marked('delegated-admin')
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_organization() -> dict[str, Any]:
    started = time.perf_counter()
    organizations = _client('organizations')
    created = False
    if not _organization():
        organizations.create_organization(FeatureSet='ALL')
        created = True
    root = _root()
    if created and root:
        # Tag the root so reset knows the lab created this organization, even after a restart.
        organizations.tag_resource(ResourceId=root['Id'], Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    if root and 'SERVICE_CONTROL_POLICY' not in _enabled_types(root):
        organizations.enable_policy_type(RootId=root['Id'], PolicyType='SERVICE_CONTROL_POLICY')
    response = {'Organization': _organization(), 'Root': _root(), 'CreatedByLab': created}
    verified, message = _check_organization()
    if verified and not created:
        message += ' The account already had an organization, so the lab reuses it.'
    return _result('organization', ORG_COMMAND, response, verified, message, started)


def step_ou_structure() -> dict[str, Any]:
    started = time.perf_counter()
    root = _root()
    if not root:
        return _result('ou-structure', OU_COMMAND, {}, False, 'Create the organization first.', started)
    organizations = _client('organizations')
    for name, parent_name in OU_TREE:
        ous = _ous()
        if name in ous:
            continue
        parent_id = root['Id'] if parent_name is None else ous[parent_name]['Id']
        organizations.create_organizational_unit(ParentId=parent_id, Name=name, Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    response = {'Root': root['Id'], 'OrganizationalUnits': _ous()}
    verified, message = _check_ous()
    return _result('ou-structure', OU_COMMAND, response, verified, message, started)


def step_member_accounts() -> dict[str, Any]:
    started = time.perf_counter()
    root, ous = _root(), _ous()
    if not root or len(ous) < len(OU_TREE):
        return _result('member-accounts', ACCOUNTS_COMMAND, {}, False, 'Create the OUs first.', started)
    organizations = _client('organizations')
    for account in _lab_accounts_any_status().values():
        if account.get('Status') != 'ACTIVE':
            # A closed lab account keeps its email; take it out so the name and email can be reused.
            try:
                organizations.remove_account_from_organization(AccountId=account['Id'])
            except ClientError:
                pass
    statuses = {}
    for name, info in ACCOUNTS.items():
        account = _accounts().get(name)
        if not account:
            status = organizations.create_account(Email=info['email'], AccountName=name,
                                                  Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])['CreateAccountStatus']
            deadline = time.monotonic() + 30
            while status.get('State') == 'IN_PROGRESS' and time.monotonic() < deadline:
                time.sleep(2)
                status = organizations.describe_create_account_status(
                    CreateAccountRequestId=status['Id'])['CreateAccountStatus']
            statuses[name] = {key: status.get(key) for key in ('State', 'AccountId', 'FailureReason')}
            account = _accounts().get(name)
        if not account:
            continue
        parent = _parent(account['Id'])
        if parent != ous[info['ou']]['Id']:
            organizations.move_account(AccountId=account['Id'], SourceParentId=parent,
                                       DestinationParentId=ous[info['ou']]['Id'])
    response = {'CreateAccount': statuses,
                'Accounts': {name: {'Id': account['Id'], 'Email': account.get('Email'), 'Parent': _parent(account['Id'])}
                             for name, account in _accounts().items()}}
    verified, message = _check_accounts()
    if verified and statuses:
        message += ' Floci creates accounts at once; AWS takes a few minutes.'
    return _result('member-accounts', ACCOUNTS_COMMAND, response, verified, message, started)


def step_guardrail_scps() -> dict[str, Any]:
    started = time.perf_counter()
    ous = _ous()
    if len(ous) < len(OU_TREE):
        return _result('guardrail-scps', SCP_COMMAND, {}, False, 'Create the OUs first.', started)
    organizations = _client('organizations')
    invalid: dict[str, Any] = {}
    try:
        created = organizations.create_policy(Name='lab-invalid-scp', Description='Truncated JSON (lab check)',
                                              Type='SERVICE_CONTROL_POLICY', Content=INVALID_SCP_CONTENT)
        invalid = {'Outcome': 'accepted', 'Problems': scp_problems(INVALID_SCP_CONTENT)}
        organizations.delete_policy(PolicyId=created['Policy']['PolicySummary']['Id'])
    except ClientError as exc:
        invalid = {'Outcome': 'refused', 'Code': _code(exc)}
    for name, info in SCPS.items():
        content = json.dumps(info['document'])
        policy = _policies().get(name)
        if policy:
            organizations.update_policy(PolicyId=policy['Id'], Content=content, Description=info['description'])
        else:
            policy = organizations.create_policy(Name=name, Description=info['description'],
                                                 Type='SERVICE_CONTROL_POLICY', Content=content)['Policy']['PolicySummary']
        target = ous[info['target']]['Id']
        if target not in _targets(policy['Id']):
            organizations.attach_policy(PolicyId=policy['Id'], TargetId=target)
    response = {'Policies': {name: {'Target': info['target'], 'Document': info['document']}
                             for name, info in SCPS.items()},
                'InvalidScpAttempt': invalid}
    verified, message = _check_scps()
    if verified and invalid.get('Outcome') == 'accepted':
        message += ' Floci accepted a truncated SCP that AWS rejects, so the step validated each document itself.'
    return _result('guardrail-scps', SCP_COMMAND, response, verified, message, started)


def _levels_for(account_id: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """SCP documents attached at each level from the root down to the account."""
    organizations = _client('organizations')
    chain = [account_id]
    while True:
        parents = organizations.list_parents(ChildId=chain[-1]).get('Parents', [])
        if not parents:
            break
        chain.append(parents[0]['Id'])
        if parents[0]['Type'] == 'ROOT':
            break
    names = {unit['Id']: name for name, unit in _ous().items()}
    levels = []
    for target in reversed(chain):
        documents = [json.loads(_policy_content(policy['Id'])) for policy in _policies_for(target)]
        label = 'root' if target.startswith('r-') else names.get(target, f'account {target}')
        levels.append((label, documents))
    return levels


def step_scp_evaluation() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_scps()
    if not ok or not _check_accounts()[0]:
        return _result('scp-evaluation', EVAL_COMMAND, {}, False,
                       f'Create the accounts and SCPs first: {problem if not ok else _check_accounts()[1]}', started)
    accounts = _accounts()
    rows = []
    for label, account, action, region, context in REQUESTS:
        if account == 'management':
            decision = {'Decision': 'allowed', 'Level': None, 'Reason': 'SCPs never apply to the management account'}
        else:
            decision = evaluate_scps(_levels_for(accounts[account]['Id']), action,
                                     {'aws:RequestedRegion': region, **context})
        rows.append({'Request': label, 'Account': account, 'Action': action, 'Region': region, **decision,
                     'Expected': EXPECTED[label]})
    assume: dict[str, Any]
    try:
        _client('sts').assume_role(
            RoleArn=f'arn:aws:iam::{accounts[DEV_ACCOUNT]["Id"]}:role/OrganizationAccountAccessRole',
            RoleSessionName='guardrail-test')
        assume = {'Outcome': 'allowed'}
    except ClientError as exc:
        assume = {'Outcome': 'denied', 'Code': _code(exc)}
    response = {'Evaluations': rows, 'AssumeMemberRole': assume}
    wrong = [row['Request'] for row in rows if row['Decision'] != row['Expected']]
    if wrong:
        return _result('scp-evaluation', EVAL_COMMAND, response, False,
                       f'These requests came out wrong, so check the SCPs: {", ".join(wrong)}.', started)
    denied = sum(1 for row in rows if row['Decision'] == 'denied')
    message = (f'{denied} of {len(rows)} requests are denied by SCPs: the large dev instance, the eu-west-1 bucket, '
               'stopping CloudTrail and leaving the organization. IAM in eu-west-1 is exempt through NotAction, and '
               'the management account is never limited by SCPs.')
    if assume['Outcome'] != 'allowed':
        message += (' Floci denied assuming OrganizationAccountAccessRole in the member account, so it cannot '
                    'enforce SCPs; the step evaluated them.')
    _marker('scp-evaluation', {'message': message})
    verified, check = _check_marked('scp-evaluation', _check_scps)
    return _result('scp-evaluation', EVAL_COMMAND, response, verified, message if verified else check, started)


def step_tag_policy() -> dict[str, Any]:
    started = time.perf_counter()
    root = _root()
    accounts = _accounts()
    if not root or PROD_ACCOUNT not in accounts:
        return _result('tag-policy', TAG_COMMAND, {}, False, 'Create the organization and accounts first.', started)
    organizations = _client('organizations')
    if 'TAG_POLICY' not in _enabled_types(root):
        organizations.enable_policy_type(RootId=root['Id'], PolicyType='TAG_POLICY')
    policy = _policies('TAG_POLICY').get(TAG_POLICY)
    content = json.dumps(TAG_POLICY_DOCUMENT)
    if policy:
        organizations.update_policy(PolicyId=policy['Id'], Content=content)
    else:
        policy = organizations.create_policy(Name=TAG_POLICY, Description='CostCenter tag values', Type='TAG_POLICY',
                                             Content=content)['Policy']['PolicySummary']
    if root['Id'] not in _targets(policy['Id']):
        organizations.attach_policy(PolicyId=policy['Id'], TargetId=root['Id'])
    try:
        effective = organizations.describe_effective_policy(
            PolicyType='TAG_POLICY', TargetId=accounts[PROD_ACCOUNT]['Id'])['EffectivePolicy']
        effective_content = json.loads(effective.get('PolicyContent') or '{}')
    except ClientError as exc:
        effective, effective_content = {'Code': _code(exc)}, {}
    response = {'TagPolicy': TAG_POLICY_DOCUMENT, 'EffectivePolicyForProd': effective}
    values = (((effective_content.get('tags') or {}).get('costcenter') or {}).get('tag_value') or {}).get('@@assign')
    verified, message = _check_tag_policy()
    if verified and values != COST_CENTERS:
        return _result('tag-policy', TAG_COMMAND, response, False,
                       f'The effective tag policy for {PROD_ACCOUNT} should allow {", ".join(COST_CENTERS)}.', started)
    if verified:
        message += f' The effective policy for {PROD_ACCOUNT} shows it; Floci does not block noncompliant tags.'
    return _result('tag-policy', TAG_COMMAND, response, verified, message, started)


def step_delegated_admin() -> dict[str, Any]:
    started = time.perf_counter()
    security = _accounts().get(SECURITY_ACCOUNT)
    if not security:
        return _result('delegated-admin', DELEGATE_COMMAND, {}, False, f'Create {SECURITY_ACCOUNT} first.', started)
    organizations = _client('organizations')
    for service in TRUSTED_SERVICES:
        organizations.enable_aws_service_access(ServicePrincipal=service)
    delegated = {item['ServicePrincipal'] for item in organizations.list_delegated_services_for_account(
        AccountId=security['Id']).get('DelegatedServices', [])} if _is_delegated(security['Id']) else set()
    for service in DELEGATED_SERVICES:
        if service not in delegated:
            organizations.register_delegated_administrator(AccountId=security['Id'], ServicePrincipal=service)
    separate = {name: tiered_cost(gb) for name, gb in S3_USAGE_GB.items()}
    combined = tiered_cost(sum(S3_USAGE_GB.values()))
    billing = {'SeparateBills': separate, 'SeparateTotal': round(sum(separate.values()), 2),
               'ConsolidatedTotal': combined, 'MonthlySavings': round(sum(separate.values()) - combined, 2),
               'TotalGB': sum(S3_USAGE_GB.values())}
    response = {'TrustedAccess': organizations.list_aws_service_access_for_organization().get('EnabledServicePrincipals'),
                'DelegatedServices': organizations.list_delegated_services_for_account(
                    AccountId=security['Id']).get('DelegatedServices'),
                'ConsolidatedBilling': billing}
    message = (f'{SECURITY_ACCOUNT} administers GuardDuty and Security Hub for the organization, with trusted access '
               f'for CloudTrail and Config. One bill pools {billing["TotalGB"]:,} GB of S3 Standard into the cheaper '
               f'tier after 50 TB: ${billing["ConsolidatedTotal"]:,.2f} a month instead of '
               f'${billing["SeparateTotal"]:,.2f} as separate accounts.')
    if billing['MonthlySavings'] <= 0:
        return _result('delegated-admin', DELEGATE_COMMAND, response, False,
                       'Pooled usage should cost less than separate bills.', started)
    _marker('delegated-admin', {'message': message})
    verified, check = _check_delegation()
    return _result('delegated-admin', DELEGATE_COMMAND, response, verified, message if verified else check, started)


def _is_delegated(account_id: str) -> bool:
    admins = _client('organizations').list_delegated_administrators().get('DelegatedAdministrators', [])
    return any(admin.get('Id') == account_id for admin in admins)


RUNNERS = {
    'organization': step_organization,
    'ou-structure': step_ou_structure,
    'member-accounts': step_member_accounts,
    'guardrail-scps': step_guardrail_scps,
    'scp-evaluation': step_scp_evaluation,
    'tag-policy': step_tag_policy,
    'delegated-admin': step_delegated_admin,
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
    except (ClientError, BotoCoreError, KeyError, ValueError):
        return False


def status() -> dict[str, Any]:
    checks = {
        'organization': lambda: _check_organization()[0],
        'ou-structure': lambda: _check_ous()[0],
        'member-accounts': lambda: _check_accounts()[0],
        'guardrail-scps': lambda: _check_scps()[0],
        'scp-evaluation': lambda: _check_marked('scp-evaluation', _check_scps)[0],
        'tag-policy': lambda: _check_tag_policy()[0],
        'delegated-admin': lambda: _check_delegation()[0],
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
    """Remove the lab's delegation, policies, accounts and OUs; delete the organization only if the lab created it
    and nothing else is left in it."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code in ('AWSOrganizationsNotInUseException', 'PolicyNotAttachedException',
                                              'AccountNotRegisteredException', 'PolicyTypeNotEnabledException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    organizations = _client('organizations')
    try:
        organization, root = _organization(), _root()
    except (ClientError, BotoCoreError) as exc:
        organization = root = None
        errors.append(f'lookup: {exc}')
    if organization and root:
        try:
            accounts, ous = _accounts(), _ous()
            scps, tag_policies = _policies(), _policies('TAG_POLICY')
        except (ClientError, BotoCoreError) as exc:
            accounts, ous, scps, tag_policies = {}, {}, {}, {}
            errors.append(f'lookup: {exc}')
        security = accounts.get(SECURITY_ACCOUNT)
        if security:
            for service in DELEGATED_SERVICES:
                attempt(f'delegated-admin:{service}', lambda service=service: organizations.deregister_delegated_administrator(
                    AccountId=security['Id'], ServicePrincipal=service))
        lab_policies = [(name, scps[name]) for name in SCPS if name in scps]
        if TAG_POLICY in tag_policies:
            lab_policies.append((TAG_POLICY, tag_policies[TAG_POLICY]))
        for name, policy in lab_policies:
            for target in _targets(policy['Id']):
                attempt(f'detach:{name}', lambda policy=policy, target=target: organizations.detach_policy(
                    PolicyId=policy['Id'], TargetId=target))
            attempt(f'policy:{name}', lambda policy=policy: organizations.delete_policy(PolicyId=policy['Id']))
        for label, account in _lab_accounts_any_status().items():
            name = label.split(':')[0]
            try:
                organizations.remove_account_from_organization(AccountId=account['Id'])
                deleted.append(f'account:{name}')
            except ClientError:
                # AWS removes only accounts that can stand alone; close the rest.
                attempt(f'account:{name}', lambda account=account: organizations.close_account(AccountId=account['Id']))
        for name in (PROD_OU, DEV_OU, SECURITY_OU, WORKLOADS_OU):
            if name in ous:
                attempt(f'ou:{name}', lambda name=name: organizations.delete_organizational_unit(
                    OrganizationalUnitId=ous[name]['Id']))
        try:
            root_tags = {tag['Key']: tag['Value'] for tag in
                         organizations.list_tags_for_resource(ResourceId=root['Id']).get('Tags', [])}
        except (ClientError, BotoCoreError):
            root_tags = {}
        if root_tags.get('Lab') == LAB_KEY:
            try:
                others = [account for account in organizations.list_accounts().get('Accounts', [])
                          if account['Id'] != organization.get('MasterAccountId') and account.get('Status') == 'ACTIVE']
                units = organizations.list_organizational_units_for_parent(ParentId=root['Id']).get(
                    'OrganizationalUnits', [])
            except (ClientError, BotoCoreError):
                others, units = ['unknown'], []
            if not others and not units:
                attempt(f'organization:{organization["Id"]}', lambda: organizations.delete_organization())

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws organizations deregister-delegated-administrator --account-id <{SECURITY_ACCOUNT}-id> '
            '--service-principal guardduty.amazonaws.com   # and securityhub\n'
            'aws organizations detach-policy / delete-policy   # the three SCPs and the tag policy\n'
            'aws organizations remove-account-from-organization --account-id <id>   # or close-account\n'
            'aws organizations delete-organizational-unit --organizational-unit-id <id>   # each lab OU\n'
            'aws organizations delete-organization   # only if the lab created it and it is empty'
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
            else 'The lab accounts, OUs, policies and delegation are removed.',
        },
    }
