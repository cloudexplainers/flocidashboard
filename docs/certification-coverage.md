# Certification Coverage: CLF-C02 and SAA-C03

This document maps the Floci Dashboard workflow labs to the official AWS exam guides for **AWS Certified Cloud Practitioner (CLF-C02)** and **AWS Certified Solutions Architect – Associate (SAA-C03)**. It also lists what is missing and proposes a prioritized plan to close the gaps. It is the first step toward exam-aligned study tracks for every AWS certification.

It is a planning document. No labs are built here; pick from [the plan](#5-prioritized-lab-plan).

**Checked on:** 2026-10-22, against the AWS exam guides and a disposable Floci **2.3.0** emulator (`compose.labs.yml`, port 14566).

---

## TL;DR

- **The current 78 labs (484 steps) are strong where SAA is strongest:** IAM and identity (SAA 1.1), VPC security and endpoints (SAA 1.2), and decoupled, event-driven design with SQS, SNS, EventBridge, Lambda, Step Functions, and API Gateway (SAA 2.1).
- **There are big holes in high-weight SAA topics:** load balancing and Auto Scaling, Multi-AZ and read replicas, caching, Route 53 and DR, storage classes and tiering, the data and streaming domain (SAA 3.5), and nearly all of cost optimization (SAA domain 4, 20%).
- **For CLF, coverage is lopsided.** The labs serve the hands-on parts of domains 2 and 3. Domain 1 (cloud concepts, 24%) and domain 4 (billing and support, 12%) are almost entirely conceptual, so labs can't cover them. Those need "exam lens" concept cards, not labs.
- **Floci 2.3.0 can support most of the missing SAA hands-on topics.** Verified working: ALB data plane, Auto Scaling groups launching instances, RDS/Aurora/ElastiCache as real containers, Postgres read replicas and snapshots, CloudWatch alarms evaluating real metrics, and S3 Object Lock enforcement. Many other services accept the API but don't simulate the behavior (S3 replication, storage-class retrieval, NAT, CloudFront, SCPs, KMS key policies). Labs on those must be honest about it.
- **Recommended Wave 1 (six labs):** HA web tier (ALB + ASG), RDS/Aurora resilience, S3 data protection and storage tiers, a NAT/three-tier VPC extension, KMS key management, and Route 53 routing and failover.
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
| 1.1 | Benefits of the AWS Cloud | 📖 | — | Concept |
| 1.2 | Well-Architected pillars | 📖 | — | Concept. Could tag each lab with its primary pillar. |
| 1.3 | Migration and the CAF | 📖 | — | Concept. DMS can be labbed lightly (see SAA 4.3). |
| 1.4 | Cloud economics | 📖 / ◐ | #66 (automation) | Rightsizing and BYOL are concepts. |
| 2.1 | Shared responsibility | 📖 / ◐ | EC2 vs RDS vs Lambda labs exist | No lab makes the comparison explicit. Ideal as an exam-lens card on #15, #31, and #34. |
| 2.2 | Security, governance, compliance | ◐ | #20, #21, #24, #54, #55 | **CloudTrail, Config, GuardDuty, Inspector, Security Hub, Shield, Artifact** |
| 2.3 | Access management | ● | 11 IAM labs, #27, #28, #29, #37 | Root-user protection, **MFA, password policy, IAM Identity Center**, federation |
| 2.4 | Security components and resources | ○ | — | WAF, Shield, Firewall Manager, Trusted Advisor, Marketplace |
| 3.1 | Deploying and operating | ● | #9, #35, #57, #66 | Console vs CLI vs IaC is already implicit. Make it explicit. |
| 3.2 | Global infrastructure | ◐ | Subnets pin AZs in #32 and #33, route53/routing-failover (failover and latency routing across Regions) | Multi-AZ HA, edge locations |
| 3.3 | Compute | ◐ | #15, #60, #65, guided EC2, lambda/performance-cost (memory sizing, versions, aliases, concurrency) | **Instance types, Auto Scaling, load balancers** |
| 3.4 | Databases | ◐ | #14, #31, #39, elasticache/cache-aside (Aurora source of truth, ElastiCache in front), dynamodb/capacity-at-scale (capacity modes, TTL, streams, global tables) | EC2-hosted vs managed, DMS |
| 3.5 | Network | ● | #21, #32, #33, #36, #61, #62, #63, #64, route53/routing-failover (hosted zones, routing policies) | VPN and Direct Connect (concept) |
| 3.6 | Storage | ◐ | 13 S3 labs, ec2/block-file-storage (EBS gp3 and io2, EFS), backup/backup-dr-plan (vault, plan, retention) | **Storage classes**, instance store (concept), FSx, Storage Gateway |
| 3.7 | AI/ML and analytics | ◐ | kinesis/streaming-data-lake (Kinesis, Firehose, Glue, Athena) | SageMaker AI, Lex, Quick (concept) |
| 3.8 | Other categories | ◐ | SQS, SNS, EventBridge labs | SES, CodeBuild and CodePipeline, X-Ray, IoT Core, Amplify, Connect, WorkSpaces |
| 4.1 | Pricing models | ○ / 📖 | — | On-Demand, RI, Spot, Savings Plans, data transfer, storage tiers |
| 4.2 | Billing and cost tools | ○ | — | **Budgets, Cost Explorer**, Organizations consolidated billing, cost allocation tags |
| 4.3 | Support and resources | 📖 | — | Support plans, re:Post, Trusted Advisor, Health, APN |

**CLF reading:** more than a third of CLF weight (all of domains 1 and 4 = 36%, plus parts of 2.1, 2.4, and 3.2) is conceptual. The highest-value CLF additions are concept cards plus a few lightweight labs: account hardening, cost tools, and governance services.

### SAA-C03

| Task | Topic | Coverage | Covered by | Gap |
|---|---|---|---|---|
| 1.1 | Secure access | ● | 11 IAM labs, #21, #37 | **Multi-account: Organizations, SCPs, Control Tower**; MFA; IAM Identity Center; directory federation |
| 1.2 | Secure workloads | ● | #27, #28, #29, #32, #33, #38, #62, #63, #64, ec2/nat-three-tier (NAT gateways) | WAF and Shield, GuardDuty and Macie, VPN and Direct Connect |
| 1.3 | Data security controls | ◐ | #19, #20, #21, #22, #24, #26, s3/data-protection-tiers (Object Lock, CRR config), kms/key-management (key policies, rotation, grants, envelope encryption, SSE-KMS), backup/backup-dr-plan (KMS-encrypted vault, vault lock) | **ACM/TLS**, secret rotation |
| 2.1 | Scalable, loosely coupled | ● | ~20 labs (SQS, SNS, EventBridge, Lambda, Step Functions, API Gateway, ECS), elasticache/cache-aside (caching) | **Load balancing (ALB), horizontal scaling, CDN, read replicas**, storage types (block vs file) |
| 2.2 | HA and fault tolerance | ◐ | #19, #44, #53, #61, #66, route53/routing-failover (health checks, failover records, failover drill), dynamodb/capacity-at-scale (PITR, global table replica), backup/backup-dr-plan (cross-Region copy, DR strategy by RPO and RTO) | **Multi-AZ, RDS Proxy, Aurora failover**, quotas and throttling, X-Ray |
| 3.1 | Storage performance | ◐ | #25, S3 basics, ec2/block-file-storage (gp3 and io2 limits, Elastic Volumes, EFS throughput and mount targets) | hybrid storage, FSx |
| 3.2 | Elastic compute | ◐ | #50, #60, #65, lambda/performance-cost (memory sweep, reserved concurrency, provisioned concurrency in concept) | **Auto Scaling and scaling metrics, instance families**, Batch and EMR |
| 3.3 | Database performance | ◐ | #14, #31, elasticache/cache-aside (cache-aside, TTLs, invalidation, eviction policy, Multi-AZ replication group), dynamodb/capacity-at-scale (RCU and WCU sizing, auto scaling, streams) | **Read replicas, Aurora, DAX, RDS Proxy** |
| 3.4 | Network performance | ◐ | #32, #61, #62, #64, ec2/nat-three-tier, route53/routing-failover (latency and weighted routing) | **CloudFront, Global Accelerator, ALB vs NLB**, VPN, Direct Connect |
| 3.5 | Data ingestion and transformation | ● | kinesis/streaming-data-lake (Data Streams, Firehose to partitioned S3, Glue table with partition projection, Athena, JSON to Parquet) | Lake Formation, DataSync, Glue ETL jobs |
| 4.1 | Cost-optimized storage | ◐ | #22, #25, ec2/block-file-storage (gp3 vs io2 monthly cost, EFS IA and Archive tiering) | backup/backup-dr-plan (cold storage after 90 days, 35-day vs 7-year retention) | **Storage tiers and transitions, Intelligent-Tiering, Requester Pays**, cost tools |
| 4.2 | Cost-optimized compute | ◐ | #60 touches Fargate, lambda/performance-cost (cost per million requests by memory size, arm64 pricing) | **Spot, RI, Savings Plans, instance sizing, ALB vs NLB vs GWLB**, scaling strategies |
| 4.3 | Cost-optimized databases | ◐ | dynamodb/capacity-at-scale (provisioned with auto scaling vs on-demand, TTL instead of paid deletes) | **Aurora Serverless**, snapshot retention, DMS |
| 4.4 | Cost-optimized networking | ◐ | #62, #63 (endpoints), ec2/nat-three-tier (NAT per AZ vs shared, NAT vs gateway endpoint) | **VPC peering vs Transit Gateway**, CloudFront, API throttling |

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
| Edge | CloudFront distribution, Global Accelerator | No edge caching |
| Route 53 | Hosted zones, private zones and VPC associations, failover, weighted, latency and alias records, health checks | Checked again 2026-10-23: health checks never probe (every check reports success) and Floci drops their Regions, Inverted and calculated or alarm settings; `UpdateHealthCheck` replaces the whole config. Records keep failover, weighted, latency and alias fields but lose GeoLocation and MultiValueAnswer. No routing validation: two primaries, weights above 255, mixed policies, a CNAME at the apex, an alias to a missing name, and missing health check IDs are all accepted. Floci's DNS server answers A records in private zones only (to any client, every value); public zones get NXDOMAIN, and `TestDNSAnswer` is unsupported. |
| Org governance | Organizations, OUs, create-account, SCP create and attach, IAM Identity Center instance, Control Tower list | SCP enforcement wasn't observable. The test ran in the management account, which AWS also exempts, so the result is inconclusive. |
| KMS | Rotation, key policies, grants, `generate-data-key` | Checked again 2026-10-23: **a key-policy Deny was not enforced**, and a key policy without the root statement didn't block an IAM-allowed user. KMS calls are authorized from IAM policies only, so a grant gave its grantee nothing. Malformed or lockout policies and missing principals are accepted. Real: data keys, encryption context on decrypt, on-demand rotation (old ciphertext still decrypts), Disabled and PendingDeletion refusing requests. Automatic rotation is a flag with no period; a cancelled deletion returns to Enabled (AWS: Disabled); `ListKeyRotations` is unsupported. SSE-KMS on S3 never calls KMS. |
| Security services | GuardDuty detector, Security Hub, Macie, WAFv2 web ACL with a rate rule, Network Firewall list, ACM request | No findings and no WAF blocking were observed |
| Audit | CloudTrail trail, Config recorder and managed rule | `lookup-events` returned nothing. The Config rule stayed `INSUFFICIENT_DATA`. |
| Data protection | DLM | No lifecycle policies (AWS Backup is now in `backup/backup-dr-plan`) |
| Migration and transfer | DMS replication instance, DataSync location, Transfer Family server | No data moved |
| Cost | Budgets, Cost Explorer (synthetic, near-zero amounts), CUR definitions, Pricing `get-products` | No real spend |
| Other | API Gateway REST usage plans and API keys, Glue database and crawler, Lake Formation settings, Batch, Elastic Beanstalk, ECR, Lightsail, IoT things, SES identity, CodeBuild, CodePipeline, CodeDeploy, Comprehend (stub), Translate (echo) | Varies |

### Unsupported in Floci 2.3.0

- **Networking:** Site-to-Site VPN (`CreateVpnGateway`, `CreateCustomerGateway`), Direct Connect, placement groups.
- **Storage:** EBS **snapshots**, FSx, Storage Gateway.
- **Databases:** RDS **MySQL** snapshots and read replicas (Postgres works), DynamoDB `CreateBackup` and `RestoreTableToPointInTime`, DAX.
- **Purchasing and cost:** Reserved Instance offerings, Savings Plans, Compute Optimizer, Pricing Calculator API, Cost Explorer cost-allocation-tag APIs.
- **Ops:** CloudWatch alarm actions (an alarm in ALARM sent nothing to its SNS topic), `describe-alarm-history`, CloudWatch composite alarms, SSM Session Manager, Automation, and Patch Manager, X-Ray, AWS Health, Trusted Advisor and Support, Resilience Hub.
- **Security and compliance:** Shield, AWS Artifact.
- **AI and end-user:** QuickSight, Rekognition, Polly, Lex, Amplify, WorkSpaces, App Runner, Outposts.
- **Inconclusive** (`AuthorizationHeaderMalformed` with signature validation on): Inspector2, Detective, Bedrock control plane, GuardDuty and Access Analyzer `ListFindings`, Lambda provisioned concurrency. Retest these with `FLOCI_AUTH_VALIDATE_SIGNATURES=false` before ruling them out.

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
| W2-7 | **Edge delivery**: CloudFront distribution with S3 origin and OAC, cache behaviors, Global Accelerator; exam lens on CloudFront vs Global Accelerator | ~6 | 3.4, 4.4 | 3.2 | ⚠️ Control plane |

### Wave 3: governance, multi-account, cost, and CLF-oriented labs

| # | Proposed lab | Steps | SAA | CLF | Floci |
|---|---|---|---|---|---|
| W3-1 | **Account hardening**: password policy, virtual MFA device, credential report, Access Analyzer, permission boundary (enforced) | ~7 | 1.1 | 2.3 | ✅ / ⚠️ |
| W3-2 | **Multi-account guardrails**: Organizations, OUs, member account, SCP; exam lens on SCP evaluation, consolidated billing, and Control Tower | ~7 | 1.1 | 4.2 | ⚠️ SCPs not enforced |
| W3-3 | **Governance services tour**: CloudTrail trail to S3, Config recorder and managed rule, GuardDuty, Security Hub; "which service answers which question" | ~8 | 1.2, 1.3 | 2.2, 2.4 | ⚠️ Control plane |
| W3-4 | **Network scale-out**: VPC peering vs Transit Gateway, plus VPC flow logs | ~8 | 3.4, 4.4 | 3.5 | ⚠️ Control plane |
| W3-5 | **Edge security and throttling**: WAF rate-based rule on the ALB or API, API Gateway REST usage plan, API key, throttling | ~7 | 1.2, 2.2, 4.4 | 2.4 | ⚠️ (check whether WAF and usage-plan limits are enforced) |
| W3-6 | **Cost visibility**: Budgets with an alert, Cost Explorer query, tag strategy, Pricing API lookup, Spot request | ~6 | 4.x | 4.1, 4.2 | ⚠️ Synthetic data |
| W3-7 | **Purchasing options exam lens**: On-Demand, RI, Savings Plans, Spot, Dedicated Hosts and Instances | — | 4.2 | 4.1 | 📖 Concept card |

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
| #15, #31, #34 | Shared-responsibility exam-lens card (who patches what) | CLF 2.1 |

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

## 7. Extending to other certification tracks

The structure above is exam-agnostic. A new track needs three things:

1. An `EXAM_GUIDES` entry, copied from the official guide.
2. `LAB_EXAM_TAGS` rows for existing labs.
3. Concept cards and new labs for the gaps.

Labs are shared across tracks, so a lab tagged for SAA 2.1 can also be tagged for Developer and DevOps tasks. Current AWS lineup per [aws.amazon.com/certification](https://aws.amazon.com/certification/exams/) (2026-10-22). Confirm exam codes there before starting a track.

| Track | Fit with today's labs | Likely new labs | Floci constraints |
|---|---|---|---|
| **Developer – Associate** | Very high. The ~10 labs marked Dev/Ops in §2 (SQS batch and delay, Cognito groups, CORS, IMDS) plus most serverless labs already fit. | Lambda layers and destinations, API Gateway stages and authorizers, DynamoDB conditional writes and transactions, CodeBuild, CodePipeline, and CodeDeploy, SAM and CloudFormation packaging | X-Ray unsupported |
| **CloudOps Engineer – Associate (SOA-C03)**, which replaced SysOps (SOA-C02, retired 2025-09-29) | Medium. CloudWatch, SSM Run Command, the route-repair lab, and CloudFormation fit. | CloudWatch agent and dashboards, alarms to auto-remediation, Backup, ASG ops, Config remediation | SSM Automation, Patch Manager, and Session Manager unsupported; composite alarms unsupported |
| **DevOps Engineer – Professional** | Medium. CloudFormation, ECS, EKS, and EventBridge. | Blue/green and canary deployments (Lambda aliases are verified), CodePipeline, StackSets, drift, event-driven remediation | Pipeline execution depth unverified |
| **Solutions Architect – Professional** (training vendors report SAP-C02's last day as 2026-11-16 with a successor to follow; not confirmed on aws.amazon.com) | Builds on the SAA track. | Multi-account network hub (TGW), cross-account roles at scale, DR across Regions, migration with DMS | Hybrid networking and real multi-Region behavior are mostly concept |
| **Security – Specialty** | IAM, KMS, S3 security, VPC controls. | KMS and key policies, Secrets rotation, GuardDuty, Security Hub, Macie, Config rules, CloudTrail analysis, WAF | **Key-policy and SCP enforcement gaps** limit "prove the deny" labs; detection services are control plane only |
| **Advanced Networking – Specialty** (AWS lists retirement on 2026-12-31) | VPC labs. | Low priority given the retirement | VPN and Direct Connect unsupported |
| **Data Engineer – Associate** | Low today. | Kinesis, Firehose, Glue, Athena, Lake Formation, Redshift, EMR, Step Functions pipelines | Verify Glue and Athena execution depth first |
| **Machine Learning Engineer – Associate**, **AI Practitioner** | Very low. | SageMaker endpoints, Bedrock runtime calls, Comprehend and Textract | Bedrock control plane inconclusive; many AI services stubbed or unsupported. Mostly concept cards. |

**Suggested order after CLF and SAA:** Developer (cheapest, since most labs exist), then CloudOps, then Security, then Data Engineer, then the Professional exams.

---

## 8. Keeping this current

- **When AWS revises a guide,** update `EXAM_GUIDES`. The "no orphan objective" test then flags labs tagged with removed task IDs.
- **When Floci upgrades,** rerun the probe for anything marked control plane, unsupported, or inconclusive in §4. Several topics, especially SCP and KMS key-policy enforcement, S3 replication, and NAT egress, would move labs from "configuration only" to "prove it works."
- **When a lab ships,** add its tags in the same change and update this document's tables until the UI gap view replaces them.
