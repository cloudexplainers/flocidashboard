# Changelog

## Unreleased

- New lab, highly available web tier (SAA 2.2, 2.1, 3.2, 3.4): "Build a highly available web tier with an Application Load Balancer and Auto Scaling", 12 steps, the first Elastic Load Balancing lab (Level 4). It builds a VPC with public subnets in two Availability Zones, a load balancer security group that is the only source the instances accept, a launch template whose user data starts a web server, a health-checked target group, an internet-facing ALB, and an Auto Scaling group (2 to 4 instances, ELB health checks). Each step checks live state: both instances turn healthy targets in different zones, requests sent to the ALB's DNS name from inside an instance are answered by every instance, a scale-out to 3 adds a healthy target, a terminated instance is replaced and the target group follows, and an instance refresh to launch template version 2 leaves only the new release serving. Notes say what Floci doesn't do: the target tracking policy is stored but never runs (the lab scales out by hand), an instance refresh replaces every instance at once, an instance marked Unhealthy isn't replaced, only the latest launch template version is kept, and the listener port is reachable from instances, not from your computer. The catalog is now 68 labs and 397 steps.

- HA web tier controls (see "HA web tier controls" in `docs/console-accuracy-review.md`):
  - **Load balancer console:** new Health checks and Attributes tabs for a target group: edit the health check path, success codes, thresholds, timeout and interval, the deregistration delay, and stickiness (load balancer or application cookie, with duration). Listeners have Edit (port and default action: forward to a target group or a fixed response), and the selected listener's rules are listed in priority order with Add rule, Edit (path, target group, priority) and Delete.
  - **Auto Scaling workbench:** Start instance refresh, Describe instance refreshes and Set instance health.
  - **Says what Floci doesn't apply:** stickiness and the deregistration delay are stored but not applied (requests go round robin with no cookie; deregistered targets are removed at once); a changed health check interval is reported but probing keeps the old interval; an instance refresh replaces every instance at once whatever the minimum healthy percentage; an Unhealthy instance is never replaced. Floci can't cancel or roll back a refresh, so the dashboard doesn't offer it. Settings Floci would store without checking (out-of-range delays or durations, reserved cookie names) are refused with the AWS limits.

- Emulation notes on every console (see "Inventory notes on every console" in `docs/console-accuracy-review.md`):
  - **Shown everywhere:** each service console, custom or generic, now has a collapsed "How Floci emulates this service" section with the service's notes on what Floci simulates and what it accepts without simulating. Before, only the generic workbench showed them.
  - **Corrected notes:** EC2 key pairs hold real keys and security groups are enforced only with `FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED=true`; Pipes targets include EventBridge but not Kinesis; Athena runs queries in DuckDB; EKS has node groups and Fargate profiles; ACM exports certificates requested with export enabled; Secrets Manager rotates on schedule.
  - **Removed:** notes that repeated what a console says inline (DynamoDB PITR, CloudFront caching, Secrets Manager policies, RDS snapshots, restore, replicas and failover, CloudTrail events, Route 53) and notes about the dashboard's own code or protocols.

- CloudTrail log files: a trail's new **Log files** tab lists the gzipped files the trail delivered to its S3 bucket (`<prefix>/AWSLogs/…`), newest first, a day at a time, and shows the records in the one you open (event time, name, source, user, source IP, resources, error code) with each record's raw JSON. Errors such as a missing bucket or AccessDenied are shown, not an empty list. A Floci note says that only S3 data events matching the trail's event selectors are delivered, about every 60 seconds by default, and the empty Event history page links to it. Backed by `GET /api/cloudtrail/trails/<trail>/log-files/` and `.../log-files/read/?key=`.

- RDS and Aurora resilience:
  - **Console:** a database's Actions menu adds Create read replica, Promote and Restore to point in time (latest restorable time or a custom UTC time); a cluster's Actions menu adds Add reader, Failover (to a chosen reader or the one RDS picks) and Take snapshot. The Snapshots tab lists cluster snapshots, which restore to a new cluster or delete after typed confirmation. A database's Configuration tab shows its replication role, source, replicas, replication status and latest restorable time.
  - **Says what Floci doesn't do:** point-in-time restore copies the source as it is now whatever time you choose; a replica gets one copy of its PostgreSQL source and isn't updated afterwards (and accepts writes); Multi-AZ runs no standby; a failover moves only the writer role, because a cluster's instances share one database; cluster snapshots are Aurora PostgreSQL only.
  - **New lab (SAA 2.2, 3.3):** "Design resilient databases with read replicas, point-in-time restore, and Aurora failover", 10 steps. It checks the replica link on both ends, the restored instance, the promotion, the writer and reader roles, an order row written through the Data API that survives the failover, and the cluster snapshot. The catalog is now 67 labs and 385 steps.

- Floci gap fixes (see "Floci gap fixes" in `docs/console-accuracy-review.md`):
  - **Says what Floci doesn't apply:** consoles note where Floci stores a setting without enforcing or running it: SQS, KMS, ECR, Secrets Manager and Backup policies (only S3, SNS and Lambda policies are evaluated), CloudWatch alarm actions, Auto Scaling policies, scheduled actions and lifecycle hooks, DynamoDB point-in-time restore, Route 53 routing policies and health checks, KMS automatic rotation, CloudFront caching, NAT gateways, Config evaluations and more.
  - **S3 anonymous access:** a bucket's Permissions tab tests whether an unsigned request can list it and warns that, with `FLOCI_SERVICES_S3_ENFORCE_AUTH=false` (the Compose default), Block Public Access and bucket policies don't stop anonymous reads or writes.
  - **RDS snapshots:** take a manual DB snapshot of a PostgreSQL database, restore it to a new instance, or delete it, from the new Snapshots tab.
  - **Fixed:** Cloud Map "Discover instances" works (botocore no longer adds the `data-` host prefix); CloudTrail's note now says S3 data events reach the trail bucket and `LookupEvents` returns nothing; Settings shows auth flags it can't see as "Not known" instead of "Permissive".
  - **Removed:** Cloud Map "Update instance health" (Floci doesn't implement it) and 170 inventory notes that described old Floci releases.

- Backend accuracy review (see "Backend API review" in `docs/console-accuracy-review.md`):
  - **Incomplete lists are named:** when Floci doesn't return a list (often an operation it doesn't implement), or the dashboard shows only part of one, every console now shows a "Some lists are incomplete" warning instead of presenting the list as empty.
  - **No invented results:** the EKS kubeconfig and ECR push commands are built from what Floci reports, with the configured Region and `--endpoint-url`; ECR "Inject mock image tag" reports Floci's error instead of a made-up image; CodeDeploy's no-op "Simulate lifecycle event" is removed; IAM's account-root trust policy, assume-role by name and Cognito pool ARNs use the real account.
  - **No false success:** S3 "Empty bucket" deletes every version and delete marker, as the console does, and reports objects it couldn't delete; Settings → Reset Floci reports an unreachable Floci as an error.
  - **Visible defaults:** generic forms show the values used when a field is left blank. Auto Scaling scaling policies no longer send a 60-second cooldown you didn't set.
  - **Fixed lists:** Config remediation configurations and Pricing price lists load; Cognito pages pools, clients, users and groups past 60; the S3 objects view says when a bucket has more than 1,000 objects.
  - **EKS lab:** "Generate kubeconfig" waits for the cluster to be ACTIVE instead of passing with a placeholder.
- Added exam study tracks for AWS Certified Cloud Practitioner (CLF-C02) and Solutions Architect – Associate (SAA-C03):
  - **Track switcher:** the labs page switches between Levels and each exam (`/labs/?track=SAA-C03`).
  - **Exam view:** labs are grouped by domain and task statement, with progress weighted by each domain's share of the exam.
  - **Concept cards:** objectives without a lab (pricing, support plans, data ingestion, and so on) get short study cards, each pointing to the planned lab when there is one.
  - **Badges and filter:** lab cards and headers show exam badges such as `SAA 2.1`, and the filter accepts them.
  - **API:** tags, guides, and cards come from `dashboard/labs/exams.py` and `/api/labs/exams/`.
- Re-leveled the lab curriculum so core Associate-exam topics come early:
  - **Into Level 1:** the inline-policy lab.
  - **Into Level 2:** IAM roles, STS session policies, instance profiles, the EC2 instance-role lab, S3 Block Public Access and bucket policies, and the VPC and security-group/network-ACL labs.
  - **Renamed:** levels 4 and 5 are now "Workflows, Containers & Enforcement" and "Private Networking & Platform". Level subtitles describe skills instead of naming certifications.
  - **Numbering:** lab numbers come from curriculum position, so they changed; the UI derives the lab count instead of hard-coding 66.
- Six labs now do what their titles and descriptions promise, which matters for using them as exam prep:
  - **CloudWatch metric alarms:** the alarm reaches ALARM by evaluating a real error spike instead of `set-alarm-state`, and you watch it settle in OK on healthy data first.
  - **CloudWatch Logs:** the lab now creates the metric filter its title names, plus a 30-day retention policy, and confirms only the HTTP 500 lines became metric data.
  - **Secrets Manager:** adds automatic rotation with a Lambda rotation function, five new steps.
  - **CloudFormation:** previews an update with a change set (add a dead-letter queue, modify the jobs queue in place) before applying it, three new steps.
  - **S3 lifecycle:** the rule now tiers logs to Standard-IA and Glacier before expiring them.
  - **RDS:** the instance is created encrypted with 7-day automated backups. Its create, describe, and modify steps now check real instance state instead of always passing.
  - The catalog is now 375 steps.
- Added `docs/certification-coverage.md`, mapping every lab to the AWS Cloud Practitioner (CLF-C02) and Solutions Architect – Associate (SAA-C03) exam guides, with a gap analysis, Floci 2.3.0 support findings, and a prioritized plan for new labs.
- The dashboard now defaults to host port 8001 (in Compose, `.env.example`, the docs, and the Vite dev proxy) so it doesn't collide with other local Django projects on 8000. Set `DASHBOARD_PORT` to change it.
- Split `dashboard/aws.py` (11.6k lines) into a `dashboard/aws/` package with one inventory module per service; shared client plumbing and `list_resources` stay in `__init__.py`.
- Docker Compose now publishes the dashboard and Floci ports on `127.0.0.1` only (set `HOST_BIND_ADDRESS` to change it). Django runs with `DEBUG` off by default, and the hard-coded secret key is replaced by one generated per install and stored next to the dashboard database.
- Polished the AWS service consoles, including Route 53, Cognito, CloudTrail investigation workflows, session identity switching, and the AWS CLI console.
- Lab progress loads much faster: boto3 clients are cached per endpoint and identity, and lab status checks run in parallel (`/api/labs/progress/` from about 35s to under 1s). The labs page shows loading and retryable error states.
- Fixed Lambda detail pages showing empty versions, aliases, triggers, and function URLs; Lambda labs now wait for asynchronous logs on a cold start; and missing-resource errors return 404 instead of 502.
- Removed the legacy Django-template UI (templates, per-service JavaScript and CSS, and their routes) and unused frontend API client functions. The React console covers every service.
- Split the workflow lab code into one `dashboard/labs/<service>_labs.py` module per service; `monolith.py` now holds only shared helpers and dispatch (22,192 to 724 lines). No lab behavior changes.
- Fixed an ambiguous Playwright locator in the KMS crypto tools test that failed CI.
- Rewrote `CONTRIBUTING.md` for the current React and per-service lab architecture.

## 2.0.1 — 2026-10-19

- Added a complete live lab audit command and disposable Floci configuration, plus Playwright coverage for every curriculum lab and real IAM/S3 browser workflows.
- Fixed guided EC2 lab networking and guest readiness, Fargate awsvpc configuration, RDS startup timeouts, EventBridge publishing permissions, and Step Functions parallel output handling and completion verification.
- Lab Studio now stops automated runs on failed/unverified steps, shows reset/status failures, ignores stale responses after navigation, and replaces stale browser completion with current status.
- Validated the existing 65-service catalog against Floci 2.3.0; newer Floci services outside this catalog do not yet have dedicated dashboard pages.
- Updated Python to 3.14, Django to 6.1.2, boto3 to 1.43.115, and AWS CLI to 1.46.1, with a resolved Python dependency lock file.
- Updated React to 19.3.1, TypeScript to 7.0.2, Vite to 8.3.4, Lucide to 1.59.0, and Cloudscape packages and frontend tooling to their latest stable releases.
- Fixed React ref callback typing, Vite CSS module declarations, SES v2 pagination without SDK paginator definitions, and optional unsupported Auto Scaling operations.
- Replaced the unavailable curl health probe with Floci's built-in health-check script.
- Docker builds now compile frontend assets in a Node.js 26 stage and exclude host node_modules from the build context.
- Added live inventory and S3, SQS, DynamoDB, SSM, IAM/STS compatibility tests, pagination/error regressions, and CI for backend tests, frontend builds, and live Floci checks.
- Validation: 1,173 unit tests, six live compatibility tests, all 66 labs (365 steps), and 77 Playwright tests passed. The complete Playwright suite also passed in headed Chromium.

## 2.0.0 — 2026-09-01

### Official AWS Cloudscape Design System & UI Architecture
- Rebuilt frontend with **React 18**, **TypeScript**, **Vite**, and official **AWS `@cloudscape-design/components`** design system for 100% AWS console visual and architectural fidelity.
- Added persistent **AWS Favorites Bar** under the global TopNav with official AWS SVG service icons, deep service linking, and quick switching.
- Implemented authentic, contextual service-specific SideNav navigation across all 20 consoles with synchronized deep-tab routing.
- Standardized typography on **Open Sans** across the TopNav, Favorites Bar, SideNav, and HTML shell.

### Dedicated Consoles & Deepened Capabilities
- **Top 15 AWS Consoles**: Bespoke consoles for **Amazon S3**, **Amazon EC2**, **AWS IAM**, **Amazon DynamoDB**, **AWS Lambda**, **Amazon SQS**, **Amazon SNS**, **Amazon RDS**, **AWS KMS**, **AWS Secrets Manager**, **AWS CloudFormation**, **Amazon Route 53**, **Amazon EventBridge**, **Amazon CloudWatch**, and **AWS Step Functions**.
- **Tier 1-4 Enhancements**:
  - **S3 & IAM**: Presigned URLs, static website hosting, event notifications, IAM role trust policies, and IAM Policy Simulator.
  - **DynamoDB & Lambda**: Item CRUD, PartiQL SQL editor, Lambda Function URLs, and Event Source Mappings.
  - **Cognito, API Gateway, SSM**: Cognito User Pools & Auth Sandbox, API Gateway HTTP routes & live test runner, SSM Parameter Store hierarchical tree & decryption.
  - **EC2 Networking**: VPCs, subnets, route tables, security group rule managers, and interactive SVG topology graph.
- **Developer Inspection Tools**: Local SES email mailbox, non-destructive SQS message peeker (`VisibilityTimeout=0`), and live CloudWatch logs stream tailer.
- **Universal Dynamic Action Runner**: Schema-driven workbench powering all **65 supported AWS services** with zero legacy fallbacks.

### Curriculum & Workflow Labs
- Expanded catalog to **66 guided labs across 20 services (365 total steps)** including new interactive lifecycles for RDS, ECS, and EKS.
- Multi-SDK code generation for every lab step: toggle instantly between **AWS CLI v2**, **Python `boto3`**, and **Terraform `HCL`** with persistent user preference.
- Added **"Reset All"** lab teardown button on `/labs` alongside the automated **"Run All Steps" Play Through** workflow runner.

### Quality & Verification
- Expanded automated Django test suite to **1,163 passing tests** with 100% coverage across action registries, inventory enrichment, lab lifecycle execution, multi-SDK translation, AST validation, and service rendering.
- Clean TypeScript compilation (`tsc`) and optimized Vite production bundle build.


### Curriculum & Workflow Labs
- Expanded catalog to **63 labs across 17 services (349 total steps)**, adding Step Functions (Choice & Parallel workflows), Cognito (User Pools, App Clients, auth), and CloudWatch (metrics, alarms, logs).
- Built multi-SDK code generation for every lab step: instantly toggle between **AWS CLI**, **Python `boto3`**, and **Terraform HCL** with persistent `localStorage` selection.
- Added **"Run All Steps" Play Through** automation with real-time progress indicators, paced auto-scrolling, and contextual step explanations.
- Deepened all 7 guided EC2 scenarios into multi-step interactive workflows (IMDS guest socket retrieval, UserData, IAM instance roles, web server SG routing, broken route fix, private S3 gateway endpoints, and SSM RunCommand dispatch).
- Re-ordered curriculum progression to follow best-practice cloud architecture paths (IAM $\rightarrow$ S3 $\rightarrow$ KMS $\rightarrow$ SSM $\rightarrow$ Secrets Manager $\rightarrow$ Cognito $\rightarrow$ SQS $\rightarrow$ SNS $\rightarrow$ Scheduler $\rightarrow$ DynamoDB $\rightarrow$ Lambda $\rightarrow$ API Gateway $\rightarrow$ EventBridge $\rightarrow$ Step Functions $\rightarrow$ CloudWatch $\rightarrow$ EC2 $\rightarrow$ CloudFormation).

### Cloudscape Design System & Dashboard Hygiene
- Consolidated CSS tokens and styles with the **AWS Cloudscape Design System** (Squid ink `#16191f`, crisp container radii, 2–4 column key-value attribute grids).
- Universal semantic status indicators (🟢 Positive, 🟡 Warning, 🔴 Negative, 🔵 Info, ⚪ Inactive) auto-rendered across all table status columns and inventory cards.
- 100% pedagogical guide coverage: structured `About Floci <Service>` accordions added across all 65 registered services.
- Promoted 8 services to interactive workbenches:
  - **DocumentDB**: MongoDB-compatible URI generation and instance inspection.
  - **MemoryDB**: Redis CLI connection commands, shard status, and ACL user tabs.
  - **Amazon MQ**: Multi-protocol endpoint strips (AMQP, MQTT, STOMP, RabbitMQ Web Console).
  - **WAF v2**: WebACL rule list with actions and interactive WAF Request Evaluator Simulator.
  - **IoT Core**: Registered thing cards, topic rules, and interactive MQTT Test Client simulator.
  - **S3 Vectors**: Vector embedding dimension metrics and interactive Similarity Search simulator.
  - **Cost Explorer**: KPI summary tiles, daily stacked cost/usage bar chart, and anomaly watchers.
  - **AWS Pricing Calculator**: Interactive monthly cost modeler for EC2, S3, Lambda, and RDS.

### Quality & Tests
- Expanded test suite to **1,080 passing tests** covering multi-SDK code translation, lab runners, AST integrity, and service rendering.

## 0.1.3 — 2026-07-15

### Floci 1.5.32 compatibility

- Added optional S3 ACL and bucket-policy authorization configuration to Docker Compose and exposed its resolved mode on Environment Details.
- Updated S3 encryption handling for Floci's AWS-compatible default SSE-S3 response and refreshed S3 support guidance.
- Surfaced Lambda event-source failure destinations and downloadable function package locations.
- Added Cognito global sign-out and refresh-token revocation workflows.
- Added SES v2 contact create, update, and delete workflows and filtered KMS alias lookup by key.
- Documented Step Functions JSONata workflow variables and `Assign` support while retaining explicit IAM usernames in identity-management calls.

### Console polish

- Promoted EventBridge to a first-class resource console with searchable Event buses, Rules, and Targets views; URL-addressable resource selection; destination deep links; a dedicated event sender; and direct access to the application-spine lab.
- Promoted EventBridge Scheduler to a first-class resource console with searchable schedule-group and schedule views, URL-addressable detail selection, complete timing fields, target workbench links, group tag management, and direct access to the SQS delivery lab.
- Simplified S3 and IAM workbenches by removing duplicated read-only detail panels and making summary cards navigate into the interactive inventory.
- Removed the redundant upper-left service filter, renamed command search to Search, and removed duplicated supported-operation/read-only panels from first-class workbenches.
- Expanded Lambda into a first-class function console with lifecycle, configuration, code, versions, aliases, triggers, URLs, concurrency, permissions, tags, and relationship views.
- Expanded IAM with richer user, role, policy, access-key, login-profile, instance-profile, tag, and permission-management workflows.
- Expanded ECS with resource tabs, service/task relationships, task-definition revision lifecycle, deployments, infrastructure, task protection, container-instance draining, and Floci-supported service updates.
- Expanded EKS with searchable cluster, managed node group, Fargate profile, IAM relationship, and k3s connection views while removing unsupported API probes.
- Expanded ECR with searchable repository/image views, manifest inspection, stored policy management, local Docker commands, and registry garbage collection.
- Added EKS Fargate profile support introduced by the latest compatible Floci release.
- Consolidated AWS CLI parsed and raw output, added compact summary grids for large inventories, and expanded regression coverage.

### Compatibility and quality

- Audited ECS, EKS, and ECR controls against Floci's implemented handlers so the dashboard does not advertise ignored or unsupported mutations.
- Parallelized independent IAM, Lambda, ECS, EKS, and ECR inventory enrichment to reduce large-console load times.
- Added focused compatibility coverage for the latest Floci health/version response and expanded the dashboard suite to 1,000 tests.

## 0.1.2 — 2026-07-10

### EC2

- Added a first-class instance experience with searchable inventory, detail views, an improved launch workflow, state-aware lifecycle actions, polling, tag editing, and SSM command execution.
- Added a network control plane for VPCs, subnets, security groups, internet gateways, routes, Elastic IPs, NAT gateways, VPC endpoints, relationship topology, and connectivity diagnostics.
- Added advanced EC2 management for network ACLs, VPC flow logs, EBS volumes, snapshots, AMI registration, launch template versions, and Spot requests.
- Added seven guided local workflows covering IMDS, UserData, IAM instance roles, published web servers, route diagnosis, private S3 connectivity, and SSM commands.

### Quality

- Added focused API, action-registry, inventory, lab-runner, browser, and live Floci verification coverage.
- Preserved read-only inventory beneath the interactive workbench and documented local emulator compatibility boundaries in the UI.
