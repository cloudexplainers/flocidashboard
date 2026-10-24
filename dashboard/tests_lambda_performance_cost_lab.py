"""Lambda performance and cost lab: create, memory sweep, versions, alias, weighted canary, reserved concurrency,
function URL, and promotion. The fake runs the real quote handler, routes alias calls by weight, throttles bursts over
the reserved concurrency, and serves the function URL the way Floci 2.3.0 does (unsigned requests too, and weights
that UpdateAlias never clears)."""

import copy
import importlib
import io
import itertools
import json
import os
import random
import types
import zipfile
from unittest.mock import patch
from urllib.parse import parse_qsl, urlparse

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.lambda_performance_labs')

SERVICE = 'lambda'
LAB = 'performance-cost'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class FakeAws:
    def __init__(self, enforce_concurrency=True, honor_weights=True, clear_weights=False):
        self.rng = random.Random(7)
        self.enforce_concurrency, self.honor_weights, self.clear_weights = enforce_concurrency, honor_weights, clear_weights
        self.roles, self.role_policies, self.function, self.versions = {}, {}, None, {}
        self.aliases, self.url_configs, self.reserved, self.log_groups = {}, {}, None, {lab.LOG_GROUP}
        self.held = 0
        self.ids = itertools.count(1)
        self.requests = []

    def client(self, name):
        return self

    # IAM and Logs
    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': self.roles[RoleName]}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description):
        self.roles[RoleName] = {'RoleName': RoleName}

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.role_policies[(RoleName, PolicyName)] = json.loads(PolicyDocument)

    def delete_role_policy(self, RoleName, PolicyName):
        if self.role_policies.pop((RoleName, PolicyName), None) is None:
            raise client_error('NoSuchEntity')

    def delete_role(self, RoleName):
        if self.roles.pop(RoleName, None) is None:
            raise client_error('NoSuchEntity')

    def delete_log_group(self, logGroupName):
        if logGroupName not in self.log_groups:
            raise client_error('ResourceNotFoundException')
        self.log_groups.discard(logGroupName)

    # Functions and versions
    def _require(self):
        if not self.function:
            raise client_error('ResourceNotFoundException')

    def _source(self, archive):
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            return handle.read('quote.py').decode()

    def get_function_configuration(self, FunctionName):
        self._require()
        return {key: copy.deepcopy(value) for key, value in self.function.items() if key != 'source'}

    def create_function(self, FunctionName, Code, MemorySize, Architectures, Description, **settings):
        self.function = {'FunctionName': FunctionName, 'Version': '$LATEST', 'State': 'Active',
                         'LastUpdateStatus': 'Successful', 'MemorySize': MemorySize, 'Architectures': Architectures,
                         'source': self._source(Code['ZipFile']), **settings}

    def update_function_code(self, FunctionName, ZipFile):
        self._require()
        self.function['source'] = self._source(ZipFile)

    def update_function_configuration(self, FunctionName, **settings):
        self._require()
        self.function.update(copy.deepcopy(settings))

    def delete_function(self, FunctionName):
        self._require()
        self.function, self.versions, self.aliases, self.url_configs, self.reserved = None, {}, {}, {}, None

    def publish_version(self, FunctionName, Description):
        self._require()
        number = str(len(self.versions) + 1)
        self.versions[number] = {**copy.deepcopy(self.function), 'Version': number, 'Description': Description}
        return {'Version': number}

    def list_versions_by_function(self, FunctionName, Marker=None):
        self._require()
        latest = {key: value for key, value in self.function.items() if key != 'source'}
        return {'Versions': [latest] + [{key: copy.deepcopy(value) for key, value in item.items() if key != 'source'}
                                        for item in self.versions.values()]}

    # Aliases
    def get_alias(self, FunctionName, Name):
        if Name not in self.aliases:
            raise client_error('ResourceNotFoundException')
        return copy.deepcopy(self.aliases[Name])

    def create_alias(self, FunctionName, Name, FunctionVersion, Description):
        self.aliases[Name] = {'Name': Name, 'FunctionVersion': FunctionVersion}

    def update_alias(self, FunctionName, Name, FunctionVersion, RoutingConfig):
        alias = self.aliases[Name]
        alias['FunctionVersion'] = FunctionVersion
        weights = RoutingConfig.get('AdditionalVersionWeights') or {}
        if weights or self.clear_weights:
            alias['RoutingConfig'] = {'AdditionalVersionWeights': dict(weights)}

    def delete_alias(self, FunctionName, Name):
        if self.aliases.pop(Name, None) is None:
            raise client_error('ResourceNotFoundException')
        self.url_configs.pop(Name, None)

    # Invoke
    def _route(self, qualifier):
        if not qualifier:
            return '$LATEST', self.function
        alias = self.aliases[qualifier]
        version = alias['FunctionVersion']
        roll = self.rng.random()
        for extra, weight in ((alias.get('RoutingConfig') or {}).get('AdditionalVersionWeights') or {}).items():
            if self.honor_weights and roll < weight:
                version = extra
                break
            roll -= weight
        return version, self.versions[version]

    def _run(self, config, version, event):
        namespace = {}
        env = {**config['Environment']['Variables'], 'AWS_LAMBDA_FUNCTION_VERSION': version}
        with patch.dict(os.environ, env):
            exec(compile(config['source'], 'quote.py', 'exec'), namespace)
            with patch.object(namespace['time'], 'sleep'):
                return namespace['handler'](event, types.SimpleNamespace(memory_limit_in_mb=str(config['MemorySize'])))

    def invoke(self, FunctionName, Payload, Qualifier=None):
        self._require()
        event = json.loads(Payload)
        if event.get('hold_seconds'):
            self.held += 1
            if self.enforce_concurrency and self.reserved is not None and self.held > self.reserved:
                raise client_error('TooManyRequestsException', 'Rate Exceeded.')
        else:
            self.held = 0
        if self.reserved == 0:
            raise client_error('TooManyRequestsException', 'Rate Exceeded.')
        version, config = self._route(Qualifier)
        body = self._run(config, version, event)
        return {'StatusCode': 200, 'ExecutedVersion': version, 'Payload': io.BytesIO(json.dumps(body).encode())}

    # Concurrency
    def put_function_concurrency(self, FunctionName, ReservedConcurrentExecutions):
        self._require()
        self.reserved = ReservedConcurrentExecutions

    def get_function_concurrency(self, FunctionName):
        self._require()
        return {} if self.reserved is None else {'ReservedConcurrentExecutions': self.reserved}

    def delete_function_concurrency(self, FunctionName):
        self._require()
        self.reserved = None

    def get_account_settings(self):
        return {'AccountLimit': {'ConcurrentExecutions': 1000,
                                 'UnreservedConcurrentExecutions': 1000 - (self.reserved or 0)}}

    # Function URLs
    def create_function_url_config(self, FunctionName, Qualifier, AuthType, Cors):
        self.url_configs[Qualifier] = {'FunctionUrl': f'http://url{next(self.ids)}.lambda-url.us-east-1.floci:4566/',
                                       'AuthType': AuthType, 'Cors': copy.deepcopy(Cors)}

    def update_function_url_config(self, FunctionName, Qualifier, AuthType, Cors):
        self.url_configs[Qualifier].update({'AuthType': AuthType, 'Cors': copy.deepcopy(Cors)})

    def get_function_url_config(self, FunctionName, Qualifier):
        if Qualifier not in self.url_configs:
            raise client_error('ResourceNotFoundException')
        return copy.deepcopy(self.url_configs[Qualifier])

    def delete_function_url_config(self, FunctionName, Qualifier):
        if self.url_configs.pop(Qualifier, None) is None:
            raise client_error('ResourceNotFoundException')

    def http(self, url, headers):
        self.requests.append(headers)
        qualifier = next(name for name, config in self.url_configs.items()
                         if urlparse(config['FunctionUrl']).netloc == headers['Host'])
        parsed = urlparse(url)
        event = {'version': '2.0', 'rawPath': parsed.path, 'requestContext': {'http': {'method': 'GET'}},
                 'queryStringParameters': dict(parse_qsl(parsed.query))}
        version, config = self._route(qualifier)
        response = self._run(config, version, event)
        return response['statusCode'], response['body']


class LambdaPerformanceCostLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        for target, replacement in (('_client', lambda name: self.fake), ('_http', lambda url, headers: self.fake.http(url, headers))):
            patcher = patch(f'dashboard.labs.lambda_performance_labs.{target}', side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.lambda_performance_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def test_lab_is_registered_with_eight_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 8)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '3.2'), tasks)
        self.assertIn(('SAA-C03', '4.2'), tasks)
        self.assertIn(('CLF-C02', '3.3'), tasks)

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_cost_per_million(self):
        self.assertEqual(lab._cost_per_million(128, 100), 0.41)
        self.assertEqual(lab._cost_per_million(1024, 100), 1.87)

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 8)

    def test_messages_report_what_happened_and_what_floci_skips(self):
        results = self.run_all()
        self.assertEqual(results['memory-sizing']['json']['Chosen']['MemoryMB'], 128)
        self.assertIn('does not scale CPU', results['memory-sizing']['verification']['message'])
        self.assertEqual(self.fake.versions['1']['MemorySize'], 128)
        counts = results['canary-release']['json']['ExecutedVersions']
        self.assertEqual(sum(counts.values()), lab.CANARY_INVOCATIONS)
        totals = results['canary-release']['json']['TotalsByVersion']
        self.assertAlmostEqual(totals['2'][0] / totals['1'][0], 0.9, places=2)
        self.assertEqual(results['reserved-concurrency']['json']['Throttled'], 3)
        self.assertIn('unsigned request', results['function-url']['verification']['message'])
        self.assertIn('Authorization', self.fake.requests[0])
        self.assertNotIn('Authorization', self.fake.requests[1])
        self.assertIn('old weight entry', results['promote-v2']['verification']['message'])

    def test_promotion_on_aws_clears_the_weights(self):
        self.fake.clear_weights = True
        results = self.run_all()
        self.assertTrue(results['promote-v2']['verified'])
        self.assertNotIn('old weight entry', results['promote-v2']['verification']['message'])

    def test_slower_large_sizes_pick_the_smallest(self):
        for step in lab.STEP_KEYS[:1]:
            run_lab_step(SERVICE, LAB, step)
        timings = {128: 300.0, 512: 80.0, 1024: 50.0, 1769: 30.0}
        original = self.fake._run

        def timed(config, version, event):
            body = original(config, version, event)
            if event.get('rounds') == lab.ROUNDS:
                body['handler_ms'] = timings[config['MemorySize']]
            return body
        with patch.object(self.fake, '_run', side_effect=timed):
            result = run_lab_step(SERVICE, LAB, 'memory-sizing')
        self.assertTrue(result['verified'])
        # 128 MB misses the 250 ms target; 512 MB at 80 ms costs less than 1024 MB at 50 ms.
        self.assertEqual(result['json']['Chosen']['MemoryMB'], 512)
        self.assertEqual(self.fake.function['MemorySize'], 512)
        self.assertNotIn('does not scale CPU', result['verification']['message'])

    def test_unweighted_alias_fails_the_canary(self):
        self.fake.honor_weights = False
        for step in lab.STEP_KEYS[:4]:
            run_lab_step(SERVICE, LAB, step)
        result = run_lab_step(SERVICE, LAB, 'canary-release')
        self.assertFalse(result['verified'])
        self.assertIn('got 0%', result['verification']['message'])

    def test_unenforced_concurrency_fails(self):
        self.fake.enforce_concurrency = False
        for step in lab.STEP_KEYS[:5]:
            run_lab_step(SERVICE, LAB, step)
        result = run_lab_step(SERVICE, LAB, 'reserved-concurrency')
        self.assertFalse(result['verified'])
        self.assertIn('8 ran and 0 were throttled', result['verification']['message'])

    def test_public_url_and_wrong_memory_fail(self):
        self.run_all()
        self.fake.url_configs[lab.ALIAS]['AuthType'] = 'NONE'
        self.assertIn('AWS_IAM', lab._check_url()[1])
        self.fake.function['MemorySize'] = 3008
        self.assertIn('the sweep picked', lab._check_sizing()[1])
        self.assertEqual(self.failed(), ['memory-sizing', 'function-url'])

    def test_rerunning_alias_step_after_promotion_starts_the_alias_over(self):
        self.run_all()
        result = run_lab_step(SERVICE, LAB, 'alias-live')
        self.assertTrue(result['verified'])
        self.assertEqual(self.fake.aliases[lab.ALIAS], {'Name': lab.ALIAS, 'FunctionVersion': '1'})
        self.assertEqual(self.failed(), ['function-url', 'promote-v2'])
        results = self.run_all()
        self.assertTrue(all(item['verified'] for item in results.values()))
        self.assertEqual(len(self.fake.versions), 2)

    def test_steps_out_of_order_explain_what_is_missing(self):
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'memory-sizing')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'publish-version')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'canary-release')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'function-url')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'promote-v2')['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertIsNone(self.fake.function)
        for store in (self.fake.roles, self.fake.role_policies, self.fake.aliases, self.fake.url_configs,
                      self.fake.log_groups):
            self.assertFalse(store)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        self.fake.log_groups.clear()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
