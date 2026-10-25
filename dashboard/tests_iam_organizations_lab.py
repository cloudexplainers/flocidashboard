"""Multi-account guardrails lab: organization, OUs, member accounts, guardrail SCPs, SCP evaluation, tag policy, and
delegated administration. The fake behaves like Floci 2.3.0: the Organizations control plane works, SCP content is
not validated, and member-account roles cannot be assumed."""

import importlib
import itertools
import json
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.iam_organizations_labs')

SERVICE = 'iam'
LAB = 'multi-account-guardrails'
FULL_ACCESS = json.dumps({'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Action': '*', 'Resource': '*'}]})


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class FakeOrganizations:
    def __init__(self, validate_scps=False, existing=False):
        self.ids = itertools.count(1)
        self.validate_scps = validate_scps
        self.org = None
        self.root = None
        self.ous = {}        # id -> {'Name', 'parent'}
        self.accounts = {}   # id -> {'Name', 'Email', 'Status', 'parent'}
        self.policies = {}   # id -> {'Name', 'Type', 'Content', 'targets'}
        self.types = set()
        self.tags = {}
        self.trusted, self.delegated = set(), set()
        if existing:
            self.create_organization('ALL')
            self.tags = {}

    def _next(self, prefix):
        return f'{prefix}{next(self.ids):04d}'

    def _require(self):
        if not self.org:
            raise client_error('AWSOrganizationsNotInUseException')

    # Organization and root
    def create_organization(self, FeatureSet):
        if self.org:
            raise client_error('AlreadyInOrganizationException')
        self.org = {'Id': 'o-lab', 'FeatureSet': FeatureSet, 'MasterAccountId': '000000000000'}
        self.root = 'r-lab'
        self.accounts['000000000000'] = {'Name': 'management-account', 'Email': 'm@example.com', 'Status': 'ACTIVE',
                                         'parent': self.root}
        self.policies['p-FullAWSAccess'] = {'Name': 'FullAWSAccess', 'Type': 'SERVICE_CONTROL_POLICY',
                                            'Content': FULL_ACCESS, 'targets': {self.root, '000000000000'}}

    def describe_organization(self):
        self._require()
        return {'Organization': dict(self.org)}

    def delete_organization(self):
        if any(account_id != '000000000000' for account_id in self.accounts) or self.ous:
            raise client_error('OrganizationNotEmptyException')
        self.org, self.root, self.policies, self.types = None, None, {}, set()
        self.accounts = {}

    def list_roots(self):
        self._require()
        return {'Roots': [{'Id': self.root, 'PolicyTypes': [{'Type': kind, 'Status': 'ENABLED'} for kind in self.types]}]}

    def enable_policy_type(self, RootId, PolicyType):
        if PolicyType in self.types:
            raise client_error('PolicyTypeAlreadyEnabledException')
        self.types.add(PolicyType)

    def tag_resource(self, ResourceId, Tags):
        self.tags.setdefault(ResourceId, {}).update({tag['Key']: tag['Value'] for tag in Tags})

    def list_tags_for_resource(self, ResourceId):
        return {'Tags': [{'Key': key, 'Value': value} for key, value in self.tags.get(ResourceId, {}).items()]}

    # OUs
    def create_organizational_unit(self, ParentId, Name, Tags):
        if any(unit['Name'] == Name and unit['parent'] == ParentId for unit in self.ous.values()):
            raise client_error('DuplicateOrganizationalUnitException')
        ou_id = self._next('ou-lab-')
        self.ous[ou_id] = {'Name': Name, 'parent': ParentId}
        self.policies['p-FullAWSAccess']['targets'].add(ou_id)
        return {'OrganizationalUnit': {'Id': ou_id, 'Name': Name}}

    def list_organizational_units_for_parent(self, ParentId):
        return {'OrganizationalUnits': [{'Id': key, 'Name': unit['Name']} for key, unit in self.ous.items()
                                        if unit['parent'] == ParentId]}

    def delete_organizational_unit(self, OrganizationalUnitId):
        if any(item['parent'] == OrganizationalUnitId for item in [*self.ous.values(), *self.accounts.values()]):
            raise client_error('OrganizationalUnitNotEmptyException')
        del self.ous[OrganizationalUnitId]
        for policy in self.policies.values():
            policy['targets'].discard(OrganizationalUnitId)

    # Accounts
    def create_account(self, Email, AccountName, Tags):
        if any(account['Email'] == Email for account in self.accounts.values()):
            return {'CreateAccountStatus': {'Id': 'car-1', 'State': 'FAILED', 'FailureReason': 'EMAIL_ALREADY_EXISTS'}}
        account_id = str(100000000000 + next(self.ids))
        self.accounts[account_id] = {'Name': AccountName, 'Email': Email, 'Status': 'ACTIVE', 'parent': self.root}
        self.policies['p-FullAWSAccess']['targets'].add(account_id)
        return {'CreateAccountStatus': {'Id': 'car-1', 'State': 'SUCCEEDED', 'AccountId': account_id}}

    def list_accounts(self):
        self._require()
        return {'Accounts': [{'Id': key, 'Name': item['Name'], 'Email': item['Email'], 'Status': item['Status']}
                             for key, item in self.accounts.items()]}

    def list_parents(self, ChildId):
        parent = (self.accounts.get(ChildId) or self.ous.get(ChildId) or {}).get('parent')
        if not parent:
            return {'Parents': []}
        return {'Parents': [{'Id': parent, 'Type': 'ROOT' if parent == self.root else 'ORGANIZATIONAL_UNIT'}]}

    def move_account(self, AccountId, SourceParentId, DestinationParentId):
        self.accounts[AccountId]['parent'] = DestinationParentId

    def remove_account_from_organization(self, AccountId):
        del self.accounts[AccountId]

    def close_account(self, AccountId):
        self.accounts[AccountId]['Status'] = 'PENDING_CLOSURE'

    # Policies
    def create_policy(self, Name, Description, Type, Content):
        if self.validate_scps and Type == 'SERVICE_CONTROL_POLICY' and lab.scp_problems(Content):
            raise client_error('MalformedPolicyDocumentException')
        policy_id = self._next('p-')
        self.policies[policy_id] = {'Name': Name, 'Type': Type, 'Content': Content, 'targets': set()}
        return {'Policy': {'PolicySummary': {'Id': policy_id, 'Name': Name, 'Type': Type}}}

    def update_policy(self, PolicyId, Content, Description=None):
        self.policies[PolicyId]['Content'] = Content

    def delete_policy(self, PolicyId):
        if self.policies[PolicyId]['targets']:
            raise client_error('PolicyInUseException')
        del self.policies[PolicyId]

    def list_policies(self, Filter):
        self._require()
        return {'Policies': [{'Id': key, 'Name': item['Name']} for key, item in self.policies.items()
                             if item['Type'] == Filter]}

    def describe_policy(self, PolicyId):
        return {'Policy': {'Content': self.policies[PolicyId]['Content']}}

    def attach_policy(self, PolicyId, TargetId):
        self.policies[PolicyId]['targets'].add(TargetId)

    def detach_policy(self, PolicyId, TargetId):
        self.policies[PolicyId]['targets'].discard(TargetId)

    def list_targets_for_policy(self, PolicyId):
        return {'Targets': [{'TargetId': target} for target in sorted(self.policies[PolicyId]['targets'])]}

    def list_policies_for_target(self, TargetId, Filter):
        return {'Policies': [{'Id': key} for key, item in self.policies.items()
                             if item['Type'] == Filter and TargetId in item['targets']]}

    def describe_effective_policy(self, PolicyType, TargetId):
        attached = [item for item in self.policies.values() if item['Type'] == PolicyType and self.root in item['targets']]
        if not attached:
            raise client_error('EffectivePolicyNotFoundException')
        return {'EffectivePolicy': {'PolicyContent': attached[0]['Content'], 'TargetId': TargetId}}

    # Services
    def enable_aws_service_access(self, ServicePrincipal):
        self.trusted.add(ServicePrincipal)

    def list_aws_service_access_for_organization(self):
        return {'EnabledServicePrincipals': [{'ServicePrincipal': item} for item in sorted(self.trusted)]}

    def register_delegated_administrator(self, AccountId, ServicePrincipal):
        if (AccountId, ServicePrincipal) in self.delegated:
            raise client_error('AccountAlreadyRegisteredException')
        self.delegated.add((AccountId, ServicePrincipal))

    def deregister_delegated_administrator(self, AccountId, ServicePrincipal):
        if (AccountId, ServicePrincipal) not in self.delegated:
            raise client_error('AccountNotRegisteredException')
        self.delegated.discard((AccountId, ServicePrincipal))

    def list_delegated_administrators(self):
        return {'DelegatedAdministrators': [{'Id': account} for account in sorted({item[0] for item in self.delegated})]}

    def list_delegated_services_for_account(self, AccountId):
        return {'DelegatedServices': [{'ServicePrincipal': service} for account, service in self.delegated
                                      if account == AccountId]}

    # STS
    def assume_role(self, RoleArn, RoleSessionName):
        raise client_error('AccessDenied', 'not authorized to perform: sts:AssumeRole')


class MultiAccountGuardrailsLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeOrganizations()
        patcher = patch('dashboard.labs.iam_organizations_labs._client', side_effect=lambda name: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.iam_organizations_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def policy_id(self, name):
        return next(key for key, item in self.fake.policies.items() if item['Name'] == name)

    def test_lab_is_registered_with_seven_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 7)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '1.1'), tasks)
        self.assertIn(('CLF-C02', '4.2'), tasks)

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_scp_validation_and_evaluation_helpers(self):
        self.assertEqual(lab.scp_problems(lab.INVALID_SCP_CONTENT), ['not valid JSON'])
        for info in lab.SCPS.values():
            self.assertEqual(lab.scp_problems(json.dumps(info['document'])), [])
        bad = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': '*', 'Action': 's3:*',
                                                      'Condition': {'Bool': {'aws:SecureTransport': 'true'}}}]}
        self.assertEqual(len(lab.scp_problems(json.dumps(bad))), 2)
        full = json.loads(FULL_ACCESS)
        region = lab.SCPS[lab.REGION_SCP]['document']
        levels = [('root', [full]), ('lab-workloads', [full, region])]
        self.assertEqual(lab.evaluate_scps(levels, 's3:CreateBucket', {'aws:RequestedRegion': 'eu-west-1'})['Decision'],
                         'denied')
        self.assertEqual(lab.evaluate_scps(levels, 'iam:CreateRole', {'aws:RequestedRegion': 'eu-west-1'})['Decision'],
                         'allowed')
        no_allow = lab.evaluate_scps([('root', [full]), ('ou', [region])], 's3:GetObject',
                                     {'aws:RequestedRegion': 'us-east-1'})
        self.assertEqual(no_allow['Reason'], 'no SCP at this level allows it')
        self.assertEqual(lab.tiered_cost(51_200), 1177.6)
        self.assertEqual(lab.tiered_cost(60_000), round(51_200 * 0.023 + 8_800 * 0.022, 2))

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 7)

    def test_messages_and_evaluations(self):
        results = self.run_all()
        self.assertIn('accepted a truncated SCP', results['guardrail-scps']['verification']['message'])
        self.assertIn('cannot enforce SCPs', results['scp-evaluation']['verification']['message'])
        rows = results['scp-evaluation']['json']['Evaluations']
        self.assertEqual({row['Request']: row['Decision'] for row in rows}, lab.EXPECTED)
        billing = results['delegated-admin']['json']['ConsolidatedBilling']
        self.assertGreater(billing['MonthlySavings'], 0)
        self.assertEqual(billing['TotalGB'], 82_000)

    def test_validated_scps_are_reported(self):
        self.fake.validate_scps = True
        results = self.run_all()
        self.assertTrue(results['guardrail-scps']['verified'])
        self.assertNotIn('truncated', results['guardrail-scps']['verification']['message'])

    def test_existing_organization_is_reused_and_kept(self):
        self.fake = FakeOrganizations(existing=True)
        results = self.run_all()
        self.assertIn('reuses it', results['organization']['verification']['message'])
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertIsNotNone(self.fake.org)
        self.assertFalse(self.fake.ous)
        self.assertEqual(list(self.fake.accounts), ['000000000000'])

    def test_broken_scps_and_wrong_accounts_are_caught(self):
        self.run_all()
        self.fake.policies[self.policy_id(lab.DEV_SCP)]['Content'] = lab.INVALID_SCP_CONTENT
        self.assertIn('not valid JSON', lab._check_scps()[1])
        self.assertEqual(self.failed(), ['guardrail-scps', 'scp-evaluation'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'guardrail-scps')['verified'])
        dev = next(key for key, item in self.fake.accounts.items() if item['Name'] == lab.DEV_ACCOUNT)
        self.fake.accounts[dev]['parent'] = self.fake.root
        self.assertIn('must be in lab-dev', lab._check_accounts()[1])

    def test_detached_scp_changes_the_evaluation(self):
        self.run_all()
        policy = self.fake.policies[self.policy_id(lab.DEV_SCP)]
        policy['targets'].clear()
        self.assertIn('attached', lab._check_scps()[1])
        result = run_lab_step(SERVICE, LAB, 'scp-evaluation')
        self.assertFalse(result['verified'])

    def test_removing_full_access_is_caught(self):
        self.run_all()
        workloads = next(key for key, item in self.fake.ous.items() if item['Name'] == lab.WORKLOADS_OU)
        self.fake.policies['p-FullAWSAccess']['targets'].discard(workloads)
        self.assertIn('lost FullAWSAccess', lab._check_scps()[1])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual(len(self.fake.ous), 4)
        self.assertEqual(len(self.fake.accounts), 4)
        self.assertEqual(len([item for item in self.fake.policies.values() if item['Type'] == 'SERVICE_CONTROL_POLICY']), 4)

    def test_closed_lab_accounts_are_replaced(self):
        self.run_all()
        for account in self.fake.accounts.values():
            if account['Name'] in lab.ACCOUNTS:
                account['Status'] = 'PENDING_CLOSURE'
        result = run_lab_step(SERVICE, LAB, 'member-accounts')
        self.assertTrue(result['verified'], result['verification']['message'])

    def test_steps_out_of_order_explain_what_is_missing(self):
        for step in ('ou-structure', 'member-accounts', 'guardrail-scps', 'scp-evaluation', 'tag-policy', 'delegated-admin'):
            self.assertIn('first', run_lab_step(SERVICE, LAB, step)['verification']['message'], step)

    def test_reset_removes_everything_including_the_labs_organization(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertIsNone(self.fake.org)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
