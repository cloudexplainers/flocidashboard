# Console accuracy review

A service-by-service review of the React console in `frontend/src/` against the real AWS Management
Console and AWS APIs, as far as Floci 2.3.0 allows. Each section lists findings ranked by severity,
what was fixed, and what is left with the reason.

## Summary

- **The biggest problem was silent false success.** Five consoles (S3, EC2, IAM, Lambda, DynamoDB) sent
  their create/delete/start/stop/invoke actions to the read-only inventory endpoint, which ignored them
  and returned 200, so the console said "created" or "deleted" while nothing happened. Several more
  helpers ignored HTTP errors on delete. All of these now call the real endpoints and show AWS errors.
- **Invented data was the second.** IAM showed `AdministratorAccess` on every user and a Lambda trust
  policy on every role; S3 showed Block Public Access as on and a sample CORS rule; ECS showed every
  cluster as ACTIVE with a made-up EC2 instance ID; Settings always said "Healthy". These now show the
  values the API returns, or "-" when it returns nothing.
- **Defaults now match AWS**: S3 versioning "Disabled", SQS/SNS FIFO content-based deduplication off,
  Secrets Manager 30-day recovery window, RDS final snapshot on delete, Lambda timeout from the API.
- **Destructive actions confirm** using the console's typed phrases ("delete me", "permanently delete",
  the resource name, "confirm").
- **Browser-facing URLs work from Docker**: presigned S3 URLs, S3 website endpoints, Lambda function
  URLs and API Gateway invoke URLs no longer point at the container hostname `floci`.

Verification: `npx tsc --noEmit`, `npm run build`, `npm run test:e2e` (332 passed, against the disposable
emulator) and `docker compose exec -T dashboard python manage.py test --noinput` (1,141 passed) all pass.
The "utility dashboard renders" e2e tests occasionally time out on a blank page under repeated runs; `main`
shows the same rate (1–2 failures in 32 repeats), so this is a pre-existing flake, not from these changes. All 539 workbench action
definitions resolve to a route that allows their method.

The dashboard container on port 8001 mounts this checkout and runs `runserver` with autoreload, so it
serves whatever branch is checked out here. The new `FLOCI_PUBLIC_ENDPOINT_URL` variable in
`docker-compose.yml` only takes effect after `docker compose up -d dashboard`; until then, presigned URLs
and endpoints fall back to the internal `http://floci:4566`.

Method: each console was read alongside its backend (`dashboard/aws/`, `dashboard/*_api.py`,
`dashboard/*_views.py`, `dashboard/services.py`), then probed against a disposable Floci on port 14566
populated by running every lab step (66 of 66 labs passed), with a second dashboard on port 18001.

Severity: **High** = an action silently does nothing, reports false success, or shows data that is wrong.
**Medium** = wrong AWS terminology, invented defaults, or a missing confirmation on a destructive action.
**Low** = cosmetic or structural differences from the AWS console.

## Cross-cutting

| Severity | Finding | Status |
| --- | --- | --- |
| High | `executeServiceAction(service, action, payload)` (the string form) POSTed to the inventory endpoint `/api/<service>/`, which ignores the body and returns 200. Every create/delete/start/stop that used it reported success while doing nothing. Used by S3, EC2, IAM, Lambda and DynamoDB. | Fixed: callers now use typed client functions that hit the real endpoints, and the string form is removed. |
| Medium | Destructive actions had no confirmation in several consoles. | Fixed with a shared `ConfirmActionModal` that follows the AWS "type the name to confirm" pattern. |
| Low | Dates and sizes were shown raw (ISO strings, "38 B"). | Added `format.ts` with console-style `formatAwsDate` and `formatBytes`. |

## S3

| Severity | Finding | Status |
| --- | --- | --- |
| High | Create bucket, delete bucket and upload went through the inventory endpoint and did nothing while reporting success. | Fixed: `CreateBucket`, `DeleteBucket`, `PutObject` (text or file, multipart above 5 MB) call the bucket endpoints. |
| High | The object list read `contents`, but the API returns `objects`, so every bucket showed "No objects". | Fixed: `ListObjectsV2` with `Delimiter=/`, folders as prefixes with breadcrumb navigation. |
| High | Delete object sent `{Key}` where the API wants `{key}`, and the client ignored the error, so the console said "deleted" while nothing was. | Fixed; errors now surface. |
| High | Bucket policy: the read showed `{"policy":null}` or `[object Object]`, saving always failed (string sent, object required), and "remove" called a DELETE route that did not exist (405) yet reported success. | Fixed: `GetBucketPolicy`/`PutBucketPolicy`, and a new `DeleteBucketPolicy` route. |
| High | CORS read the wrong field, so a sample config was shown as if it were configured; save sent the wrong field, so it wrote an empty rule set. | Fixed: shows the real `CORSRules`, or "No configurations to display". |
| High | Block Public Access read and wrote the wrong field: it always showed all four settings on, and saving always failed. | Fixed: shows the real `GetPublicAccessBlock` values; turning a setting off asks for confirmation, as AWS does. |
| High | Object details read `ETag`/`ContentType` where the API returns `etag`/`content_type`, so ETag was always "N/A", type always "application/octet-stream", storage class hard-coded "STANDARD", last modified "Just now". | Fixed: values come from `HeadObject`. |
| High | Download links pointed to a URL that does not exist. | Fixed: uses `/objects/download/?key=`. |
| High | Presigned URLs from the Docker dashboard pointed at `http://floci:4566`, which the browser cannot resolve. | Fixed: signed against `FLOCI_PUBLIC_ENDPOINT_URL` (set to `http://localhost:${FLOCI_PORT}` in `docker-compose.yml`). The running dashboard on 8001 needs a `docker compose up -d dashboard` to pick it up. |
| Medium | Versioning showed "Suspended" for a bucket that never had versioning; AWS shows "Disabled" and only allows Enable or Suspend. | Fixed. |
| Medium | Default encryption and Object Lock said "Unavailable locally", but Floci supports both reads. | Fixed: `GetBucketEncryption` (SSE-S3/SSE-KMS/DSSE-KMS wording) and `GetObjectLockConfiguration`. |
| Medium | Delete bucket and delete object had no confirmation. Empty bucket was missing. | Fixed: typed confirmations ("bucket name", "permanently delete"; "delete" when versioning adds a delete marker). Added Empty. |
| Medium | Website endpoint was hard-coded to `localhost:4566` without the Region. | Fixed: `http://<bucket>.s3-website-<region>.<host>:<port>`, which Floci serves (verified). |
| Medium | Event notifications had no name (`Id`), a pre-filled fake queue ARN, free-text event types, no filters and no delete. | Fixed: name, prefix/suffix filters, event type multiselect, destination type, and delete. |
| Medium | "Access" column always said "Not evaluated". | Fixed: derived from Block Public Access and the bucket policy, using the console's wording. |
| Low | Tabs were "Properties & Hosting" and "Permissions & Policy"; AWS uses Objects / Properties / Permissions. | Fixed. Bucket tags, overview and console-style labels added. |
| Low | Create bucket had no name validation and a hard-coded "us-east-1". | Fixed: S3 naming rules client-side; Region from the session. |

Left as is:
- Floci accepts a public bucket policy while `BlockPublicPolicy` is on; AWS rejects it with AccessDenied. That is emulator behavior, not a console bug.
- Presigned URLs are SigV2 (boto3's default for this client). The console uses SigV4 and caps the expiry at 12 hours. Changing the signer needs a separate client config, because cached clients are keyed without it.
- The Metrics, Management (lifecycle, replication, inventory) and Access Points tabs are not built. Lifecycle has a backend route but no UI; replication and inventory are not implemented in Floci.
- Object versions ("Show versions") are not listed in the UI, although the API supports `ListObjectVersions`.

## EC2 and VPC

| Severity | Finding | Status |
| --- | --- | --- |
| High | Launch, start, stop, reboot and terminate went through the inventory endpoint and did nothing while reporting success. Launch also used an AMI ID that Floci does not have. | Fixed: `RunInstances` and the per-instance state endpoints. Launch chooses from `DescribeImages`, `DescribeInstanceTypes`, subnets and security groups. |
| High | Adding a security group rule sent `IpProtocol/FromPort/IpRanges`, but the API expects `protocol/from_port/cidr`, so it always failed. | Fixed. Rules can now also be added to outbound and removed (`Revoke*`). |
| High | Delete VPC, subnet and security group ignored HTTP errors, so a `DependencyViolation` was shown as "deleted". | Fixed: errors surface, and each delete asks for a typed "delete" confirmation. |
| High | Security group rules that reference another security group or a prefix list were shown with a made-up source of `0.0.0.0/0`. | Fixed: one row per CIDR, IPv6 CIDR, security group or prefix list, with Type, Protocol, Port range, Source and Description columns. |
| Medium | VPC state was hard-coded "available"; "Default VPC" showed "Custom"; Name tags were not shown. | Fixed: real `State`, Default VPC Yes/No, Name column. |
| Medium | Route tables showed "Main" whenever a table had no associations. | Fixed: Main comes from `Associations[].Main`; explicit subnet associations and routes are listed. |
| Medium | Internet gateway state showed the raw attachment state, and "available" for detached gateways. | Fixed: Attached or Detached, as the console shows it. |
| Medium | Every non-running instance state was shown with the same "stopped" icon. Stop, reboot and terminate had no confirmation. | Fixed: pending, stopping and shutting-down show as in progress; stop, reboot and terminate ask first. |
| Medium | "Execute Shell Script (SSM)" showed only the SendCommand receipt, never the output. | Fixed: polls `GetCommandInvocation` and shows status, response code, stdout and stderr. |
| Low | Instance details lacked AMI ID, Availability Zone, DNS names, IAM role and launch time; column names did not match the console. | Fixed. |

Left as is:
- Status checks, monitoring and the instance Storage tab are not shown; Floci does not report instance status checks.
- Key pairs, volumes, snapshots, Elastic IPs, NAT gateways, network ACLs, launch templates and flow logs have backend routes but no dedicated tables in this console. They are listed in the inventory and the generic workbench. Building them out is a larger redesign.
- Floci stores security group rules but local Docker networking does not enforce them; the rule dialog says so.

## IAM

| Severity | Finding | Status |
| --- | --- | --- |
| High | Create user, delete user, create/delete policy, attach/detach policies, access keys and group membership all went through the inventory endpoint and did nothing while reporting success. | Fixed: each calls its IAM endpoint (`CreateUser`, `DeleteUser`, `CreatePolicy`, `Attach*Policy`, `CreateAccessKey`, `AddUserToGroup` and so on). Delete role and group now surface errors. |
| High | Every user and role was shown with an invented attached policy (`AdministratorAccess` for users, `AWSLambdaBasicExecutionRole` for roles), because the console read `AttachedPolicies` while the API returns `attached_policies`. For a learning tool this taught the wrong permissions. | Fixed: real attached managed policies and inline policies (inline documents open on click). |
| High | Every role showed a made-up Lambda trust policy, because the console read `assume_role_policy_document` while the API returns `trust_policy`. | Fixed: real trust policy, and a "Trusted entities" column derived from it, as in the Roles list. |
| High | Every policy showed an `Allow *:*` document, and an empty policy list was backfilled with five invented AWS managed policies and fake attachment counts. | Fixed: documents come from `GetPolicy` + `GetPolicyVersion`; only real policies are listed, with real attachment counts and versions. |
| High | Group membership was never shown (the console read `Users` while the API returns `users`), and removing a user from a group was not awaited or refreshed. | Fixed. |
| High | The policy simulator read `results`, but the API returns `evaluations`, so it never showed results. It also offered the account root, which `SimulatePrincipalPolicy` does not accept. | Fixed: users and roles only; results show allowed / explicit deny / implicit deny and matched statements. |
| Medium | Fallbacks invented values: user "Alice", user ID `AIDA0123456789EXAMPLE`, today's date as creation date. | Removed. Missing values show "-". |
| Medium | No confirmation for deleting users, roles, policies, groups or access keys. | Fixed: typed-name confirmations that say what the delete also removes. Access keys must be deactivated before deletion, as in the console. |
| Medium | Create user silently added a dashboard-only inline policy. | Kept (Floci needs it to resolve the user's identity in this dashboard), but it is now an explicit checkbox that says it is not AWS behavior. |
| Low | Labels and structure differed from the console ("Add User", "Trust Relationships", "IAM Policies"). | Fixed: User groups / Users / Roles / Policies, "Trusted entities", "Security credentials", max session duration in hours, Console access, Last used. |
| Low | Delete policy errors were labeled "Create managed policy". | Fixed in `iam_views.py`. |

Left as is:
- Only customer managed policies are listed. The inventory does not enumerate AWS managed policies, so they can be attached by ARN but not browsed.
- Policy versions, permissions boundaries, login profiles and tags have backend routes but no editors here.
- Identity providers, Access Analyzer and account settings are not emulated.

## Lambda

| Severity | Finding | Status |
| --- | --- | --- |
| High | Create, delete, invoke, configuration and environment updates, and alias create/delete went through the inventory endpoint and did nothing while reporting success ("Function invoked successfully" with the inventory as the "result"). | Fixed: `CreateFunction`, `DeleteFunction`, `Invoke`, `UpdateFunctionConfiguration`, `CreateAlias`, `DeleteAlias`. |
| High | Create function sent the string `dummy` as the deployment package and a hard-coded role. | Fixed: "Author from scratch" with the console's Hello World code, zipped by the backend (new `InlineSource` code option), and an execution role picker that suggests roles trusting `lambda.amazonaws.com`. |
| High | Adding an SQS trigger always sent `StartingPosition: LATEST`, which AWS rejects for SQS. | Fixed: only stream sources get a starting position. |
| High | The Function URL shown was invented (`<function-name>.lambda-url.localhost:4566`). The real one uses a hash ID and Floci's container hostname. | Fixed: shows the real `FunctionUrl` plus a browser-reachable version (`BrowserUrl`), verified to invoke the function. New URLs no longer get a wildcard CORS policy the console would not add. |
| Medium | Defaults were invented: timeout 30 s (AWS default is 3 s), memory 128 MB, name "my-function", and a fake "$LATEST — Active working draft" version row. | Removed; values come from `GetFunctionConfiguration` and `ListVersionsByFunction`. |
| Medium | Test results were raw JSON of the dashboard response. | Fixed: "Execution result: succeeded/failed", status code, executed version, function error, response and log tail, as on the console's Test tab. |
| Medium | Delete function and delete alias had no confirmation. Memory and timeout were not validated. | Fixed ("confirm" typed confirmation, 128–10240 MB, 1–900 s). |
| Low | List columns and labels did not match the console. | Fixed: Description, Package type, Runtime, Last modified; triggers show State, Last processing result and UUID; versions show aliases and last modified. |

Left as is:
- Floci returns no `LogResult` for synchronous invokes, so the Test tab's log section is empty; the Monitor tab reads the CloudWatch log group instead.
- There is no in-browser code editor or deploy; `UpdateFunctionCode` is supported by the backend (now including inline source) but not wired to an editor.
- Layers, destinations, concurrency, permissions (resource policy), VPC and tags have backend routes but no editors in this console.

## DynamoDB

| Severity | Finding | Status |
| --- | --- | --- |
| High | Create table, delete table, put item, delete item, capacity changes, point-in-time recovery and TTL all went through the inventory endpoint and did nothing while reporting success. Create/delete table, capacity and PITR had no backend route at all. | Fixed: new routes for `CreateTable`, `DeleteTable`, `UpdateTable` (capacity mode) and `Describe/UpdateContinuousBackups`; items and TTL use their existing routes. |
| High | Putting an item with a decimal number (for example `19.99`) failed with "Float types are not supported". | Fixed: JSON numbers are converted to `Decimal` before serialization. |
| High | PITR always showed "Disabled" because the inventory never read continuous backups; the stream view type showed `NEW_AND_OLD_IMAGES` even when streams were off. | Fixed: inventory reads `DescribeContinuousBackups`; streams show Off, or On with the real view type. |
| Medium | Capacity mode defaulted to on-demand when `BillingModeSummary` is absent, which in AWS means the table has always been provisioned. | Fixed. Capacity mode is shown as "On-demand" / "Provisioned". |
| Medium | Deleting an item skipped a sort key of `0` (falsy check), and new items were pre-filled with invented attributes. | Fixed: the item template contains only the key attributes, and saving validates them. |
| Medium | No confirmation for deleting tables or items. | Fixed ("confirm" typed for tables, as the console does). |
| Medium | Creation date, deletion protection and table class were never shown. | Fixed: added to the inventory and Overview tab. |
| Low | Status values were raw (`ACTIVE`), list columns did not match the console, and the PartiQL "latency" badge looked like a server metric. | Fixed: Active/Creating, Indexes, Deletion protection, Read/write capacity mode, Total size; the timing is labeled as round-trip time. |

Left as is:
- The PartiQL editor allows only `SELECT` (the backend refuses writes), and the items view is a Scan without the console's Query builder; `Query` exists in the backend.
- Creating GSIs/LSIs, enabling streams, backups (on-demand), exports and global tables are not in this console.

## Cross-cutting: Cognito identity pools

`createCognitoIdentityPool` also used the broken action path, and the dashboard had no route for it. A `POST /api/cognito/identity-pools/` route (`CreateIdentityPool`) now exists; Floci supports Cognito identity pools.

## SQS

| Severity | Finding | Status |
| --- | --- | --- |
| High | Delete queue and delete message ignored HTTP errors, so a failed delete (for example a queue that no longer exists) was reported as deleted. | Fixed. |
| High | Creating a FIFO queue silently turned on content-based deduplication. AWS leaves it off by default, and it changes whether senders must supply a deduplication ID. | Fixed: it is an explicit option, off by default. Send checks for a group ID and, when needed, a deduplication ID. |
| Medium | Attributes missing from the API were filled with defaults (visibility timeout 30, 256 KB max size, 0 messages), so the console could show values the queue did not report. Floci actually reports a 1 MiB maximum, which AWS adopted in 2025. | Fixed: values come from `GetQueueAttributes`; missing values show "-". |
| Medium | Purge and delete used plain confirmations. | Fixed: typed "purge" and "confirm", as in the console. |
| Medium | Per-message delivery delay was offered for FIFO queues, which SQS rejects. | Fixed: disabled for FIFO, with the reason. |
| Medium | Create queue offered only a name, a FIFO checkbox and visibility timeout. | Fixed: Standard/FIFO type tiles (with "can't change later"), retention, delivery delay, receive wait time and content-based deduplication, with AWS ranges validated client- and server-side. |
| Low | Encryption, content-based deduplication, redrive allow policy and DLQ source queues were not shown; durations were raw seconds. | Fixed: SSE-SQS/SSE-KMS, durations like "4 days", parsed redrive policy, message sent time and receive count. |

Left as is:
- Editing queue attributes, access policy, redrive policy and starting a DLQ redrive have backend routes (`/attributes/`, `/redrive/`) but no editors in this console.

## SNS

This console was already close to the AWS console: it reads real attributes, distinguishes pending and confirmed subscriptions, and says that a publish is not proof of delivery.

| Severity | Finding | Status |
| --- | --- | --- |
| Medium | FIFO topics were created with content-based deduplication on (both in the form and in the backend default). AWS leaves it off. | Fixed in both places. |
| Medium | Creating a subscription with the default `{}` filter policy stored an explicit empty `FilterPolicy` and `FilterPolicyScope`. | Fixed: an empty policy is omitted, as the console does. |
| Medium | Delete topic used a plain confirmation. | Fixed: typed "delete me", as in the console. |
| Low | Encryption showed "Not configured" and content-based deduplication showed raw `true/false` for standard topics; labels used Title Case. | Fixed: Enabled/Disabled, "-" for standard topics, topic owner, console wording in the create, subscribe and publish dialogs. |

Left as is:
- Floci accepts email, SMS and HTTP(S) subscriptions but does not deliver to real inboxes, phones or confirmation URLs; SQS and Lambda subscriptions are delivered. The console does not claim delivery.
- Editing the topic access policy, delivery policy and data protection policy is not implemented.

## CloudWatch

Already accurate in substance: alarms, metrics, log groups and streams all come from the API, and Floci evaluates alarms (state reasons are real).

| Severity | Finding | Status |
| --- | --- | --- |
| Low | Alarm states showed raw enum values (`ALARM`, `INSUFFICIENT_DATA`); the console shows "In alarm" and "Insufficient data". | Fixed (the Set state dialog still uses the API values). |
| Low | The alarm list lacked the console's "Last state update" and "Actions" columns; "Threshold" is called "Conditions". | Fixed. |
| Low | Stored bytes were raw numbers, and labels used Title Case ("Create Alarm", "Retention Settings", "CloudWatch Log Viewer"). | Fixed. |

Left as is:
- Dashboards, composite alarms, anomaly detection and Logs Insights queries are not in this console (Floci supports Logs Insights).

## API Gateway

Already accurate: REST and HTTP APIs, routes/resources, integrations, stages and deployments come from the API, and the test console sends real requests through Floci.

| Severity | Finding | Status |
| --- | --- | --- |
| Medium | The stage invoke URL was built from the dashboard's internal endpoint, so in Docker it pointed at `http://floci:4566`. | Fixed: built from `FLOCI_PUBLIC_ENDPOINT_URL`. The AWS-style `api_endpoint` (`*.execute-api.<region>.amazonaws.com`) is shown as reported, since Floci serves APIs under `/restapis/<id>/<stage>/_user_request_/`. |
| Medium | Delete API used a plain confirmation. | Fixed: typed "confirm". |
| Low | Unknown stage counts showed "Unavailable". | Shows "-". |

Left as is:
- Creating routes, integrations, authorizers and stages is not in this console (create API only); usage plans, API keys and custom domains are listed in the inventory but have no pages.

## RDS

Already sound: instances, clusters and parameter groups come from the API, credentials are never pre-filled, and backend errors stay in the dialog.

| Severity | Finding | Status |
| --- | --- | --- |
| Medium | Deleting an instance or cluster defaulted to "Skip final snapshot". The console defaults to creating a final snapshot (named `<id>-final-snapshot`) and asks you to type "delete me". | Fixed for instances and clusters. |
| Low | Status and engine showed raw API values (`available`, `aurora-postgresql`). | Fixed: "Available", "Aurora PostgreSQL", "PostgreSQL" and so on, in the list and detail pages. |

Left as is:
- Final snapshot creation on delete was checked against the API contract and tests, not against a live Floci instance, to avoid destroying the lab databases.
- Snapshots, automated backups, read replicas and Performance Insights are not in this console.

## ECS

| Severity | Finding | Status |
| --- | --- | --- |
| High | Container instances showed an invented EC2 instance ID (`i-0abcd1234ef567890`) when none was reported, and "Agent connected: Connected" for every instance. | Fixed: real `ec2InstanceId` and `agentConnected`, or "-". |
| High | Every cluster and service showed a green "ACTIVE" status (hard-coded success), and missing service/task counts fell back to account-wide totals. | Fixed: real status with matching indicator (Active, Provisioning, Draining, Inactive), real counts or "-". |
| Medium | Missing values were invented: desired/running count 1, launch type FARGATE, task CPU/memory 256/512, revision 1, family "default-task", compatibilities FARGATE+EC2. | Removed. |
| Medium | Stop task and deregister task definition had no confirmation. | Fixed. |

## EKS

| Severity | Finding | Status |
| --- | --- | --- |
| Medium | Create cluster, node group and Fargate profile forms were pre-filled with role ARNs, subnet IDs and security group IDs that do not exist (`subnet-01234567`, `eks-cluster-role`). Submitting them as-is failed or created resources pointing at nothing. | Fixed: fields start empty with those values as placeholders. |
| Low | A Fargate profile with no selectors showed namespace "default". | Shows "-". |

## Cognito

| Severity | Finding | Status |
| --- | --- | --- |
| High | Delete user ignored HTTP errors, and had no confirmation. | Fixed. |
| Medium | Creating an identity pool posted to a non-existent route. | Fixed (see Cross-cutting). |
| Medium | Identity pools were always created with guest (unauthenticated) access on. AWS leaves it off unless you opt in. | Fixed: a "Guest access" checkbox, off by default. |

Left as is:
- Test passwords and the `developer-alice` username are pre-filled in the user and sign-in forms for convenience. They are clearly sample values in a local learning tool.

## Elastic Load Balancing

| Severity | Finding | Status |
| --- | --- | --- |
| Medium | Register targets was pre-filled with a fake instance ID (`i-0123456789abcdef0`). | Fixed: empty, with the example as a placeholder. |
| Medium | Delete listener had no confirmation. | Fixed. |

## Secrets Manager

| Severity | Finding | Status |
| --- | --- | --- |
| Medium | Delete secret defaulted to a 7-day recovery window (UI, view and API helper). The AWS default and console default is 30 days. | Fixed in all three. The force-delete option is labeled as API/CLI-only, since the console always schedules deletion. |

## Generic service workbench

Used by services without a dedicated page. All 539 action definitions in `services.py` were checked: each path resolves to a Django route, and each route allows the action's HTTP method.

| Severity | Finding | Status |
| --- | --- | --- |
| Medium | Delete-kind actions not marked `destructive` ran without confirmation. | Fixed: any delete or destructive action asks first. |
| Low | The header said "Local AWS workbench", which read like a console page. | Now says it is the generic workbench for services without a dedicated console. |

## Settings and other dashboard pages

| Severity | Finding | Status |
| --- | --- | --- |
| High | The Settings connection test always showed "Health Status: Healthy", even when `/_floci/health` failed, and fell back to account `000000000000` and `arn:aws:iam::000000000000:root` when the identity could not be resolved. | Fixed: shows the real health result and error, and says when the identity is only a local hint. |

Console Home, Activity, Environment, CLI, Inspector, Labs and Resource Graph are dashboard tools rather than AWS consoles. Their health and status values come from real checks (Resource Graph marks a connection healthy when both resources exist).

## SES

Already careful: identity status comes from the inventory, never from a successful verification request.

| Severity | Finding | Status |
| --- | --- | --- |
| Medium | Floci verifies identities immediately, which differs from AWS (email identities stay Pending until the link is opened; domains until DNS records are published). The console did not say so. | Fixed: the verification message explains the difference. |

## EventBridge

Already accurate (rules, buses, targets and the pattern tester use real data). One "N/A" placeholder for a missing rule description now shows "-".

## Step Functions

Already accurate: definitions, executions, history and the activity workers come from the API.

| Severity | Finding | Status |
| --- | --- | --- |
| Low | State machine type showed the raw enum (`STANDARD`) and the list lacked the console's "Creation date" column. | Fixed: "Standard" / "Express" and Creation date. |

## CloudFormation

Already accurate: stacks, events, resources, outputs and change sets come from the API, and failed change sets can't be executed.

| Severity | Finding | Status |
| --- | --- | --- |
| Low | Labels used Title Case ("Create Stack", "Created Time", "Confirm Delete"). | Fixed to console wording. Stack statuses stay as the raw enum (`CREATE_COMPLETE`), which is also how the CloudFormation console shows them. |

## Consoles reviewed with no changes needed

These were checked for invented values, unchecked errors, missing confirmations and wording. Each reads its values from the API and confirms destructive actions:

- **KMS**: key state, rotation, aliases, grants and policies are real; disable and revoke confirm. Left: no "Schedule key deletion" action (7–30 day waiting period), so keys can't be deleted from this console.
- **SSM Parameter Store**: SecureString values stay hidden until you choose "Reveal decrypted value", as in AWS.
- **ECR**: repositories, images, tag mutability and lifecycle policies are real.
- **Route 53**, **CloudFront**, **AppSync**, **Athena** and **CloudTrail**: data comes from the API; failed reads are shown as errors rather than empty or invented data.

