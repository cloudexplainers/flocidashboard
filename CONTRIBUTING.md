# Contributing to Floci Dashboard

Thank you for contributing to Floci Dashboard! This guide explains the core architecture, coding standards, and verification steps for adding new features, services, or workflow labs.

---

## Core Architecture

Floci Dashboard is a Django web application that interfaces with a local [Floci](https://floci.io/) AWS-compatible emulator via `boto3`.

### Key Components

- **Service Registry (`dashboard/services.py`)**: Canonical registry of all supported services, maturity stages (`interactive_workbench`, `read_only_inspector`, `inventory_only`), and action metadata.
- **Service Inventory (`dashboard/aws.py`)**: Read-only inventory loaders and release-aware service summaries. Always return graceful empty/error shapes when Floci has no resources provisioned.
- **Interactive Action Endpoints (`dashboard/actions.py` & `dashboard/*_views.py`)**: REST/JSON endpoints for mutating service resources with strict error normalization.
- **Workflow Labs (`dashboard/labs/`)**: Curated multi-step AWS workflows with live-state verification and multi-SDK (AWS CLI, Python `boto3`, Terraform) code generation.
- **Console UI (`frontend/src/`)**: React + TypeScript single-page app built with Cloudscape. `App.tsx` routes each service to a page in `pages/` (falling back to `GenericServiceWorkbench`), and `api/client.ts` wraps the JSON endpoints. Vite builds it into `dashboard/static/dashboard/dist/`, which Django serves through `dashboard/templates/dashboard/console_spa.html`.

---

## Adding a New Service Workbench

1. **Register the Service**: Add an entry to `dashboard/services.py` with title, category, maturity, docs URL, and action metadata.
2. **Implement Inventory Loading**: Add the inventory loader function to `dashboard/aws.py`.
3. **Add a Dedicated Console (Optional)**: Services render in `GenericServiceWorkbench` by default. For a dedicated page, add `frontend/src/pages/<Service>Console.tsx`, route it in `frontend/src/App.tsx`, and add its API calls to `frontend/src/api/client.ts`. Rebuild with `npm run build` in `frontend/`.
4. **Add Automated Tests**: Add Django tests in `dashboard/tests_new_services_inventory.py` or a dedicated test module, and Playwright coverage in `frontend/e2e/dashboards.spec.ts`.

---

## Adding a New Workflow Lab

1. Create a lab module under `dashboard/labs/` (e.g. `<service>_labs.py`).
2. Define the lab using `WorkflowLab` and `LabStep` structures with descriptive goals, hints, and expected AWS CLI commands.
3. Multi-SDK code generation (`boto3` and Terraform) will be automatically derived from the CLI command shape via `dashboard/labs/snippets.py`.
4. Implement the execution runner and live-state verification functions.
5. Register the batch in `dashboard/labs/registry.py` and update batch progression in `dashboard/views.py`.

---

## Development & Testing Workflow

### Running Tests

Install the tested Python dependency set with `python -m pip install -r requirements.lock`. Docker uses Python 3.14. For frontend changes, use Node.js 22.12+ and run `npm ci && npm run build` in `frontend/`; Docker builds use Node.js 26 and compile assets automatically.

Execute the test suite inside the Docker container:

```bash
docker compose exec -T dashboard python manage.py test
```

Or from your host virtual environment:

```bash
python3 manage.py test
```

Run the separate live Floci suite as well when changing SDKs, inventory loaders, authentication, or emulator integration:

```bash
docker compose exec -T dashboard python manage.py test dashboard.integration_tests
```

It checks every registered inventory and temporary S3, SQS, DynamoDB, SSM, and IAM/STS resources. Existing resources are preserved. Tests in `dashboard/integration_tests.py` are deliberately excluded from default unit discovery so contributors can run unit tests without an emulator.

### Checking JavaScript Syntax

```bash
node --check dashboard/static/dashboard/*.js
```

### System Integrity Checks

```bash
python3 manage.py check
```
