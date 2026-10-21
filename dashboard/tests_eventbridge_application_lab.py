import importlib
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from dashboard.labs import lab_status, labs_for_service
from dashboard.labs import eventbridge_application as application


class EventBridgeApplicationLabTests(SimpleTestCase):
    def tearDown(self):
        cache.clear()

    def test_eventbridge_lab_is_registered_with_failure_experiments(self):
        labs = labs_for_service('eventbridge')
        self.assertEqual([lab['key'] for lab in labs], ['application-spine'])
        step_keys = [step['key'] for step in labs[0]['steps']]
        self.assertEqual(step_keys[-3:], ['malformed-event', 'missing-permission', 'disabled-rule'])


    def test_runner_facade_dispatches_to_eventbridge_module(self):
        module = importlib.import_module(application.run_step.__module__)
        runner = MagicMock(return_value={'verified': True})
        with patch.dict(module.RUNNERS, {'happy-path': runner}):
            result = module.run_step('happy-path')
        self.assertTrue(result['verified'])
        runner.assert_called_once_with()

    def test_status_tracks_all_reload_safe_milestones(self):
        for key in ['queues', 'roles', 'functions', 'rules', 'targets', 'api-id', 'permissions', 'happy-path', 'malformed', 'missing-permission', 'disabled-rule']:
            application.marker(key)
        result = lab_status('eventbridge', 'application-spine')
        self.assertTrue(result['complete'])
        self.assertTrue(result['steps']['disabled-rule']['verified'])

    def test_targets_include_transformer_retry_and_dead_letter_shapes(self):
        module = importlib.import_module(application.put_targets.__module__)
        events = MagicMock()
        events.put_targets.return_value = {'FailedEntryCount': 0, 'FailedEntries': []}
        with patch.object(module, 'client', return_value=events):
            result = module.put_targets()
        self.assertTrue(result['verified'])
        processing = events.put_targets.call_args_list[0].kwargs['Targets'][0]
        self.assertEqual(processing['InputTransformer'], application.PROCESSING_TRANSFORMER)
        self.assertEqual(processing['RetryPolicy']['MaximumRetryAttempts'], 2)
        self.assertEqual(processing['DeadLetterConfig']['Arn'], application.QUEUE_ARNS[application.DLQ])

    def test_reset_removes_current_and_legacy_named_resources(self):
        module = importlib.import_module(application.reset.__module__)
        clients = {name: MagicMock() for name in ['events', 'lambda', 'apigatewayv2', 'logs', 'iam', 'sqs']}
        clients['apigatewayv2'].get_apis.return_value = {
            'Items': [
                {'Name': application.API_NAME, 'ApiId': 'api-current'},
                {'Name': application.LEGACY_API_NAME, 'ApiId': 'api-legacy'},
            ]
        }
        clients['sqs'].get_queue_url.side_effect = lambda QueueName: {'QueueUrl': f'http://sqs/{QueueName}'}

        with patch.object(module, 'client', side_effect=lambda name: clients[name]):
            result = module.reset()

        self.assertTrue(result['json']['legacy_names_removed'])
        self.assertEqual(
            {call.kwargs['Name'] for call in clients['events'].delete_event_bus.call_args_list},
            {application.BUS, application.LEGACY_BUS},
        )
        self.assertEqual(
            {call.kwargs['FunctionName'] for call in clients['lambda'].delete_function.call_args_list},
            {
                application.PRODUCER_FUNCTION,
                application.NOTIFIER_FUNCTION,
                application.LEGACY_PRODUCER_FUNCTION,
                application.LEGACY_NOTIFIER_FUNCTION,
            },
        )
        self.assertEqual(
            {call.kwargs['QueueUrl'] for call in clients['sqs'].delete_queue.call_args_list},
            {
                f'http://sqs/{name}'
                for name in [
                    application.PROCESSING_QUEUE,
                    application.AUDIT_QUEUE,
                    application.DLQ,
                    application.LEGACY_PROCESSING_QUEUE,
                    application.LEGACY_AUDIT_QUEUE,
                    application.LEGACY_DLQ,
                ]
            },
        )
