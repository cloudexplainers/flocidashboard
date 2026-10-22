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

export const LAB_CURRICULUM: CurriculumLevel[] = [
  {
    id: 'level-1-beginner',
    title: 'Level 1: Beginner Foundations',
    subtitle: 'AWS Cloud Practitioner & Fundamentals',
    badge: 'Beginner',
    color: 'green',
    description: 'Master IAM security basics, S3 object storage operations, fundamental SQS queues, DynamoDB tables, and first Lambda functions.',
    labs: [
      { id: 1, service: 'iam', key: 'create-admin-user', title: 'Create a local admin user', stepCount: 4, levelId: 'level-1-beginner' },
      { id: 2, service: 'iam', key: 'create-user-alice', title: 'Create an IAM user', stepCount: 1, levelId: 'level-1-beginner' },
      { id: 3, service: 'iam', key: 'attach-policy-alice', title: 'Attach a managed policy to Alice', stepCount: 4, levelId: 'level-1-beginner' },
      { id: 4, service: 'iam', key: 'access-key-alice', title: 'Create an access key for Alice', stepCount: 3, levelId: 'level-1-beginner' },
      { id: 5, service: 'iam', key: 'group-membership-alice', title: 'Add Alice to an IAM group', stepCount: 4, levelId: 'level-1-beginner' },
      { id: 6, service: 'iam', key: 'group-policy-floci-developers', title: 'Attach a policy to an IAM group', stepCount: 6, levelId: 'level-1-beginner' },
      { id: 7, service: 's3', key: 'create-bucket', title: 'Create and inspect an S3 bucket', stepCount: 3, levelId: 'level-1-beginner' },
      { id: 8, service: 's3', key: 'object-workflow', title: 'Upload and retrieve an S3 object', stepCount: 5, levelId: 'level-1-beginner' },
      { id: 9, service: 's3', key: 'prefix-copy', title: 'Organize and copy objects with key prefixes', stepCount: 5, levelId: 'level-1-beginner' },
      { id: 10, service: 's3', key: 'metadata-tags', title: 'Add object metadata and tags', stepCount: 5, levelId: 'level-1-beginner' },
      { id: 11, service: 'sqs', key: 'create-queue', title: 'Create and inspect an SQS queue', stepCount: 4, levelId: 'level-1-beginner' },
      { id: 12, service: 'sqs', key: 'message-lifecycle', title: 'Send, receive, and delete an SQS message', stepCount: 4, levelId: 'level-1-beginner' },
      { id: 13, service: 'dynamodb', key: 'crud-query', title: 'Create a DynamoDB table and query items', stepCount: 7, levelId: 'level-1-beginner' },
      { id: 14, service: 'lambda', key: 'create-invoke-logs', title: 'Create, invoke, and inspect a Lambda function', stepCount: 5, levelId: 'level-1-beginner' },
    ],
  },
  {
    id: 'level-2-intermediate',
    title: 'Level 2: Intermediate Architecture',
    subtitle: 'Solutions Architect & Developer Associate',
    badge: 'Intermediate',
    color: 'blue',
    description: 'Implement storage tiering lifecycle rules, KMS encryption, SSM configs, Lambda-driven Secrets Manager rotation, Cognito authentication, encrypted RDS databases with automated backups, EC2 instances, and serverless compute.',
    labs: [
      { id: 15, service: 's3', key: 'version-recovery', title: 'Enable versioning and recover an earlier object version', stepCount: 6, levelId: 'level-2-intermediate' },
      { id: 16, service: 's3', key: 'default-encryption', title: 'Enable default bucket encryption', stepCount: 5, levelId: 'level-2-intermediate' },
      { id: 17, service: 's3', key: 'lifecycle-retention', title: 'Tier and expire logs with an S3 lifecycle rule', stepCount: 4, levelId: 'level-2-intermediate' },
      { id: 18, service: 's3', key: 'bucket-cors', title: 'Configure bucket CORS for a local web app', stepCount: 3, levelId: 'level-2-intermediate' },
      { id: 19, service: 's3', key: 'presigned-url', title: 'Generate temporary access with a presigned URL', stepCount: 3, levelId: 'level-2-intermediate' },
      { id: 20, service: 's3', key: 'multipart-upload', title: 'Complete a multipart upload', stepCount: 7, levelId: 'level-2-intermediate' },
      { id: 21, service: 'kms', key: 'key-alias-encrypt-decrypt', title: 'Protect local app data with KMS', stepCount: 5, levelId: 'level-2-intermediate' },
      { id: 22, service: 'ssm', key: 'parameter-store-config', title: 'Read app configuration from SSM Parameter Store', stepCount: 3, levelId: 'level-2-intermediate' },
      { id: 23, service: 'secretsmanager', key: 'secret-lifecycle', title: 'Create, update, and rotate a Secrets Manager secret', stepCount: 10, levelId: 'level-2-intermediate' },
      { id: 24, service: 'cognito', key: 'user-pool-signup-auth', title: 'Create a Cognito User Pool, register users, and authenticate', stepCount: 5, levelId: 'level-2-intermediate' },
      { id: 25, service: 'cognito', key: 'user-groups-custom-attributes', title: 'Manage Cognito user groups and custom profile attributes', stepCount: 4, levelId: 'level-2-intermediate' },
      { id: 26, service: 'rds', key: 'db-instance-lifecycle', title: 'Provision relational databases and manage instance lifecycles in Amazon RDS', stepCount: 5, levelId: 'level-2-intermediate' },
      { id: 27, service: 'ec2', key: 'guided-imds', title: 'Launch an instance and inspect IMDS', stepCount: 3, levelId: 'level-2-intermediate' },
      { id: 28, service: 'ec2', key: 'guided-userdata', title: 'Run UserData and verify its output', stepCount: 3, levelId: 'level-2-intermediate' },
      { id: 29, service: 'ec2', key: 'guided-web-server', title: 'Publish a web server through a security group', stepCount: 3, levelId: 'level-2-intermediate' },
      { id: 30, service: 'lambda', key: 'runtime-config', title: 'Read app configuration and secrets from Lambda', stepCount: 7, levelId: 'level-2-intermediate' },
      { id: 31, service: 'dynamodb', key: 'lambda-writes', title: 'Write DynamoDB items from Lambda', stepCount: 7, levelId: 'level-2-intermediate' },
    ],
  },
  {
    id: 'level-3-advanced',
    title: 'Level 3: Advanced Event-Driven & APIs',
    subtitle: 'Developer & SysOps Administrator Specialist',
    badge: 'Advanced',
    color: 'blue',
    description: 'Build reliable distributed systems with DLQ redrives, FIFO message ordering, SNS multi-queue fanouts, subscription filters, API Gateway Lambda proxies, EventBridge buses, CloudWatch alarms that evaluate real metrics, and log metric filters.',
    labs: [
      { id: 32, service: 'sqs', key: 'visibility-timeout', title: 'Understand SQS visibility timeout', stepCount: 7, levelId: 'level-3-advanced' },
      { id: 33, service: 'sqs', key: 'delayed-message', title: 'Work with delayed SQS messages', stepCount: 4, levelId: 'level-3-advanced' },
      { id: 34, service: 'sqs', key: 'batch-messages', title: 'Send and delete SQS messages in batches', stepCount: 4, levelId: 'level-3-advanced' },
      { id: 35, service: 'sqs', key: 'queue-configuration', title: 'Configure SQS queue attributes and tags', stepCount: 5, levelId: 'level-3-advanced' },
      { id: 36, service: 'sqs', key: 'dead-letter-redrive', title: 'Route failed messages to a dead-letter queue', stepCount: 12, levelId: 'level-3-advanced' },
      { id: 37, service: 'sqs', key: 'fifo-ordering', title: 'Preserve ordering and deduplicate messages with SQS FIFO', stepCount: 8, levelId: 'level-3-advanced' },
      { id: 38, service: 'sqs', key: 'purge-delete', title: 'Purge messages and delete an SQS queue', stepCount: 6, levelId: 'level-3-advanced' },
      { id: 39, service: 'sns', key: 'sqs-fanout', title: 'Fan out an SNS message to SQS queues', stepCount: 11, levelId: 'level-3-advanced' },
      { id: 40, service: 'sns', key: 'filter-policies', title: 'Route selected SNS messages with subscription filters', stepCount: 12, levelId: 'level-3-advanced' },
      { id: 41, service: 's3', key: 'object-notifications-sqs', title: 'Send S3 object-created events to SQS', stepCount: 7, levelId: 'level-3-advanced' },
      { id: 42, service: 'lambda', key: 'sqs-event-source', title: 'Process SQS messages with Lambda', stepCount: 7, levelId: 'level-3-advanced' },
      { id: 43, service: 'scheduler', key: 'sqs-delivery', title: 'Schedule an EventBridge Scheduler message to SQS', stepCount: 8, levelId: 'level-3-advanced' },
      { id: 44, service: 'apigateway', key: 'lambda-request', title: 'Send an API Gateway request to Lambda', stepCount: 9, levelId: 'level-3-advanced' },
      { id: 45, service: 'eventbridge', key: 'application-spine', title: 'Build an event-driven order application', stepCount: 11, levelId: 'level-3-advanced' },
      { id: 46, service: 'cloudwatch', key: 'metric-alarms', title: 'Publish custom CloudWatch metrics and trigger alarms', stepCount: 4, levelId: 'level-3-advanced' },
      { id: 47, service: 'cloudwatch', key: 'log-groups-metric-filters', title: 'Turn application logs into metrics with CloudWatch Logs metric filters', stepCount: 6, levelId: 'level-3-advanced' },
    ],
  },
  {
    id: 'level-4-senior',
    title: 'Level 4: Senior Security & Workflows',
    subtitle: 'Senior Solutions Architect & Microservices Specialist',
    badge: 'Senior Architect',
    color: 'red',
    description: 'Design enterprise zero-trust IAM architectures, STS session policy delegation, EC2 instance profiles, Step Functions state machines with Choice branching, and containerized microservices on ECS/Fargate.',
    labs: [
      { id: 48, service: 'iam', key: 'inline-policy-alice', title: 'Attach an inline policy to Alice', stepCount: 4, levelId: 'level-4-senior' },
      { id: 49, service: 'iam', key: 'role-trust-policy', title: 'Create a role with a trust policy', stepCount: 4, levelId: 'level-4-senior' },
      { id: 50, service: 'iam', key: 'sts-session-policy', title: 'Assume a role with an STS session policy', stepCount: 3, levelId: 'level-4-senior' },
      { id: 51, service: 'iam', key: 'ec2-instance-profile', title: 'Create an EC2 instance profile', stepCount: 5, levelId: 'level-4-senior' },
      { id: 52, service: 'iam', key: 'identity-enforcement-capstone', title: 'Switch identities and verify IAM enforcement', stepCount: 7, levelId: 'level-4-senior' },
      { id: 53, service: 'ec2', key: 'guided-instance-role', title: 'Use an IAM role from inside an instance', stepCount: 4, levelId: 'level-4-senior' },
      { id: 54, service: 'ec2', key: 'guided-ssm-command', title: 'Execute commands with SSM Run Command', stepCount: 3, levelId: 'level-4-senior' },
      { id: 55, service: 'stepfunctions', key: 'order-processing-workflow', title: 'Build a Step Functions state machine with Choice branching', stepCount: 5, levelId: 'level-4-senior' },
      { id: 56, service: 'stepfunctions', key: 'parallel-retry-workflow', title: 'Execute parallel branches and aggregate state outputs', stepCount: 3, levelId: 'level-4-senior' },
      { id: 57, service: 'ecs', key: 'fargate-microservice', title: 'Deploy containerized microservices with Amazon ECS and AWS Fargate', stepCount: 6, levelId: 'level-4-senior' },
    ],
  },
  {
    id: 'level-5-principal',
    title: 'Level 5: Principal DevOps & K8s Expert',
    subtitle: 'Principal Infrastructure & Networking Architect',
    badge: 'Principal Expert',
    color: 'red',
    description: 'Construct resilient multi-tier VPC networks, diagnostic route troubleshooting, S3 Gateway & SQS Interface PrivateLink Endpoints, EKS Kubernetes control planes with NodeGroups & Fargate, and CloudFormation stacks updated through reviewed change sets.',
    labs: [
      { id: 58, service: 'ec2', key: 'vpc-public-private', title: 'Build a VPC with public and private subnets', stepCount: 12, levelId: 'level-5-principal' },
      { id: 59, service: 'ec2', key: 'security-controls', title: 'Control VPC traffic with security groups and network ACLs', stepCount: 13, levelId: 'level-5-principal' },
      { id: 60, service: 'ec2', key: 'guided-broken-route', title: 'Diagnose and repair a broken route', stepCount: 4, levelId: 'level-5-principal' },
      { id: 61, service: 's3', key: 'bucket-security', title: 'Block public access and apply a bucket policy', stepCount: 5, levelId: 'level-5-principal' },
      { id: 62, service: 'ec2', key: 's3-gateway-endpoint', title: 'Connect a private VPC to S3 with a gateway endpoint', stepCount: 7, levelId: 'level-5-principal' },
      { id: 63, service: 'ec2', key: 'guided-private-s3', title: 'Connect privately to S3 through a VPC endpoint', stepCount: 3, levelId: 'level-5-principal' },
      { id: 64, service: 'ec2', key: 'sqs-interface-endpoint', title: 'Connect a private subnet to SQS with an interface endpoint', stepCount: 7, levelId: 'level-5-principal' },
      { id: 65, service: 'eks', key: 'control-plane-nodegroup', title: 'Provision Kubernetes control planes, node groups, and Fargate profiles with Amazon EKS', stepCount: 5, levelId: 'level-5-principal' },
      { id: 66, service: 'cloudformation', key: 's3-sqs-stack', title: 'Provision S3 and SQS resources with CloudFormation', stepCount: 11, levelId: 'level-5-principal' },
    ],
  },
];

export const FLAT_CURRICULUM_LABS: CurriculumLabItem[] = LAB_CURRICULUM.flatMap((lvl) => lvl.labs);

export function getCurriculumItem(service: string, key: string): CurriculumLabItem | undefined {
  return FLAT_CURRICULUM_LABS.find((l) => l.service === service && l.key === key);
}

export function getLevelForLab(service: string, key: string): CurriculumLevel | undefined {
  return LAB_CURRICULUM.find((lvl) => lvl.labs.some((l) => l.service === service && l.key === key));
}
