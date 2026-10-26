# Certification Coverage: CLF-C02, SAA-C03, DVA-C02, and AIF-C01

This document maps the Floci Dashboard workflow labs to the official AWS exam guides for **AWS Certified Cloud Practitioner (CLF-C02)** and **AWS Certified Solutions Architect – Associate (SAA-C03)**, and, since 2026-10-24, **AWS Certified Developer – Associate (DVA-C02)** ([§7](#7-developer--associate-dva-c02)) and, since 2026-10-26, **AWS Certified AI Practitioner (AIF-C01)** ([§8](#8-ai-practitioner-aif-c01)). It also lists what is missing and proposes a prioritized plan to close the gaps. It is the first step toward exam-aligned study tracks for every AWS certification.

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
- **AI Practitioner (AIF-C01) is the fourth track (2026-10-26).** Every task statement has a concept card and a four-question quiz, and ten existing labs are tagged, nine of them for the security and governance domain. Floci 2.3.0 runs real SageMaker training jobs and endpoints and real S3 Vectors similarity search, while Bedrock, Comprehend, Rekognition, Textract, Translate, and Transcribe return stub answers. **Wave AI** (five labs, [§8](#8-ai-practitioner-aif-c01)) adds hands-on practice for nine of the 14 task statements.
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

### Coverage by task statement (existing 95 labs)

| Task | Coverage | Covered by | Gap |
|---|---|---|---|
| 1.1 | ○ | Concept card and quiz | Theory only |
| 1.2 | ○ | Concept card and quiz | AI-1 |
| 1.3 | ◐ | kinesis/streaming-data-lake (collect, catalog, and prepare data with Glue and Athena) | Training, hosting, and monitoring: AI-2 |
| 2.1 | ○ | Concept card and quiz | Theory only |
| 2.2 | ○ | Concept card and quiz | Theory only |
| 2.3 | ○ | Concept card and quiz | AI-3 |
| 3.1 | ○ | Concept card and quiz | AI-3 |
| 3.2 | ○ | Concept card and quiz | AI-4 |
| 3.3 | ○ | Concept card and quiz | Floci has no Bedrock customization or SageMaker fine-tuning recipes, so this stays conceptual |
| 3.4 | ○ | Concept card and quiz | AI-4 scores answers with ROUGE and BLEU computed by the lab |
| 4.1 | ○ | Concept card and quiz | AI-5 |
| 4.2 | ○ | Concept card and quiz | SageMaker Clarify and Model Cards are not emulated |
| 5.1 | ● | iam/account-hardening, kms/key-management, s3/bucket-security, ec2/s3-gateway-endpoint, secretsmanager/secret-lifecycle, ec2/shared-responsibility, cloudtrail/governance-tour | Bedrock-specific least privilege: AI-5 |
| 5.2 | ● | cloudtrail/governance-tour (CloudTrail, Config rules), iam/multi-account-guardrails, s3/data-protection-tiers (retention) | Audit Manager and Artifact are concept-only |

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

### Wave AI: AI Practitioner labs

Each lab follows the existing pattern and says in each step what Floci only stores versus does.

| # | Proposed lab | AIF | Floci |
|---|---|---|---|
| AI-1 | **AI services tour**: sentiment and PII with Comprehend, labels and moderation with Rekognition, a form with Textract, Translate, a Transcribe job, then match business problems to services | 1.2 | ⚠️ Stub answers; the lab scores sample results itself |
| AI-2 | **SageMaker from training to endpoint**: training data in S3, a training job with hyperparameters, model artifacts, a model, an endpoint config and endpoint, real predictions, and a blue/green `UpdateEndpoint` | 1.3 | ✅ Real containers |
| AI-3 | **RAG with S3 Vectors and Bedrock**: chunk and embed policy documents, store them in a vector index, retrieve with metadata filters, ground a Converse prompt, and price it by tokens | 3.1, 2.3 | ✅ Retrieval is real; ⚠️ the answer is a stub |
| AI-4 | **Prompt patterns with the Converse API**: system prompt, zero-shot vs few-shot, temperature and max tokens, a prompt injection attempt, and ROUGE and BLEU scores for sample outputs | 3.2, 3.4 | ⚠️ Stub answers unless the proxy backend is on |
| AI-5 | **Bedrock guardrails and least privilege**: a guardrail with content filters, a denied topic, and PII masking, versions, a KMS key, an IAM policy that allows one model, and the lab's own check of sample prompts | 4.1, 5.1 | ⚠️ Guardrails are stored, not applied |

**Projected AIF coverage:** after Wave AI, 1.2, 1.3, 2.3, 3.1, 3.2, 3.4, 4.1, 5.1, and 5.2 have labs (9 of 14). 1.1, 2.1, 2.2, 3.3, and 4.2 stay concept cards and quizzes, which suits a foundational exam that tests understanding more than hands-on skill.

---

## 9. Extending to other certification tracks

The structure above is exam-agnostic. A new track needs three things:

1. An `EXAM_GUIDES` entry, copied from the official guide.
2. `LAB_EXAM_TAGS` rows for existing labs.
3. Concept cards and new labs for the gaps.

Labs are shared across tracks, so a lab tagged for SAA 2.1 can also be tagged for Developer and DevOps tasks. Current AWS lineup per [aws.amazon.com/certification](https://aws.amazon.com/certification/exams/) (2026-10-22). Confirm exam codes there before starting a track.

| Track | Fit with today's labs | Likely new labs | Floci constraints |
|---|---|---|---|
| **Developer – Associate (DVA-C02)** | **Track live since 2026-10-24; see [§7](#7-developer--associate-dva-c02).** | Wave D (D-1 to D-6) | X-Ray, CodeCommit, and Private CA unsupported |
| **CloudOps Engineer – Associate (SOA-C03)**, which replaced SysOps (SOA-C02, retired 2025-09-29) | Medium. CloudWatch, SSM Run Command, the route-repair lab, and CloudFormation fit. | CloudWatch agent and dashboards, alarms to auto-remediation, Backup, ASG ops, Config remediation | SSM Automation, Patch Manager, and Session Manager unsupported; composite alarms unsupported |
| **DevOps Engineer – Professional** | Medium. CloudFormation, ECS, EKS, and EventBridge. | Blue/green and canary deployments (Lambda aliases are verified), CodePipeline, StackSets, drift, event-driven remediation | Pipeline execution depth unverified |
| **Solutions Architect – Professional** (training vendors report SAP-C02's last day as 2026-11-16 with a successor to follow; not confirmed on aws.amazon.com) | Builds on the SAA track. | Multi-account network hub (TGW), cross-account roles at scale, DR across Regions, migration with DMS | Hybrid networking and real multi-Region behavior are mostly concept |
| **Security – Specialty** | IAM, KMS, S3 security, VPC controls. | KMS and key policies, Secrets rotation, GuardDuty, Security Hub, Macie, Config rules, CloudTrail analysis, WAF | **Key-policy and SCP enforcement gaps** limit "prove the deny" labs; detection services are control plane only |
| **Advanced Networking – Specialty** (AWS lists retirement on 2026-12-31) | VPC labs. | Low priority given the retirement | VPN and Direct Connect unsupported |
| **Data Engineer – Associate** | Low today. | Kinesis, Firehose, Glue, Athena, Lake Formation, Redshift, EMR, Step Functions pipelines | Verify Glue and Athena execution depth first |
| **AI Practitioner (AIF-C01)** | **Track live since 2026-10-26; see [§8](#8-ai-practitioner-aif-c01).** | Wave AI (AI-1 to AI-5) | Bedrock answers and the AI services are stubs |
| **Machine Learning Engineer – Associate** | Low. Builds on Wave AI's SageMaker lab. | Training jobs with tuning, endpoint variants, batch transform, Feature Store, Pipelines | Only training jobs and endpoints are emulated; no tuning, Pipelines, Model Monitor, or Feature Store |

**Suggested order after CLF and SAA:** Developer (cheapest, since most labs exist), then CloudOps, then Security, then Data Engineer, then the Professional exams.

---

## 10. Keeping this current

- **When AWS revises a guide,** update `EXAM_GUIDES`. The "no orphan objective" test then flags labs tagged with removed task IDs.
- **When Floci upgrades,** rerun the probe for anything marked control plane, unsupported, or inconclusive in §4. Several topics, especially SCP and KMS key-policy enforcement, S3 replication, and NAT egress, would move labs from "configuration only" to "prove it works."
- **When a lab ships,** add its tags in the same change and update this document's tables until the UI gap view replaces them.
