"""AWS certification exam guides, lab tags, and concept cards.

Each lab is tagged with the exam task statements it practices. Objectives a
local emulator cannot teach hands-on (pricing, support plans, the cloud
adoption framework, and so on) get short concept cards instead, so every task
statement in a guide points at something to study.

The mapping and its reasoning live in docs/certification-coverage.md. When AWS
revises a guide, update EXAM_GUIDES; the tests then flag tags and cards that
point at task statements that no longer exist.
"""

from __future__ import annotations

from typing import Any

CLF = 'CLF-C02'
SAA = 'SAA-C03'

EXAM_GUIDES: dict[str, dict[str, Any]] = {
    CLF: {
        'code': CLF,
        'title': 'AWS Certified Cloud Practitioner',
        'short_title': 'Cloud Practitioner',
        'level': 'foundational',
        'guide_url': 'https://docs.aws.amazon.com/aws-certification/latest/cloud-practitioner-02/cloud-practitioner-02.html',
        'checked': '2026-10-22',
        'passing_score': 700,
        'domains': [
            {
                'id': '1',
                'title': 'Cloud Concepts',
                'weight': 24,
                'tasks': [
                    {'id': '1.1', 'title': 'Define the benefits of the AWS Cloud.'},
                    {'id': '1.2', 'title': 'Identify design principles of the AWS Cloud.'},
                    {'id': '1.3', 'title': 'Understand the benefits of and strategies for migration to the AWS Cloud.'},
                    {'id': '1.4', 'title': 'Understand concepts of cloud economics.'},
                ],
            },
            {
                'id': '2',
                'title': 'Security and Compliance',
                'weight': 30,
                'tasks': [
                    {'id': '2.1', 'title': 'Understand the AWS shared responsibility model.'},
                    {'id': '2.2', 'title': 'Understand AWS Cloud security, governance, and compliance concepts.'},
                    {'id': '2.3', 'title': 'Identify AWS access management capabilities.'},
                    {'id': '2.4', 'title': 'Identify components and resources for security.'},
                ],
            },
            {
                'id': '3',
                'title': 'Cloud Technology and Services',
                'weight': 34,
                'tasks': [
                    {'id': '3.1', 'title': 'Define methods of deploying and operating in the AWS Cloud.'},
                    {'id': '3.2', 'title': 'Define the AWS global infrastructure.'},
                    {'id': '3.3', 'title': 'Identify AWS compute services.'},
                    {'id': '3.4', 'title': 'Identify AWS database services.'},
                    {'id': '3.5', 'title': 'Identify AWS network services.'},
                    {'id': '3.6', 'title': 'Identify AWS storage services.'},
                    {'id': '3.7', 'title': 'Identify AWS artificial intelligence and machine learning (AI/ML) services and analytics services.'},
                    {'id': '3.8', 'title': 'Identify services from other in-scope AWS service categories.'},
                ],
            },
            {
                'id': '4',
                'title': 'Billing, Pricing, and Support',
                'weight': 12,
                'tasks': [
                    {'id': '4.1', 'title': 'Compare AWS pricing models.'},
                    {'id': '4.2', 'title': 'Understand resources for billing, budget, and cost management.'},
                    {'id': '4.3', 'title': 'Identify AWS technical resources and AWS Support options.'},
                ],
            },
        ],
    },
    SAA: {
        'code': SAA,
        'title': 'AWS Certified Solutions Architect – Associate',
        'short_title': 'Solutions Architect – Associate',
        'level': 'associate',
        'guide_url': 'https://docs.aws.amazon.com/aws-certification/latest/solutions-architect-associate-03/solutions-architect-associate-03.html',
        'checked': '2026-10-22',
        'passing_score': 720,
        'domains': [
            {
                'id': '1',
                'title': 'Design Secure Architectures',
                'weight': 30,
                'tasks': [
                    {'id': '1.1', 'title': 'Design secure access to AWS resources.'},
                    {'id': '1.2', 'title': 'Design secure workloads and applications.'},
                    {'id': '1.3', 'title': 'Determine appropriate data security controls.'},
                ],
            },
            {
                'id': '2',
                'title': 'Design Resilient Architectures',
                'weight': 26,
                'tasks': [
                    {'id': '2.1', 'title': 'Design scalable and loosely coupled architectures.'},
                    {'id': '2.2', 'title': 'Design highly available and/or fault-tolerant architectures.'},
                ],
            },
            {
                'id': '3',
                'title': 'Design High-Performing Architectures',
                'weight': 24,
                'tasks': [
                    {'id': '3.1', 'title': 'Determine high-performing and/or scalable storage solutions.'},
                    {'id': '3.2', 'title': 'Design high-performing and elastic compute solutions.'},
                    {'id': '3.3', 'title': 'Determine high-performing database solutions.'},
                    {'id': '3.4', 'title': 'Determine high-performing and/or scalable network architectures.'},
                    {'id': '3.5', 'title': 'Determine high-performing data ingestion and transformation solutions.'},
                ],
            },
            {
                'id': '4',
                'title': 'Design Cost-Optimized Architectures',
                'weight': 20,
                'tasks': [
                    {'id': '4.1', 'title': 'Design cost-optimized storage solutions.'},
                    {'id': '4.2', 'title': 'Design cost-optimized compute solutions.'},
                    {'id': '4.3', 'title': 'Design cost-optimized database solutions.'},
                    {'id': '4.4', 'title': 'Design cost-optimized network architectures.'},
                ],
            },
        ],
    },
}


def _tags(clf: str = '', saa: str = '') -> list[dict[str, Any]]:
    """Build tags from space-separated task IDs; the first ID per exam is primary."""
    tags = []
    for exam, tasks in ((CLF, clf), (SAA, saa)):
        for index, task in enumerate(tasks.split()):
            tags.append({'exam': exam, 'task': task, 'primary': index == 0})
    return tags


# Every registered lab appears here, even when it maps to no CLF or SAA task
# (those mainly serve the Developer or CloudOps tracks).
LAB_EXAM_TAGS: dict[tuple[str, str], list[dict[str, Any]]] = {
    # Level 1
    ('iam', 'create-admin-user'): _tags(clf='2.3', saa='1.1'),
    ('iam', 'create-user-alice'): _tags(clf='2.3', saa='1.1'),
    ('iam', 'attach-policy-alice'): _tags(clf='2.3', saa='1.1'),
    ('iam', 'access-key-alice'): _tags(clf='2.3', saa='1.1'),
    ('iam', 'group-membership-alice'): _tags(clf='2.3', saa='1.1'),
    ('iam', 'group-policy-floci-developers'): _tags(clf='2.3', saa='1.1'),
    ('iam', 'inline-policy-alice'): _tags(clf='2.3', saa='1.1'),
    ('s3', 'create-bucket'): _tags(clf='3.6', saa='3.1'),
    ('s3', 'object-workflow'): _tags(clf='3.6 3.1', saa='3.1'),
    ('s3', 'prefix-copy'): _tags(clf='3.6', saa='3.1'),
    ('s3', 'metadata-tags'): _tags(clf='3.6', saa='1.3'),
    ('sqs', 'create-queue'): _tags(clf='3.8', saa='2.1'),
    ('sqs', 'message-lifecycle'): _tags(clf='3.8', saa='2.1'),
    ('dynamodb', 'crud-query'): _tags(clf='3.4', saa='3.3'),
    ('lambda', 'create-invoke-logs'): _tags(clf='3.3', saa='2.1'),
    # Level 2
    ('iam', 'role-trust-policy'): _tags(clf='2.3', saa='1.1'),
    ('iam', 'sts-session-policy'): _tags(clf='2.3', saa='1.1'),
    ('iam', 'ec2-instance-profile'): _tags(clf='2.3', saa='1.1 1.2'),
    ('s3', 'version-recovery'): _tags(clf='3.6', saa='1.3 2.2'),
    ('s3', 'default-encryption'): _tags(clf='2.2', saa='1.3'),
    ('s3', 'bucket-security'): _tags(clf='2.2 2.3', saa='1.1 1.3'),
    ('s3', 'lifecycle-retention'): _tags(clf='3.6', saa='4.1 1.3'),
    ('s3', 'bucket-cors'): _tags(saa='1.2'),
    ('s3', 'presigned-url'): _tags(clf='2.2', saa='1.3'),
    ('s3', 'multipart-upload'): _tags(clf='3.6', saa='3.1 4.1'),
    ('kms', 'key-alias-encrypt-decrypt'): _tags(clf='2.2', saa='1.3'),
    ('ssm', 'parameter-store-config'): _tags(clf='2.3', saa='1.2'),
    ('secretsmanager', 'secret-lifecycle'): _tags(clf='2.3', saa='1.2 1.3'),
    ('cognito', 'user-pool-signup-auth'): _tags(clf='2.3', saa='1.2'),
    ('cognito', 'user-groups-custom-attributes'): _tags(saa='1.2'),
    ('rds', 'db-instance-lifecycle'): _tags(clf='3.4', saa='3.3 1.3'),
    ('ec2', 'vpc-public-private'): _tags(clf='3.5', saa='1.2 3.4'),
    ('ec2', 'security-controls'): _tags(clf='3.5', saa='1.2'),
    ('ec2', 'guided-imds'): _tags(clf='3.3', saa='1.2'),
    ('ec2', 'guided-userdata'): _tags(clf='3.1', saa='2.2'),
    ('ec2', 'guided-web-server'): _tags(clf='3.5', saa='1.2'),
    ('ec2', 'guided-instance-role'): _tags(clf='2.3', saa='1.1 1.2'),
    ('lambda', 'runtime-config'): _tags(clf='2.3', saa='1.2'),
    ('dynamodb', 'lambda-writes'): _tags(clf='3.4', saa='2.1'),
    # Level 3
    ('sqs', 'visibility-timeout'): _tags(clf='3.8', saa='2.1 2.2'),
    ('sqs', 'delayed-message'): _tags(saa='2.1'),
    ('sqs', 'batch-messages'): _tags(saa='2.1'),
    ('sqs', 'queue-configuration'): _tags(clf='4.2', saa='2.1'),
    ('sqs', 'dead-letter-redrive'): _tags(clf='3.8', saa='2.1 2.2'),
    ('sqs', 'fifo-ordering'): _tags(clf='3.8', saa='2.1'),
    ('sqs', 'purge-delete'): _tags(),
    ('sns', 'sqs-fanout'): _tags(clf='3.8', saa='2.1'),
    ('sns', 'filter-policies'): _tags(clf='3.8', saa='2.1'),
    ('s3', 'object-notifications-sqs'): _tags(clf='3.8', saa='2.1'),
    ('lambda', 'sqs-event-source'): _tags(clf='3.3', saa='2.1 3.2'),
    ('scheduler', 'sqs-delivery'): _tags(clf='3.8', saa='2.1'),
    ('apigateway', 'lambda-request'): _tags(clf='3.3', saa='2.1'),
    ('eventbridge', 'application-spine'): _tags(clf='3.8', saa='2.1 2.2'),
    ('cloudwatch', 'metric-alarms'): _tags(clf='2.2', saa='2.2 3.2'),
    ('cloudwatch', 'log-groups-metric-filters'): _tags(clf='2.2', saa='2.2'),
    # Level 4
    ('iam', 'identity-enforcement-capstone'): _tags(clf='2.3', saa='1.1'),
    ('ec2', 'guided-ssm-command'): _tags(clf='3.1'),
    ('stepfunctions', 'order-processing-workflow'): _tags(saa='2.1'),
    ('stepfunctions', 'parallel-retry-workflow'): _tags(saa='2.1'),
    ('ecs', 'fargate-microservice'): _tags(clf='3.3', saa='2.1 3.2 4.2'),
    ('rds', 'aurora-resilience'): _tags(clf='3.4', saa='2.2 3.3'),
    ('elasticloadbalancing', 'ha-web-tier'): _tags(clf='3.3 3.2', saa='2.2 2.1 3.2 3.4'),
    ('s3', 'data-protection-tiers'): _tags(clf='3.6 4.1', saa='1.3 4.1 2.2'),
    ('kms', 'key-management'): _tags(clf='2.2', saa='1.3'),
    ('route53', 'routing-failover'): _tags(clf='3.5 3.2', saa='2.2 3.4'),
    ('elasticache', 'cache-aside'): _tags(clf='3.4', saa='3.3 2.1'),
    ('kinesis', 'streaming-data-lake'): _tags(clf='3.7 3.6', saa='3.5 4.1'),
    ('dynamodb', 'capacity-at-scale'): _tags(clf='3.4', saa='4.3 3.3 2.2'),
    ('lambda', 'performance-cost'): _tags(clf='3.3', saa='3.2 4.2'),
    ('ec2', 'block-file-storage'): _tags(clf='3.6', saa='3.1 4.1'),
    ('backup', 'backup-dr-plan'): _tags(clf='3.6', saa='2.2 1.3 4.1'),
    # Level 5
    ('ec2', 'nat-three-tier'): _tags(clf='3.5', saa='1.2 4.4 3.4 2.2'),
    ('ec2', 'guided-broken-route'): _tags(clf='3.5', saa='2.2 3.4'),
    ('ec2', 's3-gateway-endpoint'): _tags(clf='3.5', saa='1.2 3.4 4.4'),
    ('ec2', 'guided-private-s3'): _tags(clf='3.5', saa='1.2 4.4'),
    ('ec2', 'sqs-interface-endpoint'): _tags(clf='3.5', saa='1.2 3.4'),
    ('eks', 'control-plane-nodegroup'): _tags(clf='3.3', saa='2.1 3.2'),
    ('cloudformation', 's3-sqs-stack'): _tags(clf='3.1', saa='2.2'),
}


# Short study notes for objectives that labs cannot cover hands-on, or cover
# only partly. "planned" names the lab from docs/certification-coverage.md
# that would add hands-on practice, when Floci can support one.
CONCEPT_CARDS: list[dict[str, Any]] = [
    {
        'key': 'cloud-value',
        'title': 'Why organizations move to the AWS Cloud',
        'exams': [(CLF, '1.1')],
        'points': [
            'Trade up-front capital expense for variable, pay-as-you-go expense.',
            'Benefit from economies of scale: AWS buys capacity in bulk, which lowers prices.',
            'Stop guessing capacity: elasticity scales resources up and down with demand.',
            'Increase speed and agility: new resources are minutes away, not weeks.',
            'Go global in minutes by deploying to Regions close to users.',
        ],
        'related_labs': [],
        'planned': None,
    },
    {
        'key': 'well-architected',
        'title': 'The six pillars of the Well-Architected Framework',
        'exams': [(CLF, '1.2')],
        'points': [
            'Operational excellence: run and monitor systems, and improve processes (for example, infrastructure as code).',
            'Security: protect data and systems with least privilege, traceability, and encryption.',
            'Reliability: recover from failure and meet demand (multiple AZs, backups, scaling).',
            'Performance efficiency: use the right resource types and sizes, and keep evaluating them.',
            'Cost optimization: avoid unneeded cost (rightsizing, purchasing options, lifecycle tiering).',
            'Sustainability: minimize the environmental impact of running workloads.',
        ],
        'related_labs': [('cloudformation', 's3-sqs-stack'), ('s3', 'lifecycle-retention'), ('s3', 'data-protection-tiers')],
        'planned': None,
    },
    {
        'key': 'migration-caf',
        'title': 'Cloud Adoption Framework and migration strategies',
        'exams': [(CLF, '1.3')],
        'points': [
            'AWS CAF groups capabilities into six perspectives: business, people, governance, platform, security, and operations.',
            'Migration strategies (the 7 Rs): retire, retain, rehost (lift and shift), relocate, repurchase, replatform, and refactor.',
            'AWS DMS migrates databases with minimal downtime; AWS SCT converts schemas between different engines.',
            'Snow Family devices and DataSync move large data sets when network transfer is too slow.',
        ],
        'related_labs': [],
        'planned': None,
    },
    {
        'key': 'cloud-economics',
        'title': 'Cloud economics',
        'exams': [(CLF, '1.4')],
        'points': [
            'On-premises costs include hardware, facilities, power, cooling, and the staff to run them; the cloud turns most of this into variable cost.',
            'Licensing: bring your own license (BYOL) versus license-included pricing.',
            'Rightsizing matches instance type and size to actual use.',
            'Automation (infrastructure as code, scaling) reduces manual effort and errors.',
        ],
        'related_labs': [('cloudformation', 's3-sqs-stack')],
        'planned': None,
    },
    {
        'key': 'shared-responsibility',
        'title': 'The shared responsibility model',
        'exams': [(CLF, '2.1'), (SAA, '1.1')],
        'points': [
            'AWS is responsible for security of the cloud: facilities, hardware, the global network, and the software behind managed services.',
            'Customers are responsible for security in the cloud: their data, IAM, network rules, and encryption choices.',
            'The line moves with the service. On EC2 you patch the guest OS; on RDS AWS patches the OS and database engine; on Lambda AWS runs the runtime and you own the code and permissions.',
        ],
        'related_labs': [('lambda', 'create-invoke-logs'), ('rds', 'db-instance-lifecycle'), ('ec2', 'guided-imds')],
        'planned': None,
    },
    {
        'key': 'governance-services',
        'title': 'Monitoring, auditing, and compliance services',
        'exams': [(CLF, '2.2'), (SAA, '1.3')],
        'points': [
            'CloudWatch monitors metrics and logs; CloudTrail records API calls for auditing; AWS Config records resource configuration and evaluates rules.',
            'AWS Artifact provides AWS compliance reports and agreements.',
            'Encryption at rest (KMS) and in transit (TLS certificates from ACM) protect data.',
        ],
        'related_labs': [('cloudwatch', 'metric-alarms'), ('kms', 'key-alias-encrypt-decrypt'), ('kms', 'key-management')],
        'planned': 'W3-3 Governance services tour',
    },
    {
        'key': 'security-services',
        'title': 'AWS security services and resources',
        'exams': [(CLF, '2.4'), (SAA, '1.2')],
        'points': [
            'AWS WAF filters web requests (layer 7) on CloudFront, ALB, and API Gateway. AWS Shield protects against DDoS: Standard is automatic, Advanced is a paid tier.',
            'Firewall Manager applies firewall rules across accounts. GuardDuty detects threats from logs. Inspector scans for vulnerabilities. Macie finds sensitive data in S3. Security Hub aggregates findings.',
            'Trusted Advisor checks for security and cost best practices. Third-party security products are available in AWS Marketplace.',
            'Security information lives in the AWS Security Center, Security Blog, and Knowledge Center.',
        ],
        'related_labs': [],
        'planned': 'W3-3 Governance services tour; W3-5 Edge security and throttling',
    },
    {
        'key': 'global-infrastructure',
        'title': 'Regions, Availability Zones, and edge locations',
        'exams': [(CLF, '3.2'), (SAA, '2.2')],
        'points': [
            'A Region is a separate geographic area; choose one for latency, data sovereignty, service availability, and price.',
            'Each Region has multiple isolated Availability Zones with no shared single point of failure; spread resources across AZs for high availability.',
            'Use multiple Regions for disaster recovery, business continuity, low latency for distant users, or data residency.',
            'Edge locations serve CloudFront, Route 53, and Global Accelerator close to users.',
        ],
        'related_labs': [('ec2', 'vpc-public-private'), ('elasticloadbalancing', 'ha-web-tier'), ('route53', 'routing-failover'),
                         ('backup', 'backup-dr-plan')],
        'planned': None,
    },
    {
        'key': 'hybrid-connectivity',
        'title': 'Connecting on-premises networks and storage',
        'exams': [(CLF, '3.5'), (SAA, '1.2'), (SAA, '3.4'), (SAA, '4.4')],
        'points': [
            'Site-to-Site VPN is an encrypted tunnel over the internet: quick to set up and inexpensive.',
            'Direct Connect is a dedicated private line: consistent bandwidth and latency, but takes longer to provision.',
            'Storage Gateway gives on-premises apps cached access to cloud storage; DataSync and Transfer Family move files into AWS.',
            'Floci does not emulate VPN, Direct Connect, or Storage Gateway, so these stay conceptual.',
        ],
        'related_labs': [],
        'planned': None,
    },
    {
        'key': 'ai-analytics',
        'title': 'AI/ML and analytics services',
        'exams': [(CLF, '3.7')],
        'points': [
            'Amazon SageMaker AI builds, trains, and deploys machine learning models. Amazon Bedrock offers foundation models through an API.',
            'Purpose-built AI services: Lex (chatbots), Polly (text to speech), Transcribe (speech to text), Comprehend (text analysis), Rekognition (images and video), Textract (document text).',
            'Analytics: Athena queries data in S3 with SQL, Kinesis handles streaming data, Glue catalogs and transforms data, and Amazon Quick builds dashboards.',
        ],
        'related_labs': [('kinesis', 'streaming-data-lake')],
        'planned': None,
    },
    {
        'key': 'data-ingestion',
        'title': 'Data ingestion, transformation, and data lakes',
        'exams': [(SAA, '3.5')],
        'points': [
            'Kinesis Data Streams is for real-time custom processing with replay; Amazon Data Firehose delivers streams to S3, Redshift, or OpenSearch with no code.',
            'AWS Glue crawls and catalogs data and runs ETL jobs; converting CSV to columnar Parquet makes Athena queries faster and cheaper.',
            'Lake Formation secures and governs a data lake built on S3. Amazon EMR runs big-data frameworks such as Spark.',
            'DataSync and Storage Gateway move or bridge on-premises data into AWS.',
        ],
        'related_labs': [('kinesis', 'streaming-data-lake')],
        'planned': None,
    },
    {
        'key': 'pricing-models',
        'title': 'Compute purchasing options and data transfer costs',
        'exams': [(CLF, '4.1'), (SAA, '4.2')],
        'points': [
            'On-Demand: pay by the second or hour with no commitment.',
            'Savings Plans and Reserved Instances: commit for 1 or 3 years for a lower rate. RI discounts can be shared across accounts in an AWS Organization.',
            'Spot Instances: spare capacity at a deep discount, but AWS can reclaim it with a two-minute warning; good for fault-tolerant, flexible jobs.',
            'Dedicated Hosts (physical servers, useful for per-socket licensing), Dedicated Instances, and Capacity Reservations cover isolation and capacity needs.',
            'Data transfer into AWS is generally free; transfer out to the internet, between Regions, and between AZs is charged.',
        ],
        'related_labs': [('ecs', 'fargate-microservice'), ('ec2', 'nat-three-tier'), ('lambda', 'performance-cost')],
        'planned': 'W3-6 Cost visibility; W3-7 Purchasing options exam lens',
    },
    {
        'key': 'billing-tools',
        'title': 'Billing, budgets, and cost management tools',
        'exams': [(CLF, '4.2'), (SAA, '4.1'), (SAA, '4.3'), (SAA, '4.4')],
        'points': [
            'AWS Budgets alerts on actual or forecast cost and usage. Cost Explorer visualizes and forecasts spend. The Pricing Calculator estimates costs before you build.',
            'AWS Organizations consolidated billing gives one bill, combined volume discounts, and shared RI and Savings Plans benefits.',
            'Cost allocation tags (AWS-generated or user-defined) must be activated before they appear in billing reports such as the Cost and Usage Report.',
        ],
        'related_labs': [('sqs', 'queue-configuration')],
        'planned': 'W3-6 Cost visibility',
    },
    {
        'key': 'support-resources',
        'title': 'AWS Support plans and technical resources',
        'exams': [(CLF, '4.3')],
        'points': [
            'Support options range from Basic Support through AWS Business Support+, AWS Enterprise Support, and AWS Unified Operations; higher tiers add faster response and more proactive guidance.',
            'Self-service resources: AWS re:Post, Knowledge Center, Prescriptive Guidance, whitepapers, and documentation.',
            'Trusted Advisor, the AWS Health Dashboard, and the Health API help monitor environments; the Trust and Safety team handles abuse reports.',
            'The AWS Partner Network includes independent software vendors and system integrators; AWS Professional Services and solutions architects offer direct help.',
        ],
        'related_labs': [],
        'planned': None,
    },
    {
        'key': 'database-cost',
        'title': 'Choosing cost-effective databases',
        'exams': [(SAA, '4.3'), (SAA, '3.3')],
        'points': [
            'DynamoDB on-demand suits unpredictable traffic; provisioned capacity with auto scaling is cheaper for steady traffic.',
            'Aurora Serverless v2 scales capacity with demand for variable workloads; Reserved Instances lower the cost of steady RDS use.',
            'Read replicas offload reads; caching with ElastiCache can cut database load further.',
            'Keep snapshot retention to what recovery requirements need. DMS and SCT handle homogeneous and heterogeneous migrations.',
        ],
        'related_labs': [('dynamodb', 'crud-query'), ('rds', 'db-instance-lifecycle'), ('rds', 'aurora-resilience'),
                         ('elasticache', 'cache-aside'), ('dynamodb', 'capacity-at-scale')],
        'planned': None,
    },
]


def tags_for_lab(service: str, key: str) -> list[dict[str, Any]]:
    return [dict(tag) for tag in LAB_EXAM_TAGS.get((service, key), [])]


def exam_tracks(labs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return each exam guide with the labs and concept cards attached to every task."""
    exams = []
    for code, guide in EXAM_GUIDES.items():
        domains = []
        for domain in guide['domains']:
            tasks = []
            for task in domain['tasks']:
                task_labs = [
                    {'service': lab['service'], 'key': lab['key'], 'title': lab.get('title'), 'primary': tag['primary']}
                    for lab in labs
                    for tag in LAB_EXAM_TAGS.get((lab['service'], lab['key']), [])
                    if tag['exam'] == code and tag['task'] == task['id']
                ]
                concepts = [card['key'] for card in CONCEPT_CARDS if (code, task['id']) in card['exams']]
                tasks.append({**task, 'labs': task_labs, 'concepts': concepts})
            domains.append({**{k: v for k, v in domain.items() if k != 'tasks'}, 'tasks': tasks})
        exams.append({**{k: v for k, v in guide.items() if k != 'domains'}, 'domains': domains})
    concepts = [
        {
            'key': card['key'],
            'title': card['title'],
            'points': card['points'],
            'exams': [{'exam': exam, 'task': task} for exam, task in card['exams']],
            'related_labs': [{'service': service, 'key': key} for service, key in card['related_labs']],
            'planned': card['planned'],
        }
        for card in CONCEPT_CARDS
    ]
    return {'exams': exams, 'concepts': concepts}
