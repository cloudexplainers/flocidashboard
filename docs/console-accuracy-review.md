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
- Key pairs, volumes, snapshots, network ACLs, launch templates and flow logs have backend routes but no dedicated tables in this console. They are listed in the inventory and the generic workbench. Building them out is a larger redesign. (Elastic IPs and NAT gateways now have tables; see "Elastic IP and NAT gateway tables" below.)
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
| EC2/VPC | Key pairs, volumes, snapshots, Elastic IPs, NAT gateways, network ACLs, launch templates and flow logs have no tables; no status checks. | Elastic IPs and NAT gateways: done (see "Elastic IP and NAT gateway tables"). The rest left: a larger redesign; Floci reports no status checks. |
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

# Floci gap fixes

A read-only audit compared Floci's source (the `2.3.0` tag the dashboard targets, and `main`) with every console
and found where the dashboard implies behavior Floci doesn't have. This pass fixed what it found, after checking
each finding against current `main`: the backend review had already removed the invented ECR image, the
CodeDeploy lifecycle simulation and the placeholder EKS kubeconfig, and made inventories report lists Floci
doesn't return instead of showing "(0)". Those are not repeated here.

The rule this pass applies: where Floci stores a setting but doesn't apply it, the console says so next to the
setting, in a "How Floci emulates this" info note (`frontend/src/components/FlociNote.tsx`, `data-testid="floci-note"`),
an action description in the generic workbench, or an inventory note. Notes describe what Floci does without
version numbers, except where `main` already behaves differently (alarm actions, DynamoDB restore), because
Compose pulls `floci/floci:latest`.

## Labeled

| Console | What it now says | Floci source (2.3.0) |
| --- | --- | --- |
| SQS (queue access policy, Edit queue) | The policy is stored and returned but not evaluated; a Deny blocks nothing, and SNS delivers to the queue regardless. Floci enforces resource policies only for S3, SNS and Lambda. | `ResourcePolicyProvider` is implemented only by `lambda/`, `s3/` and `sns/` (also on `main`). |
| KMS (key policy, grants, rotation) | The key policy is not evaluated; with IAM enforcement on, only identity policies are checked. Grants are stored, not evaluated. Automatic rotation is recorded but never runs and the period is ignored; on-demand rotation does create new key material. | No KMS policy provider; `docs/services/kms.md` "Grant Support Scope"; `KmsService.enableKeyRotation` sets a flag and nothing schedules rotation. |
| ECR (lifecycle policy, repository policy, scan on push) | Lifecycle rules don't expire images; the repository policy is not evaluated; no images are scanned. | `docs/services/ecr.md` "Not Implemented". |
| Secrets Manager | New read-only "Resource permissions" tab shows the secret's resource policy with the same note. | Live: a Deny-all `GetSecretValue` resource policy still returned the value. |
| Backup (workbench) | Recovery points hold no data; plan schedules never run; vault notifications aren't published and vault access policies aren't evaluated. | `BackupService` schedules only job state transitions; `docs/services/backup.md` "Not Yet Supported". |
| CloudWatch (alarm detail) | Floci evaluates the alarm and changes its state, but runs only Application Auto Scaling actions; SNS, EC2 and EC2 Auto Scaling actions don't run in 2.3.0 (`main` adds SNS). | The only `AlarmActionHandler` is `applicationautoscaling/ScalingPolicyAlarmActionHandler`; `AlarmSnsNotifier` exists on `main` only. |
| Auto Scaling (workbench) | Floci scales only through desired capacity. Scaling policies, scheduled actions and lifecycle hooks are stored and never run; the save actions say so in their description and success message. The inventory no longer lists notification configurations and `DescribeTags` as supported. | `AutoScalingService` "accept and remember" comments; `completeLifecycleAction` "Stored-only"; the reconciler never reads hooks; live `UnsupportedOperation` for the notification and tag calls. |
| DynamoDB (Capacity and backups) | The PITR text no longer promises restore to any second: PITR is recorded, but 2.3.0 has no `RestoreTableToPointInTime` or `CreateBackup` (`main` adds both). The inventory notes that replicas are reported `ACTIVE` but nothing is replicated. | Live on 2.3.0 (audit). |
| Route 53 (records) | Routing policies and health checks are stored but not applied: the DNS server answers private zones only and returns every value; `GetHealthCheckStatus` always reports success. | `Route53Controller.getHealthCheckStatus` returns a fixed "Success: HTTP Status Code 200"; `docs/services/route53.md`. |
| CloudTrail | The old note said Floci records no activity and doesn't store event selectors; both were wrong. Now: `LookupEvents` is always empty (no management events), and a logging trail delivers matching S3 data events to its bucket under `AWSLogs/` (about every 60 seconds by default). | `CloudTrailJsonHandler.lookupEvents` returns an empty array; `docs/services/cloudtrail.md`. |
| Config (workbench) | "Start rule evaluation" records the request but evaluates nothing; compliance comes only from `PutEvaluations`. The old note ("always INSUFFICIENT_DATA") was also wrong. | `docs/services/config.md` "Deviations". |
| CloudFront | No caching (cache policies and TTLs change nothing), instant Deployed/Completed, functions stored but never run. Signed URLs are enforced. | `docs/services/cloudfront.md`; functions appear only in XML serialization. |
| RDS (Modify) | Every change applies at once whatever "Apply immediately" says. | `docs/services/rds.md` "Modifying a DB instance". |
| EC2 | Security group rules: not enforced unless `FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED=true` (the old text said never). NAT gateway create: recorded, routes nothing. | `docs/services/ec2.md`. |
| Cloud Map, Cognito identity pools, AppSync, IAM | Every Cloud Map instance stays HEALTHY; identity pools can't vend credentials (`GetId`, `GetCredentialsForIdentity` missing); HTTP data sources don't resolve; access-key usage is never recorded. | `docs/services/cloudmap.md`, `cognitoidentity.md`, `appsync.md`, `iam.md`. |
| Inventory notes (workbench and inventory-only pages) | API Gateway keys and usage plans not enforced; WAF filters no traffic; Cost Explorer costs are synthesized; Pricing is a bundled snapshot; MQ is RabbitMQ only; OpenSearch security settings not enforced; Glue crawler schedules never run; IoT topic authorization not enforced; CodeDeploy `ContinueDeployment` is a no-op; Firehose adds a newline per record; Classic Load Balancers serve no traffic; EMR runs no Hadoop or Spark. | The matching `docs/services/*.md` lines at 2.3.0. |

170 inventory notes of the form "Floci 1.5.x adds ..." were deleted. They were release-changelog lines about older
versions; the workbench already hid them. A unit test now keeps them out.

## Hidden, removed or fixed

| Finding | Change |
| --- | --- |
| Cloud Map "Discover instances" always failed: botocore sent `DiscoverInstances` to `data-localhost:4566`. | `FlociClientFactory` sets `inject_host_prefix=False` for every client. Safe because Floci serves every operation on one endpoint; the only other prefixed operations the botocore models have that matter here (Logs `StartLiveTail`, Step Functions `StartSyncExecution`, S3 `WriteGetObjectResponse`) would hit equally unresolvable hosts. No lab calls any of them. Checked live: discovery works. |
| Cloud Map "Update instance health" called `UpdateInstanceCustomHealthStatus`, which Floci answers with `UnknownOperationException` (also on `main`). | Action, route and helper removed; the inventory lists it under "Not emulated". |
| Settings showed S3, IAM and signature enforcement from the dashboard's own environment. Compose passes those variables only to Floci, so in Docker it showed IAM enforcement as "Permissive" while Floci enforced it. | Unset flags show "Not known (not set for the dashboard)", and the panel says Floci doesn't report its configuration. The top-nav identity panel does the same. |

ECR "Inject mock image tag" stays: since the backend review it reports Floci's `UnsupportedOperation` and its
description says why. Other operations Floci rejects are reached only by inventory loaders, which already report
them in the "Some lists are incomplete" warning.

## S3 anonymous access

`docker-compose.yml` starts Floci with `FLOCI_SERVICES_S3_ENFORCE_AUTH=false`. With that setting Floci allows
every unsigned request: an unsigned `GET` read an object from a bucket with Block Public Access fully on, and an
unsigned `PUT` wrote one (checked on the disposable emulator). Bucket policies and ACLs never apply to anonymous
callers. The console now:

- tests it per bucket: the Permissions tab sends an unsigned `ListObjectsV2` with `MaxKeys=0`
  (`GET /api/s3/buckets/<bucket>/anonymous-access/`) and, when Floci allows it, shows a warning above Block
  Public Access that names the setting. When Floci refuses, it says so in one line.
- says next to the bucket list that the Access column follows Block Public Access and bucket policies as in AWS,
  but Floci applies them to unsigned requests only with enforcement on.

`docker-compose.yml` and the owner's Floci were not changed. To turn enforcement on, set
`FLOCI_SERVICES_S3_ENFORCE_AUTH=true` in `.env` and run `docker compose up -d floci` (add it to the dashboard
service too if Settings should show it). Checked on a separate throwaway Floci with enforcement on:

- Presigned URLs, signed with `test` or IAM keys, still work. The presigned-URL lab passes either way, but today it
  would also pass if signing were broken, because unsigned reads succeed.
- Unsigned reads of a private object get `403 AccessDenied`; with a public-read bucket policy (and Block Public
  Access off) they succeed, as in AWS, and a website endpoint served the bucket. Plain object links that aren't
  presigned therefore need such a policy; website endpoints without one were not checked.
- The dashboard's own calls are signed, and no lab makes unsigned S3 requests, so labs are unaffected. The
  Block Public Access lab could then add a step proving that an anonymous request is refused.

## Added

RDS manual snapshots: a Snapshots tab (also in the side navigation) lists DB snapshots and offers Restore
snapshot (to a new DB instance, optional class) and Delete snapshot (typed confirmation); a database's header
has Take snapshot. Backed by `POST /api/rds/snapshots/`, `DELETE /api/rds/snapshots/<id>/` and
`POST /api/rds/snapshots/<id>/restore/`, and by `DescribeDBSnapshots` in the inventory. A Floci note says what a
snapshot is there: a `pg_dumpall` of the database, PostgreSQL only.

Checked in source (`RdsService.createDbSnapshot` / `restoreDbInstanceFromDbSnapshot`) and live on 2.3.0: a row
written to a PostgreSQL instance was present in the instance restored from its snapshot; a MySQL snapshot
returns `InvalidDBInstanceState` ("not supported for engine MYSQL"), which the console shows. Snapshots are
`available` at once and only `manual` snapshots exist.

## RDS and Aurora resilience (2026-10-23)

The top item of the recommended next work (below) is done. The RDS console now offers Restore to point in time, Create read replica
and Promote on a database, Add reader, Failover and Take snapshot on a cluster, and a Cluster snapshots table
(restore to a new cluster, delete) on the Snapshots tab. Backed by `POST /api/rds/instances/<id>/restore-point-in-time/`,
`.../read-replicas/`, `.../promote/`, `POST /api/rds/clusters/<id>/instances/`, `.../failover/`, and
`/api/rds/cluster-snapshots/` (create, delete, `restore/`). The inventory adds `DescribeDBClusterSnapshots`, the
replica links, `StatusInfos` and `LatestRestorableTime`. The lab `rds/aurora-resilience` (SAA 2.2, 3.3) uses all of it.

Checked in `RdsService.java` and `docs/services/rds.md` at `2.3.0`, then live on the disposable emulator:

| Behavior | Floci 2.3.0 | Console says |
| --- | --- | --- |
| Point-in-time restore | Checks the window (`InvalidRestoreFault` outside it, `PointInTimeRestoreNotEnabled` with retention 0), then copies the source with `pg_dumpall`: a restore to an earlier time held a row written later. PostgreSQL only. | Note in the restore dialog |
| Read replica | One `pg_dumpall` copy at creation; a later write to the source never reached the replica, and the replica accepted a write. Links (`ReadReplicaSourceDBInstanceIdentifier`, `StatusInfos`, the source's list) are reported. MySQL/MariaDB refused; a source needs backups. | Notes in the dialog and the Configuration tab |
| Promote | Clears both links, sets retention (1 day by default), reboots. | Nothing to label |
| Multi-AZ | Stored and reported; no standby, no `SecondaryAvailabilityZone`. | Note in the Configuration tab |
| Aurora failover | Only `IsClusterWriter` moves; the instances share one database, so data and connections stay. No reader: `InvalidDBClusterStateFault`. The `AvailabilityZone` a cluster instance asks for is ignored. | Notes in the Failover and Add reader dialogs |
| Cluster snapshots | Available at once; restore creates a cluster without instances that holds the data. Aurora MySQL refused. | Note on the Snapshots tab and in the dialog |

The lab writes its order row with the RDS Data API through the cluster (as AWS allows for Aurora), using the
secret `--manage-master-user-password` creates; Floci doesn't report `HttpEndpointEnabled`, so the lab passes
`--enable-http-endpoint` for fidelity only. `check_labs --service rds` passed on 2.3.0.

## HA web tier lab (2026-10-23)

The top item of the recommended next work (below) is done: `elasticloadbalancing/ha-web-tier` (SAA 2.2, 2.1, 3.2,
3.4), 12 steps, in `dashboard/labs/elasticloadbalancing_labs.py`. It is the first lab for the Elastic Load Balancing
service and sits in Level 4 after the RDS resilience lab. It uses a launch template rather than a launch configuration,
because AWS recommends templates and the instance refresh needs a new version.

Checked live on a throwaway Floci 2.3.0 (two full runs over leftovers, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| ALB data plane | The listener runs on that port inside the Floci container (`ElbV2DataPlane`), and the load balancer DNS name (`<name>-<id>.elb.floci`) resolves to Floci inside instances. Eight requests from an instance alternated between the targets (round robin); with three targets, all three answered. | Load balancer and traffic steps |
| Target health | New targets go `initial`, then `unhealthy` (`Target.FailedHealthChecks`) until user data starts the server, then `healthy` after the threshold: about a minute after launch. | The wait steps allow 180 s |
| ASG with a target group | Instances are spread over the two subnets' zones and registered with the target group; set-desired-capacity 3 added a third healthy target. | Nothing to label |
| Self-healing | A terminated instance was dropped from the group and the target group, and a replacement in the same zone was healthy within about 50 s. | Note: an instance set Unhealthy is not replaced (see "HA web tier controls") |
| Target tracking policy | `PutScalingPolicy` stores and returns it; nothing evaluates it. | Note; the lab scales out by hand |
| Instance refresh | Replaced all three instances at once (outage of about a minute) from `$Latest`, `Successful` in about 20 s, `MinHealthyPercentage` echoed only. | Note in the step |
| Launch template versions | `CreateLaunchTemplateVersion` raises `LatestVersionNumber`, but `DescribeLaunchTemplateVersions` returns only the latest version, even when asked for version 1. | Note in the step; the template check accepts either release |
| Ubuntu guest | `ami-ubuntu2204` has Perl but not curl in this environment, so the user data server and the request client are Perl one-liners. The lab shows the curl command a learner would run. | Explanation of the traffic step |

The instance security group allows port 8080 only from the load balancer security group. Floci doesn't enforce
security groups by default; with `FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED=true` the health checks come from the
Floci container, not from a member of that group, which was not tested. `check_labs --service elasticloadbalancing`
passed on 2.3.0 (a run takes about four minutes, most of it waiting for health checks).

## HA web tier controls (2026-10-23)

The ELBv2 and Auto Scaling item of the recommended next work (below) is done. The Elastic Load Balancing console adds Health checks
and Attributes tabs for a target group, Edit on each listener (port and default action), and a rules table for the
selected listener with Add rule, Edit (path, forward target group, priority) and Delete. Backed by
`GET`/`PUT /api/elasticloadbalancing/target-groups/<arn>/attributes/`, `PUT .../health-check/`, and `PUT` on
`/api/elasticloadbalancing/listeners/<arn>/` and `/rules/<arn>/` (`ModifyRule`, then `SetRulePriorities` for a new
priority). The inventory now loads each target group's attributes. The Auto Scaling workbench adds Start instance refresh,
Describe instance refreshes and Set instance health (`/api/autoscaling/groups/<name>/instance-refreshes/`,
`/api/autoscaling/instances/<id>/health/`).

Checked in `services/elbv2/` and `services/autoscaling/` at `2.3.0`, then live on a throwaway Floci 2.3.0 (two nginx
targets behind an ALB, and an ASG of two instances):

| Behavior | Floci 2.3.0 | Console says |
| --- | --- | --- |
| Stickiness (`lb_cookie`, `app_cookie`) | Stored and reported; four requests alternated between the targets with no `Set-Cookie`. `main` applies `lb_cookie` duration stickiness. | Note on the Attributes tab and in the dialog |
| Deregistration delay | Stored; a deregistered target vanished from `DescribeTargetHealth` at once (no `draining`) and got no more requests. | Same note |
| Attribute values | Any key or value is stored (`bogus.key`, duration `0`). | The dashboard checks AWS limits itself (delay 0–3600, duration 1–604800, reserved `AWSALB*` cookie names) |
| Health check edits | Path, timeout, thresholds and success codes apply at once (path `/missing` made both targets `unhealthy`, `Target.ResponseCodeMismatch`). A new interval is reported but the probe timer keeps the old one (`ElbV2HealthChecker.startMonitoring`). With every target unhealthy, traffic still reached both (fail open, as in AWS). | Note on the Health checks tab |
| `ModifyListener` | A new port moved the listener at once (old port refused connections); a fixed-response default action applied. Floci also accepted a change to HTTPS with no certificate, so the edit dialog keeps the protocol. | Protocol shown read-only |
| `ModifyRule`, `SetRulePriorities` | Action and path changes re-route at once (checked through the console: `/api/` moved to the other target group). | Nothing to label |
| Instance refresh | Real: both instances were marked `Terminating` together and replaced from the new launch configuration within 20 s, `Successful`, 100 %. `MinHealthyPercentage` and `InstanceWarmup` are echoed in `Preferences`, not applied. A second start returns `InstanceRefreshInProgress`. | Action description |
| Cancel and rollback | `CancelInstanceRefresh` and `RollbackInstanceRefresh` return `UnsupportedOperation` (also on `main`). Suspending the `InstanceRefresh` process pauses a refresh (`AutoScalingReconciler.refreshPaused`). | Not offered; listed under "Not emulated"; inventory note |
| Set instance health | `Unhealthy` is stored and reported; the reconciler never replaces the instance (watched 40 s) and the grace period is ignored. A stopped or terminated instance is replaced to keep the desired capacity (both checked). | Action description and success message; inventory note |

The Elastic Load Balancing inventory notes no longer repeat what these tabs say inline, and two notes about how the
dashboard loads the lists were dropped (see "Inventory notes on every console" below). No lab in this change; the HA
web tier lab (W1-1) followed, see "HA web tier lab" below.

## Inventory notes on every console (2026-10-23)

Every inventory (`GET /api/<service>/`) can return `notes`, `not_supported` and `out_of_scope`, but only the
generic workbench showed them, so notes on custom consoles (DynamoDB replicas, Secrets Manager rotation) were
visible only through the API. The fetch hook in `frontend/src/api/loadErrors.ts`, which already raises the
"Some lists are incomplete" warning, now also keeps each service's notes, and `ConsoleLayout` shows them once,
above the console, in a collapsed "How Floci emulates this service" section (`components/InventoryNotes.tsx`).
The workbench no longer renders its own copy. If a console's inventory request failed, the section requests
the inventory once itself; that request doesn't raise the incomplete-lists warning.

Every service's notes were then reviewed against Floci's source at `2.3.0`:

| Change | Services |
| --- | --- |
| Wrong, corrected | EC2 (`CreateKeyPair` returns a real key and the public key is copied into the instance; security groups are enforced only with `FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED=true`), Pipes (targets are Lambda, SQS, SNS, EventBridge and Step Functions; no Kinesis), EKS ("Phase 1" was out of date: node groups and Fargate profiles exist), Athena (DuckDB, mock mode off by default), ACM (export also allowed for certificates requested with export enabled), Secrets Manager (also rotates on schedule) |
| Repeated a console's inline note, removed | DynamoDB PITR, CloudFront caching, CloudTrail events, Secrets Manager resource policies, RDS snapshots, `ApplyImmediately`, point-in-time restore, replicas, Aurora failover and cluster snapshots, Route 53 |
| Implementation chatter, removed or reworded | Protocol lines (KMS, CloudFormation, ElastiCache, ECR, Step Functions, RDS, Pipes), "This page focuses…"/"This read-only inspector…", "round out"/"complements", AppSync's "should now work", DynamoDB's `Expected` changelog line. Notes that say the page shows only part of a list are kept, reworded as "This page…". |

`dashboard/tests_inventory_notes.py` checks the notes statically (no chatter, no inline duplicates, the
corrected claims). CloudTrail keeps only its event-selector note; the console says the rest inline. Elastic Load
Balancing and Auto Scaling notes were left as they are, since the HA web tier work was changing those consoles.

## CloudTrail log-file viewer (2026-10-23)

Done from the recommended next work. Floci records no management events, so `LookupEvents` is always empty, but
a logging trail delivers matching S3 data events to its bucket. A trail's **Log files** tab now reads them:

- `GET /api/cloudtrail/trails/<trail>/log-files/` finds the trail's bucket and `S3KeyPrefix` with `DescribeTrails`,
  walks the `AWSLogs/<account>/CloudTrail/<region>/yyyy/MM/dd/` folders (`ListObjectsV2` with a delimiter), and
  returns whole days of `.json.gz` files newest first, about 100 files per request; `?before=yyyy/MM/dd` continues.
  Days with more than 2,000 files per account and Region are cut off and reported through `record_truncation`.
- `GET .../log-files/read/?key=` reads one file under that prefix only, up to 5 MB compressed and 25 MB expanded,
  gunzips it and returns `Records`. A missing trail or bucket, AccessDenied, a file that isn't gzip or JSON, or
  one without `Records` is an error, never an empty list.
- A Floci note on the tab, and a link from the Event history note.

Checked in `CloudTrailLogWriter.java` (key layout; a prefix gets a `/` appended, as in AWS; files go under the
event's Region, so a multi-Region trail can have several Region folders) and live on 2.3.0: a trail with prefix
`audit` and an `arn:aws:s3:::bucket/` selector delivered one file after about 60 seconds holding PutObject,
GetObject, a GetObject that failed with `NoSuchKey`, and DeleteObject; the tab listed and opened it. A trail
whose bucket was deleted shows `NoSuchBucket`. CloudTrail digest files and other objects in the bucket are not
listed.

## S3 data protection lab (2026-10-23)

`s3/data-protection-tiers` (SAA 1.3, 4.1, 2.2), 13 steps, in `dashboard/labs/s3_data_protection_labs.py` (dispatched from
`s3_labs.py`). It sits in Level 4 after the HA web tier lab. It covers Wave 1 item W1-3 with three buckets:
`lab-records-vault` (Object Lock), `lab-records-archive` (versioned; tiers, lifecycle, replication source, Requester
Pays) and `lab-records-dr` (versioned, us-west-2). Lifecycle transitions of current objects are already in
`s3/lifecycle-retention`, so this lab's rule works on noncurrent versions instead.

Checked live on a throwaway Floci 2.3.0 (a full run, a second run over the leftovers, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Object Lock at bucket creation | Versioning turns on with it; the default retention is applied to new versions and reported by `HeadObject`. | Nothing to label |
| GOVERNANCE retention | A version delete without the bypass is `AccessDenied`; with `--bypass-governance-retention` it succeeds (also through `DeleteObjects`). | Nothing to label |
| Legal hold | Blocks a delete even with the bypass; turning it off allows it. | Nothing to label |
| COMPLIANCE retention | Refuses a delete with the bypass, shortening the date, and switching to GOVERNANCE; once the date passes, the delete works. | The lab keeps it for 60 s so Reset can clean up; Reset waits for it |
| Storage classes | Every class (STANDARD_IA, ONEZONE_IA, INTELLIGENT_TIERING, GLACIER, GLACIER_IR, DEEP_ARCHIVE) is stored and reported, and `CopyObject` can change it. A GLACIER or DEEP_ARCHIVE object reads without a restore; `RestoreObject` is accepted but sets no `Restore` header. | Note in the step and its result |
| Intelligent-Tiering configuration, lifecycle rules | Stored and returned as sent; no object changes tier or expires. | Notes in the steps |
| Cross-Region Replication | A bucket created with `LocationConstraint=us-west-2` reports that Region. The rule is stored (Floci accepted it even before versioning was on, which AWS refuses), but no object reached the destination, and the role is not checked. | Note in the step; the result counts the destination's objects |
| Requester Pays | Stored; GETs need no `x-amz-request-payer` and return no `x-amz-request-charged`. | Note in the step |

The console had no forms for Object Lock retention, legal holds, replication or Intelligent-Tiering; they were added
afterwards (see "S3 management and data protection forms").
`check_labs --service s3 --lab data-protection-tiers` passed on 2.3.0; it takes about a minute because Reset waits out
the COMPLIANCE retention.

## Three-tier VPC with NAT lab (2026-10-23)

`ec2/nat-three-tier` (SAA 1.2, 4.4, 3.4, 2.2), 10 steps, in `dashboard/labs/ec2_nat_labs.py` (dispatched from
`ec2_labs.py`). It opens Level 5 and covers Wave 1 item W1-4 in its own VPC (`lab-three-tier-vpc`, 10.64.0.0/16), so
`ec2/vpc-public-private` stays a short introduction.

Checked live on a throwaway Floci 2.3.0 (a full run, a second run over the leftovers, deliberately broken setups, then
reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| NAT gateway create | `available` at once, with a private IP from the subnet and the Elastic IP's public IP. | Note in the step |
| NAT gateway rules | Accepts an Elastic IP another NAT gateway already uses, and a public NAT gateway with no Elastic IP (AWS refuses both). Also lets you delete the subnet holding a NAT gateway, detach the internet gateway, and release an Elastic IP in use. | The step checks the first two rules itself; Reset deletes in AWS order anyway |
| NAT gateway delete | Gone at once (`NatGatewayNotFound`); AWS shows `deleted` for about an hour. A route to it stays `active`; AWS marks it `blackhole`. | Note in the routing step |
| Traffic | Not routed. An instance launched in a subnet with no default route reached the internet: each instance container is also on Docker's bridge network, whose default route wins. | Note in the routing steps and the trace |
| Elastic IPs | Allocated from a made-up pool; tags work; `DescribeAddresses` shows no association for an address a NAT gateway uses. | Note in the step |
| DB subnet group | Accepts subnets in one zone (AWS requires two). | The step checks the zones itself |
| S3 gateway endpoint | Stored with its route tables, but no prefix-list route appears in them. | Note in the step and its result |
| Egress-only internet gateway, Reachability Analyzer | `UnsupportedOperation`. | Mentioned in the explanations only |

The console still has no Elastic IP or NAT gateway tables (see "EC2 and VPC" above); they show in the inventory and
the generic workbench. `check_labs --service ec2 --lab nat-three-tier` passed on 2.3.0.

## KMS key management lab (2026-10-23)

`kms/key-management` (SAA 1.3), 8 steps, in `dashboard/labs/kms_key_management_labs.py` (dispatched from
`kms_labs.py`). It closes Level 4 and covers Wave 1 item W1-5 with its own key (`alias/lab-orders-cmk`), roles
`lab-kms-key-admin` and `lab-kms-orders-app`, and bucket `lab-kms-orders-records`, so `kms/key-alias-encrypt-decrypt`
stays the short introduction.

Checked live on a throwaway Floci 2.3.0 with IAM enforcement on (a full run, a second run over the leftovers,
deliberately broken setups, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Key policy | Stored and returned as sent. Not evaluated: a Deny for the caller was ignored, and a policy without the root statement didn't stop an IAM-allowed user. Accepts malformed JSON, a policy that locks everyone out, and principals that don't exist (AWS refuses all three). | The step checks the root statement and the admin/user split itself |
| IAM for KMS | Enforced: a user with no IAM policy got `AccessDeniedException` on Encrypt and GenerateDataKey. | Mentioned in the key policy step |
| Grants | Stored and listed with constraints and the retiring principal; revoke and retire work. A user whose only permission was a grant (with or without the grant token) was still refused. | Note in the step and its result |
| Rotation | `EnableKeyRotation` accepts 90-2560 days and drops the period; `GetKeyRotationStatus` returns only the flag and nothing rotates on a schedule. `RotateKeyOnDemand` adds new key material, and ciphertext from before it still decrypts. `ListKeyRotations` is `UnsupportedOperation`. | Note in the step and its result |
| Data keys and context | `GenerateDataKey` returns a real 32-byte key whose `CiphertextBlob` decrypts back to it. A wrong or missing encryption context is `InvalidCiphertextException`. | The decrypt step proves the refusal |
| Key states | Encrypt is `DisabledException` while disabled and `KMSInvalidStateException` while pending deletion. `CancelKeyDeletion` returns the key to Enabled; AWS leaves it Disabled. | The step enables the key either way and names the difference |
| SSE-KMS on S3 | `PutBucketEncryption` with a KMS key and Bucket Key is stored. An upload without headers isn't reported as encrypted; with explicit headers it is. S3 never calls KMS: an unknown key ID is accepted, an object stays readable after its key is disabled, and a user without `kms:Decrypt` can download it. | Note in the step and its result |

Python's standard library has no AES, so the envelope steps encrypt the record with an HMAC-SHA256 keystream and tag
as a stand-in for AES-256-GCM, and the explanation says so. The KMS console already shows the key policy, rotation and
grants the lab creates. `check_labs --service kms` passed on 2.3.0.

## Route 53 routing and failover lab (2026-10-23)

`route53/routing-failover` (SAA 2.2, 3.4), 8 steps, in `dashboard/labs/route53_labs.py`, the first Route 53 lab and
its own batch after load balancing. It closes Level 4 and covers Wave 1 item W1-6: a public hosted zone
`lab-shop.example.com`, HTTP health checks for a us-east-1 primary (192.0.2.10) and a us-west-2 standby
(198.51.100.20), failover records for `www`, an apex alias to `www` with EvaluateTargetHealth, a 90/10 weighted pair
for `api`, latency records for `app`, a failover drill, and a split-horizon private zone of the same name on VPC
`lab-route53-vpc` (10.53.0.0/16). Wave 1 is now complete.

Checked live on a throwaway Floci 2.3.0 (a full run, a second run over the leftovers, deliberately broken setups,
steps out of order, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Hosted zones | Created with four placeholder name servers and NS and SOA records. Duplicate caller references and deleting a zone with records are refused, as on AWS. A private zone is accepted for a VPC that doesn't exist or has DNS hostnames off. | The private zone step checks the VPC attributes itself |
| Health checks | Stored, but never probe: `GetHealthCheckStatus` reports "Success: HTTP Status Code 200" from checker 1.2.3.4 for any address. Accepts a 20-second interval and a threshold of 20. Drops Regions, Inverted, EnableSNI, MeasureLatency, calculated children and CloudWatch alarm settings. `UpdateHealthCheck` replaces the whole config with only the fields sent. Deleting a check a record uses is allowed. `GetHealthCheckCount`, `GetHealthCheckLastFailureReason` and `GetCheckerIpRanges` fail. | The step validates interval, threshold and target, and says Floci reports success without probing |
| Records | Failover, weighted, latency, set identifiers, health check IDs, TTLs and alias targets are kept. GeoLocation and MultiValueAnswer are dropped. CREATE of an existing record, DELETE of a missing record and DELETE with values that differ are refused. Not validated: two PRIMARY records, weights above 255, a weighted record without a set identifier, mixed policies on one name, a CNAME at the apex, an alias to a missing name, a health check ID that doesn't exist. | Each record step checks those rules and repairs strays on a rerun |
| DNS answers | Floci's DNS server (port 53 on its Docker network) answers A records in private zones to any client, with every value; the private zone's CNAME got an empty answer. Public zone names get NXDOMAIN. `TestDNSAnswer` and `ListQueryLoggingConfigs` fail. | The drill plays the resolver over the stored records: primary, then secondary with the primary failing, then primary with both failing (AWS fails open) |

The Route 53 console already labels routing policies and shows private zone VPCs; its Floci note no longer says geolocation is stored.
`check_labs --service route53` passed on 2.3.0.

## Elastic IP and NAT gateway tables (2026-10-23)

The EC2 console's network views (`/ec2/elastic_ips`, `/ec2/nat_gateways`, also in the side navigation) replace the
inventory-only listing. Both are in `frontend/src/pages/EC2AddressingPanels.tsx`.

- **Elastic IPs**: the console's columns (Name, allocated address, type, allocation ID, associated instance, associated
  resource, private IP, association ID, network border group), a filter, a Summary and Tags detail, and Allocate (with a
  Name tag), Associate (running or stopped instances, "Allow reassociation"), Disassociate and Release, each confirmed.
- **NAT gateways**: Name, ID, connectivity type, state, state message, primary public and private IPv4, primary network
  interface, VPC, subnet and creation time; Details, Route tables (every route that targets the gateway) and Tags; Create
  (Name tag, subnet, Public or Private, and for Public an unused Elastic IP, with "Allocate Elastic IP" in the form)
  and Delete (typed "delete", listing the routes that point at it).
- Backend: `AllocateAddress` and `CreateNatGateway` take a Name tag (`TagSpecifications`), `AssociateAddress` takes
  `AllowReassociation`, and `CreateNatGateway` no longer requires an Elastic IP for a private gateway. The generic
  workbench forms match.

Checked live on a throwaway Floci 2.3.0:

| Behavior | Floci 2.3.0 | Console |
| --- | --- | --- |
| NAT data plane | Created gateways are `available` at once and route tables can target them, but no traffic passes through them; instances reach the network through Docker networking. | Warning on the NAT gateways view |
| Public NAT gateway without an Elastic IP | Accepted (AWS rejects it). A private gateway with one is refused, as on AWS. | The backend and form require an Elastic IP for Public and refuse one for Private |
| One Elastic IP on several NAT gateways | Accepted (AWS rejects it). | The form lists only addresses that aren't associated with an instance or a gateway |
| NAT association in `DescribeAddresses` | Missing: the address shows no association while a gateway uses it. | "Associated with" and the association ID come from `DescribeNatGateways` |
| Deleting a NAT gateway | It disappears at once (AWS lists it as Deleted for about an hour); routes to it stay `active` (AWS: `blackhole`); its Elastic IP stays allocated, as on AWS. | Noted in the warning and the delete dialog |
| Releasing an associated Elastic IP | Allowed, whether the address is on an instance or a NAT gateway (AWS refuses). | Release is disabled until the address is disassociated; a NAT gateway's address can't be disassociated, as in AWS |
| Re-association | `AssociateAddress` moves an associated address without `AllowReassociation`, and an instance keeps its earlier Elastic IP when it gets a second one (AWS replaces it). Instance and network interface IDs aren't validated. | The Associate dialog requires "Allow reassociation" for an associated address and offers only listed instances |
| Public IP while associated | A running instance reports its container IP as its public IP, and an associated Elastic IP reports that address too; the allocated address comes back after disassociation. | Info note on the Elastic IPs view |
| Name tags | `TagSpecifications` on `AllocateAddress` and `CreateNatGateway` are stored and returned. | Name columns |

Tests: `dashboard/tests_ec2_addressing.py`, two mocked browser tests in `frontend/e2e/dashboards.spec.ts`, and a live
one in `frontend/e2e/dashboards.live.spec.ts` (allocate, create a NAT gateway on it, see the association, delete,
release), which passed against 2.3.0.

## S3 management and data protection forms (2026-10-23)

The S3 item of the recommended next work (below) is done. A bucket now has a **Management** tab with Lifecycle rules
(create, edit, delete) and Replication rules (create, delete), and its Properties tab adds Intelligent-Tiering Archive
configurations (create, edit, delete) and an editable Object Lock panel (turn it on, default retention mode and period).
An object's details dialog shows Object Lock retention and legal hold for the version shown, with Edit retention and
Turn on/off legal hold, when the bucket has Object Lock. Backed by `/api/s3/buckets/<bucket>/object-lock/`,
`.../objects/retention/`, `.../objects/legal-hold/`, `.../replication/` and `.../intelligent-tiering/`; `HeadObject`
now returns the lock fields, and the existing lifecycle route checks rules before sending them.

Checked in `S3Service.java` and `docs/services/s3.md` at `2.3.0`, then live on the disposable emulator:

| Behavior | Floci 2.3.0 | Console |
| --- | --- | --- |
| Object Lock retention and legal hold | Enforced: a version under governance retention can be deleted only with the bypass, compliance retention can't be shortened or changed, and a legal hold blocks a delete even with the bypass. Removing governance retention needs the bypass. | Nothing to label beyond the note; the retention form asks for the bypass and refuses changes compliance forbids |
| Turning Object Lock on | Accepted on a bucket without versioning (AWS: `InvalidBucketState`), and versioning can then be suspended on a locked bucket (AWS refuses). | The dashboard refuses both: Object Lock needs versioning first, and Suspend is disabled on a locked bucket |
| Retention or legal hold without Object Lock | Accepted on any bucket (AWS: `InvalidRequest`, "Bucket is missing Object Lock Configuration"). | The dashboard refuses it; the form is shown only on locked buckets |
| Lifecycle rules | Stored and returned as sent, never run (no transition, expiration or multipart abort). No validation: a rule without actions, a Standard-IA transition before 30 days and an expiration before the last transition are all accepted. | Note on the tab; the form and the backend check the AWS rules (actions, 30-day IA minimum, expiration after transitions, unique storage classes and IDs, no delete-marker cleanup with expiration days, no multipart abort with tag filters). Rules with dates or size filters are listed but edited only with the CLI |
| Replication | Stored, nothing replicated, role not checked. Accepted with versioning off on either bucket and with a destination that doesn't exist. | Note on the tab; the dashboard requires versioning on the source and on a destination in this account, unique priorities and a role ARN |
| Intelligent-Tiering archive configurations | Stored; day limits checked (90-730 Archive, 180-730 Deep Archive); objects never change tier. | Note in the form; the form checks the same limits |

The S3 inventory listed replication and Intelligent-Tiering as not implemented and Request payment as supported; it now
lists the first two as supported (configuration only) and Request payment under not implemented, as Floci's own docs do.

Verification: `manage.py test` (1,365 passed), `npx tsc --noEmit`, `npm run build`, the mocked browser suite with six
new S3 tests, and a new live test (`dashboards.live.spec.ts`) that turns on Object Lock, sets a legal hold that Floci
then enforces against a delete, and creates a lifecycle rule, a replication rule and a tiering configuration through
the console, checking each against Floci 2.3.0 with the SDK.

## Floci features the console didn't show (2026-10-23)

The audit listed twelve features Floci 2.3.0 implements that the console didn't expose; all twelve are now in the
console. Each was checked live on a throwaway Floci 2.3.0 before it got a console form, and the live checks are kept as `ConsoleFeatureLiveTests` in
`dashboard/integration_tests.py`.

| Feature | Where | Floci 2.3.0 | Console says |
| --- | --- | --- | --- |
| EventBridge archives and replays | EventBridge, Archives and replays tab | Archives record matching events (`EventCount` goes up) and a replay sends them back to the source bus, where rules deliver them to targets again; a rule filter on the replay is honored. Replayed events lack the `replay-name` field, `DescribeReplay` drops `FilterArns`, and a replay is COMPLETED as soon as it starts, so `CancelReplay` always finds nothing to cancel. Replay names can't be reused. | Floci note on the tab |
| CloudWatch dashboards | CloudWatch, Dashboards tab | `PutDashboard`, `ListDashboards`, `GetDashboard` and `DeleteDashboards` work; a body that isn't JSON is refused. Floci draws nothing. | The console draws text and metric widgets (metric rows, including `.` and `...` shorthand, chart through the existing metric statistics endpoint); other widget types show their JSON |
| Logs subscription filters | CloudWatch, Log groups tab, under the selected group | Filters are stored and matching events are delivered, gzip-compressed, to Kinesis streams and Lambda functions as they arrive. Floci accepts more than the two filters AWS allows per log group. | The console stops at two filters and says why |
| IAM password policy | IAM, Account settings | `UpdateAccountPasswordPolicy` and `GetAccountPasswordPolicy` work, and Floci enforces length, character classes and reuse on `CreateLoginProfile`/`UpdateLoginProfile` (PasswordPolicyViolation). With no policy set, it accepts any password; AWS applies a default. Floci itself checks length 6 to 128. | Shows "IAM default" when there is no policy; the form checks the AWS limits (age 1 to 1095 days, reuse 1 to 24) |
| IAM credential report | IAM, Credential report | `GenerateCredentialReport` and `GetCredentialReport` return the AWS CSV columns. A report is reused for 4 hours, as on AWS, so new users may be missing. | The table and download show the report's generation time |
| Secrets Manager replication | Secrets Manager, secret details, Replication tab | Matches AWS: replicas sync on every new version, are read-only, can be promoted to standalone secrets, and are deleted when removed. A replica name that already exists in the target Region gives a Failed status. | The Region list leaves out the secret's own Region; promote and remove ask for confirmation |
| EC2 key pairs | EC2, Key Pairs | Create, import, describe and delete work, and instances launched with a key get its public key. ED25519 requests return an RSA 2048 key, and `KeyType` isn't reported. | Floci note, and the private key dialog says when Floci returned RSA instead of ED25519 |
| Lambda async destinations | Lambda, function, Asynchronous invocation tab | Retries happen as configured (about a minute, then two minutes later), and on-failure and on-success records are delivered to SQS. Floci doesn't check the AWS limits (retries 0 to 2, age 60 to 21600 seconds) or that a destination exists. | The form checks the limits and the destination ARN's service |
| Step Functions versions and aliases | Step Functions, state machine, Versions and aliases tab | Weighted alias routing is honored, executions record the version and alias, and a version an alias still routes to can't be deleted. | The alias form allows one version or two with weights adding up to 100 |
| DynamoDB export to S3 | DynamoDB, table, Exports to S3 tab | Full exports in DynamoDB JSON with the AWS manifest files, done in seconds. No point-in-time recovery needed (AWS requires it); ION and incremental exports are unsupported; a missing bucket gives FAILED with S3NoSuchBucket. | The console requires PITR, as AWS does, and offers only full DynamoDB JSON exports |
| Kinesis resharding | Kinesis workbench: Update shard count, Split shard, Merge shards | `UpdateShardCount` (with the 2x limit and the on-demand refusal), `SplitShard` and `MergeShards` work. A split hash key equal to the shard's starting key is accepted (AWS refuses it). | Split defaults to the middle of the range and checks the key; merge checks the shards are adjacent |
| Route 53 private-zone VPC association | Route 53, Create hosted zone (Private) and the zone's VPCs tab | `AssociateVPCWithHostedZone` and `DisassociateVPCFromHostedZone` work. Floci doesn't check that the VPC exists, lets the last VPC be removed (AWS refuses), and refuses VPCs on public zones as AWS does. Its DNS server answers a private zone's A records to any client, associated or not. | The console checks the VPC exists, keeps at least one VPC, and the Floci note says resolution isn't limited to the VPCs |
| CloudFront origin access control | CloudFront, Origin access tab, and Create distribution (S3 origins) | Create, list, get and delete work, distributions keep `OriginAccessControlId`, and deleting a control in use is refused. `UpdateOriginAccessControl` returns 405. The bucket policy isn't checked against the distribution. | No edit (create a new control instead); the tab shows which distributions use each control and the bucket policy statement to add |

## Smaller partial behaviors (2026-10-24)

The last open item from the gap fixes: smaller features Floci 2.3.0 only partly supports, now labeled where the
console offers them, checked against Floci's source at the `2.3.0` tag. Unit tests in `PartialBehaviorLabelTests`
(`dashboard/tests_floci_gap_fixes.py`) keep the labels in place.

| Feature | Floci 2.3.0 | Console says |
| --- | --- | --- |
| ECS placement strategies and constraints | Stored and returned, never used: every task runs as a container on the Floci host. Capacity provider strategies are applied. | Inventory note |
| EKS add-ons | Recorded metadata only, `ACTIVE` at once, nothing installed in the cluster; `eks-pod-identity-agent` turns on Pod Identity injection. | Inventory note |
| OpenSearch `UpgradeDomain` | Checks the upgrade path, then only changes the stored version; `GetUpgradeHistory` stays empty and a running container keeps its image. | Inventory note and the Upgrade domain action description |
| Logs Insights | `fields`, `filter` (`=`, `!=`, `==`), `sort`, `dedup`, `limit`. Other commands fail the query; an `and`/`or` filter compares only the first condition, without an error. | Inventory note and the Start Logs Insights query action description |
| CloudFormation | `ValidateTemplate` accepts anything and returns no parameters; unsupported resource types reach `CREATE_COMPLETE` with a placeholder ID and nothing created; stack policies are no-ops. | Floci note in Create stack (the success text no longer says "Template syntax is valid"), inventory notes, action description, and the CloudFormation lab's validate step |
| IoT device shadows | Only top-level keys of `desired`/`reported` are merged (a nested object replaces the stored one); the document has no `delta` or `metadata`. | Inventory note and the Update device shadow action description |
| SES receipt rules | Stored; Floci receives no inbound mail, so rules never run. The console doesn't offer them. | Inventory note |

## ElastiCache cache-aside lab (2026-10-24)

`elasticache/cache-aside` (SAA 3.3, 2.1), 8 steps, in `dashboard/labs/elasticache_labs.py`, the first ElastiCache lab
and its own batch after RDS. It starts Wave 2 (W2-1): VPC `lab-cache-vpc` (10.61.0.0/16) with private subnets in
us-east-1a and us-east-1b, security groups `lab-cache-app` and `lab-cache-redis` (6379 from the app group only), cache
subnet group `lab-cache-subnets`, parameter group `lab-cache-redis7` with `maxmemory-policy allkeys-lru`, replication
group `lab-product-cache` (primary and replica, Multi-AZ, automatic failover, encryption at rest), Aurora PostgreSQL
cluster `lab-cache-products-db` with a seeded `products` table, and Lambda `lab-product-reader` running cache-aside.

Checked live on a throwaway Floci 2.3.0 (a full run, a second run over the leftovers, steps out of order, a cache
group opened to 0.0.0.0/0, the eviction policy set back to noeviction, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Replication group | One Valkey 7.1 container per group, proxied on Floci's host from port 6379 up; the primary and reader endpoints are the same address. The replica is a record: `INFO replication` shows `role:master`, `connected_slaves:0`. Multi-AZ with one node is accepted (AWS refuses). `TestFailover` and `DescribeEvents` are unsupported. Nodes have no `PreferredAvailabilityZone` or `SecurityGroups`. | The step checks a replica, failover, Multi-AZ, encryption, subnet and parameter groups, and says the nodes share one container |
| Parameter group | Stored and reported `in-sync`, not applied: the container keeps `maxmemory-policy noeviction` and `maxmemory 0`. | The step checks the stored value; the miss step reports the container's real policy |
| Network | Subnet group keeps both subnets and zones. Security groups aren't enforced, and Lambda `VpcConfig` is stored but the function runs on Floci's Docker network. | The step checks the ingress rule names only the app group |
| Lambda reader | Reaches the cache at the primary endpoint (`floci:<port>`) and Floci at `AWS_ENDPOINT_URL`. GET, SET with EX, TTL and DEL are real. | Miss, hit and invalidation are observed, not simulated |
| Data API authorization | With IAM enforcement on, `rds-data:ExecuteStatement` is allowed only by `Resource: "*"`; the cluster ARN, `cluster:*` and `arn:aws:rds:*` are denied. The caller needs no `secretsmanager:GetSecretValue`. | The role policy has the scoped statements plus a labeled `FlociOnlyDataApi` statement to delete on AWS |

`check_labs --service elasticache` passed on 2.3.0.

## Streaming data lake lab (2026-10-24)

`kinesis/streaming-data-lake` (SAA 3.5, 4.1), 9 steps, in `dashboard/labs/kinesis_labs.py`, the first Kinesis lab
and its own batch after ElastiCache. It covers Wave 2 item W2-2: buckets `lab-clickstream-lake` and
`lab-clickstream-athena-results`, on-demand stream `lab-clickstream`, delivery stream `lab-clickstream-to-s3` (role
`lab-clickstream-firehose-role`), Glue database `lab_clickstream` with tables `raw_clicks` (JSON, partition projection
on `dt`) and `curated_clicks` (Parquet), and workgroup `lab-clickstream`.

Checked live on a throwaway Floci 2.3.0 (a full run, a second run over the leftovers, a new batch after a finished
run, steps out of order, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Kinesis | On-demand mode and retention changes are kept; an on-demand stream has one shard (AWS: four). PutRecords and TRIM_HORIZON replay are real. | The step reports the shard count |
| Firehose | A Kinesis-source stream really reads the stream and flushes to S3 within seconds at a 0-second buffer, evaluating `!{timestamp:yyyy-MM-dd}`. Accepts an expression prefix without an error prefix, and `PutRecord` on a Kinesis-source stream (AWS refuses both). Delivers with a role that has no permissions. Parquet conversion is stored, but with `FLOCI_AUTH_VALIDATE_SIGNATURES=true` the converter's read of its staging object fails (InvalidAccessKeyId) and the batch goes to the error output. | The step checks the error prefix and role policy itself |
| Glue | Databases and tables are stored. `StartCrawler` reports SUCCEEDED and creates no tables. Partition projection properties are stored and ignored. | The step creates the table through the Glue API |
| Athena | Runs SQL in a `floci-duck` DuckDB sidecar over Glue tables, reading Hive `dt=` folders and JSON or Parquet. DuckDB dialect: `CREATE EXTERNAL TABLE`, `MSCK REPAIR TABLE`, CTAS `WITH (...)` and `UNLOAD` fail; `COPY ... TO 's3://...' (FORMAT PARQUET, PARTITION_BY (dt))` works. A workgroup's result location is ignored (results go to `s3://floci-athena-results/` unless the query sets one), `DataScannedInBytes` is 0, and the scan cutoff is never applied. The sidecar downloads DuckDB's httpfs extension on first use, so it needs internet access. | The query steps compare counts with the batch and name the results bucket; the Parquet step shows the CTAS and runs the COPY |

`check_labs --service kinesis` passed on 2.3.0.

## DynamoDB at scale lab (2026-10-24)

`dynamodb/capacity-at-scale` (SAA 4.3, 3.3, 2.2), 8 steps, in `dashboard/labs/dynamodb_scale_labs.py` (delegated from
`dynamodb_labs.py`, so it sits in the DynamoDB batch). It covers Wave 2 item W2-3: provisioned table
`lab-orders-at-scale` with a NEW_AND_OLD_IMAGES stream, sized to 240 RCU and 120 WCU for 120 strongly consistent 6 KB
reads and 40 2.5 KB writes a second, read and write scalable targets from the sized capacity to 4x with 70% target
tracking, on-demand table `lab-sessions` with TTL on `expires_at`, audit table `lab-order-audit` fed by Lambda
`lab-order-shipped-audit` (role `lab-order-audit-role`) through a stream mapping filtered to MODIFY records whose new
status is SHIPPED, 7-day PITR, and a `us-west-2` replica.

Checked live on a throwaway Floci 2.3.0 (a full run, a second run over the leftovers, steps out of order, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Capacity | Provisioned throughput is stored and reported; requests are never throttled. Switching billing mode back and forth is allowed at once (AWS limits switches). | The step checks the capacity against the math |
| Application Auto Scaling | Scalable targets and target tracking policies are stored, and the policies create their CloudWatch alarms. DynamoDB publishes no consumed-capacity metrics, so nothing ever scales. | The step checks the targets and policies and counts the alarms |
| TTL | Really deletes expired items, usually within 15 to 60 seconds (AWS: up to a few days). | The step waits up to 150 seconds and checks live items stay |
| Streams to Lambda | The event source mapping is real: nested filter patterns, retries and bisect settings apply, and the function's scoped `PutItem` is enforced. The mapping reads the stream without checking the role. | The step checks the role's stream and write statements itself |
| PITR | `UpdateContinuousBackups` stores the flag and window. `RestoreTableToPointInTime` and `CreateBackup` return UnknownOperationException. | The step records the refused restore |
| Global table | A replica added with `ReplicaUpdates` is listed as ACTIVE, but no table exists in the replica Region and nothing is copied. | The step checks the stream and write auto scaling prerequisites and reports the missing table |

`check_labs --service dynamodb --lab capacity-at-scale` passed on 2.3.0.

## Lambda performance and cost lab (2026-10-24)

`lambda/performance-cost` (SAA 3.2, 4.2), 8 steps, in `dashboard/labs/lambda_performance_labs.py` (delegated from
`lambda_labs.py`, so it sits in the Lambda batch). It covers Wave 2 item W2-4: function `lab-price-quote` (role
`lab-price-quote-role`) with a CPU-bound pricing loop, a memory sweep over 128, 512, 1,024 and 1,769 MB with a cost per
million requests, version 1 (standard pricing) and version 2 (volume discount), alias `live` with a 20% canary,
reserved concurrency of 5 tested with 8 simultaneous calls, an AWS_IAM function URL on the alias, and promotion to
version 2.

Checked live on a throwaway Floci 2.3.0 (a full run, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Memory | The configured size reaches the function (`context.memory_limit_in_mb`), but CPU doesn't scale with it: the loop took about 40 ms at every size. No `LogResult` tail is returned. | The sweep reports flat durations and picks the smallest size under the target |
| Versions and aliases | Real: versions keep their memory and environment, and weighted routing sends about the configured share to the extra version (10 of 50 at 20%). `UpdateAlias` never clears `AdditionalVersionWeights`, even with an empty map. | Promotion ignores a leftover weight on the primary version and says AWS clears it; re-pointing the alias at version 1 recreates it |
| Reserved concurrency | Enforced: with 5 reserved, 5 of 8 simultaneous calls ran and 3 got TooManyRequestsException. A reservation of 0 throttles every call. | The step counts the throttles |
| Provisioned concurrency | `PutProvisionedConcurrencyConfig` is routed to the S3 handler (AuthorizationHeaderMalformed, "This endpoint belongs to s3"). | Explained, not configured |
| Function URLs | Served at `<id>.lambda-url.<region>.floci:4566`. Calling Floci's endpoint with that Host header invokes the alias with a version 2.0 HTTP event. AWS_IAM auth also answers unsigned requests. | The step signs its request, checks the auth type and CORS itself, and reports the unsigned answer |

`check_labs --service lambda --lab performance-cost` passed on 2.3.0.

## Block and file storage lab (2026-10-24)

`ec2/block-file-storage` (SAA 3.1, 4.1), 8 steps, in `dashboard/labs/ec2_storage_labs.py` (delegated from
`ec2_labs.py`, after the NAT lab). It covers Wave 2 item W2-5: instances `lab-storage-app-1` and `lab-storage-app-2` in
us-east-1a (group `lab-storage-app`), encrypted gp3 volume `lab-storage-gp3` (20 GiB, 3,000 IOPS, 125 MiB/s, grown to
40 GiB and 6,000 IOPS), encrypted io2 volume `lab-storage-io2` (100 GiB, 16,000 IOPS), and EFS file system
`lab-storage-shared` with mount targets in us-east-1a and us-east-1b (group `lab-storage-efs`, NFS only from the app
group), lifecycle to IA and Archive, backups, a TLS-only policy, and an `/orders` access point. Shell work runs
through SSM Run Command.

Checked live on a throwaway Floci 2.3.0 (a full run, then reset; the Ubuntu image was pulled from Docker Hub and
tagged as `public.ecr.aws/docker/library/ubuntu:22.04` because the sandbox can't reach ECR's CDN):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Volume limits | `CreateVolume` accepts any IOPS and throughput (20,000 IOPS on a 20 GiB gp3, 64,000 on a 20 GiB io2). | The steps check the gp3 and io2 limits and show the monthly cost |
| Attach and format | The attachment is a loop device in the privileged instance container, so `mkfs.ext4`, `mount` and the file are real. The instance's `BlockDeviceMappings` lists only the root volume. | Reported as real |
| Modify | `ModifyVolume` completes at once and the device grows; shrinking is refused; a second change within six hours is allowed. Online `resize2fs` doesn't take effect, so the step unmounts, resizes and remounts. | The step reports the refused shrink |
| Move | Detach and attach to another instance keep the data. Attaching across AZs, and deleting an attached volume, are refused. `DeleteVolume` fails with InternalError while an instance still has the device mounted. | Reset terminates the instances before deleting volumes |
| Snapshots | `CreateSnapshot` returns UnsupportedOperation. | The move step records the refusal |
| EFS | File systems, mount targets, security groups, lifecycle, backup policy, file system policy and access points are stored. No NFS server runs, every mount target reports AZ `us-east-1a` and IP `10.0.0.10`, a second mount target in one AZ is refused, and `DescribeFileSystems` with an unknown creation token raises FileSystemNotFound (AWS returns an empty list). | The steps check the subnets' AZs, the NFS rule, and every setting |

`check_labs --service ec2 --lab block-file-storage` passed on 2.3.0.

## Backup and DR plan lab (2026-10-24)

`backup/backup-dr-plan` (SAA 2.2, 1.3, 4.1; CLF 3.6), 7 steps, in `dashboard/labs/backup_labs.py` (a new lab service,
registered in `monolith.py` after Kinesis). It covers Wave 2 item W2-6: KMS key `alias/lab-backup-vault`, vault
`lab-orders-vault` with an access policy that denies `backup:DeleteRecoveryPoint` and a vault lock (7 to 2,555 days,
3-day grace), copy vault `lab-orders-vault-dr` in us-west-2, plan `lab-orders-plan` (rule `daily-35-days` at
`cron(0 5 * * ? *)`, kept 35 days and copied to the DR vault; rule `monthly-7-years`, cold after 90 days, kept 2,555),
role `lab-backup-role`, selection `tagged-daily` on the tag `backup=daily`, table `lab-orders-backup`, and a DR
strategy chooser over four workloads.

Checked live on a throwaway Floci 2.3.0 (a full run, then reset):

| Behavior | Floci 2.3.0 | Lab says |
| --- | --- | --- |
| Backup jobs | `StartBackupJob` reaches COMPLETED and creates a recovery point, which holds no data. | Reported as real, with a note that nothing is copied |
| Vault lock | Enforced: `DeleteRecoveryPoint` inside the minimum retention is refused. | The step shows the refused delete |
| Access policy | Stored, never evaluated. | The step checks the policy denies deletes |
| Plan rules | Schedules never run, lifecycle isn't validated (cold storage under 90 days before delete is accepted), and plan tags are dropped. | The steps check the cron, the 90-day cold storage minimum and both retentions |
| Selections | `ListOfTags` and `Conditions` are dropped, so the stored selection matches nothing. | The lab checks the tags it sent and that the table carries the tag |
| Restore, copy and more | `StartRestoreJob`, `StartCopyJob`, `GetRecoveryPointRestoreMetadata`, report plans, frameworks and `ListProtectedResources` fail with AuthorizationHeaderMalformed (misrouted). | The step records the refused restore; the DR step works out RPO and RTO itself |

`check_labs --service backup` passed on 2.3.0.

## Recommended next work

| Item | Why not here |
| --- | --- |
| Re-check version-specific notes when the image moves past 2.3.0 (on 2026-10-24 `floci/floci:latest` was still the 2.3.0 image; only nightlies are newer) | CloudWatch alarm actions (SNS on `main`), DynamoDB backups and PITR restore, ECR `PutImage`, S3 lifecycle expiration (`main` applies it; the Management tab note says it never runs), ELBv2 HTTPS and stickiness. Better still, detect support at run time. |
