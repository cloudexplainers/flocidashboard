"""Serverless environments lab: artifacts in S3, AppConfig flags per environment, a SAM change set, dev and prod
stacks from one template, a dev-only release, and a flag flip in prod. The fake behaves like Floci 2.3.0: the SAM
transform adds a version and a live alias that points at $LATEST, an update publishes no new version, AppConfig
deployments finish at once, and flag constraints are not validated. Invocations run the lab's real layer and handler
code against the fake's flags and tables."""

import importlib
import io
import itertools
import json
import types
import zipfile
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.cloudformation_sam_labs')

SERVICE = 'cloudformation'
LAB = 'sam-environments'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class Body:
    def __init__(self, data: bytes):
        self.data = data

    def read(self):
        return self.data


def unzip(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name).decode() for name in archive.namelist()}


class FakeAws:
    def __init__(self, validate_constraints=False, publish_versions=False):
        self.validate_constraints = validate_constraints
        self.publish_versions = publish_versions
        self.ids = itertools.count(1)
        self.buckets: dict[str, dict[str, bytes]] = {}
        self.apps: dict[str, dict] = {}
        self.strategies: dict[str, dict] = {}
        self.stacks: dict[str, dict] = {}
        self.change_sets: dict[tuple[str, str], dict] = {}
        self.functions: dict[str, dict] = {}
        self.tables: dict[str, list] = {}

    def _id(self):
        return f'id{next(self.ids):04d}'

    # S3
    def create_bucket(self, Bucket):
        if Bucket in self.buckets:
            raise client_error('BucketAlreadyOwnedByYou')
        self.buckets[Bucket] = {}

    def put_bucket_versioning(self, Bucket, VersioningConfiguration):
        pass

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.buckets[Bucket][Key] = Body

    def list_objects_v2(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        return {'Contents': [{'Key': key} for key in self.buckets[Bucket]]}

    def list_object_versions(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        return {'Versions': [{'Key': key, 'VersionId': 'v1'} for key in self.buckets[Bucket]]}

    def delete_objects(self, Bucket, Delete):
        for item in Delete['Objects']:
            self.buckets[Bucket].pop(item['Key'], None)

    def delete_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        del self.buckets[Bucket]

    # AppConfig
    def list_applications(self):
        return {'Items': [{'Id': app_id, 'Name': app['Name']} for app_id, app in self.apps.items()]}

    def create_application(self, Name, Description=''):
        app_id = self._id()
        self.apps[app_id] = {'Name': Name, 'environments': {}, 'profiles': {}}
        return {'Id': app_id, 'Name': Name}

    def delete_application(self, ApplicationId):
        del self.apps[ApplicationId]

    def list_environments(self, ApplicationId):
        return {'Items': [{'Id': env_id, 'Name': env['Name']} for env_id, env in self.apps[ApplicationId]['environments'].items()]}

    def create_environment(self, ApplicationId, Name, Description=''):
        self.apps[ApplicationId]['environments'][self._id()] = {'Name': Name, 'deployed': None, 'deployments': []}

    def delete_environment(self, ApplicationId, EnvironmentId):
        del self.apps[ApplicationId]['environments'][EnvironmentId]

    def list_configuration_profiles(self, ApplicationId):
        return {'Items': [{'Id': profile_id, 'Name': profile['Name']}
                          for profile_id, profile in self.apps[ApplicationId]['profiles'].items()]}

    def create_configuration_profile(self, ApplicationId, Name, LocationUri, Type):
        self.apps[ApplicationId]['profiles'][self._id()] = {'Name': Name, 'versions': {}, 'next': 1}

    def delete_configuration_profile(self, ApplicationId, ConfigurationProfileId):
        del self.apps[ApplicationId]['profiles'][ConfigurationProfileId]

    def create_hosted_configuration_version(self, ApplicationId, ConfigurationProfileId, ContentType, Content, Description=''):
        document = json.loads(Content)
        if self.validate_constraints:
            for flag, values in document['values'].items():
                maximum = document['flags'][flag]['attributes']['percent']['constraints']['maximum']
                if values.get('percent', 0) > maximum:
                    raise client_error('BadRequestException', 'percent is above its maximum')
        profile = self.apps[ApplicationId]['profiles'][ConfigurationProfileId]
        number = profile['next']
        profile['next'] += 1
        profile['versions'][number] = document
        return {'VersionNumber': number}

    def list_hosted_configuration_versions(self, ApplicationId, ConfigurationProfileId):
        versions = self.apps[ApplicationId]['profiles'][ConfigurationProfileId]['versions']
        return {'Items': [{'VersionNumber': number} for number in versions]}

    def delete_hosted_configuration_version(self, ApplicationId, ConfigurationProfileId, VersionNumber):
        del self.apps[ApplicationId]['profiles'][ConfigurationProfileId]['versions'][VersionNumber]

    def list_deployment_strategies(self):
        return {'Items': [{'Id': strategy_id, 'Name': item['Name']} for strategy_id, item in self.strategies.items()]}

    def create_deployment_strategy(self, Name, **kwargs):
        self.strategies[self._id()] = {'Name': Name, **kwargs}

    def delete_deployment_strategy(self, DeploymentStrategyId):
        del self.strategies[DeploymentStrategyId]

    def start_deployment(self, ApplicationId, EnvironmentId, DeploymentStrategyId, ConfigurationProfileId,
                         ConfigurationVersion, Description=''):
        environment = self.apps[ApplicationId]['environments'][EnvironmentId]
        environment['deployed'] = (ConfigurationProfileId, int(ConfigurationVersion))
        environment['deployments'].append('COMPLETE')
        return {'DeploymentNumber': len(environment['deployments']), 'State': 'COMPLETE'}

    def get_deployment(self, ApplicationId, EnvironmentId, DeploymentNumber):
        return {'State': 'COMPLETE', 'PercentageComplete': 100.0}

    def served(self, app_name_or_id, environment_name_or_id):
        app_id = next(app_id for app_id, app in self.apps.items() if app_name_or_id in (app_id, app['Name']))
        app = self.apps[app_id]
        environment = next(env for env_id, env in app['environments'].items() if environment_name_or_id in (env_id, env['Name']))
        if not environment['deployed']:
            return b''
        profile_id, version = environment['deployed']
        values = app['profiles'][profile_id]['versions'][version]['values']
        return json.dumps({name: value if value.get('enabled') else {'enabled': False}
                           for name, value in values.items()}).encode()

    def start_configuration_session(self, ApplicationIdentifier, EnvironmentIdentifier, ConfigurationProfileIdentifier,
                                    **kwargs):
        return {'InitialConfigurationToken': json.dumps([ApplicationIdentifier, EnvironmentIdentifier])}

    def get_latest_configuration(self, ConfigurationToken):
        app, environment = json.loads(ConfigurationToken)
        return {'Configuration': Body(self.served(app, environment))}

    # CloudFormation
    def describe_stacks(self, StackName):
        if StackName not in self.stacks:
            raise client_error('ValidationError', f'Stack with id {StackName} does not exist')
        stack = self.stacks[StackName]
        return {'Stacks': [{'StackName': StackName, 'StackStatus': stack['status'], 'CreationTime': stack['created'],
                            'LastUpdatedTime': stack.get('updated')}]}

    def create_change_set(self, StackName, ChangeSetName, ChangeSetType, TemplateBody, Parameters, Capabilities, Tags):
        body = json.loads(TemplateBody)
        stage = Parameters[0]['ParameterValue']
        resources = dict(body['Resources'])
        if 'CAPABILITY_AUTO_EXPAND' not in Capabilities:
            raise client_error('InsufficientCapabilitiesException')
        resources['QuoteFunctionVersion'] = {'Type': 'AWS::Lambda::Version'}
        resources['QuoteFunctionAliasLive'] = {'Type': 'AWS::Lambda::Alias'}
        types_ = {name: ('AWS::DynamoDB::Table' if item['Type'] == 'AWS::Serverless::SimpleTable' else
                         'AWS::Lambda::Function' if item['Type'] == 'AWS::Serverless::Function' else item['Type'])
                  for name, item in resources.items()}
        if ChangeSetType == 'CREATE':
            self.stacks[StackName] = {'status': 'REVIEW_IN_PROGRESS', 'created': 'now', 'resources': {}, 'template': None}
            changes = [{'ResourceChange': {'Action': 'Add', 'LogicalResourceId': name, 'ResourceType': kind}}
                       for name, kind in types_.items()]
        else:
            old = self.stacks[StackName]['template']
            changed = old['Resources']['QuoteFunction']['Properties']['CodeUri'] != body['Resources']['QuoteFunction']['Properties']['CodeUri']
            changes = [{'ResourceChange': {'Action': 'Modify', 'LogicalResourceId': 'QuoteFunction',
                                           'ResourceType': 'AWS::Lambda::Function', 'Replacement': 'False'}}] if changed else []
        status = 'CREATE_COMPLETE' if changes else 'FAILED'
        self.change_sets[(StackName, ChangeSetName)] = {
            'Status': status, 'StatusReason': None if changes else "The submitted information didn't contain changes.",
            'Changes': changes, 'template': body, 'stage': stage, 'types': types_}

    def describe_change_set(self, StackName, ChangeSetName):
        item = self.change_sets[(StackName, ChangeSetName)]
        return {'Status': item['Status'], 'StatusReason': item['StatusReason'], 'Changes': item['Changes']}

    def execute_change_set(self, StackName, ChangeSetName):
        change_set = self.change_sets[(StackName, ChangeSetName)]
        stack = self.stacks[StackName]
        stage, body = change_set['stage'], change_set['template']
        function_name, table = lab.FUNCTIONS[stage], lab.TABLES[stage]
        code_key = body['Resources']['QuoteFunction']['Properties']['CodeUri']['Key']
        creating = stack['status'] == 'REVIEW_IN_PROGRESS'
        function = self.functions.setdefault(function_name, {'versions': [], 'alias': '$LATEST'})
        function['code'] = self.buckets[lab.BUCKET][code_key]
        function['stage'] = stage
        if creating:
            function['versions'] = ['1']
            self.tables.setdefault(table, [])
        elif self.publish_versions:
            function['versions'].append(str(len(function['versions']) + 1))
            function['alias'] = function['versions'][-1]
        stack.update({'status': 'CREATE_COMPLETE' if creating else 'UPDATE_COMPLETE', 'template': body,
                      'resources': change_set['types'], 'updated': None if creating else f'u{self._id()}'})

    def describe_stack_resources(self, StackName):
        return {'StackResources': [{'LogicalResourceId': name, 'ResourceType': kind}
                                   for name, kind in self.stacks[StackName]['resources'].items()]}

    def delete_stack(self, StackName):
        stack = self.stacks.pop(StackName, None)
        if stack and stack['template']:
            stage = next(stage for stage, name in lab.STACKS.items() if name == StackName)
            self.functions.pop(lab.FUNCTIONS[stage], None)
            self.tables.pop(lab.TABLES[stage], None)

    # Lambda: run the real layer and handler code
    def invoke(self, FunctionName, Payload):
        name, _, alias = FunctionName.partition(':')
        function = self.functions[name]
        stage = function['stage']
        fake = self
        pricing = types.ModuleType('pricing')
        exec(compile(lab.LAYER_SOURCE, 'pricing.py', 'exec'), pricing.__dict__)  # noqa: S102

        class Table:
            def put_item(self, Item):
                fake.tables[lab.TABLES[stage]].append(Item)

        boto3 = types.SimpleNamespace(client=lambda service: fake,
                                      resource=lambda service: types.SimpleNamespace(Table=lambda name: Table()))
        environ = {'STAGE': stage, 'FLAGS_APP': lab.FLAGS_APP, 'FLAGS_PROFILE': lab.FLAGS_PROFILE, 'TABLE': lab.TABLES[stage]}
        with patch.dict('sys.modules', {'boto3': boto3, 'pricing': pricing}), patch.dict('os.environ', environ):
            namespace: dict = {}
            exec(compile(unzip(function['code'])['app.py'], 'app.py', 'exec'), namespace)  # noqa: S102
            result = namespace['handler'](json.loads(Payload), types.SimpleNamespace(aws_request_id=self._id()))
        return {'Payload': Body(json.dumps(result).encode())}

    def get_alias(self, FunctionName, Name):
        if FunctionName not in self.functions:
            raise client_error('ResourceNotFoundException')
        return {'FunctionVersion': self.functions[FunctionName]['alias']}

    def list_versions_by_function(self, FunctionName):
        if FunctionName not in self.functions:
            raise client_error('ResourceNotFoundException')
        return {'Versions': [{'Version': '$LATEST'}] + [{'Version': v} for v in self.functions[FunctionName]['versions']]}

    # DynamoDB
    def scan(self, TableName, Select):
        if TableName not in self.tables:
            raise client_error('ResourceNotFoundException')
        return {'Count': len(self.tables[TableName])}


class SamEnvironmentsLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        patcher = patch('dashboard.labs.cloudformation_sam_labs._client', side_effect=lambda name: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.cloudformation_sam_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self, results):
        return {key: result['verification']['message'] for key, result in results.items() if not result['verified']}

    def test_lab_is_registered_with_seven_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 7)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('DVA-C02', '3.1'), tasks)
        primary = [tag for tag in LAB_EXAM_TAGS[(SERVICE, LAB)] if tag['exam'] == 'DVA-C02' and tag['primary']]
        self.assertEqual(primary[0]['task'], '3.3')

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_template_works_around_the_transform_gaps(self):
        resources = lab.template(1)['Resources']
        self.assertEqual(resources['PricingLayer']['Type'], 'AWS::Lambda::LayerVersion')
        self.assertEqual(resources['QuoteFunction']['Properties']['Role'], {'Fn::GetAtt': ['QuoteRole', 'Arn']})
        self.assertNotIn('Policies', resources['QuoteFunction']['Properties'])
        self.assertEqual(lab.template(2)['Resources']['QuoteFunction']['Properties']['CodeUri']['Key'], lab.app_key(2))
        self.assertIn('RELEASE = "2"', lab.app_source(2))

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        self.assertEqual(self.failed(results), {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 7)

    def test_messages_report_results_and_floci_gaps(self):
        results = self.run_all()
        self.assertIn('Floci does not validate constraints', results['feature-flags']['verification']['message'])
        self.assertIn('generated QuoteFunctionAliasLive, QuoteFunctionVersion', results['review-changeset']['verification']['message'])
        self.assertIn('priced 20 items at 170', results['deploy-dev']['verification']['message'])
        self.assertIn('alias points at $LATEST', results['deploy-dev']['verification']['message'])
        self.assertIn('priced 20 items at 200', results['deploy-prod']['verification']['message'])
        self.assertIn('No new version was published', results['update-dev']['verification']['message'])
        self.assertIn('at 170 instead of 200', results['flag-prod']['verification']['message'])
        self.assertEqual(len(self.fake.tables[lab.TABLES['dev']]), 2)

    def test_aws_like_constraints_and_versions_are_reported_as_on_aws(self):
        self.fake.validate_constraints = True
        self.fake.publish_versions = True
        results = self.run_all()
        self.assertEqual(self.failed(results), {})
        self.assertIn('rejected (BadRequestException)', results['feature-flags']['verification']['message'])
        self.assertNotIn('No new version', results['update-dev']['verification']['message'])

    def test_prod_with_its_flag_on_too_early_fails_deploy_prod(self):
        for step in lab.STEP_KEYS[:4]:
            run_lab_step(SERVICE, LAB, step)
        lab._deploy_flags('prod', True)
        result = run_lab_step(SERVICE, LAB, 'deploy-prod')
        self.assertFalse(result['verified'])
        self.assertIn('total 200', result['verification']['message'])

    def test_flag_prod_check_needs_the_flag_on(self):
        self.run_all()
        lab._deploy_flags('prod', False)
        self.assertIn('enabled', lab._check_flag_prod()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'flag-prod')['verified'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertEqual(self.failed(results), {})
        self.assertIn('UPDATE change set', results['review-changeset']['verification']['message'])
        self.assertEqual(len(self.fake.apps), 1)
        self.assertEqual(len(self.fake.strategies), 1)
        self.assertEqual(set(self.fake.stacks), set(lab.STACKS.values()))

    def test_steps_out_of_order_explain_what_is_missing(self):
        for step in lab.STEP_KEYS[2:]:
            with self.subTest(step=step):
                self.assertIn('first', run_lab_step(SERVICE, LAB, step)['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        for store in (self.fake.buckets, self.fake.apps, self.fake.strategies, self.fake.stacks, self.fake.functions):
            self.assertFalse(store)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
