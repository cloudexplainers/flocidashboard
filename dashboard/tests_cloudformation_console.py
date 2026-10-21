import json
from unittest.mock import patch, call
from django.test import SimpleTestCase


class CloudFormationWorkspaceTests(SimpleTestCase):
    @patch('dashboard.cloudformation_api._client')
    def test_change_set_review_collects_all_change_pages(self, client_mock):
        client = client_mock.return_value
        client.describe_change_set.side_effect = [
            {'ChangeSetName': 'next', 'Status': 'CREATE_COMPLETE', 'ExecutionStatus': 'AVAILABLE', 'Changes': [{'ResourceChange': {'LogicalResourceId': 'Queue', 'Replacement': 'Conditional'}}], 'NextToken': 'page-two'},
            {'Changes': [{'ResourceChange': {'LogicalResourceId': 'Bucket', 'Action': 'Add'}}]},
        ]
        response = self.client.get('/api/cloudformation/stacks/orders/change-sets/next/')
        self.assertEqual(response.status_code, 200)
        result = response.json()['change_set']
        self.assertEqual(len(result['Changes']), 2)
        self.assertNotIn('NextToken', result)
        client.describe_change_set.assert_has_calls([
            call(StackName='orders', ChangeSetName='next'),
            call(StackName='orders', ChangeSetName='next', NextToken='page-two'),
        ])

    @patch('dashboard.cloudformation_api._client')
    def test_proposed_template_parameters_and_capabilities_reach_sdk(self, client_mock):
        client = client_mock.return_value
        client.create_change_set.return_value = {'Id': 'change-id', 'StackId': 'stack-id'}
        proposed = '{"Resources":{"Queue":{"Type":"AWS::SQS::Queue"}}}'
        response = self.client.post('/api/cloudformation/change-sets/', data=json.dumps({
            'stack_name': 'orders', 'change_set_name': 'next', 'template_body': proposed,
            'parameters': [{'ParameterKey': 'Count', 'ParameterValue': 0}, {'ParameterKey': 'Secret', 'UsePreviousValue': True}],
            'capabilities': ['CAPABILITY_NAMED_IAM'],
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.create_change_set.assert_called_once_with(StackName='orders', ChangeSetName='next', TemplateBody=proposed, ChangeSetType='UPDATE', Parameters=[{'ParameterKey': 'Count', 'ParameterValue': '0'}, {'ParameterKey': 'Secret', 'UsePreviousValue': True}], Capabilities=['CAPABILITY_NAMED_IAM'])

    @patch('dashboard.cloudformation_api._client')
    def test_stack_creation_does_not_add_unrequested_capabilities(self, client_mock):
        client = client_mock.return_value
        client.create_stack.return_value = {'StackId': 'stack-id'}
        response = self.client.post('/api/cloudformation/stacks/', data=json.dumps({'stack_name': 'orders', 'template_body': 'Resources: {}', 'parameters': {'Count': 0}, 'capabilities': [], 'disable_rollback': False}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.create_stack.assert_called_once_with(StackName='orders', TemplateBody='Resources: {}', DisableRollback=False, Parameters=[{'ParameterKey': 'Count', 'ParameterValue': '0'}])

    @patch('dashboard.cloudformation_api._client')
    def test_change_set_sdk_errors_are_not_success_responses(self, client_mock):
        from botocore.exceptions import ClientError
        client_mock.return_value.delete_change_set.side_effect = ClientError({'Error': {'Code': 'InvalidChangeSetStatus', 'Message': 'Change set is still executing'}}, 'DeleteChangeSet')
        response = self.client.delete('/api/cloudformation/stacks/orders/change-sets/next/')
        self.assertGreaterEqual(response.status_code, 400)
        self.assertIn('Change set is still executing', response.json()['error'])

    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_reads_nonpaginated_stack_resources_directly(self, factory_mock, paginate_mock):
        from .aws import cloudformation_inventory
        client = factory_mock.return_value.client.return_value
        client.meta.service_model.operation_names = []
        client.can_paginate.return_value = False
        client.describe_stacks.return_value = {'Stacks': [{'StackName': 'orders'}]}
        client.describe_stack_resources.return_value = {'StackResources': [{'LogicalResourceId': 'Queue', 'ResourceStatus': 'CREATE_COMPLETE'}]}
        client.get_template.return_value = {'TemplateBody': 'Resources: {}'}
        client.get_stack_policy.return_value = {}
        paginate_mock.side_effect = lambda _client, operation, _key, **kwargs: [{'StackName': 'orders'}] if operation == 'list_stacks' else []
        result = cloudformation_inventory()
        self.assertEqual(result['stacks'][0]['resources'][0]['LogicalResourceId'], 'Queue')
        client.describe_stack_resources.assert_called_once_with(StackName='orders')
        self.assertNotIn('describe_stack_resources', [item.args[1] for item in paginate_mock.call_args_list])
