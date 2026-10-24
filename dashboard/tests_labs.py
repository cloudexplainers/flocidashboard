import json
import os
import zipfile
import base64
import importlib
import inspect
import re
from contextlib import ExitStack
from io import BytesIO
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from .labs import labs_for_service, next_lab_batch, reset_lab, run_lab_step
from .labs.registry import LAB_BATCH_ORDER, all_labs
from .labs.runner import run_lab_step as facade_run_lab_step
from .labs.types import Lab, LabStep, ResetResult, StepResult, VerificationResult


def access_denied(action: str = 'iam:GetUser') -> ClientError:
    return ClientError(
        {
            'Error': {
                'Code': 'AccessDenied',
                'Message': f'User is not authorized to perform: {action}',
            },
        },
        action.rsplit(':', 1)[-1],
    )


class LabsRegistryAuditTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.monolith = importlib.import_module('dashboard.labs.monolith')
        cls.run_source = inspect.getsource(cls.monolith.run_lab_step)
        cls.status_source = inspect.getsource(cls.monolith.lab_status)
        cls.reset_source = inspect.getsource(cls.monolith.reset_lab)

    def _branch_source(self, source: str, service_key: str, lab_key: str) -> str:
        marker = f"service_key == '{service_key}' and lab_key == '{lab_key}'"
        start = source.find(marker)
        if start == -1 and service_key == 'ec2' and lab_key.startswith('guided-'):
            marker = "service_key == 'ec2' and lab_key.startswith('guided-')"
            start = source.find(marker)
        if start == -1:
            marker = f"service_key == '{service_key}'"
            start = source.find(marker)
        self.assertNotEqual(
            start,
            -1,
            f'Missing implementation branch for {service_key}/{lab_key}',
        )
        next_branch = source.find('\n    if service_key == ', start + 1)
        if next_branch == -1:
            next_branch = source.find('\n    raise ValueError', start + 1)
        if next_branch == -1:
            next_branch = source.find('\n    return {', start + 1)
        return source[start:next_branch if next_branch != -1 else len(source)]

    def _expanded_branch_source(self, source: str, service_key: str, lab_key: str) -> str:
        branch = self._branch_source(source, service_key, lab_key)
        delegated = re.search(r'from \.([a-z0-9_]+) import (\w+)', branch)
        if not delegated:
            return branch
        module = importlib.import_module(f'dashboard.labs.{delegated.group(1)}')
        # Service modules keep per-lab branches in their run_step/status/reset,
        # so follow the delegation into the matching branch there.
        target_source = inspect.getsource(getattr(module, delegated.group(2), module))
        if f"service_key == '{service_key}'" in target_source:
            return f'{branch}\n{self._expanded_branch_source(target_source, service_key, lab_key)}'
        return f'{branch}\n{inspect.getsource(module)}'

    def test_final_batch_completion_counts_every_batch(self):
        last_service = LAB_BATCH_ORDER[-1]['service']
        last_lab = labs_for_service(last_service)[-1]['key']

        result = next_lab_batch(last_service, last_lab)

        self.assertEqual(result['title'], 'All learning batches complete')
        self.assertIn(f'all {len(LAB_BATCH_ORDER)} AWS service lab batches', result['summary'])

    def test_every_batch_service_has_labs(self):
        for batch in LAB_BATCH_ORDER:
            with self.subTest(service=batch['service']):
                self.assertTrue(labs_for_service(batch['service']))

    def test_every_registered_lab_has_runner_status_and_reset_branches(self):
        for lab in all_labs():
            with self.subTest(service=lab['service'], lab=lab['key']):
                self._branch_source(self.run_source, lab['service'], lab['key'])
                self._branch_source(self.status_source, lab['service'], lab['key'])
                self._branch_source(self.reset_source, lab['service'], lab['key'])

    def test_every_registered_step_has_runner_dispatch(self):
        for lab in all_labs():
            branch = self._expanded_branch_source(self.run_source, lab['service'], lab['key'])
            for step in lab['steps']:
                with self.subTest(service=lab['service'], lab=lab['key'], step=step['key']):
                    self.assertRegex(
                        branch,
                        rf"(step_key == ['\"]{re.escape(step['key'])}['\"]|['\"]{re.escape(step['key'])}['\"]\s*:)",
                        f'Missing runner dispatch for {lab["service"]}/{lab["key"]}/{step["key"]}',
                    )

    def test_every_registered_step_has_status_entry(self):
        for lab in all_labs():
            branch = self._expanded_branch_source(self.status_source, lab['service'], lab['key'])
            for step in lab['steps']:
                with self.subTest(service=lab['service'], lab=lab['key'], step=step['key']):
                    self.assertRegex(
                        branch,
                        rf"['\"]{re.escape(step['key'])}['\"]\s*:",
                        f'Missing status entry for {lab["service"]}/{lab["key"]}/{step["key"]}',
                    )


class LabsPageTests(SimpleTestCase):
    def test_labs_package_facades_preserve_public_api(self):
        self.assertIs(facade_run_lab_step, run_lab_step)
        self.assertEqual(len(all_labs()), 78)
        self.assertTrue(labs_for_service('iam'))
        self.assertTrue(issubclass(Lab, dict))
        self.assertTrue(issubclass(LabStep, dict))
        self.assertTrue(issubclass(StepResult, dict))
        self.assertTrue(issubclass(ResetResult, dict))
        self.assertTrue(issubclass(VerificationResult, dict))


    def test_labs_catalog_endpoint_returns_instant_metadata(self):
        response = self.client.get(reverse('dashboard:labs-catalog'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['total_labs'], 78)
        self.assertIn('services', data)
        self.assertIn('labs', data)
        self.assertGreaterEqual(len(data['services']), 20)

    @patch('dashboard.views.lab_status')
    def test_api_lab_status_returns_live_verification_state(self, status_mock):
        status_mock.return_value = {
            'complete': True,
            'steps': {'create-bucket': {'verified': True, 'verification': {'message': 'Bucket exists'}}},
        }
        response = self.client.get(reverse('dashboard:lab-status', kwargs={'service_key': 's3', 'lab_key': 'create-bucket'}))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['complete'])
        self.assertTrue(data['steps']['create-bucket']['verified'])


    @patch('dashboard.views.reset_lab')
    @patch('dashboard.views.lab_status')
    @patch('dashboard.views.all_labs')
    def test_reset_completed_honors_all_flag_in_json_body(self, all_labs_mock, status_mock, reset_mock):
        all_labs_mock.return_value = [
            {'service': 's3', 'key': 'one', 'title': 'One', 'steps': []},
            {'service': 's3', 'key': 'two', 'title': 'Two', 'steps': []},
        ]
        reset_mock.return_value = {'reset': True}

        response = self.client.post(
            reverse('dashboard:labs-global-reset'),
            data=json.dumps({'all': True}),
            content_type='application/json',
        )

        payload = response.json()
        self.assertTrue(payload['is_reset_all'])
        self.assertEqual(payload['target_lab_count'], 2)
        status_mock.assert_not_called()

    def test_wait_for_verification_retries_until_logs_arrive(self):
        from dashboard import labs as monolith

        results = iter([
            {'status': 'failed', 'message': 'not yet'},
            {'status': 'failed', 'message': 'not yet'},
            {'status': 'passed', 'message': 'found'},
        ])
        with patch.object(monolith.time, 'sleep') as sleep_mock:
            verification = monolith._wait_for_verification(lambda: next(results), timeout_seconds=60)
        self.assertEqual(verification['status'], 'passed')
        self.assertEqual(sleep_mock.call_count, 2)

        with patch.object(monolith.time, 'sleep'):
            failed = monolith._wait_for_verification(lambda: {'status': 'failed', 'message': 'never'}, timeout_seconds=0)
        self.assertEqual(failed['message'], 'never')

    @patch('dashboard.views.all_labs')
    @patch('dashboard.views.lab_status')
    def test_labs_progress_reports_completed_labs_and_steps(self, status_mock, all_labs_mock):
        cache.clear()
        all_labs_mock.return_value = [
            {
                'service': 'iam',
                'key': 'create-user-alice',
                'title': 'Create an IAM user',
                'steps': [
                    {'key': 'create-user'},
                    {'key': 'attach-policy'},
                ],
            },
            {
                'service': 's3',
                'key': 'create-bucket',
                'title': 'Create an S3 bucket',
                'steps': [
                    {'key': 'create-bucket'},
                ],
            },
        ]
        statuses = {
            'create-user-alice': {
                'complete': True,
                'steps': {
                    'create-user': {'verified': True},
                    'attach-policy': {'verified': True},
                },
            },
            'create-bucket': {'complete': False, 'steps': {}},
        }
        # Labs are checked in parallel, so answer by lab rather than call order.
        status_mock.side_effect = lambda service, lab_key: statuses[lab_key]

        response = self.client.get(reverse('dashboard:labs-progress'))
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['completed_lab_count'], 1)
        self.assertEqual(payload['total_lab_count'], 2)
        self.assertEqual(payload['completed_step_count'], 2)
        self.assertEqual(payload['total_step_count'], 3)
        self.assertFalse(payload['cached'])

    @patch('dashboard.views.all_labs')
    @patch('dashboard.views.lab_status')
    def test_labs_progress_keeps_lab_order_and_request_endpoint_in_workers(self, status_mock, all_labs_mock):
        import threading
        import time

        from .aws import reset_runtime_endpoint_override, runtime_endpoint_override, set_runtime_endpoint_override
        from .views import _labs_progress_snapshot

        all_labs_mock.return_value = [
            {'service': 's3', 'key': f'lab-{index}', 'title': f'Lab {index}', 'steps': [{'key': 'one'}]}
            for index in range(12)
        ]
        seen = []

        def status(service, lab_key):
            # Finish early labs last so out-of-order completion would show up.
            time.sleep(0.01 * (12 - int(lab_key.split('-')[1])) / 12)
            seen.append((threading.get_ident(), runtime_endpoint_override()))
            return {'complete': lab_key == 'lab-3', 'steps': {'one': {'verified': lab_key == 'lab-3'}}}

        status_mock.side_effect = status
        token = set_runtime_endpoint_override('http://alternate.localhost.floci.io:4566')
        try:
            snapshot = _labs_progress_snapshot()
        finally:
            reset_runtime_endpoint_override(token)

        self.assertEqual([lab['key'] for lab in snapshot['labs']], [f'lab-{index}' for index in range(12)])
        self.assertEqual(snapshot['completed_lab_count'], 1)
        self.assertTrue(snapshot['labs'][3]['complete'])
        self.assertEqual({endpoint for _, endpoint in seen}, {'http://alternate.localhost.floci.io:4566'})
        self.assertGreater(len({thread for thread, _ in seen}), 1)

    @patch('dashboard.views.FlociClientFactory')
    @patch('dashboard.views.all_labs')
    @patch('dashboard.views.lab_status')
    def test_labs_progress_cache_is_scoped_to_endpoint_context(self, status_mock, all_labs_mock, factory_mock):
        cache.clear()
        all_labs_mock.return_value = [
            {
                'service': 'iam',
                'key': 'create-user-alice',
                'title': 'Create an IAM user',
                'steps': [{'key': 'create-user'}],
            },
        ]
        status_mock.side_effect = [
            {'complete': True, 'steps': {'create-user': {'verified': True}}},
            {'complete': False, 'steps': {}},
        ]

        endpoint_one = MagicMock(
            endpoint_url='http://localhost:4566',
            region='us-east-1',
            credential_source='local_default',
            profile=None,
            access_key_id='test',
        )
        endpoint_two = MagicMock(
            endpoint_url='http://alternate.localhost.floci.io:4566',
            region='us-east-1',
            credential_source='local_default',
            profile=None,
            access_key_id='test',
        )
        factory_mock.side_effect = [endpoint_one, endpoint_one, endpoint_two]

        first = self.client.get(reverse('dashboard:labs-progress')).json()
        second = self.client.get(reverse('dashboard:labs-progress')).json()
        third = self.client.get(reverse('dashboard:labs-progress')).json()

        self.assertFalse(first['cached'])
        self.assertTrue(second['cached'])
        self.assertFalse(third['cached'])
        self.assertEqual(first['completed_lab_count'], 1)
        self.assertEqual(second['completed_lab_count'], 1)
        self.assertEqual(third['completed_lab_count'], 0)
        self.assertEqual(status_mock.call_count, 2)


class LabsRunnerTests(SimpleTestCase):
    def test_iam_lab_registry_starts_with_admin_then_alice(self):
        labs = labs_for_service('iam')

        self.assertEqual(labs[0]['key'], 'create-admin-user')
        self.assertEqual(labs[0]['steps'][0]['command'], 'aws iam create-user --user-name floci-admin')
        self.assertEqual(labs[0]['steps'][1]['command'], 'aws iam put-user-policy --user-name floci-admin --policy-name AdministratorAccess --policy-document file://administrator-access.json')
        self.assertEqual(labs[1]['key'], 'create-user-alice')
        self.assertEqual(labs[1]['steps'][0]['command'], 'aws iam create-user --user-name Alice')
        self.assertEqual(labs[2]['key'], 'attach-policy-alice')
        self.assertEqual(len(labs[2]['steps']), 4)
        self.assertEqual(labs[3]['key'], 'access-key-alice')
        self.assertEqual(len(labs[3]['steps']), 3)
        self.assertEqual(labs[4]['key'], 'group-membership-alice')
        self.assertEqual(len(labs[4]['steps']), 4)
        self.assertEqual(labs[5]['key'], 'group-policy-floci-developers')
        self.assertEqual(len(labs[5]['steps']), 6)
        self.assertEqual(labs[6]['key'], 'inline-policy-alice')
        self.assertEqual(len(labs[6]['steps']), 4)
        self.assertEqual(labs[7]['key'], 'role-trust-policy')
        self.assertEqual(len(labs[7]['steps']), 4)
        self.assertEqual(labs[8]['key'], 'sts-session-policy')
        self.assertEqual(len(labs[8]['steps']), 3)
        self.assertEqual(labs[9]['key'], 'ec2-instance-profile')
        self.assertEqual(len(labs[9]['steps']), 5)
        self.assertEqual(labs[10]['key'], 'identity-enforcement-capstone')
        self.assertEqual(len(labs[10]['steps']), 7)

    def test_s3_lab_registry_includes_create_bucket_workflow(self):
        labs = labs_for_service('s3')

        self.assertEqual(len(labs), 13)
        self.assertEqual(labs[0]['key'], 'create-bucket')
        self.assertEqual(
            [step['key'] for step in labs[0]['steps']],
            ['create-bucket', 'head-bucket', 'list-buckets'],
        )
        self.assertEqual(labs[1]['key'], 'object-workflow')
        self.assertEqual(
            [step['key'] for step in labs[1]['steps']],
            ['create-bucket', 'put-object', 'list-objects', 'head-object', 'get-object'],
        )
        self.assertEqual(labs[2]['key'], 'prefix-copy')
        self.assertEqual(
            [step['key'] for step in labs[2]['steps']],
            ['create-bucket', 'put-source-object', 'list-incoming-prefix', 'copy-object', 'list-archive-prefix'],
        )
        self.assertEqual(labs[3]['key'], 'metadata-tags')
        self.assertEqual(
            [step['key'] for step in labs[3]['steps']],
            ['create-bucket', 'put-object', 'head-object', 'put-object-tagging', 'get-object-tagging'],
        )
        self.assertEqual(labs[4]['key'], 'version-recovery')
        self.assertEqual(
            [step['key'] for step in labs[4]['steps']],
            ['create-bucket', 'enable-versioning', 'put-version-one', 'put-version-two', 'list-object-versions', 'get-version-one'],
        )
        self.assertEqual(labs[5]['key'], 'presigned-url')
        self.assertEqual(
            [step['key'] for step in labs[5]['steps']],
            ['create-bucket', 'put-object', 'presign-object'],
        )
        self.assertEqual(labs[6]['key'], 'bucket-security')
        self.assertEqual(
            [step['key'] for step in labs[6]['steps']],
            ['create-bucket', 'put-public-access-block', 'get-public-access-block', 'put-bucket-policy', 'get-bucket-policy'],
        )
        self.assertEqual(labs[7]['key'], 'default-encryption')
        self.assertEqual(
            [step['key'] for step in labs[7]['steps']],
            ['create-bucket', 'put-bucket-encryption', 'get-bucket-encryption', 'put-object', 'head-object'],
        )
        self.assertEqual(labs[8]['key'], 'lifecycle-retention')
        self.assertEqual(
            [step['key'] for step in labs[8]['steps']],
            ['create-bucket', 'put-object', 'put-lifecycle-configuration', 'get-lifecycle-configuration'],
        )
        self.assertEqual(labs[9]['key'], 'bucket-cors')
        self.assertEqual(
            [step['key'] for step in labs[9]['steps']],
            ['create-bucket', 'put-bucket-cors', 'get-bucket-cors'],
        )
        self.assertEqual(labs[10]['key'], 'object-notifications-sqs')
        self.assertEqual(
            [step['key'] for step in labs[10]['steps']],
            [
                'create-queue',
                'set-queue-policy',
                'create-bucket',
                'put-notification-configuration',
                'get-notification-configuration',
                'put-object',
                'receive-event',
            ],
        )
        self.assertEqual(labs[11]['key'], 'multipart-upload')
        self.assertEqual(
            [step['key'] for step in labs[11]['steps']],
            [
                'create-bucket',
                'create-multipart-upload',
                'upload-part-one',
                'upload-part-two',
                'list-parts',
                'complete-multipart-upload',
                'get-object',
            ],
        )

    def test_sqs_lab_registry_includes_create_queue_workflow(self):
        labs = labs_for_service('sqs')

        self.assertEqual(len(labs), 9)
        self.assertEqual(labs[0]['key'], 'create-queue')
        self.assertEqual(
            [step['key'] for step in labs[0]['steps']],
            ['create-queue', 'get-queue-url', 'get-queue-attributes', 'list-queues'],
        )
        self.assertEqual(labs[1]['key'], 'message-lifecycle')
        self.assertEqual(
            [step['key'] for step in labs[1]['steps']],
            ['create-queue', 'send-message', 'receive-message', 'delete-message'],
        )
        self.assertEqual(labs[2]['key'], 'visibility-timeout')
        self.assertEqual(
            [step['key'] for step in labs[2]['steps']],
            [
                'create-queue',
                'send-message',
                'receive-message',
                'extend-message-visibility',
                'verify-hidden',
                'shorten-message-visibility',
                'receive-after-timeout',
            ],
        )
        self.assertEqual(labs[3]['key'], 'delayed-message')
        self.assertEqual(
            [step['key'] for step in labs[3]['steps']],
            [
                'create-queue',
                'send-delayed-message',
                'get-queue-attributes',
                'receive-after-delay',
            ],
        )
        self.assertEqual(labs[4]['key'], 'batch-messages')
        self.assertEqual(
            [step['key'] for step in labs[4]['steps']],
            [
                'create-queue',
                'send-message-batch',
                'receive-message',
                'delete-message-batch',
            ],
        )
        self.assertEqual(labs[5]['key'], 'queue-configuration')
        self.assertEqual(
            [step['key'] for step in labs[5]['steps']],
            [
                'create-queue',
                'set-queue-attributes',
                'get-queue-attributes',
                'tag-queue',
                'list-queue-tags',
            ],
        )
        self.assertEqual(labs[6]['key'], 'dead-letter-redrive')
        self.assertEqual(
            [step['key'] for step in labs[6]['steps']],
            [
                'create-dlq',
                'create-source-queue',
                'set-redrive-policy',
                'get-redrive-policy',
                'send-message',
                'fail-message-once',
                'fail-message-twice',
                'trigger-dead-lettering',
                'inspect-dlq',
                'start-message-move-task',
                'list-message-move-tasks',
                'receive-redriven-message',
            ],
        )
        self.assertEqual(labs[7]['key'], 'fifo-ordering')
        self.assertEqual(
            [step['key'] for step in labs[7]['steps']],
            [
                'create-queue',
                'get-queue-attributes',
                'send-created',
                'send-duplicate',
                'send-paid',
                'send-fulfilled',
                'inspect-message-count',
                'receive-ordered-messages',
            ],
        )
        self.assertEqual(labs[8]['key'], 'purge-delete')
        self.assertEqual(
            [step['key'] for step in labs[8]['steps']],
            [
                'create-queue',
                'send-message-batch',
                'inspect-populated-queue',
                'purge-queue',
                'inspect-purged-queue',
                'delete-queue',
            ],
        )

    def test_sns_lab_registry_includes_sqs_fanout_workflow(self):
        labs = labs_for_service('sns')

        self.assertEqual(len(labs), 2)
        self.assertEqual(labs[0]['key'], 'sqs-fanout')
        self.assertEqual(
            [step['key'] for step in labs[0]['steps']],
            [
                'create-topic',
                'create-orders-queue',
                'create-audit-queue',
                'set-orders-queue-policy',
                'set-audit-queue-policy',
                'subscribe-orders-queue',
                'subscribe-audit-queue',
                'list-subscriptions',
                'publish-message',
                'receive-orders-copy',
                'receive-audit-copy',
            ],
        )
        self.assertEqual(labs[1]['key'], 'filter-policies')
        self.assertEqual(
            [step['key'] for step in labs[1]['steps']],
            [
                'create-topic',
                'create-created-queue',
                'create-priority-queue',
                'set-created-queue-policy',
                'set-priority-queue-policy',
                'subscribe-created-filter',
                'subscribe-priority-filter',
                'inspect-filter-policies',
                'publish-created-event',
                'publish-priority-event',
                'receive-created-route',
                'receive-priority-route',
            ],
        )

    def test_scheduler_lab_registry_includes_sqs_delivery_workflow(self):
        labs = labs_for_service('scheduler')

        self.assertEqual(len(labs), 1)
        self.assertEqual(labs[0]['key'], 'sqs-delivery')
        self.assertEqual(
            [step['key'] for step in labs[0]['steps']],
            [
                'create-queue',
                'create-role',
                'put-role-policy',
                'create-schedule-group',
                'create-schedule',
                'get-schedule',
                'receive-scheduled-message',
                'confirm-schedule-deleted',
            ],
        )

    def test_cloudformation_lab_registry_includes_s3_sqs_stack_workflow(self):
        labs = labs_for_service('cloudformation')

        self.assertEqual(len(labs), 1)
        self.assertEqual(labs[0]['key'], 's3-sqs-stack')
        self.assertEqual(
            [step['key'] for step in labs[0]['steps']],
            [
                'validate-template',
                'create-stack',
                'describe-stack',
                'describe-stack-resources',
                'describe-stack-events',
                'inspect-provisioned-resources',
                'create-change-set',
                'describe-change-set',
                'execute-change-set',
                'delete-stack',
                'confirm-resources-deleted',
            ],
        )

    def test_ec2_lab_registry_includes_public_private_vpc_workflow(self):
        labs = labs_for_service('ec2')

        self.assertEqual(len(labs), 13)
        self.assertEqual(labs[0]['key'], 'vpc-public-private')
        self.assertEqual(
            [step['key'] for step in labs[0]['steps']],
            [
                'create-vpc',
                'create-public-subnet',
                'enable-public-ip',
                'create-private-subnet',
                'create-internet-gateway',
                'attach-internet-gateway',
                'create-public-route-table',
                'create-internet-route',
                'associate-public-route-table',
                'create-private-route-table',
                'associate-private-route-table',
                'inspect-network-topology',
            ],
        )
        self.assertEqual(labs[1]['key'], 'security-controls')
        self.assertEqual(
            [step['key'] for step in labs[1]['steps']],
            [
                'create-vpc',
                'create-subnet',
                'create-web-security-group',
                'allow-trusted-https',
                'create-app-security-group',
                'allow-web-to-app',
                'inspect-security-groups',
                'create-network-acl',
                'deny-ssh-network-acl',
                'allow-https-network-acl',
                'allow-return-network-acl',
                'associate-network-acl',
                'inspect-network-acl-support',
            ],
        )
        self.assertEqual(labs[2]['key'], 's3-gateway-endpoint')
        self.assertEqual(
            [step['key'] for step in labs[2]['steps']],
            [
                'create-vpc',
                'create-private-subnet',
                'create-private-route-table',
                'associate-private-route-table',
                'create-s3-bucket',
                'create-s3-gateway-endpoint',
                'inspect-private-s3-path',
            ],
        )
        self.assertEqual(labs[3]['key'], 'sqs-interface-endpoint')
        self.assertEqual(
            [step['key'] for step in labs[3]['steps']],
            [
                'create-vpc',
                'create-private-subnet',
                'create-endpoint-security-group',
                'allow-vpc-https',
                'create-sqs-queue',
                'create-sqs-interface-endpoint',
                'inspect-private-sqs-path',
            ],
        )

    @patch('dashboard.labs.FlociClientFactory')
    def test_reset_iam_create_user_lab_deletes_alice(self, factory_mock):
        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        result = reset_lab('iam', 'create-user-alice')

        iam.delete_user.assert_called_once_with(UserName='Alice')
        self.assertTrue(result['reset'])
        self.assertTrue(result['deleted'])
        self.assertEqual(result['command'], 'aws iam delete-user --user-name Alice')

    @patch('dashboard.labs.FlociClientFactory')
    def test_policy_lab_create_policy_step_creates_and_verifies_policy(self, factory_mock):
        iam = MagicMock()
        iam.create_policy.return_value = {
            'Policy': {
                'PolicyName': 'AliceListBucketsPolicy',
                'Arn': 'arn:aws:iam::000000000000:policy/AliceListBucketsPolicy',
            },
        }
        iam.get_policy.return_value = {
            'Policy': {
                'PolicyName': 'AliceListBucketsPolicy',
                'Arn': 'arn:aws:iam::000000000000:policy/AliceListBucketsPolicy',
            },
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'attach-policy-alice', 'create-policy')

        iam.create_policy.assert_called_once()
        kwargs = iam.create_policy.call_args.kwargs
        self.assertEqual(kwargs['PolicyName'], 'AliceListBucketsPolicy')
        self.assertIn('s3:ListAllMyBuckets', kwargs['PolicyDocument'])
        iam.get_policy.assert_called_once_with(PolicyArn='arn:aws:iam::000000000000:policy/AliceListBucketsPolicy')
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_policy_lab_create_policy_step_treats_existing_policy_as_verified(self, factory_mock):
        iam = MagicMock()
        iam.create_policy.side_effect = ClientError(
            {'Error': {'Code': 'EntityAlreadyExists', 'Message': 'Policy already exists'}},
            'CreatePolicy',
        )
        iam.get_policy.return_value = {
            'Policy': {
                'PolicyName': 'AliceListBucketsPolicy',
                'Arn': 'arn:aws:iam::000000000000:policy/AliceListBucketsPolicy',
            },
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'attach-policy-alice', 'create-policy')

        iam.create_policy.assert_called_once()
        iam.get_policy.assert_called_with(PolicyArn='arn:aws:iam::000000000000:policy/AliceListBucketsPolicy')
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_policy_lab_attach_policy_step_attaches_and_verifies_policy(self, factory_mock):
        iam = MagicMock()
        iam.list_attached_user_policies.return_value = {
            'AttachedPolicies': [
                {
                    'PolicyName': 'AliceListBucketsPolicy',
                    'PolicyArn': 'arn:aws:iam::000000000000:policy/AliceListBucketsPolicy',
                },
            ],
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'attach-policy-alice', 'attach-policy')

        iam.attach_user_policy.assert_called_once_with(
            UserName='Alice',
            PolicyArn='arn:aws:iam::000000000000:policy/AliceListBucketsPolicy',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_policy_lab_list_attached_policies_step_returns_attachment(self, factory_mock):
        iam = MagicMock()
        iam.list_attached_user_policies.return_value = {
            'AttachedPolicies': [
                {
                    'PolicyName': 'AliceListBucketsPolicy',
                    'PolicyArn': 'arn:aws:iam::000000000000:policy/AliceListBucketsPolicy',
                },
            ],
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'attach-policy-alice', 'list-attached-policies')

        iam.list_attached_user_policies.assert_called_with(UserName='Alice')
        self.assertTrue(result['verified'])
        self.assertIn('AliceListBucketsPolicy', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_policy_lab_reset_detaches_and_deletes_policy(self, factory_mock):
        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        result = reset_lab('iam', 'attach-policy-alice')

        iam.detach_user_policy.assert_called_once_with(
            UserName='Alice',
            PolicyArn='arn:aws:iam::000000000000:policy/AliceListBucketsPolicy',
        )
        iam.delete_policy.assert_called_once_with(
            PolicyArn='arn:aws:iam::000000000000:policy/AliceListBucketsPolicy',
        )
        self.assertTrue(result['reset'])
        self.assertTrue(result['detached'])
        self.assertTrue(result['deleted'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_access_key_lab_create_key_step_creates_and_verifies_key(self, factory_mock):
        iam = MagicMock()
        iam.create_access_key.return_value = {
            'AccessKey': {
                'UserName': 'Alice',
                'AccessKeyId': 'AKIALOCALALICE',
                'SecretAccessKey': 'local-secret',
                'Status': 'Active',
            },
        }
        iam.list_access_keys.return_value = {
            'AccessKeyMetadata': [
                {
                    'UserName': 'Alice',
                    'AccessKeyId': 'AKIALOCALALICE',
                    'Status': 'Active',
                },
            ],
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'access-key-alice', 'create-access-key')

        iam.create_access_key.assert_called_once_with(UserName='Alice')
        iam.list_access_keys.assert_called_once_with(UserName='Alice')
        self.assertTrue(result['verified'])
        self.assertIn('AKIALOCALALICE', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_access_key_lab_list_keys_step_returns_key_metadata(self, factory_mock):
        iam = MagicMock()
        iam.list_access_keys.return_value = {
            'AccessKeyMetadata': [
                {
                    'UserName': 'Alice',
                    'AccessKeyId': 'AKIALOCALALICE',
                    'Status': 'Active',
                },
            ],
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'access-key-alice', 'list-access-keys')

        iam.list_access_keys.assert_called_with(UserName='Alice')
        self.assertTrue(result['verified'])
        self.assertIn('AKIALOCALALICE', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_access_key_lab_status_marks_key_steps_complete(self, factory_mock):
        iam = MagicMock()
        iam.get_user.return_value = {'User': {'UserName': 'Alice'}}
        iam.list_access_keys.return_value = {
            'AccessKeyMetadata': [
                {
                    'UserName': 'Alice',
                    'AccessKeyId': 'AKIALOCALALICE',
                    'Status': 'Active',
                },
            ],
        }
        factory_mock.return_value.client.return_value = iam

        from .labs import lab_status

        status = lab_status('iam', 'access-key-alice')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-access-key']['verified'])
        self.assertTrue(status['steps']['list-access-keys']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_access_key_lab_reset_deletes_alice_access_keys(self, factory_mock):
        iam = MagicMock()
        iam.list_access_keys.return_value = {
            'AccessKeyMetadata': [
                {'AccessKeyId': 'AKIALOCALONE'},
                {'AccessKeyId': 'AKIALOCALTWO'},
            ],
        }
        factory_mock.return_value.client.return_value = iam

        result = reset_lab('iam', 'access-key-alice')

        iam.delete_access_key.assert_any_call(UserName='Alice', AccessKeyId='AKIALOCALONE')
        iam.delete_access_key.assert_any_call(UserName='Alice', AccessKeyId='AKIALOCALTWO')
        self.assertEqual(iam.delete_access_key.call_count, 2)
        self.assertTrue(result['reset'])
        self.assertTrue(result['deleted'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_lab_create_group_step_creates_and_verifies_group(self, factory_mock):
        iam = MagicMock()
        iam.create_group.return_value = {'Group': {'GroupName': 'FlociDevelopers'}}
        iam.get_group.return_value = {'Group': {'GroupName': 'FlociDevelopers'}, 'Users': []}
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'group-membership-alice', 'create-group')

        iam.create_group.assert_called_once_with(GroupName='FlociDevelopers')
        iam.get_group.assert_called_once_with(GroupName='FlociDevelopers')
        self.assertTrue(result['verified'])
        self.assertIn('FlociDevelopers', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_lab_add_user_step_adds_and_verifies_membership(self, factory_mock):
        iam = MagicMock()
        iam.get_group.return_value = {
            'Group': {'GroupName': 'FlociDevelopers'},
            'Users': [{'UserName': 'Alice'}],
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'group-membership-alice', 'add-user-to-group')

        iam.add_user_to_group.assert_called_once_with(GroupName='FlociDevelopers', UserName='Alice')
        iam.get_group.assert_called_once_with(GroupName='FlociDevelopers')
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_lab_get_group_step_returns_membership(self, factory_mock):
        iam = MagicMock()
        iam.get_group.return_value = {
            'Group': {'GroupName': 'FlociDevelopers'},
            'Users': [{'UserName': 'Alice'}],
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'group-membership-alice', 'get-group')

        iam.get_group.assert_called_with(GroupName='FlociDevelopers')
        self.assertTrue(result['verified'])
        self.assertIn('Alice', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_lab_status_marks_membership_steps_complete(self, factory_mock):
        iam = MagicMock()
        iam.get_user.return_value = {'User': {'UserName': 'Alice'}}
        iam.get_group.return_value = {
            'Group': {'GroupName': 'FlociDevelopers'},
            'Users': [{'UserName': 'Alice'}],
        }
        factory_mock.return_value.client.return_value = iam

        from .labs import lab_status

        status = lab_status('iam', 'group-membership-alice')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-group']['verified'])
        self.assertTrue(status['steps']['add-user-to-group']['verified'])
        self.assertTrue(status['steps']['get-group']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_lab_reset_removes_alice_then_deletes_group(self, factory_mock):
        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        result = reset_lab('iam', 'group-membership-alice')

        iam.remove_user_from_group.assert_called_once_with(GroupName='FlociDevelopers', UserName='Alice')
        iam.delete_group.assert_called_once_with(GroupName='FlociDevelopers')
        self.assertTrue(result['reset'])
        self.assertTrue(result['removed'])
        self.assertTrue(result['deleted'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_policy_lab_create_policy_step_creates_and_verifies_policy(self, factory_mock):
        iam = MagicMock()
        iam.create_policy.return_value = {
            'Policy': {
                'PolicyName': 'FlociDevelopersListBucketsPolicy',
                'Arn': 'arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
            },
        }
        iam.get_policy.return_value = {
            'Policy': {
                'PolicyName': 'FlociDevelopersListBucketsPolicy',
                'Arn': 'arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
            },
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'group-policy-floci-developers', 'create-policy')

        iam.create_policy.assert_called_once()
        kwargs = iam.create_policy.call_args.kwargs
        self.assertEqual(kwargs['PolicyName'], 'FlociDevelopersListBucketsPolicy')
        self.assertIn('s3:ListAllMyBuckets', kwargs['PolicyDocument'])
        iam.get_policy.assert_called_once_with(
            PolicyArn='arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_policy_lab_attach_policy_step_attaches_and_verifies_policy(self, factory_mock):
        iam = MagicMock()
        iam.list_attached_group_policies.return_value = {
            'AttachedPolicies': [
                {
                    'PolicyName': 'FlociDevelopersListBucketsPolicy',
                    'PolicyArn': 'arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
                },
            ],
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'group-policy-floci-developers', 'attach-group-policy')

        iam.attach_group_policy.assert_called_once_with(
            GroupName='FlociDevelopers',
            PolicyArn='arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_policy_lab_list_attached_policies_step_returns_attachment(self, factory_mock):
        iam = MagicMock()
        iam.list_attached_group_policies.return_value = {
            'AttachedPolicies': [
                {
                    'PolicyName': 'FlociDevelopersListBucketsPolicy',
                    'PolicyArn': 'arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
                },
            ],
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'group-policy-floci-developers', 'list-attached-group-policies')

        iam.list_attached_group_policies.assert_called_with(GroupName='FlociDevelopers')
        self.assertTrue(result['verified'])
        self.assertIn('FlociDevelopersListBucketsPolicy', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_policy_lab_status_marks_all_steps_complete(self, factory_mock):
        iam = MagicMock()
        iam.get_user.return_value = {'User': {'UserName': 'Alice'}}
        iam.get_group.return_value = {
            'Group': {'GroupName': 'FlociDevelopers'},
            'Users': [{'UserName': 'Alice'}],
        }
        iam.get_policy.return_value = {
            'Policy': {
                'PolicyName': 'FlociDevelopersListBucketsPolicy',
                'Arn': 'arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
            },
        }
        iam.list_attached_group_policies.return_value = {
            'AttachedPolicies': [
                {
                    'PolicyName': 'FlociDevelopersListBucketsPolicy',
                    'PolicyArn': 'arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
                },
            ],
        }
        factory_mock.return_value.client.return_value = iam

        from .labs import lab_status

        status = lab_status('iam', 'group-policy-floci-developers')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-group']['verified'])
        self.assertTrue(status['steps']['add-user-to-group']['verified'])
        self.assertTrue(status['steps']['create-policy']['verified'])
        self.assertTrue(status['steps']['attach-group-policy']['verified'])
        self.assertTrue(status['steps']['list-attached-group-policies']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_group_policy_lab_reset_detaches_policy_and_removes_group(self, factory_mock):
        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        result = reset_lab('iam', 'group-policy-floci-developers')

        iam.detach_group_policy.assert_called_once_with(
            GroupName='FlociDevelopers',
            PolicyArn='arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
        )
        iam.delete_policy.assert_called_once_with(
            PolicyArn='arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy',
        )
        iam.remove_user_from_group.assert_called_once_with(GroupName='FlociDevelopers', UserName='Alice')
        iam.delete_group.assert_called_once_with(GroupName='FlociDevelopers')
        self.assertTrue(result['reset'])
        self.assertTrue(result['detached'])
        self.assertTrue(result['deleted_policy'])
        self.assertTrue(result['removed'])
        self.assertTrue(result['deleted_group'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_inline_policy_lab_puts_and_verifies_policy(self, factory_mock):
        iam = MagicMock()
        iam.get_user_policy.return_value = {
            'UserName': 'Alice',
            'PolicyName': 'AliceInlineListBuckets',
            'PolicyDocument': {'Statement': [{'Action': 's3:ListAllMyBuckets'}]},
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'inline-policy-alice', 'put-user-policy')

        iam.put_user_policy.assert_called_once()
        kwargs = iam.put_user_policy.call_args.kwargs
        self.assertEqual(kwargs['UserName'], 'Alice')
        self.assertEqual(kwargs['PolicyName'], 'AliceInlineListBuckets')
        self.assertIn('s3:ListAllMyBuckets', kwargs['PolicyDocument'])
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_inline_policy_lab_lists_policy_names(self, factory_mock):
        iam = MagicMock()
        iam.list_user_policies.return_value = {'PolicyNames': ['AliceInlineListBuckets']}
        iam.get_user_policy.return_value = {
            'UserName': 'Alice',
            'PolicyName': 'AliceInlineListBuckets',
            'PolicyDocument': {'Statement': [{'Action': 's3:ListAllMyBuckets'}]},
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'inline-policy-alice', 'list-user-policies')

        iam.list_user_policies.assert_called_once_with(UserName='Alice')
        self.assertTrue(result['verified'])
        self.assertIn('AliceInlineListBuckets', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_inline_policy_lab_gets_policy_document(self, factory_mock):
        iam = MagicMock()
        iam.get_user_policy.return_value = {
            'UserName': 'Alice',
            'PolicyName': 'AliceInlineListBuckets',
            'PolicyDocument': {'Statement': [{'Action': 's3:ListAllMyBuckets'}]},
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'inline-policy-alice', 'get-user-policy')

        iam.get_user_policy.assert_called_with(
            UserName='Alice',
            PolicyName='AliceInlineListBuckets',
        )
        self.assertTrue(result['verified'])
        self.assertIn('s3:ListAllMyBuckets', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_inline_policy_lab_status_marks_policy_steps_complete(self, factory_mock):
        iam = MagicMock()
        iam.get_user.return_value = {'User': {'UserName': 'Alice'}}
        iam.get_user_policy.return_value = {
            'UserName': 'Alice',
            'PolicyName': 'AliceInlineListBuckets',
            'PolicyDocument': {'Statement': [{'Action': 's3:ListAllMyBuckets'}]},
        }
        factory_mock.return_value.client.return_value = iam

        from .labs import lab_status

        status = lab_status('iam', 'inline-policy-alice')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['put-user-policy']['verified'])
        self.assertTrue(status['steps']['list-user-policies']['verified'])
        self.assertTrue(status['steps']['get-user-policy']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_inline_policy_lab_reset_deletes_policy_and_leaves_alice(self, factory_mock):
        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        result = reset_lab('iam', 'inline-policy-alice')

        iam.delete_user_policy.assert_called_once_with(
            UserName='Alice',
            PolicyName='AliceInlineListBuckets',
        )
        iam.delete_user.assert_not_called()
        self.assertTrue(result['reset'])
        self.assertTrue(result['deleted'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_role_lab_creates_role_with_lambda_trust_policy(self, factory_mock):
        iam = MagicMock()
        role = {
            'RoleName': 'FlociApplicationRole',
            'AssumeRolePolicyDocument': {
                'Statement': [{
                    'Effect': 'Allow',
                    'Principal': {'Service': 'lambda.amazonaws.com'},
                    'Action': 'sts:AssumeRole',
                }],
            },
        }
        iam.create_role.return_value = {'Role': role}
        iam.get_role.return_value = {'Role': role}
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'role-trust-policy', 'create-role')

        iam.create_role.assert_called_once()
        kwargs = iam.create_role.call_args.kwargs
        self.assertEqual(kwargs['RoleName'], 'FlociApplicationRole')
        self.assertIn('lambda.amazonaws.com', kwargs['AssumeRolePolicyDocument'])
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_role_lab_puts_and_verifies_permissions_policy(self, factory_mock):
        iam = MagicMock()
        iam.get_role_policy.return_value = {
            'RoleName': 'FlociApplicationRole',
            'PolicyName': 'FlociApplicationListBuckets',
            'PolicyDocument': {'Statement': [{'Action': 's3:ListAllMyBuckets'}]},
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'role-trust-policy', 'put-role-policy')

        iam.put_role_policy.assert_called_once()
        kwargs = iam.put_role_policy.call_args.kwargs
        self.assertEqual(kwargs['RoleName'], 'FlociApplicationRole')
        self.assertEqual(kwargs['PolicyName'], 'FlociApplicationListBuckets')
        self.assertIn('s3:ListAllMyBuckets', kwargs['PolicyDocument'])
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_role_lab_status_marks_trust_and_permissions_complete(self, factory_mock):
        iam = MagicMock()
        iam.get_role.return_value = {
            'Role': {
                'RoleName': 'FlociApplicationRole',
                'AssumeRolePolicyDocument': {
                    'Statement': [{
                        'Effect': 'Allow',
                        'Principal': {'Service': 'lambda.amazonaws.com'},
                        'Action': 'sts:AssumeRole',
                    }],
                },
            },
        }
        iam.get_role_policy.return_value = {
            'RoleName': 'FlociApplicationRole',
            'PolicyName': 'FlociApplicationListBuckets',
        }
        factory_mock.return_value.client.return_value = iam

        from .labs import lab_status

        status = lab_status('iam', 'role-trust-policy')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-role']['verified'])
        self.assertTrue(status['steps']['get-role']['verified'])
        self.assertTrue(status['steps']['put-role-policy']['verified'])
        self.assertTrue(status['steps']['get-role-policy']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_role_lab_reset_deletes_policy_before_role(self, factory_mock):
        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        result = reset_lab('iam', 'role-trust-policy')

        iam.delete_role_policy.assert_called_once_with(
            RoleName='FlociApplicationRole',
            PolicyName='FlociApplicationListBuckets',
        )
        iam.delete_role.assert_called_once_with(RoleName='FlociApplicationRole')
        self.assertTrue(result['deleted_policy'])
        self.assertTrue(result['deleted_role'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sts_session_policy_lab_creates_account_trusted_role(self, factory_mock):
        iam = MagicMock()
        role = {
            'RoleName': 'FlociStsSessionRole',
            'AssumeRolePolicyDocument': {
                'Statement': [{
                    'Effect': 'Allow',
                    'Principal': {'AWS': 'arn:aws:iam::000000000000:root'},
                    'Action': 'sts:AssumeRole',
                }],
            },
        }
        iam.create_role.return_value = {'Role': role}
        iam.get_role.return_value = {'Role': role}
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'sts-session-policy', 'create-role')

        iam.create_role.assert_called_once()
        kwargs = iam.create_role.call_args.kwargs
        self.assertEqual(kwargs['RoleName'], 'FlociStsSessionRole')
        self.assertIn('arn:aws:iam::000000000000:root', kwargs['AssumeRolePolicyDocument'])
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sts_session_policy_lab_puts_role_permissions_policy(self, factory_mock):
        iam = MagicMock()
        iam.get_role_policy.return_value = {
            'RoleName': 'FlociStsSessionRole',
            'PolicyName': 'FlociStsListBuckets',
            'PolicyDocument': {'Statement': [{'Action': 's3:ListAllMyBuckets'}]},
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'sts-session-policy', 'put-role-policy')

        iam.put_role_policy.assert_called_once()
        kwargs = iam.put_role_policy.call_args.kwargs
        self.assertEqual(kwargs['RoleName'], 'FlociStsSessionRole')
        self.assertEqual(kwargs['PolicyName'], 'FlociStsListBuckets')
        self.assertIn('s3:ListAllMyBuckets', kwargs['PolicyDocument'])
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sts_session_policy_lab_assumes_role_with_session_policy(self, factory_mock):
        sts = MagicMock()
        sts.assume_role.return_value = {
            'Credentials': {
                'AccessKeyId': 'ASIASESSION',
                'SecretAccessKey': 'secret',
                'SessionToken': 'token',
            },
            'AssumedRoleUser': {'Arn': 'arn:aws:sts::000000000000:assumed-role/FlociStsSessionRole/floci-session-policy-lab'},
        }
        factory_mock.return_value.client.return_value = sts
        cache.delete('floci-lab:iam:sts-session-policy:assumed')

        result = run_lab_step('iam', 'sts-session-policy', 'assume-role')

        sts.assume_role.assert_called_once()
        kwargs = sts.assume_role.call_args.kwargs
        self.assertEqual(kwargs['RoleArn'], 'arn:aws:iam::000000000000:role/FlociStsSessionRole')
        self.assertEqual(kwargs['RoleSessionName'], 'floci-session-policy-lab')
        self.assertIn('s3:ListAllMyBuckets', kwargs['Policy'])
        self.assertTrue(result['verified'])
        self.assertEqual(cache.get('floci-lab:iam:sts-session-policy:assumed')['access_key_id'], 'ASIASESSION')

    @patch('dashboard.labs.FlociClientFactory')
    def test_sts_session_policy_lab_status_marks_steps_complete(self, factory_mock):
        iam = MagicMock()
        iam.get_role.return_value = {
            'Role': {
                'RoleName': 'FlociStsSessionRole',
                'AssumeRolePolicyDocument': {
                    'Statement': [{
                        'Effect': 'Allow',
                        'Principal': {'AWS': 'arn:aws:iam::000000000000:root'},
                        'Action': 'sts:AssumeRole',
                    }],
                },
            },
        }
        iam.get_role_policy.return_value = {
            'RoleName': 'FlociStsSessionRole',
            'PolicyName': 'FlociStsListBuckets',
        }
        factory_mock.return_value.client.return_value = iam
        cache.set('floci-lab:iam:sts-session-policy:assumed', {'access_key_id': 'ASIASESSION'}, timeout=3600)

        from .labs import lab_status

        status = lab_status('iam', 'sts-session-policy')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-role']['verified'])
        self.assertTrue(status['steps']['put-role-policy']['verified'])
        self.assertTrue(status['steps']['assume-role']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sts_session_policy_lab_reset_deletes_policy_role_and_session_marker(self, factory_mock):
        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam
        cache.set('floci-lab:iam:sts-session-policy:assumed', {'access_key_id': 'ASIASESSION'}, timeout=3600)

        result = reset_lab('iam', 'sts-session-policy')

        iam.delete_role_policy.assert_called_once_with(
            RoleName='FlociStsSessionRole',
            PolicyName='FlociStsListBuckets',
        )
        iam.delete_role.assert_called_once_with(RoleName='FlociStsSessionRole')
        self.assertIsNone(cache.get('floci-lab:iam:sts-session-policy:assumed'))
        self.assertTrue(result['deleted_policy'])
        self.assertTrue(result['deleted_role'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_identity_enforcement_lab_allows_charlie_to_assume_role(self, factory_mock):
        iam = MagicMock()
        iam.create_user.return_value = {
            'User': {
                'UserName': 'Charlie',
                'Arn': 'arn:aws:iam::000000000000:user/Charlie',
            },
        }
        iam.get_user.return_value = {
            'User': {
                'UserName': 'Charlie',
                'Arn': 'arn:aws:iam::000000000000:user/Charlie',
            },
        }
        iam.list_access_keys.return_value = {'AccessKeyMetadata': []}
        iam.create_access_key.return_value = {
            'AccessKey': {
                'AccessKeyId': 'AKIACHARLIE',
                'SecretAccessKey': 'secret',
            },
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'identity-enforcement-capstone', 'create-user')

        self.assertTrue(result['verified'])
        iam.put_user_policy.assert_called_once()
        policy = json.loads(iam.put_user_policy.call_args.kwargs['PolicyDocument'])
        self.assertIn(
            {
                'Effect': 'Allow',
                'Action': 'sts:AssumeRole',
                'Resource': '*',
            },
            policy['Statement'],
        )

    @patch('dashboard.labs.FlociClientFactory')
    def test_instance_profile_lab_creates_profile(self, factory_mock):
        iam = MagicMock()
        iam.create_instance_profile.return_value = {
            'InstanceProfile': {
                'InstanceProfileName': 'FlociEc2InstanceProfile',
                'Roles': [],
            },
        }
        iam.get_instance_profile.return_value = {
            'InstanceProfile': {
                'InstanceProfileName': 'FlociEc2InstanceProfile',
                'Roles': [],
            },
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'ec2-instance-profile', 'create-instance-profile')

        iam.create_instance_profile.assert_called_once_with(
            InstanceProfileName='FlociEc2InstanceProfile',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_instance_profile_lab_adds_role_and_verifies_association(self, factory_mock):
        iam = MagicMock()
        iam.get_instance_profile.side_effect = [
            {
                'InstanceProfile': {
                    'InstanceProfileName': 'FlociEc2InstanceProfile',
                    'Roles': [],
                },
            },
            {
                'InstanceProfile': {
                    'InstanceProfileName': 'FlociEc2InstanceProfile',
                    'Roles': [{'RoleName': 'FlociEc2Role'}],
                },
            },
        ]
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'ec2-instance-profile', 'add-role-to-instance-profile')

        iam.add_role_to_instance_profile.assert_called_once_with(
            InstanceProfileName='FlociEc2InstanceProfile',
            RoleName='FlociEc2Role',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_instance_profile_lab_status_marks_association_steps_complete(self, factory_mock):
        iam = MagicMock()
        iam.get_role.return_value = {
            'Role': {
                'RoleName': 'FlociEc2Role',
                'AssumeRolePolicyDocument': {
                    'Statement': [{
                        'Effect': 'Allow',
                        'Principal': {'Service': 'ec2.amazonaws.com'},
                        'Action': 'sts:AssumeRole',
                    }],
                },
            },
        }
        iam.get_instance_profile.return_value = {
            'InstanceProfile': {
                'InstanceProfileName': 'FlociEc2InstanceProfile',
                'Roles': [{'RoleName': 'FlociEc2Role'}],
            },
        }
        factory_mock.return_value.client.return_value = iam

        from .labs import lab_status

        status = lab_status('iam', 'ec2-instance-profile')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-role']['verified'])
        self.assertTrue(status['steps']['create-instance-profile']['verified'])
        self.assertTrue(status['steps']['add-role-to-instance-profile']['verified'])
        self.assertTrue(status['steps']['get-instance-profile']['verified'])
        self.assertTrue(status['steps']['list-instance-profiles-for-role']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_instance_profile_lab_reset_removes_association_before_resources(self, factory_mock):
        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        result = reset_lab('iam', 'ec2-instance-profile')

        iam.remove_role_from_instance_profile.assert_called_once_with(
            InstanceProfileName='FlociEc2InstanceProfile',
            RoleName='FlociEc2Role',
        )
        iam.delete_instance_profile.assert_called_once_with(
            InstanceProfileName='FlociEc2InstanceProfile',
        )
        iam.delete_role.assert_called_once_with(RoleName='FlociEc2Role')
        self.assertTrue(result['removed'])
        self.assertTrue(result['deleted_profile'])
        self.assertTrue(result['deleted_role'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_create_bucket_step_creates_and_verifies_bucket(self, factory_mock):
        s3 = MagicMock()
        s3.create_bucket.return_value = {'Location': '/lab-basics'}
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'create-bucket', 'create-bucket')

        s3.create_bucket.assert_called_once_with(Bucket='lab-basics')
        s3.head_bucket.assert_called_once_with(Bucket='lab-basics')
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_create_bucket_step_treats_existing_bucket_as_verified(self, factory_mock):
        s3 = MagicMock()
        s3.create_bucket.side_effect = ClientError(
            {'Error': {'Code': 'BucketAlreadyOwnedByYou', 'Message': 'Bucket exists'}},
            'CreateBucket',
        )
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'create-bucket', 'create-bucket')

        self.assertTrue(result['verified'])
        self.assertGreaterEqual(s3.head_bucket.call_count, 2)

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_head_bucket_step_verifies_bucket(self, factory_mock):
        s3 = MagicMock()
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'create-bucket', 'head-bucket')

        self.assertTrue(result['verified'])
        self.assertIn('us-east-1', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_list_buckets_step_returns_lab_bucket(self, factory_mock):
        s3 = MagicMock()
        s3.list_buckets.return_value = {
            'Buckets': [{'Name': 'lab-basics'}],
        }
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'create-bucket', 'list-buckets')

        s3.list_buckets.assert_called_once_with()
        self.assertTrue(result['verified'])
        self.assertIn('lab-basics', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_lab_status_marks_all_steps_complete_from_live_bucket(self, factory_mock):
        s3 = MagicMock()
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'create-bucket')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-bucket']['verified'])
        self.assertTrue(status['steps']['head-bucket']['verified'])
        self.assertTrue(status['steps']['list-buckets']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_lab_reset_removes_versions_objects_and_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = [{
            'Versions': [{'Key': 'versioned.txt', 'VersionId': 'v1'}],
            'DeleteMarkers': [{'Key': 'deleted.txt', 'VersionId': 'd1'}],
        }]
        object_paginator.paginate.return_value = [{
            'Contents': [{'Key': 'plain.txt'}],
        }]
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'create-bucket')

        self.assertEqual(s3.delete_objects.call_count, 2)
        s3.delete_bucket.assert_called_once_with(Bucket='lab-basics')
        self.assertEqual(result['deleted_objects'], 3)
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_object_lab_uploads_exact_artifact_and_verifies_it(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Hello from the Floci S3 lab!\n'
        s3.put_object.return_value = {'ETag': '"etag"'}
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 29,
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'object-workflow', 'put-object')

        s3.put_object.assert_called_once_with(
            Bucket='lab-objects',
            Key='hello.txt',
            Body=b'Hello from the Floci S3 lab!\n',
            ContentType='text/plain',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_object_lab_lists_and_verifies_hello_object(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Hello from the Floci S3 lab!\n'
        s3.list_objects_v2.return_value = {
            'Contents': [{'Key': 'hello.txt', 'Size': 29}],
        }
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 29,
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'object-workflow', 'list-objects')

        s3.list_objects_v2.assert_called_once_with(Bucket='lab-objects')
        self.assertTrue(result['verified'])
        self.assertIn('hello.txt', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_object_lab_download_response_includes_exact_body(self, factory_mock):
        s3 = MagicMock()
        download_body = MagicMock()
        download_body.read.return_value = b'Hello from the Floci S3 lab!\n'
        verify_body = MagicMock()
        verify_body.read.return_value = b'Hello from the Floci S3 lab!\n'
        s3.get_object.side_effect = [
            {'Body': download_body, 'ContentType': 'text/plain', 'ContentLength': 29},
            {'Body': verify_body},
        ]
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 29,
        }
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'object-workflow', 'get-object')

        self.assertTrue(result['verified'])
        self.assertIn('downloaded-hello.txt', result['stdout'])
        self.assertIn('Hello from the Floci S3 lab!', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_object_lab_status_requires_matching_content_and_metadata(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Hello from the Floci S3 lab!\n'
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 29,
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'object-workflow')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-bucket']['verified'])
        self.assertTrue(status['steps']['put-object']['verified'])
        self.assertTrue(status['steps']['get-object']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_object_lab_reset_deletes_its_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = [{
            'Contents': [{'Key': 'hello.txt'}],
        }]
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'object-workflow')

        s3.delete_objects.assert_called_once_with(
            Bucket='lab-objects',
            Delete={'Objects': [{'Key': 'hello.txt'}], 'Quiet': True},
        )
        s3.delete_bucket.assert_called_once_with(Bucket='lab-objects')
        self.assertEqual(result['lab'], 'object-workflow')
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_prefix_lab_uploads_source_report(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Floci S3 lab report\nStatus: ready for archive\n'
        s3.put_object.return_value = {'ETag': '"source"'}
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'prefix-copy', 'put-source-object')

        s3.put_object.assert_called_once_with(
            Bucket='lab-prefixes',
            Key='incoming/report.txt',
            Body=b'Floci S3 lab report\nStatus: ready for archive\n',
            ContentType='text/plain',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_prefix_lab_lists_exact_incoming_prefix(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Floci S3 lab report\nStatus: ready for archive\n'
        s3.list_objects_v2.return_value = {
            'Contents': [{'Key': 'incoming/report.txt'}],
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'prefix-copy', 'list-incoming-prefix')

        s3.list_objects_v2.assert_called_once_with(
            Bucket='lab-prefixes',
            Prefix='incoming/',
        )
        self.assertTrue(result['verified'])
        self.assertIn('incoming/report.txt', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_prefix_lab_copies_report_to_archive(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Floci S3 lab report\nStatus: ready for archive\n'
        s3.copy_object.return_value = {'CopyObjectResult': {'ETag': '"archive"'}}
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'prefix-copy', 'copy-object')

        s3.copy_object.assert_called_once_with(
            CopySource={
                'Bucket': 'lab-prefixes',
                'Key': 'incoming/report.txt',
            },
            Bucket='lab-prefixes',
            Key='archive/report.txt',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_prefix_lab_status_tracks_source_and_archive_separately(self, factory_mock):
        s3 = MagicMock()
        source_body = MagicMock()
        source_body.read.return_value = b'Floci S3 lab report\nStatus: ready for archive\n'
        archive_body = MagicMock()
        archive_body.read.return_value = b'Floci S3 lab report\nStatus: ready for archive\n'
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.get_object.side_effect = [
            {'Body': source_body},
            {'Body': archive_body},
        ]
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'prefix-copy')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['put-source-object']['verified'])
        self.assertTrue(status['steps']['list-incoming-prefix']['verified'])
        self.assertTrue(status['steps']['copy-object']['verified'])
        self.assertTrue(status['steps']['list-archive-prefix']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_prefix_lab_reset_deletes_both_objects_and_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = [{
            'Contents': [
                {'Key': 'incoming/report.txt'},
                {'Key': 'archive/report.txt'},
            ],
        }]
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'prefix-copy')

        s3.delete_objects.assert_called_once_with(
            Bucket='lab-prefixes',
            Delete={
                'Objects': [
                    {'Key': 'incoming/report.txt'},
                    {'Key': 'archive/report.txt'},
                ],
                'Quiet': True,
            },
        )
        s3.delete_bucket.assert_called_once_with(Bucket='lab-prefixes')
        self.assertEqual(result['deleted_objects'], 2)
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_metadata_lab_uploads_invoice_with_user_metadata(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Invoice: FLOCI-1001\nAmount: 42.00\n'
        s3.put_object.return_value = {'ETag': '"invoice"'}
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 34,
            'Metadata': {
                'project': 'floci',
                'classification': 'internal',
            },
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'metadata-tags', 'put-object')

        s3.put_object.assert_called_once_with(
            Bucket='lab-metadata',
            Key='documents/invoice.txt',
            Body=b'Invoice: FLOCI-1001\nAmount: 42.00\n',
            ContentType='text/plain',
            Metadata={
                'project': 'floci',
                'classification': 'internal',
            },
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_metadata_lab_head_object_returns_metadata(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Invoice: FLOCI-1001\nAmount: 42.00\n'
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 34,
            'Metadata': {
                'project': 'floci',
                'classification': 'internal',
            },
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'metadata-tags', 'head-object')

        s3.head_object.assert_called_with(
            Bucket='lab-metadata',
            Key='documents/invoice.txt',
        )
        self.assertTrue(result['verified'])
        self.assertIn('classification', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_metadata_lab_applies_expected_tags(self, factory_mock):
        s3 = MagicMock()
        s3.put_object_tagging.return_value = {}
        s3.get_object_tagging.return_value = {
            'TagSet': [
                {'Key': 'CostCenter', 'Value': 'training'},
                {'Key': 'Environment', 'Value': 'lab'},
            ],
        }
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'metadata-tags', 'put-object-tagging')

        s3.put_object_tagging.assert_called_once_with(
            Bucket='lab-metadata',
            Key='documents/invoice.txt',
            Tagging={
                'TagSet': [
                    {'Key': 'Environment', 'Value': 'lab'},
                    {'Key': 'CostCenter', 'Value': 'training'},
                ],
            },
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_metadata_lab_status_tracks_metadata_and_tags_separately(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Invoice: FLOCI-1001\nAmount: 42.00\n'
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 34,
            'Metadata': {
                'project': 'floci',
                'classification': 'internal',
            },
        }
        s3.get_object.return_value = {'Body': body}
        s3.get_object_tagging.return_value = {
            'TagSet': [
                {'Key': 'Environment', 'Value': 'lab'},
                {'Key': 'CostCenter', 'Value': 'training'},
            ],
        }
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'metadata-tags')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['put-object']['verified'])
        self.assertTrue(status['steps']['head-object']['verified'])
        self.assertTrue(status['steps']['put-object-tagging']['verified'])
        self.assertTrue(status['steps']['get-object-tagging']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_metadata_lab_reset_deletes_object_and_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = [{
            'Contents': [{'Key': 'documents/invoice.txt'}],
        }]
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'metadata-tags')

        s3.delete_objects.assert_called_once_with(
            Bucket='lab-metadata',
            Delete={
                'Objects': [{'Key': 'documents/invoice.txt'}],
                'Quiet': True,
            },
        )
        s3.delete_bucket.assert_called_once_with(Bucket='lab-metadata')
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_version_lab_enables_bucket_versioning(self, factory_mock):
        s3 = MagicMock()
        s3.put_bucket_versioning.return_value = {}
        s3.get_bucket_versioning.return_value = {'Status': 'Enabled'}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'version-recovery', 'enable-versioning')

        s3.put_bucket_versioning.assert_called_once_with(
            Bucket='lab-versioning',
            VersioningConfiguration={'Status': 'Enabled'},
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_version_lab_writes_v1_and_verifies_its_content(self, factory_mock):
        s3 = MagicMock()
        s3.put_object.return_value = {'VersionId': 'version-one'}
        s3.list_object_versions.return_value = {
            'Versions': [{
                'Key': 'configuration.txt',
                'VersionId': 'version-one',
                'IsLatest': True,
            }],
        }

        def get_object(**kwargs):
            body = MagicMock()
            body.read.return_value = b'feature_enabled=false\nrelease=v1\n'
            return {'Body': body, 'VersionId': kwargs['VersionId']}

        s3.get_object.side_effect = get_object
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'version-recovery', 'put-version-one')

        s3.put_object.assert_called_once_with(
            Bucket='lab-versioning',
            Key='configuration.txt',
            Body=b'feature_enabled=false\nrelease=v1\n',
            ContentType='text/plain',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_version_lab_identifies_latest_v2_and_preserved_v1(self, factory_mock):
        s3 = MagicMock()
        s3.list_object_versions.return_value = {
            'Versions': [
                {
                    'Key': 'configuration.txt',
                    'VersionId': 'version-two',
                    'IsLatest': True,
                },
                {
                    'Key': 'configuration.txt',
                    'VersionId': 'version-one',
                    'IsLatest': False,
                },
            ],
        }

        def get_object(**kwargs):
            body = MagicMock()
            body.read.return_value = (
                b'feature_enabled=false\nrelease=v1\n'
                if kwargs['VersionId'] == 'version-one'
                else b'feature_enabled=true\nrelease=v2\n'
            )
            return {'Body': body, 'VersionId': kwargs['VersionId']}

        s3.get_object.side_effect = get_object
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'version-recovery', 'list-object-versions')

        self.assertTrue(result['verified'])
        self.assertIn('version-one', result['stdout'])
        self.assertIn('version-two', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_version_lab_skips_unreadable_stale_versions(self, factory_mock):
        s3 = MagicMock()
        s3.put_object.return_value = {'VersionId': 'new-version-one'}
        s3.list_object_versions.return_value = {
            'Versions': [
                {
                    'Key': 'configuration.txt',
                    'VersionId': 'new-version-one',
                    'IsLatest': True,
                },
                {
                    'Key': 'configuration.txt',
                    'VersionId': 'stale-version',
                    'IsLatest': False,
                },
            ],
        }

        def get_object(**kwargs):
            if kwargs['VersionId'] == 'stale-version':
                raise ClientError(
                    {'Error': {'Code': '500', 'Message': 'Internal Server Error'}},
                    'GetObject',
                )
            body = MagicMock()
            body.read.return_value = b'feature_enabled=false\nrelease=v1\n'
            return {'Body': body}

        s3.get_object.side_effect = get_object
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'version-recovery', 'put-version-one')

        self.assertTrue(result['verified'])
        self.assertIn('new-version-one', result['verification']['message'])
        self.assertNotIn('stale-version', result['verification']['message'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_version_lab_recovers_v1_by_discovered_version_id(self, factory_mock):
        s3 = MagicMock()
        s3.list_object_versions.return_value = {
            'Versions': [
                {
                    'Key': 'configuration.txt',
                    'VersionId': 'version-two',
                    'IsLatest': True,
                },
                {
                    'Key': 'configuration.txt',
                    'VersionId': 'version-one',
                    'IsLatest': False,
                },
            ],
        }

        def get_object(**kwargs):
            body = MagicMock()
            body.read.return_value = (
                b'feature_enabled=false\nrelease=v1\n'
                if kwargs['VersionId'] == 'version-one'
                else b'feature_enabled=true\nrelease=v2\n'
            )
            return {'Body': body, 'VersionId': kwargs['VersionId']}

        s3.get_object.side_effect = get_object
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'version-recovery', 'get-version-one')

        self.assertTrue(result['verified'])
        self.assertIn('"VersionId": "version-one"', result['stdout'])
        self.assertIn('feature_enabled=false', result['stdout'])
        self.assertIn('recovered-configuration.txt', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_version_lab_status_requires_latest_v2(self, factory_mock):
        s3 = MagicMock()
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.get_bucket_versioning.return_value = {'Status': 'Enabled'}
        s3.list_object_versions.return_value = {
            'Versions': [
                {
                    'Key': 'configuration.txt',
                    'VersionId': 'version-two',
                    'IsLatest': True,
                },
                {
                    'Key': 'configuration.txt',
                    'VersionId': 'version-one',
                    'IsLatest': False,
                },
            ],
        }

        def get_object(**kwargs):
            body = MagicMock()
            body.read.return_value = (
                b'feature_enabled=false\nrelease=v1\n'
                if kwargs['VersionId'] == 'version-one'
                else b'feature_enabled=true\nrelease=v2\n'
            )
            return {'Body': body}

        s3.get_object.side_effect = get_object
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'version-recovery')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['enable-versioning']['verified'])
        self.assertTrue(status['steps']['put-version-one']['verified'])
        self.assertTrue(status['steps']['put-version-two']['verified'])
        self.assertTrue(status['steps']['get-version-one']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_version_lab_reset_deletes_all_versions(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = [{
            'Versions': [
                {'Key': 'configuration.txt', 'VersionId': 'version-two'},
                {'Key': 'configuration.txt', 'VersionId': 'version-one'},
            ],
        }]
        object_paginator.paginate.return_value = []
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'version-recovery')

        s3.delete_objects.assert_called_once_with(
            Bucket='lab-versioning',
            Delete={
                'Objects': [
                    {'Key': 'configuration.txt', 'VersionId': 'version-two'},
                    {'Key': 'configuration.txt', 'VersionId': 'version-one'},
                ],
                'Quiet': True,
            },
        )
        s3.delete_bucket.assert_called_once_with(Bucket='lab-versioning')
        self.assertEqual(result['deleted_objects'], 2)

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_presigned_lab_uploads_exact_guide(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Temporary access with an S3 presigned URL.\n'
        s3.put_object.return_value = {'ETag': '"guide"'}
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'presigned-url', 'put-object')

        s3.put_object.assert_called_once_with(
            Bucket='lab-presigned',
            Key='shared/guide.txt',
            Body=b'Temporary access with an S3 presigned URL.\n',
            ContentType='text/plain',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.s3_labs._redeem_s3_presigned_url')
    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_presigned_lab_generates_and_redeems_url(self, factory_mock, redeem_mock):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = (
            'http://localhost:4566/lab-presigned/shared/guide.txt?signature=sample'
        )
        factory_mock.return_value.client.return_value = s3
        redeem_mock.return_value = b'Temporary access with an S3 presigned URL.\n'

        result = run_lab_step('s3', 'presigned-url', 'presign-object')

        s3.generate_presigned_url.assert_called_once_with(
            'get_object',
            Params={
                'Bucket': 'lab-presigned',
                'Key': 'shared/guide.txt',
            },
            ExpiresIn=300,
        )
        redeem_mock.assert_called_once_with(
            'http://localhost:4566/lab-presigned/shared/guide.txt?signature=sample',
        )
        self.assertTrue(result['verified'])
        self.assertIn('signature=sample', result['stdout'])
        self.assertIn('Temporary access with an S3 presigned URL.', result['stdout'])

    @patch('dashboard.labs.s3_labs._redeem_s3_presigned_url')
    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_presigned_lab_status_requires_successful_http_get(self, factory_mock, redeem_mock):
        s3 = MagicMock()
        object_body = MagicMock()
        object_body.read.return_value = b'Temporary access with an S3 presigned URL.\n'
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.get_object.return_value = {'Body': object_body}
        s3.generate_presigned_url.return_value = 'http://localhost:4566/signed'
        factory_mock.return_value.client.return_value = s3
        redeem_mock.return_value = b'Temporary access with an S3 presigned URL.\n'

        from .labs import lab_status

        status = lab_status('s3', 'presigned-url')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-bucket']['verified'])
        self.assertTrue(status['steps']['put-object']['verified'])
        self.assertTrue(status['steps']['presign-object']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_presigned_lab_reset_deletes_guide_and_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = [{
            'Contents': [{'Key': 'shared/guide.txt'}],
        }]
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'presigned-url')

        s3.delete_objects.assert_called_once_with(
            Bucket='lab-presigned',
            Delete={
                'Objects': [{'Key': 'shared/guide.txt'}],
                'Quiet': True,
            },
        )
        s3.delete_bucket.assert_called_once_with(Bucket='lab-presigned')
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_security_lab_enables_all_public_access_blocks(self, factory_mock):
        s3 = MagicMock()
        configuration = {
            'BlockPublicAcls': True,
            'IgnorePublicAcls': True,
            'BlockPublicPolicy': True,
            'RestrictPublicBuckets': True,
        }
        s3.put_public_access_block.return_value = {}
        s3.get_public_access_block.return_value = {
            'PublicAccessBlockConfiguration': configuration,
        }
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'bucket-security', 'put-public-access-block')

        s3.put_public_access_block.assert_called_once_with(
            Bucket='lab-security',
            PublicAccessBlockConfiguration=configuration,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_security_lab_applies_least_privilege_policy(self, factory_mock):
        s3 = MagicMock()
        policy = {
            'Version': '2012-10-17',
            'Statement': [{
                'Sid': 'AllowLocalAccountList',
                'Effect': 'Allow',
                'Principal': {'AWS': 'arn:aws:iam::000000000000:root'},
                'Action': 's3:ListBucket',
                'Resource': 'arn:aws:s3:::lab-security',
            }],
        }
        s3.put_bucket_policy.return_value = {}
        s3.get_bucket_policy.return_value = {'Policy': json.dumps(policy)}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'bucket-security', 'put-bucket-policy')

        s3.put_bucket_policy.assert_called_once()
        kwargs = s3.put_bucket_policy.call_args.kwargs
        self.assertEqual(kwargs['Bucket'], 'lab-security')
        self.assertEqual(json.loads(kwargs['Policy']), policy)
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_security_lab_status_tracks_blocks_and_policy_separately(self, factory_mock):
        s3 = MagicMock()
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.get_public_access_block.return_value = {
            'PublicAccessBlockConfiguration': {
                'BlockPublicAcls': True,
                'IgnorePublicAcls': True,
                'BlockPublicPolicy': True,
                'RestrictPublicBuckets': True,
            },
        }
        s3.get_bucket_policy.return_value = {
            'Policy': json.dumps({
                'Version': '2012-10-17',
                'Statement': [{
                    'Sid': 'AllowLocalAccountList',
                    'Effect': 'Allow',
                    'Principal': {'AWS': 'arn:aws:iam::000000000000:root'},
                    'Action': 's3:ListBucket',
                    'Resource': 'arn:aws:s3:::lab-security',
                }],
            }),
        }
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'bucket-security')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['put-public-access-block']['verified'])
        self.assertTrue(status['steps']['get-public-access-block']['verified'])
        self.assertTrue(status['steps']['put-bucket-policy']['verified'])
        self.assertTrue(status['steps']['get-bucket-policy']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_security_lab_reset_removes_configuration_then_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = []
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'bucket-security')

        s3.delete_bucket_policy.assert_called_once_with(Bucket='lab-security')
        s3.delete_public_access_block.assert_called_once_with(Bucket='lab-security')
        s3.delete_bucket.assert_called_once_with(Bucket='lab-security')
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_encryption_lab_configures_aes256_default(self, factory_mock):
        s3 = MagicMock()
        configuration = {
            'Rules': [{
                'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'},
                'BucketKeyEnabled': False,
            }],
        }
        s3.put_bucket_encryption.return_value = {}
        s3.get_bucket_encryption.return_value = {
            'ServerSideEncryptionConfiguration': configuration,
        }
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'default-encryption', 'put-bucket-encryption')

        s3.put_bucket_encryption.assert_called_once_with(
            Bucket='lab-encryption',
            ServerSideEncryptionConfiguration=configuration,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_encryption_lab_uploads_record_after_default_enabled(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Protected by the bucket encryption default.\n'
        s3.put_object.return_value = {'ETag': '"record"'}
        s3.get_bucket_encryption.return_value = {
            'ServerSideEncryptionConfiguration': {
                'Rules': [{
                    'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'},
                    'BucketKeyEnabled': False,
                }],
            },
        }
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 44,
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'default-encryption', 'put-object')

        s3.put_object.assert_called_once_with(
            Bucket='lab-encryption',
            Key='protected/record.txt',
            Body=b'Protected by the bucket encryption default.\n',
            ContentType='text/plain',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_encryption_lab_status_requires_config_and_object(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Protected by the bucket encryption default.\n'
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.get_bucket_encryption.return_value = {
            'ServerSideEncryptionConfiguration': {
                'Rules': [{
                    'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'},
                    'BucketKeyEnabled': False,
                }],
            },
        }
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 44,
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'default-encryption')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['put-bucket-encryption']['verified'])
        self.assertTrue(status['steps']['get-bucket-encryption']['verified'])
        self.assertTrue(status['steps']['put-object']['verified'])
        self.assertTrue(status['steps']['head-object']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_encryption_lab_reset_removes_encryption_then_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = [{
            'Contents': [{'Key': 'protected/record.txt'}],
        }]
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'default-encryption')

        s3.delete_bucket_encryption.assert_called_once_with(Bucket='lab-encryption')
        s3.delete_objects.assert_called_once()
        s3.delete_bucket.assert_called_once_with(Bucket='lab-encryption')
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_lifecycle_lab_uploads_log_under_target_prefix(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'2026-06-18T12:00:00Z INFO Floci lifecycle lab started\n'
        s3.put_object.return_value = {'ETag': '"log"'}
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 54,
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'lifecycle-retention', 'put-object')

        s3.put_object.assert_called_once_with(
            Bucket='lab-lifecycle',
            Key='logs/app.log',
            Body=b'2026-06-18T12:00:00Z INFO Floci lifecycle lab started\n',
            ContentType='text/plain',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_lifecycle_lab_tiers_then_expires_logs(self, factory_mock):
        s3 = MagicMock()
        configuration = {
            'Rules': [{
                'ID': 'TierThenExpireApplicationLogs',
                'Status': 'Enabled',
                'Filter': {'Prefix': 'logs/'},
                'Transitions': [
                    {'Days': 30, 'StorageClass': 'STANDARD_IA'},
                    {'Days': 90, 'StorageClass': 'GLACIER'},
                ],
                'Expiration': {'Days': 365},
            }],
        }
        s3.put_bucket_lifecycle_configuration.return_value = {}
        s3.get_bucket_lifecycle_configuration.return_value = configuration
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step(
            's3',
            'lifecycle-retention',
            'put-lifecycle-configuration',
        )

        s3.put_bucket_lifecycle_configuration.assert_called_once_with(
            Bucket='lab-lifecycle',
            LifecycleConfiguration=configuration,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_lifecycle_lab_status_requires_log_and_rule(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'2026-06-18T12:00:00Z INFO Floci lifecycle lab started\n'
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 54,
        }
        s3.get_object.return_value = {'Body': body}
        s3.get_bucket_lifecycle_configuration.return_value = {
            'Rules': [{
                'ID': 'TierThenExpireApplicationLogs',
                'Status': 'Enabled',
                'Filter': {'Prefix': 'logs/'},
                'Transitions': [
                    {'Days': 90, 'StorageClass': 'GLACIER'},
                    {'Days': 30, 'StorageClass': 'STANDARD_IA'},
                ],
                'Expiration': {'Days': 365},
            }],
        }
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'lifecycle-retention')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-bucket']['verified'])
        self.assertTrue(status['steps']['put-object']['verified'])
        self.assertTrue(status['steps']['put-lifecycle-configuration']['verified'])
        self.assertTrue(status['steps']['get-lifecycle-configuration']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_lifecycle_lab_rejects_expiration_only_rule(self, factory_mock):
        s3 = MagicMock()
        s3.get_bucket_lifecycle_configuration.return_value = {
            'Rules': [{
                'ID': 'TierThenExpireApplicationLogs',
                'Status': 'Enabled',
                'Filter': {'Prefix': 'logs/'},
                'Expiration': {'Days': 365},
            }],
        }
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'lifecycle-retention')

        self.assertFalse(status['steps']['put-lifecycle-configuration']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_lifecycle_lab_reset_removes_rule_then_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = [{
            'Contents': [{'Key': 'logs/app.log'}],
        }]
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'lifecycle-retention')

        s3.delete_bucket_lifecycle.assert_called_once_with(Bucket='lab-lifecycle')
        s3.delete_objects.assert_called_once()
        s3.delete_bucket.assert_called_once_with(Bucket='lab-lifecycle')
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_cors_lab_configures_local_web_app_read_access(self, factory_mock):
        s3 = MagicMock()
        configuration = {
            'CORSRules': [{
                'ID': 'AllowLocalWebAppRead',
                'AllowedOrigins': ['http://localhost:3000'],
                'AllowedMethods': ['GET', 'HEAD'],
                'AllowedHeaders': ['Authorization'],
                'ExposeHeaders': ['ETag'],
                'MaxAgeSeconds': 3600,
            }],
        }
        s3.put_bucket_cors.return_value = {}
        s3.get_bucket_cors.return_value = configuration
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'bucket-cors', 'put-bucket-cors')

        s3.put_bucket_cors.assert_called_once_with(
            Bucket='lab-cors',
            CORSConfiguration=configuration,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_cors_lab_get_returns_and_verifies_rule(self, factory_mock):
        s3 = MagicMock()
        configuration = {
            'CORSRules': [{
                'ID': 'AllowLocalWebAppRead',
                'AllowedOrigins': ['http://localhost:3000'],
                'AllowedMethods': ['GET', 'HEAD'],
                'AllowedHeaders': ['Authorization'],
                'ExposeHeaders': ['ETag'],
                'MaxAgeSeconds': 3600,
            }],
        }
        s3.get_bucket_cors.return_value = configuration
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'bucket-cors', 'get-bucket-cors')

        self.assertTrue(result['verified'])
        self.assertIn('http://localhost:3000', result['stdout'])
        self.assertIn('ETag', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_cors_lab_status_requires_bucket_and_exact_rule(self, factory_mock):
        s3 = MagicMock()
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.get_bucket_cors.return_value = {
            'CORSRules': [{
                'ID': 'AllowLocalWebAppRead',
                'AllowedOrigins': ['http://localhost:3000'],
                'AllowedMethods': ['GET', 'HEAD'],
                'AllowedHeaders': ['Authorization'],
                'ExposeHeaders': ['ETag'],
                'MaxAgeSeconds': 3600,
            }],
        }
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'bucket-cors')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-bucket']['verified'])
        self.assertTrue(status['steps']['put-bucket-cors']['verified'])
        self.assertTrue(status['steps']['get-bucket-cors']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_cors_lab_reset_removes_rule_then_bucket(self, factory_mock):
        s3 = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = []
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'bucket-cors')

        s3.delete_bucket_cors.assert_called_once_with(Bucket='lab-cors')
        s3.delete_bucket.assert_called_once_with(Bucket='lab-cors')
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_notifications_lab_sets_queue_policy(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-s3-events'
        policy = {
            'Version': '2012-10-17',
            'Statement': [{
                'Sid': 'AllowS3ObjectCreatedEvents',
                'Effect': 'Allow',
                'Principal': {'Service': 's3.amazonaws.com'},
                'Action': 'sqs:SendMessage',
                'Resource': 'arn:aws:sqs:us-east-1:000000000000:lab-s3-events',
                'Condition': {
                    'ArnEquals': {
                        'aws:SourceArn': 'arn:aws:s3:::lab-notifications',
                    },
                    'StringEquals': {'aws:SourceAccount': '000000000000'},
                },
            }],
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.set_queue_attributes.return_value = {}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {'Policy': json.dumps(policy)},
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            's3',
            'object-notifications-sqs',
            'set-queue-policy',
        )

        sqs.set_queue_attributes.assert_called_once_with(
            QueueUrl=queue_url,
            Attributes={'Policy': json.dumps(policy)},
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_notifications_lab_configures_queue_destination(self, factory_mock):
        s3 = MagicMock()
        configuration = {
            'QueueConfigurations': [{
                'Id': 'SendObjectCreatedToSqs',
                'QueueArn': 'arn:aws:sqs:us-east-1:000000000000:lab-s3-events',
                'Events': ['s3:ObjectCreated:*'],
            }],
        }
        s3.put_bucket_notification_configuration.return_value = {}
        s3.get_bucket_notification_configuration.return_value = configuration
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step(
            's3',
            'object-notifications-sqs',
            'put-notification-configuration',
        )

        s3.put_bucket_notification_configuration.assert_called_once_with(
            Bucket='lab-notifications',
            NotificationConfiguration=configuration,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_notifications_lab_uploads_report(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = b'Floci S3 notification delivery report\n'
        s3.put_object.return_value = {'ETag': '"report"'}
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 38,
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step(
            's3',
            'object-notifications-sqs',
            'put-object',
        )

        s3.put_object.assert_called_once_with(
            Bucket='lab-notifications',
            Key='uploads/report.txt',
            Body=b'Floci S3 notification delivery report\n',
            ContentType='text/plain',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_notifications_lab_receives_matching_s3_event(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-s3-events'
        event = {
            'Records': [{
                'eventSource': 'aws:s3',
                'eventName': 'ObjectCreated:Put',
                's3': {
                    'bucket': {'name': 'lab-notifications'},
                    'object': {'key': 'uploads/report.txt', 'size': 38},
                },
            }],
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {
            'Messages': [{
                'MessageId': 'message-1',
                'Body': json.dumps(event),
            }],
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            's3',
            'object-notifications-sqs',
            'receive-event',
        )

        sqs.receive_message.assert_called_once_with(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
        )
        self.assertTrue(result['verified'])
        self.assertIn('ObjectCreated:Put', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_notifications_lab_status_requires_delivered_event(self, factory_mock):
        s3 = MagicMock()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-s3-events'
        queue_arn = 'arn:aws:sqs:us-east-1:000000000000:lab-s3-events'
        policy = {
            'Version': '2012-10-17',
            'Statement': [{
                'Sid': 'AllowS3ObjectCreatedEvents',
                'Effect': 'Allow',
                'Principal': {'Service': 's3.amazonaws.com'},
                'Action': 'sqs:SendMessage',
                'Resource': queue_arn,
                'Condition': {
                    'ArnEquals': {
                        'aws:SourceArn': 'arn:aws:s3:::lab-notifications',
                    },
                    'StringEquals': {'aws:SourceAccount': '000000000000'},
                },
            }],
        }
        body = MagicMock()
        body.read.return_value = b'Floci S3 notification delivery report\n'
        event = {
            'Records': [{
                'eventSource': 'aws:s3',
                'eventName': 'ObjectCreated:Put',
                's3': {
                    'bucket': {'name': 'lab-notifications'},
                    'object': {'key': 'uploads/report.txt', 'size': 38},
                },
            }],
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.side_effect = [
            {'Attributes': {'QueueArn': queue_arn}},
            {'Attributes': {'Policy': json.dumps(policy)}},
        ]
        sqs.receive_message.return_value = {
            'Messages': [{'MessageId': 'message-1', 'Body': json.dumps(event)}],
        }
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.get_bucket_notification_configuration.return_value = {
            'QueueConfigurations': [{
                'Id': 'SendObjectCreatedToSqs',
                'QueueArn': queue_arn,
                'Events': ['s3:ObjectCreated:*'],
            }],
        }
        s3.head_object.return_value = {
            'ContentType': 'text/plain',
            'ContentLength': 38,
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.side_effect = (
            lambda service: sqs if service == 'sqs' else s3
        )

        from .labs import lab_status

        status = lab_status('s3', 'object-notifications-sqs')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['set-queue-policy']['verified'])
        self.assertTrue(status['steps']['put-notification-configuration']['verified'])
        self.assertTrue(status['steps']['receive-event']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_notifications_lab_reset_removes_bucket_then_queue(self, factory_mock):
        s3 = MagicMock()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-s3-events'
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = [{
            'Contents': [{'Key': 'uploads/report.txt'}],
        }]
        s3.get_paginator.side_effect = [version_paginator, object_paginator]
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        factory_mock.return_value.client.side_effect = (
            lambda service: sqs if service == 'sqs' else s3
        )

        result = reset_lab('s3', 'object-notifications-sqs')

        s3.put_bucket_notification_configuration.assert_called_once_with(
            Bucket='lab-notifications',
            NotificationConfiguration={},
        )
        s3.delete_bucket.assert_called_once_with(Bucket='lab-notifications')
        sqs.delete_queue.assert_called_once_with(QueueUrl=queue_url)
        self.assertTrue(result['deleted_bucket'])
        self.assertTrue(result['deleted_queue'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_multipart_lab_initiates_upload(self, factory_mock):
        s3 = MagicMock()
        active_upload = {
            'Key': 'archives/application.bin',
            'UploadId': 'upload-1',
        }
        s3.list_multipart_uploads.side_effect = [
            {'Uploads': []},
            {'Uploads': [active_upload]},
        ]
        s3.create_multipart_upload.return_value = {
            'Bucket': 'lab-multipart',
            'Key': 'archives/application.bin',
            'UploadId': 'upload-1',
        }
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step(
            's3',
            'multipart-upload',
            'create-multipart-upload',
        )

        s3.create_multipart_upload.assert_called_once_with(
            Bucket='lab-multipart',
            Key='archives/application.bin',
            ContentType='application/octet-stream',
        )
        self.assertTrue(result['verified'])
        self.assertIn('upload-1', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_multipart_lab_uploads_minimum_sized_first_part(self, factory_mock):
        s3 = MagicMock()
        active_upload = {
            'Key': 'archives/application.bin',
            'UploadId': 'upload-1',
        }
        s3.list_multipart_uploads.return_value = {'Uploads': [active_upload]}
        s3.upload_part.return_value = {'ETag': '"part-one"'}
        s3.list_parts.return_value = {
            'Parts': [{
                'PartNumber': 1,
                'ETag': '"part-one"',
                'Size': 5 * 1024 * 1024,
            }],
        }
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step(
            's3',
            'multipart-upload',
            'upload-part-one',
        )

        call = s3.upload_part.call_args.kwargs
        self.assertEqual(call['Bucket'], 'lab-multipart')
        self.assertEqual(call['Key'], 'archives/application.bin')
        self.assertEqual(call['PartNumber'], 1)
        self.assertEqual(call['UploadId'], 'upload-1')
        self.assertEqual(len(call['Body']), 5 * 1024 * 1024)
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_multipart_lab_lists_both_parts(self, factory_mock):
        s3 = MagicMock()
        active_upload = {
            'Key': 'archives/application.bin',
            'UploadId': 'upload-1',
        }
        parts = [
            {'PartNumber': 1, 'ETag': '"part-one"', 'Size': 5 * 1024 * 1024},
            {'PartNumber': 2, 'ETag': '"part-two"', 'Size': 1024},
        ]
        s3.list_multipart_uploads.return_value = {'Uploads': [active_upload]}
        s3.list_parts.return_value = {'Parts': parts}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step('s3', 'multipart-upload', 'list-parts')

        self.assertTrue(result['verified'])
        self.assertIn('"PartNumber": 1', result['stdout'])
        self.assertIn('"PartNumber": 2', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_multipart_lab_completes_with_discovered_etags(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = (
            b'A' * (5 * 1024 * 1024)
            + b'B' * 1024
        )
        active_upload = {
            'Key': 'archives/application.bin',
            'UploadId': 'upload-1',
        }
        parts = [
            {'PartNumber': 2, 'ETag': '"part-two"', 'Size': 1024},
            {'PartNumber': 1, 'ETag': '"part-one"', 'Size': 5 * 1024 * 1024},
        ]
        s3.list_multipart_uploads.return_value = {'Uploads': [active_upload]}
        s3.list_parts.return_value = {'Parts': parts}
        s3.complete_multipart_upload.return_value = {'ETag': '"complete-2"'}
        s3.head_object.return_value = {
            'ContentType': 'application/octet-stream',
            'ContentLength': 5 * 1024 * 1024 + 1024,
            'ETag': '"complete-2"',
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        result = run_lab_step(
            's3',
            'multipart-upload',
            'complete-multipart-upload',
        )

        s3.complete_multipart_upload.assert_called_once_with(
            Bucket='lab-multipart',
            Key='archives/application.bin',
            UploadId='upload-1',
            MultipartUpload={
                'Parts': [
                    {'ETag': '"part-one"', 'PartNumber': 1},
                    {'ETag': '"part-two"', 'PartNumber': 2},
                ],
            },
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_multipart_lab_status_recomputes_from_completed_object(self, factory_mock):
        s3 = MagicMock()
        body = MagicMock()
        body.read.return_value = (
            b'A' * (5 * 1024 * 1024)
            + b'B' * 1024
        )
        s3.head_bucket.return_value = {'BucketRegion': 'us-east-1'}
        s3.head_object.return_value = {
            'ContentType': 'application/octet-stream',
            'ContentLength': 5 * 1024 * 1024 + 1024,
            'ETag': '"complete-2"',
        }
        s3.get_object.return_value = {'Body': body}
        factory_mock.return_value.client.return_value = s3

        from .labs import lab_status

        status = lab_status('s3', 'multipart-upload')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-multipart-upload']['verified'])
        self.assertTrue(status['steps']['upload-part-one']['verified'])
        self.assertTrue(status['steps']['upload-part-two']['verified'])
        self.assertTrue(status['steps']['complete-multipart-upload']['verified'])
        self.assertTrue(status['steps']['get-object']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_s3_multipart_lab_reset_aborts_uploads_then_deletes_bucket(self, factory_mock):
        s3 = MagicMock()
        multipart_paginator = MagicMock()
        version_paginator = MagicMock()
        object_paginator = MagicMock()
        multipart_paginator.paginate.return_value = [{
            'Uploads': [{
                'Key': 'archives/application.bin',
                'UploadId': 'upload-1',
            }],
        }]
        version_paginator.paginate.return_value = []
        object_paginator.paginate.return_value = [{
            'Contents': [{'Key': 'archives/application.bin'}],
        }]
        s3.get_paginator.side_effect = [
            multipart_paginator,
            version_paginator,
            object_paginator,
        ]
        factory_mock.return_value.client.return_value = s3

        result = reset_lab('s3', 'multipart-upload')

        s3.abort_multipart_upload.assert_called_once_with(
            Bucket='lab-multipart',
            Key='archives/application.bin',
            UploadId='upload-1',
        )
        s3.delete_bucket.assert_called_once_with(Bucket='lab-multipart')
        self.assertEqual(result['aborted_uploads'], 1)
        self.assertTrue(result['deleted_bucket'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_create_queue_step_creates_and_verifies_queue(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.create_queue.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'create-queue', 'create-queue')

        sqs.create_queue.assert_called_once_with(QueueName='lab-basics')
        self.assertTrue(result['verified'])
        self.assertEqual(
            result['command'],
            'aws sqs create-queue --queue-name lab-basics',
        )

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_get_queue_attributes_step_verifies_expected_arn(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'QueueArn': 'arn:aws:sqs:us-east-1:000000000000:lab-basics',
                'VisibilityTimeout': '30',
            },
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'create-queue',
            'get-queue-attributes',
        )

        sqs.get_queue_attributes.assert_called_with(
            QueueUrl=queue_url,
            AttributeNames=['All'],
        )
        self.assertTrue(result['verified'])
        self.assertIn('VisibilityTimeout', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_list_queues_step_finds_lab_queue(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.list_queues.return_value = {'QueueUrls': [queue_url]}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'create-queue', 'list-queues')

        sqs.list_queues.assert_called()
        self.assertTrue(result['verified'])
        self.assertIn('lab-basics', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_create_queue_lab_status_recomputes_from_live_queue(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'QueueArn': 'arn:aws:sqs:us-east-1:000000000000:lab-basics',
            },
        }
        sqs.list_queues.return_value = {'QueueUrls': [queue_url]}
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'create-queue')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-queue']['verified'])
        self.assertTrue(status['steps']['get-queue-url']['verified'])
        self.assertTrue(status['steps']['get-queue-attributes']['verified'])
        self.assertTrue(status['steps']['list-queues']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_create_queue_lab_reset_deletes_queue(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'create-queue')

        sqs.delete_queue.assert_called_once_with(QueueUrl=queue_url)
        self.assertTrue(result['reset'])
        self.assertTrue(result['deleted'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_message_lifecycle_sends_known_body_and_attribute(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'MessageId': 'message-1',
            'ReceiptHandle': 'receipt-1',
            'Body': '{"event":"order.created","order_id":"FLOCI-1001"}',
            'MessageAttributes': {
                'Lab': {
                    'DataType': 'String',
                    'StringValue': 'message-lifecycle',
                },
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.send_message.return_value = {'MessageId': 'message-1'}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'message-lifecycle', 'send-message')

        sqs.send_message.assert_called_once_with(
            QueueUrl=queue_url,
            MessageBody='{"event":"order.created","order_id":"FLOCI-1001"}',
            MessageAttributes={
                'Lab': {
                    'DataType': 'String',
                    'StringValue': 'message-lifecycle',
                },
            },
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_message_lifecycle_receives_without_hiding_message(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'MessageId': 'message-1',
            'ReceiptHandle': 'receipt-1',
            'Body': '{"event":"order.created","order_id":"FLOCI-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'message-lifecycle'},
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'message-lifecycle', 'receive-message')

        sqs.receive_message.assert_called_once_with(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
            AttributeNames=['All'],
            MessageAttributeNames=['All'],
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_message_lifecycle_deletes_with_live_receipt_handle(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'MessageId': 'message-1',
            'ReceiptHandle': 'receipt-1',
            'Body': '{"event":"order.created","order_id":"FLOCI-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'message-lifecycle'},
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.side_effect = [
            {'Messages': [message]},
            {'Messages': []},
        ]
        sqs.delete_message.return_value = {}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'message-lifecycle', 'delete-message')

        sqs.delete_message.assert_called_once_with(
            QueueUrl=queue_url,
            ReceiptHandle='receipt-1',
        )
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:message-lifecycle:deleted'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_message_lifecycle_status_remembers_verified_deletion(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:sqs:message-lifecycle:deleted', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': []}
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'message-lifecycle')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['send-message']['verified'])
        self.assertTrue(status['steps']['receive-message']['verified'])
        self.assertTrue(status['steps']['delete-message']['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_message_lifecycle_reset_deletes_only_matching_message(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:sqs:message-lifecycle:deleted', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        matching = {
            'ReceiptHandle': 'receipt-1',
            'Body': '{"event":"order.created","order_id":"FLOCI-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'message-lifecycle'},
            },
        }
        unrelated = {
            'ReceiptHandle': 'receipt-2',
            'Body': 'leave me alone',
            'MessageAttributes': {},
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [matching, unrelated]}
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'message-lifecycle')

        sqs.delete_message.assert_called_once_with(
            QueueUrl=queue_url,
            ReceiptHandle='receipt-1',
        )
        sqs.delete_queue.assert_not_called()
        self.assertEqual(result['deleted_messages'], 1)
        self.assertIsNone(cache.get('floci-lab:sqs:message-lifecycle:deleted'))

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_visibility_lab_receives_and_captures_receipt(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'MessageId': 'job-1',
            'ReceiptHandle': 'visibility-receipt',
            'Body': '{"event":"job.ready","job_id":"FLOCI-JOB-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'visibility-timeout'},
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'visibility-timeout', 'receive-message')

        self.assertTrue(result['verified'])
        self.assertEqual(
            cache.get('floci-lab:sqs:visibility-timeout:receipt'),
            'visibility-receipt',
        )
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_visibility_lab_extends_timeout_with_cached_receipt(self, factory_mock):
        cache.clear()
        cache.set(
            'floci-lab:sqs:visibility-timeout:receipt',
            'visibility-receipt',
        )
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.change_message_visibility.return_value = {}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'visibility-timeout',
            'extend-message-visibility',
        )

        sqs.change_message_visibility.assert_called_once_with(
            QueueUrl=queue_url,
            ReceiptHandle='visibility-receipt',
            VisibilityTimeout=60,
        )
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_visibility_lab_shortens_timeout_after_hidden_check(self, factory_mock):
        cache.clear()
        cache.set(
            'floci-lab:sqs:visibility-timeout:receipt',
            'visibility-receipt',
        )
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'visibility-timeout',
            'shorten-message-visibility',
        )

        sqs.change_message_visibility.assert_called_once_with(
            QueueUrl=queue_url,
            ReceiptHandle='visibility-receipt',
            VisibilityTimeout=2,
        )
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_visibility_lab_verifies_message_is_hidden(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': []}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'visibility-timeout', 'verify-hidden')

        sqs.receive_message.assert_called_once_with(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=0,
            AttributeNames=['All'],
            MessageAttributeNames=['All'],
        )
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:visibility-timeout:hidden'))
        cache.clear()

    @patch('dashboard.labs.time.sleep')
    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_visibility_lab_receives_again_after_timeout(
        self,
        factory_mock,
        sleep_mock,
    ):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'MessageId': 'job-1',
            'ReceiptHandle': 'new-receipt',
            'Body': '{"event":"job.ready","job_id":"FLOCI-JOB-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'visibility-timeout'},
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'visibility-timeout',
            'receive-after-timeout',
        )

        sleep_mock.assert_called_once_with(2.25)
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:visibility-timeout:returned'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_visibility_lab_status_requires_hidden_and_returned_markers(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set('floci-lab:sqs:visibility-timeout:hidden', True)
        cache.set('floci-lab:sqs:visibility-timeout:returned', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'MessageId': 'job-1',
            'ReceiptHandle': 'new-receipt',
            'Body': '{"event":"job.ready","job_id":"FLOCI-JOB-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'visibility-timeout'},
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'visibility-timeout')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['verify-hidden']['verified'])
        self.assertTrue(status['steps']['receive-after-timeout']['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_visibility_lab_reset_reveals_and_deletes_matching_job(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set(
            'floci-lab:sqs:visibility-timeout:receipt',
            'old-receipt',
        )
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'ReceiptHandle': 'current-receipt',
            'Body': '{"event":"job.ready","job_id":"FLOCI-JOB-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'visibility-timeout'},
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'visibility-timeout')

        sqs.change_message_visibility.assert_called_once_with(
            QueueUrl=queue_url,
            ReceiptHandle='old-receipt',
            VisibilityTimeout=0,
        )
        sqs.delete_message.assert_called_once_with(
            QueueUrl=queue_url,
            ReceiptHandle='current-receipt',
        )
        sqs.delete_queue.assert_not_called()
        self.assertEqual(result['deleted_messages'], 1)
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_delayed_lab_sends_and_immediately_verifies_delay(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.send_message.return_value = {'MessageId': 'report-1'}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {'ApproximateNumberOfMessagesDelayed': '1'},
        }
        sqs.receive_message.return_value = {'Messages': []}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'delayed-message',
            'send-delayed-message',
        )

        sqs.send_message.assert_called_once_with(
            QueueUrl=queue_url,
            MessageBody='{"event":"report.generate","report_id":"FLOCI-REPORT-1001"}',
            MessageAttributes={
                'Lab': {
                    'DataType': 'String',
                    'StringValue': 'delayed-message',
                },
            },
            DelaySeconds=10,
        )
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:delayed-message:observed'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_delayed_lab_reads_delayed_message_count(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:sqs:delayed-message:observed', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {'ApproximateNumberOfMessagesDelayed': '1'},
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'delayed-message',
            'get-queue-attributes',
        )

        sqs.get_queue_attributes.assert_called_once_with(
            QueueUrl=queue_url,
            AttributeNames=['ApproximateNumberOfMessagesDelayed'],
        )
        self.assertTrue(result['verified'])
        self.assertIn('"ApproximateNumberOfMessagesDelayed": "1"', result['stdout'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_delayed_lab_receives_message_after_delay(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'MessageId': 'report-1',
            'ReceiptHandle': 'report-receipt',
            'Body': '{"event":"report.generate","report_id":"FLOCI-REPORT-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'delayed-message'},
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'delayed-message',
            'receive-after-delay',
        )

        sqs.receive_message.assert_called_once_with(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
            AttributeNames=['All'],
            MessageAttributeNames=['All'],
        )
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:delayed-message:returned'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_delayed_lab_status_requires_observed_and_returned_states(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set('floci-lab:sqs:delayed-message:observed', True)
        cache.set('floci-lab:sqs:delayed-message:returned', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        message = {
            'MessageId': 'report-1',
            'ReceiptHandle': 'report-receipt',
            'Body': '{"event":"report.generate","report_id":"FLOCI-REPORT-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'delayed-message'},
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'delayed-message')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['send-delayed-message']['verified'])
        self.assertTrue(status['steps']['get-queue-attributes']['verified'])
        self.assertTrue(status['steps']['receive-after-delay']['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_delayed_lab_reset_deletes_only_matching_message(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:sqs:delayed-message:observed', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        matching = {
            'ReceiptHandle': 'report-receipt',
            'Body': '{"event":"report.generate","report_id":"FLOCI-REPORT-1001"}',
            'MessageAttributes': {
                'Lab': {'DataType': 'String', 'StringValue': 'delayed-message'},
            },
        }
        unrelated = {
            'ReceiptHandle': 'other-receipt',
            'Body': 'unrelated',
            'MessageAttributes': {},
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': [matching, unrelated]}
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'delayed-message')

        sqs.delete_message.assert_called_once_with(
            QueueUrl=queue_url,
            ReceiptHandle='report-receipt',
        )
        sqs.delete_queue.assert_not_called()
        self.assertEqual(result['deleted_messages'], 1)
        self.assertIsNone(cache.get('floci-lab:sqs:delayed-message:observed'))

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_batch_lab_sends_three_entries(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        messages = [
            {
                'MessageId': f'message-{index}',
                'ReceiptHandle': f'receipt-{index}',
                'Body': f'{{"event":"task.created","task_id":"FLOCI-TASK-{index}"}}',
                'MessageAttributes': {
                    'Lab': {'DataType': 'String', 'StringValue': 'batch-messages'},
                },
            }
            for index in range(1, 4)
        ]
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.send_message_batch.return_value = {
            'Successful': [
                {'Id': f'task-{index}', 'MessageId': f'message-{index}'}
                for index in range(1, 4)
            ],
            'Failed': [],
        }
        sqs.receive_message.return_value = {'Messages': messages}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'batch-messages',
            'send-message-batch',
        )

        call = sqs.send_message_batch.call_args.kwargs
        self.assertEqual(call['QueueUrl'], queue_url)
        self.assertEqual(len(call['Entries']), 3)
        self.assertEqual(
            {entry['Id'] for entry in call['Entries']},
            {'task-1', 'task-2', 'task-3'},
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_batch_lab_receives_all_three_messages(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        messages = [
            {
                'MessageId': f'message-{index}',
                'ReceiptHandle': f'receipt-{index}',
                'Body': f'{{"event":"task.created","task_id":"FLOCI-TASK-{index}"}}',
                'MessageAttributes': {
                    'Lab': {'DataType': 'String', 'StringValue': 'batch-messages'},
                },
            }
            for index in range(1, 4)
        ]
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': messages}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'batch-messages', 'receive-message')

        self.assertTrue(result['verified'])
        self.assertEqual(len(result['json']['Messages']), 3)

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_batch_lab_deletes_with_live_receipt_handles(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        messages = [
            {
                'MessageId': f'message-{index}',
                'ReceiptHandle': f'receipt-{index}',
                'Body': f'{{"event":"task.created","task_id":"FLOCI-TASK-{index}"}}',
                'MessageAttributes': {
                    'Lab': {'DataType': 'String', 'StringValue': 'batch-messages'},
                },
            }
            for index in range(1, 4)
        ]
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        receive_calls = iter([{'Messages': messages}])
        sqs.receive_message.side_effect = lambda **_kwargs: next(
            receive_calls,
            {'Messages': []},
        )
        sqs.delete_message_batch.return_value = {
            'Successful': [{'Id': f'task-{index}'} for index in range(1, 4)],
            'Failed': [],
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'batch-messages',
            'delete-message-batch',
        )

        sqs.delete_message_batch.assert_called_once_with(
            QueueUrl=queue_url,
            Entries=[
                {'Id': 'task-1', 'ReceiptHandle': 'receipt-1'},
                {'Id': 'task-2', 'ReceiptHandle': 'receipt-2'},
                {'Id': 'task-3', 'ReceiptHandle': 'receipt-3'},
            ],
        )
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:batch-messages:deleted'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_batch_lab_status_remembers_verified_deletion(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:sqs:batch-messages:deleted', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': []}
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'batch-messages')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['send-message-batch']['verified'])
        self.assertTrue(status['steps']['receive-message']['verified'])
        self.assertTrue(status['steps']['delete-message-batch']['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_batch_lab_reset_deletes_only_matching_messages(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        messages = [
            {
                'ReceiptHandle': f'receipt-{index}',
                'Body': f'{{"event":"task.created","task_id":"FLOCI-TASK-{index}"}}',
                'MessageAttributes': {
                    'Lab': {'DataType': 'String', 'StringValue': 'batch-messages'},
                },
            }
            for index in range(1, 4)
        ]
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': messages}
        sqs.delete_message_batch.return_value = {
            'Successful': [{'Id': f'task-{index}'} for index in range(1, 4)],
        }
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'batch-messages')

        sqs.delete_message_batch.assert_called_once()
        sqs.delete_queue.assert_not_called()
        self.assertEqual(result['deleted_messages'], 3)

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_configuration_lab_sets_processing_attributes(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        configured = {
            'VisibilityTimeout': '45',
            'MessageRetentionPeriod': '86400',
            'ReceiveMessageWaitTimeSeconds': '10',
        }
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.return_value = {'Attributes': configured}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'queue-configuration',
            'set-queue-attributes',
        )

        sqs.set_queue_attributes.assert_called_once_with(
            QueueUrl=queue_url,
            Attributes=configured,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_configuration_lab_tags_queue(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        tags = {'Environment': 'lab', 'Purpose': 'training'}
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.list_queue_tags.return_value = {'Tags': tags}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'queue-configuration',
            'tag-queue',
        )

        sqs.tag_queue.assert_called_once_with(
            QueueUrl=queue_url,
            Tags=tags,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_configuration_lab_status_requires_attributes_and_tags(
        self,
        factory_mock,
    ):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'VisibilityTimeout': '45',
                'MessageRetentionPeriod': '86400',
                'ReceiveMessageWaitTimeSeconds': '10',
            },
        }
        sqs.list_queue_tags.return_value = {
            'Tags': {'Environment': 'lab', 'Purpose': 'training'},
        }
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'queue-configuration')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['set-queue-attributes']['verified'])
        self.assertTrue(status['steps']['get-queue-attributes']['verified'])
        self.assertTrue(status['steps']['tag-queue']['verified'])
        self.assertTrue(status['steps']['list-queue-tags']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_configuration_lab_reset_restores_defaults_and_removes_tags(
        self,
        factory_mock,
    ):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-basics'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'queue-configuration')

        sqs.set_queue_attributes.assert_called_once_with(
            QueueUrl=queue_url,
            Attributes={
                'VisibilityTimeout': '30',
                'MessageRetentionPeriod': '345600',
                'ReceiveMessageWaitTimeSeconds': '0',
            },
        )
        sqs.untag_queue.assert_called_once_with(
            QueueUrl=queue_url,
            TagKeys=['Environment', 'Purpose'],
        )
        sqs.delete_queue.assert_not_called()
        self.assertTrue(result['reset_attributes'])
        self.assertTrue(result['removed_tags'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_redrive_lab_configures_source_queue_policy(self, factory_mock):
        sqs = MagicMock()
        source_url = 'http://localhost:4566/000000000000/lab-redrive-source'
        policy = {
            'deadLetterTargetArn': (
                'arn:aws:sqs:us-east-1:000000000000:lab-redrive-dlq'
            ),
            'maxReceiveCount': '2',
        }
        sqs.get_queue_url.return_value = {'QueueUrl': source_url}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {'RedrivePolicy': json.dumps(policy)},
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'dead-letter-redrive',
            'set-redrive-policy',
        )

        sqs.set_queue_attributes.assert_called_once_with(
            QueueUrl=source_url,
            Attributes={'RedrivePolicy': json.dumps(policy)},
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_redrive_lab_records_two_failed_receives(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        source_url = 'http://localhost:4566/000000000000/lab-redrive-source'
        sqs.get_queue_url.return_value = {'QueueUrl': source_url}
        messages = [
            {
                'MessageId': 'payment-1',
                'ReceiptHandle': f'receipt-{receive_count}',
                'Body': (
                    '{"event":"payment.process","payment_id":'
                    '"FLOCI-PAYMENT-1001","simulate_failure":true}'
                ),
                'Attributes': {'ApproximateReceiveCount': str(receive_count)},
                'MessageAttributes': {
                    'Lab': {
                        'DataType': 'String',
                        'StringValue': 'dead-letter-redrive',
                    },
                },
            }
            for receive_count in (1, 2)
        ]
        sqs.receive_message.side_effect = [
            {'Messages': [messages[0]]},
            {'Messages': [messages[1]]},
        ]
        factory_mock.return_value.client.return_value = sqs

        first = run_lab_step(
            'sqs',
            'dead-letter-redrive',
            'fail-message-once',
        )
        second = run_lab_step(
            'sqs',
            'dead-letter-redrive',
            'fail-message-twice',
        )

        self.assertTrue(first['verified'])
        self.assertTrue(second['verified'])
        self.assertTrue(
            cache.get('floci-lab:sqs:dead-letter-redrive:failure-one')
        )
        self.assertTrue(
            cache.get('floci-lab:sqs:dead-letter-redrive:failure-two')
        )
        self.assertEqual(sqs.receive_message.call_count, 2)
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_redrive_lab_inspects_dlq_and_starts_move_task(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        dlq_url = 'http://localhost:4566/000000000000/lab-redrive-dlq'
        message = {
            'MessageId': 'payment-1',
            'ReceiptHandle': 'dlq-receipt',
            'Body': (
                '{"event":"payment.process","payment_id":'
                '"FLOCI-PAYMENT-1001","simulate_failure":true}'
            ),
            'Attributes': {'ApproximateReceiveCount': '4'},
            'MessageAttributes': {
                'Lab': {
                    'DataType': 'String',
                    'StringValue': 'dead-letter-redrive',
                },
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': dlq_url}
        sqs.receive_message.return_value = {'Messages': [message]}
        sqs.start_message_move_task.return_value = {'TaskHandle': 'task-1'}
        factory_mock.return_value.client.return_value = sqs

        inspect_result = run_lab_step(
            'sqs',
            'dead-letter-redrive',
            'inspect-dlq',
        )
        move_result = run_lab_step(
            'sqs',
            'dead-letter-redrive',
            'start-message-move-task',
        )

        self.assertTrue(inspect_result['verified'])
        self.assertTrue(move_result['verified'])
        sqs.start_message_move_task.assert_called_once_with(
            SourceArn='arn:aws:sqs:us-east-1:000000000000:lab-redrive-dlq',
            DestinationArn=(
                'arn:aws:sqs:us-east-1:000000000000:lab-redrive-source'
            ),
            MaxNumberOfMessagesPerSecond=10,
        )
        self.assertEqual(
            cache.get('floci-lab:sqs:dead-letter-redrive:task'),
            'task-1',
        )
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_redrive_lab_triggers_dead_letter_transition(self, factory_mock):
        cache.clear()
        cache.set(
            'floci-lab:sqs:dead-letter-redrive:failure-two',
            True,
        )
        sqs = MagicMock()
        source_url = 'http://localhost:4566/000000000000/lab-redrive-source'
        sqs.get_queue_url.return_value = {'QueueUrl': source_url}
        sqs.receive_message.return_value = {}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'dead-letter-redrive',
            'trigger-dead-lettering',
        )

        self.assertTrue(result['verified'])
        self.assertTrue(
            cache.get('floci-lab:sqs:dead-letter-redrive:triggered')
        )
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_redrive_lab_verifies_task_and_returned_message(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:sqs:dead-letter-redrive:task', 'task-1')
        sqs = MagicMock()
        source_url = 'http://localhost:4566/000000000000/lab-redrive-source'
        task = {
            'TaskHandle': 'task-1',
            'Status': 'COMPLETED',
            'SourceArn': (
                'arn:aws:sqs:us-east-1:000000000000:lab-redrive-dlq'
            ),
            'DestinationArn': (
                'arn:aws:sqs:us-east-1:000000000000:lab-redrive-source'
            ),
            'ApproximateNumberOfMessagesMoved': 1,
        }
        message = {
            'MessageId': 'payment-1',
            'ReceiptHandle': 'source-receipt',
            'Body': (
                '{"event":"payment.process","payment_id":'
                '"FLOCI-PAYMENT-1001","simulate_failure":true}'
            ),
            'Attributes': {'ApproximateReceiveCount': '1'},
            'MessageAttributes': {
                'Lab': {
                    'DataType': 'String',
                    'StringValue': 'dead-letter-redrive',
                },
            },
        }
        sqs.get_queue_url.return_value = {'QueueUrl': source_url}
        sqs.list_message_move_tasks.return_value = {'Results': [task]}
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.return_value = sqs

        task_result = run_lab_step(
            'sqs',
            'dead-letter-redrive',
            'list-message-move-tasks',
        )
        receive_result = run_lab_step(
            'sqs',
            'dead-letter-redrive',
            'receive-redriven-message',
        )

        self.assertTrue(task_result['verified'])
        self.assertTrue(receive_result['verified'])
        self.assertTrue(
            cache.get('floci-lab:sqs:dead-letter-redrive:returned')
        )
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_redrive_lab_status_recomputes_topology_and_task(
        self,
        factory_mock,
    ):
        cache.clear()
        for key in (
            'failure-one',
            'failure-two',
            'triggered',
            'dlq-observed',
            'returned',
        ):
            cache.set(
                f'floci-lab:sqs:dead-letter-redrive:{key}',
                True,
            )
        cache.set('floci-lab:sqs:dead-letter-redrive:task', 'task-1')
        sqs = MagicMock()
        source_url = 'http://localhost:4566/000000000000/lab-redrive-source'
        dlq_url = 'http://localhost:4566/000000000000/lab-redrive-dlq'
        urls = {
            'lab-redrive-source': source_url,
            'lab-redrive-dlq': dlq_url,
        }
        sqs.get_queue_url.side_effect = lambda QueueName: {
            'QueueUrl': urls[QueueName],
        }

        def get_attributes(QueueUrl, AttributeNames):
            if AttributeNames == ['QueueArn']:
                return {
                    'Attributes': {
                        'QueueArn': (
                            'arn:aws:sqs:us-east-1:000000000000:'
                            + QueueUrl.rsplit('/', 1)[-1]
                        ),
                    },
                }
            return {
                'Attributes': {
                    'RedrivePolicy': json.dumps({
                        'deadLetterTargetArn': (
                            'arn:aws:sqs:us-east-1:000000000000:'
                            'lab-redrive-dlq'
                        ),
                        'maxReceiveCount': '2',
                    }),
                },
            }

        sqs.get_queue_attributes.side_effect = get_attributes
        sqs.list_message_move_tasks.return_value = {
            'Results': [{
                'TaskHandle': 'task-1',
                'Status': 'COMPLETED',
                'DestinationArn': (
                    'arn:aws:sqs:us-east-1:000000000000:'
                    'lab-redrive-source'
                ),
                'ApproximateNumberOfMessagesMoved': 1,
            }],
        }
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'dead-letter-redrive')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['set-redrive-policy']['verified'])
        self.assertTrue(status['steps']['trigger-dead-lettering']['verified'])
        self.assertTrue(status['steps']['list-message-move-tasks']['verified'])
        self.assertTrue(status['steps']['receive-redriven-message']['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_redrive_lab_reset_cancels_task_and_deletes_both_queues(
        self,
        factory_mock,
    ):
        cache.clear()
        sqs = MagicMock()
        urls = {
            'lab-redrive-source': (
                'http://localhost:4566/000000000000/lab-redrive-source'
            ),
            'lab-redrive-dlq': (
                'http://localhost:4566/000000000000/lab-redrive-dlq'
            ),
        }
        sqs.get_queue_url.side_effect = lambda QueueName: {
            'QueueUrl': urls[QueueName],
        }
        sqs.list_message_move_tasks.return_value = {
            'Results': [{'TaskHandle': 'task-1', 'Status': 'RUNNING'}],
        }
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'dead-letter-redrive')

        sqs.cancel_message_move_task.assert_called_once_with(
            TaskHandle='task-1',
        )
        self.assertEqual(
            [call.kwargs['QueueUrl'] for call in sqs.delete_queue.call_args_list],
            [urls['lab-redrive-source'], urls['lab-redrive-dlq']],
        )
        self.assertEqual(result['cancelled_tasks'], 1)
        self.assertEqual(
            result['deleted_queues'],
            ['lab-redrive-source', 'lab-redrive-dlq'],
        )

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_fifo_lab_creates_explicit_deduplication_queue(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-orders.fifo'
        sqs.create_queue.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'QueueArn': (
                    'arn:aws:sqs:us-east-1:000000000000:lab-orders.fifo'
                ),
                'FifoQueue': 'true',
                'ContentBasedDeduplication': 'false',
            },
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'fifo-ordering', 'create-queue')

        sqs.create_queue.assert_called_once_with(
            QueueName='lab-orders.fifo',
            Attributes={
                'FifoQueue': 'true',
                'ContentBasedDeduplication': 'false',
            },
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_fifo_lab_suppresses_duplicate_deduplication_id(
        self,
        factory_mock,
    ):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-orders.fifo'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.send_message.side_effect = [
            {'MessageId': 'message-1', 'SequenceNumber': '41'},
            {'MessageId': 'message-1', 'SequenceNumber': '41'},
        ]
        factory_mock.return_value.client.return_value = sqs

        first = run_lab_step('sqs', 'fifo-ordering', 'send-created')
        duplicate = run_lab_step('sqs', 'fifo-ordering', 'send-duplicate')

        self.assertTrue(first['verified'])
        self.assertTrue(duplicate['verified'])
        self.assertEqual(sqs.send_message.call_count, 2)
        duplicate_call = sqs.send_message.call_args_list[1].kwargs
        self.assertEqual(
            duplicate_call['MessageDeduplicationId'],
            'FLOCI-ORDER-1001-1',
        )
        self.assertEqual(
            duplicate_call['MessageGroupId'],
            'customer-FLOCI-1001',
        )
        self.assertTrue(
            cache.get('floci-lab:sqs:fifo-ordering:deduplicated')
        )
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_fifo_lab_receives_three_messages_in_order(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:sqs:fifo-ordering:deduplicated', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-orders.fifo'
        bodies = [
            (
                '{"event":"order.status","order_id":"FLOCI-ORDER-1001",'
                f'"status":"{status}","step":{step}}}'
            )
            for step, status in enumerate(
                ('created', 'paid', 'fulfilled'),
                start=1,
            )
        ]
        messages = [
            {
                'MessageId': f'message-{step}',
                'ReceiptHandle': f'receipt-{step}',
                'Body': body,
                'Attributes': {
                    'MessageGroupId': 'customer-FLOCI-1001',
                    'MessageDeduplicationId': f'FLOCI-ORDER-1001-{step}',
                    'SequenceNumber': str(40 + step),
                },
            }
            for step, body in enumerate(bodies, start=1)
        ]
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {'Messages': messages}
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'fifo-ordering',
            'receive-ordered-messages',
        )

        self.assertTrue(result['verified'])
        self.assertEqual(
            result['verification']['resource']['sequence_numbers'],
            [41, 42, 43],
        )
        self.assertTrue(cache.get('floci-lab:sqs:fifo-ordering:ordered'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_fifo_lab_status_requires_deduplication_and_order(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set('floci-lab:sqs:fifo-ordering:deduplicated', True)
        cache.set('floci-lab:sqs:fifo-ordering:ordered', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-orders.fifo'
        bodies = [
            (
                '{"event":"order.status","order_id":"FLOCI-ORDER-1001",'
                f'"status":"{status}","step":{step}}}'
            )
            for step, status in enumerate(
                ('created', 'paid', 'fulfilled'),
                start=1,
            )
        ]
        messages = [
            {
                'Body': body,
                'Attributes': {
                    'MessageGroupId': 'customer-FLOCI-1001',
                    'MessageDeduplicationId': f'FLOCI-ORDER-1001-{step}',
                    'SequenceNumber': str(step),
                },
            }
            for step, body in enumerate(bodies, start=1)
        ]
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}

        def get_attributes(QueueUrl, AttributeNames):
            if AttributeNames == ['ApproximateNumberOfMessages']:
                return {'Attributes': {'ApproximateNumberOfMessages': '3'}}
            return {
                'Attributes': {
                    'QueueArn': (
                        'arn:aws:sqs:us-east-1:000000000000:'
                        'lab-orders.fifo'
                    ),
                    'FifoQueue': 'true',
                    'ContentBasedDeduplication': 'false',
                },
            }

        sqs.get_queue_attributes.side_effect = get_attributes
        sqs.receive_message.return_value = {'Messages': messages}
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'fifo-ordering')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['send-duplicate']['verified'])
        self.assertTrue(status['steps']['inspect-message-count']['verified'])
        self.assertTrue(status['steps']['receive-ordered-messages']['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_fifo_lab_reset_deletes_owned_queue(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:sqs:fifo-ordering:deduplicated', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-orders.fifo'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'fifo-ordering')

        sqs.delete_queue.assert_called_once_with(QueueUrl=queue_url)
        self.assertTrue(result['deleted_queue'])
        self.assertIsNone(
            cache.get('floci-lab:sqs:fifo-ordering:deduplicated')
        )

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_cleanup_lab_creates_configured_tagged_queue(self, factory_mock):
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-cleanup'
        sqs.create_queue.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'QueueArn': (
                    'arn:aws:sqs:us-east-1:000000000000:lab-cleanup'
                ),
                'VisibilityTimeout': '45',
                'ApproximateNumberOfMessages': '0',
            },
        }
        sqs.list_queue_tags.return_value = {
            'Tags': {'Purpose': 'cleanup-training'},
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'purge-delete', 'create-queue')

        sqs.create_queue.assert_called_once_with(
            QueueName='lab-cleanup',
            Attributes={'VisibilityTimeout': '45'},
            tags={'Purpose': 'cleanup-training'},
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_cleanup_lab_populates_three_messages(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-cleanup'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.send_message_batch.return_value = {
            'Successful': [
                {'Id': f'cleanup-{number}'}
                for number in range(1, 4)
            ],
            'Failed': [],
        }
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'QueueArn': (
                    'arn:aws:sqs:us-east-1:000000000000:lab-cleanup'
                ),
                'VisibilityTimeout': '45',
                'ApproximateNumberOfMessages': '3',
            },
        }
        sqs.list_queue_tags.return_value = {
            'Tags': {'Purpose': 'cleanup-training'},
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step(
            'sqs',
            'purge-delete',
            'send-message-batch',
        )

        entries = sqs.send_message_batch.call_args.kwargs['Entries']
        self.assertEqual(len(entries), 3)
        self.assertEqual(
            {entry['Id'] for entry in entries},
            {'cleanup-1', 'cleanup-2', 'cleanup-3'},
        )
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:purge-delete:populated'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_cleanup_lab_purges_messages_but_preserves_configuration(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set('floci-lab:sqs:purge-delete:populated', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-cleanup'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'QueueArn': (
                    'arn:aws:sqs:us-east-1:000000000000:lab-cleanup'
                ),
                'VisibilityTimeout': '45',
                'ApproximateNumberOfMessages': '0',
            },
        }
        sqs.list_queue_tags.return_value = {
            'Tags': {'Purpose': 'cleanup-training'},
        }
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'purge-delete', 'purge-queue')

        sqs.purge_queue.assert_called_once_with(QueueUrl=queue_url)
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:purge-delete:purged'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_cleanup_lab_deletes_queue_and_verifies_absence(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set('floci-lab:sqs:purge-delete:purged', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-cleanup'
        sqs.get_queue_url.side_effect = [
            {'QueueUrl': queue_url},
            ClientError(
                {
                    'Error': {
                        'Code': 'AWS.SimpleQueueService.NonExistentQueue',
                        'Message': 'missing',
                    },
                },
                'GetQueueUrl',
            ),
        ]
        factory_mock.return_value.client.return_value = sqs

        result = run_lab_step('sqs', 'purge-delete', 'delete-queue')

        sqs.delete_queue.assert_called_once_with(QueueUrl=queue_url)
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:sqs:purge-delete:deleted'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_cleanup_lab_status_completes_after_queue_is_absent(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set('floci-lab:sqs:purge-delete:populated', True)
        cache.set('floci-lab:sqs:purge-delete:purged', True)
        cache.set('floci-lab:sqs:purge-delete:deleted', True)
        sqs = MagicMock()
        sqs.get_queue_url.side_effect = ClientError(
            {
                'Error': {
                    'Code': 'AWS.SimpleQueueService.NonExistentQueue',
                    'Message': 'missing',
                },
            },
            'GetQueueUrl',
        )
        factory_mock.return_value.client.return_value = sqs

        from .labs import lab_status

        status = lab_status('sqs', 'purge-delete')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['purge-queue']['verified'])
        self.assertTrue(status['steps']['delete-queue']['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_sqs_cleanup_lab_reset_deletes_queue_and_clears_markers(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set('floci-lab:sqs:purge-delete:populated', True)
        sqs = MagicMock()
        queue_url = 'http://localhost:4566/000000000000/lab-cleanup'
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        factory_mock.return_value.client.return_value = sqs

        result = reset_lab('sqs', 'purge-delete')

        sqs.delete_queue.assert_called_once_with(QueueUrl=queue_url)
        self.assertTrue(result['deleted_queue'])
        self.assertIsNone(cache.get('floci-lab:sqs:purge-delete:populated'))

    @patch('dashboard.labs.FlociClientFactory')
    def test_sns_fanout_lab_creates_topic_and_queue_policy(self, factory_mock):
        sns = MagicMock()
        sqs = MagicMock()
        orders_url = (
            'http://localhost:4566/000000000000/lab-order-processing'
        )
        topic_arn = (
            'arn:aws:sns:us-east-1:000000000000:lab-order-events'
        )
        orders_arn = (
            'arn:aws:sqs:us-east-1:000000000000:lab-order-processing'
        )
        sns.create_topic.return_value = {'TopicArn': topic_arn}
        sns.get_topic_attributes.return_value = {
            'Attributes': {'TopicArn': topic_arn},
        }
        sqs.get_queue_url.return_value = {'QueueUrl': orders_url}
        expected_policy = {
            'Version': '2012-10-17',
            'Statement': [{
                'Sid': 'AllowOrderTopicDelivery',
                'Effect': 'Allow',
                'Principal': {'Service': 'sns.amazonaws.com'},
                'Action': 'sqs:SendMessage',
                'Resource': orders_arn,
                'Condition': {'ArnEquals': {'aws:SourceArn': topic_arn}},
            }],
        }
        sqs.get_queue_attributes.return_value = {
            'Attributes': {'Policy': json.dumps(expected_policy)},
        }
        factory_mock.return_value.client.side_effect = (
            lambda service: sns if service == 'sns' else sqs
        )

        topic_result = run_lab_step('sns', 'sqs-fanout', 'create-topic')
        policy_result = run_lab_step(
            'sns',
            'sqs-fanout',
            'set-orders-queue-policy',
        )

        self.assertTrue(topic_result['verified'])
        self.assertTrue(policy_result['verified'])
        sqs.set_queue_attributes.assert_called_once_with(
            QueueUrl=orders_url,
            Attributes={'Policy': json.dumps(expected_policy)},
        )

    @patch('dashboard.labs.FlociClientFactory')
    def test_sns_fanout_lab_subscribes_queue_with_raw_delivery(
        self,
        factory_mock,
    ):
        sns = MagicMock()
        sqs = MagicMock()
        topic_arn = (
            'arn:aws:sns:us-east-1:000000000000:lab-order-events'
        )
        orders_arn = (
            'arn:aws:sqs:us-east-1:000000000000:lab-order-processing'
        )
        subscription_arn = f'{topic_arn}:subscription-1'
        sns.subscribe.return_value = {'SubscriptionArn': subscription_arn}
        sns.list_subscriptions_by_topic.return_value = {
            'Subscriptions': [{
                'SubscriptionArn': subscription_arn,
                'Protocol': 'sqs',
                'Endpoint': orders_arn,
                'TopicArn': topic_arn,
            }],
        }
        sns.get_subscription_attributes.return_value = {
            'Attributes': {'RawMessageDelivery': 'true'},
        }
        factory_mock.return_value.client.side_effect = (
            lambda service: sns if service == 'sns' else sqs
        )

        result = run_lab_step(
            'sns',
            'sqs-fanout',
            'subscribe-orders-queue',
        )

        sns.subscribe.assert_called_once_with(
            TopicArn=topic_arn,
            Protocol='sqs',
            Endpoint=orders_arn,
            Attributes={'RawMessageDelivery': 'true'},
            ReturnSubscriptionArn=True,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sns_fanout_lab_publish_delivers_to_both_queues(
        self,
        factory_mock,
    ):
        sns = MagicMock()
        sqs = MagicMock()
        topic_arn = (
            'arn:aws:sns:us-east-1:000000000000:lab-order-events'
        )
        urls = {
            'lab-order-processing': (
                'http://localhost:4566/000000000000/lab-order-processing'
            ),
            'lab-order-audit': (
                'http://localhost:4566/000000000000/lab-order-audit'
            ),
        }
        message = {
            'MessageId': 'fanout-copy',
            'Body': '{"event":"order.created","order_id":"FLOCI-FANOUT-1001"}',
            'MessageAttributes': {
                'EventType': {
                    'DataType': 'String',
                    'StringValue': 'order.created',
                },
                'Environment': {
                    'DataType': 'String',
                    'StringValue': 'lab',
                },
            },
        }
        sns.publish.return_value = {'MessageId': 'sns-message'}
        sqs.get_queue_url.side_effect = lambda QueueName: {
            'QueueUrl': urls[QueueName],
        }
        sqs.receive_message.return_value = {'Messages': [message]}
        factory_mock.return_value.client.side_effect = (
            lambda service: sns if service == 'sns' else sqs
        )

        result = run_lab_step('sns', 'sqs-fanout', 'publish-message')

        sns.publish.assert_called_once_with(
            TopicArn=topic_arn,
            Message='{"event":"order.created","order_id":"FLOCI-FANOUT-1001"}',
            Subject='Order created',
            MessageAttributes={
                'EventType': {
                    'DataType': 'String',
                    'StringValue': 'order.created',
                },
                'Environment': {
                    'DataType': 'String',
                    'StringValue': 'lab',
                },
            },
        )
        self.assertEqual(sqs.receive_message.call_count, 2)
        self.assertTrue(result['verified'])

    def test_sns_fanout_lab_status_requires_both_subscriptions_and_deliveries(
        self,
    ):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_fanout_topic',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_fanout_queue',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_fanout_queue_policy',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_fanout_subscription',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_fanout_delivery',
                return_value=passed,
            ))
            from .labs import lab_status

            status = lab_status('sns', 'sqs-fanout')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['list-subscriptions']['verified'])
        self.assertTrue(status['steps']['publish-message']['verified'])
        self.assertTrue(status['steps']['receive-orders-copy']['verified'])
        self.assertTrue(status['steps']['receive-audit-copy']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sns_fanout_lab_reset_removes_subscriptions_topic_and_queues(
        self,
        factory_mock,
    ):
        sns = MagicMock()
        sqs = MagicMock()
        topic_arn = (
            'arn:aws:sns:us-east-1:000000000000:lab-order-events'
        )
        subscription_arns = [
            f'{topic_arn}:subscription-{number}'
            for number in (1, 2)
        ]
        sns.list_subscriptions_by_topic.return_value = {
            'Subscriptions': [
                {'SubscriptionArn': arn}
                for arn in subscription_arns
            ],
        }
        urls = {
            'lab-order-processing': (
                'http://localhost:4566/000000000000/lab-order-processing'
            ),
            'lab-order-audit': (
                'http://localhost:4566/000000000000/lab-order-audit'
            ),
        }
        sqs.get_queue_url.side_effect = lambda QueueName: {
            'QueueUrl': urls[QueueName],
        }
        factory_mock.return_value.client.side_effect = (
            lambda service: sns if service == 'sns' else sqs
        )

        result = reset_lab('sns', 'sqs-fanout')

        self.assertEqual(sns.unsubscribe.call_count, 2)
        sns.delete_topic.assert_called_once_with(TopicArn=topic_arn)
        self.assertEqual(sqs.delete_queue.call_count, 2)
        self.assertEqual(result['unsubscribed'], 2)
        self.assertTrue(result['deleted_topic'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sns_filter_lab_subscribes_with_attribute_filter(
        self,
        factory_mock,
    ):
        sns = MagicMock()
        sqs = MagicMock()
        topic_arn = (
            'arn:aws:sns:us-east-1:000000000000:lab-filtered-events'
        )
        queue_arn = (
            'arn:aws:sqs:us-east-1:000000000000:lab-created-events'
        )
        subscription_arn = f'{topic_arn}:subscription-1'
        sns.subscribe.return_value = {'SubscriptionArn': subscription_arn}
        sns.list_subscriptions_by_topic.return_value = {
            'Subscriptions': [{
                'SubscriptionArn': subscription_arn,
                'Protocol': 'sqs',
                'Endpoint': queue_arn,
            }],
        }
        sns.get_subscription_attributes.return_value = {
            'Attributes': {
                'RawMessageDelivery': 'true',
                'FilterPolicyScope': 'MessageAttributes',
                'FilterPolicy': json.dumps({
                    'EventType': ['order.created'],
                }),
            },
        }
        factory_mock.return_value.client.side_effect = (
            lambda service: sns if service == 'sns' else sqs
        )

        result = run_lab_step(
            'sns',
            'filter-policies',
            'subscribe-created-filter',
        )

        sns.subscribe.assert_called_once_with(
            TopicArn=topic_arn,
            Protocol='sqs',
            Endpoint=queue_arn,
            Attributes={
                'RawMessageDelivery': 'true',
                'FilterPolicyScope': 'MessageAttributes',
                'FilterPolicy': json.dumps({
                    'EventType': ['order.created'],
                }),
            },
            ReturnSubscriptionArn=True,
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sns_filter_lab_publishes_two_attributed_events(
        self,
        factory_mock,
    ):
        sns = MagicMock()
        sqs = MagicMock()
        sns.publish.side_effect = [
            {'MessageId': 'created-message'},
            {'MessageId': 'priority-message'},
        ]
        factory_mock.return_value.client.side_effect = (
            lambda service: sns if service == 'sns' else sqs
        )

        created = run_lab_step(
            'sns',
            'filter-policies',
            'publish-created-event',
        )
        priority = run_lab_step(
            'sns',
            'filter-policies',
            'publish-priority-event',
        )

        self.assertTrue(created['verified'])
        self.assertTrue(priority['verified'])
        first = sns.publish.call_args_list[0].kwargs
        second = sns.publish.call_args_list[1].kwargs
        self.assertEqual(
            first['MessageAttributes']['EventType']['StringValue'],
            'order.created',
        )
        self.assertEqual(
            second['MessageAttributes']['Priority']['StringValue'],
            'high',
        )

    @patch('dashboard.labs.FlociClientFactory')
    def test_sns_filter_lab_created_queue_excludes_priority_event(
        self,
        factory_mock,
    ):
        sns = MagicMock()
        sqs = MagicMock()
        queue_url = (
            'http://localhost:4566/000000000000/lab-created-events'
        )
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {
            'Messages': [{
                'Body': (
                    '{"event":"order.created",'
                    '"order_id":"FLOCI-FILTER-1001"}'
                ),
            }],
        }
        factory_mock.return_value.client.side_effect = (
            lambda service: sns if service == 'sns' else sqs
        )

        result = run_lab_step(
            'sns',
            'filter-policies',
            'receive-created-route',
        )

        self.assertTrue(result['verified'])
        self.assertEqual(len(result['json']['Messages']), 1)

    def test_sns_filter_lab_status_requires_both_exact_routes(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_filter_topic',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_filter_queue',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_filter_queue_policy',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_filter_subscription',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.sns_labs._verify_sns_filter_route',
                return_value=passed,
            ))
            from .labs import lab_status

            status = lab_status('sns', 'filter-policies')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['inspect-filter-policies']['verified'])
        self.assertTrue(status['steps']['receive-created-route']['verified'])
        self.assertTrue(status['steps']['receive-priority-route']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_sns_filter_lab_reset_removes_topic_subscriptions_and_queues(
        self,
        factory_mock,
    ):
        sns = MagicMock()
        sqs = MagicMock()
        topic_arn = (
            'arn:aws:sns:us-east-1:000000000000:lab-filtered-events'
        )
        sns.list_subscriptions_by_topic.return_value = {
            'Subscriptions': [
                {'SubscriptionArn': f'{topic_arn}:subscription-{number}'}
                for number in (1, 2)
            ],
        }
        urls = {
            'lab-created-events': (
                'http://localhost:4566/000000000000/lab-created-events'
            ),
            'lab-priority-events': (
                'http://localhost:4566/000000000000/lab-priority-events'
            ),
        }
        sqs.get_queue_url.side_effect = lambda QueueName: {
            'QueueUrl': urls[QueueName],
        }
        factory_mock.return_value.client.side_effect = (
            lambda service: sns if service == 'sns' else sqs
        )

        result = reset_lab('sns', 'filter-policies')

        self.assertEqual(sns.unsubscribe.call_count, 2)
        sns.delete_topic.assert_called_once_with(TopicArn=topic_arn)
        self.assertEqual(sqs.delete_queue.call_count, 2)
        self.assertEqual(result['unsubscribed'], 2)

    @patch('dashboard.labs.scheduler_labs._verify_scheduler_role')
    @patch('dashboard.labs.FlociClientFactory')
    def test_scheduler_lab_creates_trusted_scoped_role(
        self,
        factory_mock,
        verify_role_mock,
    ):
        iam = MagicMock()
        sqs = MagicMock()
        scheduler = MagicMock()
        verify_role_mock.return_value = {'status': 'passed', 'message': 'verified'}
        factory_mock.return_value.client.side_effect = (
            lambda service: (
                iam if service == 'iam'
                else sqs if service == 'sqs'
                else scheduler
            )
        )

        role_result = run_lab_step('scheduler', 'sqs-delivery', 'create-role')

        iam.create_role.assert_called_once()
        role_call = iam.create_role.call_args.kwargs
        self.assertEqual(role_call['RoleName'], 'FlociSchedulerSqsRole')
        self.assertIn(
            'scheduler.amazonaws.com',
            role_call['AssumeRolePolicyDocument'],
        )
        self.assertTrue(role_result['verified'])

    @patch('dashboard.labs.scheduler_labs._verify_scheduler_role_policy')
    @patch('dashboard.labs.FlociClientFactory')
    def test_scheduler_lab_adds_sqs_send_permission(
        self,
        factory_mock,
        verify_policy_mock,
    ):
        iam = MagicMock()
        verify_policy_mock.return_value = {
            'status': 'passed',
            'message': 'verified',
        }
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step(
            'scheduler',
            'sqs-delivery',
            'put-role-policy',
        )

        call = iam.put_role_policy.call_args.kwargs
        self.assertEqual(call['RoleName'], 'FlociSchedulerSqsRole')
        self.assertEqual(call['PolicyName'], 'SendScheduledReportToSqs')
        self.assertIn('sqs:SendMessage', call['PolicyDocument'])
        self.assertIn('lab-scheduled-reports', call['PolicyDocument'])
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.scheduler_labs._verify_scheduler_schedule')
    @patch('dashboard.labs.FlociClientFactory')
    def test_scheduler_lab_creates_one_time_auto_delete_schedule(
        self,
        factory_mock,
        verify_schedule_mock,
    ):
        cache.clear()
        scheduler = MagicMock()
        scheduler.create_schedule.return_value = {
            'ScheduleArn': (
                'arn:aws:scheduler:us-east-1:000000000000:schedule/'
                'lab-scheduler/send-report-ready'
            ),
        }
        verify_schedule_mock.return_value = {
            'status': 'passed',
            'message': 'verified',
        }
        factory_mock.return_value.client.return_value = scheduler

        result = run_lab_step(
            'scheduler',
            'sqs-delivery',
            'create-schedule',
        )

        call = scheduler.create_schedule.call_args.kwargs
        self.assertTrue(call['ScheduleExpression'].startswith('at('))
        self.assertEqual(call['FlexibleTimeWindow'], {'Mode': 'OFF'})
        self.assertEqual(call['ActionAfterCompletion'], 'DELETE')
        self.assertEqual(
            call['Target']['Arn'],
            'arn:aws:sqs:us-east-1:000000000000:lab-scheduled-reports',
        )
        self.assertEqual(
            call['Target']['RoleArn'],
            'arn:aws:iam::000000000000:role/FlociSchedulerSqsRole',
        )
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:scheduler:sqs-delivery:created'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_scheduler_lab_receives_scheduled_message(self, factory_mock):
        cache.clear()
        sqs = MagicMock()
        scheduler = MagicMock()
        iam = MagicMock()
        queue_url = (
            'http://localhost:4566/000000000000/'
            'lab-scheduled-reports'
        )
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        sqs.receive_message.return_value = {
            'Messages': [{
                'MessageId': 'scheduled-message',
                'Body': (
                    '{"event":"report.ready",'
                    '"report_id":"FLOCI-SCHEDULE-1001"}'
                ),
            }],
        }
        factory_mock.return_value.client.side_effect = (
            lambda service: (
                iam if service == 'iam'
                else sqs if service == 'sqs'
                else scheduler
            )
        )

        result = run_lab_step(
            'scheduler',
            'sqs-delivery',
            'receive-scheduled-message',
        )

        self.assertTrue(result['verified'])
        self.assertTrue(
            cache.get('floci-lab:scheduler:sqs-delivery:delivered')
        )
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_scheduler_lab_confirms_automatic_schedule_deletion(
        self,
        factory_mock,
    ):
        cache.clear()
        scheduler = MagicMock()
        scheduler.get_schedule.side_effect = ClientError(
            {
                'Error': {
                    'Code': 'ResourceNotFoundException',
                    'Message': 'missing',
                },
            },
            'GetSchedule',
        )
        factory_mock.return_value.client.return_value = scheduler

        result = run_lab_step(
            'scheduler',
            'sqs-delivery',
            'confirm-schedule-deleted',
        )

        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:scheduler:sqs-delivery:deleted'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_scheduler_lab_reset_removes_dependencies_in_order(
        self,
        factory_mock,
    ):
        cache.clear()
        iam = MagicMock()
        sqs = MagicMock()
        scheduler = MagicMock()
        queue_url = (
            'http://localhost:4566/000000000000/'
            'lab-scheduled-reports'
        )
        sqs.get_queue_url.return_value = {'QueueUrl': queue_url}
        factory_mock.return_value.client.side_effect = (
            lambda service: (
                iam if service == 'iam'
                else sqs if service == 'sqs'
                else scheduler
            )
        )

        result = reset_lab('scheduler', 'sqs-delivery')

        scheduler.delete_schedule.assert_called_once()
        scheduler.delete_schedule_group.assert_called_once()
        iam.delete_role_policy.assert_called_once()
        iam.delete_role.assert_called_once()
        sqs.delete_queue.assert_called_once_with(QueueUrl=queue_url)
        self.assertTrue(result['deleted_schedule'])
        self.assertTrue(result['deleted_group'])
        self.assertTrue(result['deleted_role'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_cloudformation_lab_validates_template(self, factory_mock):
        cloudformation = MagicMock()
        cloudformation.validate_template.return_value = {
            'Parameters': [],
            'Capabilities': [],
        }
        factory_mock.return_value.client.return_value = cloudformation

        result = run_lab_step(
            'cloudformation',
            's3-sqs-stack',
            'validate-template',
        )

        call = cloudformation.validate_template.call_args.kwargs
        template = json.loads(call['TemplateBody'])
        self.assertEqual(
            template['Resources']['StorageBucket']['Type'],
            'AWS::S3::Bucket',
        )
        self.assertEqual(
            template['Resources']['JobsQueue']['Type'],
            'AWS::SQS::Queue',
        )
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.cloudformation_labs._verify_cloudformation_stack')
    @patch('dashboard.labs.cloudformation_labs._wait_for_cloudformation_status')
    @patch('dashboard.labs.FlociClientFactory')
    def test_cloudformation_lab_creates_stack_and_waits_for_completion(
        self,
        factory_mock,
        wait_mock,
        verify_mock,
    ):
        cache.clear()
        cloudformation = MagicMock()
        cloudformation.create_stack.return_value = {'StackId': 'stack-id'}
        wait_mock.return_value = {
            'StackName': 'lab-storage-messaging',
            'StackStatus': 'CREATE_COMPLETE',
        }
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}
        factory_mock.return_value.client.return_value = cloudformation

        result = run_lab_step(
            'cloudformation',
            's3-sqs-stack',
            'create-stack',
        )

        cloudformation.create_stack.assert_called_once()
        call = cloudformation.create_stack.call_args.kwargs
        self.assertEqual(call['StackName'], 'lab-storage-messaging')
        self.assertIn('lab-cfn-storage', call['TemplateBody'])
        self.assertTrue(result['verified'])
        self.assertTrue(
            cache.get('floci-lab:cloudformation:s3-sqs:created')
        )
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_cloudformation_lab_maps_logical_to_physical_resources(
        self,
        factory_mock,
    ):
        cloudformation = MagicMock()
        resources = [
            {
                'LogicalResourceId': 'StorageBucket',
                'PhysicalResourceId': 'lab-cfn-storage',
                'ResourceType': 'AWS::S3::Bucket',
                'ResourceStatus': 'CREATE_COMPLETE',
            },
            {
                'LogicalResourceId': 'JobsQueue',
                'PhysicalResourceId': (
                    'http://localhost:4566/000000000000/lab-cfn-jobs'
                ),
                'ResourceType': 'AWS::SQS::Queue',
                'ResourceStatus': 'CREATE_COMPLETE',
            },
        ]
        cloudformation.describe_stack_resources.return_value = {
            'StackResources': resources,
        }
        factory_mock.return_value.client.return_value = cloudformation

        result = run_lab_step(
            'cloudformation',
            's3-sqs-stack',
            'describe-stack-resources',
        )

        self.assertTrue(result['verified'])
        self.assertEqual(len(result['json']['StackResources']), 2)

    @patch('dashboard.labs.cloudformation_labs._verify_cloudformation_deleted')
    @patch('dashboard.labs.FlociClientFactory')
    def test_cloudformation_lab_deletes_stack_after_inspection(
        self,
        factory_mock,
        verify_deleted_mock,
    ):
        cache.clear()
        cache.set('floci-lab:cloudformation:s3-sqs:inspected', True)
        cache.set('floci-lab:cloudformation:s3-sqs:change-set-executed', True)
        cloudformation = MagicMock()
        cloudformation.describe_stacks.side_effect = ClientError(
            {
                'Error': {
                    'Code': 'ValidationError',
                    'Message': 'missing',
                },
            },
            'DescribeStacks',
        )
        verify_deleted_mock.return_value = {
            'status': 'passed',
            'message': 'deleted',
        }
        factory_mock.return_value.client.return_value = cloudformation

        result = run_lab_step(
            'cloudformation',
            's3-sqs-stack',
            'delete-stack',
        )

        cloudformation.delete_stack.assert_called_once_with(
            StackName='lab-storage-messaging',
        )
        self.assertTrue(result['verified'])
        self.assertTrue(
            cache.get('floci-lab:cloudformation:s3-sqs:deleted')
        )
        cache.clear()

    def test_cloudformation_lab_status_completes_after_owned_resources_deleted(
        self,
    ):
        cache.clear()
        cache.set('floci-lab:cloudformation:s3-sqs:created', True)
        cache.set('floci-lab:cloudformation:s3-sqs:inspected', True)
        cache.set('floci-lab:cloudformation:s3-sqs:change-set-executed', True)
        with patch(
            'dashboard.labs.cloudformation_labs._verify_cloudformation_deleted',
            return_value={'status': 'passed', 'message': 'deleted'},
        ):
            from .labs import lab_status

            status = lab_status('cloudformation', 's3-sqs-stack')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['describe-stack-events']['verified'])
        self.assertTrue(status['steps']['execute-change-set']['verified'])
        self.assertTrue(status['steps']['confirm-resources-deleted']['verified'])
        cache.clear()

    def test_cloudformation_lab_is_incomplete_without_the_change_set(self):
        cache.clear()
        cache.set('floci-lab:cloudformation:s3-sqs:created', True)
        cache.set('floci-lab:cloudformation:s3-sqs:inspected', True)
        with patch(
            'dashboard.labs.cloudformation_labs._verify_cloudformation_deleted',
            return_value={'status': 'passed', 'message': 'deleted'},
        ):
            from .labs import lab_status

            status = lab_status('cloudformation', 's3-sqs-stack')

        self.assertFalse(status['complete'])
        self.assertFalse(status['steps']['execute-change-set']['verified'])
        cache.clear()

    def test_cloudformation_lab_refuses_delete_before_change_set(self):
        cache.clear()
        cache.set('floci-lab:cloudformation:s3-sqs:inspected', True)
        with self.assertRaisesRegex(ValueError, 'change set'):
            run_lab_step('cloudformation', 's3-sqs-stack', 'delete-stack')
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_cloudformation_change_set_review_requires_add_and_in_place_modify(self, factory_mock):
        cloudformation = MagicMock()
        factory_mock.return_value.client.return_value = cloudformation

        def change_set(replacement):
            return {
                'Status': 'CREATE_COMPLETE',
                'ExecutionStatus': 'AVAILABLE',
                'Changes': [
                    {'ResourceChange': {'Action': 'Add', 'LogicalResourceId': 'JobsDeadLetterQueue'}},
                    {'ResourceChange': {
                        'Action': 'Modify',
                        'LogicalResourceId': 'JobsQueue',
                        'Replacement': replacement,
                    }},
                ],
            }

        cloudformation.describe_change_set.return_value = change_set('False')
        result = run_lab_step('cloudformation', 's3-sqs-stack', 'describe-change-set')
        self.assertTrue(result['verified'])

        cloudformation.describe_change_set.return_value = change_set('True')
        result = run_lab_step('cloudformation', 's3-sqs-stack', 'describe-change-set')
        self.assertFalse(result['verified'])

    @patch('dashboard.labs.ec2_labs._verify_ec2_vpc')
    @patch('dashboard.labs.FlociClientFactory')
    def test_ec2_network_lab_creates_tagged_vpc(
        self,
        factory_mock,
        verify_vpc_mock,
    ):
        cache.clear()
        ec2 = MagicMock()
        ec2.create_vpc.return_value = {
            'Vpc': {
                'VpcId': 'vpc-lab',
                'CidrBlock': '10.42.0.0/16',
            },
        }
        verify_vpc_mock.return_value = {'status': 'passed', 'message': 'verified'}
        factory_mock.return_value.client.return_value = ec2

        result = run_lab_step('ec2', 'vpc-public-private', 'create-vpc')

        call = ec2.create_vpc.call_args.kwargs
        self.assertEqual(call['CidrBlock'], '10.42.0.0/16')
        self.assertEqual(
            call['TagSpecifications'][0]['Tags'][1],
            {'Key': 'Lab', 'Value': 'vpc-public-private'},
        )
        self.assertTrue(result['verified'])
        self.assertEqual(
            cache.get('floci-lab:ec2:vpc-networking:vpc-id'),
            'vpc-lab',
        )
        cache.clear()

    @patch('dashboard.labs.ec2_labs._verify_ec2_routes')
    @patch('dashboard.labs.FlociClientFactory')
    def test_ec2_network_lab_creates_public_default_route(
        self,
        factory_mock,
        verify_routes_mock,
    ):
        cache.clear()
        cache.set('floci-lab:ec2:vpc-networking:public-rt-id', 'rtb-public')
        cache.set('floci-lab:ec2:vpc-networking:igw-id', 'igw-lab')
        ec2 = MagicMock()
        ec2.create_route.return_value = {'Return': True}
        verify_routes_mock.return_value = (
            {'status': 'passed', 'message': 'public'},
            {'status': 'failed', 'message': 'private'},
        )
        factory_mock.return_value.client.return_value = ec2

        result = run_lab_step(
            'ec2',
            'vpc-public-private',
            'create-internet-route',
        )

        ec2.create_route.assert_called_once_with(
            RouteTableId='rtb-public',
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId='igw-lab',
        )
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.ec2_labs._verify_ec2_routes')
    @patch('dashboard.labs.FlociClientFactory')
    def test_ec2_network_lab_associates_private_route_table(
        self,
        factory_mock,
        verify_routes_mock,
    ):
        cache.clear()
        cache.set('floci-lab:ec2:vpc-networking:private-rt-id', 'rtb-private')
        cache.set(
            'floci-lab:ec2:vpc-networking:private-subnet-id',
            'subnet-private',
        )
        ec2 = MagicMock()
        ec2.associate_route_table.return_value = {
            'AssociationId': 'rtbassoc-private',
        }
        verify_routes_mock.return_value = (
            {'status': 'failed', 'message': 'public'},
            {'status': 'passed', 'message': 'private'},
        )
        factory_mock.return_value.client.return_value = ec2

        result = run_lab_step(
            'ec2',
            'vpc-public-private',
            'associate-private-route-table',
        )

        ec2.associate_route_table.assert_called_once_with(
            RouteTableId='rtb-private',
            SubnetId='subnet-private',
        )
        self.assertTrue(result['verified'])
        cache.clear()

    def test_ec2_network_lab_status_requires_public_and_private_routes(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_vpc',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_subnets',
                return_value=(passed, passed),
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_igw',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_routes',
                return_value=(passed, passed),
            ))
            from .labs import lab_status

            status = lab_status('ec2', 'vpc-public-private')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-internet-route']['verified'])
        self.assertTrue(status['steps']['associate-private-route-table']['verified'])
        self.assertTrue(status['steps']['inspect-network-topology']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_ec2_security_lab_allows_trusted_https(self, factory_mock):
        cache.clear()
        cache.set('floci-lab:ec2:security-controls:web-sg-id', 'sg-web')
        ec2 = MagicMock()
        ec2.authorize_security_group_ingress.return_value = {'Return': True}
        factory_mock.return_value.client.return_value = ec2
        with patch(
            'dashboard.labs.ec2_labs._verify_ec2_security_group_rules',
            return_value=(
                {'status': 'passed', 'message': 'web'},
                {'status': 'failed', 'message': 'app'},
            ),
        ):
            result = run_lab_step(
                'ec2',
                'security-controls',
                'allow-trusted-https',
            )

        call = ec2.authorize_security_group_ingress.call_args.kwargs
        self.assertEqual(call['GroupId'], 'sg-web')
        permission = call['IpPermissions'][0]
        self.assertEqual(permission['FromPort'], 443)
        self.assertEqual(
            permission['IpRanges'][0]['CidrIp'],
            '203.0.113.0/24',
        )
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_ec2_security_lab_references_web_group_from_app_group(
        self,
        factory_mock,
    ):
        cache.clear()
        cache.set('floci-lab:ec2:security-controls:web-sg-id', 'sg-web')
        cache.set('floci-lab:ec2:security-controls:app-sg-id', 'sg-app')
        ec2 = MagicMock()
        ec2.authorize_security_group_ingress.return_value = {'Return': True}
        factory_mock.return_value.client.return_value = ec2
        with patch(
            'dashboard.labs.ec2_labs._verify_ec2_security_group_rules',
            return_value=(
                {'status': 'passed', 'message': 'web'},
                {'status': 'passed', 'message': 'app'},
            ),
        ):
            result = run_lab_step(
                'ec2',
                'security-controls',
                'allow-web-to-app',
            )

        call = ec2.authorize_security_group_ingress.call_args.kwargs
        self.assertEqual(call['GroupId'], 'sg-app')
        permission = call['IpPermissions'][0]
        self.assertEqual(permission['FromPort'], 8080)
        self.assertEqual(
            permission['UserIdGroupPairs'][0]['GroupId'],
            'sg-web',
        )
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.ec2_labs._verify_ec2_network_acl_association')
    @patch('dashboard.labs.ec2_labs._verify_ec2_network_acl_rules')
    @patch('dashboard.labs.FlociClientFactory')
    def test_ec2_security_lab_inspects_network_acl_rules(
        self,
        factory_mock,
        verify_rules_mock,
        verify_association_mock,
    ):
        cache.clear()
        cache.set('floci-lab:ec2:security-controls:vpc-id', 'vpc-security')
        verify_rules_mock.return_value = {'status': 'passed', 'message': 'rules verified'}
        verify_association_mock.return_value = {
            'status': 'passed',
            'message': 'association verified',
        }
        ec2 = MagicMock()
        ec2.describe_network_acls.return_value = {
            'NetworkAcls': [{'NetworkAclId': 'acl-custom'}],
        }
        factory_mock.return_value.client.return_value = ec2

        result = run_lab_step(
            'ec2',
            'security-controls',
            'inspect-network-acl-support',
        )

        self.assertTrue(result['verified'])
        self.assertEqual(
            result['verification']['message'],
            'The custom network ACL is associated with the subnet and contains the intended stateless rules.',
        )
        ec2.describe_network_acls.assert_called_once()
        cache.clear()

    def test_ec2_security_lab_status_requires_groups_and_network_acl(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_security_vpc',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_security_subnet',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_security_group_rules',
                return_value=(passed, passed),
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_network_acl_created',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_network_acl_rule',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_network_acl_rules',
                return_value=passed,
            ))
            stack.enter_context(patch(
                'dashboard.labs.ec2_labs._verify_ec2_network_acl_association',
                return_value=passed,
            ))
            from .labs import lab_status

            status = lab_status('ec2', 'security-controls')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['allow-trusted-https']['verified'])
        self.assertTrue(status['steps']['allow-web-to-app']['verified'])
        self.assertTrue(status['steps']['create-network-acl']['verified'])
        self.assertTrue(status['steps']['associate-network-acl']['verified'])
        self.assertTrue(status['steps']['inspect-network-acl-support']['verified'])

    @patch('dashboard.labs.ec2_labs._verify_ec2_s3_gateway_endpoint')
    @patch('dashboard.labs.FlociClientFactory')
    def test_ec2_endpoint_lab_creates_bucket_scoped_s3_gateway_endpoint(
        self,
        factory_mock,
        verify_endpoint_mock,
    ):
        cache.clear()
        cache.set('floci-lab:ec2:s3-endpoint:vpc-id', 'vpc-private')
        cache.set('floci-lab:ec2:s3-endpoint:route-table-id', 'rtb-private')
        ec2 = MagicMock()
        ec2.meta.region_name = 'us-east-1'
        ec2.create_vpc_endpoint.return_value = {
            'VpcEndpoint': {'VpcEndpointId': 'vpce-s3'},
        }
        factory_mock.return_value.client.return_value = ec2
        verify_endpoint_mock.return_value = {
            'status': 'passed',
            'message': 'verified',
        }

        result = run_lab_step(
            'ec2',
            's3-gateway-endpoint',
            'create-s3-gateway-endpoint',
        )

        call = ec2.create_vpc_endpoint.call_args.kwargs
        self.assertEqual(call['VpcId'], 'vpc-private')
        self.assertEqual(call['VpcEndpointType'], 'Gateway')
        self.assertEqual(call['ServiceName'], 'com.amazonaws.us-east-1.s3')
        self.assertEqual(call['RouteTableIds'], ['rtb-private'])
        policy = json.loads(call['PolicyDocument'])
        self.assertEqual(
            policy['Statement'][0]['Resource'][0],
            'arn:aws:s3:::lab-private-s3-data',
        )
        self.assertTrue(result['verified'])
        cache.clear()

    def test_ec2_endpoint_lab_status_requires_private_path_and_boundary(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            for verifier in (
                '_verify_ec2_endpoint_vpc',
                '_verify_ec2_endpoint_subnet',
                '_verify_ec2_endpoint_route_table',
                '_verify_ec2_endpoint_bucket',
                '_verify_ec2_s3_gateway_endpoint',
                '_verify_ec2_endpoint_route_boundary',
            ):
                stack.enter_context(patch(
                    f'dashboard.labs.ec2_labs.{verifier}',
                    return_value=passed,
                ))
            from .labs import lab_status

            status = lab_status('ec2', 's3-gateway-endpoint')

        self.assertTrue(status['complete'])
        self.assertTrue(
            status['steps']['create-s3-gateway-endpoint']['verified']
        )
        self.assertTrue(status['steps']['inspect-private-s3-path']['verified'])

    @patch('dashboard.labs.ec2_labs._verify_ec2_sqs_interface_endpoint')
    @patch('dashboard.labs.FlociClientFactory')
    def test_ec2_interface_lab_creates_private_dns_sqs_endpoint(
        self,
        factory_mock,
        verify_endpoint_mock,
    ):
        cache.clear()
        cache.set('floci-lab:ec2:sqs-interface:vpc-id', 'vpc-private')
        cache.set('floci-lab:ec2:sqs-interface:subnet-id', 'subnet-private')
        cache.set('floci-lab:ec2:sqs-interface:sg-id', 'sg-endpoint')
        ec2 = MagicMock()
        ec2.meta.region_name = 'us-east-1'
        ec2.create_vpc_endpoint.return_value = {
            'VpcEndpoint': {'VpcEndpointId': 'vpce-sqs'},
        }
        factory_mock.return_value.client.return_value = ec2
        verify_endpoint_mock.return_value = {
            'status': 'passed',
            'message': 'verified',
        }

        result = run_lab_step(
            'ec2',
            'sqs-interface-endpoint',
            'create-sqs-interface-endpoint',
        )

        call = ec2.create_vpc_endpoint.call_args.kwargs
        self.assertEqual(call['VpcEndpointType'], 'Interface')
        self.assertEqual(call['ServiceName'], 'com.amazonaws.us-east-1.sqs')
        self.assertEqual(call['SubnetIds'], ['subnet-private'])
        self.assertEqual(call['SecurityGroupIds'], ['sg-endpoint'])
        self.assertTrue(call['PrivateDnsEnabled'])
        policy = json.loads(call['PolicyDocument'])
        self.assertEqual(
            policy['Statement'][0]['Resource'],
            'arn:aws:sqs:us-east-1:000000000000:lab-private-sqs',
        )
        self.assertTrue(result['verified'])
        cache.clear()

    def test_ec2_interface_lab_status_requires_endpoint_topology(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            for verifier in (
                '_verify_ec2_interface_vpc',
                '_verify_ec2_interface_subnet',
                '_verify_ec2_interface_security_group',
                '_verify_ec2_interface_queue',
                '_verify_ec2_sqs_interface_endpoint',
                '_verify_ec2_interface_inspection',
            ):
                stack.enter_context(patch(
                    f'dashboard.labs.ec2_labs.{verifier}',
                    return_value=passed,
                ))
            from .labs import lab_status

            status = lab_status('ec2', 'sqs-interface-endpoint')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['allow-vpc-https']['verified'])
        self.assertTrue(
            status['steps']['create-sqs-interface-endpoint']['verified']
        )
        self.assertTrue(status['steps']['inspect-private-sqs-path']['verified'])


    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_lab_create_function_packages_handler_zip(self, factory_mock):
        lambda_client = MagicMock()
        lambda_client.create_function.return_value = {
            'FunctionName': 'lab-echo',
            'Role': 'arn:aws:iam::000000000000:role/FlociLambdaLabRole',
        }
        lambda_client.get_function.return_value = {
            'Configuration': {
                'FunctionName': 'lab-echo',
                'Role': 'arn:aws:iam::000000000000:role/FlociLambdaLabRole',
            },
        }
        factory_mock.return_value.client.return_value = lambda_client

        result = run_lab_step('lambda', 'create-invoke-logs', 'create-function')

        call = lambda_client.create_function.call_args.kwargs
        self.assertEqual(call['FunctionName'], 'lab-echo')
        self.assertEqual(call['Runtime'], 'python3.11')
        self.assertEqual(call['Handler'], 'handler.lambda_handler')
        with zipfile.ZipFile(BytesIO(call['Code']['ZipFile'])) as archive:
            source = archive.read('handler.py').decode('utf-8')
        self.assertIn('def lambda_handler', source)
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_lab_invoke_records_expected_echo_response(self, factory_mock):
        cache.clear()
        lambda_client = MagicMock()
        lambda_client.invoke.return_value = {
            'StatusCode': 200,
            'ExecutedVersion': '$LATEST',
            'Payload': BytesIO(json.dumps({
                'ok': True,
                'request_id': 'FLOCI-LAMBDA-1001',
                'echo': {'message': 'hello from the Lambda lab'},
            }).encode('utf-8')),
        }
        factory_mock.return_value.client.return_value = lambda_client

        result = run_lab_step('lambda', 'create-invoke-logs', 'invoke-function')

        call = lambda_client.invoke.call_args.kwargs
        self.assertEqual(call['FunctionName'], 'lab-echo')
        self.assertEqual(call['InvocationType'], 'RequestResponse')
        self.assertIn(b'FLOCI-LAMBDA-1001', call['Payload'])
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:lambda:create-invoke-logs:invoke'))
        cache.clear()

    def test_lambda_lab_status_requires_role_function_invoke_and_logs(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            for verifier in (
                '_verify_lambda_role',
                '_verify_lambda_role_policy',
                '_verify_lambda_function',
                '_verify_lambda_invocation',
                '_verify_lambda_log_group',
                '_verify_lambda_logs',
            ):
                stack.enter_context(patch(
                    f'dashboard.labs.lambda_labs.{verifier}',
                    return_value=passed,
                ))
            from .labs import lab_status

            status = lab_status('lambda', 'create-invoke-logs')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-role']['verified'])
        self.assertTrue(status['steps']['create-function']['verified'])
        self.assertTrue(status['steps']['invoke-function']['verified'])
        self.assertTrue(status['steps']['inspect-logs']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_lab_reset_deletes_function_logs_role_and_cache(self, factory_mock):
        cache.set('floci-lab:lambda:create-invoke-logs:invoke', {'ok': True})
        cache.set('floci-lab:lambda:create-invoke-logs:logs', {'ok': True})
        lambda_client = MagicMock()
        logs = MagicMock()
        iam = MagicMock()
        factory_mock.return_value.client.side_effect = lambda service: {
            'lambda': lambda_client,
            'logs': logs,
            'iam': iam,
        }[service]

        result = reset_lab('lambda', 'create-invoke-logs')

        lambda_client.delete_function.assert_called_once_with(FunctionName='lab-echo')
        logs.delete_log_group.assert_called_once_with(logGroupName='/aws/lambda/lab-echo')
        iam.delete_role_policy.assert_called_once_with(
            RoleName='FlociLambdaLabRole',
            PolicyName='FlociLambdaLabLogs',
        )
        iam.delete_role.assert_called_once_with(RoleName='FlociLambdaLabRole')
        self.assertTrue(result['reset'])
        self.assertIsNone(cache.get('floci-lab:lambda:create-invoke-logs:invoke'))
        self.assertIsNone(cache.get('floci-lab:lambda:create-invoke-logs:logs'))


    @patch('dashboard.labs.apigateway_labs._verify_apigw_lambda_integration')
    @patch('dashboard.labs.apigateway_labs._apigw_lambda_api_id')
    @patch('dashboard.labs.FlociClientFactory')
    def test_apigateway_lab_creates_lambda_proxy_integration(
        self,
        factory_mock,
        api_id_mock,
        verify_mock,
    ):
        cache.clear()
        api_id_mock.return_value = 'api123'
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}
        apigw = MagicMock()
        apigw.get_paginator.side_effect = ValueError('no paginator')
        apigw.get_integrations.return_value = {'Items': []}
        apigw.create_integration.return_value = {'IntegrationId': 'int123'}
        factory_mock.return_value.client.return_value = apigw

        result = run_lab_step('apigateway', 'lambda-request', 'create-integration')

        apigw.create_integration.assert_called_once_with(
            ApiId='api123',
            IntegrationType='AWS_PROXY',
            IntegrationUri='arn:aws:lambda:us-east-1:000000000000:function:lab-echo',
            PayloadFormatVersion='2.0',
        )
        self.assertTrue(result['verified'])
        self.assertEqual(cache.get('floci-lab:apigateway:lambda-request:integration-id'), 'int123')
        cache.clear()

    @override_settings(FLOCI_AWS_ENDPOINT_URL='http://localhost:4566')
    @patch('dashboard.labs.apigateway_labs.urlopen')
    @patch('dashboard.labs.apigateway_labs._find_apigw_lambda_api')
    def test_apigateway_lab_send_request_records_lambda_echo(
        self,
        api_mock,
        urlopen_mock,
    ):
        cache.clear()
        api_mock.return_value = {'ApiId': 'api123', 'ApiEndpoint': 'http://localhost:4566/api123'}
        response_mock = MagicMock()
        response_mock.__enter__.return_value = response_mock
        response_mock.read.return_value = b'{"ok": true, "request_id": "FLOCI-LAMBDA-1001"}'
        response_mock.getcode.return_value = 200
        response_mock.headers.items.return_value = [('Content-Type', 'application/json')]
        urlopen_mock.return_value = response_mock

        with patch.dict(os.environ, {
            'FLOCI_AWS_ENDPOINT_URL': 'http://localhost:4566',
            'AWS_ENDPOINT_URL': 'http://localhost:4566',
        }):
            result = run_lab_step('apigateway', 'lambda-request', 'send-request')

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(
            request.full_url,
            'http://localhost:4566/restapis/api123/$default/_user_request_/echo',
        )
        self.assertIn(b'FLOCI-LAMBDA-1001', request.data)
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:apigateway:lambda-request:request'))
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    @patch('dashboard.labs.apigateway_labs.urlopen')
    @patch('dashboard.labs.apigateway_labs._find_apigw_lambda_api')
    def test_apigateway_lab_send_request_falls_back_to_local_endpoint(
        self,
        api_mock,
        urlopen_mock,
        factory_mock,
    ):
        cache.clear()
        api_mock.return_value = {
            'ApiId': 'api123',
            'ApiEndpoint': 'https://api123.execute-api.localhost.localstack.cloud:4566',
        }
        factory_mock.return_value.endpoint_url = 'http://localhost:4566'
        response_mock = MagicMock()
        response_mock.__enter__.return_value = response_mock
        response_mock.read.return_value = b'{"ok": true, "request_id": "FLOCI-LAMBDA-1001"}'
        response_mock.getcode.return_value = 200
        response_mock.headers.items.return_value = [('Content-Type', 'application/json')]
        urlopen_mock.return_value = response_mock

        result = run_lab_step('apigateway', 'lambda-request', 'send-request')

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(
            request.full_url,
            'http://localhost:4566/restapis/api123/$default/_user_request_/echo',
        )
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.apigateway_labs._apigw_lambda_request_log_verification')
    @patch('dashboard.labs.apigateway_labs.urlopen')
    @patch('dashboard.labs.apigateway_labs._find_apigw_lambda_api')
    def test_apigateway_lab_send_request_accepts_logged_empty_response(
        self,
        api_mock,
        urlopen_mock,
        log_verify_mock,
    ):
        cache.clear()
        api_mock.return_value = {'ApiId': 'api123', 'ApiEndpoint': 'https://api123.execute-api.us-east-1.amazonaws.com'}
        log_verify_mock.return_value = {
            'status': 'passed',
            'message': 'log marker found',
        }
        response_mock = MagicMock()
        response_mock.__enter__.return_value = response_mock
        response_mock.read.return_value = b''
        response_mock.getcode.return_value = 200
        response_mock.headers.items.return_value = []
        urlopen_mock.return_value = response_mock

        result = run_lab_step('apigateway', 'lambda-request', 'send-request')

        self.assertTrue(result['verified'])
        self.assertEqual(result['json']['body'], '')
        log_verify_mock.assert_called_once()
        cache.clear()

    def test_apigateway_lab_status_requires_api_lambda_and_request(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            for verifier in (
                '_verify_lambda_role',
                '_verify_lambda_role_policy',
                '_verify_lambda_function',
                '_verify_apigw_lambda_api',
                '_verify_apigw_lambda_integration',
                '_verify_apigw_lambda_route',
                '_verify_apigw_lambda_stage',
                '_verify_apigw_lambda_permission',
                '_verify_apigw_lambda_request',
            ):
                stack.enter_context(patch(
                    f'dashboard.labs.apigateway_labs.{verifier}',
                    return_value=passed,
                ))
            from .labs import lab_status

            status = lab_status('apigateway', 'lambda-request')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-api']['verified'])
        self.assertTrue(status['steps']['create-integration']['verified'])
        self.assertTrue(status['steps']['create-route']['verified'])
        self.assertTrue(status['steps']['send-request']['verified'])

    @patch('dashboard.labs.apigateway_labs._find_apigw_lambda_api')
    @patch('dashboard.labs.FlociClientFactory')
    def test_apigateway_lab_reset_deletes_api_lambda_and_cache(
        self,
        factory_mock,
        api_mock,
    ):
        cache.set('floci-lab:apigateway:lambda-request:request', {'ok': True})
        cache.set('floci-lab:apigateway:lambda-request:api-id', 'api123')
        cache.set('floci-lab:apigateway:lambda-request:integration-id', 'int123')
        cache.set('floci-lab:apigateway:lambda-request:route-id', 'route123')
        api_mock.return_value = {'ApiId': 'api123'}
        apigw = MagicMock()
        lambda_client = MagicMock()
        logs = MagicMock()
        iam = MagicMock()
        factory_mock.return_value.client.side_effect = lambda service: {
            'apigatewayv2': apigw,
            'lambda': lambda_client,
            'logs': logs,
            'iam': iam,
        }[service]

        result = reset_lab('apigateway', 'lambda-request')

        apigw.delete_api.assert_called_once_with(ApiId='api123')
        lambda_client.remove_permission.assert_called_once_with(
            FunctionName='lab-echo',
            StatementId='AllowFlociApiGatewayInvoke',
        )
        lambda_client.delete_function.assert_called_once_with(FunctionName='lab-echo')
        logs.delete_log_group.assert_called_once_with(logGroupName='/aws/lambda/lab-echo')
        iam.delete_role_policy.assert_called_once_with(
            RoleName='FlociLambdaLabRole',
            PolicyName='FlociLambdaLabLogs',
        )
        iam.delete_role.assert_called_once_with(RoleName='FlociLambdaLabRole')
        self.assertTrue(result['reset'])
        self.assertIsNone(cache.get('floci-lab:apigateway:lambda-request:request'))
        self.assertIsNone(cache.get('floci-lab:apigateway:lambda-request:api-id'))


    @patch('dashboard.labs.dynamodb_labs._verify_dynamodb_table_exists')
    @patch('dashboard.labs.FlociClientFactory')
    def test_dynamodb_lab_create_table_uses_on_demand_table_with_customer_index(
        self,
        factory_mock,
        verify_mock,
    ):
        dynamodb = MagicMock()
        dynamodb.create_table.return_value = {'TableDescription': {'TableName': 'lab-orders'}}
        factory_mock.return_value.client.return_value = dynamodb
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('dynamodb', 'crud-query', 'create-table')

        call = dynamodb.create_table.call_args.kwargs
        self.assertEqual(call['TableName'], 'lab-orders')
        self.assertEqual(call['BillingMode'], 'PAY_PER_REQUEST')
        self.assertEqual(call['KeySchema'], [{'AttributeName': 'OrderId', 'KeyType': 'HASH'}])
        self.assertEqual(call['GlobalSecondaryIndexes'][0]['IndexName'], 'CustomerIdIndex')
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.dynamodb_labs._verify_dynamodb_order_updated')
    @patch('dashboard.labs.FlociClientFactory')
    def test_dynamodb_lab_update_item_sets_paid_status_and_records_marker(
        self,
        factory_mock,
        verify_mock,
    ):
        cache.clear()
        dynamodb = MagicMock()
        dynamodb.update_item.return_value = {'Attributes': {'Status': {'S': 'PAID'}}}
        factory_mock.return_value.client.return_value = dynamodb
        verify_mock.return_value = {
            'status': 'passed',
            'message': 'verified',
            'resource': {'Status': {'S': 'PAID'}},
        }

        result = run_lab_step('dynamodb', 'crud-query', 'update-item')

        call = dynamodb.update_item.call_args.kwargs
        self.assertEqual(call['UpdateExpression'], 'SET #status = :status, #total = :total')
        self.assertEqual(call['ExpressionAttributeNames'], {'#status': 'Status', '#total': 'Total'})
        self.assertEqual(call['ExpressionAttributeValues'][':total']['N'], '84.00')
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:dynamodb:crud-query:update-item'))
        cache.clear()

    @patch('dashboard.labs.dynamodb_labs._verify_dynamodb_customer_query')
    @patch('dashboard.labs.FlociClientFactory')
    def test_dynamodb_lab_queries_customer_index_and_records_marker(
        self,
        factory_mock,
        verify_mock,
    ):
        cache.clear()
        dynamodb = MagicMock()
        dynamodb.query.return_value = {'Items': [{'OrderId': {'S': 'ORDER#1001'}}]}
        factory_mock.return_value.client.return_value = dynamodb
        verify_mock.return_value = {
            'status': 'passed',
            'message': 'verified',
            'resource': {'Items': []},
        }

        result = run_lab_step('dynamodb', 'crud-query', 'query-customer-index')

        call = dynamodb.query.call_args.kwargs
        self.assertEqual(call['IndexName'], 'CustomerIdIndex')
        self.assertEqual(call['KeyConditionExpression'], 'CustomerId = :customer')
        self.assertEqual(call['ExpressionAttributeValues'][':customer']['S'], 'CUSTOMER#42')
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:dynamodb:crud-query:query'))
        cache.clear()

    def test_dynamodb_lab_status_uses_delete_table_marker_for_completion(self):
        passed = {'status': 'passed', 'message': 'verified'}
        cache.set('floci-lab:dynamodb:crud-query:delete-table', True)
        with ExitStack() as stack:
            stack.enter_context(patch(
                'dashboard.labs.dynamodb_labs._verify_dynamodb_table_deleted',
                return_value=passed,
            ))
            from .labs import lab_status

            status = lab_status('dynamodb', 'crud-query')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['delete-table']['verified'])
        cache.clear()

    @patch('dashboard.labs.FlociClientFactory')
    def test_dynamodb_lab_reset_deletes_item_table_and_cache(self, factory_mock):
        cache.set('floci-lab:dynamodb:crud-query:put-item', {'ok': True})
        cache.set('floci-lab:dynamodb:crud-query:update-item', {'ok': True})
        cache.set('floci-lab:dynamodb:crud-query:query', {'ok': True})
        cache.set('floci-lab:dynamodb:crud-query:delete-item', True)
        cache.set('floci-lab:dynamodb:crud-query:delete-table', True)
        dynamodb = MagicMock()
        factory_mock.return_value.client.return_value = dynamodb

        result = reset_lab('dynamodb', 'crud-query')

        dynamodb.delete_item.assert_called_once_with(
            TableName='lab-orders',
            Key={'OrderId': {'S': 'ORDER#1001'}},
        )
        dynamodb.delete_table.assert_called_once_with(TableName='lab-orders')
        self.assertTrue(result['reset'])
        self.assertIsNone(cache.get('floci-lab:dynamodb:crud-query:put-item'))
        self.assertIsNone(cache.get('floci-lab:dynamodb:crud-query:delete-table'))


    @patch('dashboard.labs.dynamodb_labs._verify_lambda_dynamodb_function')
    @patch('dashboard.labs.FlociClientFactory')
    def test_dynamodb_lambda_lab_create_function_packages_writer_with_table_env(
        self,
        factory_mock,
        verify_mock,
    ):
        lambda_client = MagicMock()
        lambda_client.create_function.return_value = {
            'FunctionName': 'lab-order-writer',
            'Role': 'arn:aws:iam::000000000000:role/FlociLambdaDynamoDbRole',
        }
        factory_mock.return_value.client.return_value = lambda_client
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('dynamodb', 'lambda-writes', 'create-function')

        call = lambda_client.create_function.call_args.kwargs
        self.assertEqual(call['FunctionName'], 'lab-order-writer')
        self.assertEqual(call['Environment'], {'Variables': {'TABLE_NAME': 'lab-lambda-orders'}})
        with zipfile.ZipFile(BytesIO(call['Code']['ZipFile'])) as archive:
            source = archive.read('handler.py').decode('utf-8')
        self.assertIn('dynamodb.put_item', source)
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_dynamodb_lambda_lab_invoke_records_write_response(self, factory_mock):
        cache.clear()
        lambda_client = MagicMock()
        lambda_client.invoke.return_value = {
            'StatusCode': 200,
            'Payload': BytesIO(json.dumps({
                'ok': True,
                'order_id': 'ORDER#2001',
                'request_id': 'FLOCI-LAMBDA-DDB-2001',
            }).encode('utf-8')),
        }
        factory_mock.return_value.client.return_value = lambda_client

        result = run_lab_step('dynamodb', 'lambda-writes', 'invoke-function')

        call = lambda_client.invoke.call_args.kwargs
        self.assertEqual(call['FunctionName'], 'lab-order-writer')
        self.assertIn(b'FLOCI-LAMBDA-DDB-2001', call['Payload'])
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:dynamodb:lambda-writes:invoke'))
        cache.clear()

    def test_dynamodb_lambda_lab_status_requires_table_function_item_and_logs(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            for verifier in (
                '_verify_lambda_dynamodb_table',
                '_verify_lambda_dynamodb_role',
                '_verify_lambda_dynamodb_policy',
                '_verify_lambda_dynamodb_function',
                '_verify_lambda_dynamodb_invocation',
                '_verify_lambda_dynamodb_item',
                '_verify_lambda_dynamodb_logs',
            ):
                stack.enter_context(patch(
                    f'dashboard.labs.dynamodb_labs.{verifier}',
                    return_value=passed,
                ))
            from .labs import lab_status

            status = lab_status('dynamodb', 'lambda-writes')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-table']['verified'])
        self.assertTrue(status['steps']['invoke-function']['verified'])
        self.assertTrue(status['steps']['get-written-item']['verified'])
        self.assertTrue(status['steps']['inspect-logs']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_dynamodb_lambda_lab_reset_deletes_writer_table_and_cache(self, factory_mock):
        cache.set('floci-lab:dynamodb:lambda-writes:invoke', {'ok': True})
        cache.set('floci-lab:dynamodb:lambda-writes:logs', {'ok': True})
        lambda_client = MagicMock()
        logs = MagicMock()
        iam = MagicMock()
        dynamodb = MagicMock()
        factory_mock.return_value.client.side_effect = lambda service: {
            'lambda': lambda_client,
            'logs': logs,
            'iam': iam,
            'dynamodb': dynamodb,
        }[service]

        result = reset_lab('dynamodb', 'lambda-writes')

        lambda_client.delete_function.assert_called_once_with(FunctionName='lab-order-writer')
        logs.delete_log_group.assert_called_once_with(logGroupName='/aws/lambda/lab-order-writer')
        iam.delete_role_policy.assert_called_once_with(
            RoleName='FlociLambdaDynamoDbRole',
            PolicyName='FlociLambdaDynamoDbWrite',
        )
        iam.delete_role.assert_called_once_with(RoleName='FlociLambdaDynamoDbRole')
        dynamodb.delete_item.assert_called_once_with(
            TableName='lab-lambda-orders',
            Key={'OrderId': {'S': 'ORDER#2001'}},
        )
        dynamodb.delete_table.assert_called_once_with(TableName='lab-lambda-orders')
        self.assertTrue(result['reset'])
        self.assertIsNone(cache.get('floci-lab:dynamodb:lambda-writes:invoke'))
        self.assertIsNone(cache.get('floci-lab:dynamodb:lambda-writes:logs'))


    @patch('dashboard.labs.lambda_labs._verify_lambda_config_function')
    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_runtime_config_lab_create_function_packages_reader_with_env(
        self,
        factory_mock,
        verify_mock,
    ):
        lambda_client = MagicMock()
        lambda_client.create_function.return_value = {
            'FunctionName': 'lab-config-reader',
            'Role': 'arn:aws:iam::000000000000:role/FlociLambdaConfigRole',
        }
        factory_mock.return_value.client.return_value = lambda_client
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('lambda', 'runtime-config', 'create-function')

        call = lambda_client.create_function.call_args.kwargs
        self.assertEqual(call['FunctionName'], 'lab-config-reader')
        self.assertEqual(call['Environment'], {
            'Variables': {
                'PARAMETER_NAME': '/floci/lab/lambda/config',
                'SECRET_ID': 'lab/lambda-secret',
            },
        })
        with zipfile.ZipFile(BytesIO(call['Code']['ZipFile'])) as archive:
            source = archive.read('handler.py').decode('utf-8')
        self.assertIn('ssm.get_parameter', source)
        self.assertIn('secretsmanager.get_secret_value', source)
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_runtime_config_lab_invoke_records_safe_response(self, factory_mock):
        cache.clear()
        lambda_client = MagicMock()
        lambda_client.invoke.return_value = {
            'StatusCode': 200,
            'Payload': BytesIO(json.dumps({
                'ok': True,
                'request_id': 'FLOCI-LAMBDA-CONFIG-3001',
                'parameter_name': '/floci/lab/lambda/config',
                'secret_id': 'lab/lambda-secret',
                'application': 'orders',
                'environment': 'local',
                'secret_username': 'floci-lambda',
                'secret_keys': ['api_key', 'username'],
            }).encode('utf-8')),
        }
        factory_mock.return_value.client.return_value = lambda_client

        result = run_lab_step('lambda', 'runtime-config', 'invoke-function')

        call = lambda_client.invoke.call_args.kwargs
        self.assertEqual(call['FunctionName'], 'lab-config-reader')
        self.assertIn(b'FLOCI-LAMBDA-CONFIG-3001', call['Payload'])
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:lambda:runtime-config:invoke'))
        self.assertNotIn('local-lambda-secret', result['stdout'])
        cache.clear()

    def test_lambda_runtime_config_lab_status_requires_config_function_invoke_and_logs(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            for verifier in (
                '_verify_lambda_config_parameter',
                '_verify_lambda_config_secret',
                '_verify_lambda_config_role',
                '_verify_lambda_config_role_policy',
                '_verify_lambda_config_function',
                '_verify_lambda_config_invocation',
                '_verify_lambda_config_logs',
            ):
                stack.enter_context(patch(
                    f'dashboard.labs.lambda_labs.{verifier}',
                    return_value=passed,
                ))
            from .labs import lab_status

            status = lab_status('lambda', 'runtime-config')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['put-parameter']['verified'])
        self.assertTrue(status['steps']['create-secret']['verified'])
        self.assertTrue(status['steps']['create-function']['verified'])
        self.assertTrue(status['steps']['invoke-function']['verified'])
        self.assertTrue(status['steps']['inspect-logs']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_runtime_config_lab_reset_deletes_owned_resources_and_cache(self, factory_mock):
        cache.set('floci-lab:lambda:runtime-config:invoke', {'ok': True})
        cache.set('floci-lab:lambda:runtime-config:logs', {'ok': True})
        lambda_client = MagicMock()
        logs = MagicMock()
        iam = MagicMock()
        ssm = MagicMock()
        secrets = MagicMock()
        factory_mock.return_value.client.side_effect = lambda service: {
            'lambda': lambda_client,
            'logs': logs,
            'iam': iam,
            'ssm': ssm,
            'secretsmanager': secrets,
        }[service]

        result = reset_lab('lambda', 'runtime-config')

        lambda_client.delete_function.assert_called_once_with(FunctionName='lab-config-reader')
        logs.delete_log_group.assert_called_once_with(logGroupName='/aws/lambda/lab-config-reader')
        iam.delete_role_policy.assert_called_once_with(
            RoleName='FlociLambdaConfigRole',
            PolicyName='FlociLambdaConfigRead',
        )
        iam.delete_role.assert_called_once_with(RoleName='FlociLambdaConfigRole')
        ssm.delete_parameter.assert_called_once_with(Name='/floci/lab/lambda/config')
        secrets.delete_secret.assert_called_once_with(
            SecretId='lab/lambda-secret',
            ForceDeleteWithoutRecovery=True,
        )
        self.assertTrue(result['reset'])
        self.assertIsNone(cache.get('floci-lab:lambda:runtime-config:invoke'))
        self.assertIsNone(cache.get('floci-lab:lambda:runtime-config:logs'))


    @patch('dashboard.labs.lambda_labs._verify_lambda_sqs_function')
    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_sqs_lab_create_function_packages_sqs_handler(
        self,
        factory_mock,
        verify_mock,
    ):
        lambda_client = MagicMock()
        lambda_client.create_function.return_value = {
            'FunctionName': 'lab-sqs-consumer',
            'Role': 'arn:aws:iam::000000000000:role/FlociLambdaSqsConsumerRole',
        }
        factory_mock.return_value.client.return_value = lambda_client
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('lambda', 'sqs-event-source', 'create-function')

        call = lambda_client.create_function.call_args.kwargs
        self.assertEqual(call['FunctionName'], 'lab-sqs-consumer')
        self.assertEqual(call['Role'], 'arn:aws:iam::000000000000:role/FlociLambdaSqsConsumerRole')
        with zipfile.ZipFile(BytesIO(call['Code']['ZipFile'])) as archive:
            source = archive.read('handler.py').decode('utf-8')
        self.assertIn('event.get("Records", [])', source)
        self.assertIn('processed sqs event', source)
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.lambda_labs._verify_lambda_sqs_event_source_mapping')
    @patch('dashboard.labs.lambda_labs._find_lambda_sqs_event_source_mapping')
    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_sqs_lab_creates_event_source_mapping(
        self,
        factory_mock,
        find_mapping_mock,
        verify_mock,
    ):
        cache.clear()
        find_mapping_mock.return_value = None
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}
        lambda_client = MagicMock()
        lambda_client.create_event_source_mapping.return_value = {
            'UUID': 'mapping-123',
            'FunctionArn': 'arn:aws:lambda:us-east-1:000000000000:function:lab-sqs-consumer',
            'EventSourceArn': 'arn:aws:sqs:us-east-1:000000000000:lab-lambda-events',
        }
        factory_mock.return_value.client.return_value = lambda_client

        result = run_lab_step('lambda', 'sqs-event-source', 'create-event-source-mapping')

        lambda_client.create_event_source_mapping.assert_called_once_with(
            FunctionName='lab-sqs-consumer',
            EventSourceArn='arn:aws:sqs:us-east-1:000000000000:lab-lambda-events',
            BatchSize=5,
            Enabled=True,
        )
        self.assertTrue(result['verified'])
        self.assertEqual(cache.get('floci-lab:lambda:sqs-event-source:mapping'), 'mapping-123')
        cache.clear()

    @patch('dashboard.labs.lambda_labs._verify_lambda_sqs_message_sent')
    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_sqs_lab_send_message_records_marker(
        self,
        factory_mock,
        verify_mock,
    ):
        cache.clear()
        sqs = MagicMock()
        sqs.get_queue_url.return_value = {'QueueUrl': 'http://localhost/queue/lab-lambda-events'}
        sqs.send_message.return_value = {'MessageId': 'msg-123', 'MD5OfMessageBody': 'abc'}
        factory_mock.return_value.client.return_value = sqs
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('lambda', 'sqs-event-source', 'send-message')

        call = sqs.send_message.call_args.kwargs
        self.assertEqual(call['QueueUrl'], 'http://localhost/queue/lab-lambda-events')
        self.assertIn('FLOCI-LAMBDA-SQS-4001', call['MessageBody'])
        self.assertEqual(call['MessageAttributes']['event_type']['StringValue'], 'order.created')
        self.assertTrue(result['verified'])
        self.assertTrue(cache.get('floci-lab:lambda:sqs-event-source:send-message'))
        cache.clear()

    def test_lambda_sqs_lab_status_requires_queue_mapping_message_and_logs(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            for verifier in (
                '_verify_lambda_sqs_queue',
                '_verify_lambda_sqs_role',
                '_verify_lambda_sqs_role_policy',
                '_verify_lambda_sqs_function',
                '_verify_lambda_sqs_event_source_mapping',
                '_verify_lambda_sqs_message_sent',
                '_verify_lambda_sqs_logs',
            ):
                stack.enter_context(patch(
                    f'dashboard.labs.lambda_labs.{verifier}',
                    return_value=passed,
                ))
            from .labs import lab_status

            status = lab_status('lambda', 'sqs-event-source')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-queue']['verified'])
        self.assertTrue(status['steps']['create-event-source-mapping']['verified'])
        self.assertTrue(status['steps']['send-message']['verified'])
        self.assertTrue(status['steps']['inspect-logs']['verified'])

    @patch('dashboard.labs.lambda_labs._find_lambda_sqs_event_source_mapping')
    @patch('dashboard.labs.FlociClientFactory')
    def test_lambda_sqs_lab_reset_deletes_mapping_function_queue_and_cache(
        self,
        factory_mock,
        find_mapping_mock,
    ):
        cache.set('floci-lab:lambda:sqs-event-source:mapping', 'mapping-123')
        cache.set('floci-lab:lambda:sqs-event-source:send-message', {'ok': True})
        cache.set('floci-lab:lambda:sqs-event-source:logs', {'ok': True})
        find_mapping_mock.return_value = {'UUID': 'mapping-123'}
        lambda_client = MagicMock()
        logs = MagicMock()
        iam = MagicMock()
        sqs = MagicMock()
        sqs.get_queue_url.return_value = {'QueueUrl': 'http://localhost/queue/lab-lambda-events'}
        factory_mock.return_value.client.side_effect = lambda service: {
            'lambda': lambda_client,
            'logs': logs,
            'iam': iam,
            'sqs': sqs,
        }[service]

        result = reset_lab('lambda', 'sqs-event-source')

        lambda_client.delete_event_source_mapping.assert_called_once_with(UUID='mapping-123')
        lambda_client.delete_function.assert_called_once_with(FunctionName='lab-sqs-consumer')
        logs.delete_log_group.assert_called_once_with(logGroupName='/aws/lambda/lab-sqs-consumer')
        iam.delete_role_policy.assert_called_once_with(
            RoleName='FlociLambdaSqsConsumerRole',
            PolicyName='FlociLambdaSqsConsumerPolicy',
        )
        iam.delete_role.assert_called_once_with(RoleName='FlociLambdaSqsConsumerRole')
        sqs.delete_queue.assert_called_once_with(QueueUrl='http://localhost/queue/lab-lambda-events')
        self.assertTrue(result['reset'])
        self.assertIsNone(cache.get('floci-lab:lambda:sqs-event-source:mapping'))
        self.assertIsNone(cache.get('floci-lab:lambda:sqs-event-source:send-message'))
        self.assertIsNone(cache.get('floci-lab:lambda:sqs-event-source:logs'))


    @patch('dashboard.labs.kms_labs._verify_kms_lab_key')
    @patch('dashboard.labs.kms_labs._find_kms_lab_key_id')
    @patch('dashboard.labs.FlociClientFactory')
    def test_kms_lab_create_key_tags_symmetric_key(
        self,
        factory_mock,
        find_mock,
        verify_mock,
    ):
        cache.clear()
        find_mock.return_value = None
        kms = MagicMock()
        kms.create_key.return_value = {
            'KeyMetadata': {
                'KeyId': 'key-123',
                'KeyUsage': 'ENCRYPT_DECRYPT',
                'KeySpec': 'SYMMETRIC_DEFAULT',
            },
        }
        factory_mock.return_value.client.return_value = kms
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('kms', 'key-alias-encrypt-decrypt', 'create-key')

        call = kms.create_key.call_args.kwargs
        self.assertEqual(call['Description'], 'Floci dashboard local workflow lab key')
        self.assertEqual(call['KeyUsage'], 'ENCRYPT_DECRYPT')
        self.assertEqual(call['KeySpec'], 'SYMMETRIC_DEFAULT')
        self.assertIn({'TagKey': 'Lab', 'TagValue': 'kms-crypto'}, call['Tags'])
        self.assertEqual(cache.get('floci-lab:kms:crypto:key-id'), 'key-123')
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.kms_labs._verify_kms_lab_ciphertext')
    @patch('dashboard.labs.FlociClientFactory')
    def test_kms_lab_encrypt_records_ciphertext(self, factory_mock, verify_mock):
        cache.clear()
        kms = MagicMock()
        kms.encrypt.return_value = {
            'KeyId': 'key-123',
            'CiphertextBlob': b'encrypted-config',
            'EncryptionAlgorithm': 'SYMMETRIC_DEFAULT',
        }
        factory_mock.return_value.client.return_value = kms
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('kms', 'key-alias-encrypt-decrypt', 'encrypt')

        call = kms.encrypt.call_args.kwargs
        self.assertEqual(call['KeyId'], 'alias/lab-data-key')
        self.assertIn(b'sample-local-token', call['Plaintext'])
        self.assertEqual(
            cache.get('floci-lab:kms:crypto:ciphertext')['CiphertextBlob'],
            base64.b64encode(b'encrypted-config').decode('ascii'),
        )
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.kms_labs._verify_kms_lab_decrypt')
    @patch('dashboard.labs.FlociClientFactory')
    def test_kms_lab_decrypt_records_plaintext_match(self, factory_mock, verify_mock):
        cache.clear()
        cache.set('floci-lab:kms:crypto:ciphertext', {
            'CiphertextBlob': base64.b64encode(b'encrypted-config').decode('ascii'),
        })
        kms = MagicMock()
        kms.decrypt.return_value = {
            'KeyId': 'key-123',
            'Plaintext': b'{"application":"orders","environment":"local","secret":"sample-local-token"}',
            'EncryptionAlgorithm': 'SYMMETRIC_DEFAULT',
        }
        factory_mock.return_value.client.return_value = kms
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('kms', 'key-alias-encrypt-decrypt', 'decrypt')

        kms.decrypt.assert_called_once_with(CiphertextBlob=b'encrypted-config')
        self.assertTrue(cache.get('floci-lab:kms:crypto:decrypt'))
        self.assertTrue(result['verified'])
        cache.clear()

    def test_kms_lab_status_requires_key_alias_ciphertext_and_decrypt(self):
        passed = {'status': 'passed', 'message': 'verified'}
        with ExitStack() as stack:
            for verifier in (
                '_verify_kms_lab_key',
                '_verify_kms_lab_alias',
                '_verify_kms_lab_ciphertext',
                '_verify_kms_lab_decrypt',
            ):
                stack.enter_context(patch(
                    f'dashboard.labs.kms_labs.{verifier}',
                    return_value=passed,
                ))
            from .labs import lab_status

            status = lab_status('kms', 'key-alias-encrypt-decrypt')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['create-key']['verified'])
        self.assertTrue(status['steps']['create-alias']['verified'])
        self.assertTrue(status['steps']['encrypt']['verified'])
        self.assertTrue(status['steps']['decrypt']['verified'])

    @patch('dashboard.labs.kms_labs._find_kms_lab_key_id')
    @patch('dashboard.labs.FlociClientFactory')
    def test_kms_lab_reset_deletes_alias_schedules_key_and_clears_cache(
        self,
        factory_mock,
        find_mock,
    ):
        cache.set('floci-lab:kms:crypto:key-id', 'key-123')
        cache.set('floci-lab:kms:crypto:ciphertext', {'ok': True})
        cache.set('floci-lab:kms:crypto:decrypt', {'ok': True})
        find_mock.return_value = 'key-123'
        kms = MagicMock()
        factory_mock.return_value.client.return_value = kms

        result = reset_lab('kms', 'key-alias-encrypt-decrypt')

        kms.delete_alias.assert_called_once_with(AliasName='alias/lab-data-key')
        kms.schedule_key_deletion.assert_called_once_with(
            KeyId='key-123',
            PendingWindowInDays=7,
        )
        self.assertTrue(result['reset'])
        self.assertIsNone(cache.get('floci-lab:kms:crypto:key-id'))
        self.assertIsNone(cache.get('floci-lab:kms:crypto:ciphertext'))
        self.assertIsNone(cache.get('floci-lab:kms:crypto:decrypt'))


    @patch('dashboard.labs.ssm_labs._verify_ssm_parameter')
    @patch('dashboard.labs.FlociClientFactory')
    def test_ssm_lab_put_parameter_writes_json_config(self, factory_mock, verify_mock):
        ssm = MagicMock()
        ssm.put_parameter.return_value = {'Version': 1, 'Tier': 'Standard'}
        factory_mock.return_value.client.return_value = ssm
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('ssm', 'parameter-store-config', 'put-parameter')

        call = ssm.put_parameter.call_args.kwargs
        self.assertEqual(call['Name'], '/floci/lab/app/config')
        self.assertEqual(call['Type'], 'String')
        self.assertTrue(call['Overwrite'])
        self.assertIn('"checkout":true', call['Value'])
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_ssm_lab_status_requires_parameter_and_path(self, factory_mock):
        ssm = MagicMock()
        parameter = {
            'Name': '/floci/lab/app/config',
            'Type': 'String',
            'Value': '{"application":"orders","environment":"local","featureFlags":{"checkout":true,"auditMode":"verbose"}}',
        }
        ssm.get_parameter.return_value = {'Parameter': parameter}
        ssm.get_parameters_by_path.return_value = {'Parameters': [parameter]}
        factory_mock.return_value.client.return_value = ssm
        from .labs import lab_status

        status = lab_status('ssm', 'parameter-store-config')

        self.assertTrue(status['complete'])
        self.assertTrue(status['steps']['put-parameter']['verified'])
        self.assertTrue(status['steps']['get-parameters-by-path']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_ssm_lab_reset_deletes_parameter(self, factory_mock):
        ssm = MagicMock()
        ssm.get_parameter.side_effect = ClientError(
            {'Error': {'Code': 'ParameterNotFound', 'Message': 'missing'}},
            'GetParameter',
        )
        factory_mock.return_value.client.return_value = ssm

        result = reset_lab('ssm', 'parameter-store-config')

        ssm.delete_parameter.assert_called_once_with(Name='/floci/lab/app/config')
        self.assertTrue(result['reset'])
        self.assertTrue(result['deleted'])


    @patch('dashboard.labs.secretsmanager_labs._verify_secretsmanager_secret_exists')
    @patch('dashboard.labs.FlociClientFactory')
    def test_secretsmanager_lab_create_secret_writes_initial_json(self, factory_mock, verify_mock):
        secrets = MagicMock()
        secrets.create_secret.return_value = {
            'Name': 'lab/app-credentials',
            'ARN': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:lab/app-credentials',
            'VersionId': 'version-1',
        }
        factory_mock.return_value.client.return_value = secrets
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('secretsmanager', 'secret-lifecycle', 'create-secret')

        call = secrets.create_secret.call_args.kwargs
        self.assertEqual(call['Name'], 'lab/app-credentials')
        self.assertIn('initial-local-password', call['SecretString'])
        self.assertTrue(result['verified'])

    @patch('dashboard.labs.secretsmanager_labs._verify_secretsmanager_initial_read')
    @patch('dashboard.labs.FlociClientFactory')
    def test_secretsmanager_lab_initial_read_records_marker(self, factory_mock, verify_mock):
        cache.clear()
        secrets = MagicMock()
        secrets.get_secret_value.return_value = {
            'Name': 'lab/app-credentials',
            'SecretString': '{"username":"lab-app","password":"initial-local-password"}',
        }
        factory_mock.return_value.client.return_value = secrets
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('secretsmanager', 'secret-lifecycle', 'get-initial-secret')

        self.assertTrue(cache.get('floci-lab:secretsmanager:secret-lifecycle:initial-read'))
        self.assertTrue(result['verified'])
        cache.clear()

    @patch('dashboard.labs.secretsmanager_labs._verify_secretsmanager_manual_update_is_current')
    @patch('dashboard.labs.FlociClientFactory')
    def test_secretsmanager_lab_put_secret_value_writes_updated_json(self, factory_mock, verify_mock):
        secrets = MagicMock()
        secrets.put_secret_value.return_value = {
            'Name': 'lab/app-credentials',
            'VersionId': 'version-2',
            'VersionStages': ['AWSCURRENT'],
        }
        factory_mock.return_value.client.return_value = secrets
        verify_mock.return_value = {'status': 'passed', 'message': 'verified'}

        result = run_lab_step('secretsmanager', 'secret-lifecycle', 'put-secret-value')

        call = secrets.put_secret_value.call_args.kwargs
        self.assertEqual(call['SecretId'], 'lab/app-credentials')
        self.assertIn('updated-local-password', call['SecretString'])
        self.assertTrue(result['verified'])

    SECRETS_STATUS_VERIFIERS = (
        '_verify_secretsmanager_secret_exists',
        '_verify_secretsmanager_initial_read',
        '_verify_secretsmanager_updated_secret',
        '_verify_secrets_rotation_role',
        '_verify_secrets_rotation_function',
        '_verify_secrets_rotation_permission',
        '_verify_secrets_rotation_enabled',
        '_verify_secrets_rotated_value',
    )

    def _secrets_status(self, failing=()):
        passed = {'status': 'passed', 'message': 'verified'}
        failed = {'status': 'failed', 'message': 'not yet'}
        with ExitStack() as stack:
            for verifier in self.SECRETS_STATUS_VERIFIERS:
                stack.enter_context(patch(
                    f'dashboard.labs.secretsmanager_labs.{verifier}',
                    return_value=failed if verifier in failing else passed,
                ))
            from .labs import lab_status

            return lab_status('secretsmanager', 'secret-lifecycle')

    def test_secretsmanager_lab_status_requires_initial_updated_and_rotated_secret(self):
        status = self._secrets_status()

        self.assertTrue(status['complete'])
        self.assertEqual(len(status['steps']), 10)
        self.assertTrue(all(step['verified'] for step in status['steps'].values()))

    def test_secretsmanager_lab_status_incomplete_until_rotation_finishes(self):
        status = self._secrets_status(failing={'_verify_secrets_rotated_value'})

        self.assertFalse(status['complete'])
        self.assertTrue(status['steps']['rotate-secret']['verified'])
        self.assertFalse(status['steps']['get-rotated-secret']['verified'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_secretsmanager_rotated_value_requires_new_password_and_manual_previous(self, factory_mock):
        secrets = MagicMock()
        factory_mock.return_value.client.return_value = secrets

        def versions(current_password, previous_password):
            values = {
                'AWSCURRENT': {'username': 'lab-app', 'password': current_password},
                'AWSPREVIOUS': {'username': 'lab-app', 'password': previous_password},
            }
            return lambda **kwargs: {'SecretString': json.dumps(values[kwargs['VersionStage']])}

        from dashboard.labs.secretsmanager_labs import _verify_secrets_rotated_value

        # Before rotation: the manual update is still current.
        secrets.get_secret_value.side_effect = versions('updated-local-password', 'initial-local-password')
        self.assertEqual(_verify_secrets_rotated_value()['status'], 'failed')

        # After rotation: a generated password is current and the manual update is previous.
        secrets.get_secret_value.side_effect = versions('rotated-0a1b2c3d4e5f6789', 'updated-local-password')
        self.assertEqual(_verify_secrets_rotated_value()['status'], 'passed')

    @patch('dashboard.labs.FlociClientFactory')
    def test_secretsmanager_lab_reset_deletes_secret_and_clears_markers(self, factory_mock):
        cache.set('floci-lab:secretsmanager:secret-lifecycle:initial-read', {'ok': True})
        cache.set('floci-lab:secretsmanager:secret-lifecycle:updated-read', {'ok': True})
        secrets = MagicMock()
        secrets.describe_secret.side_effect = ClientError(
            {'Error': {'Code': 'ResourceNotFoundException', 'Message': 'missing'}},
            'DescribeSecret',
        )
        factory_mock.return_value.client.return_value = secrets

        result = reset_lab('secretsmanager', 'secret-lifecycle')

        secrets.delete_secret.assert_called_once_with(
            SecretId='lab/app-credentials',
            ForceDeleteWithoutRecovery=True,
        )
        self.assertTrue(result['reset'])
        self.assertTrue(result['deleted'])
        secrets.delete_function.assert_called_once_with(FunctionName='lab-secret-rotator')
        secrets.delete_role.assert_called_once_with(RoleName='FlociSecretsRotationRole')
        self.assertIsNone(cache.get('floci-lab:secretsmanager:secret-lifecycle:initial-read'))
        self.assertIsNone(cache.get('floci-lab:secretsmanager:secret-lifecycle:updated-read'))

    @patch('dashboard.labs.FlociClientFactory')
    def test_run_iam_create_user_step_creates_and_verifies_alice(self, factory_mock):
        iam = MagicMock()
        iam.create_user.return_value = {'User': {'UserName': 'Alice', 'Arn': 'arn:aws:iam::000000000000:user/Alice'}}
        iam.get_user.return_value = {'User': {'UserName': 'Alice', 'Arn': 'arn:aws:iam::000000000000:user/Alice'}}
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'create-user-alice', 'create-user')

        iam.create_user.assert_called_once_with(UserName='Alice')
        iam.get_user.assert_called_once_with(UserName='Alice')
        self.assertEqual(result['command'], 'aws iam create-user --user-name Alice')
        self.assertEqual(result['exit_code'], 0)
        self.assertTrue(result['verified'])
        self.assertIn('"UserName": "Alice"', result['stdout'])

    @patch('dashboard.labs.FlociClientFactory')
    def test_run_iam_create_user_step_treats_existing_alice_as_verified(self, factory_mock):
        iam = MagicMock()
        iam.create_user.side_effect = ClientError(
            {'Error': {'Code': 'EntityAlreadyExists', 'Message': 'User already exists'}},
            'CreateUser',
        )
        iam.get_user.return_value = {'User': {'UserName': 'Alice', 'Arn': 'arn:aws:iam::000000000000:user/Alice'}}
        factory_mock.return_value.client.return_value = iam

        result = run_lab_step('iam', 'group-membership-alice', 'create-user')

        iam.create_user.assert_called_once_with(UserName='Alice')
        iam.get_user.assert_called_with(UserName='Alice')
        self.assertTrue(result['verified'])
        self.assertEqual(result['lab'], 'group-membership-alice')

    @patch('dashboard.views.reset_lab')
    def test_reset_lab_endpoint_returns_reset_payload(self, reset_mock):
        reset_mock.return_value = {
            'service': 'iam',
            'lab': 'create-user-alice',
            'command': 'aws iam delete-user --user-name Alice',
            'exit_code': 0,
            'stdout': '{}',
            'stderr': '',
            'reset': True,
            'deleted': True,
            'verification': {'status': 'passed', 'message': 'User Alice was removed.'},
        }

        response = self.client.post(reverse('dashboard:lab-reset', kwargs={
            'service_key': 'iam',
            'lab_key': 'create-user-alice',
        }))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['reset'])
        reset_mock.assert_called_once_with('iam', 'create-user-alice')

    @patch('dashboard.views.lab_status')
    @patch('dashboard.views.run_lab_step')
    def test_run_lab_step_endpoint_returns_runner_payload(self, run_mock, status_mock):
        run_mock.return_value = {
            'service': 'iam',
            'lab': 'create-user-alice',
            'step': 'create-user',
            'command': 'aws iam create-user --user-name Alice',
            'exit_code': 0,
            'stdout': '{}',
            'stderr': '',
            'verified': True,
            'verification': {'status': 'passed', 'message': 'User Alice exists in local IAM.'},
        }
        status_mock.return_value = {'complete': False, 'steps': {}}

        response = self.client.post(reverse('dashboard:lab-step-run', kwargs={
            'service_key': 'iam',
            'lab_key': 'create-user-alice',
            'step_key': 'create-user',
        }))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['verified'])
        self.assertFalse(response.json()['lab_complete'])
        self.assertIsNone(response.json()['next_batch'])
        run_mock.assert_called_once_with('iam', 'create-user-alice', 'create-user')

    @patch('dashboard.views.lab_status')
    @patch('dashboard.views.run_lab_step')
    def test_run_lab_step_endpoint_preserves_step_payload_when_status_check_fails(self, run_mock, status_mock):
        run_mock.return_value = {
            'service': 'iam',
            'lab': 'create-user-alice',
            'step': 'create-user',
            'command': 'aws iam create-user --user-name Alice',
            'exit_code': 0,
            'stdout': '{}',
            'stderr': '',
            'verified': True,
            'verification': {'status': 'passed', 'message': 'User Alice exists in local IAM.'},
        }
        status_mock.side_effect = access_denied('iam:GetUser')

        response = self.client.post(reverse('dashboard:lab-step-run', kwargs={
            'service_key': 'iam',
            'lab_key': 'create-user-alice',
            'step_key': 'create-user',
        }))

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload['verified'])
        self.assertFalse(payload['lab_complete'])
        self.assertIsNone(payload['next_batch'])
        self.assertIn('User is not authorized to perform: iam:GetUser', payload['status_warning'])

    @patch('dashboard.views.lab_status')
    @patch('dashboard.views.run_lab_step')
    def test_run_lab_step_endpoint_returns_next_batch_when_complete(
        self,
        run_mock,
        status_mock,
    ):
        run_mock.return_value = {
            'service': 'iam',
            'lab': 'identity-enforcement-capstone',
            'step': 'verify-role-s3-denied',
            'verified': True,
        }
        status_mock.return_value = {'complete': True, 'steps': {}}

        response = self.client.post(reverse('dashboard:lab-step-run', kwargs={
            'service_key': 'iam',
            'lab_key': 'identity-enforcement-capstone',
            'step_key': 'verify-role-s3-denied',
        }))

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload['lab_complete'])
        self.assertEqual(payload['next_batch']['title'], 'S3 labs')
        self.assertEqual(
            payload['next_batch']['href'],
            '/labs/?service=s3&lab=create-bucket',
        )

    @patch('dashboard.views.reset_lab')
    @patch('dashboard.views.lab_status')
    @patch('dashboard.views.all_labs')
    def test_global_lab_reset_resets_only_completed_labs(
        self,
        all_labs_mock,
        status_mock,
        reset_mock,
    ):
        all_labs_mock.return_value = [
            {
                'service': 'iam',
                'key': 'create-user-alice',
                'title': 'Create an IAM user',
                'steps': [],
            },
            {
                'service': 's3',
                'key': 'create-bucket',
                'title': 'Create an S3 bucket',
                'steps': [],
            },
        ]
        status_mock.side_effect = [
            {'complete': True, 'steps': {}},
            {'complete': False, 'steps': {}},
        ]
        reset_mock.return_value = {'reset': True}

        response = self.client.post(reverse('dashboard:labs-global-reset'))

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload['reset'])
        self.assertEqual(payload['completed_lab_count'], 1)
        self.assertEqual(payload['reset_lab_count'], 1)
        reset_mock.assert_called_once_with('iam', 'create-user-alice')


class StepFunctionsLabRunnerTests(SimpleTestCase):
    @patch('dashboard.labs.stepfunctions_labs.client')
    def test_stepfunctions_order_processing_and_parallel_runners(self, client_mock):
        cache.clear()
        mock_sfn = MagicMock()
        mock_iam = MagicMock()
        client_mock.side_effect = lambda name: mock_sfn if name == 'stepfunctions' else mock_iam

        mock_iam.create_role.return_value = {'Role': {'RoleName': 'FlociStepFunctionsExecutionRole'}}
        mock_sfn.create_state_machine.return_value = {'stateMachineArn': 'arn:aws:states:us-east-1:000000000000:stateMachine:lab-order-processing-workflow'}
        mock_sfn.start_execution.return_value = {'executionArn': 'arn:aws:states:us-east-1:000000000000:execution:lab-order-processing-workflow:vip-1'}
        mock_sfn.describe_execution.return_value = {'executionArn': 'arn:aws:states:us-east-1:000000000000:execution:lab-order-processing-workflow:vip-1', 'status': 'SUCCEEDED', 'output': '{"status":"VIP_PROCESSED"}'}
        mock_sfn.get_execution_history.return_value = {'events': [{'type': 'ExecutionSucceeded'}]}
        mock_sfn.describe_state_machine.return_value = {'status': 'ACTIVE'}

        from dashboard.labs.stepfunctions_labs import run_step, status, reset

        res1 = run_step('stepfunctions', 'order-processing-workflow', 'create-execution-role')
        self.assertTrue(res1['verified'])

        res2 = run_step('stepfunctions', 'order-processing-workflow', 'create-choice-state-machine')
        self.assertTrue(res2['verified'])

        res3 = run_step('stepfunctions', 'order-processing-workflow', 'start-vip-execution')
        self.assertTrue(res3['verified'])

        res4 = run_step('stepfunctions', 'order-processing-workflow', 'start-standard-execution')
        self.assertTrue(res4['verified'])

        res5 = run_step('stepfunctions', 'order-processing-workflow', 'inspect-execution-history')
        self.assertTrue(res5['verified'])

        st = status('stepfunctions', 'order-processing-workflow')
        self.assertTrue(st['complete'])

        rst = reset('stepfunctions', 'order-processing-workflow')
        self.assertTrue(rst['reset'])


class CognitoLabRunnerTests(SimpleTestCase):
    @patch('dashboard.labs.cognito_labs.client')
    def test_cognito_user_pool_and_group_runners(self, client_mock):
        cache.clear()
        mock_cog = MagicMock()
        client_mock.return_value = mock_cog

        mock_cog.list_user_pools.return_value = {'UserPools': []}
        mock_cog.create_user_pool.return_value = {'UserPool': {'Id': 'us-east-1_mock123', 'Name': 'lab-auth-user-pool'}}
        mock_cog.list_user_pool_clients.return_value = {'UserPoolClients': []}
        mock_cog.create_user_pool_client.return_value = {'UserPoolClient': {'ClientId': 'client_mock123', 'ClientName': 'lab-web-app-client'}}
        mock_cog.admin_create_user.return_value = {'User': {'Username': 'developer@floci.local'}}
        mock_cog.admin_get_user.return_value = {'Username': 'developer@floci.local', 'UserStatus': 'CONFIRMED'}
        mock_cog.admin_initiate_auth.return_value = {'AuthenticationResult': {'IdToken': 'mock.id.token', 'AccessToken': 'mock.access.token', 'TokenType': 'Bearer'}}
        mock_cog.create_group.return_value = {'Group': {'GroupName': 'Developers'}}
        mock_cog.admin_list_groups_for_user.return_value = {'Groups': [{'GroupName': 'Developers'}]}

        from dashboard.labs.cognito_labs import run_step, status, reset

        res1 = run_step('cognito', 'user-pool-signup-auth', 'create-user-pool')
        self.assertTrue(res1['verified'])

        res2 = run_step('cognito', 'user-pool-signup-auth', 'create-app-client')
        self.assertTrue(res2['verified'])

        res3 = run_step('cognito', 'user-pool-signup-auth', 'sign-up-user')
        self.assertTrue(res3['verified'])

        res4 = run_step('cognito', 'user-pool-signup-auth', 'confirm-set-password')
        self.assertTrue(res4['verified'])

        res5 = run_step('cognito', 'user-pool-signup-auth', 'authenticate-user')
        self.assertTrue(res5['verified'])

        st = status('cognito', 'user-pool-signup-auth')
        self.assertTrue(st['complete'])

        rst = reset('cognito', 'user-pool-signup-auth')
        self.assertTrue(rst['reset'])


class CloudWatchLabRunnerTests(SimpleTestCase):
    @patch('dashboard.labs.cloudwatch_labs.client')
    def test_cloudwatch_metrics_and_logs_runners(self, client_mock):
        cache.clear()
        mock_cw = MagicMock()
        mock_logs = MagicMock()
        client_mock.side_effect = lambda name: mock_cw if name == 'cloudwatch' else mock_logs

        def alarm(state):
            return {'MetricAlarms': [{'AlarmName': 'lab-order-error-alarm', 'Threshold': 10, 'StateValue': state}]}

        mock_cw.describe_alarms.side_effect = [alarm('INSUFFICIENT_DATA'), alarm('OK'), alarm('ALARM'), alarm('ALARM')]
        mock_cw.get_metric_statistics.return_value = {'Datapoints': [{'Sum': 2.0}]}
        mock_logs.describe_log_groups.return_value = {
            'logGroups': [{'logGroupName': '/aws/floci/app-logs', 'retentionInDays': 30}],
        }
        mock_logs.describe_log_streams.return_value = {'logStreams': [{'logStreamName': 'web-server-1'}]}
        mock_logs.describe_metric_filters.return_value = {
            'metricFilters': [{'filterName': 'lab-http-500-errors', 'filterPattern': '"HTTP 500"'}],
        }

        from dashboard.labs.cloudwatch_labs import run_step, status, reset

        for step in ['create-metric-alarm', 'publish-healthy-metrics', 'publish-error-spike', 'describe-alarms']:
            with self.subTest(step=step):
                self.assertTrue(run_step('cloudwatch', 'metric-alarms', step)['verified'])
        mock_cw.set_alarm_state.assert_not_called()
        self.assertEqual(
            [call.kwargs['MetricData'][0]['Value'] for call in mock_cw.put_metric_data.call_args_list],
            [2, 25],
        )
        self.assertTrue(status('cloudwatch', 'metric-alarms')['complete'])

        for step in [
            'create-log-group', 'put-retention-policy', 'create-log-stream',
            'put-metric-filter', 'put-log-events', 'get-metric-statistics',
        ]:
            with self.subTest(step=step):
                self.assertTrue(run_step('cloudwatch', 'log-groups-metric-filters', step)['verified'])
        mock_logs.put_retention_policy.assert_called_once_with(logGroupName='/aws/floci/app-logs', retentionInDays=30)
        events = mock_logs.put_log_events.call_args.kwargs['logEvents']
        self.assertEqual(sum('HTTP 500' in event['message'] for event in events), 2)
        self.assertTrue(status('cloudwatch', 'log-groups-metric-filters')['complete'])

        self.assertTrue(reset('cloudwatch', 'metric-alarms')['reset'])

    @patch('dashboard.labs.cloudwatch_labs.ALARM_WAIT_SECONDS', 0)
    @patch('dashboard.labs.cloudwatch_labs.client')
    def test_cloudwatch_alarm_step_fails_until_cloudwatch_evaluates_alarm(self, client_mock):
        cache.clear()
        mock_cw = MagicMock()
        client_mock.return_value = mock_cw
        mock_cw.describe_alarms.return_value = {
            'MetricAlarms': [{'AlarmName': 'lab-order-error-alarm', 'Threshold': 10, 'StateValue': 'OK'}],
        }

        from dashboard.labs.cloudwatch_labs import _wait_for_alarm_state, step_describe_alarms

        with patch('dashboard.labs.cloudwatch_labs._wait_for_alarm_state', lambda expected: _wait_for_alarm_state(expected, 0)):
            result = step_describe_alarms()

        self.assertFalse(result['verified'])
        self.assertIn('is OK', result['verification']['message'])


class MultiSdkSnippetsTests(SimpleTestCase):
    def test_generate_boto3_snippet_for_simple_command(self):
        from dashboard.labs.snippets import generate_boto3_snippet

        snippet = generate_boto3_snippet('aws s3api create-bucket --bucket demo-bucket')
        self.assertIn("import boto3", snippet)
        self.assertIn("s3 = boto3.client('s3')", snippet)
        self.assertIn("create_bucket", snippet)
        self.assertIn("Bucket='demo-bucket'", snippet)

    def test_generate_terraform_snippet_for_s3_and_sqs(self):
        from dashboard.labs.snippets import generate_terraform_snippet

        s3_tf = generate_terraform_snippet('aws s3api create-bucket --bucket orders-bucket')
        self.assertIn('resource "aws_s3_bucket" "lab_bucket"', s3_tf)
        self.assertIn('bucket        = "orders-bucket"', s3_tf)

        sqs_tf = generate_terraform_snippet('aws sqs create-queue --queue-name orders-queue.fifo')
        self.assertIn('resource "aws_sqs_queue" "lab_queue"', sqs_tf)
        self.assertIn('fifo_queue                  = true', sqs_tf)

    def test_get_step_snippets_returns_all_three_targets(self):
        from dashboard.labs.snippets import get_step_snippets

        step = {
            'key': 'create-user',
            'command': 'aws iam create-user --user-name Alice',
        }
        snippets = get_step_snippets(step, 'iam')
        self.assertEqual(snippets['cli'], 'aws iam create-user --user-name Alice')
        self.assertIn('iam = boto3.client(\'iam\')', snippets['boto3'])
        self.assertIn('resource "aws_iam_user"', snippets['terraform'])
