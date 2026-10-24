"""Lambda event lifecycle lab: queues, the order function, sync test events, a DLQ, destinations, async success and
failure, and a KMS-encrypted environment variable. The fake runs the real order handler (with a fake boto3 for its KMS
call) and handles async events the way Floci 2.3.0 does: retries, AWS-shaped destination records, a DLQ that only gets
failed events while no OnFailure destination is set, and kms:Decrypt checked against key/*."""

import base64
import copy
import importlib
import json
import sys
import types
import uuid
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.lambda_lifecycle_labs')

SERVICE = 'lambda'
LAB = 'async-error-handling'
ACCOUNT = '000000000000'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}},
                       'Operation')


class Payload:
    def __init__(self, body):
        self.body = body

    def read(self):
        return b'' if self.body is None else json.dumps(self.body).encode()


class FakeAws:
    def __init__(self, retries=True, dlq=True, dlq_with_destination=False, decrypt=True):
        self.retries, self.dlq, self.dlq_with_destination, self.decrypt_allowed = (
            retries, dlq, dlq_with_destination, decrypt)
        self.queues: dict[str, list] = {}
        self.function = None
        self.invoke_config = None
        self.roles: dict[str, dict] = {}
        self.role_policies: dict[tuple[str, str], str] = {}
        self.keys: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}
        self.log_groups = {lab.LOG_GROUP}
        self.calls: list[int] = []

    # SQS
    def _url(self, name):
        return f'http://localhost:4566/{ACCOUNT}/{name}'

    def _name(self, url):
        name = url.rsplit('/', 1)[-1]
        if name not in self.queues:
            raise client_error('AWS.SimpleQueueService.NonExistentQueue')
        return name

    def create_queue(self, QueueName):
        self.queues.setdefault(QueueName, [])
        return {'QueueUrl': self._url(QueueName)}

    def get_queue_url(self, QueueName):
        if QueueName not in self.queues:
            raise client_error('AWS.SimpleQueueService.NonExistentQueue')
        return {'QueueUrl': self._url(QueueName)}

    def delete_queue(self, QueueUrl):
        del self.queues[self._name(QueueUrl)]

    def _send(self, arn, body, attributes=None):
        name = arn.rsplit(':', 1)[-1]
        if name in self.queues:
            self.queues[name].append({'Body': json.dumps(body), 'ReceiptHandle': uuid.uuid4().hex,
                                      'MessageAttributes': attributes or {}})

    def receive_message(self, QueueUrl, MaxNumberOfMessages=1, WaitTimeSeconds=0, MessageAttributeNames=None):
        messages = self.queues[self._name(QueueUrl)][:MaxNumberOfMessages]
        result = []
        for message in messages:
            item = dict(message)
            if not MessageAttributeNames:
                item.pop('MessageAttributes')
            result.append(item)
        return {'Messages': result} if result else {}

    def delete_message(self, QueueUrl, ReceiptHandle):
        name = self._name(QueueUrl)
        self.queues[name] = [item for item in self.queues[name] if item['ReceiptHandle'] != ReceiptHandle]

    # IAM
    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': self.roles[RoleName]}

    def create_role(self, RoleName, **kwargs):
        self.roles[RoleName] = {'RoleName': RoleName, 'Arn': f'arn:aws:iam::{ACCOUNT}:role/{RoleName}'}

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.role_policies[(RoleName, PolicyName)] = PolicyDocument

    def get_role_policy(self, RoleName, PolicyName):
        if (RoleName, PolicyName) not in self.role_policies:
            raise client_error('NoSuchEntity')
        return {'PolicyDocument': json.loads(self.role_policies[(RoleName, PolicyName)])}

    def delete_role_policy(self, RoleName, PolicyName):
        if (RoleName, PolicyName) not in self.role_policies:
            raise client_error('NoSuchEntity')
        del self.role_policies[(RoleName, PolicyName)]

    def delete_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        del self.roles[RoleName]

    # KMS
    def _key(self, key_id):
        key_id = self.aliases.get(key_id, key_id)
        for arn, key in self.keys.items():
            if key_id in (arn, key['KeyId']) and not key.get('pending'):
                return key
        raise client_error('NotFoundException')

    def create_key(self, Description=''):
        key_id = uuid.uuid4().hex
        arn = f'arn:aws:kms:us-east-1:{ACCOUNT}:key/{key_id}'
        self.keys[arn] = {'KeyId': key_id, 'Arn': arn}
        return {'KeyMetadata': self.keys[arn]}

    def create_alias(self, AliasName, TargetKeyId):
        self.aliases[AliasName] = TargetKeyId

    def describe_key(self, KeyId):
        return {'KeyMetadata': self._key(KeyId)}

    def encrypt(self, KeyId, Plaintext, EncryptionContext):
        key = self._key(KeyId)
        blob = json.dumps({'key': key['Arn'], 'context': EncryptionContext,
                           'plain': base64.b64encode(Plaintext).decode()}).encode()
        return {'CiphertextBlob': blob}

    def delete_alias(self, AliasName):
        if AliasName not in self.aliases:
            raise client_error('NotFoundException')
        del self.aliases[AliasName]

    def schedule_key_deletion(self, KeyId, PendingWindowInDays):
        self.keys[KeyId]['pending'] = True

    def _role_decrypt(self, CiphertextBlob, EncryptionContext):
        """kms.decrypt as the function's role: Floci checks the policy against key/*, not the key ARN."""
        document = json.loads(self.role_policies.get((lab.ROLE_NAME, lab.ROLE_POLICY), '{}') or '{}')
        allowed = self.decrypt_allowed and any(
            statement.get('Action') == 'kms:Decrypt' and statement.get('Resource') in (lab.KEY_RESOURCE, '*')
            for statement in document.get('Statement', []))
        if not allowed:
            raise client_error('AccessDeniedException', 'User is not authorized to perform: kms:Decrypt')
        blob = json.loads(CiphertextBlob)
        if blob['context'] != EncryptionContext:
            raise client_error('InvalidCiphertextException')
        return {'Plaintext': base64.b64decode(blob['plain'])}

    # Logs
    def delete_log_group(self, logGroupName):
        if logGroupName not in self.log_groups:
            raise client_error('ResourceNotFoundException')
        self.log_groups.discard(logGroupName)

    # Lambda
    def get_function_configuration(self, FunctionName):
        if not self.function:
            raise client_error('ResourceNotFoundException')
        return copy.deepcopy(self.function)

    def create_function(self, FunctionName, Code, **settings):
        self.function = {'FunctionName': FunctionName, 'FunctionArn': lab.FUNCTION_ARN, 'State': 'Active',
                         'LastUpdateStatus': 'Successful', **{key: value for key, value in settings.items()
                                                              if key != 'Description'}}
        self.code = Code['ZipFile']

    def update_function_code(self, FunctionName, ZipFile):
        self.code = ZipFile

    def update_function_configuration(self, FunctionName, **settings):
        self.function.update(settings)

    def delete_function(self, FunctionName):
        if not self.function:
            raise client_error('ResourceNotFoundException')
        self.function = None
        self.invoke_config = None

    def get_function_event_invoke_config(self, FunctionName):
        if not self.invoke_config:
            raise client_error('ResourceNotFoundException')
        return copy.deepcopy(self.invoke_config)

    def put_function_event_invoke_config(self, FunctionName, **settings):
        self.invoke_config = {'FunctionArn': lab.FUNCTION_ARN, **settings}
        return copy.deepcopy(self.invoke_config)

    def update_function_event_invoke_config(self, FunctionName, **settings):
        self.invoke_config = {**(self.invoke_config or {'FunctionArn': lab.FUNCTION_ARN}), **settings}
        return copy.deepcopy(self.invoke_config)

    def delete_function_event_invoke_config(self, FunctionName):
        if not self.invoke_config:
            raise client_error('ResourceNotFoundException')
        self.invoke_config = None

    def _run(self, event):
        """Run the real handler with a fake boto3 whose KMS client decrypts as the function's role."""
        fake_boto3 = types.ModuleType('boto3')
        fake_boto3.client = lambda name: types.SimpleNamespace(decrypt=self._role_decrypt)
        namespace: dict = {}
        environment = {**((self.function.get('Environment') or {}).get('Variables') or {}),
                       'AWS_LAMBDA_FUNCTION_NAME': lab.FUNCTION}
        with patch.dict(sys.modules, {'boto3': fake_boto3}), patch.dict('os.environ', environment), \
                patch('builtins.print'):
            exec(compile(lab.ORDERS_SOURCE, 'orders.py', 'exec'), namespace)
            try:
                return namespace['handler'](event, types.SimpleNamespace(aws_request_id=uuid.uuid4().hex)), None
            except Exception as exc:  # noqa: BLE001 - the handler's error becomes the function error
                return {'errorMessage': str(exc), 'errorType': type(exc).__name__}, exc

    def invoke(self, FunctionName, Payload, InvocationType='RequestResponse', LogType=None):
        if not self.function:
            raise client_error('ResourceNotFoundException')
        event = json.loads(Payload)
        if InvocationType != 'Event':
            body, error = self._run(event)
            response = {'StatusCode': 200, 'Payload': globals()['Payload'](body)}
            if error:
                response['FunctionError'] = 'Unhandled'
            return response
        config = self.invoke_config or {}
        attempts = 1 + (config.get('MaximumRetryAttempts', 2) if self.retries else 0)
        count = 0
        for count in range(1, attempts + 1):
            body, error = self._run(event)
            if not error:
                break
        self.calls.append(count)
        destinations = config.get('DestinationConfig') or {}
        condition = 'RetriesExhausted' if error else 'Success'
        record = {'version': '1.0', 'requestContext': {'functionArn': lab.FUNCTION_ARN, 'condition': condition,
                                                       'approximateInvokeCount': count},
                  'requestPayload': event, 'responsePayload': body}
        if not error and destinations.get('OnSuccess'):
            self._send(destinations['OnSuccess']['Destination'], record)
        if error:
            if destinations.get('OnFailure'):
                self._send(destinations['OnFailure']['Destination'], record)
            dlq = (self.function.get('DeadLetterConfig') or {}).get('TargetArn')
            if dlq and self.dlq and (self.dlq_with_destination or not destinations.get('OnFailure')):
                self._send(dlq, event, {'ErrorMessage': {'StringValue': body['errorMessage'], 'DataType': 'String'}})
        return {'StatusCode': 202, 'Payload': globals()['Payload'](None)}


class LambdaAsyncErrorHandlingLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.use(FakeAws())
        sleeper = patch('dashboard.labs.lambda_lifecycle_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def use(self, fake):
        self.fake = fake
        patcher = patch('dashboard.labs.lambda_lifecycle_labs._client', side_effect=lambda name: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Waits end at once: the fake delivers async results synchronously.
        drain = lab._drain
        waiter = patch('dashboard.labs.lambda_lifecycle_labs._drain',
                       side_effect=lambda url, matches, timeout, attributes=False: drain(url, matches, 0.5, attributes))
        waiter.start()
        self.addCleanup(waiter.stop)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failures(self, results):
        return {key: result['verification']['message'] for key, result in results.items() if not result['verified']}

    def test_lab_is_registered_with_eight_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 8)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('DVA-C02', '1.2'), tasks)
        self.assertIn(('DVA-C02', '2.3'), tasks)
        self.assertIn(('SAA-C03', '2.1'), tasks)

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_every_step_verifies_twice_and_status_is_complete(self):
        self.assertEqual(self.failures(self.run_all()), {})
        self.assertEqual(self.failures(self.run_all()), {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 8)

    def test_messages_report_what_happened(self):
        results = self.run_all()
        self.assertIn('HTTP 200', results['test-event']['verification']['message'])
        self.assertIn('amount must be positive', results['dead-letter-queue']['verification']['message'])
        self.assertIn('ran 2 times', results['async-failure']['verification']['message'])
        self.assertEqual(self.fake.calls[-1], 2)
        self.assertEqual(results['async-success']['json']['InvokeStatusCode'], 202)

    def test_role_can_send_to_every_queue_and_decrypt_only_after_the_kms_step(self):
        self.run_all()
        document = json.loads(self.fake.role_policies[(lab.ROLE_NAME, lab.ROLE_POLICY)])
        sids = {statement['Sid'] for statement in document['Statement']}
        self.assertEqual(sids, {'Logs', 'DestinationsAndDlq', 'DecryptToken'})
        self.assertEqual(lab.role_policy(False)['Statement'][-1]['Sid'], 'DestinationsAndDlq')

    def test_environment_holds_ciphertext_not_the_token(self):
        result = self.run_all()['encrypted-env']
        stored = self.fake.function['Environment']['Variables'][lab.TOKEN_VARIABLE]
        self.assertFalse(stored.startswith(lab.TOKEN_PREFIX))
        self.assertNotIn(lab.TOKEN_PREFIX, json.dumps(result['json']['Environment']))
        self.assertEqual(result['json']['TokenCheck']['token_prefix'], lab.TOKEN_PREFIX)

    def test_plaintext_environment_variable_fails(self):
        self.run_all()
        self.fake.function['Environment'] = {'Variables': {lab.TOKEN_VARIABLE: lab.TOKEN_PREFIX + 'abc'}}
        self.assertIn('encrypted-env', [k for k, v in lab_status(SERVICE, LAB)['steps'].items() if not v['verified']])

    def test_denied_decrypt_fails_with_the_error(self):
        self.use(FakeAws(decrypt=False))
        result = self.run_all()['encrypted-env']
        self.assertFalse(result['verified'])
        self.assertIn('could not decrypt', result['verification']['message'])

    def test_missing_dlq_delivery_fails(self):
        self.use(FakeAws(dlq=False))
        failures = self.failures(self.run_all())
        self.assertEqual(list(failures), ['dead-letter-queue'])
        self.assertIn('did not reach', failures['dead-letter-queue'])

    def test_no_retry_fails_the_failure_step(self):
        self.use(FakeAws(retries=False))
        failures = self.failures(self.run_all())
        self.assertEqual(list(failures), ['async-failure'])
        self.assertIn('2 attempts', failures['async-failure'])

    def test_aws_style_dlq_copy_alongside_the_destination_still_passes(self):
        self.use(FakeAws(dlq_with_destination=True))
        self.assertEqual(self.failures(self.run_all()), {})
        self.assertEqual(self.failures(self.run_all()), {})

    def test_steps_out_of_order_explain_what_is_missing(self):
        for step in ('create-function', 'test-event', 'dead-letter-queue', 'destinations', 'async-success',
                     'async-failure', 'encrypted-env'):
            result = run_lab_step(SERVICE, LAB, step)
            self.assertFalse(result['verified'], step)
            self.assertIn('first', result['verification']['message'], step)

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertIsNone(self.fake.function)
        for store in (self.fake.queues, self.fake.roles, self.fake.role_policies, self.fake.aliases,
                      self.fake.log_groups):
            self.assertFalse(store)
        self.assertTrue(all(key.get('pending') for key in self.fake.keys.values()))
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        self.fake.log_groups.clear()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
