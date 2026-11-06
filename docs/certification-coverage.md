# Certification Coverage: CLF-C02, SAA-C03, DVA-C02, AIF-C01, DOP-C02, SOA-C03, and DEA-C01

This document maps the Floci Dashboard workflow labs to the official AWS exam guides for **AWS Certified Cloud Practitioner (CLF-C02)** and **AWS Certified Solutions Architect – Associate (SAA-C03)**, and, since 2026-10-24, **AWS Certified Developer – Associate (DVA-C02)** ([§7](#7-developer--associate-dva-c02)), since 2026-10-26, **AWS Certified AI Practitioner (AIF-C01)** ([§8](#8-ai-practitioner-aif-c01)), since 2026-11-02, **AWS Certified DevOps Engineer – Professional (DOP-C02)** ([§9](#9-devops-engineer--professional-dop-c02)), since 2026-11-04, **AWS Certified CloudOps Engineer – Associate (SOA-C03)** ([§10](#10-cloudops-engineer--associate-soa-c03)), and, since 2026-11-06, **AWS Certified Data Engineer – Associate (DEA-C01)** ([§11](#11-data-engineer--associate-dea-c01)). It also lists what is missing and proposes a prioritized plan to close the gaps. It is the first step toward exam-aligned study tracks for every AWS certification.

It is a planning document. No labs are built here; pick from [the plan](#5-prioritized-lab-plan).

**Checked on:** 2026-10-22, against the AWS exam guides and a disposable Floci **2.3.0** emulator (`compose.labs.yml`, port 14566).

---

## TL;DR

- **The current 91 labs (584 steps) are strong where SAA is strongest:** IAM and identity (SAA 1.1), VPC security and endpoints (SAA 1.2), and decoupled, event-driven design with SQS, SNS, EventBridge, Lambda, Step Functions, and API Gateway (SAA 2.1).
- **There are big holes in high-weight SAA topics:** load balancing and Auto Scaling, Multi-AZ and read replicas, caching, Route 53 and DR, storage classes and tiering, the data and streaming domain (SAA 3.5), and nearly all of cost optimization (SAA domain 4, 20%).
- **For CLF, coverage is lopsided.** The labs serve the hands-on parts of domains 2 and 3. Domain 1 (cloud concepts, 24%) and domain 4 (billing and support, 12%) are almost entirely conceptual, so labs can't cover them. Those need "exam lens" concept cards, not labs.
- **Floci 2.3.0 can support most of the missing SAA hands-on topics.** Verified working: ALB data plane, Auto Scaling groups launching instances, RDS/Aurora/ElastiCache as real containers, Postgres read replicas and snapshots, CloudWatch alarms evaluating real metrics, and S3 Object Lock enforcement. Many other services accept the API but don't simulate the behavior (S3 replication, storage-class retrieval, NAT, CloudFront, SCPs, KMS key policies). Labs on those must be honest about it.
- **Recommended Wave 1 (six labs):** HA web tier (ALB + ASG), RDS/Aurora resilience, S3 data protection and storage tiers, a NAT/three-tier VPC extension, KMS key management, and Route 53 routing and failover.
- **Developer – Associate (DVA-C02) is the third track (2026-10-24).** **Wave D** (six labs, [§7](#7-developer--associate-dva-c02)) is complete: CI/CD with CodeBuild, CodePipeline and CodeDeploy, API Gateway stages and authorizers, DynamoDB application patterns, SAM with AppConfig, Lambda async error handling, and serverless observability. All 13 DVA task statements now have hands-on labs; X-Ray and Private CA stay concept cards because Floci lacks them. **Wave 3** (governance, multi-account, network scale-out, edge security, and cost; seven items, [§5](#wave-3-governance-multi-account-cost-and-clf-oriented-labs)) is complete as of 2026-10-25.
- **AI Practitioner (AIF-C01) is the fourth track (2026-10-26).** Every task statement has a concept card and a four-question quiz, and ten existing labs are tagged, nine of them for the security and governance domain. Floci 2.3.0 runs real SageMaker training jobs and endpoints and real S3 Vectors similarity search, while Bedrock, Comprehend, Rekognition, Textract, Translate, and Transcribe return stub answers. **Wave AI is complete** (five labs shipped 2026-10-26, [§8](#8-ai-practitioner-aif-c01)): the AI services tour, SageMaker from training to endpoint, RAG with S3 Vectors, prompt patterns with evaluation, and responsible AI with guardrails, which together give hands-on practice for nine of the 14 task statements.
- **DevOps Engineer – Professional (DOP-C02) is the fifth track (2026-11-02).** 52 existing labs were tagged, and with the first five Wave P labs (P-1 to P-5, shipped) 18 of its 19 task statements are hands-on (resilience, monitoring, and security come from the SAA and DVA work). **Wave P** (seven labs, [§9](#9-devops-engineer--professional-dop-c02)) fills the professional-level gaps: StackSets across Regions, container and in-place deployments, pipeline test gates, event-driven remediation, centralized logging, and machine identity. Floci 2.4.0 runs StackSets, custom resources, ECS blue/green deployments, composite alarms, log subscriptions, and secret rotation, but not drift detection, SSM Automation, Patch Manager, Config remediation, or ABAC.
- **Data Engineer – Associate (DEA-C01) is the seventh track (2026-11-06).** 57 existing labs are tagged and 16 concept cards cover every task statement. All six Wave E labs shipped the same day as Level 12, so all 17 are hands-on and Wave E is complete. **Wave E** (six labs, [§11](#11-data-engineer--associate-dea-c01)) adds a Redshift Serverless warehouse, the Glue catalog with schema evolution and data quality, event-driven batch ETL, an Airflow pipeline on MWAA, stream processing, and lake governance. Floci 2.4.0 runs Redshift as real PostgreSQL behind the Data API, Athena in DuckDB, real Airflow, and real Kafka, but Glue scripts and crawlers don't run, Glue Data Quality is stored only, and Lake Formation is not enforced.
- **UI direction:** add exam and task tags to each lab in one Python registry, then add a "Study track" switcher (Levels · CLF-C02 · SAA-C03) with domain-weighted progress and concept cards for objectives that can't be labbed. The same structure extends to every other certification.

---

## 1. Sources and method

### Exam guides (checked 2026-10-22)

| Exam | Guide | Domains (weight) | Passing score |
|---|---|---|---|
| CLF-C02 | [docs.aws.amazon.com/aws-certification/latest/cloud-practitioner-02](https://docs.aws.amazon.com/aws-certification/latest/cloud-practitioner-02/cloud-practitioner-02.html) | 1 Cloud Concepts (24%) · 2 Security and Compliance (30%) · 3 Cloud Technology and Services (34%) · 4 Billing, Pricing, and Support (12%) | 700 |
| SAA-C03 | [docs.aws.amazon.com/aws-certification/latest/solutions-architect-associate-03](https://docs.aws.amazon.com/aws-certification/latest/solutions-architect-associate-03/solutions-architect-associate-03.html) | 1 Design Secure Architectures (30%) · 2 Design Resilient Architectures (26%) · 3 Design High-Performing Architectures (24%) · 4 Design Cost-Optimized Architectures (20%) | 720 |

Both exams have 50 scored and 15 unscored questions. Both exam codes are still current. A web search found no announced SAA-C04 or CLF-C03. AWS edits the guide text in place without changing the code; for example, the current text names Amazon SageMaker AI, Amazon Quick, AWS Business Support+, and AWS Unified Operations. **Re-check the guides whenever this document is updated.**

### Lab inventory

Built from the live registry (`dashboard.labs.registry.all_labs()`), which reported 66 labs and 365 steps when this was written (375 after the quick-win fixes in §5). The curriculum levels and lab IDs come from `frontend/src/labCurriculum.ts`.

### Floci probe

All three probe rounds ran against the disposable emulator only (`docker compose -f compose.labs.yml -p flocidashboard-lab-tests up -d --wait`, port 14566, IAM enforcement and signature validation on). The user's Floci on 4566 was not touched. Each topic was tested with real CLI calls. Results fall into three classes:

- **Behaves:** the operation works and the effect is observable (traffic served, instance launched, request denied, alarm fired).
- **Control plane only:** the API accepts and stores the configuration, but the behavior is not simulated (for example, S3 replication is accepted but no object is copied).
- **Unsupported:** an `UnsupportedOperation` or `UnknownOperation` error, or the service is absent.

A few REST-JSON calls failed with `AuthorizationHeaderMalformed`. Those are marked **inconclusive** because they look like a signing or routing quirk with signature validation on, not missing support.

### Coverage legend used below

| Mark | Meaning |
|---|---|
| ● | **Hands-on.** At least one lab directly exercises the objective. |
| ◐ | **Partial.** A lab touches the topic, but a key exam angle is missing. |
| ○ | **None.** No lab covers it. |
| 📖 | **Concept-only.** Not hands-on by nature (for example, support plans or the CAF). Needs a study card or video, not a lab. |

---

## 2. Lab-to-exam mapping

Lab numbers follow the curriculum order in `frontend/src/labCurriculum.ts`. They changed on 2026-10-22, when IAM roles, inline policies, S3 bucket security, and the VPC and security-group labs moved into Levels 1 and 2. Task statement IDs refer to the exam guide numbering, for example `SAA 2.1` = *Design scalable and loosely coupled architectures*. **Relevance** is how directly the lab serves these two exams. "Dev/Ops" means the lab mainly serves the Developer or CloudOps tracks.

### Level 1: Beginner Foundations

| # | Lab | CLF-C02 | SAA-C03 | Relevance | Notes |
|---|---|---|---|---|---|
| 1 | iam/create-admin-user | 2.3 | 1.1 | High | Good place to teach "don't use the root user." It uses an inline AdministratorAccess policy, so the exam lens should contrast that with the AWS managed policy. |
| 2 | iam/create-user-alice | 2.3 | 1.1 | Low | One step; a warm-up. |
| 3 | iam/attach-policy-alice | 2.3 | 1.1 | High | Custom vs managed policies and least privilege, both exam phrases. |
| 4 | iam/access-key-alice | 2.3 | 1.1 | Medium | Access keys. Missing: rotation and "prefer roles over long-term keys." |
| 5 | iam/group-membership-alice | 2.3 | 1.1 | High | |
| 6 | iam/group-policy-floci-developers | 2.3 | 1.1 | High | |
| 7 | iam/inline-policy-alice | 2.3 | 1.1 | Medium | |
| 8 | s3/create-bucket | 3.6 | 3.1 | Medium | |
| 9 | s3/object-workflow | 3.1, 3.6 | 3.1 | Medium | Programmatic access (CLI) is a CLF 3.1 phrase. |
| 10 | s3/prefix-copy | 3.6 | 3.1 | Low | Could note per-prefix request scaling. |
| 11 | s3/metadata-tags | 3.6 | 1.3 | Medium | Classification. Object tags are **not** cost allocation tags; the exam lens should say so. |
| 12 | sqs/create-queue | 3.8 | 2.1 | Medium | |
| 13 | sqs/message-lifecycle | 3.8 | 2.1 | High | |
| 14 | dynamodb/crud-query | 3.4 | 3.3 | Medium | Has a GSI query. Missing: capacity modes, the most-tested DynamoDB angle. |
| 15 | lambda/create-invoke-logs | 3.3 | 2.1 | High | Serverless compute. |

### Level 2: Intermediate Architecture

| # | Lab | CLF-C02 | SAA-C03 | Relevance | Notes |
|---|---|---|---|---|---|
| 16 | iam/role-trust-policy | 2.3 | 1.1 | High | |
| 17 | iam/sts-session-policy | 2.3 | 1.1 | High | STS. |
| 18 | iam/ec2-instance-profile | 2.3 | 1.1, 1.2 | High | |
| 19 | s3/version-recovery | 3.6 | 1.3, 2.2 | High | Data recovery and durability. |
| 20 | s3/default-encryption | 2.2 | 1.3 | High | Encryption at rest (SSE-S3 only; no SSE-KMS). |
| 21 | s3/bucket-security | 2.2, 2.3 | 1.1, 1.3 | High | Resource policies and Block Public Access. |
| 22 | s3/lifecycle-retention | 3.6 | 1.3, 4.1 | High | ✅ Now tiers to STANDARD_IA (30 days) and GLACIER (90 days) before expiring at 365. Floci stores the rule but doesn't move objects. |
| 23 | s3/bucket-cors | — | 1.2 | Low | Dev-oriented. |
| 24 | s3/presigned-url | 2.2 | 1.3 | Medium | Temporary, scoped data access. |
| 25 | s3/multipart-upload | 3.6 | 3.1, 4.1 | Medium | Upload performance and batch strategies. |
| 26 | kms/key-alias-encrypt-decrypt | 2.2 | 1.3 | ◐ | No key policy, rotation, or envelope (data key) pattern. ✅ Those are now in `kms/key-management` (Level 4, W1-5). |
| 27 | ssm/parameter-store-config | 2.3 | 1.2 | Medium | Credential and config storage. |
| 28 | secretsmanager/secret-lifecycle | 2.3 | 1.2, 1.3 | High | ✅ Now adds a Lambda rotation function and real rotation (AWSCURRENT/AWSPREVIOUS), the exam's Secrets Manager vs Parameter Store differentiator. |
| 29 | cognito/user-pool-signup-auth | 2.3 | 1.2 | Medium | User pools only; no identity pools or federation. |
| 30 | cognito/user-groups-custom-attributes | — | 1.2 | Low | Dev. |
| 31 | rds/db-instance-lifecycle | 3.4 | 3.3, 1.3 | ◐ | ✅ Now encrypted at creation with 7-day automated backups. Multi-AZ, read replicas, point-in-time restore and Aurora failover are in `rds/aurora-resilience` (W1-2). |
| 32 | ec2/vpc-public-private | 3.5 | 1.2, 3.4 | ◐ | No NAT gateway, so the private subnet has no egress story. NAT is heavily tested (SAA 1.2, 4.4). ✅ The NAT story is now in `ec2/nat-three-tier` (Level 5, W1-4). |
| 33 | ec2/security-controls | 3.5 | 1.2 | High | Stateful SGs vs stateless NACLs. |
| 34 | ec2/guided-imds | 3.3 | 1.2 | Low | Dev/Ops. |
| 35 | ec2/guided-userdata | 3.1 | 2.2 | Low | Bootstrapping; a light touch on immutable infrastructure. |
| 36 | ec2/guided-web-server | 3.5 | 1.2 | Medium | Security group in action. |
| 37 | ec2/guided-instance-role | 2.3 | 1.1, 1.2 | High | "Use roles, not keys, on EC2," a top SAA answer. |
| 38 | lambda/runtime-config | 2.3 | 1.2 | High | Application configuration and credentials security. |
| 39 | dynamodb/lambda-writes | 3.4 | 2.1 | Medium | |

### Level 3: Advanced Event-Driven and APIs

| # | Lab | CLF-C02 | SAA-C03 | Relevance | Notes |
|---|---|---|---|---|---|
| 40 | sqs/visibility-timeout | 3.8 | 2.1, 2.2 | Medium | A frequent SAA distractor topic. |
| 41 | sqs/delayed-message | — | 2.1 | Low | Dev. |
| 42 | sqs/batch-messages | — | 2.1 | Low | Dev. |
| 43 | sqs/queue-configuration | 4.2 | 2.1 | Low | Long polling and retention are useful. Tags are a light touch on CLF 4.2. |
| 44 | sqs/dead-letter-redrive | 3.8 | 2.1, 2.2 | High | Failure isolation. |
| 45 | sqs/fifo-ordering | 3.8 | 2.1 | High | FIFO vs standard is a classic SAA question. |
| 46 | sqs/purge-delete | — | — | Low | Ops hygiene. |
| 47 | sns/sqs-fanout | 3.8 | 2.1 | High | The SNS-to-SQS fan-out pattern. |
| 48 | sns/filter-policies | 3.8 | 2.1 | High | |
| 49 | s3/object-notifications-sqs | 3.8 | 2.1 | High | Event-driven ingestion. |
| 50 | lambda/sqs-event-source | 3.3 | 2.1, 3.2 | High | Decoupling so components scale independently. |
| 51 | scheduler/sqs-delivery | 3.8 | 2.1 | Medium | |
| 52 | apigateway/lambda-request | 3.3 | 2.1 | High | API creation. HTTP API only; no REST API, throttling, or usage plans. |
| 53 | eventbridge/application-spine | 3.8 | 2.1, 2.2 | High | Capstone. Failure experiments map to SAA 2.2. |
| 54 | cloudwatch/metric-alarms | 2.2 | 2.2, 3.2 | High | ✅ Now reaches ALARM from a real error spike after settling in OK. Floci doesn't run alarm actions (no SNS notification). |
| 55 | cloudwatch/log-groups-metric-filters | 2.2 | 2.2 | Medium | ✅ Now creates a 30-day retention policy and a metric filter, and verifies that only HTTP 500 lines became metric data. |

### Level 4: Workflows, Containers and Enforcement

| # | Lab | CLF-C02 | SAA-C03 | Relevance | Notes |
|---|---|---|---|---|---|
| 56 | iam/identity-enforcement-capstone | 2.3 | 1.1 | High | Role switching with real enforcement. |
| 57 | ec2/guided-ssm-command | 3.1 | — | Low | Dev/Ops (CloudOps). |
| 58 | stepfunctions/order-processing-workflow | — | 2.1 | High | Workflow orchestration. |
| 59 | stepfunctions/parallel-retry-workflow | — | 2.1 | Medium | |
| 60 | ecs/fargate-microservice | 3.3 | 2.1, 3.2, 4.2 | High | When to use containers and Fargate. |

### Level 5: Private Networking and Platform

| # | Lab | CLF-C02 | SAA-C03 | Relevance | Notes |
|---|---|---|---|---|---|
| 61 | ec2/guided-broken-route | 3.5 | 2.2, 3.4 | Medium | Route tables. Troubleshooting itself is CloudOps. |
| 62 | ec2/s3-gateway-endpoint | 3.5 | 1.2, 3.4, 4.4 | High | VPC endpoints for security and to avoid NAT data charges. |
| 63 | ec2/guided-private-s3 | 3.5 | 1.2, 4.4 | Medium | Overlaps with #62. |
| 64 | ec2/sqs-interface-endpoint | 3.5 | 1.2, 3.4 | High | PrivateLink. |
| 65 | eks/control-plane-nodegroup | 3.3 | 2.1, 3.2 | Medium | Associate exams only need "when to pick EKS vs ECS"; node groups and kubeconfig are deeper than needed. |
| 66 | cloudformation/s3-sqs-stack | 3.1 | 2.2 | High | IaC, repeatable processes, and automation for infrastructure integrity. ✅ Now previews and applies an update through a change set (Add vs Modify, Replacement). |

**Summary of relevance.** About 38 labs are high or medium value for SAA and about 30 for CLF. Roughly 10 labs (#23, #30, #34, #41, #42, #46, #57, and parts of #55 and #65) mainly serve the Developer or CloudOps tracks. They should stay, tagged for those tracks later.

---

## 3. Coverage by exam objective (gap analysis)

### CLF-C02

| Task | Topic | Coverage | Covered by | Gap |
|---|---|---|---|---|
| 1.1 | Benefits of the AWS Cloud | ◐ | cloudformation/well-architected-review (a full copy created and deleted in seconds, each benefit matched to evidence), pricing/cloud-economics (economies of scale and trading fixed for variable cost), cloud-value card | Mostly conceptual |
| 1.2 | Well-Architected pillars | ● | cloudformation/well-architected-review (six-pillar review, change set fixes, failure drill, design principles), well-architected card | Well-Architected Tool (not in Floci) |
| 1.3 | Migration and the CAF | ● | dms/migration-toolkit (7 Rs portfolio, CAF perspectives, DataSync vs Snowball, DMS full load and CDC, cutover, Beanstalk replatform), migration-caf card | Application Migration Service, Storage Gateway, schema conversion (not in Floci) |
| 1.4 | Cloud economics | ● | pricing/cloud-economics (three-year TCO against an on-premises rack, elastic Auto Scaling, rightsizing from CPU metrics, BYOL on a Dedicated Host, Lightsail vs EC2), #66 (automation), cloud-economics card | Compute Optimizer (not in Floci) |
| 2.1 | Shared responsibility | ● | ec2/shared-responsibility (guest OS on EC2, RDS maintenance window, Lambda runtime, S3 data protection, responsibility matrix, shared controls), shared-responsibility card | Patch Manager scans (unsupported in Floci) |
| 2.2 | Security, governance, compliance | ● | #20, #21, #24, #54, #55, cloudtrail/governance-tour (CloudTrail trail and data events, Config recorder and managed rule, GuardDuty, Security Hub) | Inspector, Shield, Artifact (concept) |
| 2.3 | Access management | ● | 11 IAM labs, #27, #28, #29, #37, iam/account-hardening (password policy, virtual MFA, deny without MFA, permissions boundary, credential report, root user check) | **IAM Identity Center**, federation |
| 2.4 | Security components and resources | ● | cloudtrail/governance-tour (GuardDuty, Security Hub, which service answers which question), wafv2/edge-throttling (WAF web ACL, managed rules, Shield Standard vs Advanced, which layer does what) | Firewall Manager, Trusted Advisor, Marketplace (concept) |
| 3.1 | Deploying and operating | ● | #9, #35, #57, #66 | Console vs CLI vs IaC is already implicit. Make it explicit. |
| 3.2 | Global infrastructure | ◐ | Subnets pin AZs in #32 and #33, route53/routing-failover (failover and latency routing across Regions), cloudfront/edge-delivery (edge locations, price classes, Global Accelerator across two Regions) | Multi-AZ HA |
| 3.3 | Compute | ◐ | #15, #60, #65, guided EC2, lambda/performance-cost (memory sizing, versions, aliases, concurrency) | **Instance types, Auto Scaling, load balancers** |
| 3.4 | Databases | ◐ | #14, #31, #39, elasticache/cache-aside (Aurora source of truth, ElastiCache in front), dynamodb/capacity-at-scale (capacity modes, TTL, streams, global tables) | EC2-hosted vs managed, DMS |
| 3.5 | Network | ● | #21, #32, #33, #36, #61, #62, #63, #64, route53/routing-failover (hosted zones, routing policies), ec2/network-scale-out (VPC peering, Transit Gateway, flow logs) | VPN and Direct Connect (concept) |
| 3.6 | Storage | ◐ | 13 S3 labs, ec2/block-file-storage (EBS gp3 and io2, EFS), backup/backup-dr-plan (vault, plan, retention) | **Storage classes**, instance store (concept), FSx, Storage Gateway |
| 3.7 | AI/ML and analytics | ◐ | kinesis/streaming-data-lake (Kinesis, Firehose, Glue, Athena) | SageMaker AI, Lex, Quick (concept) |
| 3.8 | Other categories | ◐ | SQS, SNS, EventBridge labs | SES, CodeBuild and CodePipeline, X-Ray, IoT Core, Amplify, Connect, WorkSpaces |
| 4.1 | Pricing models | ◐ / 📖 | costexplorer/cost-visibility (On-Demand prices from the Price List API, a Spot request, three-year cost of each purchasing option), pricing/cloud-economics (Dedicated Host, Lightsail bundles, data transfer out), pricing-models card | Data transfer pricing (card) |
| 4.2 | Billing and cost tools | ● | iam/multi-account-guardrails (consolidated billing tiers, CostCenter tag policy), costexplorer/cost-visibility (tag audit, Budgets with actual and forecast alerts, Cost Explorer by service and tag, Cost Anomaly Detection) | Cost allocation tag activation (unsupported in Floci), Pricing Calculator |
| 4.3 | Support and resources | 📖 + quiz | support-resources card, 6-question practice quiz | Support plans, re:Post, Trusted Advisor, Health, APN. Floci has no Support, Trusted Advisor or Health API, so this stays concept-only. |

**Practice quizzes (2026-10-25):** every CLF task statement also has a practice quiz in the study track (Wave C item C-1).

**CLF reading:** more than a third of CLF weight (all of domains 1 and 4 = 36%, plus parts of 2.1, 2.4, and 3.2) is conceptual. The highest-value CLF additions are concept cards plus a few lightweight labs: account hardening, cost tools, and governance services.

### SAA-C03

| Task | Topic | Coverage | Covered by | Gap |
|---|---|---|---|---|
| 1.1 | Secure access | ● | 11 IAM labs, #21, #37, iam/account-hardening (MFA, permissions boundary, Access Analyzer external access), iam/multi-account-guardrails (Organizations, OUs, SCP guardrails and evaluation, delegated administrator) | Control Tower (concept), IAM Identity Center, directory federation |
| 1.2 | Secure workloads | ● | #27, #28, #29, #32, #33, #38, #62, #63, #64, ec2/nat-three-tier (NAT gateways), cloudtrail/governance-tour (GuardDuty threat alerts, Security Hub), wafv2/edge-throttling (WAF IP, geo, rate-based and managed rules on an API stage) | Shield Advanced, Macie, VPN and Direct Connect |
| 1.3 | Data security controls | ◐ | #19, #20, #21, #22, #24, #26, s3/data-protection-tiers (Object Lock, CRR config), kms/key-management (key policies, rotation, grants, envelope encryption, SSE-KMS), backup/backup-dr-plan (KMS-encrypted vault, vault lock), cloudtrail/governance-tour (audit log bucket, CloudTrail data events, Config compliance) | **ACM/TLS**, secret rotation |
| 2.1 | Scalable, loosely coupled | ● | ~20 labs (SQS, SNS, EventBridge, Lambda, Step Functions, API Gateway, ECS), elasticache/cache-aside (caching) | **Load balancing (ALB), horizontal scaling, CDN, read replicas**, storage types (block vs file) |
| 2.2 | HA and fault tolerance | ◐ | #19, #44, #53, #61, #66, route53/routing-failover (health checks, failover records, failover drill), dynamodb/capacity-at-scale (PITR, global table replica), backup/backup-dr-plan (cross-Region copy, DR strategy by RPO and RTO) | **Multi-AZ, RDS Proxy, Aurora failover**, quotas and throttling, X-Ray |
| 3.1 | Storage performance | ◐ | #25, S3 basics, ec2/block-file-storage (gp3 and io2 limits, Elastic Volumes, EFS throughput and mount targets) | hybrid storage, FSx |
| 3.2 | Elastic compute | ◐ | #50, #60, #65, lambda/performance-cost (memory sweep, reserved concurrency, provisioned concurrency in concept) | **Auto Scaling and scaling metrics, instance families**, Batch and EMR |
| 3.3 | Database performance | ◐ | #14, #31, elasticache/cache-aside (cache-aside, TTLs, invalidation, eviction policy, Multi-AZ replication group), dynamodb/capacity-at-scale (RCU and WCU sizing, auto scaling, streams) | **Read replicas, Aurora, DAX, RDS Proxy** |
| 3.4 | Network performance | ◐ | #32, #61, #62, #64, ec2/nat-three-tier, route53/routing-failover (latency and weighted routing), cloudfront/edge-delivery (CloudFront behaviors and caching, Global Accelerator static IPs and traffic dials), ec2/network-scale-out (peering vs Transit Gateway, segmented TGW route tables, flow logs) | **ALB vs NLB**, VPN, Direct Connect |
| 3.5 | Data ingestion and transformation | ● | kinesis/streaming-data-lake (Data Streams, Firehose to partitioned S3, Glue table with partition projection, Athena, JSON to Parquet) | Lake Formation, DataSync, Glue ETL jobs |
| 4.1 | Cost-optimized storage | ◐ | #22, #25, ec2/block-file-storage (gp3 vs io2 monthly cost, EFS IA and Archive tiering) | backup/backup-dr-plan (cold storage after 90 days, 35-day vs 7-year retention) | **Storage tiers and transitions, Intelligent-Tiering, Requester Pays**, cost tools |
| 4.2 | Cost-optimized compute | ◐ | #60 touches Fargate, lambda/performance-cost (cost per million requests by memory size, arm64 pricing), costexplorer/cost-visibility (Spot request, RI and Savings Plans pricing, a purchasing option per workload) | Instance sizing, ALB vs NLB vs GWLB, scaling strategies |
| 4.3 | Cost-optimized databases | ◐ | dynamodb/capacity-at-scale (provisioned with auto scaling vs on-demand, TTL instead of paid deletes) | **Aurora Serverless**, snapshot retention, DMS |
| 4.4 | Cost-optimized networking | ◐ | #62, #63 (endpoints), ec2/nat-three-tier (NAT per AZ vs shared, NAT vs gateway endpoint), cloudfront/edge-delivery (price class, cache hit ratio, invalidation cost), ec2/network-scale-out (peering vs Transit Gateway cost model), wafv2/edge-throttling (usage plan throttling and quotas) | — |

**SAA reading:** domain 1 is the best-covered, and domain 2.1 is excellent. The largest weighted gaps are, in order:

1. **2.2 HA and fault tolerance.** It is heavily weighted and the labs have almost no Multi-AZ or failover content.
2. **Domain 4 cost optimization (20%).** Nearly empty.
3. **3.3 and 3.4 database and network performance.**
4. **3.5 data ingestion.** Nothing yet.

---

## 4. What Floci can and can't support

Probed on Floci 2.3.0, 2026-10-22. "Lab-ready" means a lab could have meaningful runners **and** verifiers.

### Behaves: lab-ready, with observable effects

| Capability | Evidence | Exam use |
|---|---|---|
| ALB/NLB/GWLB creation, listeners, target groups | An ALB listener served `hello-from-alb` to a request from inside an emulated EC2 instance | SAA 2.1, 2.2, 3.4, 4.2; CLF 3.3 |
| Auto Scaling groups from launch templates, across two AZ subnets | Raising desired capacity from 1 to 2 launched 2 running instances. Target-tracking policy accepted. | SAA 2.1, 3.2, 4.2; CLF 3.3 |
| RDS Postgres/MySQL as real containers, `--multi-az`, `--storage-encrypted`, forced-failover reboot | Instances `available` with real ports | SAA 2.2, 3.3, 1.3 |
| RDS **Postgres** read replica and DB snapshot | Both succeeded | SAA 2.2, 3.3, 4.3 |
| Aurora PostgreSQL/MySQL clusters, Serverless v2 config, reader instance, cluster snapshot | All succeeded (real containers) | SAA 3.3, 4.3; CLF 3.4 |
| ElastiCache Redis (backed by Valkey) | A real container started. Checked again 2026-10-24: a Lambda on Floci's Docker network reached it at the primary endpoint and ran GET, SET with EX, TTL and DEL; the replica, AZs, security groups and parameter group are stored only (see W2-1) | SAA 2.1, 3.3; CLF 3.4 |
| CloudWatch alarms evaluating real `put-metric-data` | The alarm reached `ALARM` without `set-alarm-state` | SAA 2.2, 3.2 |
| CloudWatch Logs metric filters and retention | Two "HTTP 500" lines produced a metric Sum of 2; the 200 line didn't count | CLF 2.2; SAA 2.2 |
| Secrets Manager rotation with a Lambda | Floci invoked all four rotation steps and promoted the new version (it pre-creates an empty AWSPENDING version, which the lab's function handles) | SAA 1.2, 1.3; CLF 2.3 |
| CloudFormation change sets | `Add` and in-place `Modify` (`Replacement` False) reported; execution reached `UPDATE_COMPLETE` and applied the SQS changes | SAA 2.2; CLF 3.1 |
| IAM identity policies, **permission boundaries**, and bucket-policy explicit deny | Enforced (AccessDenied) | SAA 1.1, 1.3 |
| S3 Object Lock (GOVERNANCE, COMPLIANCE, legal hold) | Deleting a locked version was denied; the governance bypass worked, but not against COMPLIANCE or a legal hold; COMPLIANCE retention could not be shortened or changed to GOVERNANCE, and expired retention allowed the delete | SAA 1.3 |
| Lambda versions, aliases, reserved concurrency, memory size, function URLs | Verified 2026-10-24: weighted alias routing and reserved concurrency throttling are real, and function URLs invoke the alias. CPU doesn't scale with memory, AWS_IAM URLs answer unsigned requests, UpdateAlias never clears weights, and provisioned concurrency requests are routed to S3 (see W2-4) | SAA 3.2, 4.2 |
| DynamoDB on-demand billing, Streams, TTL, PITR flag, replica update | Verified 2026-10-24: TTL deletes expired items within about a minute, and a filtered stream mapping invokes Lambda with retries. Capacity is never enforced, auto scaling never scales, PITR is a flag, and a replica has no table in the other Region (see W2-3) | SAA 3.3, 4.3, 2.2 |
| EFS file system, mount target, IA lifecycle | Configuration only (checked 2026-10-24): no NFS server, and every mount target reports the same AZ and IP (see W2-5) | SAA 3.1, 4.1; CLF 3.6 |
| EBS `create-volume` (gp3, io2) and `modify-volume` | Verified 2026-10-24: an attached volume is a real loop device in the instance container, so a filesystem and its data move with the volume, and modify-volume grows the device. IOPS and throughput limits aren't checked; CreateSnapshot is unsupported (see W2-5) | SAA 3.1, 4.1 |
| Spot instance request | Launched a real instance | SAA 4.2; CLF 4.1 |
| Kinesis put-record, Firehose stream, Athena query execution | Verified end to end 2026-10-24: Firehose read the stream and wrote S3 objects under the evaluated `!{timestamp}` prefix within seconds, and Athena (DuckDB SQL) returned the right counts from a Glue table over the `dt=` folders. Athena DDL and CTAS are unsupported, and Firehose Parquet conversion fails with signature validation on (see W2-2) | SAA 3.5; CLF 3.7 |

### Control plane only: config is stored, behavior is not simulated

A lab can teach configuration and inspection with these, but it must not claim the effect happened.

| Area | What's accepted | What's not simulated |
|---|---|---|
| S3 | Storage classes on upload, lifecycle **transitions**, Intelligent-Tiering, replication, Requester Pays, Transfer Acceleration, website config, SSE-KMS | Objects never move tiers. A GLACIER object is readable without a restore. Replication copied nothing. |
| VPC | NAT gateway + EIP, private NAT gateway, VPC peering, Transit Gateway, flow logs | Checked 2026-10-23 for NAT: no traffic is routed or translated. An instance in a subnet with no default route reached the internet through Docker's bridge. A NAT gateway is `available` at once; Floci accepts an EIP already used by another NAT gateway, a public NAT gateway with no EIP, releasing an EIP in use, and deleting a subnet that holds a NAT gateway (AWS refuses all four). A route to a deleted NAT gateway stays `active` (AWS: `blackhole`). Egress-only internet gateways and Reachability Analyzer are unsupported. Peering reachability is untested. |
| Edge | CloudFront distribution, Global Accelerator | Checked 2026-10-24: a CloudFront distribution serves requests for real (routes by path to S3 origins, applies the default root object and custom error pages, and a disabled distribution returns 404), but nothing is cached, HTTP isn't redirected to HTTPS, and OAC, bucket policies and Block Public Access don't stop anonymous reads. A behavior that targets a missing origin is accepted. Global Accelerator stores and validates accelerators, listeners and endpoint groups (traffic dial 0 to 100, port ranges, one group per Region, delete only when disabled), but passes no traffic, never health checks (every endpoint is HEALTHY), and accepts any endpoint ID (see W2-7) |
| Route 53 | Hosted zones, private zones and VPC associations, failover, weighted, latency and alias records, health checks | Checked again 2026-10-23: health checks never probe (every check reports success) and Floci drops their Regions, Inverted and calculated or alarm settings; `UpdateHealthCheck` replaces the whole config. Records keep failover, weighted, latency and alias fields but lose GeoLocation and MultiValueAnswer. No routing validation: two primaries, weights above 255, mixed policies, a CNAME at the apex, an alias to a missing name, and missing health check IDs are all accepted. Floci's DNS server answers A records in private zones only (to any client, every value); public zones get NXDOMAIN, and `TestDNSAnswer` is unsupported. |
| Org governance | Organizations, OUs, create-account, SCP create and attach, IAM Identity Center instance, Control Tower list | SCP enforcement wasn't observable. The test ran in the management account, which AWS also exempts, so the result is inconclusive. |
| KMS | Rotation, key policies, grants, `generate-data-key` | Checked again 2026-10-23: **a key-policy Deny was not enforced**, and a key policy without the root statement didn't block an IAM-allowed user. KMS calls are authorized from IAM policies only, so a grant gave its grantee nothing. Malformed or lockout policies and missing principals are accepted. Real: data keys, encryption context on decrypt, on-demand rotation (old ciphertext still decrypts), Disabled and PendingDeletion refusing requests. Automatic rotation is a flag with no period; a cancelled deletion returns to Enabled (AWS: Disabled); `ListKeyRotations` is unsupported. SSE-KMS on S3 never calls KMS. |
| Security services | GuardDuty detector, Security Hub, Macie, WAFv2 web ACL with a rate rule, Network Firewall list, ACM request | No findings and no WAF blocking were observed |
| Audit | CloudTrail trail, Config recorder and managed rule | `lookup-events` returned nothing. The Config rule stayed `INSUFFICIENT_DATA`. |
| Data protection | DLM | No lifecycle policies (AWS Backup is now in `backup/backup-dr-plan`) |
| Migration and transfer | DMS replication instance, DataSync location, Transfer Family server | No data moved |
| Cost | Budgets, Cost Explorer (synthetic, near-zero amounts), CUR definitions, Pricing `get-products` | No real spend |
| Other | API Gateway REST usage plans and API keys, Glue database and crawler, Lake Formation settings, Batch, Elastic Beanstalk, ECR, Lightsail, IoT things, SES identity, CodeBuild, CodePipeline, CodeDeploy, Bedrock guardrails, Comprehend, Rekognition, Textract and Transcribe (stubs), Translate (echo) | Varies; AI services in [§8](#8-ai-practitioner-aif-c01) |

### Unsupported in Floci 2.3.0

- **Networking:** Site-to-Site VPN (`CreateVpnGateway`, `CreateCustomerGateway`), Direct Connect, placement groups.
- **Storage:** EBS **snapshots**, FSx, Storage Gateway.
- **Databases:** RDS **MySQL** snapshots and read replicas (Postgres works), DynamoDB `CreateBackup` and `RestoreTableToPointInTime`, DAX.
- **Purchasing and cost:** Reserved Instance offerings, Savings Plans, Compute Optimizer, Pricing Calculator API, Cost Explorer cost-allocation-tag APIs.
- **Ops:** CloudWatch alarm actions (an alarm in ALARM sent nothing to its SNS topic), `describe-alarm-history`, CloudWatch composite alarms, SSM Session Manager, Automation, and Patch Manager, X-Ray, AWS Health, Trusted Advisor and Support, Resilience Hub.
- **Security and compliance:** Shield, AWS Artifact.
- **AI and end-user:** QuickSight, Polly, Lex, Kendra, Personalize, Amplify, WorkSpaces, App Runner, Outposts. (Rekognition is a stub, not unsupported; see [§8](#8-ai-practitioner-aif-c01).)
- **Inconclusive** (`AuthorizationHeaderMalformed` with signature validation on): Inspector2, Detective, GuardDuty and Access Analyzer `ListFindings`, Lambda provisioned concurrency. Retest these with `FLOCI_AUTH_VALIDATE_SIGNATURES=false` before ruling them out. The Bedrock control plane was on this list; rechecked 2026-10-26, guardrails work and `ListFoundationModels` is the operation Floci lacks (it answers unsupported operations with this error).

### Gap verdicts

| Gap | Floci verdict | Approach |
|---|---|---|
| ALB + ASG, scaling | ✅ Behaves | Lab |
| Multi-AZ, read replicas, Aurora, snapshots | ✅ Behaves (use Postgres) | Lab |
| Caching (ElastiCache) | ✅ Behaves | Lab |
| Data lake and streaming | ✅ Behaves (Athena speaks DuckDB SQL) | Lab |
| Lambda sizing and concurrency, DynamoDB capacity | ✅ Behaves | Lab |
| Object Lock, permission boundaries | ✅ Behaves | Lab |
| NAT, peering, TGW, CloudFront, Global Accelerator, Route 53 | ⚠️ Control plane | Lab that teaches configuration and design, with an explicit "what Floci doesn't simulate" note |
| Storage tiers, replication, Backup | ⚠️ Control plane | Lab on configuration plus exam lens |
| KMS key policies, SCPs | ⚠️ Not enforced | Lab inspects policies but doesn't fake a deny; concept card explains evaluation logic |
| CloudTrail, Config, GuardDuty, Security Hub | ⚠️ Control plane | One "governance services tour" lab plus concept cards |
| Budgets and Cost Explorer | ⚠️ Synthetic | Short lab plus concept cards |
| VPN, Direct Connect, Storage Gateway, FSx, Snow family, Shield, Artifact, Trusted Advisor, support plans, AI services, WorkSpaces | ❌ | **Concept cards and videos only** |
| CLF Domain 1, shared responsibility, pricing models | 📖 | **Concept cards and videos only** |

---

## 5. Prioritized lab plan

**Priority = exam weight × size of gap × Floci fidelity.** Every new lab follows the existing pattern in [`buildinglabs.md`](../buildinglabs.md) and [`CONTRIBUTING.md`](../CONTRIBUTING.md): one module per service in `dashboard/labs/`, runners, verifiers, status, reset, and `check_labs` on the disposable emulator. Step counts are estimates.

### Wave 1: biggest SAA gaps, high Floci fidelity

| # | Proposed lab | Service module | Steps | SAA | CLF | Floci | Why first |
|---|---|---|---|---|---|---|---|
| ✅ W1-1 | **Highly available web tier** (shipped 2026-10-23 as `elasticloadbalancing/ha-web-tier`, 12 steps, SAA 2.2, 2.1, 3.2 and 3.4; Floci stores the target tracking policy without running it and replaces every instance at once in a refresh): launch template, ALB across 2 AZ subnets, target group with health check, ASG (min 2), target-tracking policy, scale out, curl the ALB | `elbv2_labs.py` (new) + `autoscaling` | ~11 | 2.1, 2.2, 3.2, 3.4, 4.2 | 3.2, 3.3 | ✅ (confirm ALB forwards to ASG targets, not just fixed responses) | The single most-tested SAA architecture, and nothing covers it today |
| ✅ W1-2 | **RDS and Aurora resilience** (shipped 2026-10-23 as `rds/aurora-resilience`, 10 steps, SAA 2.2 and 3.3; Floci's point-in-time restore and replicas copy the source once, Multi-AZ runs no standby): Postgres Multi-AZ with encryption and backup retention, read replica, manual snapshot, forced failover; then an Aurora cluster with a reader and a cluster snapshot | `rds_labs.py` (extend) | ~10 | 2.2, 3.3, 1.3, 4.3 | 3.4 | ✅ | Read replicas vs Multi-AZ is a top SAA trap |
| ✅ W1-3 | **S3 data protection and storage tiers** (shipped 2026-10-23 as `s3/data-protection-tiers`, 13 steps, SAA 1.3, 4.1 and 2.2; Object Lock is enforced, while storage classes, Intelligent-Tiering, lifecycle rules, replication and Requester Pays are stored without effect): upload with storage classes, lifecycle transitions to STANDARD_IA, GLACIER, and expiry, Intelligent-Tiering, Object Lock compliance (real deny), cross-Region replication config, Requester Pays | `s3_labs.py` (extend) | ~10 | 1.3, 4.1, 2.2 | 3.6, 4.1 | ✅ / ⚠️ (tiering and replication are config only) | Covers SAA 4.1, the largest single cost task |
| ✅ W1-4 | **Three-tier VPC with NAT** (shipped 2026-10-23 as `ec2/nat-three-tier`, 10 steps, SAA 1.2, 4.4, 3.4 and 2.2; Floci stores the NAT gateways and routes but routes no traffic, so the lab checks the configuration, including the AWS rules Floci skips): extend the VPC lab with an EIP, a NAT gateway in the public subnet, a private default route via NAT, and a database subnet tier; exam lens on one NAT per AZ vs a shared NAT, and NAT vs gateway endpoint cost | `ec2_labs.py` (extend #32 or add a new lab) | ~8 | 1.2, 3.4, 4.4 | 3.5 | ⚠️ Control plane (check whether private-instance egress works) | NAT is in three SAA task statements |
| ✅ W1-5 | **KMS key management** (shipped 2026-10-23 as `kms/key-management`, 8 steps, SAA 1.3; Floci enforces the encryption context, on-demand rotation and key states, stores key policies and grants without using them, and S3 never calls KMS, so the lab checks the policy and grant itself): customer managed key with key policy, automatic rotation, grant, envelope encryption via `generate-data-key`, SSE-KMS bucket default | `kms_labs.py` (extend) | ~7 | 1.3 | 2.2 | ✅ / ⚠️ (key policy not enforced; inspect only) | Keys, rotation, and access policies are explicit SAA 1.3 skills |
| ✅ W1-6 | **Route 53 routing and failover** (shipped 2026-10-23 as `route53/routing-failover`, 8 steps, SAA 2.2 and 3.4; Floci stores zones, records and health checks but never probes or routes, so the lab checks the routing rules itself and plays the resolver in a failover drill): hosted zone, health check, failover primary and secondary records, weighted records; exam lens on all routing policies | `route53_labs.py` (new) | ~7 | 2.2, 3.4 | 3.5 | ⚠️ Records verifiable; resolution not | Route 53 underpins every DR answer |

### Wave 2: performance, data, and remaining resilience

| # | Proposed lab | Steps | SAA | CLF | Floci |
|---|---|---|---|---|---|
| ✅ W2-1 | **Cache-aside with ElastiCache** (shipped 2026-10-24 as `elasticache/cache-aside`, 8 steps, SAA 3.3 and 2.1; the cache, the Aurora database and the Lambda reader are real, while the replica, security groups, AZs and the eviction policy are stored only, so the lab checks them itself): Redis cluster, a Lambda or EC2 reader that checks the cache before RDS, TTL | ~8 | 2.1, 3.3 | 3.4 | ✅ (Lambda reaches the cache over Floci's Docker network) |
| ✅ W2-2 | **Streaming ingestion to a data lake** (shipped 2026-10-24 as `kinesis/streaming-data-lake`, 9 steps, SAA 3.5 and 4.1; the stream, Firehose delivery and Athena queries are real, while the crawler, workgroup limits, and Athena DDL are not, so the lab defines the table through Glue and runs the DuckDB equivalent of CTAS): Kinesis stream, Firehose to S3, Glue crawler and table, Athena query; exam lens on Data Streams vs Firehose and on Parquet | ~10 | 3.5 | 3.7 | ✅ Verified end to end |
| ✅ W2-3 | **DynamoDB at scale** (shipped 2026-10-24 as `dynamodb/capacity-at-scale`, 8 steps, SAA 4.3, 3.3 and 2.2; TTL, the stream processor and its filter are real, while capacity, auto scaling, PITR and the replica are stored only, so the lab checks them itself): provisioned vs on-demand, Application Auto Scaling target, TTL, Streams to Lambda, PITR, global table replica | ~9 | 3.3, 4.3, 2.2 | 3.4 | ✅ (no `CreateBackup` or restore) |
| ✅ W2-4 | **Lambda performance and cost** (shipped 2026-10-24 as `lambda/performance-cost`, 8 steps, SAA 3.2 and 4.2; versions, the weighted canary, throttling and the function URL are real, while CPU doesn't scale with memory and URL auth isn't checked, so the lab says so and checks the auth type itself): memory sizing, versions, alias with weighted routing, reserved concurrency, function URL | ~7 | 3.2, 4.2 | 3.3 | ✅ (provisioned concurrency inconclusive) |
| ✅ W2-5 | **Block and file storage** (shipped 2026-10-24 as `ec2/block-file-storage`, 8 steps, SAA 3.1 and 4.1; EBS attach, format, grow and move are real through SSM, while volume limits and all of EFS are stored only, so the lab checks them itself): EBS gp3 vs io2, modify-volume, attach to an instance; EFS with mount targets in 2 AZs and an IA lifecycle | ~8 | 3.1, 4.1 | 3.6 | ✅ (no snapshots) |
| ✅ W2-6 | **Backup and DR plan** (shipped 2026-10-24 as `backup/backup-dr-plan`, 7 steps, SAA 2.2, 1.3 and 4.1; backup jobs complete and the vault lock blocks deletes, while restore, copy jobs and tag selections aren't run or kept, so the lab checks them itself): AWS Backup vault, plan, and retention, plus a DR strategy chooser (backup and restore, pilot light, warm standby, active-active) mapped to RPO and RTO | ~6 | 2.2, 1.3, 4.1 | 3.6 | ✅ (no restore) |
| ✅ W2-7 | **Edge delivery** (shipped 2026-10-24 as `cloudfront/edge-delivery`, 9 steps, SAA 3.4 and 4.4; the distribution routes requests to the S3 origins for real, while caching, HTTPS redirects, OAC and all of Global Accelerator are stored only, so the lab checks them itself): CloudFront distribution with S3 origin and OAC, cache behaviors, Global Accelerator; exam lens on CloudFront vs Global Accelerator | ~6 | 3.4, 4.4 | 3.2 | ✅ (no caching; Global Accelerator is control plane) |

### Wave 3: governance, multi-account, cost, and CLF-oriented labs

| # | Proposed lab | Steps | SAA | CLF | Floci |
|---|---|---|---|---|---|
| ✅ W3-1 | **Account hardening** (shipped 2026-10-25 as `iam/account-hardening`, 7 steps, SAA 1.1 and CLF 2.3; the password policy, MFA codes, the deny without MFA and the boundary are enforced, while MFA sessions and analyzer findings are not, so the lab says so and reviews the policies itself): password policy, virtual MFA device, credential report, Access Analyzer, permission boundary (enforced) | ~7 | 1.1 | 2.3 | ✅ / ⚠️ |
| ✅ W3-2 | **Multi-account guardrails** (shipped 2026-10-25 as `iam/multi-account-guardrails`, 7 steps, SAA 1.1 and CLF 4.2; the Organizations control plane is real, while SCP content is not validated and member accounts have no identities, so the lab validates the SCPs and evaluates sample requests through the OU tree itself): Organizations, OUs, member account, SCP; exam lens on SCP evaluation, consolidated billing, and Control Tower | ~7 | 1.1 | 4.2 | ⚠️ SCPs not enforced |
| ✅ W3-3 | **Governance services tour** (shipped 2026-10-25 as `cloudtrail/governance-tour`, 8 steps, SAA 1.3 and 1.2, CLF 2.2 and 2.4; Floci stores the trail, recorder, rule, detector and hub but records no events, evaluates no rules and creates no findings, so the lab evaluates the Config rule and matches sample findings itself): CloudTrail trail to S3, Config recorder and managed rule, GuardDuty, Security Hub; "which service answers which question" | ~8 | 1.2, 1.3 | 2.2, 2.4 | ⚠️ Control plane |
| ✅ W3-4 | **Network scale-out** (shipped 2026-10-25 as `ec2/network-scale-out`, 8 steps, SAA 3.4 and 4.4, CLF 3.5; Floci stores peering, Transit Gateway routing and flow logs but moves no packets, accepts transitive routes and overlapping peering, never propagates routes and writes no flow log records, so the lab evaluates routing and sample records itself): VPC peering vs Transit Gateway, plus VPC flow logs | ~8 | 3.4, 4.4 | 3.5 | ⚠️ Control plane |
| ✅ W3-5 | **Edge security and throttling** (shipped 2026-10-25 as `wafv2/edge-throttling`, 7 steps, SAA 1.2, 4.4 and 2.2, CLF 2.4; API keys are enforced, while WAF never blocks and usage plans drop throttle and quota, so the lab evaluates the web ACL and a token bucket itself): WAF rate-based rule on the ALB or API, API Gateway REST usage plan, API key, throttling | ~7 | 1.2, 2.2, 4.4 | 2.4 | ⚠️ (check whether WAF and usage-plan limits are enforced) |
| ✅ W3-6 | **Cost visibility** (shipped 2026-10-25 as `costexplorer/cost-visibility`, 7 steps, SAA 4.2 and 4.1, CLF 4.2 and 4.1; budgets, monitors and Spot requests are stored, while spend is synthetic and alerts, forecasts, anomalies and RI or Savings Plans offerings are missing, so the lab evaluates them from sample spend): Budgets with an alert, Cost Explorer query, tag strategy, Pricing API lookup, Spot request | ~6 | 4.x | 4.1, 4.2 | ⚠️ Synthetic data |
| ✅ W3-7 | **Purchasing options exam lens** (shipped 2026-10-25: the `pricing-models` card now covers Compute vs EC2 Instance Savings Plans, Standard vs Convertible RIs, payment options, Dedicated Hosts vs Instances and Capacity Reservations, and the cost-visibility lab ends with a purchasing option per workload): On-Demand, RI, Savings Plans, Spot, Dedicated Hosts and Instances | — | 4.2 | 4.1 | 📖 Concept card |

### Wave C: close out Cloud Practitioner (CLF-C02)

Started 2026-10-25. Before Wave C, labs covered 13 of the 19 CLF task statements; 1.1, 1.2, 1.3, 1.4, 2.1 and 4.3 had concept cards only. A probe of Floci 2.3.0 found DMS (replication instances, endpoints, tasks that start and stop, endpoints protected while in use), DataSync (agents, locations, tasks; executions unsupported), the Pricing Calculator (workload estimates only, total cost 0), Service Quotas (list and increase requests) and Elastic Beanstalk (environments go Ready and Green at once) as control planes. There is no Support, Trusted Advisor, Health, Well-Architected Tool, Storage Gateway or Application Migration Service, and SSM patch baselines and maintenance windows can't be created. So 4.3 stays concept-only with a quiz.

Wave C finished on 2026-10-25: labs now cover 18 of the 19 CLF task statements, and every task has a practice quiz.

| # | Item | Steps | SAA | CLF | Floci |
|---|---|---|---|---|---|
| ✅ C-1 | **Practice quizzes** (shipped 2026-10-25 in `dashboard/labs/quizzes.py`): 76 exam-style questions, 3 to 6 for every CLF task statement, with explanations, a best score per task in the browser, and a "quizzes passed" count in the track header | — | — | all 19 | — |
| ✅ C-2 | **Shared responsibility tour** (shipped 2026-10-25 as `ec2/shared-responsibility`, 7 steps, CLF 2.1 with 3.3, 3.4 and 3.6, SAA 1.1, in Level 8; the guest OS, Lambda runtime, RDS settings and Block Public Access are real, while patch baselines can't be created and AWS-RunPatchBaseline never runs, so the lab reads the guest OS itself): the same app on EC2, RDS, Lambda and S3, with who patches, encrypts and controls access on each, a responsibility matrix built from what was deployed, and twelve tasks sorted into AWS, customer and shared | 7 | 1.1 | 2.1 | ⚠️ Patching control plane only |
| ✅ C-3 | **Migration toolkit** (shipped 2026-10-25 as `dms/migration-toolkit`, 7 steps, CLF 1.3 with 3.4 and 3.6, SAA 4.3 and 3.5, a new lab service; DMS, DataSync and Beanstalk are control planes and no data moves, so the lab applies the table mappings and compares DataSync with Snowball itself): 7 Rs portfolio with the CAF perspectives, DataSync NFS to S3, DMS full load and CDC with cutover, Beanstalk replatform | 7 | 4.3 | 1.3, 3.4 | ⚠️ Control plane |
| ✅ C-4 | **Well-Architected review** (shipped 2026-10-25 as `cloudformation/well-architected-review`, 7 steps, CLF 1.2 with 1.1 and 3.1, SAA 2.2 and 2.1; the stacks, change set and SQS redrive are real, and the lab scores the pillars itself because the Well-Architected Tool is not emulated): deploy a CloudFormation workload, review it pillar by pillar, fix it with a change set, a failure drill, review again, and a disposable full copy matched to the design principles and cloud benefits | 7 | 2.2 | 1.2, 1.1 | ✅ |
| ✅ C-5 | **Cloud economics** (shipped 2026-10-25 as `pricing/cloud-economics`, 7 steps, CLF 1.4 with 1.1 and 4.1, SAA 4.2, a new lab service; the Auto Scaling group, metrics, Dedicated Host and Lightsail bundles are real, while the Price List covers three instance types and Pricing Calculator totals stay at $0, so the lab fills in typical list prices and does the math itself): Price List lookups, a Pricing Calculator estimate, on-premises TCO vs pay as you go, elastic capacity, rightsizing from metrics, BYOL on a Dedicated Host, Lightsail vs EC2 | 7 | 4.2 | 1.4, 1.1 | ⚠️ Partial prices |

### Small extensions to existing labs (quick wins)

The six rows marked ✅ shipped on 2026-10-22 and pass `check_labs` on Floci 2.3.0.

| Lab | Extension | Exam value |
|---|---|---|
| ✅ #22 s3/lifecycle-retention | Add a transition rule, not just expiry | SAA 4.1 |
| ✅ #28 secretsmanager/secret-lifecycle | Add a rotation Lambda and `rotate-secret` | SAA 1.2, 1.3; CLF 2.3 |
| ✅ #54 cloudwatch/metric-alarms | Replace `set-alarm-state` with a real threshold breach | SAA 2.2, 3.2 |
| ✅ #55 cloudwatch/log-groups-metric-filters | Add the metric filter and retention the title promises | CLF 2.2 |
| ✅ #31 rds/db-instance-lifecycle | Add `--storage-encrypted` and `--backup-retention-period` | SAA 1.3 |
| ✅ #66 cloudformation/s3-sqs-stack | Add a change set (drift detection still open) | SAA 2.2; CLF 3.1 |
| #52 apigateway/lambda-request | Exam lens on HTTP API vs REST API (usage plans, caching, WAF) | SAA 2.1 |
| #4 iam/access-key-alice | Add key rotation (create a second key, deactivate the first) | CLF 2.3 |
| ✅ #15, #31, #34 | Shared-responsibility exam-lens card (who patches what); shipped as the card plus the `ec2/shared-responsibility` lab (C-2) | CLF 2.1 |

### Concept-only content (cards and videos, not labs)

These are best as short **exam-lens cards** in the dashboard and as @cloudexplainers videos:

- **CLF Domain 1:** cloud benefits, Well-Architected pillars, CAF and migration strategies (the 7 Rs), cloud economics.
- **Shared responsibility model** across EC2, RDS, and Lambda.
- **Pricing models and support:** support plans, Trusted Advisor, AWS Health, Artifact, Marketplace, APN, re:Post.
- **Hybrid connectivity:** VPN vs Direct Connect, Storage Gateway, FSx family, Snow family, DataSync vs Transfer Family.
- **AI/ML and analytics names:** SageMaker AI, Lex, Polly, Rekognition, Comprehend, Quick, Bedrock.
- **End-user and other services:** WorkSpaces, AppStream 2.0, Amplify, Connect, IoT Core.

### Projected coverage after each wave

| | Today | + Wave 1 | + Wave 2 | + Wave 3 and cards |
|---|---|---|---|---|
| SAA task statements with ● hands-on | 3 of 14 | 7 of 14 | 11 of 14 | 13 of 14 (3.5 depends on Floci) |
| CLF task statements with ● or 📖 card | 3 of 19 | 7 of 19 | 10 of 19 | 19 of 19 |

These are rough counts. A task counts as ● only when its headline skills are hands-on.

---

## 6. Curriculum UI: exam tags and study tracks

**Status (2026-10-22): implemented.** Tags, concept cards, and the guides live in `dashboard/labs/exams.py`; the catalog returns `exams` per lab and `/api/labs/exams/` returns each guide with its labs and cards; the labs page has a Levels · CLF-C02 · SAA-C03 switcher (`?track=` in the URL). `dashboard/tests_exams.py` enforces valid tags and that every task statement has a lab or a concept card. The sketch below was the plan; the code is the reference now.

### Data model (one source of truth)

The lab definitions live in Python, and `labCurriculum.ts` already duplicates titles and step counts. Put exam data on the backend so the frontend, `check_labs`, tests, and video tooling all read the same thing:

```python
# dashboard/labs/exams.py  (proposed)
EXAM_GUIDES = {
    'SAA-C03': {
        'title': 'AWS Certified Solutions Architect – Associate',
        'level': 'associate',
        'guide_url': 'https://docs.aws.amazon.com/aws-certification/latest/solutions-architect-associate-03/...',
        'checked': '2026-10-22',
        'domains': [
            {'id': '1', 'title': 'Design Secure Architectures', 'weight': 30, 'tasks': [
                {'id': '1.1', 'title': 'Design secure access to AWS resources'},
                ...
            ]},
            ...
        ],
    },
    'CLF-C02': {...},
}

# Many-to-many tags; 'primary' marks the task the lab is mainly about.
LAB_EXAM_TAGS = {
    ('s3', 'version-recovery'): [('SAA-C03', '1.3', 'primary'), ('SAA-C03', '2.2'), ('CLF-C02', '3.6')],
    ...
}

# Objectives that cannot be labbed become concept cards (later linked to videos).
CONCEPT_CARDS = {
    'shared-responsibility': {'title': '...', 'exams': [('CLF-C02', '2.1')], 'video_url': None},
    ...
}
```

The `/api/labs` payload gains `exams: [{exam, task, primary}]` per lab. A new `/api/exams` endpoint returns the guides plus each task's labs and cards.

**Tests to add:**

- Every tag references a real exam and task.
- Every task statement has at least one lab or concept card. This is the "no orphan objective" audit, and it also catches stale task IDs when AWS revises a guide.
- `labCurriculum.ts` and the registry agree. Better still, generate the curriculum from the API so the duplication goes away.

### UI

- **Study track switcher** on `/labs/`: `Levels` (today's 5 levels) · `CLF-C02` · `SAA-C03`. A track view groups labs by **domain → task statement**, shows the domain weight, and lists concept cards inline where no lab exists.
- **Domain-weighted progress.** Progress per domain, plus an overall bar weighted by exam percentages, so finishing 10 IAM labs doesn't look like 50% of SAA. It is still derived from live lab state, matching the current "status-aware" design.
- **Lab badges.** Small Cloudscape badges (`SAA 2.1`, `CLF 3.8`) on lab cards and the lab header. The primary task is bold, and each badge links to the track view.
- **Exam lens panel** on each lab: two or three lines on how the exam asks about this topic, common distractors, and "what Floci doesn't simulate" where relevant. This keeps the lab honest and exam-useful.
- **Gap view** for contributors: tasks with no lab, sorted by weight. This is the plan in §5 rendered live.
- **Video tie-in.** The tags and exam-lens text give every Remotion video its title card ("SAA-C03 · Domain 2 · Task 2.1"), chapter markers, and cross-links. Each lab and concept card gets an optional `video_url` for @cloudexplainers.

The existing **Levels** view should stay the default. Exam tracks are a lens on the same labs, not a second curriculum.

---

## 7. Developer – Associate (DVA-C02)

**Added 2026-10-24.** Checked against the [DVA-C02 exam guide](https://docs.aws.amazon.com/aws-certification/latest/developer-associate-02/developer-associate-02.html) and a disposable Floci **2.3.0** emulator (`compose.labs.yml`, port 14566, IAM enforcement and signature validation on). The tags live in `dashboard/labs/exams.py` like the other tracks, so the labs page has a **DVA-C02 Developer – Associate** study track.

### Exam guide

| Domain (weight) | Task statements |
|---|---|
| 1 Development with AWS Services (32%) | 1.1 Develop code for applications hosted on AWS · 1.2 Develop code for AWS Lambda · 1.3 Use data stores in application development |
| 2 Security (26%) | 2.1 Implement authentication and/or authorization for applications and AWS services · 2.2 Implement encryption by using AWS services · 2.3 Manage sensitive data in application code |
| 3 Deployment (24%) | 3.1 Prepare application artifacts to be deployed to AWS · 3.2 Test applications in development environments · 3.3 Automate deployment testing · 3.4 Deploy code by using AWS CI/CD services |
| 4 Troubleshooting and Optimization (18%) | 4.1 Assist in a root cause analysis · 4.2 Instrument code for observability · 4.3 Optimize applications by using AWS services and features |

50 scored and 15 unscored questions; passing score 720. The current guide also names Amazon Q Developer (skills 1.1.11 and 3.3.6), AWS AppConfig (3.1.5), and AWS Private CA (2.2.2).

### Coverage by task statement (existing 79 labs)

| Task | Coverage | Covered by | Gap |
|---|---|---|---|
| 1.1 | ● | 21 labs: all SQS labs, SNS fan-out and filters, S3 notifications, EventBridge application spine, Scheduler, both Step Functions labs, API Gateway to Lambda, Kinesis data lake, S3 SDK labs | API transformations and validation (1.1.6), retry and circuit-breaker code for third parties (1.1.13) |
| 1.2 | ● | lambda/create-invoke-logs, runtime-config, sqs-event-source, performance-cost, async-error-handling (test events, async retries, destinations, a DLQ), dynamodb/lambda-writes, capacity-at-scale (stream processor), cloudformation/sam-environments (a layer, a function reading flags with a safe default) | — |
| 1.3 | ● | **dynamodb/application-patterns** (key design, conditional writes, optimistic locking, transactions, consistent reads, paging, PartiQL), dynamodb/crud-query, capacity-at-scale, elasticache/cache-aside, S3 labs, rds/db-instance-lifecycle | Caching with DAX (card) |
| 2.1 | ● | **apigateway/rest-api-developer** (Lambda TOKEN and Cognito authorizers on a REST API), 11 IAM labs (access keys, roles, STS, the identity capstone), Cognito user pools and groups, S3 presigned URLs and bucket policy, IMDS and instance roles | — |
| 2.2 | ● | kms/key-alias-encrypt-decrypt, kms/key-management (rotation, grants, envelope encryption), s3/default-encryption | Client-side vs server-side and certificates are concept-only (card) |
| 2.3 | ● | secretsmanager/secret-lifecycle, ssm/parameter-store-config, lambda/runtime-config, lambda/async-error-handling (an environment variable encrypted with a customer managed key and decrypted with an encryption context) | Masking, multi-tenant access (card) |
| 3.1 | ● | **cloudformation/sam-environments** (a layer and function zips packaged to S3, AppConfig feature flags per environment), lambda/create-invoke-logs (zip package), ecs/fargate-microservice (container image), ssm/parameter-store-config, ec2/guided-userdata | — |
| 3.2 | ● | **apigateway/rest-api-developer** (dev and prod stages with stage variables, a MOCK integration), **cloudformation/sam-environments** (one template as dev and prod stacks, a dev-only release), apigateway/lambda-request, lambda/sqs-event-source, eventbridge/application-spine | — |
| 3.3 | ● | **cloudformation/sam-environments** (a SAM template, its change set, CREATE and UPDATE deployments), apigateway/rest-api-developer (test event, per-environment API deployments), cloudformation/s3-sqs-stack (change set), lambda/performance-cost (aliases) | — |
| 3.4 | ● | **codepipeline/lambda-cicd** (CodeBuild, CodePipeline with approval, CodeDeploy canary and rollback), lambda/performance-cost (weighted alias), cloudformation/s3-sqs-stack | Blue/green for ECS and EC2, Elastic Beanstalk (cards) |
| 4.1 | ● | cloudwatch/log-groups-metric-filters, sqs/dead-letter-redrive, ec2/guided-broken-route, cloudwatch/metric-alarms, lambda/async-error-handling (function errors, failure records and DLQ attributes), cloudwatch/serverless-observability (a Logs Insights query for rejected orders) | Reading build and deployment logs (Floci leaves build logs empty) |
| 4.2 | ● | cloudwatch/metric-alarms (custom metrics), log-groups-metric-filters, serverless-observability (structured JSON logs, metric filters on JSON fields, PutMetricData and EMF, an alarm with SNS actions, a dashboard, active tracing), elasticloadbalancing/ha-web-tier (health checks) | X-Ray traces are unsupported (card) |
| 4.3 | ● | sns/filter-policies (subscription filter policies are skill 4.3.4), lambda/performance-cost, elasticache/cache-aside, cloudfront/edge-delivery, sqs/batch-messages | — |

Every task statement already has at least one lab, so the track works today. Six concept cards cover what labs can't: DynamoDB access patterns, encryption for developers, sensitive data handling, deployment strategies, X-Ray tracing, and developer tooling (SAM, SDK retries, Amazon Q Developer).

### What Floci 2.3.0 does for developer services

Probed 2026-10-24 with real SDK calls.

| Area | Result |
|---|---|
| **CodeBuild** | **Behaves.** A build pulls the source zip from S3, runs every buildspec phase with `sh` in a real Docker container of the project's image, passes environment variables, fails the build on a non-zero exit (the failed phase's context holds the command output, such as a failing unit test), and uploads a zip artifact (honoring `base-directory`). The image must be pullable by the Docker host (`aws/codebuild/standard:7.0` is not on Docker Hub, so the build faults; `python:3.12-slim` works). The artifact is always named `<project>-<build number>.zip` at the bucket root, whatever the path and name settings. The log group and stream are created but stay empty. `sourceLocationOverride` on `StartBuild` is ignored. |
| **CodePipeline** | **Behaves.** An S3 source → CodeBuild → manual approval pipeline runs: the build really runs, the approval waits for a token, and `PutApprovalResult` finishes the execution. A project with source type `CODEPIPELINE` gets no source files from the pipeline, so its build fails; a project with an S3 source pointing at the same object works. |
| **CodeDeploy (Lambda)** | **Behaves, on a fast clock.** Deployment groups, Lambda deployment configs (built-in and custom) and AppSpec deployments shift the alias for real: a canary sets the alias weight (for example 10%) and completes, and linear configs step the weight up, but intervals take seconds instead of minutes. Stopping a deployment leaves the alias weights where they were (no automatic rollback), alarm-based rollback is stored only, and target versions aren't validated. |
| **CodeCommit** | Unsupported (`UnknownOperation`). Labs use an S3 source instead. |
| **AWS AppConfig** | **Behaves.** Applications, environments, feature-flag profiles, hosted versions, deployment strategies and deployments work, and the AppConfig Data API returns each environment's flag values, to a Lambda function too. Deployments complete instantly whatever the strategy, and flag constraints are not validated (an out-of-range value is accepted). |
| **API Gateway REST** | **Mostly behaves.** Lambda TOKEN authorizers (401 and 403, context passed to the backend), Cognito user pool authorizers, request validators with JSON schema models, `apiKeyRequired`, MOCK integrations with mapping templates, and per-stage variables in the Lambda event all work. A key only works on the stages its usage plan covers (403 elsewhere). **Not enforced:** usage plan throttles and quotas (a plan reads back with only its name, though its stages still apply). Canary settings are dropped, and every stage serves the latest configuration rather than the deployment it points at. `${stageVariables.x}` in a Lambda integration URI is not substituted. `TestInvokeMethod` fails. |
| **DynamoDB application patterns** | **Behaves.** Condition expressions (`ConditionalCheckFailed`), optimistic locking, `TransactWriteItems` (all-or-nothing, cancellation reasons, idempotent client tokens), `TransactGetItems`, batch writes (25-item limit), paging with `LastEvaluatedKey`, GSI projections and the rule against consistent reads on a GSI, reserved words, consumed capacity, and PartiQL (`DuplicateItemException`, conditional `UPDATE`). Reads are never stale and nothing is throttled, so `UnprocessedItems` is always empty. |
| **AWS SAM** | **Mostly behaves.** CloudFormation runs the `AWS::Serverless-2016-10-31` transform with change sets: Globals apply, a SAM function with `AutoPublishAlias` creates the function, a version and an alias, and `SimpleTable` creates a table. **Gaps:** the transform drops a function's `Policies` (the generated role gets only basic execution, and Floci enforces the role for DynamoDB, so declare the role explicitly); `Ref` on `AWS::Serverless::LayerVersion` returns no ARN, so the stack rolls back (use `AWS::Lambda::LayerVersion`); the alias points at `$LATEST`, and an update publishes no new version. |
| **Lambda layers** | **Behaves.** A published layer is mounted under `/opt` and its Python module imports. |
| **Lambda async invocation** | **Behaves.** Event invoke configs (retries, maximum event age), SQS OnSuccess and OnFailure destinations with AWS-shaped records (condition, `approximateInvokeCount`, request and response payloads) and a DLQ (the event with `ErrorMessage` attributes) all work. The first retry comes about a minute later. Once an OnFailure destination is set, failed events go only there, not to the DLQ. Synchronous calls return no `LogResult` tail. |
| **KMS from Lambda** | **Behaves, with one quirk.** The function's boto3 uses its role's credentials and Floci enforces the role policy on KMS; the encryption context is enforced. `kms:Decrypt` is checked against `arn:aws:kms:<region>:<account>:key/*`, so a policy that names the key ARN is denied. |
| **CloudWatch Logs** | **Partial.** JSON metric filters publish counts and field values with units. Logs Insights runs `fields`, `filter field = "x"`, `sort` and `limit`; `stats` and `like` fail. `FilterLogEvents` ignores JSON patterns (plain terms work). Embedded metric format lines are not turned into metrics. A role policy with a `cloudwatch:namespace` condition denies PutMetricData. |
| **CloudWatch alarms and dashboards** | **Partial.** Alarms are evaluated and reach ALARM in about 15 seconds, but their SNS actions never run (`SetAlarmState` doesn't notify either). Dashboards are stored. Lambda's JSON log format and active tracing are stored without effect. |
| **X-Ray, AWS Private CA** | Unsupported. |
| **SSM SecureString** | Stored; `GetParameter` without `WithDecryption` returns plaintext (AWS returns ciphertext). |

### Wave D: Developer labs

Built after Wave 2 and before Wave 3. Each lab follows the existing pattern and says in each step what Floci only stores versus enforces.

| # | Proposed lab | Steps | DVA | Also | Floci |
|---|---|---|---|---|---|
| ✅ D-1 | **CI/CD pipeline for a Lambda service** (shipped 2026-10-24 as `codepipeline/lambda-cicd`, 9 steps, DVA 3.4, 3.1, 3.3 and 4.1, the first lab of Level 6: Developer Workflows; builds, the pipeline and the canary are real, while build logs stay empty and alarms never stop a deployment, so the lab reads the failed phase and rolls back by redeploying): source zip in S3, CodeBuild project with a buildspec (install, unit tests, package), a failing build read from its phases, CodePipeline with source, build, and manual approval stages, then a CodeDeploy canary from version 1 to 2 of a Lambda alias, and a rollback | ~9 | 3.4, 3.1, 3.3, 4.1 | — | ✅ build, pipeline, and canary real (canary runs in seconds) |
| ✅ D-2 | **API Gateway for developers** (shipped 2026-10-24 as `apigateway/rest-api-developer`, 7 steps, DVA 2.1, 1.1, 3.2 and 3.3; the canary was dropped because Floci discards canary settings): a Lambda backend tested with a sample event, a REST API with dev and prod stages and stage variables, a Lambda TOKEN authorizer, a Cognito authorizer, a request validator with a model, a MOCK integration with a mapping template, and an API key with a usage plan | 7 | 2.1, 1.1, 3.2, 3.3 | SAA 1.2 | ✅ authorizers, validation, keys and plan stages; ⚠️ throttles and quotas not enforced, canary dropped |
| ✅ D-3 | **DynamoDB application patterns** (shipped 2026-10-24 as `dynamodb/application-patterns`, 8 steps, DVA 1.3 and 1.1; every pattern behaves as on AWS, except that reads are never stale and nothing is throttled): single-table key design with a sparse GSI, batch writes with retries, conditional writes, optimistic locking with a version attribute, a transfer with `TransactWriteItems` and an idempotency token, consistent reads, query vs scan with paging, and PartiQL | 8 | 1.3, 1.1 | SAA 3.3 | ✅ |
| ✅ D-4 | **Serverless packaging with SAM and AppConfig** (shipped 2026-10-24 as `cloudformation/sam-environments`, 7 steps, DVA 3.3, 3.1, 3.2 and 1.2; the template declares its role and uses `AWS::Lambda::LayerVersion` to work around two transform gaps): a Lambda layer, a SAM template with `AutoPublishAlias` reviewed as a change set and deployed as a dev stack and then a prod stack, a dev-only release, and an AppConfig feature flag the function reads per environment and that turns a feature on in prod without a deploy | 7 | 3.1, 3.2, 3.3, 1.2 | — | ✅ with workarounds; ⚠️ alias on `$LATEST`, instant flag deployments |
| ✅ D-5 | **Lambda event lifecycle and errors** (shipped 2026-10-24 as `lambda/async-error-handling`, 8 steps, DVA 1.2, 2.3 and 4.1; the DLQ comes before the destinations because Floci stops copying failed events to the DLQ once an OnFailure destination is set): async invoke with retries and maximum event age, OnSuccess and OnFailure destinations, a DLQ, a test event, an environment variable encrypted with a customer managed key | 8 | 1.2, 2.3, 4.1 | SAA 2.1 | ✅ retries, destinations, DLQ and KMS real; ⚠️ Decrypt checked against `key/*` |
| ✅ D-6 | **Observability for a serverless app** (shipped 2026-10-24 as `cloudwatch/serverless-observability`, 8 steps, DVA 4.2 and 4.1; the alarm step checks the state because Floci never runs alarm actions, and the tracing step stores the setting because X-Ray is missing): structured JSON logs, a metric filter on a JSON field, a Logs Insights query, custom metrics with PutMetricData and EMF, an alarm that notifies SNS, a dashboard, active tracing | 8 | 4.2, 4.1 | SAA 2.2 | ✅ filters, metrics, alarm state and dashboards; ⚠️ no EMF, no stats queries, no alarm actions, no X-Ray |

**Projected DVA coverage:** before Wave D, 5 of 13 task statements were ● hands-on (1.1, 2.1, 2.2, 2.3, 4.3); D-1 makes 3.4 hands-on, D-2 adds API authorizers, stages, and validation, D-3 makes 1.3 hands-on, D-4 makes 3.1, 3.2 and 3.3 hands-on, D-5 makes 1.2 hands-on, and D-6 makes 4.1 and 4.2 hands-on. With Wave D complete, all 13 are, with X-Ray and Private CA left to concept cards.

---

## 8. AI Practitioner (AIF-C01)

**Added 2026-10-26.** Checked against the AIF-C01 exam guide (version 1.4) and a disposable Floci **2.3.0** emulator (`compose.labs.yml`, port 14566, IAM enforcement and signature validation on). The tags live in `dashboard/labs/exams.py` like the other tracks, so the labs page has an **AIF-C01 AI Practitioner** study track with a concept card and a practice quiz for every task statement.

### Exam guide

| Domain (weight) | Task statements |
|---|---|
| 1 Fundamentals of AI and ML (20%) | 1.1 Explain basic AI concepts and terminologies · 1.2 Identify practical use cases for AI · 1.3 Describe the ML development lifecycle |
| 2 Fundamentals of Generative AI (24%) | 2.1 Explain the basic concepts of generative AI · 2.2 Understand the capabilities and limitations of generative AI for solving business problems · 2.3 Describe AWS infrastructure and technologies for building generative AI applications |
| 3 Applications of Foundation Models (28%) | 3.1 Describe design considerations for applications that use foundation models · 3.2 Choose effective prompt engineering techniques · 3.3 Describe the training and fine-tuning process for foundation models · 3.4 Describe methods to evaluate foundation model performance |
| 4 Guidelines for Responsible AI (14%) | 4.1 Explain the development of AI systems that are responsible · 4.2 Recognize the importance of transparent and explainable models |
| 5 Security, Compliance, and Governance for AI Solutions (14%) | 5.1 Explain methods to secure AI systems · 5.2 Recognize governance and compliance regulations for AI systems |

50 scored and 15 unscored questions; passing score 700. In-scope machine learning services: Amazon A2I, Bedrock, Comprehend, Fraud Detector, Kendra, Lex, Personalize, Polly, Q, Rekognition, SageMaker, Textract, Transcribe, and Translate, plus analytics, governance, and security services the other tracks already cover.

### Coverage by task statement

| Task | Coverage | Covered by | Gap |
|---|---|---|---|
| 1.1 | ◐ | comprehend/ai-services-tour (confidence scores, inference against managed models), sagemaker/train-to-endpoint (training data, labels, features, inference, overfitting and underfitting from the metrics), concept card and quiz | Mostly theory |
| 1.2 | ● | comprehend/ai-services-tour (13 problems matched to a service or to no AI, Comprehend, Translate, Rekognition, Textract and Transcribe calls, a keyword-rule baseline, human review under 90% confidence) | Floci's answers are fixed, so the lab says what AWS would return; Polly, Lex, Personalize and Kendra are not emulated |
| 1.3 | ● | sagemaker/train-to-endpoint (data split, a real training job with hyperparameters, evaluation on held-out data, a model, a real-time endpoint, predictions, retraining and a blue/green endpoint update, inference options and a drift check), kinesis/streaming-data-lake (collect, catalog, and prepare data with Glue and Athena) | Model Monitor, the model registry, tuning jobs and Pipelines are not emulated, so the lab checks drift itself |
| 2.1 | ◐ | s3vectors/rag-knowledge-base (tokens, embeddings, a vector store, prompt context and token-based pricing), concept card and quiz | Mostly theory; Floci's answers are stubs |
| 2.2 | ◐ | bedrockruntime/prompt-patterns (cost per 1,000 tickets on two models, business objectives beside a model metric), concept card and quiz | Mostly theory; Floci's answers are stubs |
| 2.3 | ● | s3vectors/rag-knowledge-base (S3 Vectors as the vector store, the Converse API, retrieval instead of fine-tuning, token pricing) | Bedrock Knowledge Bases, customization and the model catalog are not emulated |
| 3.1 | ● | s3vectors/rag-knowledge-base (chunking, embeddings, a vector index with metadata filters, top-K retrieval, grounded prompts, a grounding check, refusing what the documents do not answer, cost per 1,000 tokens) | Guardrails contextual grounding and Knowledge Bases are not emulated |
| 3.2 | ● | bedrockruntime/prompt-patterns (zero-shot, a system prompt with a role, format and rules, few-shot examples as prior turns, temperature, top-p, max tokens and stop sequences, a prompt injection with its defense and an output check) | Floci ignores inference settings and cannot be injected, so the lab checks the prompt and the answer format |
| 3.3 | ○ | Concept card and quiz | Floci has no Bedrock customization or SageMaker fine-tuning recipes, so this stays conceptual |
| 3.4 | ● | bedrockruntime/prompt-patterns (ROUGE-1, ROUGE-2 and ROUGE-L over four candidate summaries, BLEU over two translations, business objectives beside the metric) | Bedrock model evaluation jobs are not emulated, so the lab computes the metrics |
| 4.1 | ● | bedrock/guardrails-least-privilege (content filters, a denied topic, blocked words, PII masking and blocking, contextual grounding, guardrail versions, an evaluation of sample prompts, and a ten-item responsible AI checklist) | Guardrails are stored, not applied, so the lab evaluates the prompts; Clarify and Model Monitor are not emulated |
| 4.2 | ○ | Concept card and quiz | SageMaker Clarify and Model Cards are not emulated |
| 5.1 | ● | bedrock/guardrails-least-privilege (a policy allowing one model only with the guardrail, simulated against two models, a customer managed KMS key, a private encrypted prompt log, client-side PII masking), iam/account-hardening, kms/key-management, s3/bucket-security, ec2/s3-gateway-endpoint, secretsmanager/secret-lifecycle, ec2/shared-responsibility, cloudtrail/governance-tour | Macie and VPC endpoints for Bedrock are concept-only |
| 5.2 | ● | bedrock/guardrails-least-privilege (a CloudTrail trail over guardrail and model activity, guardrail versions as a change record), cloudtrail/governance-tour (CloudTrail, Config rules), iam/multi-account-guardrails, s3/data-protection-tiers (retention) | Bedrock model invocation logging, Audit Manager and Artifact are concept-only |

Fourteen concept cards and 56 quiz questions (four per task) cover what labs can't.

### What Floci 2.3.0 does for AI services

Probed 2026-10-26 with real SDK calls. Every service below is in the 2.3.0 tag; Floci's main branch only fixed edge cases since.

| Service | Result |
|---|---|
| **SageMaker AI training** | **Behaves.** `CreateTrainingJob` runs the training image in a real container under the `/opt/ml` contract: S3 channels are copied to `/opt/ml/input/data/<channel>`, hyperparameters to `/opt/ml/input/config/hyperparameters.json`, and whatever the container writes to `/opt/ml/model` is uploaded as `model.tar.gz`. `ContainerEntrypoint` works, so a stock `python` image can train a script. The container must create `/opt/ml/model` itself (it is missing on 2.3.0). A failing script sets `Failed` with the traceback as `FailureReason`. |
| **SageMaker AI hosting** | **Behaves.** `CreateEndpoint` starts the model image with the command `serve`, copies `ModelDataUrl` into `/opt/ml/model`, waits for `GET /ping`, and `InvokeEndpoint` proxies to `POST /invocations`. A stock `node` image works when the model's `Environment` sets `NODE_OPTIONS=--require /opt/ml/model/inference.js` (SageMaker on AWS also runs `serve` with the model's environment, so the trick should carry over; not tried on AWS). Endpoints are CPU-only. |
| **S3 Vectors** | **Behaves.** Vector buckets and indexes, `PutVectors`, and `QueryVectors` with real cosine or Euclidean ranking, `returnDistance`, and metadata filters (`$eq`, `$in`, `$and`, ...) applied before top-k. |
| **Bedrock Runtime** | **Stub.** `Converse` and `InvokeModel` return `Floci stub response for model=<id>` with synthetic token counts (`inputTokens` 10, `outputTokens` 12) whatever the prompt; system prompts and inference settings are accepted and ignored. The optional `proxy` backend (`FLOCI_SERVICES_BEDROCK_RUNTIME_BACKEND=proxy`) forwards `Converse` to an OpenAI-compatible server such as Ollama for real answers. `InvokeModelWithResponseStream` and `ApplyGuardrail` are unsupported. |
| **Bedrock guardrails** | **Control plane only.** Create, update, version, and tag guardrails with content filters, denied topics, word lists, and PII actions. Nothing applies them: a Converse call with `guardrailConfig` returns the normal stub answer with no trace, and `ApplyGuardrail` is missing. `ListFoundationModels`, customization, model evaluation, Knowledge Bases, and classic Agents are unsupported. |
| **Bedrock AgentCore** | Control plane only: runtimes, endpoints, gateways, memories, and credential providers are stored; nothing runs. |
| **Comprehend** | **Stub.** `DetectSentiment` is always `NEUTRAL`, `DetectDominantLanguage` always `en`, `DetectKeyPhrases` returns one stub phrase, and PII detection finds nothing. Language codes are validated. `DetectEntities`, `DetectSyntax`, and custom models are unsupported. |
| **Rekognition** | **Stub.** `DetectLabels` and `DetectText` return one `Floci` entry; faces, face comparison, and moderation find nothing. Images are validated as AWS validates them (a GIF is `InvalidImageFormatException`). Collections and video are unsupported. |
| **Textract** | **Stub.** Every document returns one `Floci` line; async jobs succeed at once. |
| **Translate** | **Echo.** `TranslateText` returns the input unchanged; unknown language codes are `UnsupportedLanguagePairException`, and text over 10,000 bytes is refused. |
| **Transcribe** | **Stub.** Jobs are `COMPLETED` at once with a synthetic transcript URI and no transcript. |
| **Lex, Polly, Kendra, Personalize, Q, Fraud Detector, A2I** | Unsupported. |

Comprehend, Rekognition, Textract, and Translate can return chosen answers from an `AI_MOCK_CONFIG` JSON file that Floci re-reads when it changes (keyed by the exact input text, or by bucket and key for Textract). The dashboard doesn't mount one, so labs judge results from sample data, the way other labs check rules Floci stores but doesn't enforce.

Consoles (2026-10-26): the dashboard has pages for SageMaker AI, Bedrock guardrails, Comprehend, Rekognition, and Translate, next to the existing Bedrock Runtime, S3 Vectors, Textract, and Transcribe pages, so learners can inspect and change what the labs create.

### Wave AI: AI Practitioner labs

Each lab follows the existing pattern and says in each step what Floci only stores versus does.

| # | Proposed lab | AIF | Floci |
|---|---|---|---|
| ✅ AI-1 | **AI services tour** (shipped 2026-10-26 as `comprehend/ai-services-tour`, 8 steps, AIF 1.2 with 1.1, CLF 3.7, the first lab of Level 9 and a new lab service): match business problems to services or to no AI, then Comprehend, Translate, Rekognition, Textract and Transcribe on sample inputs, a keyword-rule baseline, and human review under a confidence threshold | 1.2 | ⚠️ Stub answers; the lab says what AWS would return |
| ✅ AI-2 | **SageMaker from training to endpoint** (shipped 2026-10-26 as `sagemaker/train-to-endpoint`, 8 steps, AIF 1.3 with 1.1, CLF 3.7, a new lab service): a train and validation split in S3, a real training job, evaluation (R squared 0.61 at 5 epochs), a model, an endpoint, predictions checked against the model's math, retraining to R squared 0.98 and a blue/green `UpdateEndpoint`, then inference options and a drift check | 1.3 | ✅ Real containers |
| ✅ AI-3 | **RAG with S3 Vectors and Bedrock** (shipped 2026-10-26 as `s3vectors/rag-knowledge-base`, 8 steps, AIF 3.1 with 2.3 and 2.1, a new lab service): chunk and embed policy documents, a 64-dimension cosine index with metadata, top-K retrieval and a metadata filter, a grounded Converse prompt, a citation and grounding check, a distance threshold that refuses unanswerable questions, and token pricing | 3.1, 2.3 | ✅ Retrieval is real; ⚠️ the answer is a stub |
| ✅ AI-4 | **Prompt patterns with the Converse API** (shipped 2026-10-26 as `bedrockruntime/prompt-patterns`, 8 steps, AIF 3.2 with 3.4 and 2.2, a new lab service): zero-shot, a system prompt with a role and format, few-shot turns, inference settings, a prompt injection and its defense, then ROUGE, BLEU, business objectives and a model cost comparison | 3.2, 3.4 | ⚠️ Stub answers unless the proxy backend is on |
| ✅ AI-5 | **Bedrock guardrails and least privilege** (shipped 2026-10-26 as `bedrock/guardrails-least-privilege`, 8 steps, AIF 4.1 with 5.1 and 5.2, a new lab service; completes Wave AI): a full guardrail, version 1 beside a changing draft, the guardrail on a Converse call, an evaluation of eight sample prompts, client-side PII masking, a KMS key and a private prompt log, a simulated least-privilege policy, and a CloudTrail trail with a responsible AI checklist | 4.1, 5.1 | ⚠️ Guardrails are stored, not applied |

**AIF coverage (Wave AI complete, 2026-10-26):** 1.2, 1.3, 2.3, 3.1, 3.2, 3.4, 4.1, 5.1 and 5.2 have hands-on labs (9 of 14), and 1.1, 2.1 and 2.2 are partly covered by those labs too. 3.3 (training and fine-tuning) and 4.2 (transparency and explainability) stay concept cards and quizzes, because Floci emulates neither Bedrock customization nor SageMaker Clarify and Model Cards, which suits a foundational exam that tests understanding more than hands-on skill.

---

## 9. DevOps Engineer – Professional (DOP-C02)

**Added 2026-11-02.** Checked against the [DOP-C02 exam guide](https://docs.aws.amazon.com/aws-certification/latest/devops-engineer-professional-02/devops-engineer-professional-02.html) and a disposable Floci **2.4.0** emulator (`compose.labs.yml` pinned to 2.4.0, port 14566, IAM enforcement and signature validation on), with the Floci 2.4.0 source as the reference for anything a probe could not settle. The tags live in `dashboard/labs/exams.py` like the other tracks, so the labs page has a **DOP-C02 DevOps Engineer – Professional** study track.

### Exam guide

| Domain (weight) | Task statements |
|---|---|
| 1 SDLC Automation (22%) | 1.1 Implement CI/CD pipelines · 1.2 Integrate automated testing into CI/CD pipelines · 1.3 Build and manage artifacts · 1.4 Implement deployment strategies for instance, container, and serverless environments |
| 2 Configuration Management and IaC (17%) | 2.1 Define cloud infrastructure and reusable components to provision and manage systems throughout their lifecycle · 2.2 Deploy automation to create, onboard, and secure AWS accounts in a multi-account or multi-Region environment · 2.3 Design and build automated solutions for complex tasks and large-scale environments |
| 3 Resilient Cloud Solutions (15%) | 3.1 Implement highly available solutions to meet resilience and business requirements · 3.2 Implement solutions that are scalable to meet business requirements · 3.3 Implement automated recovery processes to meet RTO and RPO requirements |
| 4 Monitoring and Logging (15%) | 4.1 Configure the collection, aggregation, and storage of logs and metrics · 4.2 Audit, monitor, and analyze logs and metrics to detect issues · 4.3 Automate monitoring and event management of complex environments |
| 5 Incident and Event Response (14%) | 5.1 Manage event sources to process, notify, and take action in response to events · 5.2 Implement configuration changes in response to events · 5.3 Troubleshoot system and application failures |
| 6 Security and Compliance (17%) | 6.1 Implement techniques for identity and access management at scale · 6.2 Apply automation for security controls and data protection · 6.3 Implement security monitoring and auditing solutions |

65 scored and 10 unscored questions; passing score 750. A professional exam asks for the best of several working designs, so the labs aim at the trade-offs: which deployment strategy, which automation, and what to do when it fails.

### Coverage by task statement (existing 100 labs)

52 existing labs are tagged for DOP tasks, and the Wave P labs add to the table as they ship. Legend as in §1; ○ here means a concept card but no lab.

| Task | Coverage | Covered by | Gap |
|---|---|---|---|
| 1.1 | ● | codepipeline/lambda-cicd (CodeBuild, CodePipeline with approval, CodeDeploy), **codepipeline/pipeline-quality-gates** (build settings from Parameter Store and Secrets Manager, failed stage events), ssm/parameter-store-config, secretsmanager/secret-lifecycle | — |
| 1.2 | ● | **codepipeline/pipeline-quality-gates** (unit tests, a coverage gate, a JUnit report, an integration test stage against DynamoDB), codepipeline/lambda-cicd (unit tests, a failing build read from its phase) | Load tests (1.2.5, card); report groups create no reports in Floci |
| 1.3 | ● | **ecs/container-blue-green** (ECR with immutable tags, scan on push, a lifecycle policy and its preview, images pushed through the API), codepipeline/lambda-cicd (zip artifacts in S3), cloudformation/sam-environments (layers), ecs/fargate-microservice (container image) | ECR repository policies and replication, CodeArtifact, Image Builder (card) |
| 1.4 | ● | **ecs/container-blue-green** (CodeDeploy ECS blue/green with a canary, a smoke-test hook, and a failed hook that stops a release), lambda-cicd (Lambda canary), lambda/performance-cost (weighted alias), elasticloadbalancing/ha-web-tier (instance refresh) | EC2 in-place deployments with the CodeDeploy agent (1.4.2, 1.4.4) |
| 2.1 | ● | **cloudformation/stacksets-baseline** (StackSets in two Regions, a custom resource, stack protection, drift, Service Catalog), cloudformation/s3-sqs-stack (change sets), sam-environments (SAM, AppConfig), well-architected-review | CDK and modules (card) |
| 2.2 | ● | **cloudformation/stacksets-baseline** (a baseline in two Regions, a service-managed rollout to an OU), iam/multi-account-guardrails (Organizations, OUs, SCPs, delegated admin), iam/role-trust-policy, cloudtrail/governance-tour | Control Tower and Account Factory (card) |
| 2.3 | ◐ | ec2/guided-ssm-command (Run Command), ec2/guided-userdata, both Step Functions labs | State Manager, Patch Manager, Inventory and Automation are unsupported (card) |
| 3.1 | ● | elasticloadbalancing/ha-web-tier, rds/aurora-resilience, route53/routing-failover, dynamodb/capacity-at-scale, cloudfront/edge-delivery, ec2/nat-three-tier, well-architected-review | — |
| 3.2 | ● | ha-web-tier (Auto Scaling), ecs/fargate-microservice, eks/control-plane-nodegroup, lambda/performance-cost, elasticache/cache-aside, apigateway/rest-api-developer, capacity-at-scale | — |
| 3.3 | ● | backup/backup-dr-plan, route53/routing-failover, rds/aurora-resilience, s3/data-protection-tiers, s3/version-recovery, ha-web-tier | FIS and Resilience Hub (card) |
| 4.1 | ● | **cloudwatch/centralized-logging** (KMS-encrypted log groups with retention, subscription filters to Kinesis and Firehose, an S3 archive with a lifecycle rule, a cross-account destination and a metric stream), log-groups-metric-filters, serverless-observability, kinesis/streaming-data-lake, s3/lifecycle-retention, ec2/network-scale-out (flow logs) | The CloudWatch agent (4.1.3); metric streams and destinations are stored only |
| 4.2 | ● | **cloudwatch/centralized-logging** (per-service metrics from a dimensioned metric filter, a composite incident alarm, a Logs Insights query across two log groups), serverless-observability (dashboards, Logs Insights), metric-alarms, streaming-data-lake (Athena), log-groups-metric-filters | X-Ray (card) |
| 4.3 | ● | dynamodb/capacity-at-scale (auto scaling), ha-web-tier (target tracking, health checks), metric-alarms, eventbridge/application-spine, **eventbridge/drift-remediation** (automatic fixes from events, a storm gate), route53/routing-failover, cloudtrail/governance-tour (Config rules), ec2/guided-ssm-command | Config remediation actions (Floci has none) |
| 5.1 | ● | codepipeline/pipeline-quality-gates (failed stage events to SQS), eventbridge/application-spine, lambda/async-error-handling, sqs/dead-letter-redrive, sns/sqs-fanout, s3/object-notifications-sqs, both Step Functions labs, eventbridge/drift-remediation (Parameter Store and S3 events) | — |
| 5.2 | ● | **eventbridge/drift-remediation** (Parameter Store changes reverted and public uploads made private from their events, a custom Config rule, a Step Functions workflow that escalates during a change storm) | Config remediation actions and SSM Automation runbooks (unsupported in Floci; card) |
| 5.3 | ● | **ssm/fleet-release-troubleshooting** (a release that fails validation read from its exit code and stderr and rolled back, drift after a scale-out, a CodeDeploy NoInstancesReachable checklist, a CloudFormation rollback traced to its first failed resource), lambda-cicd (failed build phase), ec2/guided-broken-route, lambda/async-error-handling, sqs/dead-letter-redrive | Failed scaling activities and CodeDeploy lifecycle events on EC2 (Floci reaches no instances) |
| 6.1 | ● | **iam/machine-identity** (a team creating its own roles under a permissions boundary, role chaining with a session policy, ABAC decided by the lab, and a database password rotated through all four steps), iam/account-hardening, identity-enforcement-capstone, role-trust-policy, sts-session-policy, instance roles, secretsmanager/secret-lifecycle | ABAC is not evaluated by Floci (the lab decides from the tags); IAM Identity Center (card) |
| 6.2 | ● | eventbridge/drift-remediation (public uploads made private), iam/machine-identity (a boundary the team cannot remove or widen), iam/multi-account-guardrails, cloudtrail/governance-tour, wafv2/edge-throttling, kms/key-management, s3/bucket-security, s3/data-protection-tiers, ec2/s3-gateway-endpoint | Macie, Network Firewall (card) |
| 6.3 | ● | cloudtrail/governance-tour (CloudTrail, Config, GuardDuty and Security Hub alerts), iam/account-hardening (credential report), ec2/network-scale-out (flow logs), **cloudwatch/centralized-logging** (errors streamed to a security account's destination) | — |

Domain 3 and most of domains 4 and 6 are already hands-on from the SAA and DVA work. The gaps are where the professional exam goes deeper than the associate exams: multi-account and multi-Region infrastructure as code, container and instance deployments, testing in the pipeline, remediation, and troubleshooting. Eleven new concept cards cover the theory, eight of them naming a planned lab, and the X-Ray card now also covers DOP 4.2.

### What Floci 2.4.0 does for DevOps services

Probed 2026-11-02 with real SDK calls, and checked against the Floci source at the 2.4.0 tag.

| Area | Result |
|---|---|
| **CloudFormation StackSets** | **Behaves.** A self-managed StackSet deploys real stack instances into several Regions (an SNS topic appears in us-west-2), and operations report `SUCCEEDED`. A service-managed StackSet is refused until trusted access with Organizations is activated (`ActivateOrganizationsAccess` exists), as on AWS. |
| **CloudFormation stacks** | **Mostly behaves.** Lambda-backed custom resources run for real (the function gets a `ResponseURL`, and `Fn::GetAtt` reads its `Data`), exports work, and termination protection blocks `DeleteStack`. **Not supported:** `DetectStackDrift` and resource drift, `ListTypes` (no registry or modules). `SetStackPolicy` is accepted but not enforced (an update the policy denies succeeds) and `GetStackPolicy` returns nothing. Rollback triggers are stored. |
| **AWS Service Catalog** | **Control plane only.** Portfolios, products, launch constraints, principals, and organization shares work, and `ProvisionProduct` returns a `SUCCEEDED` record, but no stack or resource is created. |
| **CodeDeploy (EC2)** | **Reaches no instances.** Checked live on 2026-11-02 (the source read had suggested hooks run over SSM): a Server deployment with EC2 tag filters fails with `NoInstancesReachable` even when the tags match, because Floci's lookup of tagged instances fails internally; an `autoScalingGroups` list finds none either. On-premises instances do resolve, but a hook script is sent as SSM Run Command that stays `Pending` (it waits for an agent), so every scripted hook ends `ScriptFailed` within a second. Plain Run Command on a Floci instance does run. DownloadBundle and Install always succeed (no files are copied), and the AppSpec must be passed inline as `appSpecContent`. |
| **CodeDeploy (ECS)** | **Behaves (from source, to verify in the lab).** A deployment creates a green task set, runs Lambda hooks, moves the production listener to the green target group, and deletes the blue task set. |
| **CodeBuild extras** | **Behaves (checked live in P-4).** `env/parameter-store` and `env/secrets-manager` in the buildspec are resolved into the build's environment, and builds get `AWS_ENDPOINT_URL` (`http://floci:4566` when Floci runs in Docker) and test credentials, so a test in the build reaches Floci's DynamoDB. A failed phase's context holds `Exit code N:` and the output's tail, and artifacts upload even when the build fails. With `packaging: NONE` the files land at the bucket root, ignoring `path` and `name`. Report groups are stored, but the buildspec's `reports` section creates no reports and `ListReports` is unsupported. Build output never reaches the log stream. |
| **CodePipeline events** | **Behaves (checked live in P-4).** Execution, stage, and action state changes reach the default event bus as `aws.codepipeline` events; a rule on failed stages delivers them to SQS. The CodeBuild action ignores the input artifact, so projects read their own S3 source. |
| **ECR** | **Partial.** Repositories with tag immutability, scan on push, repository policies, lifecycle policies, and replication configuration are stored, and Floci starts a real registry container for image pushes. Lifecycle preview and scan findings are unsupported, and lifecycle rules don't expire images. |
| **CodeArtifact** | **Control plane.** Domains, repositories, tokens, and repository endpoints work. |
| **EC2 Image Builder** | Unsupported. |
| **Auto Scaling** | **Partial.** Groups launch real instances within about 10 seconds. Lifecycle hooks, warm pools, and scheduled actions are stored, but a launching hook does not hold the instance in `Pending:Wait`, and an instance marked `Unhealthy` (or stopped) is not replaced. Instance refresh replaces instances. `PutNotificationConfiguration` is unsupported. |
| **Systems Manager** | **Partial.** Run Command and documents work, and associations are stored. Automation executions, Patch Manager, Inventory, Compliance, and OpsCenter are unsupported. Parameter changes emit `Parameter Store Change` events. |
| **AWS Config** | **Partial.** Recorder, delivery channel, rules, and conformance packs are stored; managed and custom Lambda rules stay `INSUFFICIENT_DATA` after `StartConfigRulesEvaluation` (the Lambda is not invoked), but `PutEvaluations` exists. Remediation configurations and aggregators are unsupported. |
| **CloudWatch** | **Partial.** Metric alarms and **composite alarms** evaluate for real (a composite of two alarms reaches ALARM). Metric streams are stored. Anomaly detectors and Synthetics canaries are unsupported. |
| **CloudWatch Logs** | **Partial.** KMS-encrypted log groups and retention are stored, and a subscription filter to Kinesis delivers only matching events, gzip-encoded as on AWS. Export tasks are unsupported. |
| **Secrets Manager rotation** | **Behaves, with one quirk.** `RotateSecret` invokes the rotation Lambda for `createSecret`, `setSecret`, `testSecret`, and `finishSecret`, and moves the labels. Floci creates the `AWSPENDING` version itself before the first step, so a function that checks for an existing pending version (as the AWS templates do) never writes the new value. |
| **IAM tag conditions (ABAC)** | **Not evaluated.** A policy with `aws:ResourceTag/...` or `aws:PrincipalTag/...` conditions denies every request, on SSM and SQS alike, so ABAC can't be shown by allow and deny. |
| **EventBridge service events** | **Partial.** CodePipeline, ECS task state, Parameter Store, Secrets Manager (CloudTrail-style), S3, Scheduler, and Step Functions events arrive. Auto Scaling, Config compliance, Health, and general CloudTrail API events do not. |
| **FIS** | **Control plane.** Experiment templates and experiments are validated and tracked; no fault is injected. |
| **Elastic Beanstalk, Control Tower, Detective, Network Firewall, ACM, Managed Prometheus** | Control plane only (stored). |
| **X-Ray, Synthetics, Health, Resilience Hub, Proton, App Runner, Managed Grafana, License Manager, Compute Optimizer, Trusted Advisor** | Unsupported. |

### Wave P: DevOps Professional labs

A new curriculum level, **Level 10: DevOps Professional**, holds these labs. Each lab follows the existing pattern and says in each step what Floci only stores versus what it does. Ship one lab per PR, in this order.

| # | Proposed lab | Steps | DOP | Floci |
|---|---|---|---|---|
| ✅ P-1 | **Multi-Region baseline with StackSets** (shipped 2026-11-02 as `cloudformation/stacksets-baseline`, 8 steps, DOP 2.1, 2.2 and 6.2, the first lab of Level 10; it adds a service-managed rollout to an OU because Floci deploys those for real, and drops per-Region overrides because Floci ignores them): a baseline StackSet deployed to two Regions with per-Region parameter overrides, a Lambda-backed custom resource, exports and termination protection, a stack policy the lab checks itself, a drift check by comparing live settings to the template, and a Service Catalog product with a launch constraint | ~8 | 2.1, 2.2 | ✅ StackSets and custom resources real; ⚠️ no drift, stack policy and Service Catalog provisioning not applied |
| ✅ P-2 | **Container blue/green with CodeDeploy** (shipped 2026-11-02 as `ecs/container-blue-green`, 8 steps, DOP 1.4, 1.3 and 3.2; the smoke test runs on BeforeAllowTraffic because Floci ignores the test listener and AfterAllowTestTraffic, and the lab removes the failed task set because Floci does not roll back): an ECR repository with immutable tags, scan on push and a lifecycle policy, an image pushed to it, an ECS service behind an ALB with blue and green target groups, a CodeDeploy ECS deployment with a test hook, and a failed hook that stops the rollout | ~8 | 1.4, 1.3, 3.2 | ✅ task sets, Lambda hooks and the canary listener shift real; ⚠️ no rollback; ⚠️ ECR lifecycle and scans stored only |
| ✅ P-3 | **In-place deployments and troubleshooting** (shipped 2026-11-02 as `ssm/fleet-release-troubleshooting`, 8 steps, DOP 5.3, 1.4 and 3.2; because Floci's CodeDeploy reaches no EC2 instances, the in-place release runs as a rolling Run Command and the CodeDeploy step becomes a NoInstancesReachable diagnosis, and a scale-out step shows in-place drift): an Auto Scaling group from a launch template, a rolling in-place release, a release that fails validation and is rolled back, drift after a scale-out, an instance refresh from a new launch template version, and a CloudFormation rollback read from its events | 8 | 5.3, 1.4, 3.2 | ✅ Run Command, instance refresh and stack rollback real; ⚠️ CodeDeploy reaches no EC2 instances, MaxConcurrency and minimum healthy percentage not applied |
| ✅ P-4 | **Pipeline quality gates** (shipped 2026-11-02 as `codepipeline/pipeline-quality-gates`, 8 steps, DOP 1.2, 1.1, 5.1 and 5.3; the builds, settings, integration test, pipeline and failure events are real, and the lab reads the JUnit file itself because Floci creates no reports): CodeBuild with settings from Parameter Store and Secrets Manager, unit tests with a coverage minimum that blocks an untested release, a report group, an integration test stage that calls DynamoDB through Floci, and failed stage events routed to SQS that catch a broken release | 8 | 1.2, 1.1, 5.1, 5.3 | ✅ builds, secrets, events real; ⚠️ no test reports, logs empty |
| ✅ P-5 | **Event-driven remediation** (shipped 2026-11-02 as `eventbridge/drift-remediation`, 8 steps, DOP 5.2, 4.3, 5.1 and 6.2; events, the function, the workflow, Config evaluations and both kinds of alarm are real; Floci never invokes a custom Config rule's function, so the lab runs its periodic evaluation, and with no Config remediation or Automation the fixing is done by Lambda): a Parameter Store change reverted from its EventBridge event, a public upload made private the same way, a custom Config rule fed by `PutEvaluations`, a composite alarm that spots a change storm during a deployment, and a Step Functions workflow that fixes or escalates and records what it did | 8 | 5.2, 4.3, 5.1, 6.2 | ✅ events, Lambda, Step Functions, alarms; ⚠️ custom rules not invoked, no Config remediation |
| ✅ P-6 | **Centralized logging pipeline** (shipped 2026-11-02 as `cloudwatch/centralized-logging`, 9 steps, DOP 4.1, 4.2 and 6.3; Firehose replaces the consumer function because Floci delivers subscription filters to Firehose and Firehose writes to S3, and the lab reads the archive itself instead of Athena, whose Floci engine downloads a DuckDB extension from the internet): KMS-encrypted log groups with retention, a subscription filter streaming errors to Kinesis, a consumer that lands them in S3 with a lifecycle rule, a metric filter and a composite alarm, Logs Insights and Athena queries, and a metric stream | 9 | 4.1, 4.2, 6.3 | ✅ subscription delivery to Kinesis and Firehose, alarms real; ⚠️ key not enforced, Insights has no stats, destinations and metric streams stored only |
| ✅ P-7 | **Machine identity at scale** (shipped 2026-11-02 as `iam/machine-identity`, 8 steps, DOP 6.1 and 6.2; boundaries, the iam:PermissionsBoundary condition, explicit denies, session policies and the rotation function are real; the lab evaluates ABAC itself and, because Floci never retries a failed rotation, clears the stuck version and rotates again): a Secrets Manager rotation function through all four steps, a permissions boundary for roles a team creates, role chaining with session policies, and an ABAC policy whose tag decisions the lab evaluates itself | 8 | 6.1, 6.2 | ✅ rotation, boundaries and session policies real; ⚠️ ABAC not evaluated, chained sessions not capped at an hour |

**Projected DOP coverage:** today 18 of 19 task statements are ● hands-on and one is ◐ (2.3). P-1 made 2.1 and 2.2 hands-on, P-2 made 1.3 and 1.4 hands-on, P-3 made 5.3 hands-on, P-4 made 1.2 hands-on, and P-5 made 5.2 hands-on. P-6 adds subscription filters, encrypted log groups, an S3 log archive and a cross-account destination to 4.1, 4.2 and 6.3, and P-7 adds delegated role creation, role chaining, ABAC and rotation to 6.1 and 6.2, all of which were already hands-on. Wave P is complete. 2.3 stays partial, because Floci 2.4.0 has no Automation, Patch Manager, or Inventory.

---

## 10. CloudOps Engineer – Associate (SOA-C03)

**Added 2026-11-04.** Checked against the [SOA-C03 exam guide](https://docs.aws.amazon.com/aws-certification/latest/sysops-administrator-associate-03/sysops-administrator-associate-03.html) (the guide is titled CloudOps Engineer – Associate, but its URL still says sysops-administrator) and a disposable Floci **2.4.0** emulator (`compose.labs.yml` pinned to 2.4.0, port 14566, IAM enforcement and signature validation on), with the Floci 2.4.0 source as the reference for anything a probe could not settle. SOA-C03 replaced SysOps Administrator – Associate (SOA-C02). The tags live in `dashboard/labs/exams.py` like the other tracks, so the labs page has a **SOA-C03 CloudOps Engineer – Associate** study track.

### Exam guide

| Domain (weight) | Task statements |
|---|---|
| 1 Monitoring, Logging, Analysis, Remediation, and Performance Optimization (22%) | 1.1 Implement metrics, alarms, and filters by using AWS monitoring and logging services · 1.2 Identify and remediate issues by using monitoring and availability metrics · 1.3 Implement performance optimization strategies for compute, storage, and database resources |
| 2 Reliability and Business Continuity (22%) | 2.1 Implement scalability and elasticity · 2.2 Implement highly available and resilient environments · 2.3 Implement backup and restore strategies |
| 3 Deployment, Provisioning, and Automation (22%) | 3.1 Provision and maintain cloud resources · 3.2 Automate the management of existing resources |
| 4 Security and Compliance (16%) | 4.1 Implement and manage security and compliance tools and policies · 4.2 Implement strategies to protect data and infrastructure |
| 5 Networking and Content Delivery (18%) | 5.1 Implement and optimize networking features and connectivity · 5.2 Configure domains, DNS services, and content delivery · 5.3 Troubleshoot network connectivity issues |

65 questions (50 scored, 15 unscored), 130 minutes, passing score 720. SOA-C03 has no exam labs; every question is multiple choice or multiple response. It asks what an operator does: which metric, alarm, or log shows the problem, and which setting or automation fixes it.

### Coverage by task statement (existing 107 labs)

71 existing labs are tagged for SOA tasks, because the operations work overlaps the Solutions Architect and DevOps labs. Eleven new concept cards cover the theory, ten of them naming a planned Wave O lab, and seven earlier cards (hybrid connectivity, fleet configuration, recovery testing, artifacts, multi-account IaC, security automation, and automatic remediation) now also cover SOA tasks. Legend as in §1; ○ here means a concept card but no lab.

| Task | Coverage | Covered by | Gap |
|---|---|---|---|
| 1.1 | ● | **cloudwatch/fleet-monitoring** (agent configuration in Parameter Store, CWAgent memory, disk and process metrics, alarms with SNS, reboot and recover actions, a composite fleet alarm, a dashboard across Regions and accounts), cloudwatch/metric-alarms, log-groups-metric-filters, serverless-observability (dashboards), centralized-logging (composite alarm), cloudtrail/governance-tour, sns/sqs-fanout, lambda/create-invoke-logs | No agent in Floci (the lab collects for it); EC2 alarm actions stored only; no alarm state change events |
| 1.2 | ● | **eventbridge/ops-automation** (a silent rule debugged with TestEventPattern, an input transformer, a replay from the archive, a target DLQ, a runbook document run through Lambda and Run Command, a schedule), eventbridge/drift-remediation (fixes from events), application-spine, ec2/guided-ssm-command, sqs/dead-letter-redrive, lambda/async-error-handling, metric-alarms | Automation executions (unsupported; the runbook is stored and its steps run through Lambda) |
| 1.3 | ● | **ec2/performance-tuning** (CPU throttling and an out-of-memory kill measured and alarmed on, a right-sized type from the catalog through a launch template version, gp2 to gp3, RDS storage autoscaling, Performance Insights and RDS Proxy, multipart uploads, ranged reads and Transfer Acceleration), ec2/block-file-storage (gp3, io2, EFS), s3/multipart-upload, lifecycle-retention, data-protection-tiers, lambda/performance-cost, rds/aurora-resilience (read replicas), elasticache/cache-aside, dms/migration-toolkit (DataSync) | Compute Optimizer, Performance Insights metrics, placement groups (not in Floci; card) |
| 2.1 | ● | **cloudwatch/fleet-monitoring** (an ECS service scaled out by an alarm through Application Auto Scaling), elasticloadbalancing/ha-web-tier (Auto Scaling), dynamodb/capacity-at-scale, ecs/fargate-microservice, elasticache/cache-aside, cloudfront/edge-delivery, rds/aurora-resilience, eks/control-plane-nodegroup | EC2 Auto Scaling policies are stored only (card) |
| 2.2 | ● | ha-web-tier (ALB health checks), route53/routing-failover, rds/aurora-resilience, ec2/nat-three-tier, rds/db-instance-lifecycle, well-architected-review | Route 53 health checks never probe (card) |
| 2.3 | ● | **backup/restore-drill** (a timed drill: golden AMI, DynamoDB and RDS point-in-time restores, an S3 version recovered, RTO and RPO measured, the app repointed), backup/backup-dr-plan, s3/version-recovery, s3/data-protection-tiers, rds/aurora-resilience (PITR, snapshots), dynamodb/capacity-at-scale | EBS snapshots and AWS Backup restore jobs (not in Floci; card) |
| 3.1 | ● | **cloudformation/deployment-troubleshooting** (a deployer denied by IAM, a failed stack read from its events and recovered from ROLLBACK_COMPLETE, a missing export, an Auto Scaling group in a full subnet, an AMI copied to another Region, a subnet shared through AWS RAM), cloudformation/s3-sqs-stack, sam-environments, stacksets-baseline, well-architected-review, ecs/container-blue-green (ECR images), ssm/fleet-release-troubleshooting (stack rollback), codepipeline/lambda-cicd, ec2/guided-userdata | Image Builder, CDK, Terraform (card); AMI launch permissions (not in Floci) |
| 3.2 | ● | eventbridge/ops-automation (a runbook document, a scheduled cleanup), ec2/guided-ssm-command, ssm/fleet-release-troubleshooting, eventbridge/drift-remediation, application-spine, s3/object-notifications-sqs, scheduler/sqs-delivery, stepfunctions/order-processing-workflow, ssm/parameter-store-config | Maintenance windows and Automation (unsupported; card) |
| 4.1 | ● | **iam/access-troubleshooting** (a denied call reproduced, the policy simulator before and after a least-privilege fix, an `aws:RequestedRegion` guardrail, access for an unknown account found and removed, a conformance pack evaluated and fixed), iam/account-hardening (password policy, MFA), identity-enforcement-capstone, role-trust-policy, sts-session-policy, multi-account-guardrails (SCPs), machine-identity, s3/bucket-security, cloudtrail/governance-tour, eventbridge/drift-remediation | No denied events in CloudTrail, no Access Analyzer findings, conformance packs stored (the lab evaluates); Trusted Advisor (card) |
| 4.2 | ● | **iam/access-troubleshooting** (a KMS decrypt denied for a missing grant and then for a disabled key, an ACM certificate on an HTTPS listener with a TLS 1.3 policy and an HTTP redirect, a TLS-only bucket policy), kms/key-management, key-alias-encrypt-decrypt, s3/default-encryption, secretsmanager/secret-lifecycle, ssm/parameter-store-config, cloudtrail/governance-tour (Security Hub, GuardDuty), s3/metadata-tags, lambda/runtime-config, centralized-logging | KMS key policy denies and `aws:SecureTransport` ignored; Macie and Inspector (card) |
| 5.1 | ● | **ec2/network-troubleshooting** (a NAT gateway for a private subnet, an egress-only gateway for IPv6, a DNS Firewall domain list), ec2/vpc-public-private, security-controls, nat-three-tier, s3-gateway-endpoint, sqs-interface-endpoint, guided-private-s3, network-scale-out (peering, Transit Gateway), guided-web-server, wafv2/edge-throttling | DNS Firewall rule groups unsupported (the lab evaluates the domain list) |
| 5.2 | ● | **ec2/network-troubleshooting** (a private hosted zone that needs enableDnsHostnames, an outbound Resolver endpoint and forwarding rule, a CloudFront cache key trimmed to raise the hit ratio), route53/routing-failover, cloudfront/edge-delivery (with Global Accelerator) | Query logging and invalidations unsupported (card) |
| 5.3 | ● | **ec2/network-troubleshooting** (a network ACL that drops return traffic, read from flow log records and fixed, a private subnet with no route out traced hop by hop, load balancer access log entries diagnosed), ec2/guided-broken-route, network-scale-out (flow logs), security-controls, guided-web-server | No packets, flow logs or access logs in Floci (the lab evaluates paths and builds the records); hybrid connectivity and Network Monitor (card) |

Most of domains 2, 4 and 5 are hands-on from the SAA and DOP work. The gaps are the operator's view: the agent and alarm actions, rule and runbook troubleshooting, performance tuning from metrics, timed restores, failed deployments, access troubleshooting, and reading network logs.

### What Floci 2.4.0 does for CloudOps services

Probed 2026-11-04 with real SDK calls, and checked against the Floci source at the 2.4.0 tag. The DevOps probe in §9 still applies (Systems Manager, Auto Scaling, Config, CloudWatch).

| Area | Result |
|---|---|
| **CloudWatch alarms** | **Partial.** Metric and composite alarms evaluate. Alarm actions run for SNS and for Application Auto Scaling policies (an alarm scales an ECS service, with cooldowns). EC2 actions (`arn:aws:automate:...:ec2:reboot`) are stored but not applied, and alarm state changes send no `CloudWatch Alarm State Change` events to EventBridge. |
| **CloudWatch dashboards** | **Behaves (stored).** `PutDashboard` accepts widgets with another `accountId` and `region`, and `GetDashboard` returns them. Observability Access Manager sinks can be listed. `PutAnomalyDetector` is unsupported. |
| **CloudWatch agent** | No agent. Floci instances are containers; metrics come only from the API (`PutMetricData`). The agent's Parameter Store configuration can be stored. |
| **EventBridge** | **Behaves.** Input transformers are accepted, `TestEventPattern` evaluates patterns, archives exist. |
| **Systems Manager** | **Partial.** Automation documents can be created, but `StartAutomationExecution`, maintenance windows, patch baselines (other than listing the defaults), patch states, Inventory, Compliance, Session Manager and OpsItems are unsupported. `AWS-RunShellScript` is not a listed document for associations. |
| **EBS** | **Partial.** `ModifyVolume` (gp2 to gp3 with IOPS and throughput) completes at once, with a modification record. `CreateSnapshot` is unsupported. Data Lifecycle Manager policies can be listed. |
| **EC2** | **Partial.** `CreateImage` and `CopyImage` exist. Placement groups and Reachability Analyzer are unsupported. |
| **RDS** | **Partial.** Snapshots, point-in-time restore, read replicas, promotion, Aurora failover, storage autoscaling (`MaxAllocatedStorage`) and Performance Insights settings exist; RDS Proxy can be created (it needs real subnets). The Performance Insights API is unsupported. |
| **S3, EFS, FSx, DataSync** | Transfer Acceleration and server access logging are stored. EFS lifecycle policies (to IA and back) are stored. DataSync is control plane. FSx is unsupported. |
| **IAM and access** | **Partial.** The account password policy is enforced on `CreateLoginProfile`. Virtual MFA devices can be created. The policy simulator (`SimulateCustomPolicy`, `SimulatePrincipalPolicy` for users and roles) evaluates. `aws:RequestedRegion` is resolved in conditions. IAM Access Analyzer creates analyzers, but `ListFindings` and `ValidatePolicy` are not routed (they answer with an S3 signature error). Identity Center has an instance. Trusted Advisor is unsupported. |
| **AWS Config** | Conformance packs are stored; `DescribeConformancePackCompliance` and remediation configurations are unsupported. |
| **ACM** | **Control plane.** A requested certificate is `ISSUED` at once, without validation. |
| **Security services** | GuardDuty and Network Firewall are control plane. Security Hub `GetFindings`, Inspector and Shield are not routed or unsupported. Macie needs to be enabled. |
| **VPC networking** | **Stored, not routed.** Network ACLs, egress-only internet gateways, VPC peering (with accept) and Transit Gateways are created; flow logs exist (see the network-scale-out lab). Traffic between Floci containers ignores them. VPN and Direct Connect are unsupported. |
| **Route 53 and Resolver** | Records and health checks stored (health checks never probe). Resolver rules and endpoints list, and DNS Firewall domain lists work, but firewall rule groups and Resolver query logging are unsupported, and public query logging fails. |
| **CloudFront and Global Accelerator** | Distributions and accelerators are stored; CloudFront invalidations are unsupported. |
| **ELB** | Load balancer attributes include `access_logs.s3.enabled` (stored); no log files are written. |
| **AWS RAM** | **Control plane.** Resource shares can be created and listed. |
| **Unsupported** | Image Builder, Compute Optimizer, Performance Insights, Internet Monitor, Network Monitor, VPC Lattice, Trusted Advisor, Shield, FSx, Direct Connect, VPN. |

### Wave O: CloudOps Associate labs

A new curriculum level, **Level 11: CloudOps Associate**, holds these labs. Each lab follows the existing pattern and says in each step what Floci only stores versus what it does. Ship one lab per PR, in this order.

| # | Proposed lab | Steps | SOA | Floci |
|---|---|---|---|---|
| ✅ O-1 | **Fleet monitoring and alarm actions** (shipped 2026-11-04 as `cloudwatch/fleet-monitoring`, 8 steps, SOA 1.1, 2.1 and 1.2, the first lab of Level 11; the alarms, notifications and Application Auto Scaling are real; the lab collects for the missing agent, and the alarms ignore missing data because collection runs only when a step does): a CloudWatch agent configuration in Parameter Store, memory and disk metrics collected from instances with Run Command and published to the CWAgent namespace, an alarm per instance that notifies SNS, an EC2 recover action, a composite alarm, an alarm that scales an ECS service through Application Auto Scaling, and a dashboard with widgets from two Regions and a second account | 8 | 1.1, 2.1, 1.2 | ✅ alarms, SNS, Application Auto Scaling real; ⚠️ no agent (the lab collects for it), EC2 actions stored only |
| ✅ O-2 | **Event-driven operations and runbooks** (shipped 2026-11-04 as `eventbridge/ops-automation`, 8 steps, SOA 1.2 and 3.2): a rule that never fires debugged with `TestEventPattern` (a case mismatch fixed with `equals-ignore-case`; numeric matching shown as unsupported), an input transformer that enriches the alert, the missed alert replayed from an archive, a failing target found through its dead-letter queue, an Automation runbook document (stored), the same steps run by a Lambda function and Run Command, and a scheduled cleanup with EventBridge Scheduler | 8 | 1.2, 3.2 | ✅ rules, transformers, replay, DLQs, Lambda, Run Command, Scheduler; ⚠️ Automation executions and numeric patterns unsupported |
| ✅ O-3 | **Performance tuning from metrics** (shipped 2026-11-04 as `ec2/performance-tuning`, 8 steps, SOA 1.3, 2.1 and 1.1): workers that saturate a t3.micro's 2 vCPUs and a report job killed for lack of memory, both measured from the instance and alarmed on, a right-sized type picked from the instance type catalog and launched from a new launch template version, a gp2 volume moved to gp3 with 4,000 IOPS, RDS storage autoscaling, Performance Insights, a larger class and an RDS Proxy, and S3 multipart uploads, ranged reads and Transfer Acceleration | 8 | 1.3, 2.1 | ✅ CPU and memory limits per type, throttling, OOM kills, volume modifications, proxy targets, multipart; ⚠️ an in-place resize keeps the old limits, gp3 limits unchecked, RDS and S3 settings stored |
| ✅ O-4 | **Backup and restore drill** (shipped 2026-11-04 as `backup/restore-drill`, 8 steps, SOA 2.3 and 2.2): RTO and RPO targets for a web server, an orders table, an assets bucket and a database; a golden AMI, point-in-time recovery, object versions and an RDS snapshot; an incident that loses the server and corrupts the data; each resource restored and timed; and the app repointed at the restored copies | 8 | 2.3, 2.2 | ✅ AMI with the instance's files, DynamoDB and RDS PITR, S3 versions real; ⚠️ no EBS snapshots or Backup restore jobs, RDS restores take seconds |
| ✅ O-5 | **Provisioning troubleshooting and sharing** (shipped 2026-11-04 as `cloudformation/deployment-troubleshooting`, 7 steps, SOA 3.1 and 4.1): a deployer denied `cloudformation:CreateStack` by IAM and fixed, a stack that fails on an invalid queue property and is recreated from ROLLBACK_COMPLETE, a misspelled export, an Auto Scaling group stuck in a /28 full of other network interfaces and fixed with a second subnet, an AMI copied to us-west-2, a subnet shared with AWS RAM, and termination protection | 7 | 3.1, 4.1 | ✅ IAM on CloudFormation calls, stack failures and events, exports, subnet address counts, AMI copy, termination protection; ⚠️ RAM shares stored, resource permissions and most property values unchecked |
| ✅ O-6 | **Access troubleshooting and compliance** (shipped 2026-11-04 as `iam/access-troubleshooting`, 7 steps, SOA 4.1 and 4.2): an analyst denied a report, the policy simulator before and after a least-privilege fix, an `aws:RequestedRegion` guardrail, a KMS decrypt that fails for a missing grant and then for a disabled key, an ACM certificate on an HTTPS listener with an HTTP redirect, access for an unknown account found and removed, and a conformance pack evaluated and fixed | 7 | 4.1, 4.2 | ✅ IAM on object and key ARNs, the Region condition, the simulator, KMS key states, ACM in-use protection; ⚠️ no denied events in CloudTrail, no Access Analyzer findings, key policy denies and `aws:SecureTransport` ignored, conformance packs stored (the lab evaluates) |
| ✅ O-7 | **Network troubleshooting** (shipped 2026-11-04 as `ec2/network-troubleshooting`, 7 steps, SOA 5.3, 5.1 and 5.2): a network ACL that drops return traffic, read from flow log records and fixed, a private subnet given a NAT gateway, an IPv6 subnet given an egress-only gateway, load balancer access log entries diagnosed, private DNS, a forwarding rule and a DNS Firewall domain list, and a CloudFront cache key trimmed | 7 | 5.3, 5.1, 5.2 | ✅ every network, DNS and CloudFront object stored; ⚠️ no packets, flow logs, access logs or DNS answers, so the lab evaluates them; AssociateSubnetCidrBlock, firewall rule groups and adding domains to a list unsupported |

**SOA coverage:** Wave O is complete (2026-11-04), and all 13 task statements are ● hands-on. O-1 added the agent's metrics, alarm actions, alarm-driven scaling and a cross-account dashboard to 1.1 and 2.1, which were already hands-on. O-2 made 1.2 hands-on, O-3 made 1.3 hands-on, and O-7 made 5.3 hands-on; the other labs add the operator skills the existing labs skip. The agent, Automation, Trusted Advisor, and hybrid connectivity are left to concept cards.

---

## 11. Data Engineer – Associate (DEA-C01)

**Added 2026-11-06.** Checked against the [DEA-C01 exam guide](https://docs.aws.amazon.com/aws-certification/latest/data-engineer-associate-01/data-engineer-associate-01.html) and a disposable Floci **2.4.0** emulator (`compose.labs.yml` pinned to 2.4.0, port 14566, IAM enforcement and signature validation on), with the Floci 2.4.0 source as the reference for anything a probe could not settle. The tags live in `dashboard/labs/exams.py` like the other tracks, so the labs page has a **DEA-C01 Data Engineer – Associate** study track.

### Exam guide

| Domain (weight) | Task statements |
|---|---|
| 1 Data Ingestion and Transformation (34%) | 1.1 Perform data ingestion · 1.2 Transform and process data · 1.3 Orchestrate data pipelines · 1.4 Apply programming concepts |
| 2 Data Store Management (26%) | 2.1 Choose a data store · 2.2 Understand data cataloging systems · 2.3 Manage the lifecycle of data · 2.4 Design data models and schema evolution |
| 3 Data Operations and Support (22%) | 3.1 Automate data processing by using AWS services · 3.2 Analyze data by using AWS services · 3.3 Maintain and monitor data pipelines · 3.4 Ensure data quality |
| 4 Data Security and Governance (18%) | 4.1 Apply authentication mechanisms · 4.2 Apply authorization mechanisms · 4.3 Ensure data encryption and masking · 4.4 Prepare logs for audit · 4.5 Understand data privacy and governance |

65 questions (50 scored, 15 unscored), 130 minutes, passing score 720. Every question is multiple choice or multiple response. The questions ask which service, format, or setting fits a pipeline: Kinesis or MSK, Glue or EMR, Athena or Redshift, a partition or a sort key, a Lake Formation grant or a bucket policy.

### Coverage by task statement (120 labs)

57 existing labs are tagged for DEA tasks, mostly from the Solutions Architect, Developer, and DevOps work (streaming, S3 lifecycle, DynamoDB, Step Functions, IAM, KMS, and logging). Sixteen new concept cards cover the theory. All six Wave E labs shipped on 2026-11-06 (bold below), so no card names a planned lab any more, and the DynamoDB access patterns card now also covers DEA 2.4. Legend as in §1; ○ here means a concept card but no lab.

| Task | Coverage | Covered by | Gap |
|---|---|---|---|
| 1.1 | ● | kinesis/streaming-data-lake (Kinesis, Firehose, replay), dynamodb/capacity-at-scale (streams), lambda/sqs-event-source, s3/object-notifications-sqs, scheduler/sqs-delivery, sns/sqs-fanout, dms/migration-toolkit (CDC), s3/multipart-upload, **kinesis/stream-processing** (batching, bisecting, on-failure queue, replay, enhanced fan-out, resharding, MSK) | Tumbling windows and ParallelizationFactor are dropped by Floci (explained) |
| 1.2 | ● | kinesis/streaming-data-lake (Parquet conversion), ecs/fargate-microservice, **stepfunctions/etl-pipeline** (validate, clean, partition by order date, reject bad rows) | Glue scripts and EMR Spark don't run in Floci (card) |
| 1.3 | ● | stepfunctions/order-processing-workflow, parallel-retry-workflow, eventbridge/application-spine, sns/sqs-fanout, sqs/dead-letter-redrive, lambda/async-error-handling, **stepfunctions/etl-pipeline** (Map, Retry, Catch, S3 events, nightly schedule), **mwaa/airflow-pipeline** (a DAG with retries and a failure callback) | — |
| 1.4 | ● | lambda/performance-cost, runtime-config, cloudformation/sam-environments, s3-sqs-stack, codepipeline/lambda-cicd, dynamodb/lambda-writes, lambda/sqs-event-source, **stepfunctions/etl-pipeline** (transform code, IAM least privilege), **mwaa/airflow-pipeline** (DAG code in versioned S3, rollback), **kinesis/stream-processing** (idempotent consumer) | — |
| 2.1 | ● | dynamodb/capacity-at-scale, application-patterns, crud-query, rds/db-instance-lifecycle, aurora-resilience, elasticache/cache-aside, s3vectors/rag-knowledge-base (vector index), ec2/block-file-storage, s3/object-workflow, **redshift/serverless-warehouse** | Choosing between Redshift, DynamoDB and S3 for a pipeline sink (card) |
| 2.2 | ● | kinesis/streaming-data-lake (a Glue table with partition projection), **glue/catalog-quality** (partitions in sync, partition index, crawler, table versions) | Crawlers store their settings but infer nothing in Floci (the lab infers the schema) |
| 2.3 | ● | s3/lifecycle-retention, version-recovery, data-protection-tiers, dynamodb/capacity-at-scale (TTL), backup/backup-dr-plan, restore-drill, rds/aurora-resilience, **redshift/serverless-warehouse** (staged loads, export, lifecycle) | COPY and UNLOAD are refused by Floci (the lab loads with INSERT) |
| 2.4 | ● | dynamodb/application-patterns (key design), crud-query, dms/migration-toolkit (SCT), **redshift/serverless-warehouse** (star schema, keys), **glue/catalog-quality** (Schema Registry, table versions) | Redshift DISTKEY and SORTKEY shown, not run (card) |
| 3.1 | ● | stepfunctions/order-processing-workflow, parallel-retry-workflow, eventbridge/application-spine, scheduler/sqs-delivery, s3/object-notifications-sqs, lambda/create-invoke-logs, **stepfunctions/etl-pipeline**, **mwaa/airflow-pipeline** (extract, transform, load, check in Airflow) | — |
| 3.2 | ● | kinesis/streaming-data-lake (Athena queries), **redshift/serverless-warehouse** (materialized view, window function), **glue/catalog-quality** (Athena) | QuickSight, DataBrew, and Athena Spark not in Floci (card) |
| 3.3 | ● | cloudwatch/metric-alarms, log-groups-metric-filters, centralized-logging, serverless-observability, cloudtrail/governance-tour, sqs/dead-letter-redrive, lambda/async-error-handling, **stepfunctions/etl-pipeline** (failure alerts), **glue/catalog-quality** (quality alarm), **mwaa/airflow-pipeline** (a failed task found, cleared and rerun), **kinesis/stream-processing** (poison record found from failure metadata) | Airflow task logs don't reach CloudWatch in Floci (the lab reads task states from the CLI) |
| 3.4 | ● | **glue/catalog-quality** (rules, scores, quarantine), **stepfunctions/etl-pipeline** (rejected rows, Athena check) | Glue Data Quality evaluation runs and DataBrew not in Floci (the lab scores the rules) |
| 4.1 | ● | secretsmanager/secret-lifecycle, iam/role-trust-policy, sts-session-policy, ec2-instance-profile, machine-identity, ec2/security-controls, s3-gateway-endpoint, sqs-interface-endpoint, **lakeformation/lake-governance** (an assumed analyst role) | — |
| 4.2 | ● | iam/machine-identity (ABAC), identity-enforcement-capstone, inline-policy-alice, sts-session-policy, access-troubleshooting, s3/bucket-security, ssm/parameter-store-config, secretsmanager/secret-lifecycle, lambda/runtime-config, **redshift/serverless-warehouse** (grants enforced per user), **lakeformation/lake-governance** (LF-Tags and grants, a prefix-limited IAM policy) | Lake Formation grants are stored, not enforced (the lab evaluates them; IAM is enforced) |
| 4.3 | ● | kms/key-management, key-alias-encrypt-decrypt, s3/default-encryption, bucket-security, bedrock/guardrails-least-privilege (PII masking), iam/access-troubleshooting (TLS), **redshift/serverless-warehouse** (masked view), **lakeformation/lake-governance** (SSE-KMS, PII found and pseudonymized) | Macie not routed (the lab scans for PII) |
| 4.4 | ● | cloudtrail/governance-tour, cloudwatch/centralized-logging, log-groups-metric-filters, **lakeformation/lake-governance** (S3 data events, a denied read found in the trail) | CloudTrail Lake not in Floci (card) |
| 4.5 | ● | iam/multi-account-guardrails (SCPs), cloudtrail/governance-tour (Config), s3/data-protection-tiers (replication), backup/backup-dr-plan, **lakeformation/lake-governance** (PII classification, Region guardrail) | Macie findings not routed (card) |

Most of domain 4 and the general parts of domains 1 to 3 are hands-on from earlier tracks. The gaps are the data engineer's own tools: the warehouse, the catalog, data quality, pipeline orchestration, stream processing, and lake governance.

### What Floci 2.4.0 does for data services

Probed 2026-11-06 with real SDK calls, and checked against the Floci source at the 2.4.0 tag. The Wave 2 notes in §4 on Firehose, Glue, and Athena still apply.

| Area | Result |
|---|---|
| **Redshift Serverless and Redshift** | **Behaves.** A workgroup (or a provisioned cluster) is a real PostgreSQL 15 container. The Data API runs SQL asynchronously (`ExecuteStatement`, `BatchExecuteStatement` as one transaction, `DescribeStatement`, `GetStatementResult`) and rejects several statements in one `ExecuteStatement`, as AWS does. Window functions, views, and materialized views (with `REFRESH`) work. Database users and grants are enforced: a statement run with another user's secret (`SecretArn`) is refused on a table it was not granted. Namespace snapshots and usage limits exist. Redshift-only syntax fails: `DISTSTYLE`, `DISTKEY`, `SORTKEY`, `ENCODE`, `COPY`, `UNLOAD`, `LISTAGG`, `GETDATE`, and `APPROXIMATE COUNT`. `SVV_TABLE_INFO` exists. |
| **Athena** | **Behaves.** SQL runs in DuckDB over Glue tables (CSV, JSON, Parquet, Iceberg), including partitioned tables. No CTAS or Hive DDL. The DuckDB sidecar downloads an extension on first use, so Athena needs internet access once. |
| **Glue Data Catalog** | **Behaves.** Databases, tables, partitions (`BatchCreatePartition`, `GetPartitions` with an expression), partition indexes, table versions, connections. |
| **Glue Schema Registry** | **Behaves.** Compatibility is enforced: a `BACKWARD` schema accepts a new Avro version that adds a field with a default and refuses one that changes a field's type. |
| **Glue crawlers, jobs, workflows** | **Control plane.** A crawler runs and finishes without creating tables or partitions. A job run reports `SUCCEEDED` without running its script. Workflows and triggers start runs. |
| **Glue Data Quality** | **Stored only.** `CreateDataQualityRuleset` stores DQDL; `StartDataQualityRulesetEvaluationRun` is unsupported. |
| **Lake Formation** | **Stored, not enforced.** Data lake settings, registered locations, grants (including column grants), and LF-Tags are stored and listed. Athena and Redshift ignore them. `CreateDataCellsFilter` is not routed (an S3 signature error). |
| **Step Functions** | **Partial.** Lambda, SNS, SQS, DynamoDB, and the Redshift Data API (`aws-sdk:redshiftdata`) integrations run. The Athena and Glue integrations (`athena:startQueryExecution`, `glue:startJobRun`, `.sync` or not) are unsupported. |
| **MWAA** | **Behaves.** An environment is a real Airflow 2.10 (webserver and scheduler) with its own PostgreSQL, ready in about 30 seconds once the images are pulled (about 1.5 GB). DAG files sync from the S3 `dags` folder every 30 seconds (`dags reserialize` loads them at once). `CreateCliToken` and the `/aws_mwaa/cli` endpoint run allowed Airflow CLI commands, and DAG code reaches Floci through boto3. |
| **MSK** | **Behaves.** A cluster is a real Redpanda broker that Kafka clients can use; configurations are stored. Redpanda's HTTP proxy does not create topics. |
| **Managed Service for Apache Flink** | **Behaves.** `StartApplication` runs a real Flink cluster and submits the application's JAR from S3. A lab would need a prebuilt JAR. |
| **EMR and EMR Serverless** | **Control plane** by default. Job runs succeed without running; Spark runs only when Floci is started with the docker runner mode, which the dashboard's Compose file does not set. |
| **Not routed or unsupported** | Glue DataBrew, AppFlow, QuickSight dashboards, Athena Spark, CloudTrail Lake, Macie findings (`AuthorizationHeaderMalformed`), and the SageMaker Catalog. |

### Wave E: Data Engineer labs

A new curriculum level, **Level 12: Data Engineer Associate**, holds these labs. Each lab follows the existing pattern and says in each step what Floci only stores versus what it does. The labs ship in two batches (E-1 to E-3, then E-4 to E-6) to keep the number of PRs small.

| # | Proposed lab | Steps | DEA | Floci |
|---|---|---|---|---|
| ✅ E-1 | **Redshift Serverless warehouse** (shipped 2026-11-06 as `redshift/serverless-warehouse`, 8 steps, the first lab of Level 12): a namespace and workgroup, a star schema designed with distribution and sort keys (shown as the Redshift DDL, run as standard SQL), sales staged in S3 and loaded through the Data API in a transaction, a materialized view and a rolling seven-day average, an analyst user who can read only a masked view, a usage limit and a snapshot, and UNLOAD as the exam lens | 8 | 2.1, 2.4, 2.3, 3.2, 4.2, 4.3 | ✅ PostgreSQL SQL, transactions, materialized views, grants enforced per user; ⚠️ DISTKEY, SORTKEY, COPY and UNLOAD not accepted (the lab loads with INSERT and explains COPY) |
| ✅ E-2 | **Catalog, schema evolution, and data quality** (shipped 2026-11-06 as `glue/catalog-quality`, 8 steps; a column added to the table must be in every file, or Floci's Athena fails, so the lab backfills older files): a partitioned Glue table over raw orders, new partitions registered and found with an expression, a partition index, a new column with a table version, an Avro schema in the Schema Registry that accepts a compatible change and refuses a breaking one, a crawler, a DQDL ruleset evaluated with Athena SQL, bad rows quarantined, and a quality score metric with an alarm | 8 | 2.2, 3.4, 2.4, 3.2, 3.3 | ✅ catalog, partitions, indexes, table versions, Schema Registry compatibility, Athena; ⚠️ crawlers create nothing and DQDL is stored only (the lab evaluates the rules) |
| ✅ E-3 | **Event-driven batch ETL** (shipped 2026-11-06 as `stepfunctions/etl-pipeline`, 8 steps; Floci checks the IAM policy of both the workflow and the function role): a Map state runs a Lambda transform per file that validates rows, writes JSON partitioned by order date, and rejects bad rows; Retry for transient errors and Catch to set a bad file aside; an SNS alert to SQS when a file fails; a DynamoDB manifest that makes reruns skip finished files; S3 Object Created events through EventBridge with an input transformer; a nightly EventBridge Scheduler sweep with a dead-letter queue; a fixed file replayed by its upload; and an Athena check of the curated output | 8 | 1.2, 1.3, 1.4, 3.1, 3.3, 3.4 | ✅ Lambda, Map, Retry, Catch, JSONPath filters, S3 events, EventBridge, Scheduler, SNS to SQS, Athena; ⚠️ no Athena or Glue service integrations in Step Functions (the lab runs the Athena check) |
| ✅ E-4 | **Airflow pipeline on MWAA** (shipped 2026-11-06 as `mwaa/airflow-pipeline`, 8 steps; the dashboard reaches the Airflow CLI on Floci's Docker network, so run it with Docker Compose): a versioned DAG bucket with requirements, an Airflow 2.10.5 environment with an execution role, a DAG of extract, transform, load and check tasks with retries and a failure callback that alerts through SNS, a run triggered with a CLI token, a late file that fails the extract, its task states and alert, the task cleared with its downstream tasks and rerun, the workers scaled, and a broken DAG rolled back to the previous S3 version | 8 | 1.3, 3.1, 3.3, 1.4 | ✅ real Airflow, DAG sync from S3, CLI token, variables, retries, callbacks, boto3 from tasks; ⚠️ pools and Airflow configuration options refused, no task logs in CloudWatch |
| ✅ E-5 | **Stream processing with Kinesis, Lambda, and MSK** (shipped 2026-11-06 as `kinesis/stream-processing`, 8 steps, the second Kinesis lab): a stream with 48-hour retention, an event source mapping with a batch window, bisecting, two retries and an SQS on-failure destination, an idempotent consumer that counts each click once in a DynamoDB transaction, a cut-off record isolated and read back by its sequence number, a replay from a timestamp, an enhanced fan-out consumer, a shard split, and an MSK cluster with its bootstrap brokers | 8 | 1.1, 1.4, 3.3 | ✅ real polling, batching, bisecting, retries, destination messages, AT_TIMESTAMP, enhanced fan-out, resharding, a real Kafka broker; ⚠️ tumbling windows and ParallelizationFactor dropped |
| ✅ E-6 | **Data lake governance** (shipped 2026-11-06 as `lakeformation/lake-governance`, 8 steps): a lake bucket with SSE-KMS by default and a bucket policy that refuses unencrypted uploads and plain HTTP, the location registered with Lake Formation, LF-Tags on the database and columns, a tag-based grant to an analyst role (stored, and evaluated by the lab), PII found and pseudonymized into a curated zone, an IAM policy that limits the analyst to the curated prefix (enforced: the raw read is AccessDenied), a Region guardrail, and a trail with S3 data events that records the denied read | 8 | 4.2, 4.3, 4.5, 4.4, 4.1 | ✅ IAM prefix policies and Region conditions enforced, KMS, CloudTrail data events delivered to S3; ⚠️ Lake Formation grants not enforced, tag searches not routed, Macie not routed (the lab scans for PII) |

**DEA coverage:** Wave E is complete. All six labs shipped on 2026-11-06 and all 17 task statements are ● hands-on: E-1 made 2.4 and 3.2 hands-on, E-2 made 2.2 and 3.4 hands-on, and E-3 made 1.2 hands-on. E-4 to E-6 add Airflow orchestration, stream processing, and lake governance to tasks that were already hands-on, and no concept card names a planned lab. Glue ETL scripts, crawlers that infer schemas, DataBrew, QuickSight, and Lake Formation enforcement stay concept cards until Floci adds them.

---

## 12. Extending to other certification tracks

The structure above is exam-agnostic. A new track needs three things:

1. An `EXAM_GUIDES` entry, copied from the official guide.
2. `LAB_EXAM_TAGS` rows for existing labs.
3. Concept cards and new labs for the gaps.

Labs are shared across tracks, so a lab tagged for SAA 2.1 can also be tagged for Developer and DevOps tasks. Current AWS lineup per [aws.amazon.com/certification](https://aws.amazon.com/certification/exams/) (2026-10-22). Confirm exam codes there before starting a track.

| Track | Fit with today's labs | Likely new labs | Floci constraints |
|---|---|---|---|
| **Developer – Associate (DVA-C02)** | **Track live since 2026-10-24; see [§7](#7-developer--associate-dva-c02).** | Wave D (D-1 to D-6) | X-Ray, CodeCommit, and Private CA unsupported |
| **CloudOps Engineer – Associate (SOA-C03)**, which replaced SysOps (SOA-C02, retired 2025-09-29) | **Track live since 2026-11-04; see [§10](#10-cloudops-engineer--associate-soa-c03).** | Wave O (O-1 to O-7) | No CloudWatch agent, Automation executions, Patch Manager, EBS snapshots, Trusted Advisor, or enforced network rules |
| **DevOps Engineer – Professional (DOP-C02)** | **Track live since 2026-11-02; see [§9](#9-devops-engineer--professional-dop-c02).** | Wave P (P-1 to P-7) | No drift detection, SSM Automation, Patch Manager, Config remediation, or ABAC evaluation |
| **Solutions Architect – Professional** (training vendors report SAP-C02's last day as 2026-11-16 with a successor to follow; not confirmed on aws.amazon.com) | Builds on the SAA track. | Multi-account network hub (TGW), cross-account roles at scale, DR across Regions, migration with DMS | Hybrid networking and real multi-Region behavior are mostly concept |
| **Security – Specialty** | IAM, KMS, S3 security, VPC controls. | KMS and key policies, Secrets rotation, GuardDuty, Security Hub, Macie, Config rules, CloudTrail analysis, WAF | **Key-policy and SCP enforcement gaps** limit "prove the deny" labs; detection services are control plane only |
| **Advanced Networking – Specialty** (AWS lists retirement on 2026-12-31) | VPC labs. | Low priority given the retirement | VPN and Direct Connect unsupported |
| **Data Engineer – Associate (DEA-C01)** | **Track live since 2026-11-06; see [§11](#11-data-engineer--associate-dea-c01).** | Wave E (E-1 to E-6) | Glue scripts and crawlers don't run, Data Quality is stored only, Lake Formation is not enforced, no DataBrew or QuickSight |
| **AI Practitioner (AIF-C01)** | **Track and Wave AI complete since 2026-10-26; see [§8](#8-ai-practitioner-aif-c01).** | Fine-tuning and explainability if Floci gains Bedrock customization or SageMaker Clarify | Bedrock answers and the AI services are stubs |
| **Machine Learning Engineer – Associate** | Medium. Builds on Wave AI's SageMaker lab. | Training jobs with tuning, endpoint variants, batch transform, Feature Store, Pipelines | Only training jobs and endpoints are emulated; no tuning, Pipelines, Model Monitor, or Feature Store |

**Suggested order after CLF and SAA:** Developer (cheapest, since most labs exist), then CloudOps, then Security, then Data Engineer, then the Professional exams.

---

## 13. Keeping this current

- **When AWS revises a guide,** update `EXAM_GUIDES`. The "no orphan objective" test then flags labs tagged with removed task IDs.
- **When Floci upgrades,** rerun the probe for anything marked control plane, unsupported, or inconclusive in §4. Several topics, especially SCP and KMS key-policy enforcement, S3 replication, and NAT egress, would move labs from "configuration only" to "prove it works."
- **When a lab ships,** add its tags in the same change and update this document's tables until the UI gap view replaces them.
