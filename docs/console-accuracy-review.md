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
- ~~Floci accepts a public bucket policy while `BlockPublicPolicy` is on.~~ Corrected in the second review: Floci 2.3.0 rejects it with AccessDenied, as AWS does (`S3Service.putBucketPolicy`; confirmed live).
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


# Second review

A second pass that picks up the first review's "Left as is" items, reviews the 38 services that use the
generic workbench (`GenericServiceWorkbench.tsx`) for accuracy rather than routing, and takes a deeper look
at the consoles that got only a light pass (KMS, SSM, ECR, Route 53, CloudFront, AppSync, Athena, CloudTrail).
Severity uses the same scale as above.

## Summary

- **Empty lists that weren't empty.** Inventories called `get_paginator` for list operations botocore can't
  paginate (Firehose, Athena work groups, Transcribe, EventBridge buses, OpenSearch packages, Cost Explorer,
  DynamoDB Streams, Secrets Manager versions). The error was swallowed, so those lists always read as empty:
  a Firehose stream you had just created never appeared. `_paginate` now follows the operation's own token.
- **Wrong resources in a list.** The Neptune page listed DocumentDB clusters and the other way round, because
  `DescribeDBClusters` returns every RDS-family engine.
- **Actions that did the wrong thing or quietly nothing.** Transfer Family's workbench included IAM "Update
  user" and "Create login profile" actions. Firehose created streams with no destination, which AWS rejects;
  Floci accepts them and delivers their records to its own `floci-firehose-results` bucket. RDS "Create final snapshot" is accepted by Floci
  but no snapshot is made; the console now checks and says so.
- **The generic workbench is now safer and closer to the console.** Only real resource lists are offered as
  resource types; selecting a resource fills the fields that identify it or its parent, but never points a
  delete at the parent; deletes need "delete" typed; enum fields are dropdowns with the AWS values; defaults
  are visible and match AWS; emulator notes are shown, without stale release-changelog lines.
- **Several "Left as is" gaps are filled**: KMS key deletion scheduling and aliases, S3 "Show versions" and SigV4
  presigned URLs, SQS queue editing and DLQ redrive, DynamoDB Query and PartiQL writes, a Lambda code editor
  with Deploy, SNS topic access policy editing.

Verification: see the end of this section.

Method: as in the first review, against a disposable Floci on port 14566 and a second dashboard on port 18001.
Every generic-workbench service was populated with boto3 (streams, delivery streams, Glue databases and tables,
ElastiCache, OpenSearch, MSK, Neptune, DocumentDB, MemoryDB, Transfer servers and users, Transcribe jobs,
CodeBuild/CodeDeploy/CodePipeline, Config, Backup, AppConfig, Pipes, Scheduler, IoT, ACM, Auto Scaling, Batch,
EMR, WAF, Amazon MQ, Cloud Map, Elastic Beanstalk, S3 Vectors), then each inventory and action was checked
against the AWS API reference and the console. A script checked which form fields a selected resource fills for
every action and resource type. Emulator behavior was first established by live probes, then each claim was
checked against Floci's source at the 2.3.0 tag and at `main` (see "Checked against the Floci source" below).

## Cross-cutting (second review)

| Severity | Finding | Status |
| --- | --- | --- |
| High | `_paginate` used `get_paginator` for operations botocore can't paginate; the error was swallowed, so Firehose delivery streams, Athena work groups, Transcribe jobs and vocabularies, EventBridge buses, OpenSearch packages and VPC endpoints, Cost Explorer tags and categories, DynamoDB Streams and Secrets Manager version lists were always empty. | Fixed: falls back to the operation's `NextToken`/`NextPageToken` (or Firehose's and DynamoDB Streams' start keys), with a loop guard. |
| Medium | Action errors that AWS returns as 4xx but that weren't in the known code lists (`HostedZoneNotEmpty`, `ResourceInUse`, ...) were returned as HTTP 502, which reads as a dashboard or emulator failure. | Fixed: the AWS 4xx status is kept. |
| Low | The shared value renderer showed every boolean as Enabled/Disabled ("Deleted: Disabled", CloudTrail "Read only: Disabled") and timestamps as raw ISO strings. | Fixed: Enabled/Disabled only for feature switches, Yes/No otherwise; console date format. |

## Generic workbench

The 38 services without a dedicated console: ACM, AppConfig, Amazon MQ, Auto Scaling, Backup, Batch, BCM Data
Exports, Bedrock Runtime, Cloud Map, Cloud Control, CodeBuild, CodePipeline, CodeDeploy, Config, Cost Explorer,
CUR, DocumentDB, MemoryDB, EMR, ElastiCache, Elastic Beanstalk, Firehose, Glue, IoT, MSK, Kinesis, Neptune,
OpenSearch, Pipes, Pricing, RDS Data, Resource Groups Tagging, S3 Vectors, Scheduler, Textract, Transcribe,
Transfer Family and WAF v2.

| Severity | Finding | Status |
| --- | --- | --- |
| High | Firehose delivery streams never appeared (see Cross-cutting). | Fixed. |
| High | Neptune listed DocumentDB (and other RDS-family) clusters and DocumentDB listed Neptune ones. | Fixed: each lists only its own engine, in the inventory and the resource graph. |
| High | Transfer Family's actions included IAM `Update user` (`PATCH /api/iam/users/{user}/`) and `Create login profile`; "Update user" on an SFTP user would have renamed an IAM user. | Removed. Every workbench action now stays within its own service's API (checked by a test). |
| Medium | Firehose "Create delivery stream" took only a name. AWS requires a destination; Floci accepts a stream without one and delivers its records to its own `floci-firehose-results` bucket, which has no AWS equivalent. The inventory note also said Floci flushes "after every 5 records"; 2.3.0 flushes on the buffering hints, as AWS does. | Fixed: Direct PUT with an Amazon S3 destination (bucket and role required), the console's 5 MiB / 300 s buffer defaults, validated ranges. The notes describe the real flush and default-bucket behavior. |
| High | Selecting a resource could fill the wrong resource's form. Rows and actions use different names (`name` vs `{stream}`), and a naive fill put an Auto Scaling group's name into "Delete launch configuration" or a user's server into "Delete server". Before, almost nothing was filled at all. | Fixed: a field is filled from the selected row when the row's type matches the field (`streams` → `{stream}`, `config_rules` → `{rule}` via `ConfigRuleName`), or from a parent reference the row carries (`application_name` on a deployment group). Delete and update forms are filled only when the selected row is their target. ACM actions take `{certificate_arn}`. |
| Medium | Resource types included lists that aren't resources: SDK operation lists that escaped the filter, enum reference lists (ACM key algorithms, Auto Scaling adjustment types), state tallies, Textract stub blocks, and "Notes". | Fixed: only lists of objects that aren't reference data are resource types. Emulator notes and "not emulated" lists show in an info box, "How Floci emulates this service". Release-changelog notes ("Floci 1.5.20 adds ...") describe old versions and are not shown; three internal "inferred from the SDK" notes were removed. |
| Medium | Deletes used a plain confirmation that didn't name the target. | Fixed: deletes need "delete" typed and show the target identifiers. Stopping a backup job and detaching Auto Scaling instances are no longer treated as deletes. |
| Medium | Enum fields were free text (Kinesis stream mode, ACM validation method and key algorithm, AppConfig profile type, Cloud Map namespace type, CodeDeploy compute platform, CodePipeline retry mode and approval status, lifecycle hook transition and default result, Glue schema format and compatibility, Pipes desired state, Scheduler state, Transcribe media format, Transfer endpoint type and domain, OpenSearch volume type, ElastiCache user auth type). | Fixed: fields can declare `options` and a `default`, shown as a select with AWS values. A declared default is visible in the form and sent unless changed, instead of being applied silently by the backend. |
| Medium | Hidden backend defaults differed from AWS: CodeDeploy compute platform `Lambda` (AWS: `Server`), lifecycle hook default result `CONTINUE` (AWS: `ABANDON`), OpenSearch `gp2` (console: `gp3`), Transcribe `MediaFormat` forced to `mp4` (AWS detects it), ElastiCache auth type `no-password` (AWS: `no-password-required`; Floci rejects `no-password` too, so creating such a user failed). | Fixed in the API helpers and views, and shown in the forms. |
| Medium | Neptune "Delete DB cluster" showed "Skip final snapshot" unchecked but the backend defaulted to skipping. Floci never creates Neptune snapshots. | The checkbox now starts checked and says Floci doesn't create Neptune snapshots. |
| Medium | Amazon MQ showed 0 users for RabbitMQ brokers. RabbitMQ brokers have no Amazon MQ user API (AWS rejects `ListUsers`); their users live in the RabbitMQ console. | Shows "-" (unknown), and `ListUsers` is no longer called for them. |
| Low | Batch job times were epoch milliseconds; Auto Scaling groups didn't show their launch template; Kinesis capacity mode was buried in an object; Transfer users and Glue tables were only nested inside their parents. | Fixed: ISO timestamps, `launch_template_name`, `capacity_mode`, and `users` / `tables` lists. |
| Low | Glue crawlers and jobs were not listed, although Floci supports `GetCrawlers` and `GetJobs`. | Fixed: `crawlers` (state, database, schedule, last crawl status) and `jobs` (command, script, Glue version) lists. |
| Low | OpenSearch reports its endpoint as the container address (`http://172.x.x.x:9200`). | Note added: use the host port Floci publishes (9400-9499). |

Left as is:
- Most status values are shown as the API enum (`ACTIVE`, `available`), which many consoles also do; a generic
  humanizer would be wrong for some services (CloudFormation keeps the enum).
- Raw JSON fields remain for nested inputs (Backup plans, CodeBuild project options, Config rules, AppSync
  resolvers). Turning each into a structured form is per-service console work.
- ACM issues requested certificates immediately, with no "Pending validation" state, unless Floci is started with
  `FLOCI_SERVICES_ACM_VALIDATION_WAIT_SECONDS` above 0; the emulator notes say so.
- Config rule compliance and WAF CloudFront-scope resources were not added.

## "Left as is" from the first review

| Service | Item | Status |
| --- | --- | --- |
| KMS | No "Schedule key deletion". | Fixed: 7-30 day waiting period (default 30; the API helper defaulted to 7 and silently clamped out-of-range values), acknowledgement checkbox, "Cancel key deletion" for keys pending deletion. Key states read as in the console ("Pending deletion"). |
| RDS | Final snapshot on delete verified only against the API contract. | Verified live: Floci 2.3 accepts `SkipFinalSnapshot=false` with a name on instance and cluster delete but creates no snapshot. The delete now checks for the snapshot, and the console warns that the data is not kept instead of implying it is. |
| S3 | Presigned URLs were SigV2; the console uses SigV4 with a 12-hour cap. | Fixed: the S3 client signs with SigV4 (verified: the URL downloads the object with signature validation on); the console offers up to 12 hours. |
| S3 | Object versions not listed. | Fixed: "Show versions" toggle with Version ID column, delete markers, version download and presign, and permanent delete of a version (typed "permanently delete"). Per-key `DeleteObjects` errors, which arrive in a 200 response, now surface. |
| SQS | No editors for queue attributes, access policy or redrive. | Fixed: "Edit" for visibility timeout, retention, delivery delay, maximum message size and receive wait time (validated against the SQS limits), the dead-letter queue (same queue type only, maximum receives 1-1000) and the access policy; only changed attributes are sent. Dead-letter queues get "Start DLQ redrive". The redrive route built a source ARN from a fixed account and Region; it now looks up the queue's ARN. |
| DynamoDB | PartiQL allowed only SELECT; items view was Scan only. | Fixed: "Scan or query items" (table or index, partition key, sort key condition including between and begins_with, number validation, sort order). PartiQL runs INSERT, UPDATE and DELETE as in the console. |
| Lambda | No code editor or deploy. | Fixed: "Code source" lists the package files from `GetFunction`'s `Code.Location` (fetched through the configured Floci endpoint only), edits text files and deploys. Deploy rebuilds the current package with the edited files replaced, so other files are kept. Verified live: edit, deploy, invoke returns the new code. |
| SNS | Topic access policy not editable. | Fixed: Edit/Save changes with JSON validation via `SetTopicAttributes`. |
| S3 | Metrics, Management (lifecycle, replication, inventory) and Access Points tabs. | Left: larger console work; replication and inventory are not in Floci. |
| S3 | "Floci accepts a public bucket policy while Block Public Access is on." | Wrong: Floci 2.3.0 rejects it with AccessDenied, as AWS does. The first-review line is corrected. |
| EC2/VPC | Key pairs, volumes, snapshots, Elastic IPs, NAT gateways, network ACLs, launch templates and flow logs have no tables; no status checks. | Left: a larger redesign; Floci reports no status checks. |
| IAM | AWS managed policies can't be browsed; no editors for policy versions, permissions boundaries, login profiles or tags. | Left: larger console work. |
| Lambda | No `LogResult` from Floci (confirmed in source: `InvokeResult.setLogResult` is never called); layers, destinations, concurrency, resource policy, VPC and tags have no editors. | Left: emulator behavior / larger console work. |
| DynamoDB | GSIs/LSIs, streams, on-demand backups, exports and global tables. | Left: larger console work. |
| SNS | Delivery policy and data protection policy editors; email/SMS/HTTP delivery. | Left: the backend now accepts `DeliveryPolicy`, but no editor was built; delivery is emulator behavior. |
| CloudWatch | Dashboards, composite alarms, anomaly detection, Logs Insights. | Left: larger console work (Floci supports Logs Insights). |
| API Gateway | Creating routes, integrations, authorizers and stages; usage plans, API keys, custom domains. | Left: larger console work. |
| RDS | Snapshots, automated backups, read replicas, Performance Insights. | Left: larger console work. |
| Cognito | Sample passwords and `developer-alice` pre-filled. | Left: clearly sample values in a learning tool. |

## Consoles with only a light pass in the first review

| Console | Severity | Finding | Status |
| --- | --- | --- | --- |
| KMS | Medium | No key deletion (see above) and no way to manage aliases. | Fixed: deletion scheduling, plus an Aliases tab with create and delete (`alias/aws/` refused). |
| SSM | Medium | The create form had no Tier, Data type or KMS key choice, and nothing checked the 4 KB (Standard) / 8 KB (Advanced) limit. | Fixed, with the console defaults (Standard, `text`, `alias/aws/ssm`); an advanced parameter can't be moved back to standard. |
| ECR | Medium | Deleting a repository or images used a plain confirmation; the console requires "delete" typed. | Fixed. |
| Route 53 | Medium | Records and hosted zones couldn't be deleted (the backend supported both). | Fixed: record delete (apex NS and SOA excluded), zone delete with "delete" typed; a non-empty zone shows AWS's `HostedZoneNotEmpty` message. |
| Route 53 | Low | Title Case labels and only five record types. | Fixed: console wording; NS, PTR, SRV, CAA, NAPTR, SPF and DS added. |
| CloudFront | Medium | New distributions used legacy cache settings (`ForwardedValues`); the console defaults to a cache policy with the managed CachingOptimized policy. | Fixed: cache policy select with the managed policies (default CachingOptimized), custom policies and legacy settings. Labels use console wording. |
| CloudTrail | Medium | New trails were single-Region without global service events and did not start logging; the API helper defaulted `IncludeGlobalServiceEvents` to false (AWS defaults it to true). | Fixed: multi-Region with global service events, and logging starts after creation (optional), as in the console. Verified live. |
| AppSync | Medium | API key expiry was entered as a Unix timestamp. | Fixed: days until expiry (1-365, default 7), rounded down to the hour as AppSync does. |
| Athena | Low | Title Case labels in the create workgroup form. | Fixed. |

Left as is:
- KMS rotation period (90-2560 days) isn't shown or set: Floci 2.3.0 range-checks `RotationPeriodInDays`, then
  discards it, and `GetKeyRotationStatus` returns only `KeyRotationEnabled`.
- SSM parameter labels can be viewed but not attached; SSM descriptions are dropped by Floci when a parameter is
  overwritten without one (AWS keeps them).
- ECR image details were reviewed in code only: Floci 2.3.0 has no `PutImage` (Floci's `main` added it on
  2026-10-20, not yet released), and pushing needs Docker login to the registry container.
- Route 53 health checks are in the inventory but have no page in this console.
- Athena has no delete workgroup action.

## Checked against the Floci source

Each Floci behavior this review relies on, checked in `/Users/chris/floci` (pulled 2026-10-22, commit
`433ab5aef`) at the `2.3.0` tag the emulator runs and at `main`, which is 410 commits ahead. Paths are under
`src/main/java/io/github/hectorvent/floci/services/`.

| Claim | Source | Verdict |
| --- | --- | --- |
| RDS accepts "create final snapshot" on delete but makes no snapshot. | `rds/RdsQueryHandler.handleDeleteDbInstance` / `handleDeleteDbCluster` never read `SkipFinalSnapshot` or `FinalDBSnapshotIdentifier`. Same on `main`. | Confirmed. |
| Neptune never creates snapshots. | `neptune/NeptuneQueryHandler` has no snapshot operations. Same on `main`. | Confirmed. |
| Firehose: a stream with no destination loses its records. | `firehose/FirehoseService.resolveBucket` falls back to `floci-firehose-results`. | Wrong; corrected above. The records go to that bucket after the buffering interval. |
| Firehose flushes after every 5 records. | `application.yml`: `flush-record-count: 0`; flushes follow the buffering hints. | Wrong (stale inventory note); corrected. |
| KMS doesn't report the rotation period. | `kms/KmsJsonHandler.handleEnableKeyRotation` validates and drops the value. | Confirmed. |
| ECR rejects `PutImage`. | Missing in 2.3.0; added on `main` in #5784. | Confirmed for 2.3.0. |
| ACM issues certificates immediately. | `acm/AcmService`: `PENDING_VALIDATION` only when `validation-wait-seconds` > 0 (default 0). | Confirmed, with the setting now noted. |
| SSM drops a description on overwrite. | `ssm/SsmService.storeParameter` sets the new (possibly null) description. | Confirmed. |
| Glue has no crawler or job list operations. | `glue/GlueJsonHandler` handles `GetCrawlers`, `ListCrawlers`, `GetJobs`, `ListJobs`. | Wrong; crawlers and jobs are now listed. |
| Amazon MQ rejects user APIs on RabbitMQ brokers. | `amazonmq/AmazonMqService`, matching AWS. | Confirmed. |
| ElastiCache auth type values. | `elasticache/UserAuthentication` accepts `password`, `no-password-required`, `iam`. | Confirmed; the old `no-password` value was rejected. |
| OpenSearch host ports 9400-9499. | `EmulatorConfig` `proxyBasePort`/`proxyMaxPort` defaults. | Confirmed. |
| Lambda returns no `LogResult` (first review). | `lambda/model/InvokeResult.setLogResult` is never called. | Confirmed. |
| S3 accepts a public policy under Block Public Access (first review). | `s3/S3Service.putBucketPolicy` rejects it; reproduced live. | Wrong; corrected. |

## Verification (second review)

`npx tsc --noEmit`, `npm run build`, `npm run test:e2e` (344 passed, against the disposable emulator) and
`docker compose exec -T dashboard python manage.py test --noinput` run on this branch (1,158 passed) all pass.
The live Route 53 and CloudFront specs (`--project=live`) pass after their label changes. `check_labs` on the
disposable emulator passed 66 of 66 labs after the S3 SigV4 change. The emulator was stopped afterwards and the
containers it started were removed.

# Backend API review

The two console reviews mostly checked what each page shows. Their worst bugs came from the Python layer
underneath, so this pass swept that layer on its own: `dashboard/*_api.py`, `dashboard/*_views.py`, the
inventory loaders in `dashboard/aws/`, `dashboard/actions.py` and the action metadata in `dashboard/services.py`.
Severity uses the same scale as above.

## Summary

- **About 50 inventory lists read as empty when Floci hadn't returned them.** Every optional list in an
  inventory goes through `_safe_value(..., [])`, which turned any failure into an empty list. On an empty
  Floci 2.3.0, 52 list calls across 17 services fail, mostly because Floci doesn't implement the operation
  (ElastiCache snapshots and events, SSM sessions and OpsItems, Transfer workflows, Transcribe medical jobs,
  MemoryDB subnet groups, ...). The console showed each as "(0)". Inventory endpoints now return
  `load_errors`, and every console shows a warning that names the lists Floci didn't return.
- **Invented data.** The EKS kubeconfig was built for clusters that might not exist, with a placeholder CA
  and an invented ARN. The ECR "Inject mock image" action reported a published image with a made-up digest
  whenever `PutImage` failed, which on Floci 2.3.0 is always. ECR push commands named a registry that doesn't
  exist. CodeDeploy "Simulate lifecycle event" called nothing and reported success.
- **False success.** "Empty bucket" always reported 0 objects deleted and ignored per-object failures; it also
  left old versions behind in versioned buckets, so the bucket still couldn't be deleted. Settings reported
  "successfully wiped and reset" when Floci was unreachable.
- **Hidden or wrong defaults.** Auto Scaling scaling policies always sent `Cooldown=60` (AWS uses the group's
  cooldown when it's omitted) and turned an entered 0 into 60. Defaults that match AWS are now visible in the
  generic forms instead of being applied silently.
- **Silent truncation.** Cognito listed at most 60 pools, clients, users and groups. The S3 objects view showed
  1,000 objects with no sign that more existed.

Method: scripted first, then each finding read in context.

- A botocore model check (`ast` over every backend file): each `client.<operation>(Keyword=...)` call, and
  each key assigned into a dict later passed as `**params`, must be an input member of that operation; each
  CamelCase `response.get('Key')` read must exist in some shape of the file's services. It found two real
  bugs (OpenSearch `EngineVersion`, S3 `VersionIdMarker`); the rest were attribute-map keys.
- A field check: for every action in `services.py`, resolve its path with Django's URL resolver and compare
  the action's fields with the `body.get(...)` names its view reads, and the view's defaults with the fields'
  `default` values.
- A load-error probe: run every `*_inventory()` against an empty disposable Floci with the new collector on,
  and list every failed call.
- Greps for `except Exception`, `000000000000`, `us-east-1`, `simulated`, `[:N]` caps and `MaxResults=` without
  paging.

Probes ran against a disposable Floci 2.3.0 on port 14566 only.

## Findings

| Severity | Finding | Status |
| --- | --- | --- |
| High | Inventories turned every failed list call into an empty list (`_safe_value`), so lists Floci doesn't implement, or that failed, read as "(0)". | Fixed: `_safe_value` records failures (except narrow "doesn't exist" codes such as `NoSuch*`, `*NotFound*`) in a per-request collector, also across the loaders' worker threads. Inventory endpoints add `load_errors` (operation, code, message, `unsupported`), and every console shows a "Some lists are incomplete" warning naming them. |
| High | EKS `get_kubeconfig` swallowed every `DescribeCluster` error and returned a kubeconfig with an invented endpoint, a placeholder CA certificate and an `arn:aws:eks:...:000000000000` ARN, even for a missing cluster. The EKS lab's "Generate kubeconfig" step passed on it while the cluster was still `CREATING`. | Fixed: a missing cluster is a 404, a cluster without endpoint and CA is reported with its status, and the lab step waits up to 3 minutes for `ACTIVE` and otherwise asks the learner to run it again. |
| High | ECR "Inject mock image tag" returned a fabricated image (made-up digest and size, `simulated: true`) whenever `PutImage` failed. Floci 2.3.0 has no `PutImage`, so it never worked and always reported success. | Fixed: the error is returned; the action description says 2.3.0 lacks `PutImage`. |
| High | ECR push commands used a made-up `floci:4566/ecr/<repo>` registry. Floci serves `<account>.dkr.ecr.<region>.localhost:<port>/<repo>` and returns the login address as `proxyEndpoint`. | Fixed: built from `repositoryUri` and `GetAuthorizationToken`; a missing repository is a 404. |
| High | CodeDeploy "Simulate lifecycle event" made no API call and returned "simulated as Succeeded". | Removed. `put_lifecycle_event_hook_execution_status` is the real call. |
| High | S3 "Empty bucket" sent `Quiet=True` (S3 then lists only failures), counted `Deleted` (always empty) and ignored `Errors`: it always said 0 objects were deleted and never reported failures. It also deleted only current objects, so a versioned bucket kept its versions and couldn't be deleted. | Fixed: deletes every version and delete marker, as the console's Empty does, counts what S3 reports, and returns 409 with the failures when objects remain. Checked live: a versioned bucket empties and can then be deleted. |
| High | Settings → Reset Floci said "successfully wiped and reset" when Floci was unreachable: the view returned 200 with `ok: false`. | Fixed: 502 with the reason. |
| High | Config's `DescribeRemediationConfigurations` was called without its required `ConfigRuleNames`, and Pricing's `ListPriceLists` without `EffectiveDate` and `CurrencyCode`. botocore rejected both before sending, and the error was swallowed, so the lists were always empty. | Fixed: rule names are passed in chunks of 25; price lists use today's date and USD, which the inventory notes say. |
| Medium | Auto Scaling `put_scaling_policy` always sent `Cooldown=60` and `ScalingAdjustment=1` when left blank, and `or` turned an entered 0 into 60. | Fixed: a blank cooldown is omitted (AWS then uses the group's default cooldown), 0 is kept, and the adjustment is required. |
| Medium | ECR push commands and the EKS kubeconfig used `us-east-1` instead of the configured Region, and gave AWS CLI commands without `--endpoint-url`, which would reach real AWS. | Fixed: configured Region; `--endpoint-url` with the public Floci endpoint. |
| Medium | Defaults applied when a generic-form field is left blank were invisible: ACM expiry days (45), lifecycle hook heartbeat (3600 s), ElastiCache user access string (`on ~* +@all`, full access), Kafka version and broker count, OpenSearch engine, instance type and size, Kinesis shard count, Transcribe language, and others. | Fixed: shown as field defaults. CodePipeline's required transition type is now a field (Inbound/Outbound); Auto Scaling's delete has a visible "Force delete" choice (default on, as the console's Delete). |
| Medium | Cognito listed user pools, app clients, users and groups with one call capped at 60, and built pool ARNs with account `000000000000` (also used for `ListTagsForResource`). | Fixed: every list is paged; the ARN is the one `DescribeUserPool` returns. |
| Medium | IAM's "account root" trust template and assume-role by role name used account `000000000000` instead of the caller's account. | Fixed: account from `GetCallerIdentity`. |
| Medium | IoT `create_topic_rule` added a republish action with an invented role ARN when no actions were given. | Fixed: no actions are added. |
| Medium | RDS's final-snapshot check treated any lookup error as "no snapshot", so the console would say Floci skipped the snapshot when the check itself failed. | Fixed: only `DBSnapshotNotFound`-type codes mean missing; other errors say the snapshot couldn't be checked. |
| Medium | The S3 objects view asked for 1,000 objects and ignored `is_truncated`. | Fixed: the header shows "1,000+" and says more exist. Version listing also keeps `NextVersionIdMarker`. |
| Medium | Inventories that load details only for the first N items (CodePipeline 50 pipelines, SSM 50 documents, S3 Vectors indexes for 50 buckets, Batch 10 queues and 25 jobs per status) and `_paginate`'s 100-page guard cut lists silently. | Fixed: each cut is reported in the same warning. |
| Low | OpenSearch "Update domain config" offered an engine version field; `UpdateDomainConfig` has no such parameter, so botocore rejected the call. | Fixed: field removed; the helper points to Upgrade domain. |
| Low | SQS `start_message_move_task` had a dead fallback that returned an invented `COMPLETED` task. | Removed. |
| Low | Cognito discovery and JWKS links used the internal endpoint (`floci:4566` in Docker). | Fixed: public endpoint. |
| Low | A non-JSON reply to Floci's state reset was replaced with an invented `{"status": "OK"}`. | Fixed: the raw reply is kept. |
| Low | IAM access-key and inline-policy action paths used placeholder names (`{user}`, `{policy}`) that differed from their fields, so a generic form would ask twice. | Fixed. |

## Left as is

| Finding | Why |
| --- | --- |
| The incomplete-list warning names operations, not the inventory keys they fill, so the list that reads "(0)" still shows in the resource-type picker. | Mapping each `_safe_value` call to its key means touching ~470 call sites; the warning already tells the user not to trust those lists. |
| Unimplemented operations are still called on every inventory load. | Calling them is how the dashboard learns that a newer Floci implements them; the cost is small. |
| Lambda and EventBridge test-event templates contain sample ARNs with account `000000000000` and `us-east-1`. | They are templates, like the console's own, and are labeled as samples. |
| `views.api_lab_status` returns 200 with an `error` key when a lab's status can't be read. | The labs page polls it and shows the error; changing the status would make polling noisy. |
| Some backend defaults remain for custom consoles (EC2 volume size, RDS instance class, ECS CPU/memory). | Those consoles send their own form values; the defaults only apply to direct API calls. |
| `identity_views._rotate_user_access_key` treats a failed `ListAccessKeys` as no keys. | `CreateAccessKey` then fails visibly at the two-key limit; low impact. |

## Floci behavior this review relies on

Checked in `/Users/chris/floci` (pulled 2026-10-22, commit `433ab5aef`) at the `2.3.0` tag and at `main`, then
probed on the disposable emulator. Paths are under `src/main/java/io/github/hectorvent/floci/services/`.

| Claim | Source | Verdict |
| --- | --- | --- |
| `ListPriceLists` requires `EffectiveDate` and `CurrencyCode`. | `pricing/PricingService` validates both, at 2.3.0 and `main`. | Confirmed. |
| Config has no `DescribeRemediationConfigurations`, `DescribeDeliveryChannelStatus` or `DescribeConfigurationAggregators`. | Not handled in `configservice/` at 2.3.0 or `main`; live calls return `InvalidAction`. | Confirmed. |
| ECR has no `PutImage` in 2.3.0. | Added to `ecr/EcrJsonHandler` on `main`; live 2.3.0 returns `UnsupportedOperation`. | Confirmed. |
| ECR repository URIs are `<account>.dkr.ecr.<region>.localhost:<port>/<repo>`, and `GetAuthorizationToken` returns the login endpoint. | `ecr/registry/EcrRegistryManager.getRepositoryUri` / `getProxyEndpoint`; live: `000000000000.dkr.ecr.us-east-1.localhost:14566/<repo>`. | Confirmed. |
| An EKS cluster gets its endpoint when its k3s container starts and its CA once k3s answers; until then it is `CREATING`. | `eks/EksClusterManager.applyEndpoints` / `finalizeCluster` at 2.3.0. Live: `ACTIVE` with CA about 8 s after create. | Confirmed. |
| `DescribeUserPool` returns the pool `Arn`. | `cognito/CognitoJsonHandler` writes `Arn` at 2.3.0. | Confirmed. |
| The 52 failing inventory calls are operations Floci 2.3.0 doesn't implement. | Live replies: `UnsupportedOperation`, `UnknownOperationException`, `InvalidAction`, or a 404/405/`AuthorizationHeaderMalformed` from an unrouted path. The first three are flagged "Not implemented by this Floci version"; the rest are listed with their error. | Confirmed on 2.3.0. |

## Verification (backend review)

`ruff check .`, `manage.py check`, `manage.py test --noinput` (1,194 passed, 37 of them new in
`dashboard/tests_backend_review.py`), `npx tsc --noEmit`, `npm run build` and `npm run test:e2e` (346 passed)
pass on the branch and again on the merged `main`. `check_labs` on the disposable emulator passed the IAM (11),
SQS (9), Cognito (2) and EKS (1) labs, whose backend code changed. Kinesis shard count keeps its help text
("Defaults to 1") rather than a form default, because a default would be sent with On-demand streams too.
The emulator was stopped afterwards and the containers it started were removed.
