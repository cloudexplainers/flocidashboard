# Floci Dashboard 2.0.1

**Floci Dashboard 2.0** is an official AWS Cloudscape-powered Single Page Application for inspecting, testing, and learning against a local [Floci](https://floci.io/) AWS-compatible environment. Built with **React 19**, **TypeScript 7**, **Vite 8**, and AWS’s official **`@cloudscape-design/components`** design system, Floci Dashboard 2.0 provides AWS management consoles, developer inspection tools, interactive workflow labs, and dynamic workbenches for **65 AWS services**. Compatibility is validated against **Floci 2.3.0**; Floci also provides services beyond the dashboard's current catalog.

![Floci Dashboard 2.0 Cloudscape Console UI](./flocidashboard.png)

## Architecture & Features in Version 2.0

- **Official AWS Cloudscape Design System**: Global Top Navigation, persistent AWS Favorites Bar with official AWS service icons, collapsible Side Navigation, deep breadcrumb navigation, and authentic AWS design fidelity.
- **20 Dedicated Consoles**: Bespoke consoles for **Amazon S3**, **Amazon EC2**, **AWS IAM**, **Amazon DynamoDB**, **AWS Lambda**, **Amazon SQS**, **Amazon SNS**, **Amazon RDS**, **AWS KMS**, **AWS Secrets Manager**, **AWS CloudFormation**, **Amazon Route 53**, **Amazon EventBridge**, **Amazon CloudWatch**, **AWS Step Functions**, **Amazon Cognito**, **Amazon API Gateway**, **AWS Systems Manager (SSM)**, **Developer Inspector Inbox**, and **Workflow Labs**.
- **Universal Dynamic Action Runner**: Dynamic, schema-driven Cloudscape consoles powering all **65 AWS mock services** with zero legacy fallbacks.
- **Zero-Latency Workflow Labs (66 Labs / 365 Steps)**: 0ms catalog metadata load across 20 services, synchronized code generation for **AWS CLI v2**, **Python (`boto3`)**, and **Terraform (`HCL`)**, live state verification, and automated sequential runner.
- **Developer Inspection Inbox**: Local SES email inbox viewer, non-destructive SQS message peeker (`VisibilityTimeout=0`), and real-time Lambda CloudWatch log streams.
- **Interactive AWS CLI Sandbox**: In-browser command runner with quick presets, shortcut execution (`Cmd+Enter`/`Ctrl+Enter`), and destructive command guardrails.
- **Session Identity & STS Assumer**: Switch between Admin, IAM Users, and temporary assumed roles (`sts:AssumeRole`) with live caller ARN diagnostics.
- **Activity & Audit Log**: Real-time stream of incoming AWS operations recorded with expandable JSON payloads.

## Quickstart

These steps launch Floci and the dashboard together from this repository. They are written for macOS. The dashboard will likely work fine on Windows as well, but Windows has not been tested yet.

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) if you have not already.

Make sure Docker Desktop is running. You will also need `git` available in your shell.

Paste the following commands into your terminal one at a time, pressing Enter after each one.

Clone the dashboard repository:

```bash
git clone https://github.com/returnvalue/flocidashboard.git
```

Change into the dashboard directory:

```bash
cd flocidashboard
```

Start Floci and the dashboard:

```bash
docker compose up -d
```

Open `http://127.0.0.1:8000` in your browser.

The repo-owned `docker-compose.yml` starts:

- `floci` on `http://127.0.0.1:4566`
- `dashboard` on `http://127.0.0.1:8000`
- a shared `floci_default` Docker network for Floci-spawned containers
- persistent Docker volumes for Floci state and the dashboard SQLite session database

Follow logs while the services start:

```bash
docker compose logs -f
```

Stop everything:

```bash
docker compose down
```

Stop everything and remove local Floci/dashboard state:

```bash
docker compose down -v
```

Copy `.env.example` to `.env` before starting if you want to change exposed ports or local defaults.

### Local Python Development

Docker Compose is the primary path. You can still run Django directly on your host when you want the fastest edit/test loop against an already running Floci container.

Use Python 3.14 (also used by Docker), then create the virtual environment:

```bash
python3 -m venv .venv
```

Activate the virtual environment:

```bash
source .venv/bin/activate
```

Upgrade `pip` inside the virtual environment:

```bash
python3 -m pip install --upgrade pip
```

Install the dashboard requirements:

```bash
pip3 install -r requirements.lock
```

Set the Floci endpoint URL:

```bash
export AWS_ENDPOINT_URL=http://localhost:4566
```

Set the default AWS region:

```bash
export AWS_DEFAULT_REGION=us-east-1
```

Set the local AWS access key:

```bash
export AWS_ACCESS_KEY_ID=test
```

Set the local AWS secret key:

```bash
export AWS_SECRET_ACCESS_KEY=test
```

Or create and use your own AWS profile for Floci before running the dashboard:

Set your Floci AWS profile:

```bash
export AWS_PROFILE=floci-admin
```

Set the default AWS region:

```bash
export AWS_DEFAULT_REGION=us-east-1
```

Set the Floci endpoint URL:

```bash
export AWS_ENDPOINT_URL=http://localhost:4566
```

`FLOCI_AWS_ENDPOINT_URL` and `FLOCI_AWS_REGION` are also supported if you prefer Floci-specific names. When no explicit profile or credentials are visible to Django, the dashboard falls back to local `test/test` credentials so a fresh Floci install can still show service inventory.

Start the Django dev server:

```bash
python3 manage.py runserver 127.0.0.1:8000
```

When Django runs on your host, use `http://localhost:4566`. When Django runs inside Compose, the dashboard container uses `http://floci:4566`.


## What It Shows

- Local Floci health and version diagnostics
- Environment diagnostics for AWS endpoint, region, profile, credential source, caller identity, S3 authorization mode, and local-endpoint warnings
- Clickable service cards for 65 supported local services, with a top-24 default home view, persisted home-page service filtering, and a Tracked Resources view that shows only services with discovered resources
- Service Matrix coverage page showing registry maturity (58 interactive workbenches), API paths, shared console status, action counts, tags, and linked service pages
- Labs directory at `/labs/` showing 17 services with 63 active workflow labs, 349 runnable step counts, and direct links
- Multi-SDK code generation for every lab step: toggle instantly between **AWS CLI**, **Python `boto3`**, and **Terraform HCL**, with user preference persistence via `localStorage`
- Automated **"Run All Steps" (Play Through)** runner with live step-by-step progress tracking, auto-scrolling, speed regulation, and contextual explanations
- Activity page at `/activity/` with browser-local recent API Gateway requests, EventBridge events, Lambda invokes, and SQS sends/receives, plus links back to activity-enabled workbenches for safe replay or prefill
- AWS CLI Console at `/console/` for running AWS-only CLI commands against the active local Floci endpoint, with endpoint injection, JSON parsing, a draggable curated command palette, command history, and destructive-command confirmation
- AWS Cloudscape Design System alignment with semantic status indicators (🟢 Positive, 🟡 Warning, 🔴 Negative, 🔵 Info, ⚪ Inactive), 2–4 column key-value attribute grids, and crisp container cards
- 100% Pedagogical Coverage: Structured, educational `About Floci <Service>` accordions across all 65 services
- Local AWS workflow labs for IAM, S3, KMS, SSM Parameter Store, Secrets Manager, Cognito, SQS, SNS, EventBridge Scheduler, DynamoDB, Lambda, API Gateway, EventBridge, Step Functions, CloudWatch, EC2 compute/networking, and CloudFormation, with exact AWS CLI commands, approved one-click execution, live-state verification, reset actions, next-batch recommendations, and breadcrumb navigation
- Interactive workbenches for 58 services: S3, IAM, EC2, SQS, SNS, Lambda, DynamoDB, CloudWatch, Step Functions, EventBridge, EventBridge Pipes, EventBridge Scheduler, API Gateway, AppSync, Kinesis, KMS, Secrets Manager, SSM Parameter Store, CloudFormation, Cognito, AWS Config, RDS, Auto Scaling, ELB v2, CloudFront, AWS Cloud Map, Route 53, ACM, ECS, ECR, EKS, ElastiCache, OpenSearch, Athena, Backup, Firehose, Glue, Kafka, Neptune, SES, Transfer Family, Textract, Transcribe, CodeDeploy, CodeBuild, Bedrock Runtime, AppConfig, Resource Groups Tagging, Amazon DocumentDB, MemoryDB, Amazon MQ, WAF v2, IoT Core, S3 Vectors, Cost Explorer, and AWS Price List & Calculator
- IAM-focused identity tooling with user, group, role, managed policy, inline policy, access key, role trust, instance profile, cleanup, policy simulation, session identity switching, assumed-role credential, and tutorial/lab workflows
- Interactive Cost & Pricing Sandbox: Local Cost Explorer with stacked daily usage bar charts, cost forecast metrics, and anomaly alerts, alongside a multi-service AWS Pricing Calculator Simulator (EC2, S3, Lambda, RDS)
- Deepened EC2 guided local compute workflows: guest IMDS socket querying (`169.254.169.254`), UserData script verification, IAM instance role assume-role validation, security-group-published web servers, broken-route diagnosis & repair, private S3 gateway endpoints, and SSM RunCommand agent dispatch
- First-class Lambda, ECS, EKS, and ECR workbenches covering function lifecycle, local k3s cluster resources, OCI repositories, image manifests, stored policies, and Docker push/pull workflows
- Compatibility checks against Floci 2.3.0, including all 65 registered inventories and live S3, SQS, DynamoDB, SSM, and IAM/STS workflows. Historical release notes remain available in individual service guides.
- Loading state with the Floci cloud image while service data is fetched

## Local AWS Workflow Labs

The Labs directory at `/labs/` lists every service with curated labs. Individual service pages also link to their own lab batches when labs are available.

Labs show the command shape across AWS CLI, Python `boto3`, and Terraform. Each Run button invokes a registered boto3-backed action, displays the response, and independently verifies the result against live Floci state. Users can also click **Run All Steps** to play through multi-step workflows automatically with paced scrolling and live descriptions. Reset removes only the resources owned by that lab. When the final lab in a service batch is complete, the lab page recommends the next batch in the recommended cloud engineering learning order: IAM $\rightarrow$ S3 $\rightarrow$ KMS $\rightarrow$ SSM $\rightarrow$ Secrets Manager $\rightarrow$ Cognito $\rightarrow$ SQS $\rightarrow$ SNS $\rightarrow$ Scheduler $\rightarrow$ DynamoDB $\rightarrow$ Lambda $\rightarrow$ API Gateway $\rightarrow$ EventBridge $\rightarrow$ Step Functions $\rightarrow$ CloudWatch $\rightarrow$ EC2 $\rightarrow$ CloudFormation.

The curriculum includes **66 labs across 20 services (365 total steps)**:
- **IAM (11 labs)**: admin bootstrap, users, policies, access keys, groups, roles, STS session policies, instance profiles, switched dashboard identities, and permission-enforcement checks.
- **S3 (12 labs)**: buckets, objects, prefixes, metadata, tags, versioning, presigned URLs, security, encryption, lifecycle retention, CORS, S3-to-SQS notifications, and multipart uploads.
- **KMS (1 lab)**: key lifecycle, alias creation, envelope encryption, and decryption round trips.
- **SSM (1 lab)**: hierarchical Parameter Store application configuration and secure string storage.
- **Secrets Manager (1 lab)**: secret creation, runtime retrieval, and secret rotation updates.
- **Cognito (2 labs)**: User Pools, App Clients, authentication flows, user groups, and custom attributes.
- **SQS (9 labs)**: queue inspection, message lifecycle, visibility timeouts, delayed delivery, batch operations, dead-letter queues, managed redrive, FIFO ordering, duplicate suppression, and purge.
- **SNS (2 labs)**: SNS-to-SQS fan-out, resource policies, raw delivery, and subscription filter policies.
- **Scheduler (1 lab)**: scheduled SQS delivery through scoped IAM execution roles.
- **DynamoDB (2 labs)**: table schema definitions, item CRUD, Partition/Sort keys, query/scan filters, and Lambda write integration.
- **Lambda (3 labs)**: function creation, synchronous/asynchronous invocation, SSM/Secrets reads, SQS event source mappings, and CloudWatch log tailing.
- **API Gateway (1 lab)**: HTTP API route creation, integrations, and live request proxying to Lambda.
- **EventBridge (1 lab)**: application-spine capstone joining API Gateway, Lambda, a custom event bus, transformed SQS targets, notification handlers, and failure simulations.
- **Step Functions (2 labs)**: State Machine Choice branching workflows and Parallel execution branches.
- **CloudWatch (2 labs)**: custom metric filters, CloudWatch Alarms, and Log Group streams.
- **RDS (1 lab)**: parameter group tuning, database instance provisioning, storage scaling, and reboot lifecycles.
- **ECS (1 lab)**: Fargate cluster provisioning, task definitions, standalone task execution, and microservice replica scaling.
- **EKS (1 lab)**: Kubernetes control planes, managed EC2 worker node groups, serverless Fargate profiles, and kubeconfig generation.
- **EC2 (11 labs)**: public/private VPC routing, stateful security groups, network ACLs, VPC endpoints, guest IMDS inspection, UserData validation, IAM instance roles, web server SG routing, broken route repair, private S3 gateway endpoints, and SSM RunCommand dispatch.
- **CloudFormation (1 lab)**: template validation, change set creation, stack provisioning, and resource lifecycle management.

The EventBridge capstone also includes a live, dependency-free SVG resource graph. It lays serverless resources out in deterministic semantic columns, deep-links nodes to their workbenches, exposes configuration evidence for every relationship, and distinguishes healthy, disabled, broken, unverified, and locally unsupported states.

Lab definitions and implementation notes live in [`buildinglabs.md`](./buildinglabs.md).

## Configuration

Host-development defaults live in `flocidashboard/settings.py`:

- `FLOCI_AWS_ENDPOINT_URL`: `http://localhost:4566`
- `FLOCI_AWS_REGION`: `us-east-1`
- `FLOCI_AWS_PROFILE`: `floci-admin`

Environment variables override those defaults. If `floci-admin` is not configured locally, the dashboard uses local `test/test` credentials instead of failing the homepage with missing-credential cards.

Set `FLOCI_SERVICES_S3_ENFORCE_AUTH=true` to make Floci enforce S3 ACL and bucket-policy public/private reads and reject unknown signed access keys. The Compose default remains `false` for compatibility; Environment Details reports whether enforcement is active.

The Docker Compose quickstart overrides the endpoint to `http://floci:4566` inside the dashboard container and uses local `test/test` credentials by default. Compose defaults can be overridden in `.env`; see `.env.example` for the supported ports, credentials, and Floci runtime toggles.

## Quick Check

```bash
docker compose exec dashboard python manage.py check
```

Then refresh the browser. Service cards should appear once Floci responds.

For host Python development, run the same check from your activated virtual environment:

```bash
python3 manage.py check
```

## Testing & Verification

Run the full Django test suite inside Docker:

```bash
docker compose exec -T dashboard python manage.py test
```

Or on your local host virtual environment:

```bash
python3 manage.py test
```

Run the live compatibility suite against your configured local Floci endpoint:

```bash
docker compose exec -T dashboard python manage.py test dashboard.integration_tests
```

The live suite reads all 65 inventories and creates uniquely named temporary resources for S3, SQS, DynamoDB, SSM, and IAM/STS round trips. It cleans up its own resources. Cloud Control resource types rejected by Floci remain visible as capability gaps.

Build and verify the frontend with Node.js 22.12+ (Docker and CI use Node.js 26):

```bash
cd frontend
npm ci
npm run build
npm audit
```

Docker builds compile the frontend automatically. Compose bind-mounts this repository, so also run the frontend build on the host after editing its sources. The generated assets are tracked for host development without Node.js. The host unit suite runs JavaScript syntax checks when Node.js is installed; the Python-only runtime container skips that one check. CI runs both the complete unit suite and live compatibility suite with Node.js available.

Direct Python dependencies are pinned in `requirements.txt`; `requirements.lock` includes their resolved transitive dependencies. Update both together in a clean environment when upgrading. Frontend versions and transitive resolutions are recorded in `frontend/package.json` and `frontend/package-lock.json`.

AWS CLI 1.46.1 requires `docutils<=0.19` and `rsa<4.8`; the lock file retains the newest versions allowed by those upstream constraints.

## Contributing

### Lab verification and Playwright

The complete live lab audit executes every registered step, checks completion, resets each lab, and writes a JSON report. Use a disposable Floci instance: the labs use fixed teaching resource names. The standalone configuration runs on port 14566 without the dashboard's persistent volumes:

```bash
docker compose -f compose.labs.yml -p flocidashboard-lab-tests up -d --wait
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test FLOCI_AWS_PROFILE='' \
  python manage.py check_labs --endpoint http://localhost:14566
```

Use `--service ec2` or `--service stepfunctions --lab parallel-retry-workflow` to rerun one area. Reports default to `test-results/labs.json`. EC2 guest initialization, Lambda, RDS, ECS, and EKS exercise real Docker runtimes and may download images; allow several minutes for a cold run.

Playwright covers every curriculum lab's rendering and SDK tabs, catalog consistency, filtering, completion, error handling, and reset. Two separate live browser scenarios exercise IAM and S3 execution, reload, and teardown against the disposable endpoint:

```bash
cd frontend
npm ci
npx playwright install chromium
npm run build
npm run test:e2e
npm run test:e2e:live
```

The runner starts Django on port 18000 with a separate temporary SQLite database. It uses `../.venv/bin/python` by default; override `E2E_PYTHON` if needed. `E2E_FLOCI_ENDPOINT` overrides the disposable emulator URL. Failure screenshots and traces are written to `frontend/test-results/`; the HTML report is in `frontend/playwright-report/`. UI regression tests mock lab execution/status responses; the live project and `check_labs` use real Floci.

```bash
docker compose -f compose.labs.yml -p flocidashboard-lab-tests down
```

For guidelines on adding new service workbenches, labs, or custom UI components, please see [`CONTRIBUTING.md`](./CONTRIBUTING.md).

