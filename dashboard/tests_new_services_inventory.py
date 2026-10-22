from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .aws import amazonmq_inventory, batch_inventory, codepipeline_inventory, docdb_inventory, ec2_inventory, elasticbeanstalk_inventory, emr_inventory, iot_inventory, list_resources, memorydb_inventory, rdsdata_inventory, s3vectors_inventory, wafv2_inventory
from .services import get_service


def service_model(*operations):
    return SimpleNamespace(service_model=SimpleNamespace(operation_names=list(operations)))


class NewServiceInventoryPageTests(SimpleTestCase):

    def test_new_services_are_registered_with_correct_maturity(self):
        cases = [
            ('batch', 'Compute', 'read_only_inspector'),
            ('amazonmq', 'Application Integration', 'interactive_workbench'),
            ('docdb', 'Database', 'interactive_workbench'),
            ('emr', 'Analytics', 'read_only_inspector'),
            ('memorydb', 'Database', 'interactive_workbench'),
            ('codepipeline', 'Developer Tools', 'interactive_workbench'),
            ('s3vectors', 'Storage', 'interactive_workbench'),
            ('iot', 'Application Integration', 'interactive_workbench'),
            ('rdsdata', 'Database', 'interactive_workbench'),
            ('wafv2', 'Security', 'interactive_workbench'),
        ]

        for key, category, maturity in cases:
            with self.subTest(service=key):
                service = get_service(key)

                self.assertIsNotNone(service)
                self.assertEqual(service.maturity, maturity)
                self.assertEqual(service.api_path, None)
                self.assertEqual(service.category, category)

    @patch('dashboard.views.amazonmq_inventory')
    @patch('dashboard.views.batch_inventory')
    @patch('dashboard.views.docdb_inventory')
    @patch('dashboard.views.memorydb_inventory')
    @patch('dashboard.views.codepipeline_inventory')
    @patch('dashboard.views.s3vectors_inventory')
    @patch('dashboard.views.iot_inventory')
    @patch('dashboard.views.elasticbeanstalk_inventory')
    @patch('dashboard.views.emr_inventory')
    @patch('dashboard.views.rdsdata_inventory')
    @patch('dashboard.views.wafv2_inventory')
    def test_new_service_api_routes_return_inventory(self, wafv2, rdsdata, emr, elasticbeanstalk, iot, s3vectors, codepipeline, memorydb, docdb, batch, amazonmq):
        batch.return_value = {'summary': {'job_queues': 1}, 'job_queues': [{'jobQueueName': 'local'}]}
        amazonmq.return_value = {'summary': {'brokers': 1}, 'brokers': [{'name': 'broker'}]}
        docdb.return_value = {'summary': {'clusters': 1}, 'clusters': [{'DBClusterIdentifier': 'docs'}]}
        memorydb.return_value = {'summary': {'clusters': 1}, 'clusters': [{'Name': 'cache'}]}
        codepipeline.return_value = {'summary': {'pipelines': 1}, 'pipelines': [{'name': 'release'}]}
        s3vectors.return_value = {'summary': {'vector_buckets': 1}, 'vector_buckets': [{'vectorBucketName': 'vectors'}]}
        iot.return_value = {'summary': {'things': 1}, 'things': [{'thingName': 'sensor'}]}
        elasticbeanstalk.return_value = {'summary': {'applications': 1}, 'applications': [{'ApplicationName': 'web'}]}
        emr.return_value = {'summary': {'clusters': 1}, 'clusters': [{'name': 'analytics'}]}
        rdsdata.return_value = {'summary': {'available_sdk_operations': 5}, 'statement_operations': [{'name': 'ExecuteStatement'}]}
        wafv2.return_value = {'summary': {'web_acls': 1}, 'web_acls': [{'Name': 'local-acl'}]}

        cases = [
            ('dashboard:batch', 'job_queues', 1),
            ('dashboard:amazonmq', 'brokers', 1),
            ('dashboard:docdb', 'clusters', 1),
            ('dashboard:memorydb', 'clusters', 1),
            ('dashboard:codepipeline', 'pipelines', 1),
            ('dashboard:s3vectors', 'vector_buckets', 1),
            ('dashboard:iot', 'things', 1),
            ('dashboard:elasticbeanstalk', 'applications', 1),
            ('dashboard:emr', 'clusters', 1),
            ('dashboard:rdsdata', 'available_sdk_operations', 5),
            ('dashboard:wafv2', 'web_acls', 1),
        ]

        for route_name, summary_key, expected in cases:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['summary'][summary_key], expected)


class NewServiceInventoryHelperTests(SimpleTestCase):
    @patch('dashboard.aws.FlociClientFactory')
    def test_amazonmq_inventory_summarizes_brokers(self, factory_mock):
        mq = MagicMock()
        mq.meta = service_model('ListBrokers', 'DescribeBroker', 'ListUsers', 'ListConfigurations')
        mq.can_paginate.return_value = False
        mq.list_brokers.return_value = {
            'BrokerSummaries': [{'BrokerId': 'broker-1', 'BrokerName': 'orders', 'BrokerState': 'RUNNING'}],
        }
        mq.describe_broker.return_value = {
            'BrokerId': 'broker-1',
            'BrokerName': 'orders',
            'BrokerArn': 'arn:aws:amazonmq:us-east-1:000000000000:broker:orders:broker-1',
            'EngineType': 'RabbitMQ',
            'BrokerState': 'RUNNING',
        }
        mq.list_users.return_value = {'Users': [{'Username': 'app'}]}
        mq.list_configurations.return_value = {'Configurations': [{'Name': 'default'}]}
        factory_mock.return_value.client.return_value = mq

        result = amazonmq_inventory()

        self.assertEqual(result['summary']['brokers'], 1)
        self.assertEqual(result['summary']['running_brokers'], 1)
        self.assertEqual(result['summary']['users'], 1)
        self.assertEqual(result['brokers'][0]['name'], 'orders')

    @patch('dashboard.aws.FlociClientFactory')
    def test_batch_inventory_summarizes_batch_resources(self, factory_mock):
        batch = MagicMock()
        batch.meta = service_model(
            'DescribeComputeEnvironments',
            'DescribeJobQueues',
            'DescribeJobDefinitions',
            'ListJobs',
        )
        batch.can_paginate.return_value = False
        batch.describe_compute_environments.return_value = {
            'computeEnvironments': [{'computeEnvironmentName': 'local-env', 'status': 'VALID'}],
        }
        batch.describe_job_queues.return_value = {
            'jobQueues': [{'jobQueueName': 'local-queue', 'status': 'VALID'}],
        }
        batch.describe_job_definitions.return_value = {
            'jobDefinitions': [{'jobDefinitionName': 'worker', 'status': 'ACTIVE'}],
        }
        batch.list_jobs.return_value = {'jobSummaryList': [{'jobId': 'job-1', 'jobName': 'worker', 'status': 'RUNNING'}]}
        factory_mock.return_value.client.return_value = batch

        result = batch_inventory()

        self.assertEqual(result['summary']['compute_environments'], 1)
        self.assertEqual(result['summary']['job_queues'], 1)
        self.assertEqual(result['summary']['job_definitions'], 1)
        self.assertGreaterEqual(result['summary']['sampled_jobs'], 1)

    @patch('dashboard.aws.FlociClientFactory')
    def test_docdb_inventory_summarizes_clusters_and_instances(self, factory_mock):
        docdb = MagicMock()
        docdb.meta = service_model('DescribeDBClusters', 'DescribeDBInstances')
        docdb.can_paginate.return_value = False
        docdb.describe_db_clusters.return_value = {
            'DBClusters': [
                {'DBClusterIdentifier': 'docs', 'Status': 'available', 'Engine': 'docdb'},
                {'DBClusterIdentifier': 'graph', 'Status': 'available', 'Engine': 'neptune'},
            ],
        }
        docdb.describe_db_instances.return_value = {
            'DBInstances': [
                {'DBInstanceIdentifier': 'docs-1', 'DBInstanceStatus': 'available', 'Engine': 'docdb'},
                {'DBInstanceIdentifier': 'pg-1', 'DBInstanceStatus': 'available', 'Engine': 'postgres'},
            ],
        }
        factory_mock.return_value.client.return_value = docdb

        result = docdb_inventory()

        self.assertEqual(result['summary']['clusters'], 1)
        self.assertEqual(result['summary']['instances'], 1)
        self.assertEqual(result['clusters'][0]['DBClusterIdentifier'], 'docs')

    @patch('dashboard.aws.FlociClientFactory')
    def test_memorydb_inventory_summarizes_core_resources(self, factory_mock):
        memorydb = MagicMock()
        memorydb.meta = service_model('DescribeClusters', 'DescribeUsers', 'DescribeACLs', 'DescribeSubnetGroups', 'DescribeParameterGroups', 'DescribeSnapshots')
        memorydb.can_paginate.return_value = False
        memorydb.describe_clusters.return_value = {'Clusters': [{'Name': 'cache', 'Status': 'available'}]}
        memorydb.describe_users.return_value = {'Users': [{'Name': 'app'}]}
        memorydb.describe_acls.return_value = {'ACLs': [{'Name': 'open'}]}
        memorydb.describe_subnet_groups.return_value = {'SubnetGroups': [{'Name': 'subnets'}]}
        memorydb.describe_parameter_groups.return_value = {'ParameterGroups': [{'Name': 'params'}]}
        memorydb.describe_snapshots.return_value = {'Snapshots': [{'Name': 'snapshot'}]}
        factory_mock.return_value.client.return_value = memorydb

        result = memorydb_inventory()

        self.assertEqual(result['summary']['clusters'], 1)
        self.assertEqual(result['summary']['users'], 1)
        self.assertEqual(result['summary']['acls'], 1)
        self.assertEqual(result['clusters'][0]['Name'], 'cache')

    @patch('dashboard.aws.FlociClientFactory')
    def test_codepipeline_inventory_expands_pipeline_state(self, factory_mock):
        codepipeline = MagicMock()
        codepipeline.meta = service_model('ListPipelines', 'GetPipeline', 'GetPipelineState', 'ListPipelineExecutions', 'ListWebhooks', 'ListActionTypes')
        codepipeline.can_paginate.return_value = False
        codepipeline.list_pipelines.return_value = {'pipelines': [{'name': 'release', 'version': 1}]}
        codepipeline.get_pipeline.return_value = {'pipeline': {'version': 1, 'pipelineType': 'V2', 'stages': [{'name': 'Source'}]}}
        codepipeline.get_pipeline_state.return_value = {'stageStates': [{'stageName': 'Source'}]}
        codepipeline.list_pipeline_executions.return_value = {'pipelineExecutionSummaries': [{'pipelineExecutionId': 'exec-1'}]}
        codepipeline.list_webhooks.return_value = {'webhooks': [{'name': 'hook'}]}
        codepipeline.list_action_types.return_value = {'actionTypes': [{'id': {'category': 'Source'}}]}
        factory_mock.return_value.client.return_value = codepipeline

        result = codepipeline_inventory()

        self.assertEqual(result['summary']['pipelines'], 1)
        self.assertEqual(result['summary']['sampled_executions'], 1)
        self.assertEqual(result['pipelines'][0]['name'], 'release')

    @patch('dashboard.aws.FlociClientFactory')
    def test_s3vectors_inventory_summarizes_vector_buckets_and_indexes(self, factory_mock):
        s3vectors = MagicMock()
        s3vectors.meta = service_model('ListVectorBuckets', 'ListIndexes')
        s3vectors.can_paginate.return_value = False
        s3vectors.list_vector_buckets.return_value = {'vectorBuckets': [{'vectorBucketName': 'vectors'}]}
        s3vectors.list_indexes.return_value = {'indexes': [{'indexName': 'embeddings'}]}
        factory_mock.return_value.client.return_value = s3vectors

        result = s3vectors_inventory()

        self.assertEqual(result['summary']['vector_buckets'], 1)
        self.assertEqual(result['summary']['indexes'], 1)
        self.assertEqual(result['indexes'][0]['vectorBucketName'], 'vectors')

    @patch('dashboard.aws.FlociClientFactory')
    def test_s3vectors_global_resources_skip_unnamed_buckets_before_index_lookup(self, factory_mock):
        s3vectors = MagicMock()
        s3vectors.meta = service_model('ListVectorBuckets', 'ListIndexes')
        s3vectors.can_paginate.return_value = False
        s3vectors.list_vector_buckets.return_value = {
            'vectorBuckets': [
                {'vectorBucketArn': 'arn:missing-name'},
                {'vectorBucketName': 'vectors', 'vectorBucketArn': 'arn:vectors'},
            ],
        }
        s3vectors.list_indexes.return_value = {'indexes': [{'indexName': 'embeddings'}]}
        factory_mock.return_value.client.return_value = s3vectors

        result = list_resources({'s3vectors'})

        s3vectors.list_indexes.assert_called_once_with(vectorBucketName='vectors')
        self.assertEqual(result[0].name, 's3vectors-resources')
        self.assertEqual([item['type'] for item in result[0].items], ['vector_bucket', 'vector_bucket', 'index'])
        self.assertEqual(result[0].items[-1]['vector_bucket'], 'vectors')

    @patch('dashboard.aws.FlociClientFactory')
    def test_iot_inventory_summarizes_core_resources(self, factory_mock):
        iot = MagicMock()
        iot.meta = service_model('ListThings', 'ListThingTypes', 'ListPolicies', 'ListCertificates', 'ListTopicRules', 'ListJobs', 'ListRoleAliases')
        iot.can_paginate.return_value = False
        iot.list_things.return_value = {'things': [{'thingName': 'sensor'}]}
        iot.list_thing_types.return_value = {'thingTypes': [{'thingTypeName': 'meter'}]}
        iot.list_policies.return_value = {'policies': [{'policyName': 'allow'}]}
        iot.list_certificates.return_value = {'certificates': [{'certificateId': 'cert-1'}]}
        iot.list_topic_rules.return_value = {'rules': [{'ruleName': 'route'}]}
        iot.list_jobs.return_value = {'jobs': [{'jobId': 'job-1'}]}
        iot.list_role_aliases.return_value = {'roleAliases': ['local-role']}
        factory_mock.return_value.client.return_value = iot

        result = iot_inventory()

        self.assertEqual(result['summary']['things'], 1)
        self.assertEqual(result['summary']['policies'], 1)
        self.assertEqual(result['things'][0]['thingName'], 'sensor')

    @patch('dashboard.aws.FlociClientFactory')
    def test_elasticbeanstalk_inventory_summarizes_applications_and_environments(self, factory_mock):
        beanstalk = MagicMock()
        beanstalk.meta = service_model('DescribeApplications', 'DescribeEnvironments', 'DescribeApplicationVersions', 'DescribePlatforms', 'ListAvailableSolutionStacks')
        beanstalk.can_paginate.return_value = False
        beanstalk.describe_applications.return_value = {'Applications': [{'ApplicationName': 'web'}]}
        beanstalk.describe_environments.return_value = {'Environments': [{'EnvironmentName': 'dev'}]}
        beanstalk.describe_application_versions.return_value = {'ApplicationVersions': [{'VersionLabel': 'v1'}]}
        beanstalk.describe_platforms.return_value = {'PlatformSummaryList': [{'PlatformArn': 'arn:platform'}]}
        beanstalk.list_available_solution_stacks.return_value = {'SolutionStacks': ['64bit Amazon Linux']}
        factory_mock.return_value.client.return_value = beanstalk

        result = elasticbeanstalk_inventory()

        self.assertEqual(result['summary']['applications'], 1)
        self.assertEqual(result['summary']['environments'], 1)
        self.assertEqual(result['applications'][0]['ApplicationName'], 'web')

    @patch('dashboard.aws.FlociClientFactory')
    def test_ec2_inventory_includes_network_acls(self, factory_mock):
        ec2 = MagicMock()
        ec2.describe_instances.return_value = {'Reservations': []}
        ec2.describe_vpcs.return_value = {'Vpcs': []}
        ec2.describe_subnets.return_value = {'Subnets': []}
        ec2.describe_security_groups.return_value = {'SecurityGroups': []}
        ec2.describe_security_group_rules.return_value = {'SecurityGroupRules': []}
        ec2.describe_key_pairs.return_value = {'KeyPairs': []}
        ec2.describe_images.return_value = {'Images': []}
        ec2.describe_tags.return_value = {'Tags': []}
        ec2.describe_internet_gateways.return_value = {'InternetGateways': []}
        ec2.describe_route_tables.return_value = {'RouteTables': []}
        ec2.describe_network_acls.return_value = {'NetworkAcls': [{'NetworkAclId': 'acl-1'}]}
        ec2.describe_addresses.return_value = {'Addresses': []}
        ec2.describe_availability_zones.return_value = {'AvailabilityZones': []}
        ec2.describe_regions.return_value = {'Regions': []}
        ec2.describe_account_attributes.return_value = {'AccountAttributes': []}
        ec2.describe_instance_types.return_value = {'InstanceTypes': []}
        ec2.get_paginator.return_value.paginate.return_value.build_full_result.return_value = {
            'VpcEndpoints': [],
            'IamInstanceProfileAssociations': [],
        }
        factory_mock.return_value.client.return_value = ec2

        result = ec2_inventory()

        self.assertEqual(result['summary']['network_acls'], 1)
        self.assertEqual(result['network_acls'][0]['NetworkAclId'], 'acl-1')

    @patch('dashboard.aws.FlociClientFactory')
    def test_emr_inventory_summarizes_clusters(self, factory_mock):
        emr = MagicMock()
        emr.meta = service_model('ListClusters', 'DescribeCluster', 'ListInstanceGroups', 'ListSteps')
        emr.can_paginate.return_value = False
        emr.list_clusters.return_value = {
            'Clusters': [{'Id': 'j-1', 'Name': 'analytics', 'Status': {'State': 'WAITING'}}],
        }
        emr.describe_cluster.return_value = {
            'Cluster': {'Id': 'j-1', 'Name': 'analytics', 'ReleaseLabel': 'emr-7.0.0'},
        }
        emr.list_instance_groups.return_value = {'InstanceGroups': [{'Id': 'ig-1'}]}
        emr.list_steps.return_value = {'Steps': [{'Id': 's-1'}]}
        factory_mock.return_value.client.return_value = emr

        result = emr_inventory()

        self.assertEqual(result['summary']['clusters'], 1)
        self.assertEqual(result['summary']['instance_groups'], 1)
        self.assertEqual(result['summary']['steps'], 1)
        self.assertEqual(result['clusters'][0]['release_label'], 'emr-7.0.0')

    @patch('dashboard.aws.FlociClientFactory')
    def test_wafv2_inventory_checks_regional_and_cloudfront_scopes(self, factory_mock):
        wafv2 = MagicMock()
        wafv2.meta = service_model('ListWebACLs', 'ListRuleGroups', 'ListIPSets', 'ListRegexPatternSets')
        wafv2.can_paginate.return_value = False
        wafv2.list_web_acls.return_value = {'WebACLs': [{'Name': 'local-acl', 'Id': 'acl-1'}]}
        wafv2.list_rule_groups.return_value = {'RuleGroups': [{'Name': 'rules', 'Id': 'rg-1'}]}
        wafv2.list_ip_sets.return_value = {'IPSets': []}
        wafv2.list_regex_pattern_sets.return_value = {'RegexPatternSets': []}
        factory_mock.return_value.client.return_value = wafv2

        result = wafv2_inventory()

        self.assertEqual(result['summary']['web_acls'], 2)
        self.assertEqual(result['summary']['rule_groups'], 2)
        self.assertEqual({item['Scope'] for item in result['web_acls']}, {'REGIONAL', 'CLOUDFRONT'})

    @patch('dashboard.aws.FlociClientFactory')
    def test_rdsdata_inventory_surfaces_request_oriented_operations(self, factory_mock):
        rdsdata = MagicMock()
        rdsdata.meta = service_model(
            'ExecuteStatement',
            'BatchExecuteStatement',
            'BeginTransaction',
            'CommitTransaction',
            'RollbackTransaction',
        )
        factory_mock.return_value.client.return_value = rdsdata

        result = rdsdata_inventory()

        self.assertEqual(result['summary']['available_sdk_operations'], 5)
        self.assertIn('ExecuteStatement', result['supported_from_sdk'])
        self.assertTrue(any(field['name'] == 'resourceArn' for field in result['request_fields']))
