# Console accuracy review

A service-by-service review of the React console in `frontend/src/` against the real AWS Management
Console and AWS APIs, as far as Floci 2.3.0 allows. Each section lists findings ranked by severity,
what was fixed, and what is left with the reason.

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

