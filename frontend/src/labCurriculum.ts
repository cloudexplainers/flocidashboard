export interface CurriculumLabItem {
  id: number;
  service: string;
  key: string;
  title: string;
  stepCount: number;
  levelId: string;
}

export interface CurriculumLevel {
  id: string;
  title: string;
  subtitle: string;
  badge: string;
  color: 'blue' | 'green' | 'red' | 'grey';
  description: string;
  labs: CurriculumLabItem[];
}

type CurriculumLabEntry = Pick<CurriculumLabItem, 'service' | 'key' | 'title' | 'stepCount'>;
type CurriculumLevelDefinition = Omit<CurriculumLevel, 'labs'> & { labs: CurriculumLabEntry[] };

// Lab numbers come from position, so moving a lab between levels renumbers
// everything after it. Previous/next navigation relies on that ordering.
const LEVEL_DEFINITIONS: CurriculumLevelDefinition[] = [
  {
    id: 'level-1-beginner',
    title: 'Level 1: Beginner Foundations',
    subtitle: 'Core services: identity, storage, messaging, NoSQL, and serverless basics',
    badge: 'Beginner',
    color: 'green',
    description: 'Create IAM users, groups, and managed and inline policies; store and organize S3 objects; send and receive SQS messages; query a DynamoDB table; and run a first Lambda function.',
    labs: [
      { service: 'iam', key: 'create-admin-user', title: 'Create a local admin user', stepCount: 4 },
      { service: 'iam', key: 'create-user-alice', title: 'Create an IAM user', stepCount: 1 },
      { service: 'iam', key: 'attach-policy-alice', title: 'Attach a managed policy to Alice', stepCount: 4 },
      { service: 'iam', key: 'access-key-alice', title: 'Create an access key for Alice', stepCount: 3 },
      { service: 'iam', key: 'group-membership-alice', title: 'Add Alice to an IAM group', stepCount: 4 },
      { service: 'iam', key: 'group-policy-floci-developers', title: 'Attach a policy to an IAM group', stepCount: 6 },
      { service: 'iam', key: 'inline-policy-alice', title: 'Attach an inline policy to Alice', stepCount: 4 },
      { service: 's3', key: 'create-bucket', title: 'Create and inspect an S3 bucket', stepCount: 3 },
      { service: 's3', key: 'object-workflow', title: 'Upload and retrieve an S3 object', stepCount: 5 },
      { service: 's3', key: 'prefix-copy', title: 'Organize and copy objects with key prefixes', stepCount: 5 },
      { service: 's3', key: 'metadata-tags', title: 'Add object metadata and tags', stepCount: 5 },
      { service: 'sqs', key: 'create-queue', title: 'Create and inspect an SQS queue', stepCount: 4 },
      { service: 'sqs', key: 'message-lifecycle', title: 'Send, receive, and delete an SQS message', stepCount: 4 },
      { service: 'dynamodb', key: 'crud-query', title: 'Create a DynamoDB table and query items', stepCount: 7 },
      { service: 'lambda', key: 'create-invoke-logs', title: 'Create, invoke, and inspect a Lambda function', stepCount: 5 },
    ],
  },
  {
    id: 'level-2-intermediate',
    title: 'Level 2: Intermediate Architecture',
    subtitle: 'Secure access, data protection, VPC networking, and managed compute',
    badge: 'Intermediate',
    color: 'blue',
    description: 'Use IAM roles, STS, and instance profiles; protect S3 data with versioning, encryption, bucket policies, and lifecycle tiering; manage keys, configuration, rotated secrets, and Cognito users; run an encrypted RDS database; build a VPC with security groups and network ACLs; and run EC2 instances and Lambda functions that use roles instead of keys.',
    labs: [
      { service: 'iam', key: 'role-trust-policy', title: 'Create a role with a trust policy', stepCount: 4 },
      { service: 'iam', key: 'sts-session-policy', title: 'Assume a role with an STS session policy', stepCount: 3 },
      { service: 'iam', key: 'ec2-instance-profile', title: 'Create an EC2 instance profile', stepCount: 5 },
      { service: 's3', key: 'version-recovery', title: 'Enable versioning and recover an earlier object version', stepCount: 6 },
      { service: 's3', key: 'default-encryption', title: 'Enable default bucket encryption', stepCount: 5 },
      { service: 's3', key: 'bucket-security', title: 'Block public access and apply a bucket policy', stepCount: 5 },
      { service: 's3', key: 'lifecycle-retention', title: 'Tier and expire logs with an S3 lifecycle rule', stepCount: 4 },
      { service: 's3', key: 'bucket-cors', title: 'Configure bucket CORS for a local web app', stepCount: 3 },
      { service: 's3', key: 'presigned-url', title: 'Generate temporary access with a presigned URL', stepCount: 3 },
      { service: 's3', key: 'multipart-upload', title: 'Complete a multipart upload', stepCount: 7 },
      { service: 'kms', key: 'key-alias-encrypt-decrypt', title: 'Protect local app data with KMS', stepCount: 5 },
      { service: 'ssm', key: 'parameter-store-config', title: 'Read app configuration from SSM Parameter Store', stepCount: 3 },
      { service: 'secretsmanager', key: 'secret-lifecycle', title: 'Create, update, and rotate a Secrets Manager secret', stepCount: 10 },
      { service: 'cognito', key: 'user-pool-signup-auth', title: 'Create a Cognito User Pool, register users, and authenticate', stepCount: 5 },
      { service: 'cognito', key: 'user-groups-custom-attributes', title: 'Manage Cognito user groups and custom profile attributes', stepCount: 4 },
      { service: 'rds', key: 'db-instance-lifecycle', title: 'Provision relational databases and manage instance lifecycles in Amazon RDS', stepCount: 5 },
      { service: 'ec2', key: 'vpc-public-private', title: 'Build a VPC with public and private subnets', stepCount: 12 },
      { service: 'ec2', key: 'security-controls', title: 'Control VPC traffic with security groups and network ACLs', stepCount: 13 },
      { service: 'ec2', key: 'guided-imds', title: 'Launch an instance and inspect IMDS', stepCount: 3 },
      { service: 'ec2', key: 'guided-userdata', title: 'Run UserData and verify its output', stepCount: 3 },
      { service: 'ec2', key: 'guided-web-server', title: 'Publish a web server through a security group', stepCount: 3 },
      { service: 'ec2', key: 'guided-instance-role', title: 'Use an IAM role from inside an instance', stepCount: 4 },
      { service: 'lambda', key: 'runtime-config', title: 'Read app configuration and secrets from Lambda', stepCount: 7 },
      { service: 'dynamodb', key: 'lambda-writes', title: 'Write DynamoDB items from Lambda', stepCount: 7 },
    ],
  },
  {
    id: 'level-3-advanced',
    title: 'Level 3: Advanced Event-Driven & APIs',
    subtitle: 'Decoupled messaging, APIs, event-driven design, and monitoring',
    badge: 'Advanced',
    color: 'blue',
    description: 'Build reliable distributed systems with DLQ redrives, FIFO message ordering, SNS multi-queue fanouts, subscription filters, API Gateway Lambda proxies, EventBridge buses, CloudWatch alarms that evaluate real metrics, and log metric filters.',
    labs: [
      { service: 'sqs', key: 'visibility-timeout', title: 'Understand SQS visibility timeout', stepCount: 7 },
      { service: 'sqs', key: 'delayed-message', title: 'Work with delayed SQS messages', stepCount: 4 },
      { service: 'sqs', key: 'batch-messages', title: 'Send and delete SQS messages in batches', stepCount: 4 },
      { service: 'sqs', key: 'queue-configuration', title: 'Configure SQS queue attributes and tags', stepCount: 5 },
      { service: 'sqs', key: 'dead-letter-redrive', title: 'Route failed messages to a dead-letter queue', stepCount: 12 },
      { service: 'sqs', key: 'fifo-ordering', title: 'Preserve ordering and deduplicate messages with SQS FIFO', stepCount: 8 },
      { service: 'sqs', key: 'purge-delete', title: 'Purge messages and delete an SQS queue', stepCount: 6 },
      { service: 'sns', key: 'sqs-fanout', title: 'Fan out an SNS message to SQS queues', stepCount: 11 },
      { service: 'sns', key: 'filter-policies', title: 'Route selected SNS messages with subscription filters', stepCount: 12 },
      { service: 's3', key: 'object-notifications-sqs', title: 'Send S3 object-created events to SQS', stepCount: 7 },
      { service: 'lambda', key: 'sqs-event-source', title: 'Process SQS messages with Lambda', stepCount: 7 },
      { service: 'scheduler', key: 'sqs-delivery', title: 'Schedule an EventBridge Scheduler message to SQS', stepCount: 8 },
      { service: 'apigateway', key: 'lambda-request', title: 'Send an API Gateway request to Lambda', stepCount: 9 },
      { service: 'eventbridge', key: 'application-spine', title: 'Build an event-driven order application', stepCount: 11 },
      { service: 'cloudwatch', key: 'metric-alarms', title: 'Publish custom CloudWatch metrics and trigger alarms', stepCount: 4 },
      { service: 'cloudwatch', key: 'log-groups-metric-filters', title: 'Turn application logs into metrics with CloudWatch Logs metric filters', stepCount: 6 },
    ],
  },
  {
    id: 'level-4-senior',
    title: 'Level 4: Workflows, Containers & Enforcement',
    subtitle: 'Identity enforcement, remote operations, workflow orchestration, containers, resilient architectures, key management, DNS failover, caching, and streaming analytics',
    badge: 'Senior',
    color: 'red',
    description: 'Switch identities and prove IAM enforcement end to end, run commands on instances with SSM, orchestrate workflows with Step Functions Choice and Parallel states, deploy containerized microservices on ECS with Fargate, make databases resilient with RDS read replicas, point-in-time restore, and Aurora failover, run a highly available web tier behind an Application Load Balancer with Auto Scaling, protect and tier S3 data with Object Lock, storage classes, lifecycle rules, and cross-Region replication, manage a customer managed KMS key with a key policy, rotation, grants, envelope encryption, and SSE-KMS, route users with Route 53 health checks, failover, weighted and latency records, and a split-horizon private zone, speed up reads with an ElastiCache cache-aside reader that caches with a TTL and invalidates on writes, and stream clickstream events through Kinesis and Firehose into a partitioned S3 data lake queried with Athena and converted to Parquet, and run DynamoDB at scale with capacity sized from the workload, auto scaling, on-demand sessions expired by TTL, a filtered stream processor, point-in-time recovery, and a global table replica, and tune Lambda cost and releases with a memory sweep, versions, a weighted canary alias, reserved concurrency, and a function URL, and choose block and file storage by sizing gp3 and io2 volumes, formatting, growing, and moving a volume between instances, and sharing an EFS file system across two AZs, and plan backups with a locked AWS Backup vault, cross-Region copies, and a disaster recovery strategy chosen from RPO and RTO, and deliver content at the edge with CloudFront origin access control, cache policies, and invalidations, and Global Accelerator static IPs with a traffic dial failover.',
    labs: [
      { service: 'iam', key: 'identity-enforcement-capstone', title: 'Switch identities and verify IAM enforcement', stepCount: 7 },
      { service: 'ec2', key: 'guided-ssm-command', title: 'Execute commands with SSM Run Command', stepCount: 3 },
      { service: 'stepfunctions', key: 'order-processing-workflow', title: 'Build a Step Functions state machine with Choice branching', stepCount: 5 },
      { service: 'stepfunctions', key: 'parallel-retry-workflow', title: 'Execute parallel branches and aggregate state outputs', stepCount: 3 },
      { service: 'ecs', key: 'fargate-microservice', title: 'Deploy containerized microservices with Amazon ECS and AWS Fargate', stepCount: 6 },
      { service: 'rds', key: 'aurora-resilience', title: 'Design resilient databases with read replicas, point-in-time restore, and Aurora failover', stepCount: 10 },
      { service: 'elasticloadbalancing', key: 'ha-web-tier', title: 'Build a highly available web tier with an Application Load Balancer and Auto Scaling', stepCount: 12 },
      { service: 's3', key: 'data-protection-tiers', title: 'Protect and tier S3 data with Object Lock, storage classes, lifecycle rules, and replication', stepCount: 13 },
      { service: 'kms', key: 'key-management', title: 'Manage a customer managed KMS key with a key policy, rotation, grants, and envelope encryption', stepCount: 8 },
      { service: 'route53', key: 'routing-failover', title: 'Route traffic and fail over between Regions with Route 53', stepCount: 8 },
      { service: 'elasticache', key: 'cache-aside', title: 'Speed up reads with an ElastiCache cache-aside pattern', stepCount: 8 },
      { service: 'kinesis', key: 'streaming-data-lake', title: 'Stream clickstream events into an S3 data lake and query them with Athena', stepCount: 9 },
      { service: 'dynamodb', key: 'capacity-at-scale', title: 'Run DynamoDB at scale with capacity planning, auto scaling, TTL, streams, and global tables', stepCount: 8 },
      { service: 'lambda', key: 'performance-cost', title: 'Tune Lambda performance and cost with memory sizing, aliases, canaries, and concurrency', stepCount: 8 },
      { service: 'ec2', key: 'block-file-storage', title: 'Choose and use block and file storage with EBS gp3, io2, and EFS', stepCount: 8 },
      { service: 'backup', key: 'backup-dr-plan', title: 'Plan backups and choose a disaster recovery strategy with AWS Backup', stepCount: 7 },
      { service: 'cloudfront', key: 'edge-delivery', title: 'Deliver content at the edge with CloudFront and Global Accelerator', stepCount: 9 },
    ],
  },
  {
    id: 'level-5-principal',
    title: 'Level 5: Private Networking & Platform',
    subtitle: 'NAT gateways, private connectivity, route troubleshooting, Kubernetes, and infrastructure as code',
    badge: 'Expert',
    color: 'red',
    description: 'Build a three-tier VPC with a NAT gateway in each Availability Zone and an isolated data tier, diagnose and repair a broken route, reach S3 and SQS privately through gateway and interface endpoints, provision an EKS control plane with node groups and Fargate profiles, and manage infrastructure as code with CloudFormation stacks updated through reviewed change sets.',
    labs: [
      { service: 'ec2', key: 'nat-three-tier', title: 'Build a three-tier VPC with a NAT gateway in each Availability Zone', stepCount: 10 },
      { service: 'ec2', key: 'guided-broken-route', title: 'Diagnose and repair a broken route', stepCount: 4 },
      { service: 'ec2', key: 's3-gateway-endpoint', title: 'Connect a private VPC to S3 with a gateway endpoint', stepCount: 7 },
      { service: 'ec2', key: 'guided-private-s3', title: 'Connect privately to S3 through a VPC endpoint', stepCount: 3 },
      { service: 'ec2', key: 'sqs-interface-endpoint', title: 'Connect a private subnet to SQS with an interface endpoint', stepCount: 7 },
      { service: 'eks', key: 'control-plane-nodegroup', title: 'Provision Kubernetes control planes, node groups, and Fargate profiles with Amazon EKS', stepCount: 5 },
      { service: 'cloudformation', key: 's3-sqs-stack', title: 'Provision S3 and SQS resources with CloudFormation', stepCount: 11 },
    ],
  },
  {
    id: 'level-6-developer',
    title: 'Level 6: Developer Workflows',
    subtitle: 'CI/CD pipelines, API development, data access patterns, packaging, error handling, and observability',
    badge: 'Developer',
    color: 'blue',
    description: 'Ship code the way the Developer – Associate exam expects: build and unit test a Lambda service with CodeBuild, trace a failing build to its phase, release it through a CodePipeline with a manual approval, and shift a live alias with a CodeDeploy canary and roll it back. Then build a REST API with dev and prod stages, a Lambda authorizer, a Cognito authorizer, request validation, and an API key, and write DynamoDB code that survives retries and races with conditions, version checks, and transactions. Finally, deploy one SAM template to dev and prod and release a feature with an AppConfig flag, and handle asynchronous Lambda failures with retries, destinations, a dead-letter queue, and an encrypted secret, then instrument the service with structured logs, metric filters, custom metrics, an alarm, and a dashboard.',
    labs: [
      { service: 'codepipeline', key: 'lambda-cicd', title: 'Ship a Lambda service through a CI/CD pipeline with CodeBuild, CodePipeline, and CodeDeploy', stepCount: 9 },
      { service: 'apigateway', key: 'rest-api-developer', title: 'Build a REST API with stages, Lambda and Cognito authorizers, request validation, and API keys', stepCount: 7 },
      { service: 'dynamodb', key: 'application-patterns', title: 'Build DynamoDB application patterns: key design, conditional writes, locking, transactions, and paging', stepCount: 8 },
      { service: 'cloudformation', key: 'sam-environments', title: 'Deploy one SAM template to dev and prod, with a Lambda layer and AppConfig feature flags', stepCount: 7 },
      { service: 'lambda', key: 'async-error-handling', title: 'Handle Lambda events and failures with test events, async retries, destinations, a DLQ, and KMS', stepCount: 8 },
      { service: 'cloudwatch', key: 'serverless-observability', title: 'Instrument a serverless app with structured logs, metric filters, custom metrics, alarms, and dashboards', stepCount: 8 },
    ],
  },
  {
    id: 'level-7-governance',
    title: 'Level 7: Governance, Multi-Account & Cost',
    subtitle: 'Account hardening, multi-account guardrails, audit and compliance, network scale-out, edge security, and cost visibility',
    badge: 'Architect',
    color: 'grey',
    description: 'Run AWS the way a platform team does: harden an account with a password policy, MFA that is required before anything else works, a permissions boundary on a delegated developer, an external-access review, a credential report audit, and a root user check. Then grow into many accounts with AWS Organizations: OUs for security and workloads, member accounts, SCP guardrails evaluated through the OU tree, a CostCenter tag policy, and a delegated security administrator on one consolidated bill. Then audit it: a CloudTrail trail with S3 data events, AWS Config checking bucket versioning, GuardDuty and Security Hub alerts routed through EventBridge, and a chooser for which service answers which question. Then scale the network: VPC peering and its limits, a segmented Transit Gateway, flow logs, and a peering-versus-Transit-Gateway cost model. Then protect an API with AWS WAF rules and bronze and gold usage plans, and finish by controlling cost with tags, Budgets, Cost Explorer, anomaly detection, Spot, and purchasing options.',
    labs: [
      { service: 'iam', key: 'account-hardening', title: 'Harden an AWS account with a password policy, MFA, permissions boundaries, and access reviews', stepCount: 7 },
      { service: 'iam', key: 'multi-account-guardrails', title: 'Govern multiple accounts with AWS Organizations, OUs, SCP guardrails, and tag policies', stepCount: 7 },
      { service: 'cloudtrail', key: 'governance-tour', title: 'Audit and secure an account with CloudTrail, Config, GuardDuty, and Security Hub', stepCount: 8 },
      { service: 'ec2', key: 'network-scale-out', title: 'Scale out a network with VPC peering, Transit Gateway, and flow logs', stepCount: 8 },
      { service: 'wafv2', key: 'edge-throttling', title: 'Protect and throttle an API with AWS WAF and API Gateway usage plans', stepCount: 7 },
      { service: 'costexplorer', key: 'cost-visibility', title: 'Track and control AWS costs with tags, Budgets, Cost Explorer, and purchasing options', stepCount: 7 },
    ],
  },
  {
    id: 'level-8-cloud-practitioner',
    title: 'Level 8: Cloud Practitioner Essentials',
    subtitle: 'Well-Architected reviews, the shared responsibility model, migration strategies, and the cloud concepts behind the Cloud Practitioner exam',
    badge: 'Foundations',
    color: 'green',
    description: 'Turn the concept-heavy parts of the Cloud Practitioner exam into hands-on work: review a workload against the six Well-Architected pillars, fix it with one change set, prove the fix with a failure drill, and spin up a full test copy and throw it away. Then run the pieces of one app on EC2, RDS, Lambda, and S3 and see where AWS\'s job ends and yours begins on each, then build the responsibility matrix from what you deployed and sort common tasks into AWS, customer, and shared. Then plan a migration: sort a portfolio into the 7 Rs with the Cloud Adoption Framework perspectives, copy a file share with DataSync (or ship it on a Snowball), move a MySQL database to Aurora with a DMS full load and change data capture, cut over, and replatform a web app onto Elastic Beanstalk. Finish with the economics: price the workload from the Price List, compare a three-year TCO against an on-premises rack, scale with demand instead of buying for the peak, rightsize from real CPU metrics, weigh BYOL on a Dedicated Host, and compare Lightsail bundles with EC2.',
    labs: [
      { service: 'cloudformation', key: 'well-architected-review', title: 'Review a workload against the Well-Architected pillars and fix it with a change set', stepCount: 7 },
      { service: 'ec2', key: 'shared-responsibility', title: 'See the shared responsibility model on EC2, RDS, Lambda, and S3', stepCount: 7 },
      { service: 'dms', key: 'migration-toolkit', title: 'Plan and run a migration with the 7 Rs, DataSync, AWS DMS, and Elastic Beanstalk', stepCount: 7 },
      { service: 'pricing', key: 'cloud-economics', title: 'Work out cloud economics: TCO, elasticity, rightsizing, licensing, and fixed-price bundles', stepCount: 7 },
    ],
  },
  {
    id: 'level-9-ai-practitioner',
    title: 'Level 9: AI Practitioner',
    subtitle: 'AWS AI services, the ML lifecycle on SageMaker AI, retrieval augmented generation, prompt engineering, and guardrails',
    badge: 'AI',
    color: 'blue',
    description: 'Learn the AI and machine learning side of AWS for the AI Practitioner exam. Match business problems to the ready-made AI services (and spot the ones that need no AI at all), then send reviews, a product photo, a receipt and a support call to Comprehend, Translate, Rekognition, Textract and Transcribe, compare a managed service with a keyword rule, and send low-confidence predictions to a person. Then walk the whole machine learning lifecycle on SageMaker AI: split a dataset in S3, train a model in a real training container, score it on held-out data, host it on a real-time endpoint, get predictions, retrain with better hyperparameters and swap the endpoint with no downtime, then choose an inference option and check for drift. Finish with generative AI: chunk and embed policy documents into an S3 Vectors index, retrieve the nearest chunks for a question, ground a Converse prompt in them, check the answer for citations, refuse what the documents do not answer, and price the design by tokens. Then engineer the prompt itself: a zero-shot request, a system prompt with a role and an output format, few-shot examples, temperature and stop sequences, a prompt injection and the defense against it, ROUGE and BLEU scores for candidate answers, business objectives, and the cheapest model that still passes. Finish by making the feature responsible and secure: a Bedrock guardrail with content filters, a denied topic, blocked words and PII rules, a released version beside a changing draft, an evaluation of sample prompts, PII masked before the prompt leaves the app, a KMS key and a private prompt log, a least-privilege policy simulated against two models, and a CloudTrail trail with a responsible AI checklist.',
    labs: [
      { service: 'comprehend', key: 'ai-services-tour', title: 'Tour the AWS AI services: match use cases to Comprehend, Translate, Rekognition, Textract, and Transcribe', stepCount: 8 },
      { service: 'sagemaker', key: 'train-to-endpoint', title: 'Train a model and deploy it to an endpoint with SageMaker AI', stepCount: 8 },
      { service: 's3vectors', key: 'rag-knowledge-base', title: 'Build a RAG knowledge base with S3 Vectors and ground a Bedrock answer in it', stepCount: 8 },
      { service: 'bedrockruntime', key: 'prompt-patterns', title: 'Engineer prompts with the Converse API and evaluate the answers with ROUGE and BLEU', stepCount: 8 },
      { service: 'bedrock', key: 'guardrails-least-privilege', title: 'Guard and secure a generative AI assistant with Bedrock guardrails, KMS, least privilege, and CloudTrail', stepCount: 8 },
    ],
  },
];

let nextLabId = 1;
export const LAB_CURRICULUM: CurriculumLevel[] = LEVEL_DEFINITIONS.map((level) => ({
  ...level,
  labs: level.labs.map((lab) => ({ ...lab, id: nextLabId++, levelId: level.id })),
}));

export const FLAT_CURRICULUM_LABS: CurriculumLabItem[] = LAB_CURRICULUM.flatMap((lvl) => lvl.labs);
export const LAB_COUNT = FLAT_CURRICULUM_LABS.length;

export function getCurriculumItem(service: string, key: string): CurriculumLabItem | undefined {
  return FLAT_CURRICULUM_LABS.find((l) => l.service === service && l.key === key);
}

export function getLevelForLab(service: string, key: string): CurriculumLevel | undefined {
  return LAB_CURRICULUM.find((lvl) => lvl.labs.some((l) => l.service === service && l.key === key));
}

/** Fragment on lab links so the browser lands at the top of the opened lab. */
export const LAB_TOP_ANCHOR = 'lab-top';

/** Relative console view for a lab, used by the side navigation. */
export function labView(service: string, key: string): string {
  return `labs?service=${encodeURIComponent(service)}&lab=${encodeURIComponent(key)}#${LAB_TOP_ANCHOR}`;
}
