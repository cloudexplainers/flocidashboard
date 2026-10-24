# Floci Dashboard Roadmap

This roadmap is a living idea collector and planning aid, not a set-in-stone promise. Items may be added, removed, reordered, or reshaped as the dashboard, Floci, and contributor needs evolve.

The goal is to keep this public and useful: it should show the direction of the project, capture good ideas before they disappear, and help contributors choose work that fits the current architecture. The near-term sections should stay realistic for this Django + boto3 dashboard talking to a local Floci endpoint.

## Product Direction

Floci Dashboard should become a focused local cloud workbench. It should help developers answer a few practical questions faster than the AWS CLI alone:

1. What did my app create?
2. What can I inspect or test right now?
3. Why did my local AWS workflow fail?

The dashboard should not clone the full AWS Console. The best version is a compact developer UI for inspecting, testing, resetting, and learning local AWS workflows.

## Current Architecture Direction

Recent service workbenches established the shared pattern for future service pages:

- Service pages are layered workbenches, not replacements for inventory pages.
- Read-only inventory remains the base layer.
- Interactive tools are added above the existing inventory.
- Summary cards should continue to link into read-only sections where possible.
- Success and error feedback for UI actions should use shared lower-right toasts.
- Service-specific files should stay focused on service-specific behavior.

Core architecture files:

- `Dockerfile`, `docker-compose.yml`, `.dockerignore`, and `.env.example`: Docker-first local runtime. The default developer path launches Floci and Django together, points the dashboard container at `http://floci:4566`, and keeps exposed host ports configurable.
- `dashboard/services.py`: canonical service registry, maturity labels, and action metadata.
- `dashboard/actions.py`: shared action metadata, JSON parsing, and normalized action errors.
- `frontend/src/`: React + Cloudscape console. `App.tsx` routes services to pages in `pages/` (with `GenericServiceWorkbench` as the fallback), and `api/client.ts` wraps the JSON endpoints. Vite builds into `dashboard/static/dashboard/dist/`, served by `dashboard/templates/dashboard/console_spa.html`.
- `dashboard/console_views.py`: AWS-only CLI Console API for local Floci commands, with endpoint injection, JSON parsing, and destructive-command confirmation.
- Service-specific backend modules such as `s3_api.py` and `s3_views.py`, `iam_api.py` and `iam_views.py`, with matching React pages such as `frontend/src/pages/S3Console.tsx`.

## Guiding Principles

- Prefer useful local workflows over raw inventory alone.
- Keep interactive workbenches additive.
- Keep backend behavior close to boto3 and Floci's public AWS-compatible APIs.
- Treat Docker Compose as the primary local runtime; host Python remains a contributor convenience, not the default user path.
- Add shared architecture only after the need is visible in at least one real service page.
- Make errors clear and actionable.
- Use destructive confirmations for delete, purge, reset, empty, and cleanup actions.
- Place actions where their scope is clear: resource-scoped buttons belong with the selected resource, row, detail card, or drawer they affect; global actions belong in the page or workbench toolbar; bulk actions belong near collection selection controls.
- Keep public roadmap items feasible enough for contributors to pick up.
- Keep tutorial-style notes close to the UI where they help users understand what to test.

## Completed Recently

- **Codebase cleanup**: removed the legacy Django-template UI (templates, per-service JS and CSS, and their routes) now that the React console covers every service, plus unused frontend API client functions.
- **Lab code split**: moved each service's lab definitions, runners, verifiers, status, and reset out of `dashboard/labs/monolith.py` into `dashboard/labs/<service>_labs.py`; the monolith keeps only shared helpers and dispatch.
- **Faster lab progress**: cached boto3 clients per endpoint and identity, and parallel lab status checks (`/api/labs/progress/` from about 35s to under 1s).
- **Multi-SDK Code Tabs & Preference Sync**: Built dynamic code generation across AWS CLI, Python `boto3`, and Terraform HCL for all 63 guided labs with persistent user preference storage.
- **Automated "Run All Steps" Play-Through**: Added an automated step runner with visual progress tracking, smooth auto-scrolling, speed regulation, and real-time step explanations.
- **Curriculum Expansion (63 Labs, 349 Steps across 17 Services)**: Added Step Functions (Choice & Parallel workflows), Cognito (User Pools, App Clients, auth), CloudWatch (metrics, alarms, logs), and deepened all 7 EC2 guided compute scenarios into multi-step interactive workflows.
- **Cloudscape Design System & Hygiene Sweep**: Standardized styling with AWS Cloudscape design tokens, semantic status indicators (🟢/🟡/🔴/🔵/⚪), 2–4 column key-value attribute grids, and 100% educational guide coverage across all 65 services.
- **Promoted 8 Interactive Workbenches**: Elevated DocumentDB (MongoDB connection copier), MemoryDB (Redis CLI & ACL tabs), Amazon MQ (multi-protocol endpoints), WAF v2 (WAF Request Evaluator Simulator), IoT Core (MQTT Ingest Test Client), S3 Vectors (Similarity Search Simulator), Cost Explorer (Daily Stacked Bar Chart & Anomaly Watchers), and AWS Price List (AWS Pricing Calculator Simulator).
- Promoted EventBridge to a first-class resource console with searchable bus, rule, and target inventories, URL-addressable detail selection, scoped lifecycle actions, destination workbench links, event replay, and a direct application-spine lab entry point.
- Promoted EventBridge Scheduler to a first-class resource console with searchable group and schedule inventories, URL-addressable details, full timing-window configuration, target deep links, scoped lifecycle and tag actions, and a direct SQS delivery lab entry point.
- Added an EventBridge application-spine capstone that builds API Gateway to Lambda to a custom event bus, fans out through transformed processing and full-envelope audit queues plus a notification Lambda, correlates CloudWatch logs, exercises malformed input, missing permissions, and disabled rules, and resets every owned dependency safely.
- Added the first shared resource-relationship graph as a live EventBridge capstone visualization, using accessible dependency-free SVG, deterministic serverless columns, evidence-backed edges, workbench deep links, and explicit healthy, disabled, broken, unverified, and unsupported states.

- Rebuilt the EC2 Phase 1 experience around an Overview, Instances, and Run Command navigation shell with a searchable instance table, URL-addressable detail selection, state-aware single and bulk lifecycle actions, transition polling, a richer launch wizard, launch-time and editable tags, and AWS-native SSM command execution with invocation history and output polling.
- Added the EC2 Phase 2 network control plane with VPC and subnet lifecycle management, security group creation and ingress/egress rule editing, internet gateway attachment, route table and route workflows, Elastic IPs, NAT gateways, VPC endpoints, relationship topology, and live connectivity diagnostics.
- Added the EC2 Phase 3 advanced workbench with a network ACL editor, S3-backed VPC flow-log configuration and decoded record viewing, volume lifecycle plus snapshot inspection, AMI registration, launch template version/default management, and Spot request lifecycle controls.
- Added the EC2 Phase 4 guided local workflows for guest IMDS inspection, UserData verification, IAM instance roles, security-group-published web servers, broken-route diagnosis, private S3 gateway endpoints, and SSM command execution, all with progress tracking and cleanup.

- Merged the S3 object browser contribution and preserved the original read-only S3 inventory underneath it.
- Restored S3 summary cards, anchor-link behavior, and read-only cards after adding the new interactive browser.
- Added shared `service-console.js` primitives for summary cards, detail cards, modals, toolbars, API calls, formatting, and toasts.
- Added lower-right toast messaging for interactive action feedback.
- Added `dashboard/services.py` as the service registry.
- Added `/api/services/` to expose service metadata.
- Added `dashboard/actions.py` for shared action metadata and JSON/error helpers.
- Registered S3 action metadata.
- Built the SQS Queue Workbench with create, delete, purge, send, receive, and delete-message actions.
- Registered SQS as an interactive workbench.
- Built the SNS Publish Workbench with topic/subscription browsing and publish-message actions.
- Registered SNS as an interactive message workbench.
- Built the Lambda Invoke Workbench with function selection, JSON payload invocation, response payloads, error details, log tails, and CloudWatch log group hints.
- Registered Lambda as an interactive workbench.
- Built the DynamoDB Table Explorer with table selection, schema/index details, bounded scans, item JSON detail, and read-only PartiQL SELECT.
- Registered DynamoDB as an interactive workbench.
- Built the CloudWatch Logs Viewer with log group selection, stream browsing, recent event polling, auto-refresh, and Lambda log group deep links.
- Registered CloudWatch as an interactive workbench.
- Built the Step Functions Execution Workbench with state machine selection, JSON execution starts, execution details, recent history timelines, and stop-running-execution actions.
- Registered Step Functions as an interactive workflow workbench.
- Built the IAM Identity Workbench with principal exploration, policy document viewing, statement summaries, access key management, assume-role testing, managed policy attachment, inline policy editing, group membership, role trust editing, managed policy versioning, role/group/instance-profile creation, cleanup helpers, policy simulation, and non-destructive session identity switching.
- Registered IAM as an interactive identity workbench.
- Built the EventBridge Event Sender with event bus selection, rule and target context, JSON event detail submission, PutEvents result rendering, and EventBridge tutorial notes.
- Registered EventBridge as an interactive event workbench.
- Built the EC2 Instance Workbench with instance selection, launch and key import modals, start/stop/reboot/terminate actions, CLI snippets, IMDS hints, richer instance inventory, and EC2 tutorial notes.
- Registered EC2 as an interactive compute workbench.
- Built the API Gateway Request Workbench with REST/HTTP API selection, stage and route hints, local request execution, and response rendering.
- Registered API Gateway as an interactive request workbench.
- Built the Kinesis Stream Workbench with stream creation, stream and shard selection, PutRecord testing, GetRecords reads, and decoded record rendering.
- Registered Kinesis as an interactive stream workbench.
- Built the Secrets Manager Workbench with secret creation, explicit value reveal, value updates, scheduled deletion, and masked read-only inventory underneath.
- Registered Secrets Manager as an interactive secret workbench.
- Built the SSM Parameter Store Workbench with parameter creation, explicit value reveal, value updates, delete confirmations, and broad SSM read-only inventory underneath.
- Registered SSM as an interactive parameter workbench.
- Built the CloudFormation Stack Workbench with template validation, stack create/update/delete actions, event and resource inspection, change set helpers, and CloudFormation tutorial notes.
- Registered CloudFormation as an interactive stack workbench.
- Built the Cognito Auth Workbench with user pool, app client, resource server, user, group, password, user auth, and OAuth token helpers.
- Registered Cognito as an interactive auth workbench.
- Built the RDS Database Workbench with DB instance creation, modification, reboot, deletion, cluster creation, parameter group creation, connection snippets, engine notes, and proxy configuration context.
- Registered RDS as an interactive database workbench.
- Built the Auto Scaling Capacity Workbench with launch configuration creation, group creation/update/delete, desired-capacity scaling, instance attach/detach/termination, target group attachment, lifecycle hooks, policy helpers, and activity inspection.
- Registered Auto Scaling as an interactive capacity workbench.
- Built the ELB v2 Load Balancing Workbench with load balancer and target group creation, target registration, listener and rule helpers, target health inspection, tag updates, and Phase 1 behavior notes.
- Registered ELB v2 as an interactive load-balancing workbench.
- Built the CloudFront Management Workbench with distribution creation and updates, invalidation helpers, cache policy and OAI creation, function creation and publishing, tagging, and management-plane behavior notes.
- Registered CloudFront as an interactive CDN workbench.
- Built the Route 53 DNS Workbench with hosted zone creation and deletion, record-set change helpers, health check creation and updates, tagging, and management-plane behavior notes.
- Registered Route 53 as an interactive DNS workbench.
- Built the KMS Key Workbench with key and alias creation, encrypt/decrypt helpers, data key generation, rotation toggles, deletion scheduling, and KMS behavior notes.
- Registered KMS as an interactive key workbench.
- Built the EventBridge Scheduler Workbench with schedule group creation, schedule create/update/delete helpers, state toggles, target JSON editing, and scheduler invocation notes.
- Registered EventBridge Scheduler as an interactive schedule workbench.
- Built the EventBridge Pipes Workbench with pipe creation, source/target and parameter editing, start/stop actions, deletion, tagging endpoints, and pipe routing notes.
- Registered EventBridge Pipes as an interactive pipe workbench.
- Built the AWS Config Workbench with config rule, recorder, delivery channel, conformance pack, evaluation, recorder start/stop, delete, and tagging helpers.
- Registered AWS Config as an interactive compliance workbench.
- Built the ACM Certificate Workbench with certificate request, PEM retrieval, private certificate export, renewal, deletion, tagging, and account expiry configuration helpers.
- Registered ACM as an interactive certificate workbench.
- Built the ECS Container Workbench with cluster creation/deletion, task definition registration, run/stop task helpers, service create/update/delete flows, tagging, account settings, and Docker/mock-mode notes.
- Registered ECS as an interactive container workbench.
- Built the ECR Image Registry Workbench with repository creation/deletion, Docker login token helpers, image deletion, tag mutability updates, lifecycle/repository policy round trips, tagging, and registry garbage collection.
- Registered ECR as an interactive image registry workbench.
- Built the EKS Cluster Workbench with cluster creation/deletion, tag helpers, list-tags support, endpoint context, and mock/real-mode notes.
- Registered EKS as an interactive Kubernetes workbench.
- Built the ElastiCache Workbench with replication group lifecycle, user creation and updates, IAM auth token validation, connection snippets, and proxy-port context.
- Registered ElastiCache as an interactive cache workbench.
- Built the OpenSearch Domain Workbench with domain lifecycle, config updates, upgrade helpers, tagging, version compatibility, and instance type limit inspection.
- Registered OpenSearch as an interactive search workbench.
- Built the Athena Query Workbench with workgroup creation, query start/stop, execution details, result previews, and S3 output context.
- Registered Athena as an interactive SQL workbench.
- Built the AWS Backup plan and job workbench with plan, selection, vault, and recovery point management.
- Registered AWS Backup as an interactive backup workbench.
- Built the Amazon Data Firehose Delivery Stream workbench with stream creation, record ingestion, and S3 delivery buffering details.
- Registered Firehose as an interactive stream delivery workbench.
- Built the AWS Glue catalog and registry workbench with databases, tables, partitions, registries, schemas, and versioning flows.
- Registered Glue as an interactive catalog and schema workbench.
- Built the MSK Kafka bootstrap broker workbench with cluster lifecycle, broker lookup, and client connectivity configurations.
- Registered MSK Kafka as an interactive broker workbench.
- Built the Neptune Graph workbench with cluster and instance creation, Gremlin WebSocket credentials, and connection information.
- Registered Neptune as an interactive graph workbench.
- Built the AWS SES Email and Template workbench with domain/email verification, email drafting, templates, and clearing the local mailbox.
- Registered SES as an interactive email workbench.
- Built the AWS Transfer Family console with server creation, user setup, SSH public key importing, and management APIs.
- Registered Transfer Family as an interactive server workbench.
- Built the Textract stub workbench with synchronous document text and analysis requests plus immediate-success async job workflows.
- Registered Textract as an interactive document AI workbench.
- Built the Transcribe stub workbench with transcription job and custom vocabulary lifecycle helpers.
- Registered Transcribe as an interactive speech workbench.
- Built the CodeDeploy workbench with application, deployment group, deployment, deployment config, lifecycle hook, and tagging workflows.
- Registered CodeDeploy as an interactive deployment workbench.
- Built the CodeBuild workbench with project, build execution, report group, source credential, and curated image workflows.
- Registered CodeBuild as an interactive build workbench.
- Built the Bedrock Runtime workbench with Converse and InvokeModel request helpers, model ID examples, and decoded stub responses.
- Registered Bedrock Runtime as an interactive model runtime workbench.
- Built the AppConfig workbench with application hierarchy, hosted versions, deployment strategies, deployments, configuration sessions, and AppConfigData retrieval.
- Registered AppConfig as an interactive configuration workbench.
- Built the Resource Groups Tagging explorer with bulk tag and untag actions, filtered resource discovery, and tag-value lookup.
- Registered Resource Groups Tagging as an interactive tag explorer.
- Added a home-page service selector that defaults to the top 24 common AWS services, persists user selections, labels cards as Interactive or Read Only, and limits `/api/resources/` calls to selected services for faster loads.
- Replaced the dashboard README screenshot image.
- Added tutorial-style "About Floci" notes across interactive service pages, including Textract, Transcribe, CodeDeploy, CodeBuild, Bedrock Runtime, AppConfig, and Resource Groups Tagging.
- Added contributor architecture notes and an AI-assisted contributor prompt to `README.md`.
- Added Floci 1.5.18 inventory coverage and homepage cards for CloudFront and AWS Config, then promoted CloudFront to an interactive management workbench.
- Refreshed release-aware notes for Neptune Gremlin backend support, SNS HTTP/HTTPS delivery, SQS message move tasks and FIFO dedup scoping, Lambda port-pool behavior, API Gateway v2 ALB routing, KMS MAC operations, ElastiCache Memcached clusters, and ECS-to-ELBv2 target registration.
- Reviewed Floci 1.5.19 and refreshed Lambda layer, CloudFormation SQS queue, SNS-to-SQS subscription, and Cognito parity, API Gateway authorizer, ELBv2 action-family, S3 request-payment, SNS batch/binary-attribute, SQS queue-url, Cognito token-claim, and ECR Public Gallery dashboard notes.
- Reviewed Floci 1.5.21 and refreshed Cognito, EC2, SES v2, Firehose, KMS, CloudFormation, API Gateway, EventBridge, S3, DynamoDB, and RDS dashboard coverage notes, including SES v2 configuration set event destination inventory.
- Reviewed Floci 1.5.22, added an AppSync Phase 1 management workbench, exposed KMS GenerateRandom plus ECS RunTask overrides and create-time tags, added an ECS + ALB CloudFormation starter, and refreshed affected service notes.
- Made homepage service cards use registry page paths when available and dedupe repeated card description text from service aliases.
- Reviewed Floci 1.5.23 plus the two post-release main commits, refreshed AppSync, Glue, ECS, EKS, SES, CloudWatch Logs, DynamoDB, EC2, Step Functions, and SNS release-aware notes, and added AWS Cloud Map inventory coverage for the new servicediscovery management API.
- Reviewed Floci 1.5.24 and added CloudTrail inventory coverage for local audit trail lifecycle visibility.
- Promoted AWS Cloud Map to an interactive service discovery workbench for namespace, service, instance, discovery, custom health, and tag workflows.
- Expanded the EKS workbench with managed node group create and delete workflows on top of existing node group inventory.
- Refreshed Floci 1.5.24 coverage notes across Cognito, Glue, KMS, S3, SES, Athena, EC2, ELB v2, and ElastiCache, and added Glue UpdateDatabase plus BatchDeleteTable workflows.
- Reviewed Floci 1.5.25 and refreshed existing-service notes for RDS provisioning, Glue partitions/statistics, CloudFormation custom resources and VPC/subnet provisioning, STS secret persistence, SQS message sizing and delayed-message counts, S3 conformance, SES v2, ELBv2, ECS, Cognito, Athena, AppConfig, MSK, and EC2.
- Added Floci 1.5.25 inventory coverage and service pages for the four new services: EMR, WAF v2, AWS Batch, and RDS Data API.
- Added the Service Matrix page powered by `dashboard/services.py`, with homepage-order service rows, linked service names, API paths, maturity, shared console status, action counts, tags, and a horizontal coverage summary.
- Refined homepage service filtering by removing the unused search box, keeping the selector compact, closing it on outside clicks, preserving selected-service loading, and making Refresh the explicit cache-bypass path.
- Added short-cache headers for `/api/services/`, static JS cache-busting for dashboard and service console assets, and tests for selected-service resource loading.
- Completed a service page consistency pass for generic summary links, zero-value rendering, targeted summary-card anchors, and clearer inventory empty states.
- Hardened the shared console shell with `confirmAction`, registry-shaped `renderActionButtons`, destructive confirmation reuse, and pilot migrations for Transcribe and AppConfig action rows.
- Added JS syntax checks across dashboard static JavaScript files through `dashboard.tests.StaticJavaScriptTests`.
- Added a contributor new-service checklist to `README.md` covering registry entries, inventory/API wiring, service pages, optional static assets, resource loader registration, tests, JS checks, and roadmap notes.
- Added `ActionRegistryAuditTests` to compare `dashboard/services.py` action metadata against Django route coverage, endpoint test references, and destructive confirmation metadata.
- Moved action metadata loading into `service-console.js` with `ServiceConsole.loadServiceActions(serviceKey, fallbacks)`, then simplified the AppConfig and Transcribe console pilots.
- Added the Environment Details page at `/environment/` for Floci health, AWS endpoint, region, credential source/profile, caller identity, and local-endpoint warnings.
- Closed the action audit baseline by adding endpoint tests across S3, Cloud Map, IAM, SQS, AppSync, Auto Scaling, CloudFront, Cognito, EC2, ELB v2, OpenSearch, and Route 53, reducing `ACTION_TEST_REFERENCE_GAP_BASELINE` from 39 historical gaps to zero.
- Migrated Cloud Map action rows to `ServiceConsole.loadServiceActions(...)`, making it the third registry-driven console after AppConfig and Transcribe.
- Expanded the IAM workbench with managed policy version create/default/delete flows, user/group membership editing, and role trust policy editing.
- Added a Tracked Resources homepage filter that discovers resources across all registered services and hides empty service cards.
- Reviewed Floci 1.5.30, added read-only Amazon MQ inventory coverage, expanded EC2 flow-log and CloudTrail event visibility, surfaced CloudFormation StackSets, SES v2 contact lists, Firehose extended S3 destination detail plus UpdateDestination, Step Functions versions, and updated IAM/STS AssumeRole session-policy support.
- Reviewed Floci 1.5.29 and refreshed release-aware notes for AppSync Phase 4 VTL execution, IAM cross-account assumed-role routing and AmazonRDSEnhancedMonitoringRole, floci-core reset/nuke endpoints, ECS hostPort/native-image/CloudFormation cleanup behavior, Transcribe vocabulary persistence, shutdown persistence flushing, local ALB DNS names, Step Functions aws-sdk integrations, Cognito client overrides, SES v2 optional FromEmailAddress, SSM diagnostics, and IoT compatibility polish.
- Reviewed Floci 1.5.28, added read-only inventory coverage for IoT Core and Elastic Beanstalk, and refreshed release-aware notes for AppSync Phase 3, Kafka-backed Pipes, Steampipe-oriented read APIs, ECS EFS volumes, MemoryDB ACL auth, CodeDeploy persistence, Cognito, Lambda/SQS ESM, RDS, EC2, IAM, S3, SES, Secrets Manager, CloudFormation, CloudFront, CodeBuild, DynamoDB, Step Functions, Athena, ELBv2, EventBridge, and MSK.
- Reviewed Floci 1.5.27, added read-only inventory coverage for MemoryDB, CodePipeline, and S3 Vectors, folded EC2 Network ACLs into EC2 inventory, and refreshed release-aware notes for DynamoDB, CloudFormation SAM support, Neptune openCypher, persistence, Secrets Manager rotation, SES, ACM, SSM, and Auto Scaling.
- Reviewed Floci 1.5.26 and refreshed release-aware notes for DocumentDB, CloudFormation provisioning, SSM SendCommand and patch baselines, Auto Scaling reconciliation and mixed instances, EC2 Spot/DNS behavior, KMS EnableKey, and Cognito client/password recovery alignment.
- Added Amazon DocumentDB cluster and instance inventory, resource counting, homepage coverage, and a dedicated read-only service page.
- Expanded EC2 inventory with VPC endpoints and SSM inventory with per-operating-system default patch baselines.
- Added KMS key enable/disable actions and surfaced S3 user metadata in the object detail drawer.
- Added local AWS workflow labs with shared routes, UI, live-state verification, reset behavior, and breadcrumb navigation.
- Added a top-level Labs directory that automatically lists services with registered labs and links it from the homepage between Environment and Service Matrix.
- Added completion-only next-batch recommendations so the final lab in each service batch points learners toward the next practical batch without turning the labs directory into a separate path system.
- Completed eleven IAM labs covering local admin identity bootstrap, users, managed and inline policies, access keys, groups, roles, STS session policies, EC2 instance profiles, switched dashboard identities, and local IAM enforcement checks.
- Completed twelve S3 labs covering bucket/object fundamentals, prefixes, metadata and tags, version recovery, presigned URLs, bucket security, default encryption, lifecycle retention, CORS, S3-to-SQS notifications, and multipart upload.
- Built the SQS lab foundation with queue creation, URL resolution, full attribute inspection, account queue listing, live-state completion, and queue-owned reset.
- Added the SQS message lifecycle lab with a known JSON event, message attributes, reload-safe receive verification, live receipt-handle discovery, delete verification, and message-only reset behavior.
- Added the SQS visibility timeout lab with in-flight receipt tracking, ChangeMessageVisibility, immediate hidden-state verification, timed message reappearance, and message-only cleanup.
- Added the delayed-message SQS lab with DelaySeconds, delayed-count inspection, atomic unavailable-state verification, timed delivery, and attributed message cleanup.
- Added the SQS batch-message lab with three-entry SendMessageBatch, multi-message receive verification, live receipt-handle discovery, DeleteMessageBatch, and batch-only reset behavior.
- Added the SQS queue-configuration lab with visibility, retention, long-poll attributes, operational tags, exact live-state verification, and default-restoring reset behavior.
- Added the SQS dead-letter queue and redrive lab with dedicated source/DLQ topology, `RedrivePolicy`, repeated failed receives, explicit max-receive transition, DLQ inspection, managed message move task verification, and queue-owned reset.
- Added the SQS FIFO ordering and deduplication lab with explicit FIFO attributes, one ordered message group, duplicate-send metadata comparison, queue-depth verification, sequence-number inspection, and queue-owned reset.
- Completed the foundational SQS sequence with a purge/delete lab that verifies message removal, preserved queue configuration and tags, final queue absence, and idempotent reset behavior.
- Added the first SNS lab and first dedicated multi-service messaging lab: one topic, two least-privilege SQS queue policies, two raw-delivery subscriptions, one publish, two independently verified queue copies, and dependency-aware reset.
- Added the EventBridge Scheduler-to-SQS lab with a service trust policy, queue-scoped execution permission, dedicated schedule group, dynamic one-time expression, eventual-delivery polling, and automatic schedule deletion verification.
- Added the SNS subscription filter policy lab with two message-attribute filters, two contrasting publishes, exact-route verification, exclusion checks, and dependency-aware cleanup.
- Added the first CloudFormation lab with template validation, S3 and SQS stack provisioning, outputs, logical-to-physical resource mapping, event inspection, live service verification, stack deletion, and ownership cleanup proof.
- Added the first EC2 networking lab with a dedicated VPC, public/private subnets, public-IP behavior, internet gateway attachment, separate route tables, explicit subnet associations, topology verification, and dependency-aware teardown.
- Added the EC2 traffic-control lab with trusted-CIDR HTTPS ingress, web-to-app security-group references, stateful egress inspection, an explicit network ACL support probe, intended stateless NACL rule artifacts, and isolated teardown.
- Added the EC2 S3 gateway endpoint lab with an isolated VPC and private subnet, explicit route-table association, a bucket-scoped endpoint policy, managed prefix-list route inspection, a documented Floci persistence boundary, and dependency-aware teardown.
- Added the EC2 SQS interface endpoint lab with isolated subnet placement, HTTPS-only endpoint security, private DNS, queue-scoped endpoint policy, endpoint ENI inspection, and dependency-aware teardown.
- Started the serverless application spine with a Lambda lab that creates an execution role, attaches CloudWatch Logs permissions, packages a Python handler, creates and invokes `lab-echo`, verifies the response, inspects `/aws/lambda/lab-echo`, and cleans up the function, log group, role policy, role, and recorded invocation state.
- Added the API Gateway to Lambda lab with a lab-owned HTTP API, Lambda proxy integration, `POST /echo` route, `$default` auto-deploy stage, scoped Lambda invoke permission, local request verification, and dependency-aware teardown.
- Added the DynamoDB CRUD and query lab with an on-demand orders table, `CustomerIdIndex` GSI, low-level attribute JSON item writes, key reads, update expressions, customer-index queries, item deletion, table deletion, and live-state/milestone verification.
- Added the Lambda-to-DynamoDB lab with a table-backed order writer function, least-privilege `dynamodb:PutItem` role policy, environment-driven table selection, synchronous invoke verification, DynamoDB item proof, CloudWatch Logs proof, and dependency-aware cleanup.
- Added the KMS crypto lab with a tagged symmetric key, stable alias, metadata inspection, app-configuration encrypt/decrypt round trip, ciphertext/decrypt markers, alias deletion, and key deletion scheduling cleanup.
- Added SSM Parameter Store and Secrets Manager labs for local application configuration: hierarchical JSON parameter storage, direct and path-based reads, JSON secret creation, initial read verification, rotated value updates, metadata inspection, and lab-owned cleanup.
- Added the Lambda runtime configuration lab with lab-owned SSM and Secrets Manager inputs, least-privilege read policy, environment-driven resource IDs, safe response verification, CloudWatch Logs proof, and dependency-aware cleanup.
- Added the Lambda SQS event source lab with a lab-owned queue, least-privilege polling policy, packaged consumer handler, Lambda event source mapping, SQS message proof, CloudWatch Logs proof, and dependency-aware cleanup.
- Migrated the default visual system to an AWS-adjacent console theme with semantic CSS tokens, consistent top navigation across primary pages, tightened console page headers, shared empty-state polish, and the new README screenshot.
- Reviewed Floci 1.5.31, added a Cloud Control read-only resource-discovery page, added a CloudWatch Logs Insights query path to the CloudWatch workbench, surfaced EC2 snapshots, refreshed release-aware notes for RDS Data API PostgreSQL support, RDS mock mode, Cloud Control/read-only APIs, Lambda persistence and cold starts, Elastic Beanstalk persistence, API Gateway API key tags/GetApiKey, EventBridge-to-Firehose delivery, Scheduler ECS targets, CloudFormation S3 CORS, Step Functions ItemReader and qualified Lambda ARNs, Secrets Manager BatchGetSecretValue, EC2 catalogs, and Firehose delivery targets.
- Reviewed Floci 1.5.32 and updated existing services without adding Lightsail: exposed optional S3 authorization and default encryption behavior, added Lambda event-source failure and package-download detail, Cognito global sign-out and token revocation, SES contact CRUD, KMS key-filtered aliases, and Step Functions JSONata guidance.
- Simplified the shared console experience with compact high-cardinality summaries, one primary AWS CLI output panel, focused S3 inventory, and first-class IAM instance-profile browsing and role attachment.
- Added the Activity page at `/activity/` with browser-local recent activity for API Gateway requests, EventBridge events, Lambda invokes, and SQS sends/receives; added workbench links, safe replay/prefill hooks, and shared `ServiceConsole` activity helpers.
- Added the AWS CLI Console at `/console/` for running AWS-only commands against the active Floci endpoint, including local endpoint injection, stdout/stderr capture, parsed JSON rendering, a draggable curated command palette, browser-local command history, and destructive-command confirmation.
- Scoped lab-progress caching to the active Floci endpoint/profile context so browser sessions pointed at different local endpoints do not share cached lab progress.
- Fixed API Gateway HTTP API request testing so AWS-shaped `execute-api` endpoints fall back to Floci's local execute-plane URL.
- Added console navigation polish with a persistent service rail, global `Cmd/Ctrl+K` service search, local favorites, recently visited services, and a shared collection filtering/count helper used by the homepage service grid.
- Expanded the IAM workbench with user/group/role/instance-profile creation, dependency-aware cleanup, policy simulation, trust-policy templates, a tutorial-style About Floci IAM guide, and safer user identity switching that creates a session key without deleting existing keys.
- Added the IAM enforcement capstone lab that creates a user, verifies denied S3 access, creates an assumable SQS-read role, assumes it, confirms the allowed SQS path, and confirms S3 remains denied.
- Made the repo Docker-first: `docker compose up -d` now starts Floci and the dashboard together, with env-driven Django settings, persistent local volumes, the shared `floci_default` network, and local endpoint validation that accepts the Compose service host.

## Near-Term Priorities

These are the most likely next steps because they build directly on the current codebase.

Recently completed from the previous build order:

- Service Matrix.
- Home page filtering follow-through.
- Service Page Consistency Pass.
- Shared Console Shell hardening first pass.
- Action API registry audit.
- Shared action metadata loading in `service-console.js`.
- Environment Details page.
- Completed action audit baseline cleanup across all historical route/test gaps.
- Cloud Map shared action loader migration.
- IAM policy version, group membership, trust policy, create, cleanup, simulation, identity switching, and role/profile follow-ups.
- IAM enforcement capstone lab.
- Activity page and replay/prefill support for API Gateway, EventBridge, Lambda, and SQS.
- Endpoint-scoped lab-progress caching.
- API Gateway HTTP API local execute-plane fallback.

### 1. Keep Action Audit Baseline At Zero

Use the new action metadata audit to prevent future route/test drift.

Feasible first version:

- Add endpoint tests in the same change as each new registry action.
- Keep `ACTION_TEST_REFERENCE_GAP_BASELINE` empty unless a deliberately temporary exception is documented.
- Fix or remove stale action metadata paths when the audit exposes dead links.
- Keep destructive action confirmation metadata mandatory.

Why it matters:

- The registry becomes a trustworthy map of what the UI can really execute.
- Contributors get a measurable way to improve coverage without guessing.

### 2. Review Workbench Action Placement

Revisit existing service workbenches against the resource-scoped action placement rule.

Feasible first version:

- Audit each interactive workbench for buttons that act on a selected resource but still live in a global toolbar.
- Move resource-scoped actions into the selected resource detail panel, row, drawer, or card where practical.
- Keep global actions such as refresh, create, import, filters, and broad page utilities in the page or workbench toolbar.
- Keep bulk actions near collection selection controls.
- Start with high-traffic or high-risk workbenches such as S3, SQS, Lambda, Secrets Manager, CloudFormation, EC2, and IAM.

Why it matters:

- Users can see exactly which resource a button will affect before they click.
- Destructive and identity-changing actions become less ambiguous.
- The dashboard gains a consistent interaction model across services instead of service-by-service button drift.

### 3. Cloud Control And Unified Resource Discovery

Floci 1.5.31 adds Cloud Control `list_resources` plus broader read-only describe/list APIs. The dashboard now has a first read-only Cloud Control page; the next pass should make it easier to compare unified discovery with the service-specific pages.

Feasible first version:

- Add a type-name filter or preset picker instead of loading only the fixed default set.
- Cross-check Cloud Control results against the existing service-specific inventory pages.
- Add deep links from Cloud Control resources to matching service pages where the resource type is known.
- Document gaps where Cloud Control can discover resources that the dashboard does not yet render directly.

Why it matters:

- Cloud Control is a useful bridge for Steampipe, IaC, and generic AWS resource explorers.
- The new dashboard view can become the comparison point between unified discovery and service-specific workbenches.

### 4. Step Functions Follow-Ups

Keep improving the Step Functions execution and version workbench.

Feasible follow-ups:

- Add ASL definition graph rendering.
- Improve execution-history filtering and grouping.
- Add task-token callback helpers for waitForTaskToken workflows.
- Add richer state-machine version and alias navigation if local support grows beyond publish/delete version workflows.
- Add cross-service links from state machine Lambda tasks where the resource ARN is easy to identify.

Why it matters:

- Step Functions is a natural workflow debugger for local Lambda and service-integration testing.

### 5. IAM Polish

Keep improving the IAM workbench where it makes local permission failures easier to understand.

Feasible follow-ups:

- Add optional principal search/filtering inside the IAM workbench once inventories grow large enough to make browsing slow.
- Add better affordances for copying access key and assumed-role credential exports after refresh.
- Add templates or validation hints for AssumeRole session policies and managed session policy ARNs.
- Add richer policy simulation results or managed policy version document diffs if Floci exposes enough evaluation/version metadata.

Why it matters:

- IAM is central to local AWS debugging.
- The workbench should stay focused on identity debugging, not on cloning every IAM console screen.

### 6. S3 Follow-Ups

Keep improving the existing S3 workbench.

Feasible follow-ups:

- Improve object pagination behavior.
- Improve empty/delete handling for versioned buckets.
- Replace any remaining unsafe HTML rendering.
- Add clearer empty states for buckets, prefixes, and object details.
- Add cross-service links where S3 notifications reference SQS, SNS, or Lambda resources.
- Add optional links from completed S3 labs into the matching bucket, object, or queue workbench.

Why it matters:

- S3 is the reference workbench and likely a high-traffic page.
- The completed S3 lab sequence now provides a repeatable regression and learning surface for these workflows.

### 7. Workflow Lab Framework

Continue the local AWS workflow lab system beyond the completed foundations: 78 labs across IAM, S3, KMS, SSM, Secrets Manager, Cognito, SQS, SNS, Scheduler, DynamoDB, Kinesis, Lambda, API Gateway, EventBridge, Step Functions, CloudWatch, EC2, RDS, ElastiCache, Elastic Load Balancing with Auto Scaling, Route 53, ECS, EKS, CloudFormation, and AWS Backup.

Certification study path: [`docs/certification-coverage.md`](./docs/certification-coverage.md) maps every lab to the AWS Cloud Practitioner (CLF-C02) and Solutions Architect – Associate (SAA-C03) exam domains. It also covers gaps, what Floci can emulate, a prioritized plan for new labs, and how exam tags and study tracks could work in the curriculum UI and extend to other certifications.

Feasible follow-ups:

- Build the certification lab waves from `docs/certification-coverage.md`. Wave 1: HA web tier with ALB and Auto Scaling, RDS and Aurora resilience, S3 data protection and storage tiers, a three-tier VPC with NAT, KMS key management, and Route 53 failover.
- Add exam and task tags to labs, plus a study-track view (Levels · CLF-C02 · SAA-C03) with domain-weighted progress.

- Continue the new serverless application spine with S3 object-created notifications to Lambda, EventBridge rules to Lambda or SQS, and EventBridge Pipes source-to-target flows where local support is reliable.
- Continue networking labs with multi-AZ interface endpoints, endpoint policy variations, or hybrid connectivity when local support makes them useful.
- Add “View in dashboard” links after verified steps.
- Keep next-batch recommendations completion-driven and minimal; avoid reintroducing a separate learning-path layer unless progress tracking becomes richer.
- Consider step-order guidance while preserving live-state recovery after reloads.

Why it matters:

- Labs teach AWS-shaped workflows while proving Floci behavior end to end.
- The curated runner model keeps command execution safe and repeatable.
- Multi-service labs can turn the dashboard into a practical local integration-learning environment.

### 8. EC2 Follow-Ups

Keep improving the new EC2 instance workbench.

Feasible follow-ups:

- Surface Docker container identifiers, SSH host ports, and UserData output when Floci exposes them.
- Add security group ingress and egress editing with explicit revoke confirmations.
- Add Elastic IP allocate, associate, disassociate, and release actions.
- Add VPC and subnet creation flows for local networking tests.
- Add IAM instance profile selection from IAM inventory where the relationship is easy to infer.

Why it matters:

- EC2 is now a real local compute workflow in Floci.
- Launch, lifecycle, key import, IMDS, and networking hints make the dashboard a better local debugger than raw inventory alone.

### 9. EventBridge Follow-Ups

Keep improving the new EventBridge event sender.

Feasible follow-ups:

- Add rule enable and disable actions.
- Add simple rule creation for event pattern tests.
- Add target summaries that deep-link to Lambda, SQS, SNS, or Step Functions pages where ARNs are recognizable.
- Extend Activity replay from modal prefill into optional one-click resend when the event is clearly safe.
- Add event pattern helper presets for common local app events.

Why it matters:

- EventBridge sits between many existing interactive workbenches.
- Better cross-service links would turn it into a practical event-routing debugger.

### 10. API Gateway Follow-Ups

Keep improving the new API Gateway request workbench.

Feasible follow-ups:

- Add route/resource filtering and clearer integration summaries inside the workbench.
- Extend Activity replay from request prefill into optional one-click resend when users explicitly opt in.
- Add deep links to Lambda and CloudWatch Logs when integrations or log groups are recognizable.
- Add helper presets for common JSON, query string, and header test cases.

Why it matters:

- API Gateway is now the local HTTP front door for Lambda and HTTP proxy workflows.
- Replay and cross-service links would make it a stronger request debugger.

### 11. Kinesis Follow-Ups

Keep improving the new Kinesis stream workbench.

Feasible follow-ups:

- Add stream deletion with explicit destructive confirmation.
- Add enhanced monitoring toggles where Floci supports them.
- Add stream consumer registration and deregistration.
- Add simple record replay from recently read records.
- Add links from EventBridge Pipes or Lambda event source mappings when a stream ARN is recognizable.

Why it matters:

- Kinesis now completes the event/data-stream testing loop alongside SQS, SNS, EventBridge, Lambda, and CloudWatch.

### 12. Secrets Manager And SSM Follow-Ups

Keep improving local configuration and secret debugging.

Feasible follow-ups:

- Add Secrets Manager resource policy editing and tag editing.
- Add secret restore/cancel-delete flows if Floci exposes them.
- Add SSM parameter path browsing and parameter history.
- Add bulk parameter reads by path.
- Improve SecureString/KMS edge-case messaging.

Why it matters:

- Local apps commonly fail because configuration or credentials are missing, malformed, stale, or read from the wrong path.

## Parking Lot

These ideas are still interesting, but they likely need more design, Floci support, or a larger implementation window before they belong in the active roadmap.

- Resource graph across services.
- Request trace explorer.
- Docker runtime lens.
- Full state export/import/reset manager.
- Scenario launcher.
- Broader tutorial mode beyond the current curated workflow labs.
- Large file transfer UI.
- Deep CloudFormation stack graph.
- Broad IAM policy simulation.
- Multi-user auth.

## Suggested Build Order

This order is intentionally modest and can change:

1. Keep action audit baseline at zero.
2. Review workbench action placement against the resource-scoped action rule.
3. Iterate on the Cloud Control and unified resource discovery surface.
4. Step Functions follow-ups.
5. IAM polish.
6. S3 follow-ups.
7. Extend the workflow lab framework beyond the completed foundational sequences.
8. EC2 follow-ups.
9. EventBridge follow-ups.
10. API Gateway follow-ups.
11. Kinesis follow-ups.
12. Secrets Manager and SSM follow-ups.

## Contributor Checklist

When adding or improving a service page:

- [ ] Preserve the existing read-only inventory.
- [ ] Register service and action metadata in `dashboard/services.py`.
- [ ] Use `dashboard/actions.py` for JSON parsing and normalized errors.
- [ ] Place resource-scoped buttons with the resource/detail they act on; keep only global page/workbench actions in the toolbar.
- [ ] Add destructive confirmations for risky actions.
- [ ] Add endpoint tests for every registered action, plus Playwright coverage for the page.
- [ ] Rebuild and commit the frontend assets when the UI changes.

[`CONTRIBUTING.md`](./CONTRIBUTING.md) has the full workflow and the test commands.

## Open Questions

- Should service docs links and operation counts remain manually maintained in the dashboard registry?
- Should more dedicated React pages move to declarative, registry-driven action panels like `GenericServiceWorkbench`?
- Should tutorial definitions live in this repo, Floci docs, or both?
- Which remaining inventory-only service should become the next interactive workbench?
- Should the Environment page grow a Docker/runtime lens when Floci exposes stable runtime fields?
- Which Cloud Control type names should be in the default preset list, and which should be opt-in?
