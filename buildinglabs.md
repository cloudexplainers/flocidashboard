# Building Local AWS Workflow Labs

## Current Curriculum Status

The curriculum comprises **63 labs across 17 services (349 total runnable steps)**:

- **11 IAM labs** (admin bootstrap, users, policies, access keys, groups, roles, STS session policies, instance profiles, switched dashboard identities, and permission-enforcement checks)
- **12 S3 labs** (buckets, objects, prefixes, metadata, tags, versioning, presigned URLs, security, encryption, lifecycle retention, CORS, S3-to-SQS notifications, and multipart uploads)
- **1 KMS lab** (key lifecycle, alias creation, envelope encryption, and decryption round trips)
- **1 SSM Parameter Store lab** (hierarchical application configuration and secure string storage)
- **1 Secrets Manager lab** (secret creation, runtime retrieval, and secret rotation updates)
- **2 Cognito labs** (User Pools, App Clients, authentication flows, user groups, and custom attributes)
- **9 SQS labs** (queue inspection, message lifecycle, visibility timeouts, delayed delivery, batch operations, dead-letter queues, managed redrive, FIFO ordering, duplicate suppression, and purge)
- **2 SNS labs** (SNS-to-SQS fan-out, resource policies, raw delivery, and subscription filter policies)
- **1 EventBridge Scheduler lab** (scheduled SQS delivery through scoped IAM execution roles)
- **2 DynamoDB labs** (table schema definitions, item CRUD, Partition/Sort keys, query/scan filters, and Lambda write integration)
- **3 Lambda labs** (function creation, synchronous/asynchronous invocation, SSM/Secrets reads, SQS event source mappings, and CloudWatch log tailing)
- **1 API Gateway lab** (HTTP API route creation, integrations, and live request proxying to Lambda)
- **1 EventBridge application capstone** (API Gateway, Lambda, custom event bus, transformed SQS targets, notification handlers, and failure simulations)
- **2 Step Functions labs** (State Machine Choice branching workflows and Parallel execution branches)
- **2 CloudWatch labs** (custom metric filters, CloudWatch Alarms, and Log Group streams)
- **11 EC2 labs** (public/private VPC routing, stateful security groups, network ACLs, VPC endpoints, guest IMDS inspection, UserData validation, IAM instance roles, web server SG routing, broken route repair, private S3 gateway endpoints, and SSM RunCommand dispatch)
- **1 CloudFormation lab** (template validation, change set creation, stack provisioning, and resource lifecycle management)
- **Multi-SDK Code Tabs**: dynamic code generation across AWS CLI, Python `boto3`, and Terraform HCL for every step card with persistent user preference storage
- **"Run All Steps" Play-Through Runner**: automated step execution with live progress tracking, auto-scrolling, speed regulation, and contextual explanations
- Shared breadcrumb navigation from labs to the service page or dashboard homepage
- Live-state completion for reload-safe progress
- Lab-owned cleanup and reset behavior
- Completion-only next-batch recommendations after the final lab in a service batch (ordered practical progression)
- End-to-end verification against local Floci container

The EventBridge application capstone joins API Gateway, Lambda, a custom event bus, two SQS destinations, a notification Lambda, input transformation, multiple event patterns, resource permissions, correlation logs, and controlled malformed-input, missing-permission, and disabled-rule experiments.

The dashboard also exposes `/labs/`, a registry-driven directory of every service with active labs. The homepage links to it between Environment and Service Matrix.

The dashboard also exposes `/activity/`, a browser-local recent activity page for replayable or prefillable developer actions from API Gateway, EventBridge, Lambda, and SQS workbenches. Labs should continue deriving progress from live Floci state; use Activity as a companion surface for manual follow-up testing after a lab creates useful resources, not as the source of lab completion.

## Direction

Labs are one-click, local AWS workflow lessons.

Each lab teaches a real AWS-shaped workflow while proving that Floci can emulate it locally. The page shows the AWS CLI command a user would normally type, but the user can click a button to run the pre-vetted step immediately.

This removes the slow, error-prone part of learning cloud workflows: typing command after command, fixing quoting mistakes, and wondering whether the failure came from the command, the environment, or the emulator.

The experience should feel like:

1. Read the goal.
2. See the AWS CLI command.
3. Click the command's button.
4. See the real response.
5. See the step verified against local Floci state.
6. Continue one command at a time until the workflow is complete.
7. Reset the lab when done.

Labs live in the console at:

```text
/labs/
```

Open a specific lab with its service and key, for example:

```text
/labs?service=iam&lab=create-user-alice
```

## Current Proofs

The first working lab is intentionally tiny:

```bash
aws iam create-user --user-name Alice
```

The page:

- shows the command,
- runs the approved backend action on click,
- displays the response,
- verifies that `Alice` exists in local IAM,
- marks the step complete,
- remembers completion by recomputing state from Floci when the page reloads,
- provides Reset, which runs the matching cleanup operation.

This is the core pattern. Future labs should extend this pattern rather than invent a different interaction.

The second working lab proves the multi-step pattern:

```bash
aws iam create-user --user-name Alice
aws iam create-policy --policy-name AliceListBucketsPolicy --policy-document file://alice-list-buckets-policy.json
aws iam attach-user-policy --user-name Alice --policy-arn arn:aws:iam::000000000000:policy/AliceListBucketsPolicy
aws iam list-attached-user-policies --user-name Alice
```

This lab adds important behavior:

- a Labs sidebar that switches between labs,
- a policy document artifact shown beside the `file://...` command,
- multiple independent completion checks,
- a shared prerequisite step,
- reset that cleans up only the lab-owned policy and attachment,
- a completed state where buttons become green `✓ Done` status indicators.

This is now the reference pattern for new multi-step labs.

The third working lab proves programmatic credential workflows:

```bash
aws iam create-user --user-name Alice
aws iam create-access-key --user-name Alice
aws iam list-access-keys --user-name Alice
```

This lab adds a different IAM teaching point:

- access key creation returns the secret access key in the create response,
- list calls only return key metadata,
- reset can discover live key IDs instead of storing browser state,
- credential cleanup should leave the prerequisite user alone.

This is a good pattern for labs where one step returns an identifier needed later. Prefer discovering that identifier from live Floci state during reset when the API supports it.

The fourth working lab proves group membership workflows:

```bash
aws iam create-user --user-name Alice
aws iam create-group --group-name FlociDevelopers
aws iam add-user-to-group --group-name FlociDevelopers --user-name Alice
aws iam get-group --group-name FlociDevelopers
```

This lab adds another common IAM concept:

- groups collect users for shared permissions,
- membership is verified by reading the group and inspecting its `Users` list,
- reset must remove the user from the group before deleting the group,
- the shared `create-user` step should treat an existing Alice as a verified prerequisite.

This is the reference pattern for relationship-style labs: create the two sides, attach or associate them, then use a read command that proves the relationship exists.

The fifth working lab proves group-based managed permissions:

```bash
aws iam create-user --user-name Alice
aws iam create-group --group-name FlociDevelopers
aws iam add-user-to-group --group-name FlociDevelopers --user-name Alice
aws iam create-policy --policy-name FlociDevelopersListBucketsPolicy --policy-document file://floci-developers-list-buckets-policy.json
aws iam attach-group-policy --group-name FlociDevelopers --policy-arn arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy
aws iam list-attached-group-policies --group-name FlociDevelopers
```

The sixth working lab proves inline identity policies:

```bash
aws iam create-user --user-name Alice
aws iam put-user-policy --user-name Alice --policy-name AliceInlineListBuckets --policy-document file://alice-inline-list-buckets-policy.json
aws iam list-user-policies --user-name Alice
aws iam get-user-policy --user-name Alice --policy-name AliceInlineListBuckets
```

This lab makes the managed-policy versus inline-policy distinction concrete. The inline policy is embedded in Alice, has no separate policy ARN, and is removed with `delete-user-policy`.

The seventh working lab proves role trust and role permissions as separate policy concerns:

```bash
aws iam create-role --role-name FlociApplicationRole --assume-role-policy-document file://floci-application-role-trust-policy.json
aws iam get-role --role-name FlociApplicationRole
aws iam put-role-policy --role-name FlociApplicationRole --policy-name FlociApplicationListBuckets --policy-document file://floci-application-list-buckets-policy.json
aws iam get-role-policy --role-name FlociApplicationRole --policy-name FlociApplicationListBuckets
```

The eighth working lab proves STS session policies on assumed-role credentials:

```bash
aws iam create-role --role-name FlociStsSessionRole --assume-role-policy-document file://floci-sts-role-trust-policy.json
aws iam put-role-policy --role-name FlociStsSessionRole --policy-name FlociStsListBuckets --policy-document file://floci-sts-list-buckets-policy.json
aws sts assume-role --role-arn arn:aws:iam::000000000000:role/FlociStsSessionRole --role-session-name floci-session-policy-lab --policy file://session-policy.json
```

This lab makes the Floci 1.5.30 IAM/STS improvements concrete:

- The role trust policy allows the local account root principal to call `sts:AssumeRole`.
- The role permissions policy grants the base permissions available to the role.
- The STS session policy is supplied during `AssumeRole` and can further constrain the temporary credentials.
- Reset deletes the inline role policy, deletes `FlociStsSessionRole`, and clears the recorded temporary-session verification marker.

The ninth working lab proves EC2 instance-profile wiring:

```bash
aws iam create-role --role-name FlociEc2Role --assume-role-policy-document file://floci-ec2-role-trust-policy.json
aws iam create-instance-profile --instance-profile-name FlociEc2InstanceProfile
aws iam add-role-to-instance-profile --instance-profile-name FlociEc2InstanceProfile --role-name FlociEc2Role
aws iam get-instance-profile --instance-profile-name FlociEc2InstanceProfile
aws iam list-instance-profiles-for-role --role-name FlociEc2Role
```

## Product Positioning

Use this language:

> Local AWS workflow labs

The value proposition:

- Learn AWS-shaped workflows quickly.
- See exact CLI commands.
- Run known-good steps with one click.
- Confirm the result in Floci and in the dashboard.
- Build confidence before wiring the workflow into an app or deployment.

Labs are not a browser terminal. They are guided, approved workflows.

## What We Show

Show commands without endpoint flags:

```bash
aws iam create-user --user-name Alice
```

Do not show:

```bash
aws --endpoint-url http://127.0.0.1:4566 iam create-user --user-name Alice
```

The endpoint belongs to the local Floci/dashboard configuration. The lab page should teach the command shape, not distract with environment plumbing.

In the Docker-first runtime, the dashboard container talks to Floci at `http://floci:4566`; when Django is run directly on the host, the equivalent endpoint is usually `http://localhost:4566`. Lab copy should avoid naming either endpoint unless the lesson is specifically about endpoint behavior.

## Interaction Model

Each lab contains steps.

Each step has:

- title,
- visible CLI command,
- Run button,
- explanation,
- response panel,
- verification result,
- complete state.

Each lab has:

- title,
- description,
- status,
- ordered steps,
- reset action.

The Run button should be one click per CLI command. As labs grow, completing a lab means clicking multiple Run buttons in order.

When a step is verified, its Run button becomes a completion badge:

```text
✓ Done
```

This badge should be green, disabled, and should not change appearance on hover. It should not feel clickable.

If a step has a supporting file-like artifact, show it near the command. For example, a command may show:

```bash
aws iam create-policy --policy-name AliceListBucketsPolicy --policy-document file://alice-list-buckets-policy.json
```

The page should also show `alice-list-buckets-policy.json` as an expandable artifact containing the JSON that the approved backend action will use.

## Status-Aware Labs

Lab state should be derived from live Floci state whenever possible.

Example:

- If `Alice` exists, the IAM create-user step is complete.
- If `Alice` does not exist, the step is not started.
- Reset deletes `Alice`, so the step becomes runnable again.
- If `AliceListBucketsPolicy` exists, the policy creation step is complete.
- If that policy is attached to Alice, both the attach step and the list-attached-policies read step are complete.
- If `FlociDevelopers` exists, the group creation step is complete.
- If `get-group` returns Alice in `Users`, both the add-user-to-group step and get-group read step are complete.
- If `FlociDevelopersListBucketsPolicy` exists, the group policy creation step is complete.
- If `list-attached-group-policies` returns `FlociDevelopersListBucketsPolicy`, both the attach-group-policy step and list-attached-group-policies read step are complete.
- If `get-user-policy` returns `AliceInlineListBuckets`, the put, list, and get inline-policy steps are complete.
- If `FlociStsSessionRole` exists with the local account-root trust policy, `FlociStsListBuckets` is attached, and the lab has recorded a successful `AssumeRole` response with temporary credentials, the STS session policy lab is complete.

This matters because users will move between the lab page and the normal service dashboard. If they create a resource in a lab and then inspect it elsewhere, returning to the lab should reflect reality.

When a lab naturally leads into manual testing, link or describe the matching service workbench instead of trying to write browser-local Activity entries from the server-side lab runner. For example, the API Gateway to Lambda lab can send its own verification request, while later manual request tests from the API Gateway workbench populate `/activity/` for replay/prefill.

Prefer this order:

1. Verify from live Floci state.
2. Use stored run state only for things that cannot be safely inferred.
3. Use browser state only for display details like expanded panels.

## Execution Model

The browser must not send arbitrary shell text to the server.

The browser sends identifiers:

```json
{
  "service": "iam",
  "lab": "create-user-alice",
  "step": "create-user"
}
```

The backend looks up an approved lab step and runs its implementation.

The command displayed to the user is educational and exact in intent:

```bash
aws iam create-user --user-name Alice
```

The first implementation should continue using boto3-backed actions behind the scenes. This proves the AWS-compatible API behavior through Floci without exposing shell execution.

We can revisit actual AWS CLI subprocess execution later, but it is not needed for the product value right now.

## Response Shape

Step run responses should be consistent:

```json
{
  "service": "iam",
  "lab": "create-user-alice",
  "step": "create-user",
  "command": "aws iam create-user --user-name Alice",
  "exit_code": 0,
  "stdout": "{...}",
  "stderr": "",
  "json": {},
  "duration_ms": 82,
  "verified": true,
  "verification": {
    "status": "passed",
    "message": "User Alice exists in local IAM."
  }
}
```

For multi-step labs, every step should return its own command, response, and verification. The lab page should not assume that one completed step means the whole lab is complete.

Example policy attachment verification:

```json
{
  "service": "iam",
  "lab": "attach-policy-alice",
  "step": "attach-policy",
  "command": "aws iam attach-user-policy --user-name Alice --policy-arn arn:aws:iam::000000000000:policy/AliceListBucketsPolicy",
  "exit_code": 0,
  "stdout": "{}",
  "stderr": "",
  "json": {},
  "duration_ms": 74,
  "verified": true,
  "verification": {
    "status": "passed",
    "message": "Policy AliceListBucketsPolicy is attached to Alice."
  }
}
```

Reset responses should be similarly consistent:

```json
{
  "service": "iam",
  "lab": "create-user-alice",
  "command": "aws iam delete-user --user-name Alice",
  "exit_code": 0,
  "stdout": "{}",
  "stderr": "",
  "json": {},
  "duration_ms": 61,
  "reset": true,
  "deleted": true,
  "verification": {
    "status": "passed",
    "message": "User Alice was removed."
  }
}
```

## Recommended Code Structure

The lab implementation has moved from the original single `dashboard/labs.py` module into a `dashboard/labs/` package. The package keeps the public `dashboard.labs` import path stable.

Current state:

```text
dashboard/labs/
  __init__.py
    compatibility export: dashboard.labs is the monolith module object
  monolith.py
    labs_for_service(...), get_lab(...), next_lab_batch(...), LAB_BATCH_ORDER
    run_lab_step(...), lab_status(...), reset_lab(...) dispatch to service modules
    shared helpers (boto3 clients, _json_text, _error_code, _verify_role, ...)
    re-exports every service module name so dashboard.labs.<name> keeps working
  <service>_labs.py
    one module per service (iam, s3, sqs, sns, scheduler, kms, ssm,
    secretsmanager, dynamodb, lambda, apigateway, ec2, cloudformation,
    stepfunctions, cognito, cloudwatch, rds, ecs, eks) with LABS,
    step runners, verifiers, run_step(...), status(...), and reset(...)
  eventbridge_application.py, ec2_guided.py
    EventBridge application spine and guided EC2 labs
  registry.py
    all_labs(...) plus registry re-exports
  runner.py
    run_lab_step(...), lab_status(...), reset_lab(...) facade
  types.py
    typed dictionaries for Lab, LabStep, StepResult, ResetResult, and verification payloads
```

When a test patches a function that a service module calls, patch it where it is looked up, for example `patch('dashboard.labs.kms_labs._verify_kms_lab_key')`. Boto3 clients come from the monolith helpers, so `patch('dashboard.labs.FlociClientFactory')` still covers every service module.

Keep service-specific implementation in service files. Keep generic lab routing and response formatting in common files.

## Lab Definition Shape

Use a declarative definition for display, plus explicit Python functions for behavior.

Example:

```python
Lab(
    service="iam",
    key="create-user-alice",
    title="Create an IAM user",
    description="Create a local IAM user named Alice and verify that IAM can read it back.",
    steps=[
        LabStep(
            key="create-user",
            title="Create user Alice",
            command="aws iam create-user --user-name Alice",
            explanation="Creates a local IAM user named Alice through the IAM CreateUser API.",
            run=run_create_user_alice,
            verify=verify_user_alice,
        ),
    ],
    reset=reset_create_user_alice,
)
```

The important split:

- `command` is what the user sees.
- `run` is the approved backend implementation.
- `verify` checks live Floci state.
- `reset` cleans up lab-created resources.

## Adding A New Lab

Each new lab should follow this checklist:

1. Define the lab and step metadata.
2. Implement one run function per step.
3. Implement one verification function per step.
4. Implement reset for all lab-created resources.
5. Make status recomputable from live Floci state.
6. Add page tests for visible commands and complete state.
7. Add runner tests for each step.
8. Add reset tests.
9. Run the step against local Floci and confirm the resource appears in the normal service dashboard.
10. Reset local resources after verification.

No lab should be considered done until the displayed command has been verified against local Floci behavior.

For each command, verify both paths:

- the direct lab endpoint returns `verified: true`,
- the normal service dashboard reflects the resource or state change.

After local verification, reset the lab so the page is ready for a fresh user run.

## Verification Pattern

Every step should answer:

```text
Did the intended resource or state change actually happen?
```

Examples:

- IAM user step: `get_user(UserName="Alice")` returns Alice.
- IAM customer managed policy step: `get_policy(PolicyArn=...)` returns the policy.
- IAM policy attachment step: `list_attached_user_policies` includes the policy ARN.
- IAM access key step: `list_access_keys(UserName="Alice")` returns at least one key.
- S3 bucket step: `head_bucket` succeeds.
- SQS send-message step: `receive_message` returns the expected body.
- Scheduler target step: target queue receives the scheduled message.

Verification should be independent from the original API call succeeding.

## Reset Pattern

Reset should be boring and reliable.

Rules:

- Reset should clean up only known lab resources.
- Reset should tolerate already-missing resources.
- Reset should run cleanup in dependency order.
- Reset should return a visible response.
- Reset should update the page back to a runnable state.

For multi-step labs, reset may need multiple cleanup operations. It can still appear as one Reset button for the lab.

Reset ownership matters:

- The create-user lab owns `Alice`, so its reset deletes `Alice`.
- The attach-policy lab depends on `Alice`, but owns `AliceListBucketsPolicy` and the attachment. Its reset should detach/delete the policy but leave Alice alone.
- The access-key lab depends on `Alice`, but owns Alice access keys in this controlled local workflow. Its reset should delete Alice access keys but leave Alice alone.
- The group membership lab depends on `Alice`, but owns `FlociDevelopers` and Alice's membership in that group. Its reset should remove Alice from the group, delete the group, and leave Alice alone.
- The group policy lab depends on `Alice`, but owns `FlociDevelopers`, `FlociDevelopersListBucketsPolicy`, the group policy attachment, and Alice's membership in that group. Its reset should detach/delete the policy, remove Alice from the group, delete the group, and leave Alice alone.
- The inline policy lab depends on `Alice`, but owns `AliceInlineListBuckets`. Its reset should delete the inline policy and leave Alice alone.
- The role trust lab owns `FlociApplicationRole` and `FlociApplicationListBuckets`. Its reset should delete the inline role policy before deleting the role.
- The STS session policy lab owns `FlociStsSessionRole` and `FlociStsListBuckets`. Its reset should delete the inline role policy, delete the role, and clear the recorded temporary-session marker.
- The instance profile lab owns `FlociEc2Role` and `FlociEc2InstanceProfile`. Its reset should remove the role from the profile, delete the profile, then delete the role.

This rule prevents a later lab from unexpectedly destroying a prerequisite that may have been created by an earlier lab or by the user.

For resources without tags or names, be explicit about ownership. IAM access keys cannot be tagged or named at creation, so the Alice access-key lab treats Alice's local access keys as lab-owned and deletes them during reset. That is acceptable here because this is a fixed local training user; use more isolated names for broader or riskier workflows.

## Naming

Use simple names in early labs when the teaching value is clarity:

```text
Alice
```

Use `lab-*` names when a lab creates multiple resources or resources that are more likely to conflict:

```text
lab-policy-user
lab-readonly-s3
lab-instance-profile
```

For labs with fixed names, status detection must handle resources that already exist.

## Safety

Keep these guardrails:

- Do not execute arbitrary commands from the browser.
- Do not accept free-form shell syntax.
- Do not run pipes, redirects, command separators, or subshells.
- Only run registered service/lab/step combinations.
- Reuse `FlociClientFactory` endpoint validation.
- Refuse non-local endpoints while allowing both host-local Floci URLs and the Compose service URL.
- Prefer boto3-backed actions until there is a strong reason to run AWS CLI subprocesses.
- Cleanup only lab-owned resources.

## UI Direction

Labs pages should be focused.

Do not show the standard health/status tile block on labs pages. It distracts from the workflow.

Keep:

- back link to service page,
- service eyebrow,
- lab title,
- lab list,
- command steps,
- response/verification,
- reset.

Future UI improvements:

- step numbers connected by a vertical progress line,
- disabled future steps until prior steps verify,
- compact resource snapshot after each step,
- "View in dashboard" link after verification,
- richer empty/complete states,
- multiple labs in the sidebar.

Current UI lessons:

- Labs pages should not include the standard health/status tiles.
- Lab selection should be link-based so a lab has a direct URL, such as `?lab=attach-policy-alice`.
- Completed steps should look complete, not clickable.
- Showing the supporting policy JSON made the `file://...` command understandable without requiring a real local file.
- Do not show broad learning paths on every lab page. When the final lab in a service batch is complete, show a concise next-batch recommendation instead.

## First IAM Lab Set

Build IAM labs in this order:

1. Create a local admin user
2. Create user Alice
3. Create managed policy and attach it to Alice
4. Create user and access key
5. Create group and add user
6. Attach managed policy to group
7. Create user and attach inline policy
8. Create role with trust policy
9. Assume a role with an STS session policy
10. Create instance profile and add role
11. Switch identities and verify IAM enforcement

The first lab teaches the responsible AWS habit: use bootstrap/root-like credentials only long enough to create an administrator identity, then do daily work as that user. The second lab proves the core one-step user pattern. The third lab proves the multi-step pattern and the core IAM permissions story. The fourth lab proves credential creation, metadata listing, and live-state cleanup for generated IDs. The fifth proves group membership and relationship verification. The sixth proves group-based permission assignment. The seventh proves inline user policies. The eighth separates role trust from role permissions. The ninth proves STS session policies on assumed-role credentials. The tenth proves EC2 instance-profile wiring. The eleventh proves that switched dashboard credentials experience real allow and deny outcomes.

Implemented second lab:

```text
Attach a managed policy to Alice
```

Steps:

```bash
aws iam create-user --user-name Alice
aws iam create-policy --policy-name AliceListBucketsPolicy --policy-document file://alice-list-buckets-policy.json
aws iam attach-user-policy --user-name Alice --policy-arn arn:aws:iam::000000000000:policy/AliceListBucketsPolicy
aws iam list-attached-user-policies --user-name Alice
```

Reset:

```bash
aws iam detach-user-policy --user-name Alice --policy-arn arn:aws:iam::000000000000:policy/AliceListBucketsPolicy
aws iam delete-policy --policy-arn arn:aws:iam::000000000000:policy/AliceListBucketsPolicy
```

Implemented third lab:

```text
Create user and access key
```

Steps:

```bash
aws iam create-user --user-name Alice
aws iam create-access-key --user-name Alice
aws iam list-access-keys --user-name Alice
```

Reset:

```bash
aws iam delete-access-key --user-name Alice --access-key-id <created-key-id>
```

This introduces dependency between steps and shows why live-state discovery matters: deleting an access key requires the created access key ID, and reset can discover that ID with `list-access-keys`. This lab's reset removes Alice access keys but leaves Alice in place.

Implemented fourth lab:

```text
Create group and add Alice
```

Steps:

```bash
aws iam create-user --user-name Alice
aws iam create-group --group-name FlociDevelopers
aws iam add-user-to-group --group-name FlociDevelopers --user-name Alice
aws iam get-group --group-name FlociDevelopers
```

Why this should come next:

- groups are a core IAM concept for AWS learning and certification,
- it reuses Alice without making permissions more complex yet,
- it introduces membership state and a new reset dependency order,
- it prepares naturally for a later group policy attachment lab.

Reset should remove Alice from `FlociDevelopers`, then delete `FlociDevelopers`, and leave Alice alone.

Implemented fifth lab:

```text
Attach a policy to a group
```

Steps:

```bash
aws iam create-user --user-name Alice
aws iam create-group --group-name FlociDevelopers
aws iam add-user-to-group --group-name FlociDevelopers --user-name Alice
aws iam create-policy --policy-name FlociDevelopersListBucketsPolicy --policy-document file://floci-developers-list-buckets-policy.json
aws iam attach-group-policy --group-name FlociDevelopers --policy-arn arn:aws:iam::000000000000:policy/FlociDevelopersListBucketsPolicy
aws iam list-attached-group-policies --group-name FlociDevelopers
```

Why this should come next:

- it combines the permissions story from the managed policy lab with the group membership story,
- group-based permissions are a common AWS real-world pattern,
- it prepares learners for least-privilege design without introducing roles yet.

Reset should detach the group policy, delete the policy, remove Alice from the group, delete the group, and leave Alice alone.

Implemented sixth lab:

```text
Attach an inline policy to Alice
```

Steps:

```bash
aws iam create-user --user-name Alice
aws iam put-user-policy --user-name Alice --policy-name AliceInlineListBuckets --policy-document file://alice-inline-list-buckets-policy.json
aws iam list-user-policies --user-name Alice
aws iam get-user-policy --user-name Alice --policy-name AliceInlineListBuckets
```

Why this should come next:

- it teaches the difference between customer managed policies and inline policies,
- inline policy questions show up often in IAM learning and certification prep,
- reset is straightforward: delete the inline policy and leave Alice alone.

Reset should delete `AliceInlineListBuckets` from Alice and leave Alice in place.

Implemented seventh lab:

```text
Create a role with a trust policy
```

Steps:

```bash
aws iam create-role --role-name FlociApplicationRole --assume-role-policy-document file://floci-application-role-trust-policy.json
aws iam get-role --role-name FlociApplicationRole
aws iam put-role-policy --role-name FlociApplicationRole --policy-name FlociApplicationListBuckets --policy-document file://floci-application-list-buckets-policy.json
aws iam get-role-policy --role-name FlociApplicationRole --policy-name FlociApplicationListBuckets
```

This lab teaches the most important role distinction:

- The trust policy answers who or what may assume the role.
- The permissions policy answers what an assumed role may do.

Reset deletes the inline role policy before deleting `FlociApplicationRole`.

Implemented eighth lab:

```text
Assume a role with an STS session policy
```

Steps:

```bash
aws iam create-role --role-name FlociStsSessionRole --assume-role-policy-document file://floci-sts-role-trust-policy.json
aws iam put-role-policy --role-name FlociStsSessionRole --policy-name FlociStsListBuckets --policy-document file://floci-sts-list-buckets-policy.json
aws sts assume-role --role-arn arn:aws:iam::000000000000:role/FlociStsSessionRole --role-session-name floci-session-policy-lab --policy file://session-policy.json
```

This lab teaches that STS `AssumeRole` combines the role trust policy, the role permissions policy, and optional session policies on the temporary credentials.

Reset deletes `FlociStsListBuckets`, deletes `FlociStsSessionRole`, and clears the ephemeral session verification marker.

Implemented ninth lab:

```text
Create an EC2 instance profile
```

Steps:

```bash
aws iam create-role --role-name FlociEc2Role --assume-role-policy-document file://floci-ec2-role-trust-policy.json
aws iam create-instance-profile --instance-profile-name FlociEc2InstanceProfile
aws iam add-role-to-instance-profile --instance-profile-name FlociEc2InstanceProfile --role-name FlociEc2Role
aws iam get-instance-profile --instance-profile-name FlociEc2InstanceProfile
aws iam list-instance-profiles-for-role --role-name FlociEc2Role
```

This lab teaches that an instance profile is the IAM container EC2 uses to expose a role to an instance. The role and profile use dedicated names so this lab can reset independently from the application-role lab.

Reset removes the role from the profile, deletes the profile, then deletes `FlociEc2Role`.

Implemented tenth lab:

```text
Switch identities and verify IAM enforcement
```

Steps:

```bash
aws iam create-user --user-name Charlie
aws s3 ls
aws iam create-role --role-name CharlieSqsReadRole --assume-role-policy-document file://charlie-sqs-read-role-trust-policy.json
aws iam put-role-policy --role-name CharlieSqsReadRole --policy-name CharlieSqsRead --policy-document file://charlie-sqs-read-policy.json
aws sts assume-role --role-arn arn:aws:iam::000000000000:role/CharlieSqsReadRole --role-session-name floci-enforcement-lab
aws sqs list-queues
aws s3 ls
```

This lab teaches the practical debugging loop IAM is meant to support: create a low-privilege user, verify an unrelated service call is denied, create a role with a narrow SQS permission, assume that role as the user, verify SQS succeeds, and verify S3 still fails. It ties together identity switching, trust policy evaluation, role permissions, and the difference between an expected denial and a broken workflow.

Reset deletes `CharlieSqsReadRole`, its inline policy, Charlie's access keys, Charlie's baseline identity policy, the Charlie user, and the recorded enforcement markers.

## IAM Coverage

The IAM lab collection now covers most practical core workflows supported by Floci:

- users,
- programmatic access keys,
- customer managed policies,
- inline user policies,
- groups and membership,
- group-based permissions,
- roles,
- service trust policies,
- inline role permissions,
- STS AssumeRole with session policies,
- EC2 instance profiles,
- switched user and assumed-role credentials,
- positive and negative IAM enforcement checks,
- live resource verification,
- dependency-aware cleanup.

The workbench exposes a policy simulation panel for users and roles. Keep policy simulation as a workbench diagnostic unless local Floci supports enough evaluation detail to make a lab truly useful:

```bash
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::000000000000:role/FlociApplicationRole --action-names s3:ListAllMyBuckets
```

As of July 5, 2026, local Floci still returns:

```text
UnsupportedOperation: Operation SimulatePrincipalPolicy is not supported.
```

`SimulateCustomPolicy` is also unsupported. Do not add a policy-simulation lab until one of these APIs works end to end and can teach more than the existing enforcement capstone. When support lands, add one lab that demonstrates both an allowed action and an implicitly denied action with clear evaluation output.

With that exception, the IAM lab set has covered most everyday IAM use cases that Floci currently supports. Future IAM labs should be driven by newly supported APIs or a clearly valuable certification scenario, not by adding more variations of the same resource operations.

## S3 Lab Progression

S3 should move from the basic storage model into protection, controlled access, and automation. The progression should cover the workflows learners see most often in applications, operations work, and AWS certification material.

Build S3 labs in this order:

1. Create and inspect a bucket.
2. Upload, list, download, and delete an object.
3. Organize keys with prefixes and copy objects.
4. Add object metadata and tags.
5. Enable versioning and recover an earlier version.
6. Generate a presigned URL.
7. Block public access and apply a bucket policy.
8. Enable default bucket encryption.
9. Apply a lifecycle rule.
10. Configure CORS and event notifications.
11. Complete a multipart upload.

This sequence deliberately teaches the S3 data model before its management features:

- Buckets are regional containers with globally shaped names.
- Objects are addressed by keys, and folders are key prefixes rather than real directories.
- Metadata and tags support application behavior, inventory, cost allocation, and lifecycle selection.
- Versioning protects against overwrite and deletion mistakes.
- Presigned URLs grant temporary object access without distributing AWS credentials.
- Public-access blocks and bucket policies teach resource-based access and defense in depth.
- Default encryption teaches data-at-rest controls.
- Lifecycle rules teach cost and retention automation.
- CORS and notifications connect S3 to browser and event-driven architectures.
- Multipart upload teaches the standard large-object workflow.

Before implementing each lab, verify every command against local Floci. The capability baseline for this sequence includes working support for:

- object upload and download,
- object copy,
- object tagging,
- versioning and object-version listing,
- public-access blocks,
- SSE-S3 default encryption,
- CORS,
- lifecycle configuration,
- presigned URLs,
- multipart upload APIs.

Notification configuration is represented in the dashboard and API. Its eventual lab should verify both saved configuration and actual event delivery to a supported local destination rather than stopping at configuration storage.

## First S3 Lab

Implemented:

```text
Create and inspect an S3 bucket
```

Steps:

```bash
aws s3api create-bucket --bucket lab-basics
aws s3api head-bucket --bucket lab-basics
aws s3api list-buckets
```

This is intentionally a three-command foundation:

- `create-bucket` creates the storage container.
- `head-bucket` proves the bucket exists and is accessible.
- `list-buckets` shows account-level discovery and the bucket creation timestamp.

Live status is derived from `HeadBucket`, so revisiting the lab reflects the real Floci state. Creation is idempotent when the fixed lab bucket already exists.

Reset owns the entire `lab-basics` bucket. It removes ordinary objects, object versions, and delete markers before deleting the bucket. This keeps reset reliable even if a learner uses the normal S3 dashboard to experiment inside the lab bucket.

Implemented second S3 lab:

```text
Upload and retrieve an object
```

Steps:

```bash
aws s3api create-bucket --bucket lab-objects
aws s3api put-object --bucket lab-objects --key hello.txt --body hello.txt --content-type text/plain
aws s3api list-objects-v2 --bucket lab-objects
aws s3api head-object --bucket lab-objects --key hello.txt
aws s3api get-object --bucket lab-objects --key hello.txt downloaded-hello.txt
```

The page shows `hello.txt` as an expandable artifact containing:

```text
Hello from the Floci S3 lab!
```

The backend uploads those exact bytes and verifies:

- bucket accessibility,
- object key,
- downloaded body,
- `text/plain` content type,
- 29-byte content length.

The delete-object command is intentionally not a lab step. If the final learning step deleted the object, a later page load could not distinguish a successfully completed workflow from one that had never started. Reset owns deletion of the object and bucket, preserving live-state completion while still teaching reliable cleanup.

The `get-object` response displays both the destination name `downloaded-hello.txt` and the downloaded text body. This preserves the AWS CLI command shape without writing an arbitrary file on the dashboard server.

Implemented third S3 lab:

```text
Organize and copy objects with key prefixes
```

Steps:

```bash
aws s3api create-bucket --bucket lab-prefixes
aws s3api put-object --bucket lab-prefixes --key incoming/report.txt --body report.txt
aws s3api list-objects-v2 --bucket lab-prefixes --prefix incoming/
aws s3api copy-object --copy-source lab-prefixes/incoming/report.txt --bucket lab-prefixes --key archive/report.txt
aws s3api list-objects-v2 --bucket lab-prefixes --prefix archive/
```

This should teach that S3 has a flat object namespace and that apparent folders are key prefixes. Reset should remove both keys and the bucket.

The page shows `report.txt` as an expandable artifact. The backend uploads its exact 46 bytes to `incoming/report.txt`, then uses `CopyObject` to create `archive/report.txt`.

Live status is intentionally split:

- the upload and incoming-prefix list steps complete when `incoming/report.txt` contains the expected artifact,
- the copy and archive-prefix list steps complete only when `archive/report.txt` contains the same expected artifact.

The verified objects have the same ETag and size, demonstrating that copy creates another object key while preserving the source. S3 does not create real `incoming` or `archive` directories; the slash-separated names are keys that the console presents hierarchically.

Implemented fourth S3 lab:

```text
Add object metadata and tags
```

Steps:

```bash
aws s3api create-bucket --bucket lab-metadata
aws s3api put-object --bucket lab-metadata --key documents/invoice.txt --body invoice.txt --content-type text/plain --metadata project=floci,classification=internal
aws s3api head-object --bucket lab-metadata --key documents/invoice.txt
aws s3api put-object-tagging --bucket lab-metadata --key documents/invoice.txt --tagging file://invoice-tags.json
aws s3api get-object-tagging --bucket lab-metadata --key documents/invoice.txt
```

This should teach the difference between HTTP/system metadata, user-defined object metadata, and object tags. Tags should use practical keys such as `Environment=lab` and `CostCenter=training`.

The uploaded object is `documents/invoice.txt`, with:

- exact 34-byte invoice contents,
- `Content-Type: text/plain`,
- user metadata `project=floci`,
- user metadata `classification=internal`,
- tag `Environment=lab`,
- tag `CostCenter=training`.

Live status is layered:

- upload and `head-object` complete only when the body, content type, size, and user metadata match,
- tagging steps complete only when the full expected tag set is present.

Tag order is ignored during verification because S3 tag responses should be treated as a set of key/value pairs.

The normal S3 object drawer now shows user metadata alongside system metadata and its existing editable tags view. This makes the distinction visible outside the lab:

- system metadata describes how S3 stores and serves the object,
- user metadata travels with the object and is set during write/copy operations,
- tags are a separate mutable classification layer used by lifecycle, cost allocation, and automation.

Implemented fifth S3 lab:

```text
Enable versioning and recover an earlier object version
```

Steps:

```bash
aws s3api create-bucket --bucket lab-versioning
aws s3api put-bucket-versioning --bucket lab-versioning --versioning-configuration Status=Enabled
aws s3api put-object --bucket lab-versioning --key configuration.txt --body configuration-v1.txt
aws s3api put-object --bucket lab-versioning --key configuration.txt --body configuration-v2.txt
aws s3api list-object-versions --bucket lab-versioning --prefix configuration.txt
aws s3api get-object --bucket lab-versioning --key configuration.txt --version-id <v1-version-id> recovered-configuration.txt
```

The backend should discover version IDs from live Floci state rather than persisting them in browser state. Verification should prove that the latest version contains v2 while retrieving the older version returns v1.

The lab uses two explicit artifacts:

```text
configuration-v1.txt
feature_enabled=false
release=v1
```

```text
configuration-v2.txt
feature_enabled=true
release=v2
```

Floci assigns opaque version IDs. The lab never assumes list order or stores those IDs in browser state. It lists the live versions, downloads each candidate by version ID, and identifies v1 and v2 by their exact bodies.

Completion requires:

- bucket versioning status `Enabled`,
- a preserved version containing v1,
- a latest version containing v2,
- successful retrieval of the discovered v1 version.

The normal S3 dashboard exposes the same state through its versioned-bucket count and `Show versions` table. Reset deletes every version and delete marker before deleting the bucket.

Implemented sixth S3 lab:

```text
Generate temporary access with a presigned URL
```

Steps:

```bash
aws s3api create-bucket --bucket lab-presigned
aws s3api put-object --bucket lab-presigned --key shared/guide.txt --body guide.txt --content-type text/plain
aws s3 presign s3://lab-presigned/shared/guide.txt --expires-in 300
```

The lab should verify the object first, generate a five-minute URL, and perform an HTTP GET against that URL to prove the temporary access actually returns the expected bytes. Explain that presigned URLs use the signer identity's permissions and should be treated as bearer credentials until expiration.

The final step does more than return a URL:

1. Generate a URL with `ExpiresIn=300`.
2. Perform an ordinary HTTP GET against the signed URL.
3. Send no AWS Authorization header or credentials with that GET.
4. Verify the response is the exact 43-byte `guide.txt` artifact.

The step response shows the URL, five-minute expiry, and downloaded body. Live completion regenerates and redeems a fresh URL from the current object state, so returning to the page proves the workflow still works rather than trusting an expired browser value.

Security lessons:

- A presigned URL grants only the operation it was signed for.
- Its effective permissions come from the signing identity.
- The URL acts as a bearer credential until it expires.
- URLs should not be logged, committed, or shared more broadly than intended.
- Short expirations reduce exposure but do not revoke a URL immediately.

Implemented seventh S3 lab:

```text
Block public access and apply a bucket policy
```

Steps:

```bash
aws s3api create-bucket --bucket lab-security
aws s3api put-public-access-block --bucket lab-security --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api get-public-access-block --bucket lab-security
aws s3api put-bucket-policy --bucket lab-security --policy file://bucket-policy.json
aws s3api get-bucket-policy --bucket lab-security
```

Use a non-public least-privilege policy, such as allowing the local account root to list the bucket. The lab should explain that public-access blocking and bucket policies are separate defense layers and that enabling `BlockPublicPolicy` intentionally rejects public policies.

The lab enables all four safeguards:

- `BlockPublicAcls`,
- `IgnorePublicAcls`,
- `BlockPublicPolicy`,
- `RestrictPublicBuckets`.

Its policy grants only `s3:ListBucket` on `arn:aws:s3:::lab-security` to `arn:aws:iam::000000000000:root`. The policy contains no wildcard principal and grants no object actions.

Live verification treats the two security layers independently:

- the block steps require the exact four-boolean configuration,
- the policy steps require the exact principal, action, effect, and resource.

The normal S3 Permissions tab displays both JSON configurations.

Floci capability boundary as of June 18, 2026:

- Floci stores and returns the bucket policy.
- Floci stores and returns Public Access Block configuration.
- The dashboard reports that bucket policies are not currently enforced on object access.

Therefore, this lab proves configuration workflows and teaches the AWS policy model, but it must not claim to prove an authorization denial. Add allow/deny access tests only when Floci begins enforcing S3 bucket policies.

Reset removes the policy and Public Access Block configuration before deleting the bucket.

Implemented eighth S3 lab:

```text
Enable default bucket encryption
```

Steps:

```bash
aws s3api create-bucket --bucket lab-encryption
aws s3api put-bucket-encryption --bucket lab-encryption --server-side-encryption-configuration file://encryption.json
aws s3api get-bucket-encryption --bucket lab-encryption
aws s3api put-object --bucket lab-encryption --key protected/record.txt --body record.txt
aws s3api head-object --bucket lab-encryption --key protected/record.txt
```

Start with SSE-S3 using `AES256`. Verify both the bucket default-encryption configuration and the uploaded object's server-side-encryption response metadata.

The lab configures:

```json
{
  "Rules": [
    {
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      },
      "BucketKeyEnabled": false
    }
  ]
}
```

Live completion requires:

- the exact bucket default-encryption configuration,
- the exact 44-byte `protected/record.txt` artifact,
- `text/plain` content metadata,
- the encryption default to remain enabled when the object is inspected.

The normal S3 Properties tab displays the AES256 configuration.

Floci capability boundary as of June 18, 2026:

- Floci stores and returns the bucket encryption configuration.
- Objects can be written and read after the default is enabled.
- `PutObject` and `HeadObject` do not currently return a `ServerSideEncryption` field for objects protected by the bucket default.

Therefore, the lab proves the configuration workflow and object creation after activation, but does not claim per-object encryption-header verification.

Reset deletes the bucket encryption configuration, object, and bucket.

Implemented ninth S3 lab:

```text
Automate retention with an S3 lifecycle rule
```

Steps:

```bash
aws s3api create-bucket --bucket lab-lifecycle
aws s3api put-object --bucket lab-lifecycle --key logs/app.log --body app.log --content-type text/plain
aws s3api put-bucket-lifecycle-configuration --bucket lab-lifecycle --lifecycle-configuration file://lifecycle.json
aws s3api get-bucket-lifecycle-configuration --bucket lab-lifecycle
```

Use an enabled rule scoped to the `logs/` prefix that expires objects after 30 days. Explain that lifecycle actions are asynchronous in AWS and the lab verifies configuration, not the passage of 30 days.

The lifecycle artifact is:

```json
{
  "Rules": [
    {
      "ID": "ExpireApplicationLogsAfter30Days",
      "Status": "Enabled",
      "Filter": {
        "Prefix": "logs/"
      },
      "Expiration": {
        "Days": 30
      }
    }
  ]
}
```

Live completion requires:

- the lifecycle bucket to exist,
- the exact `logs/app.log` artifact with `text/plain` metadata,
- the named rule to remain enabled,
- the rule to target `logs/`,
- expiration to remain set to 30 days.

Reset removes the lifecycle configuration before deleting the object and bucket.

Implemented tenth S3 lab:

```text
Configure bucket CORS
```

Steps:

```bash
aws s3api create-bucket --bucket lab-cors
aws s3api put-bucket-cors --bucket lab-cors --cors-configuration file://cors.json
aws s3api get-bucket-cors --bucket lab-cors
```

The rule allows only `GET` and `HEAD` from `http://localhost:3000`, permits the `Authorization` request header, exposes `ETag`, and caches browser preflight results for 3600 seconds. It deliberately avoids a wildcard origin.

Live completion requires the bucket and exact named CORS rule to remain present. Reset deletes the CORS configuration before deleting the bucket.

Implemented eleventh S3 lab:

```text
Send S3 object-created events to SQS
```

Steps:

```bash
aws sqs create-queue --queue-name lab-s3-events
aws sqs set-queue-attributes --queue-url <queue-url> --attributes file://queue-attributes.json
aws s3api create-bucket --bucket lab-notifications
aws s3api put-bucket-notification-configuration --bucket lab-notifications --notification-configuration file://notification.json
aws s3api get-bucket-notification-configuration --bucket lab-notifications
aws s3api put-object --bucket lab-notifications --key uploads/report.txt --body report.txt --content-type text/plain
aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1
```

The queue policy grants `sqs:SendMessage` only to the S3 service, constrained to `arn:aws:s3:::lab-notifications` and local account `000000000000`. The bucket notification selects `s3:ObjectCreated:*` and targets the dedicated queue ARN.

Live completion requires:

- the queue and expected ARN,
- the exact S3 delivery queue policy,
- the source bucket,
- the saved object-created notification configuration,
- the exact `uploads/report.txt` artifact,
- an actual SQS message containing an `aws:s3` `ObjectCreated:*` record for that bucket and key.

The receive step uses a zero-second visibility timeout so the event remains available for live-state recomputation after page reloads.

Reset removes the notification configuration first, then deletes bucket contents and the bucket, and finally deletes the queue.

Implemented twelfth S3 lab:

```text
Complete a multipart upload
```

Steps:

```bash
aws s3api create-bucket --bucket lab-multipart
aws s3api create-multipart-upload --bucket lab-multipart --key archives/application.bin --content-type application/octet-stream
aws s3api upload-part --bucket lab-multipart --key archives/application.bin --part-number 1 --upload-id <upload-id> --body part-one.bin
aws s3api upload-part --bucket lab-multipart --key archives/application.bin --part-number 2 --upload-id <upload-id> --body part-two.bin
aws s3api list-parts --bucket lab-multipart --key archives/application.bin --upload-id <upload-id>
aws s3api complete-multipart-upload --bucket lab-multipart --key archives/application.bin --upload-id <upload-id> --multipart-upload file://parts.json
aws s3api get-object --bucket lab-multipart --key archives/application.bin downloaded-application.bin
```

The first part is exactly 5 MiB because AWS requires every non-final multipart part to be at least that large. The final part is 1 KiB. The approved backend action discovers the active upload ID and current part ETags from S3 rather than storing generated identifiers in browser state.

Live completion before assembly tracks the active upload and its part sizes and ETags. After completion, the upload ID no longer exists, so completion is recomputed from the final 5,243,904-byte object, its `application/octet-stream` content type, and exact concatenated bytes.

Reset aborts any still-active lab uploads before removing completed object data and deleting the bucket.

This completes the initial S3 lab progression. The next service lab set should begin with:

```text
SQS queue and message lifecycle
```

## SQS Lab Progression

Implemented first SQS lab:

```text
Create and inspect an SQS queue
```

Steps:

```bash
aws sqs create-queue --queue-name lab-basics
aws sqs get-queue-url --queue-name lab-basics
aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names All
aws sqs list-queues
```

Live completion requires:

- the queue name to resolve to a URL ending in `lab-basics`,
- the queue attributes to contain `arn:aws:sqs:us-east-1:000000000000:lab-basics`,
- the queue URL to appear in `list-queues`.

Reset discovers the queue URL from live state and deletes only `lab-basics`.

Recommended next SQS lab:

```text
Send, receive, and delete an SQS message
```

Implemented second SQS lab:

```text
Send, receive, and delete an SQS message
```

Steps:

```bash
aws sqs create-queue --queue-name lab-basics
aws sqs send-message --queue-url <queue-url> --message-body file://message.json --message-attributes file://message-attributes.json
aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All
aws sqs delete-message --queue-url <queue-url> --receipt-handle <receipt-handle>
```

The message body is a known `order.created` JSON event with order ID `FLOCI-1001`. A `Lab=message-lifecycle` message attribute identifies the lab-owned message without touching unrelated queue contents.

Receive uses a zero-second visibility timeout so the message remains available for reload-safe live verification. Delete discovers a current receipt handle with a normal visibility timeout before removing the message.

Message deletion cannot be distinguished from “never sent” using SQS state alone, so this step uses a short-lived server-side completion marker after the live delete succeeds. Reset clears that marker and removes only matching lifecycle messages while leaving the shared `lab-basics` queue in place.

Recommended next SQS lab:

```text
Understand SQS visibility timeout
```

Implemented third SQS lab:

```text
Understand SQS visibility timeout
```

Steps:

```bash
aws sqs create-queue --queue-name lab-basics
aws sqs send-message --queue-url <queue-url> --message-body file://job.json --message-attributes file://message-attributes.json
aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 30 --wait-time-seconds 1 --message-attribute-names All
aws sqs change-message-visibility --queue-url <queue-url> --receipt-handle <receipt-handle> --visibility-timeout 60
aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 0 --message-attribute-names All
aws sqs change-message-visibility --queue-url <queue-url> --receipt-handle <receipt-handle> --visibility-timeout 2
aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 3 --message-attribute-names All
```

The lab sends a known `job.ready` event, receives it without deletion, and captures the live receipt handle. It first extends visibility to 60 seconds so a human has ample time to run the hidden-state check. After that check passes, it shortens visibility to two seconds, waits briefly, and verifies the same undeleted job becomes available again.

Short-lived server-side markers remember the verified hidden and returned phases because those temporal facts cannot be reconstructed after both moments have passed. Message body, attribute, receipt handle, hidden poll, and returned poll are all verified directly against Floci when their steps run.

Reset makes any cached in-flight receipt visible immediately, removes only the matching visibility-lab message, clears timing state, and leaves the shared queue intact.

Recommended next SQS lab:

```text
Work with delayed SQS messages
```

Implemented fourth SQS lab:

```text
Work with delayed SQS messages
```

Steps:

```bash
aws sqs create-queue --queue-name lab-basics
aws sqs send-message --queue-url <queue-url> --message-body file://report.json --message-attributes file://message-attributes.json --delay-seconds 10
aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names ApproximateNumberOfMessagesDelayed
aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 12 --message-attribute-names All
```

The send step atomically verifies the temporal state before returning: Floci must report at least one delayed message, and an immediate receive must not return the attributed `report.generate` request. This avoids making successful verification depend on how quickly a user clicks the next step.

The final step keeps polling through unrelated available messages until it finds the exact delayed-lab message or the delivery window expires. Live verification requires the known body and `Lab=delayed-message` attribute.

Reset waits for a still-delayed lab message when necessary, removes only matching messages, clears temporal markers, and leaves the shared queue intact.

Recommended next SQS lab:

```text
Send and delete SQS messages in batches
```

Implemented fifth SQS lab:

```text
Send and delete SQS messages in batches
```

Steps:

```bash
aws sqs create-queue --queue-name lab-basics
aws sqs send-message-batch --queue-url <queue-url> --entries file://send-batch.json
aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All
aws sqs delete-message-batch --queue-url <queue-url> --entries file://delete-batch.json
```

The lab sends three `task.created` events with IDs `FLOCI-TASK-1` through `FLOCI-TASK-3`. Every message carries `Lab=batch-messages`, allowing verification and reset to leave unrelated queue contents untouched.

Send requires all three batch entries to appear in `Successful` with no failures. Receive tolerates SQS returning fewer messages than requested by polling briefly and collecting unique task bodies. Delete discovers current receipt handles from live receives and builds a three-entry batch acknowledgement.

After deletion, a short-lived server-side marker distinguishes successful batch processing from a batch that was never sent. Reset removes any remaining matching task messages, clears completion state, and preserves the shared queue.

Recommended next SQS lab:

```text
Configure SQS queue attributes and tags
```

Implemented sixth SQS lab:

```text
Configure SQS queue attributes and tags
```

Steps:

```bash
aws sqs create-queue --queue-name lab-basics
aws sqs set-queue-attributes --queue-url <queue-url> --attributes file://queue-attributes.json
aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names VisibilityTimeout MessageRetentionPeriod ReceiveMessageWaitTimeSeconds
aws sqs tag-queue --queue-url <queue-url> --tags Environment=lab,Purpose=training
aws sqs list-queue-tags --queue-url <queue-url>
```

The lab configures:

- `VisibilityTimeout=45`,
- `MessageRetentionPeriod=86400`,
- `ReceiveMessageWaitTimeSeconds=10`,
- `Environment=lab`,
- `Purpose=training`.

Live completion requires every exact attribute and tag value. Reset restores the AWS defaults used by the local queue (`30`, `345600`, and `0`) and removes only the `Environment` and `Purpose` tags while leaving the shared queue in place.

Recommended next SQS lab:

```text
Route failed messages to an SQS dead-letter queue
```

Implemented seventh SQS lab:

```text
Route failed messages to an SQS dead-letter queue
```

Steps:

```bash
aws sqs create-queue --queue-name lab-redrive-dlq
aws sqs create-queue --queue-name lab-redrive-source
aws sqs set-queue-attributes --queue-url <source-queue-url> --attributes file://redrive-policy.json
aws sqs get-queue-attributes --queue-url <source-queue-url> --attribute-names RedrivePolicy
aws sqs send-message --queue-url <source-queue-url> --message-body file://payment.json --message-attributes file://message-attributes.json
aws sqs receive-message --queue-url <source-queue-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 0 --attribute-names All --message-attribute-names All
aws sqs receive-message --queue-url <source-queue-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 0 --attribute-names All --message-attribute-names All
aws sqs receive-message --queue-url <source-queue-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 0 --attribute-names All --message-attribute-names All
aws sqs receive-message --queue-url <dlq-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 1 --attribute-names All --message-attribute-names All
aws sqs start-message-move-task --source-arn arn:aws:sqs:us-east-1:000000000000:lab-redrive-dlq --destination-arn arn:aws:sqs:us-east-1:000000000000:lab-redrive-source --max-number-of-messages-per-second 10
aws sqs list-message-move-tasks --source-arn arn:aws:sqs:us-east-1:000000000000:lab-redrive-dlq --max-results 10
aws sqs receive-message --queue-url <source-queue-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 1 --attribute-names All --message-attribute-names All
```

The source queue uses `maxReceiveCount=2`. The first two receives return the message and deliberately omit `DeleteMessage`, simulating a consumer that repeatedly fails. The third source receive demonstrates the exact threshold behavior: after the receive count has reached the configured maximum, SQS withholds the message from the source response and moves it to the DLQ.

The DLQ inspection verifies the exact `payment.process` body and `Lab=dead-letter-redrive` message attribute. `StartMessageMoveTask` then performs managed redrive to the original source queue. The final receive proves that the same event returned with a fresh `ApproximateReceiveCount=1`.

Temporal milestones are stored as short-lived server-side markers, while queue existence, ARNs, `RedrivePolicy`, and message move task state are recomputed from live Floci. Reset cancels any running lab move task and deletes the dedicated source and dead-letter queues.

Recommended next SQS lab:

```text
Preserve ordering and deduplicate messages with an SQS FIFO queue
```

Implemented eighth SQS lab:

```text
Preserve ordering and deduplicate messages with an SQS FIFO queue
```

Steps:

```bash
aws sqs create-queue --queue-name lab-orders.fifo --attributes FifoQueue=true,ContentBasedDeduplication=false
aws sqs get-queue-attributes --queue-url <fifo-queue-url> --attribute-names QueueArn FifoQueue ContentBasedDeduplication
aws sqs send-message --queue-url <fifo-queue-url> --message-body file://order-created.json --message-group-id customer-FLOCI-1001 --message-deduplication-id FLOCI-ORDER-1001-1
aws sqs send-message --queue-url <fifo-queue-url> --message-body file://duplicate-created.json --message-group-id customer-FLOCI-1001 --message-deduplication-id FLOCI-ORDER-1001-1
aws sqs send-message --queue-url <fifo-queue-url> --message-body file://order-paid.json --message-group-id customer-FLOCI-1001 --message-deduplication-id FLOCI-ORDER-1001-2
aws sqs send-message --queue-url <fifo-queue-url> --message-body file://order-fulfilled.json --message-group-id customer-FLOCI-1001 --message-deduplication-id FLOCI-ORDER-1001-3
aws sqs get-queue-attributes --queue-url <fifo-queue-url> --attribute-names ApproximateNumberOfMessages
aws sqs receive-message --queue-url <fifo-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --attribute-names All --message-attribute-names All
```

The queue name ends with `.fifo`, sets `FifoQueue=true`, and leaves `ContentBasedDeduplication=false` so the workflow can teach explicit `MessageDeduplicationId` values.

All three distinct order events use `MessageGroupId=customer-FLOCI-1001`. The lab sends `created`, retries a different body with the same first deduplication ID, then sends `paid` and `fulfilled`. Floci returns the original message ID and sequence number for the duplicate request, matching FIFO duplicate-suppression behavior.

Four successful send requests leave exactly three available messages. ReceiveMessage returns those messages as `created`, `paid`, and `fulfilled`, with the expected group and deduplication IDs and strictly increasing sequence numbers.

The duplicate-send fact is retained as short-lived server-side state because queue contents alone cannot prove that the duplicate API request occurred. Queue configuration, queue depth, message bodies, group IDs, deduplication IDs, and ordering are verified from live Floci state. Reset deletes the dedicated FIFO queue and clears the completion markers.

Recommended next SQS lab:

```text
Purge messages and delete an SQS queue
```

Implemented ninth SQS lab:

```text
Purge messages and delete an SQS queue
```

Steps:

```bash
aws sqs create-queue --queue-name lab-cleanup --attributes VisibilityTimeout=45 --tags Purpose=cleanup-training
aws sqs send-message-batch --queue-url <cleanup-queue-url> --entries file://cleanup-messages.json
aws sqs get-queue-attributes --queue-url <cleanup-queue-url> --attribute-names QueueArn VisibilityTimeout ApproximateNumberOfMessages
aws sqs purge-queue --queue-url <cleanup-queue-url>
aws sqs get-queue-attributes --queue-url <cleanup-queue-url> --attribute-names QueueArn VisibilityTimeout ApproximateNumberOfMessages
aws sqs delete-queue --queue-url <cleanup-queue-url>
```

The lab creates a dedicated queue with `VisibilityTimeout=45` and `Purpose=cleanup-training`, then sends three attributed `cleanup.test` messages in a batch.

Before purge, live verification requires the expected queue ARN, visibility timeout, tag, and exactly three available messages. `PurgeQueue` removes all queue messages. The next inspection proves that the queue still exists with zero messages and that its configuration and tag remain unchanged.

`DeleteQueue` then removes the queue resource itself. Verification calls `GetQueueUrl` and requires `AWS.SimpleQueueService.NonExistentQueue`, making the difference between emptying a queue and deleting it concrete.

Because successful deletion removes the live resource needed to reconstruct earlier steps, short-lived server-side markers retain the verified populated and purged milestones. Reset deletes the dedicated queue if it still exists and clears all markers.

This completes the foundational SQS lab sequence:

- queue creation and inspection,
- message lifecycle,
- visibility timeout,
- delayed delivery,
- batch operations,
- queue attributes and tags,
- dead-letter queues and managed redrive,
- FIFO ordering and duplicate suppression,
- purge and queue deletion.

Recommended next multi-service lab:

```text
Fan out an SNS message to SQS queues
```

## SNS Lab Progression

Implemented first SNS lab:

```text
Fan out an SNS message to SQS queues
```

Steps:

```bash
aws sns create-topic --name lab-order-events
aws sqs create-queue --queue-name lab-order-processing
aws sqs create-queue --queue-name lab-order-audit
aws sqs set-queue-attributes --queue-url <orders-queue-url> --attributes file://orders-queue-policy.json
aws sqs set-queue-attributes --queue-url <audit-queue-url> --attributes file://audit-queue-policy.json
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:000000000000:lab-order-events --protocol sqs --notification-endpoint arn:aws:sqs:us-east-1:000000000000:lab-order-processing --attributes RawMessageDelivery=true --return-subscription-arn
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:000000000000:lab-order-events --protocol sqs --notification-endpoint arn:aws:sqs:us-east-1:000000000000:lab-order-audit --attributes RawMessageDelivery=true --return-subscription-arn
aws sns list-subscriptions-by-topic --topic-arn arn:aws:sns:us-east-1:000000000000:lab-order-events
aws sns publish --topic-arn arn:aws:sns:us-east-1:000000000000:lab-order-events --message file://order-created.json --subject "Order created" --message-attributes file://message-attributes.json
aws sqs receive-message --queue-url <orders-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All
aws sqs receive-message --queue-url <audit-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All
```

Each queue policy grants `sqs:SendMessage` to `sns.amazonaws.com` only when `aws:SourceArn` equals the dedicated topic ARN. Both subscriptions are immediately confirmed and use `RawMessageDelivery=true`.

The published `order.created` event therefore arrives as the exact JSON body instead of an SNS notification envelope. Its `EventType=order.created` and `Environment=lab` attributes are preserved as SQS message attributes.

Live completion requires the topic and both queue ARNs, both exact queue policies, two confirmed raw-delivery subscriptions, and the exact event body and attributes in both queues. Receive verification uses a zero-second visibility timeout so both copies remain available after reload.

Reset unsubscribes both endpoints before deleting the topic, then deletes both lab-owned queues. This lab makes the architectural distinction concrete: a queue normally distributes work among competing consumers, while a topic independently delivers one publish to every matching subscription.

Implemented second SNS lab:

```text
Route selected SNS messages with subscription filter policies
```

Steps:

```bash
aws sns create-topic --name lab-filtered-events
aws sqs create-queue --queue-name lab-created-events
aws sqs create-queue --queue-name lab-priority-events
aws sqs set-queue-attributes --queue-url <created-queue-url> --attributes file://created-queue-policy.json
aws sqs set-queue-attributes --queue-url <priority-queue-url> --attributes file://priority-queue-policy.json
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:000000000000:lab-filtered-events --protocol sqs --notification-endpoint arn:aws:sqs:us-east-1:000000000000:lab-created-events --attributes file://created-filter.json --return-subscription-arn
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:000000000000:lab-filtered-events --protocol sqs --notification-endpoint arn:aws:sqs:us-east-1:000000000000:lab-priority-events --attributes file://priority-filter.json --return-subscription-arn
aws sns get-subscription-attributes --subscription-arn <subscription-arn>
aws sns publish --topic-arn arn:aws:sns:us-east-1:000000000000:lab-filtered-events --message file://created-event.json --message-attributes file://created-attributes.json
aws sns publish --topic-arn arn:aws:sns:us-east-1:000000000000:lab-filtered-events --message file://priority-event.json --message-attributes file://priority-attributes.json
aws sqs receive-message --queue-url <created-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All
aws sqs receive-message --queue-url <priority-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All
```

The created-events subscription uses `{"EventType":["order.created"]}`. The priority subscription uses `{"Priority":["high"]}`. Both set `FilterPolicyScope=MessageAttributes` and use raw delivery.

The lab publishes a normal-priority `order.created` event and a high-priority `order.cancelled` event. Completion requires the created-events queue to contain exactly the first event and the priority queue to contain exactly the second. This proves both positive matching and non-matching exclusion.

Queue policies still scope `sqs:SendMessage` to the dedicated topic ARN. Reset unsubscribes both filtered endpoints, deletes the topic, and deletes both queues.

Recommended next architecture lab:

```text
Provision S3 and SQS resources with CloudFormation
```

## CloudFormation Lab Progression

Implemented first CloudFormation lab:

```text
Provision S3 and SQS resources with CloudFormation
```

Steps:

```bash
aws cloudformation validate-template --template-body file://storage-messaging-stack.json
aws cloudformation create-stack --stack-name lab-storage-messaging --template-body file://storage-messaging-stack.json
aws cloudformation describe-stacks --stack-name lab-storage-messaging
aws cloudformation describe-stack-resources --stack-name lab-storage-messaging
aws cloudformation describe-stack-events --stack-name lab-storage-messaging
aws s3api head-bucket --bucket lab-cfn-storage
aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names QueueArn VisibilityTimeout
aws cloudformation delete-stack --stack-name lab-storage-messaging
aws cloudformation describe-stacks --stack-name lab-storage-messaging
aws s3api head-bucket --bucket lab-cfn-storage
aws sqs get-queue-url --queue-name lab-cfn-jobs
```

The JSON template declares an `AWS::S3::Bucket` and `AWS::SQS::Queue`, plus outputs for the bucket name, queue URL, and queue ARN. Validation runs before resource creation.

Creation completion requires `CREATE_COMPLETE` and exact outputs. Resource inspection maps the logical IDs `StorageBucket` and `JobsQueue` to their physical bucket name and queue URL. Event inspection requires successful creation events for the stack and both resources.

The service-level check independently calls S3 and SQS to prove that the provisioned resources are usable and that the queue has the template-defined 30-second visibility timeout.

Deletion is performed through CloudFormation rather than directly against S3 or SQS. Completion requires the stack, bucket, and queue all to be absent. Reset is defensive: it asks CloudFormation to delete the stack first and removes any remaining lab-owned resources only if necessary.

Floci 1.5.26 capability boundary:

- Stack create, outputs, resource inventory, event history, and deletion work end to end.
- Change-set creation succeeds but currently reports an empty `Changes` list.
- Updating a named SQS queue property currently attempts resource recreation and rolls back because the queue name already exists.
- Template-defined S3 and SQS tags are not currently propagated to the underlying resources.

Do not add a successful change-set/update lesson until Floci can preview and execute the update without rollback. In AWS, CloudFormation supports in-place updates for many resource properties, and change sets are an important production workflow.

Recommended next architecture lab:

```text
Build a VPC with public and private subnets
```

## EC2 Networking Lab Progression

Implemented first networking lab:

```text
Build a VPC with public and private subnets
```

Steps:

```bash
aws ec2 create-vpc --cidr-block 10.42.0.0/16 --tag-specifications file://vpc-tags.json
aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.42.1.0/24 --availability-zone us-east-1a
aws ec2 modify-subnet-attribute --subnet-id <public-subnet-id> --map-public-ip-on-launch
aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.42.2.0/24 --availability-zone us-east-1b
aws ec2 create-internet-gateway
aws ec2 attach-internet-gateway --internet-gateway-id <igw-id> --vpc-id <vpc-id>
aws ec2 create-route-table --vpc-id <vpc-id>
aws ec2 create-route --route-table-id <public-route-table-id> --destination-cidr-block 0.0.0.0/0 --gateway-id <igw-id>
aws ec2 associate-route-table --route-table-id <public-route-table-id> --subnet-id <public-subnet-id>
aws ec2 create-route-table --vpc-id <vpc-id>
aws ec2 associate-route-table --route-table-id <private-route-table-id> --subnet-id <private-subnet-id>
aws ec2 describe-vpcs --vpc-ids <vpc-id>
aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc-id>
aws ec2 describe-internet-gateways --filters Name=attachment.vpc-id,Values=<vpc-id>
aws ec2 describe-route-tables --filters Name=vpc-id,Values=<vpc-id>
```

The VPC uses `10.42.0.0/16`. Its public subnet is `10.42.1.0/24` in `us-east-1a`, while its private subnet is `10.42.2.0/24` in `us-east-1b`.

The public subnet enables `MapPublicIpOnLaunch` and is associated with a route table containing `0.0.0.0/0` through the attached internet gateway. The private subnet is explicitly associated with a separate route table whose only route is the automatic local VPC route.

This teaches that a subnet is public because its effective route table has a route to an internet gateway; naming a subnet “public” or merely attaching an internet gateway to the VPC is not sufficient. Public IP assignment is a separate subnet behavior.

Live completion is recomputed from the VPC CIDR and tag, subnet CIDRs and attributes, gateway attachment, route-table associations, and route destinations. Reset discovers the live topology and removes associations, custom route tables, gateway attachment, gateway, subnets, and VPC in dependency order.

Floci 1.5.26 capability boundary: VPC tags persist, but tags supplied while creating subnets, route tables, and internet gateways are not currently returned. The lab therefore identifies those resources through CIDRs, attachments, routes, and associations.

Recommended next networking lab:

```text
Control VPC traffic with security groups and network ACLs
```

Implemented second networking lab:

```text
Control VPC traffic with security groups and network ACLs
```

Steps:

```bash
aws ec2 create-vpc --cidr-block 10.43.0.0/16 --tag-specifications file://vpc-tags.json
aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.43.1.0/24 --availability-zone us-east-1a
aws ec2 create-security-group --group-name lab-web-sg --description "HTTPS web tier" --vpc-id <vpc-id>
aws ec2 authorize-security-group-ingress --group-id <web-sg-id> --ip-permissions file://trusted-https.json
aws ec2 create-security-group --group-name lab-app-sg --description "Private application tier" --vpc-id <vpc-id>
aws ec2 authorize-security-group-ingress --group-id <app-sg-id> --ip-permissions file://web-to-app.json
aws ec2 describe-security-groups --group-ids <web-sg-id> <app-sg-id>
aws ec2 describe-security-group-rules --filters Name=group-id,Values=<web-sg-id>,<app-sg-id>
aws ec2 create-network-acl --vpc-id <vpc-id>
aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> --rule-number 90 --protocol 6 --rule-action deny --ingress --cidr-block 0.0.0.0/0 --port-range From=22,To=22
aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> --rule-number 100 --protocol 6 --rule-action allow --ingress --cidr-block 203.0.113.0/24 --port-range From=443,To=443
aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> --rule-number 100 --protocol 6 --rule-action allow --egress --cidr-block 203.0.113.0/24 --port-range From=1024,To=65535
aws ec2 replace-network-acl-association --association-id <default-association-id> --network-acl-id <network-acl-id>
aws ec2 describe-network-acls --filters Name=vpc-id,Values=<vpc-id>
```

The web security group allows TCP 443 only from `203.0.113.0/24`. The app security group allows TCP 8080 using the web security group as its source, which follows workload identity instead of a fixed client CIDR.

Both groups retain their default all-traffic egress. Because security groups are stateful, response traffic for an allowed inbound connection is automatically permitted without a separate ephemeral-port ingress rule.

The accompanying NACL design demonstrates ordered, stateless rules:

- rule 90 denies inbound SSH,
- rule 100 allows inbound HTTPS from the trusted CIDR,
- outbound rule 100 allows ephemeral response ports 1024–65535,
- the final `*` rule denies unmatched traffic.

Floci 1.5.26 implementation notes:

- Security-group creation, CIDR ingress, and security-group source references are accepted.
- Describe responses retain the TCP 8080 rule but currently omit its `UserIdGroupPairs` source. The lab records successful relationship creation as short-lived state while verifying the live port rule.
- Network ACL create, entry, association, and describe calls are now exercised directly.

The final NACL step verifies the custom ACL association plus the ordered stateless inbound and outbound entries.

Reset restores the subnet to the default NACL, deletes the custom NACL, then deletes the app and web security groups before removing the subnet and dedicated VPC.

Recommended next networking lab:

```text
Connect a private VPC to S3 with a gateway endpoint
```

Implemented third networking lab:

```text
Connect a private VPC to S3 with a gateway endpoint
```

Steps:

```bash
aws ec2 create-vpc --cidr-block 10.44.0.0/16 --tag-specifications file://vpc-tags.json
aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.44.1.0/24 --availability-zone us-east-1a
aws ec2 create-route-table --vpc-id <vpc-id>
aws ec2 associate-route-table --route-table-id <route-table-id> --subnet-id <subnet-id>
aws s3api create-bucket --bucket lab-private-s3-data
aws ec2 create-vpc-endpoint --vpc-id <vpc-id> --vpc-endpoint-type Gateway --service-name com.amazonaws.us-east-1.s3 --route-table-ids <route-table-id> --policy-document file://endpoint-policy.json
aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <endpoint-id>
aws ec2 describe-route-tables --route-table-ids <route-table-id>
aws s3api head-bucket --bucket lab-private-s3-data
```

The dedicated `10.44.0.0/16` VPC has no internet gateway or NAT gateway. Its `10.44.1.0/24` subnet disables automatic public IP assignment and uses a custom route table with no `0.0.0.0/0` route.

The S3 gateway endpoint uses the regional service name `com.amazonaws.us-east-1.s3`. Its endpoint policy grants only `s3:ListBucket` and `s3:GetObject` for the lab bucket and its objects. Gateway endpoints do not use security groups and do not incur the hourly and data-processing model associated with interface endpoints.

In AWS, associating the route table causes AWS to add a managed route whose destination is the regional S3 prefix list and whose target is the gateway endpoint. S3 traffic matching that prefix list stays on the AWS network; the endpoint does not make the subnet generally internet-connected.

Floci 1.5.26 capability boundary:

- Gateway endpoint creation, description, and deletion work.
- Floci currently returns an empty route-table ID in the endpoint description.
- Floci does not inject the managed S3 prefix-list route into the selected route table.
- The lab verifies that the real AWS request included the route-table ID and bucket-scoped policy, then explicitly inspects and reports the missing managed route.

Reset removes the endpoint and bucket before disassociating and deleting the route table, subnet, and dedicated VPC.

Recommended next networking lab:

```text
Connect a private subnet to an AWS service with an interface endpoint
```

Implemented fourth networking lab:

```text
Connect a private subnet to SQS with an interface endpoint
```

Steps:

```bash
aws ec2 create-vpc --cidr-block 10.45.0.0/16 --tag-specifications file://vpc-tags.json
aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.45.1.0/24 --availability-zone us-east-1a
aws ec2 create-security-group --group-name lab-sqs-endpoint-sg --description "Private SQS endpoint HTTPS" --vpc-id <vpc-id>
aws ec2 authorize-security-group-ingress --group-id <endpoint-sg-id> --ip-permissions file://endpoint-https.json
aws sqs create-queue --queue-name lab-private-sqs
aws ec2 create-vpc-endpoint --vpc-id <vpc-id> --vpc-endpoint-type Interface --service-name com.amazonaws.us-east-1.sqs --subnet-ids <subnet-id> --security-group-ids <endpoint-sg-id> --private-dns-enabled --policy-document file://endpoint-policy.json
aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <endpoint-id>
aws ec2 describe-network-interfaces --network-interface-ids <eni-id>
aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names QueueArn
```

The interface endpoint differs from the S3 gateway endpoint in the preceding lab. It places an endpoint network interface in the selected subnet, uses a security group, and can make the standard regional SQS hostname resolve to private endpoint addresses when private DNS is enabled. It does not add a service prefix-list route to the subnet route table.

The endpoint security group allows TCP 443 only from the dedicated `10.45.0.0/16` VPC. The endpoint policy allows only `sqs:GetQueueAttributes` and `sqs:SendMessage` against `lab-private-sqs`.

Live completion requires the VPC, private subnet, HTTPS ingress rule, target queue, available interface endpoint, and a completed topology inspection. The inspection reports the endpoint fields and ENIs that Floci persists while retaining the exact AWS request as the source of truth for any omitted local fields.

Reset deletes the interface endpoint first so its managed network interfaces can disappear, then removes the queue, endpoint security group, subnet, and dedicated VPC.

Recommended next networking direction:

```text
Add multi-AZ endpoint placement or hybrid DNS/routing when local support is reliable
```

## EventBridge Scheduler Lab Progression

Implemented first Scheduler lab:

```text
Schedule an EventBridge Scheduler message to SQS
```

Steps:

```bash
aws sqs create-queue --queue-name lab-scheduled-reports
aws iam create-role --role-name FlociSchedulerSqsRole --assume-role-policy-document file://scheduler-trust-policy.json
aws iam put-role-policy --role-name FlociSchedulerSqsRole --policy-name SendScheduledReportToSqs --policy-document file://send-message-policy.json
aws scheduler create-schedule-group --name lab-scheduler
aws scheduler create-schedule --name send-report-ready --group-name lab-scheduler --schedule-expression "at(<utc-time-seconds-from-now>)" --flexible-time-window Mode=OFF --target file://target.json --action-after-completion DELETE
aws scheduler get-schedule --name send-report-ready --group-name lab-scheduler
aws sqs receive-message --queue-url <scheduled-reports-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 12
aws scheduler get-schedule --name send-report-ready --group-name lab-scheduler
```

The IAM trust policy allows `scheduler.amazonaws.com` to assume the role. Its inline permissions policy grants only `sqs:SendMessage` on `arn:aws:sqs:us-east-1:000000000000:lab-scheduled-reports`.

The create-schedule runner generates a UTC `at(...)` expression several seconds in the future. The target stores the exact `report.ready` JSON input, the execution role ARN, and a bounded retry policy. `FlexibleTimeWindow=OFF` requests precise one-time invocation, while `ActionAfterCompletion=DELETE` removes the schedule after it runs.

Scheduler invocation is asynchronous. The delivery step polls through local invocation latency and verifies the exact message body with a zero-second visibility timeout. The final step calls `GetSchedule` and requires `ResourceNotFoundException`, proving automatic schedule cleanup.

Live status combines the remaining queue, role, inline policy, and schedule group with short-lived markers for schedule creation and automatic deletion. Reset tolerates a schedule already removed by `ActionAfterCompletion`, then deletes the group, role policy, role, and target queue in dependency order.

Recommended next messaging lab:

```text
Route selected SNS messages with subscription filter policies
```

## Lambda Lab Progression

Implemented first Lambda lab:

```text
Create, invoke, and inspect a Lambda function
```

Steps:

```bash
aws iam create-role --role-name FlociLambdaLabRole --assume-role-policy-document file://lambda-trust-policy.json
aws iam put-role-policy --role-name FlociLambdaLabRole --policy-name FlociLambdaLabLogs --policy-document file://lambda-logs-policy.json
aws lambda create-function --function-name lab-echo --runtime python3.11 --role arn:aws:iam::000000000000:role/FlociLambdaLabRole --handler handler.lambda_handler --zip-file fileb://function.zip
aws lambda invoke --function-name lab-echo --payload file://event.json response.json
aws logs describe-log-streams --log-group-name /aws/lambda/lab-echo
aws logs get-log-events --log-group-name /aws/lambda/lab-echo --log-stream-name <log-stream-name>
```

The IAM trust policy allows `lambda.amazonaws.com` to assume the execution role. Its inline permissions policy grants the function the CloudWatch Logs permissions needed to create log groups and streams and write events under `/aws/lambda/lab-echo`.

The function package is generated by the approved backend runner from a small `handler.py` artifact. The visible command still teaches the real AWS CLI shape with `--zip-file fileb://function.zip`.

The invocation uses a fixed `event.json` containing `FLOCI-LAMBDA-1001`. The handler echoes the event, returns a success payload, and prints a log line containing the request ID. Live completion verifies the role, inline policy, function configuration, recorded successful invocation, log group, and matching log event. Reset deletes the function, log group, role policy, role, and cached invocation/log markers.

Recommended next serverless lab:

```text
Read app configuration and secrets from Lambda
```

Implemented second Lambda lab:

```text
Read app configuration and secrets from Lambda
```

Steps:

```bash
aws ssm put-parameter --name /floci/lab/lambda/config --type String --value file://lambda-config.json --description "Floci Lambda runtime configuration" --overwrite
aws secretsmanager create-secret --name lab/lambda-secret --description "Floci Lambda runtime secret" --secret-string file://lambda-secret.json
aws iam create-role --role-name FlociLambdaConfigRole --assume-role-policy-document file://lambda-trust-policy.json
aws iam put-role-policy --role-name FlociLambdaConfigRole --policy-name FlociLambdaConfigRead --policy-document file://lambda-config-policy.json
aws lambda create-function --function-name lab-config-reader --runtime python3.11 --role arn:aws:iam::000000000000:role/FlociLambdaConfigRole --handler handler.lambda_handler --zip-file fileb://function.zip --environment Variables={PARAMETER_NAME=/floci/lab/lambda/config,SECRET_ID=lab/lambda-secret}
aws lambda invoke --function-name lab-config-reader --payload file://event.json response.json
aws logs describe-log-streams --log-group-name /aws/lambda/lab-config-reader
aws logs get-log-events --log-group-name /aws/lambda/lab-config-reader --log-stream-name <log-stream-name>
```

The lab creates its own SSM parameter and Secrets Manager secret so it can be run from a clean local environment. The Lambda execution role trusts `lambda.amazonaws.com` and grants only `ssm:GetParameter` on `/floci/lab/lambda/config`, `secretsmanager:GetSecretValue` on `lab/lambda-secret`, and CloudWatch Logs writes for `/aws/lambda/lab-config-reader`.

The function stores resource identifiers in environment variables, reads both services at invocation time, returns safe proof fields such as application name, environment, secret username, and secret key names, and does not return the secret API key value. Live completion verifies the parameter, secret, role, inline policy, function environment, successful invocation marker, and matching log event. Reset deletes the function, log group, role policy, role, SSM parameter, secret, and recorded invocation/log markers.

Recommended next serverless lab:

```text
Process SQS messages with Lambda
```

Implemented third Lambda lab:

```text
Process SQS messages with Lambda
```

Steps:

```bash
aws sqs create-queue --queue-name lab-lambda-events
aws iam create-role --role-name FlociLambdaSqsConsumerRole --assume-role-policy-document file://lambda-trust-policy.json
aws iam put-role-policy --role-name FlociLambdaSqsConsumerRole --policy-name FlociLambdaSqsConsumerPolicy --policy-document file://lambda-sqs-policy.json
aws lambda create-function --function-name lab-sqs-consumer --runtime python3.11 --role arn:aws:iam::000000000000:role/FlociLambdaSqsConsumerRole --handler handler.lambda_handler --zip-file fileb://function.zip
aws lambda create-event-source-mapping --function-name lab-sqs-consumer --event-source-arn arn:aws:sqs:us-east-1:000000000000:lab-lambda-events --batch-size 5 --enabled
aws sqs send-message --queue-url <queue-url> --message-body file://order-event.json --message-attributes file://message-attributes.json
aws logs describe-log-streams --log-group-name /aws/lambda/lab-sqs-consumer
aws logs get-log-events --log-group-name /aws/lambda/lab-sqs-consumer --log-stream-name <log-stream-name>
```

The lab creates a dedicated SQS queue and Lambda function so it can teach event source mappings without reusing the synchronous echo function. The execution role trusts `lambda.amazonaws.com` and grants only `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` on `lab-lambda-events`, plus CloudWatch Logs writes for `/aws/lambda/lab-sqs-consumer`.

The handler reads SQS `Records`, parses the order-event body, logs `FLOCI-LAMBDA-SQS-4001`, and returns a processed-record summary. Live completion verifies the queue ARN, role, inline policy, function, event source mapping, recorded SQS send, and matching log event. Reset deletes the event source mapping before deleting the function, then removes the log group, role policy, role, queue, and recorded mapping/message/log markers.

Recommended next serverless lab:

```text
Send an API Gateway request to Lambda
```

## API Gateway Lab Progression

Implemented first API Gateway lab:

```text
Send an API Gateway request to Lambda
```

Steps:

```bash
aws iam create-role --role-name FlociLambdaLabRole --assume-role-policy-document file://lambda-trust-policy.json
aws iam put-role-policy --role-name FlociLambdaLabRole --policy-name FlociLambdaLabLogs --policy-document file://lambda-logs-policy.json
aws lambda create-function --function-name lab-echo --runtime python3.11 --role arn:aws:iam::000000000000:role/FlociLambdaLabRole --handler handler.lambda_handler --zip-file fileb://function.zip
aws apigatewayv2 create-api --name lab-lambda-api --protocol-type HTTP
aws apigatewayv2 create-integration --api-id <api-id> --integration-type AWS_PROXY --integration-uri arn:aws:lambda:us-east-1:000000000000:function:lab-echo --payload-format-version 2.0
aws apigatewayv2 create-route --api-id <api-id> --route-key "POST /echo" --target integrations/<integration-id>
aws apigatewayv2 create-stage --api-id <api-id> --stage-name $default --auto-deploy
aws lambda add-permission --function-name lab-echo --statement-id AllowFlociApiGatewayInvoke --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn arn:aws:execute-api:us-east-1:000000000000:<api-id>/*/*/echo
curl -X POST <api-endpoint>/echo -H "Content-Type: application/json" --data @event.json
```

The lab uses an API Gateway v2 HTTP API because it is the shortest AWS-shaped path from an HTTP request to Lambda. The Lambda function and execution role are created as lab prerequisites so the workflow is runnable from a clean local environment.

The integration uses `AWS_PROXY` and payload format version `2.0`. The route key is `POST /echo`, the stage is `$default` with auto-deploy enabled, and the Lambda resource policy grants only API Gateway invoke access for the lab API and `/echo` route.

The final request posts the same `event.json` used by the Lambda lab through Floci's local API Gateway data plane. Live completion verifies the Lambda role, inline policy, function, HTTP API, integration, route, stage, Lambda permission, and recorded successful HTTP response. Reset deletes the API, removes the Lambda permission, deletes the function and log group, removes the role policy and role, and clears cached IDs and request state.

Recommended next serverless lab:

```text
Create a DynamoDB table and query items
```

## DynamoDB Lab Progression

Implemented first DynamoDB lab:

```text
Create a DynamoDB table and query items
```

Steps:

```bash
aws dynamodb create-table --table-name lab-orders --attribute-definitions file://attribute-definitions.json --key-schema file://key-schema.json --global-secondary-indexes file://customer-index.json --billing-mode PAY_PER_REQUEST
aws dynamodb put-item --table-name lab-orders --item file://order-item.json
aws dynamodb get-item --table-name lab-orders --key file://order-key.json
aws dynamodb update-item --table-name lab-orders --key file://order-key.json --update-expression "SET #status = :status, #total = :total" --expression-attribute-names file://attribute-names.json --expression-attribute-values file://updated-values.json --return-values ALL_NEW
aws dynamodb query --table-name lab-orders --index-name CustomerIdIndex --key-condition-expression "CustomerId = :customer" --expression-attribute-values file://query-values.json
aws dynamodb delete-item --table-name lab-orders --key file://order-key.json
aws dynamodb delete-table --table-name lab-orders
```

The table uses `OrderId` as its partition key and a `CustomerIdIndex` global secondary index for customer lookups. It uses on-demand billing with `PAY_PER_REQUEST`, keeping the workflow focused on schema, item shape, and access patterns instead of capacity math.

The item is stored with low-level DynamoDB attribute value JSON, then read by primary key, updated with an expression, queried through the customer index, deleted, and finally removed with the table. Live completion verifies the table and GSI, the initial item, the updated item, the query result, item deletion, and table deletion. Lab-owned markers distinguish a fresh missing table from a successfully completed `delete-table` step.

Recommended next serverless lab:

```text
Write DynamoDB items from Lambda
```

Implemented second DynamoDB lab:

```text
Write DynamoDB items from Lambda
```

Steps:

```bash
aws dynamodb create-table --table-name lab-lambda-orders --attribute-definitions AttributeName=OrderId,AttributeType=S --key-schema AttributeName=OrderId,KeyType=HASH --billing-mode PAY_PER_REQUEST
aws iam create-role --role-name FlociLambdaDynamoDbRole --assume-role-policy-document file://lambda-trust-policy.json
aws iam put-role-policy --role-name FlociLambdaDynamoDbRole --policy-name FlociLambdaDynamoDbWrite --policy-document file://lambda-dynamodb-policy.json
aws lambda create-function --function-name lab-order-writer --runtime python3.11 --role arn:aws:iam::000000000000:role/FlociLambdaDynamoDbRole --handler handler.lambda_handler --zip-file fileb://function.zip --environment Variables={TABLE_NAME=lab-lambda-orders}
aws lambda invoke --function-name lab-order-writer --payload file://order-event.json response.json
aws dynamodb get-item --table-name lab-lambda-orders --key file://order-key.json
aws logs describe-log-streams --log-group-name /aws/lambda/lab-order-writer
aws logs get-log-events --log-group-name /aws/lambda/lab-order-writer --log-stream-name <log-stream-name>
```

The Lambda execution role trusts `lambda.amazonaws.com` and has a lab-scoped inline policy allowing `dynamodb:PutItem` only on `lab-lambda-orders`, plus CloudWatch Logs writes for `/aws/lambda/lab-order-writer`.

The handler reads `TABLE_NAME` from its environment, writes the order event as a DynamoDB item, returns the order ID and request ID, and logs the write. Live completion verifies the table, role, inline policy, function environment, successful invocation marker, written item, and matching log event. Reset deletes the function, log group, role policy, role, item, table, and recorded invocation/log markers.

Recommended next serverless lab:

```text
Protect local app data with KMS
```

## KMS Lab Progression

Implemented first KMS lab:

```text
Protect local app data with KMS
```

Steps:

```bash
aws kms create-key --description "Floci dashboard local workflow lab key" --key-usage ENCRYPT_DECRYPT --key-spec SYMMETRIC_DEFAULT --tags file://key-tags.json
aws kms create-alias --alias-name alias/lab-data-key --target-key-id <key-id>
aws kms describe-key --key-id alias/lab-data-key
aws kms encrypt --key-id alias/lab-data-key --plaintext fileb://app-config.json
aws kms decrypt --ciphertext-blob fileb://ciphertext.bin
```

The lab creates a symmetric `ENCRYPT_DECRYPT` key tagged for recovery, then attaches the stable alias `alias/lab-data-key`. The alias demonstrates how local application configuration can refer to a stable key name instead of a generated key ID.

The encrypt step protects a small JSON app configuration payload. KMS does not retain ciphertext as discoverable service state, so the lab records the ciphertext returned by the approved encrypt step and uses that exact blob for decrypt verification. Live completion verifies the key, alias, recorded ciphertext, and decrypted plaintext match. Reset deletes the alias, schedules the lab key for deletion with a seven-day pending window, and clears recorded ciphertext/decrypt state.

Implemented next serverless lab:

```text
Read app configuration from SSM Parameter Store
```

## SSM Parameter Store Lab Progression

Implemented first SSM lab:

```text
Read app configuration from SSM Parameter Store
```

Steps:

```bash
aws ssm put-parameter --name /floci/lab/app/config --type String --value file://app-config.json --description "Floci lab application configuration" --overwrite
aws ssm get-parameter --name /floci/lab/app/config --with-decryption
aws ssm get-parameters-by-path --path /floci/lab/app/ --recursive --with-decryption
```

The lab stores a JSON application configuration parameter under a hierarchical path. The direct read verifies the exact JSON payload, while the path read demonstrates how local applications can discover a configuration namespace such as `/floci/lab/app/`.

Live completion requires:

- the parameter name to exist,
- the type to remain `String`,
- the decoded JSON to match the expected application configuration,
- the recursive path query to return the same parameter.

Reset deletes only `/floci/lab/app/config`.

Recommended next serverless lab:

```text
Create and update a Secrets Manager secret
```

## Secrets Manager Lab Progression

Implemented first Secrets Manager lab:

```text
Create and update a Secrets Manager secret
```

Steps:

```bash
aws secretsmanager create-secret --name lab/app-credentials --description "Floci lab application credentials" --secret-string file://initial-secret.json
aws secretsmanager get-secret-value --secret-id lab/app-credentials
aws secretsmanager put-secret-value --secret-id lab/app-credentials --secret-string file://rotated-secret.json
aws secretsmanager get-secret-value --secret-id lab/app-credentials
aws secretsmanager describe-secret --secret-id lab/app-credentials
```

The lab creates a JSON application credential, reads the initial value, writes a rotated value, reads the current value, and inspects metadata separately from secret material.

Live completion requires:

- the secret to exist and not be scheduled for deletion,
- a recorded successful read of the initial credential JSON,
- the current secret value to match the rotated credential JSON,
- metadata inspection through `DescribeSecret`.

The initial-read milestone uses a short-lived server-side marker because once the secret is rotated, live Secrets Manager state can no longer prove that the old value was read successfully. Reset force-deletes only `lab/app-credentials` and clears the lab markers.

Implemented follow-up Lambda lab:

```text
Read app configuration and secrets from Lambda
```

## Future Services

Good next services:

- SQS: foundational sequence complete; continue through multi-service messaging labs.
- SNS: fan-out and message-attribute filtering foundations complete.
- Lambda: create, invoke, runtime configuration reads, SQS event source mapping, and CloudWatch Logs inspection foundations complete.
- API Gateway: HTTP API to Lambda request foundation complete.
- DynamoDB: table CRUD, customer-index query, and Lambda write foundations complete.
- KMS: key, alias, encrypt, and decrypt foundation complete.
- SSM Parameter Store: hierarchical JSON configuration foundation complete.
- Secrets Manager: JSON secret creation, initial read, value update, and metadata inspection foundation complete.
- CloudFormation: create/delete foundation complete; add updates and rollback labs when local support is reliable.
- EC2 networking: routing, security-group, S3 gateway endpoint, and SQS interface endpoint foundations complete.
- EventBridge Scheduler: one-time SQS delivery foundation complete.
- Event-driven Lambda: SQS event source mapping is complete; next practical gaps are S3 object-created notifications to Lambda, EventBridge rules to Lambda or SQS, and EventBridge Pipes source-to-target flows when local support is reliable.
- RDS: create local DB instance, inspect endpoint, connect instructions, cleanup.
- DocumentDB: create cluster/instance, inspect endpoint, cleanup.

The completed Scheduler multi-service lab is:

```text
Scheduler -> SQS
```

It proves local scheduling, AWS SDK target wiring, SQS delivery, and dashboard inspection in one workflow.

## Tests Required For Each Lab

Minimum:

- labs page renders the lab title and commands,
- service page links to labs when labs exist,
- status marks completed steps from live-state verification,
- run endpoint calls the registered step,
- step runner calls expected boto3 operation,
- step runner verifies expected state,
- reset endpoint calls registered reset,
- reset tolerates missing resources,
- static JavaScript syntax passes.

Manual/local verification:

- Run each command against local Floci through the lab endpoint.
- Confirm the normal service dashboard shows the created resource.
- Reset and confirm the dashboard no longer shows the resource.

## Implementation Phases

Phase 1: First IAM proof

- `/service/iam/labs/`
- IAM service-page Labs link
- Create user Alice
- live-state completion
- reset

Phase 2: Lab framework

- Split `dashboard/labs.py` into a small package. Complete.
- Introduce typed lab definitions. Complete for shared payload shapes.
- Centralize registry and runner facades. Complete.
- Keep the existing route/template API stable while moving internals. Complete.
- Move service-specific definitions, runners, status, and reset helpers out of `monolith.py`. Complete.

Phase 3: Multi-step IAM labs

- Create user and attach policy.
- Create user and access key.
- Create group and add user.
- Attach policy to group.
- Attach inline policy to user.
- Create role with trust and permissions policies.
- Create role and instance profile.
- Enforce or clearly guide step order.
- Policy simulation remains blocked by Floci `UnsupportedOperation`.

Phase 4: S3 and SQS

- Add the next two services with simple, high-value workflows.
- Generalize "View in dashboard" links after verification.

Phase 5: Multi-service workflows

- SNS -> SQS
- Scheduler -> SQS
- CloudFormation provisioning labs

## Open Questions

- Should future steps be disabled until prior steps verify, or should users be allowed to run ahead?
- Should reset always clean all lab resources, or should it show a checklist of what it will delete?
- Should labs support user-customized names, or remain fixed for repeatability?
- Should the page store last responses in browser state so reloads can show prior output?
- When a resource pre-exists, should Reset delete it or treat it as external?
- When do we need actual AWS CLI subprocess execution, if ever?

## Recommendation

Keep the product simple and opinionated:

- one visible CLI command,
- one click to run it,
- one response,
- one verification,
- one reset path.

Scale by adding more curated steps, not by adding a generic terminal.
