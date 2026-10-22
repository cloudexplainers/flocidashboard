# Contributing to Floci Dashboard

Thanks for helping out. Floci Dashboard is a local console and lab environment for the [Floci](https://floci.io/) AWS emulator: a Django + boto3 backend and a React + Cloudscape frontend. This guide covers how the code is laid out, how to add a service console or a workflow lab, and what to run before you open a pull request.

For product direction and open ideas, see [`ROADMAP.md`](./ROADMAP.md). For lab design notes and the full curriculum, see [`buildinglabs.md`](./buildinglabs.md).

## Ground rules

- **Local only.** The dashboard talks to a local Floci endpoint. Don't add calls to real AWS or other outside services.
- **Keep it additive.** Read-only inventory is the base layer of every service page; interactive tools sit on top of it.
- **Make risky actions explicit.** Delete, purge, reset, empty, and cleanup actions need a confirmation. Put resource-scoped buttons next to the resource they act on, and keep only page-wide actions in the toolbar.
- **Fail clearly.** Return normalized errors from the backend and show them in the UI. Never show fabricated or fallback data as if it were real.
- **Keep changes focused.** One feature or fix per pull request, with tests in the same change.

## Getting set up

The quickest path is Docker, which starts Floci on port 4566 and the dashboard on 8001:

```bash
docker compose up -d
```

For host development you need Python 3.14, and Node.js 22.12+ if you touch the frontend. Floci must be running.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python manage.py migrate
DJANGO_DEBUG=true python manage.py runserver 127.0.0.1:8001
```

`DEBUG` is off by default. Turn it on for local development, or pass `--insecure` to `runserver` so it still serves the built UI.

```bash
cd frontend
npm ci
npm run dev      # Vite dev server
npm run build    # type-check and write dashboard/static/dashboard/dist/
```

See the [README](./README.md#configuration) for endpoint, Region, credential, and profile settings.

## Project layout

| Path | What lives there |
| --- | --- |
| `dashboard/services.py` | Service registry: title, category, maturity (`interactive_workbench`, `read_only_inspector`, `inventory_only`), docs links, and action metadata. |
| `dashboard/aws/` | Read-only inventory loaders: one `<service>.py` per service (`lambda_.py` for Lambda), plus `FlociClientFactory`, response cleaning, and `list_resources` in `__init__.py`. Service modules call `core.FlociClientFactory` and `core._paginate` so tests can patch them once on `dashboard.aws`. Loaders return empty or error shapes, never exceptions, when Floci has nothing to show. |
| `dashboard/actions.py` | Shared JSON parsing and normalized action errors. |
| `dashboard/<service>_api.py`, `dashboard/<service>_views.py` | Service-specific boto3 logic and JSON endpoints. |
| `dashboard/urls.py` | API routes under `api/<service>/…`. Every registered service also gets an SPA deep link automatically. |
| `dashboard/labs/` | Workflow labs: one `<service>_labs.py` per service, plus shared helpers and dispatch in `monolith.py`. |
| `dashboard/management/commands/check_labs.py` | Runs and verifies every lab step against a disposable emulator. |
| `frontend/src/App.tsx` | Routes each service to a page in `frontend/src/pages/`, falling back to `GenericServiceWorkbench`. |
| `frontend/src/api/client.ts` | Typed wrappers around the JSON endpoints. |
| `frontend/src/components/` | Shared UI, such as `ConsoleLayout` (the page shell `App.tsx` renders every page in). |
| `frontend/src/labCurriculum.ts` | Curriculum levels and the lab list shown on `/labs/`. |
| `dashboard/static/dashboard/dist/` | Built frontend assets, committed to the repo and served by `dashboard/templates/dashboard/console_spa.html`. |

## Adding or improving a service console

1. **Register the service** in `dashboard/services.py` with its title, category, maturity, docs URL, and any interactive actions.
2. **Add the backend.** Put boto3 logic in `dashboard/<service>_api.py` and JSON endpoints in `dashboard/<service>_views.py`, then route them in `dashboard/urls.py` under `api/<service>/`. Use `dashboard/actions.py` for request parsing and error responses. Every action in the registry needs an endpoint test; `ActionRegistryAuditTests` in `dashboard/tests.py` enforces this.
3. **Add the frontend (optional).** Services render in `GenericServiceWorkbench` by default. For a dedicated page, add `frontend/src/pages/<Service>Console.tsx`, route it in `App.tsx`, and add its calls to `api/client.ts`. Build it from Cloudscape components, and show results and errors in place with `Alert` and `StatusIndicator`, as the existing pages do.
4. **Rebuild the assets** with `npm run build` in `frontend/` and commit the updated `dashboard/static/dashboard/dist/`.
5. **Add tests:** Django tests in `dashboard/tests_<service>_console.py`, and Playwright coverage in `frontend/e2e/dashboards.spec.ts`. The Playwright suite mocks API responses, so cover error and empty states as well as the happy path.

## Adding a workflow lab

Labs are curated, multi-step AWS workflows. The dashboard runs each step through backend code, verifies the live result in Floci, and can reset everything the lab created. The displayed CLI command is what the step teaches; `dashboard/labs/snippets.py` derives the boto3 and Terraform tabs from it.

1. **Write the lab in its service module**, `dashboard/labs/<service>_labs.py`, or create one following an existing module such as `kms_labs.py`. A module owns:
   - the lab definitions (see `Lab` and `LabStep` in `dashboard/labs/types.py`), collected in `LABS`;
   - a runner (`_run_<service>_…`) and a verifier (`_verify_…`) for each step;
   - `run_step(...)`, `status(...)`, and `reset(...)`, which dispatch on `lab_key` and `step_key`.
2. **Use the shared helpers** in `monolith.py`, such as the `_<service>_client()` functions, `_error_code`, `_json_text`, and `_wait_for_verification`. Get boto3 clients through those helpers rather than calling `FlociClientFactory` directly, so tests can patch one place.
3. **Register a new service module** in `monolith.py`: add a branch to `labs_for_service`, `run_lab_step`, `lab_status`, and `reset_lab`, add it to the re-export list at the end of the file, and add the service to `LAB_BATCH_ORDER`.
4. **Add the lab to the curriculum** in `frontend/src/labCurriculum.ts` with the right `stepCount`. The e2e suite fails if the catalog and the curriculum disagree.
5. **Tag it for the certification tracks** in `LAB_EXAM_TAGS` in `dashboard/labs/exams.py`, using the exam guide's task statement IDs; the first task per exam is the lab's main focus. A lab that fits no CLF or SAA task still needs an entry (`_tags()`). `dashboard/tests_exams.py` fails on untagged labs and unknown task IDs. See [`docs/certification-coverage.md`](./docs/certification-coverage.md) for how labs map to the exams.
6. **Make it repeatable.** Labs use fixed resource names, so every step must cope with leftovers from an earlier run, and `reset` must remove everything the lab created.
7. **Test it.** Add unit tests in `dashboard/tests_labs.py`. `LabsRegistryAuditTests` checks that every step has a runner and a status entry. Then run `check_labs` for the service against the disposable emulator (see below).

When a test patches a function that a lab module calls, patch it where it's looked up, for example `patch('dashboard.labs.kms_labs._verify_kms_lab_key')`. `patch('dashboard.labs.FlociClientFactory')` still covers every lab module.

## Testing

| What | Command | Needs Floci? |
| --- | --- | --- |
| System checks | `python manage.py check` | No |
| Lint (CI pins ruff 0.15.10) | `ruff check .` | No |
| Unit tests (about 1,100, a few seconds) | `python manage.py test --noinput` | No |
| Live compatibility tests | `python manage.py test dashboard.integration_tests --noinput` | Yes |
| Playwright, mocked API | `npm run test:e2e` (in `frontend/`) | Barely; see below |
| Playwright, live | `npm run test:e2e:live` (in `frontend/`) | Disposable emulator |
| Lab audit | `python manage.py check_labs --endpoint http://localhost:14566` | Disposable emulator |

In Docker, prefix the Django commands with `docker compose exec -T dashboard`.

**Use the disposable emulator for anything that creates resources.** Labs and live tests use fixed names and will clash with, or clean up, resources in your everyday Floci. Start a throwaway one on port 14566:

```bash
docker compose -f compose.labs.yml -p flocidashboard-lab-tests up -d --wait
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test FLOCI_AWS_PROFILE='' \
  python manage.py check_labs --endpoint http://localhost:14566 --service kms
docker compose -f compose.labs.yml -p flocidashboard-lab-tests down
```

Some labs (EC2, Lambda, RDS, ECS, EKS) start extra containers and download images. If any are left behind after `down`, remove the `floci-aws-*` containers on the `flocidashboard-lab-tests` network.

Playwright starts its own Django server on port 18000 with a separate SQLite database. It points that server at `http://localhost:4566` unless you set `E2E_FLOCI_ENDPOINT`; set it to `http://localhost:14566` to keep test traffic off your everyday Floci. Set `E2E_PYTHON` if your interpreter isn't `../.venv/bin/python`. Install the browser once with `npx playwright install chromium`.

## Before you open a pull request

- [ ] Unit tests and `python manage.py check` pass.
- [ ] Frontend changes: `npm run build` passes, the rebuilt `dist/` is committed, and `npm run test:e2e` passes.
- [ ] Lab changes: `check_labs` passes for the affected service on the disposable emulator.
- [ ] Emulator integration or SDK changes: `dashboard.integration_tests` passes.
- [ ] Dependency changes update `requirements.txt` and `requirements.lock` together, or `frontend/package.json` and `package-lock.json` together.
- [ ] User-visible changes have a line in [`CHANGELOG.md`](./CHANGELOG.md), and docs that describe the changed behavior are updated.

CI (`.github/workflows/test.yml`) runs all of this except the lab audit on every push and pull request, against a Floci service container.
