# Changelog

## Unreleased

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
