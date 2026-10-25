"""Account hardening lab: password policy, virtual MFA, deny without MFA, permissions boundary, Access Analyzer review,
credential report audit, and root user check. The fake behaves like Floci 2.3.0: the password policy, MFA codes, the
MFA deny, and boundaries are enforced; sessions never carry MFA; analyzer findings are misrouted; and the credential
report is cached."""

import copy
import importlib
import itertools
import json
import time
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.iam_account_hardening_labs')

SERVICE = 'iam'
LAB = 'account-hardening'
SEED = 'JBSWY3DPEHPK3PXP'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class FakeAws:
    def __init__(self, enforce_password=True, check_codes=True, enforce_boundary=True, mfa_sessions=False,
                 stale_report=False):
        self.ids = itertools.count(1)
        self.enforce_password, self.check_codes = enforce_password, check_codes
        self.enforce_boundary, self.mfa_sessions, self.stale_report = enforce_boundary, mfa_sessions, stale_report
        self.password_policy = None
        self.users, self.groups, self.policies, self.roles = {}, {}, {}, {}
        self.virtual, self.analyzers, self.buckets = {}, {}, {}
        self.report_users = None
        self.root_keys = 0

    def _next(self, prefix):
        return f'{prefix}{next(self.ids):04d}'

    def _user(self, name):
        if name not in self.users:
            raise client_error('NoSuchEntity')
        return self.users[name]

    # Password policy and users
    def update_account_password_policy(self, **policy):
        self.password_policy = dict(policy, ExpirePasswords=True)

    def get_account_password_policy(self):
        if not self.password_policy:
            raise client_error('NoSuchEntity')
        return {'PasswordPolicy': dict(self.password_policy)}

    def delete_account_password_policy(self):
        self.password_policy = None

    def create_user(self, UserName, Tags=None, PermissionsBoundary=None):
        if UserName in self.users:
            raise client_error('EntityAlreadyExists')
        self.users[UserName] = {'UserName': UserName, 'Arn': f'arn:aws:iam::000000000000:user/{UserName}',
                                'boundary': PermissionsBoundary, 'login': None, 'mfa': [], 'keys': [], 'attached': []}

    def get_user(self, UserName):
        user = self._user(UserName)
        result = {'UserName': UserName, 'Arn': user['Arn']}
        if user['boundary']:
            result['PermissionsBoundary'] = {'PermissionsBoundaryType': 'Policy',
                                             'PermissionsBoundaryArn': user['boundary']}
        return {'User': result}

    def delete_user(self, UserName):
        self._user(UserName)
        del self.users[UserName]

    def create_login_profile(self, UserName, Password, PasswordResetRequired):
        user = self._user(UserName)
        if self.enforce_password and self.password_policy and lab.password_problems(Password, self.password_policy):
            raise client_error('PasswordPolicyViolation', 'Password does not conform to the account password policy.')
        user['login'] = {'UserName': UserName, 'PasswordResetRequired': PasswordResetRequired}

    def get_login_profile(self, UserName):
        login = self._user(UserName)['login']
        if not login:
            raise client_error('NoSuchEntity')
        return {'LoginProfile': dict(login)}

    def delete_login_profile(self, UserName):
        self._user(UserName)['login'] = None

    # MFA
    def create_virtual_mfa_device(self, VirtualMFADeviceName):
        serial = f'arn:aws:iam::000000000000:mfa/{VirtualMFADeviceName}'
        if serial in self.virtual:
            raise client_error('EntityAlreadyExists')
        self.virtual[serial] = {'SerialNumber': serial, 'user': None}
        return {'VirtualMFADevice': {'SerialNumber': serial, 'Base32StringSeed': SEED.encode()}}

    def list_virtual_mfa_devices(self):
        return {'VirtualMFADevices': [{'SerialNumber': serial} for serial in self.virtual]}

    def delete_virtual_mfa_device(self, SerialNumber):
        if SerialNumber not in self.virtual:
            raise client_error('NoSuchEntity')
        del self.virtual[SerialNumber]

    def enable_mfa_device(self, UserName, SerialNumber, AuthenticationCode1, AuthenticationCode2):
        user = self._user(UserName)
        now = time.time()
        valid = {lab.totp(SEED, now + offset) for offset in (-60, -30, 0, 30)}
        if self.check_codes and not {AuthenticationCode1, AuthenticationCode2} <= valid:
            raise client_error('InvalidAuthenticationCode', 'Invalid MFA one time pass code.')
        self.virtual[SerialNumber]['user'] = UserName
        user['mfa'].append(SerialNumber)

    def deactivate_mfa_device(self, UserName, SerialNumber):
        user = self._user(UserName)
        user['mfa'].remove(SerialNumber)
        self.virtual[SerialNumber]['user'] = None

    def list_mfa_devices(self, UserName):
        return {'MFADevices': [{'UserName': UserName, 'SerialNumber': serial} for serial in self._user(UserName)['mfa']]}

    # Groups
    def create_group(self, GroupName):
        if GroupName in self.groups:
            raise client_error('EntityAlreadyExists')
        self.groups[GroupName] = {'members': [], 'policies': {}}

    def _group(self, name):
        if name not in self.groups:
            raise client_error('NoSuchEntity')
        return self.groups[name]

    def put_group_policy(self, GroupName, PolicyName, PolicyDocument):
        self._group(GroupName)['policies'][PolicyName] = PolicyDocument

    def get_group_policy(self, GroupName, PolicyName):
        policies = self._group(GroupName)['policies']
        if PolicyName not in policies:
            raise client_error('NoSuchEntity')
        return {'PolicyDocument': json.loads(policies[PolicyName])}

    def delete_group_policy(self, GroupName, PolicyName):
        self._group(GroupName)['policies'].pop(PolicyName)

    def add_user_to_group(self, GroupName, UserName):
        members = self._group(GroupName)['members']
        if UserName not in members:
            members.append(UserName)

    def remove_user_from_group(self, GroupName, UserName):
        self._group(GroupName)['members'].remove(UserName)

    def get_group(self, GroupName):
        return {'Users': [{'UserName': name} for name in self._group(GroupName)['members']]}

    def list_groups_for_user(self, UserName):
        return {'Groups': [{'GroupName': name} for name, group in self.groups.items() if UserName in group['members']]}

    def delete_group(self, GroupName):
        group = self._group(GroupName)
        if group['members'] or group['policies']:
            raise client_error('DeleteConflict')
        del self.groups[GroupName]

    # Keys and policies
    def create_access_key(self, UserName):
        key = {'AccessKeyId': self._next('AKIA'), 'SecretAccessKey': 'secret', 'Status': 'Active',
               'CreateDate': '2026-10-25T00:00:00+00:00', 'user': UserName}
        self._user(UserName)['keys'].append(key)
        return {'AccessKey': dict(key)}

    def list_access_keys(self, UserName):
        return {'AccessKeyMetadata': [{key: value for key, value in item.items() if key != 'SecretAccessKey'}
                                      for item in self._user(UserName)['keys']]}

    def delete_access_key(self, UserName, AccessKeyId):
        user = self._user(UserName)
        user['keys'] = [key for key in user['keys'] if key['AccessKeyId'] != AccessKeyId]

    def create_policy(self, PolicyName, PolicyDocument, Description):
        self.policies[f'arn:aws:iam::000000000000:policy/{PolicyName}'] = PolicyDocument

    def get_policy(self, PolicyArn):
        if PolicyArn not in self.policies:
            raise client_error('NoSuchEntity')
        return {'Policy': {'Arn': PolicyArn}}

    def delete_policy(self, PolicyArn):
        if PolicyArn not in self.policies:
            raise client_error('NoSuchEntity')
        if any(user['boundary'] == PolicyArn for user in self.users.values()):
            raise client_error('DeleteConflict')
        del self.policies[PolicyArn]

    def put_user_permissions_boundary(self, UserName, PermissionsBoundary):
        self._user(UserName)['boundary'] = PermissionsBoundary

    def delete_user_permissions_boundary(self, UserName):
        self._user(UserName)['boundary'] = None

    def attach_user_policy(self, UserName, PolicyArn):
        attached = self._user(UserName)['attached']
        if PolicyArn not in attached:
            attached.append(PolicyArn)

    def detach_user_policy(self, UserName, PolicyArn):
        self._user(UserName)['attached'].remove(PolicyArn)

    def list_attached_user_policies(self, UserName):
        return {'AttachedPolicies': [{'PolicyArn': arn} for arn in self._user(UserName)['attached']]}

    # Roles
    def create_role(self, RoleName, AssumeRolePolicyDocument, Description):
        self.roles[RoleName] = AssumeRolePolicyDocument

    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': {'RoleName': RoleName, 'AssumeRolePolicyDocument': json.loads(self.roles[RoleName])}}

    def delete_role(self, RoleName):
        if self.roles.pop(RoleName, None) is None:
            raise client_error('NoSuchEntity')

    # Credential report and account summary
    def generate_credential_report(self):
        if self.report_users is None or not self.stale_report:
            self.report_users = copy.deepcopy(self.users)
            return {'State': 'STARTED', 'Description': 'Starting'}
        return {'State': 'COMPLETE', 'Description': 'Current report has already been generated within the past 4 hours.'}

    def get_credential_report(self):
        lines = ['user,arn,password_enabled,mfa_active,access_key_1_active,access_key_1_last_rotated,'
                 'access_key_2_active,access_key_2_last_rotated',
                 '<root_account>,arn:aws:iam::000000000000:root,not_supported,FALSE,FALSE,N/A,FALSE,N/A']
        for name, user in (self.report_users or {}).items():
            keys = user['keys']
            lines.append(','.join([
                name, user['Arn'], 'TRUE' if user['login'] else 'FALSE', 'TRUE' if user['mfa'] else 'FALSE',
                'TRUE' if keys else 'FALSE', keys[0]['CreateDate'] if keys else 'N/A', 'FALSE', 'N/A']))
        return {'Content': ('\n'.join(lines) + '\n').encode(), 'GeneratedTime': '2026-10-25T00:00:00Z'}

    def get_account_summary(self):
        return {'SummaryMap': {'AccountMFAEnabled': 0, 'AccountAccessKeysPresent': self.root_keys,
                               'AccountSigningCertificatesPresent': 0}}

    # Access Analyzer
    def create_analyzer(self, analyzerName, type, tags):
        self.analyzers[analyzerName] = {'name': analyzerName, 'type': type, 'status': 'ACTIVE',
                                        'arn': f'arn:aws:access-analyzer:us-east-1:000000000000:analyzer/{analyzerName}'}

    def get_analyzer(self, analyzerName):
        if analyzerName not in self.analyzers:
            raise client_error('ResourceNotFoundException')
        return {'analyzer': dict(self.analyzers[analyzerName])}

    def delete_analyzer(self, analyzerName):
        if self.analyzers.pop(analyzerName, None) is None:
            raise client_error('ResourceNotFoundException')

    def list_findings(self, analyzerArn):
        raise client_error('AuthorizationHeaderMalformed', 'This endpoint belongs to "s3".')

    # S3
    def create_bucket(self, Bucket):
        if Bucket in self.buckets:
            raise client_error('BucketAlreadyOwnedByYou')
        self.buckets[Bucket] = {'policy': None}

    def delete_public_access_block(self, Bucket):
        pass

    def put_bucket_policy(self, Bucket, Policy):
        self.buckets[Bucket]['policy'] = Policy

    def get_bucket_policy(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        if not self.buckets[Bucket]['policy']:
            raise client_error('NoSuchBucketPolicy')
        return {'Policy': self.buckets[Bucket]['policy']}

    def delete_bucket_policy(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        self.buckets[Bucket]['policy'] = None

    def delete_bucket(self, Bucket):
        if self.buckets.pop(Bucket, None) is None:
            raise client_error('NoSuchBucket')

    # Calls made as a lab user
    def owner(self, key_id):
        return next(user for user in self.users.values() if any(key['AccessKeyId'] == key_id for key in user['keys']))

    def as_user(self, key, session_token=None):
        return FakeIdentity(self, key, session_token)


class FakeIdentity:
    def __init__(self, fake, key, session_token):
        self.fake, self.key, self.session_token = fake, key, session_token

    def _user(self):
        key_id = self.key['AccessKeyId']
        if key_id.startswith('ASIA'):
            return self.fake.users[self.fake.sessions[key_id]]
        return self.fake.owner(key_id)

    def _mfa(self):
        return bool(self.session_token) and self.fake.mfa_sessions

    def _denied_without_mfa(self, action):
        user = self._user()
        for group in self.fake.groups.values():
            if user['UserName'] in group['members'] and lab.FORCE_MFA_POLICY in group['policies']:
                if action not in lab.MFA_SELF_SERVICE_ACTIONS and not self._mfa():
                    return True
        return False

    def _check(self, action):
        user = self._user()
        if self._denied_without_mfa(action):
            raise client_error('AccessDenied', f'User is not authorized to perform: {action}')
        if user['boundary'] and self.fake.enforce_boundary and not action.startswith(('s3:', 'iam:Get', 'sts:')):
            raise client_error('AccessDenied', f'User is not authorized to perform: {action}')

    def list_buckets(self):
        self._check('s3:ListAllMyBuckets')
        return {'Buckets': []}

    def list_mfa_devices(self, UserName):
        self._check('iam:ListMFADevices')
        return self.fake.list_mfa_devices(UserName)

    def create_user(self, UserName):
        self._check('iam:CreateUser')
        self.fake.create_user(UserName)

    def get_session_token(self, SerialNumber, TokenCode, DurationSeconds):
        session_id = self.fake._next('ASIA')
        self.fake.sessions = getattr(self.fake, 'sessions', {})
        self.fake.sessions[session_id] = self._user()['UserName']
        return {'Credentials': {'AccessKeyId': session_id, 'SecretAccessKey': 'secret', 'SessionToken': 'token'}}


class AccountHardeningLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        for target, side_effect in (
                ('_client', lambda name: self.fake),
                ('_user_client', lambda name, key, session_token=None: self.fake.as_user(key, session_token))):
            patcher = patch(f'dashboard.labs.iam_account_hardening_labs.{target}', side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.iam_account_hardening_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def test_lab_is_registered_with_seven_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 7)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '1.1'), tasks)
        self.assertIn(('CLF-C02', '2.3'), tasks)

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_helpers(self):
        self.assertEqual(lab.totp('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ', 59, digits=8), '94287082')  # RFC 6238 vector
        self.assertEqual(lab.password_problems('summer2026', lab.PASSWORD_POLICY),
                         ['at least 14 characters', 'an uppercase letter', 'a symbol'])
        self.assertEqual(lab.password_problems(lab.STRONG_PASSWORD, lab.PASSWORD_POLICY), [])
        self.assertEqual(lab.external_principals(lab.PUBLIC_BUCKET_POLICY), ['*'])
        self.assertEqual(lab.external_principals(lab.PARTNER_TRUST), ['arn:aws:iam::111122223333:root'])
        own = {'Statement': [{'Effect': 'Allow', 'Principal': {'AWS': 'arn:aws:iam::000000000000:root'}}]}
        self.assertEqual(lab.external_principals(own), [])
        self.assertEqual(lab._seed_text("b'ABC'"), 'ABC')

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 7)

    def test_messages_say_what_floci_enforces_and_skips(self):
        results = self.run_all()
        self.assertIn('refused "summer2026"', results['password-policy']['verification']['message'])
        self.assertIn('refused made-up codes', results['virtual-mfa']['verification']['message'])
        self.assertIn('denied too', results['require-mfa']['verification']['message'])
        self.assertIn('iam:CreateUser was denied', results['permission-boundary']['verification']['message'])
        self.assertIn('read the policies itself', results['access-analyzer']['verification']['message'])
        self.assertIn('Console password without MFA', results['credential-report']['verification']['message'])
        self.assertIn('no access keys', results['root-user']['verification']['message'])
        self.assertEqual(results['require-mfa']['json']['Calls']['ListOwnMfaDevicesWithoutMfa']['Outcome'], 'allowed')

    def test_mfa_sessions_that_carry_mfa_are_allowed(self):
        self.fake.mfa_sessions = True
        results = self.run_all()
        self.assertIn('MFA session from get-session-token was allowed', results['require-mfa']['verification']['message'])

    def test_unenforced_password_policy_is_reported_and_replaced(self):
        self.fake.enforce_password = False
        result = run_lab_step(SERVICE, LAB, 'password-policy')
        self.assertTrue(result['verified'])
        self.assertIn('accepted "summer2026"', result['verification']['message'])

    def test_unchecked_mfa_codes_are_reported(self):
        self.fake.check_codes = False
        run_lab_step(SERVICE, LAB, 'password-policy')
        result = run_lab_step(SERVICE, LAB, 'virtual-mfa')
        self.assertTrue(result['verified'])
        self.assertIn('does not check them', result['verification']['message'])

    def test_unenforced_boundary_fails_the_step(self):
        self.fake.enforce_boundary = False
        result = run_lab_step(SERVICE, LAB, 'permission-boundary')
        self.assertFalse(result['verified'])
        self.assertIn('denied iam:CreateUser', result['verification']['message'])

    def test_stale_report_rows_are_rebuilt_from_iam(self):
        self.fake.stale_report = True
        self.fake.report_users = {}
        results = self.run_all()
        self.assertTrue(results['credential-report']['verified'])
        self.assertIn('built those rows from the IAM APIs', results['credential-report']['verification']['message'])

    def test_root_access_keys_fail_the_root_check(self):
        self.run_all()
        self.fake.root_keys = 1
        self.assertEqual(self.failed(), ['root-user'])
        self.assertIn('delete them', run_lab_step(SERVICE, LAB, 'root-user')['verification']['message'])

    def test_drift_is_caught(self):
        self.run_all()
        self.fake.password_policy['MinimumPasswordLength'] = 8
        self.fake.groups[lab.ADMIN_GROUP]['members'].remove(lab.ADMIN_USER)
        self.fake.users[lab.DEV_USER]['boundary'] = None
        self.assertEqual(self.failed(), ['password-policy', 'require-mfa', 'permission-boundary'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual(len(self.fake.virtual), 1)
        self.assertEqual(len(self.fake.users[lab.ADMIN_USER]['keys']), 1)
        self.assertEqual(len(self.fake.users[lab.DEV_USER]['keys']), 1)

    def test_steps_out_of_order_explain_what_is_missing(self):
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'virtual-mfa')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'require-mfa')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'credential-report')['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        for store in (self.fake.users, self.fake.groups, self.fake.policies, self.fake.roles, self.fake.virtual,
                      self.fake.analyzers, self.fake.buckets):
            self.assertFalse(store)
        self.assertIsNone(self.fake.password_policy)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_keeps_a_password_policy_that_is_not_the_labs(self):
        self.fake.update_account_password_policy(MinimumPasswordLength=10)
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
        self.assertIsNotNone(self.fake.password_policy)
