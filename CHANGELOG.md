# Changelog

## Unreleased

- New lab, backup and DR plan (SAA 2.2, 1.3, 4.1; CLF 3.6): "Plan backups and choose a disaster recovery strategy with AWS Backup", 7 steps (last lab of Level 4, and the first AWS Backup lab). It creates a KMS-encrypted vault with an access policy that denies deleting recovery points, locks it (7 to 2,555 days, 3-day grace), adds a copy vault in us-west-2, builds a plan with a daily rule kept 35 days and copied to the DR vault and a monthly rule moved to cold storage after 90 days and kept 7 years, selects resources by the tag `backup=daily` with the AWS Backup service role, backs up a DynamoDB table on demand, and picks backup and restore, pilot light, warm standby or multi-site active-active from each workload's RPO and RTO. On Floci 2.3.0 backup jobs complete and the vault lock blocks an early delete; the lab checks what Floci only stores or skips: the lifecycle rules, the schedule, the access policy, and the tag selection (Floci drops its tags). Restore and copy jobs aren't supported. The catalog is now 78 labs across 25 services and 484 steps.
- New lab, block and file storage (SAA 3.1, 4.1): "Choose and use block and file storage with EBS gp3, io2, and EFS", 8 steps (last lab of Level 4). It launches two app servers in one AZ, creates an encrypted gp3 volume and checks it against the gp3 IOPS and throughput limits, prices a 16,000 IOPS io2 volume against gp3, attaches, formats and mounts the gp3 volume through SSM Run Command, grows it to 40 GiB and 6,000 IOPS while attached and extends the filesystem, moves it with its data to the second instance, and builds an encrypted EFS file system with mount targets in two AZs behind an NFS-only security group, IA and Archive lifecycle tiering, backups, a TLS-only policy and an access point. EBS attach, format, grow and move are real on Floci 2.3.0 (each attachment is a loop device in the instance container); the lab checks what Floci only stores: the volume limits and every EFS setting. Notes say what Floci doesn't do: IOPS and throughput limits aren't checked, snapshots are unsupported, and EFS runs no NFS server and reports the same AZ and IP for every mount target. The catalog is now 77 labs across 24 services and 477 steps.

- New lab, Lambda performance and cost (SAA 3.2, 4.2): "Tune Lambda performance and cost with memory sizing, aliases, canaries, and concurrency", 8 steps (last lab of Level 4). It creates a price quote function with a CPU-bound loop, sweeps 128 to 1,769 MB and picks the cheapest size under 250 ms from a cost per million requests, publishes version 1, points a `live` alias at it, publishes a volume-discount version 2 and sends 20% of `live` to it as a canary (checking each price matches the version that served it), reserves 5 concurrent executions and fires 8 calls at once to show the throttles, serves `live` from an AWS_IAM function URL called with a SigV4-signed request, and promotes version 2. Versions, weighted routing, throttling and the function URL are real on Floci 2.3.0. Notes say what Floci doesn't do: CPU doesn't scale with memory (durations stay flat), function URLs with AWS_IAM auth also answer unsigned requests, UpdateAlias never clears routing weights, and provisioned concurrency requests are routed to S3, so the lab explains provisioned concurrency without configuring it. The catalog is now 76 labs across 24 services and 469 steps.

- New lab, DynamoDB at scale (SAA 4.3, 3.3, 2.2): "Run DynamoDB at scale with capacity planning, auto scaling, TTL, streams, and global tables", 8 steps (last lab of Level 4). It creates a provisioned orders table with a NEW_AND_OLD_IMAGES stream, sizes it to 240 RCU and 120 WCU from a stated workload, registers read and write scalable targets with 70% target tracking, puts sessions in an on-demand table that TTL expires, audits shipped orders with a Lambda stream processor whose event source mapping filters to SHIPPED updates and retries with bisect, turns on 7-day point-in-time recovery, and adds a us-west-2 replica. TTL and the filtered stream processor are real on Floci 2.3.0; the lab checks what Floci only stores: the capacity, the scaling setup (Floci creates the alarms but never scales), PITR (restores and on-demand backups are unsupported) and the replica (listed as ACTIVE with no table in the other Region). The DynamoDB inventory notes now say capacity is never enforced and on-demand backups and restores are unsupported. The catalog is now 75 labs across 24 services and 461 steps.

- New lab, streaming data lake (SAA 3.5, 4.1): "Stream clickstream events into an S3 data lake and query them with Athena", 9 steps (last lab of Level 4, first Kinesis lab). It creates a lake bucket with Block Public Access and default encryption and an Athena results bucket that expires results after 7 days, an on-demand Kinesis data stream with 48-hour retention, and a Firehose delivery stream that reads the stream and writes to `raw/clicks/dt=!{timestamp:yyyy-MM-dd}/` with an error prefix. A producer puts 24 click events with PutRecords (retrying failed entries) and replays them from TRIM_HORIZON; the lab waits for Firehose to land them, catalogs the raw JSON in Glue with partition projection, creates an Athena workgroup with a 100 MB scan limit, counts page views for one day's partition, and writes a Snappy-compressed, partitioned Parquet copy that it queries again. Floci 2.3.0 runs the pipeline for real; the lab checks what it skips: an expression prefix without an error prefix, projection properties, and the workgroup's settings. Notes say what Floci doesn't do: an on-demand stream gets one shard, Firehose ignores its role and fails Parquet conversion with signature validation on, the Glue crawler creates no tables, Athena speaks DuckDB SQL (no CTAS or Hive DDL, so the Parquet step runs the matching DuckDB COPY), ignores the workgroup's result location, reports 0 bytes scanned and never applies the scan limit. The catalog is now 74 labs across 24 services and 453 steps.

- New lab, ElastiCache cache-aside (SAA 3.3, 2.1): "Speed up reads with an ElastiCache cache-aside pattern", 8 steps (last lab of Level 4, first ElastiCache lab, starting Wave 2). It puts a cache subnet group in two private subnets with a cache security group that allows port 6379 only from the app security group, sets `maxmemory-policy allkeys-lru` in a redis7 parameter group, creates a Multi-AZ replication group with a primary and a replica, automatic failover and encryption at rest, seeds an Aurora PostgreSQL product table through the Data API, and deploys a Lambda reader in the private subnets that checks the cache before the database. A miss reads the database and caches the product with a 300-second TTL, five repeat reads come from the cache without a query, and a price update writes the database and deletes the cached copy so the next read caches the new price. The cache, the database and the reader are real on Floci 2.3.0; the lab checks what Floci only stores: the security group rule, the subnet group zones, the eviction policy, and the replica and Multi-AZ settings (Floci accepts Multi-AZ with one node). Notes say what Floci doesn't do: one Valkey container serves both endpoints with no replicas, nodes keep no AZs or security groups, the parameter group isn't applied (the container stays on `noeviction`), `TestFailover` is unsupported, Lambda VPC settings change nothing, and Data API calls are authorized only against `Resource: "*"`, so the role has a labeled `FlociOnlyDataApi` statement to delete on AWS. The catalog is now 73 labs across 23 services and 444 steps.

## 2.0.2 — 2026-10-23

- More Floci features the console didn't show (Route 53, CloudFront):
  - **Route 53 private hosted zones:** Create hosted zone offers a private zone on a VPC, and private zones get a VPCs tab to associate and disassociate VPCs (at least one stays, as on AWS). A Floci note says its DNS server answers private zones to any client.
  - **CloudFront origin access control:** a new Origin access tab lists controls with the distributions that use them, creates and deletes them, and shows the S3 bucket policy to add; Create distribution can attach one to an S3 origin. A Floci note says Floci can't edit a control.

- More Floci features the console didn't show (Lambda, Step Functions, DynamoDB, Kinesis):
  - **Lambda async destinations:** a new Asynchronous invocation tab on each function shows and edits retry attempts, maximum event age and on-success/on-failure destinations, with the AWS limits Floci doesn't check.
  - **Step Functions versions and aliases:** a new Versions and aliases tab publishes and deletes versions and creates, edits and deletes aliases, including two-version weighted routing for canary releases.
  - **DynamoDB export to S3:** a new Exports to S3 tab lists a table's exports (with failure reasons) and starts full DynamoDB JSON exports. The console requires point-in-time recovery, as AWS does; Floci doesn't.
  - **Kinesis resharding:** the Kinesis workbench gets Update shard count, Split shard (even split by default) and Merge shards (adjacent shards only).

- More Floci features the console didn't show (IAM, Secrets Manager, EC2):
  - **IAM password policy:** a new Account settings page shows the account password policy (or that the IAM default applies) and edits it with the AWS limits. A Floci note says Floci enforces it on login profiles but accepts any password when no policy is set.
  - **IAM credential report:** a new Credential report page generates the report, waits until it's ready, and lists and downloads it.
  - **Secrets Manager replication:** the secret details get a Replication tab to replicate a secret to other Regions, remove replicas and promote a replica to a standalone secret.
  - **EC2 key pairs:** a new Key Pairs page creates (private key shown once, with a .pem download), imports and deletes key pairs. A Floci note says ED25519 requests return RSA keys.

- EC2 Elastic IP and NAT gateway tables (see "Elastic IP and NAT gateway tables" in `docs/console-accuracy-review.md`):
  - **Elastic IPs** (`/ec2/elastic_ips`): the console's columns, a filter and details, with Allocate (Name tag), Associate (with "Allow reassociation"), Disassociate and Release. The association column also shows NAT gateways, which Floci leaves out of `DescribeAddresses`.
  - **NAT gateways** (`/ec2/nat_gateways`): state, connectivity type, primary addresses, VPC and subnet, the route tables that target each gateway, Create (public with an unused Elastic IP, or private) and Delete with typed confirmation.
  - **Says what Floci 2.3.0 doesn't do:** NAT gateways route no traffic; Floci accepts a public NAT gateway without an Elastic IP and one address on several gateways, releases associated addresses, re-associates without `AllowReassociation`, keeps routes to a deleted gateway active, and reports an instance's container IP as an associated Elastic IP's address. The console and backend apply the AWS rules for the first ones.

- S3 management and data protection forms (see "S3 management and data protection forms" in `docs/console-accuracy-review.md`):
  - **Management tab:** lifecycle rules (create, edit, delete) with the console's actions: transitions of current and noncurrent versions, expiration, permanent deletion of noncurrent versions, expired delete markers and incomplete multipart uploads, scoped by prefix and tags. Replication rules (create, delete) with priority, prefix, destination bucket, IAM role, storage class and delete marker replication.
  - **Properties tab:** Intelligent-Tiering Archive configurations (create, edit, delete), and Object Lock can be turned on with a default retention mode and period. An object's details show its Object Lock retention and legal hold, with Edit retention (including the governance bypass) and Turn on/off legal hold for the version shown.
  - **AWS rules Floci skips are checked:** Object Lock and replication need versioning (on both buckets for replication), versioning can't be suspended on a locked bucket, retention and legal holds need Object Lock, and lifecycle rules follow the AWS limits (30 days before Standard-IA, expiration after the last transition, at least one action).
  - **Says what Floci doesn't do:** lifecycle rules never run, replication copies nothing, and Intelligent-Tiering never moves objects; Object Lock is enforced as in AWS. The S3 inventory no longer lists replication and Intelligent-Tiering as not implemented.

- Floci features the console didn't show (see "Floci features the console didn't show" in `docs/console-accuracy-review.md`):
  - **EventBridge archives and replays:** a new Archives and replays tab lists archives (source bus, event count, retention, pattern) and replays (status, time window). Create and delete archives, and start a replay of a time window back to the source bus, optionally limited to chosen rules. A Floci note says replayed events don't carry `replay-name` and replays finish at once.
  - **CloudWatch dashboards:** a new Dashboards tab lists dashboards, draws text and metric widgets from the body (other widget types show their JSON), and creates, edits and deletes them through a JSON editor.
  - **Logs subscription filters:** the selected log group lists its subscription filters, and you can create, replace and delete them (Kinesis, Firehose or Lambda destinations; at most two, as on AWS).

- New lab, Route 53 routing and failover (SAA 2.2, 3.4): "Route traffic and fail over between Regions with Route 53", 8 steps (last lab of Level 4, first Route 53 lab, completing Wave 1). It creates a public hosted zone, HTTP health checks for a primary endpoint in us-east-1 and a standby in us-west-2, failover PRIMARY and SECONDARY records for `www`, an alias A record at the zone apex with EvaluateTargetHealth, a 90/10 weighted pair for a canary release, and latency records for two Regions; a drill works out the answers with the primary healthy, with the primary failing (the secondary answers and the apex follows), and with both failing (Route 53 returns the primary); and a private hosted zone of the same name on a VPC with DNS support and hostnames gives split-horizon answers. Floci 2.3.0 stores the zones, records and health checks but never probes or routes, so the lab checks the AWS rules Floci skips: one primary and one secondary, existing health checks, short TTLs, no CNAME at the apex, an alias to an existing name, weights from 0 to 255, no mixed routing policies, valid health check intervals and thresholds, and DNS attributes on the VPC. Notes say what Floci doesn't do: every health check reports success without probing, GeoLocation and MultiValueAnswer are dropped from records, `UpdateHealthCheck` replaces the whole config, and its DNS server answers only private zone A records. The catalog is now 72 labs across 22 services and 436 steps.

- New lab, KMS key management (SAA 1.3): "Manage a customer managed KMS key with a key policy, rotation, grants, and envelope encryption", 8 steps (last lab of Level 4). It creates a key administrator role and an application role, a customer managed key whose key policy keeps the account root statement and separates administrators (no crypto use) from users (no admin rights), turns on automatic rotation (180 days) and rotates once on demand, then checks that ciphertext from before the rotation still decrypts. A grant lets the application role encrypt and decrypt only with the encryption context `app=orders,purpose=customer-records`. Envelope encryption: a record is encrypted locally with a `generate-data-key` data key and decrypted again, and a wrong encryption context is refused. A bucket gets the key as its SSE-KMS default with an S3 Bucket Key, and a last step sees Encrypt refused while the key is disabled and while it is pending deletion, then cancels the deletion. Floci 2.3.0 enforces the encryption context, on-demand rotation and both key states; the lab checks the key policy and grant itself and says what Floci skips: key policies and grants are stored but not used to authorize (only IAM policies are), automatic rotation is recorded without a period or schedule, S3 never calls KMS for SSE-KMS (header-less uploads aren't reported as encrypted), and a cancelled deletion leaves the key Enabled instead of Disabled. Local encryption uses an HMAC-SHA256 stand-in for AES-256-GCM because the standard library has no AES. The catalog is now 71 labs and 428 steps.

- New lab, three-tier VPC with NAT gateways (SAA 1.2, 4.4, 3.4, 2.2): "Build a three-tier VPC with a NAT gateway in each Availability Zone", 10 steps (first lab of Level 5). It lays out public, application and data subnets in us-east-1a and us-east-1b, routes the public tier to an internet gateway, gives each zone a NAT gateway with its own Elastic IP, routes each application subnet through the NAT gateway in its own zone, keeps the data tier on a local-only route table with a two-zone DB subnet group, and adds an S3 gateway endpoint to the application route tables so S3 traffic skips the NAT gateways. A last step traces every subnet's default route. The checks enforce AWS rules Floci skips: no shared Elastic IP, no public NAT gateway without one, NAT gateways only in public subnets, no cross-zone NAT route, and a DB subnet group in two zones. Notes say what Floci doesn't do: it routes no instance traffic (instances reach the internet through Docker's bridge whatever the route table says), reports NAT gateways available at once, doesn't show which NAT gateway uses an Elastic IP, keeps routes to a deleted NAT gateway active, and doesn't add the endpoint's prefix-list route. The catalog is now 70 labs and 420 steps.

- New lab, S3 data protection and storage tiers (SAA 1.3, 4.1, 2.2): "Protect and tier S3 data with Object Lock, storage classes, lifecycle rules, and replication", 13 steps (Level 4). A bucket created with Object Lock gets a default GOVERNANCE retention; the lab checks that deleting the locked version is refused, that a legal hold also blocks a delete with `--bypass-governance-retention`, and that a COMPLIANCE record refuses it too. A versioned archive bucket then takes a Deep Archive upload, an Intelligent-Tiering object with archive tiers, a lifecycle rule for noncurrent versions, expired delete markers and incomplete multipart uploads, Cross-Region Replication to a versioned bucket in us-west-2, and Requester Pays. Notes say what Floci doesn't do: storage classes are labels only (a Deep Archive object reads without a restore), Intelligent-Tiering and lifecycle rules never move or delete objects, replication copies nothing, and Requester Pays bills nothing. The COMPLIANCE record is kept for 60 seconds, and Reset waits for it to expire before deleting the bucket. The catalog is now 69 labs and 410 steps.

- New lab, highly available web tier (SAA 2.2, 2.1, 3.2, 3.4): "Build a highly available web tier with an Application Load Balancer and Auto Scaling", 12 steps, the first Elastic Load Balancing lab (Level 4). It builds a VPC with public subnets in two Availability Zones, a load balancer security group that is the only source the instances accept, a launch template whose user data starts a web server, a health-checked target group, an internet-facing ALB, and an Auto Scaling group (2 to 4 instances, ELB health checks). Each step checks live state: both instances turn healthy targets in different zones, requests sent to the ALB's DNS name from inside an instance are answered by every instance, a scale-out to 3 adds a healthy target, a terminated instance is replaced and the target group follows, and an instance refresh to launch template version 2 leaves only the new release serving. Notes say what Floci doesn't do: the target tracking policy is stored but never runs (the lab scales out by hand), an instance refresh replaces every instance at once, an instance marked Unhealthy isn't replaced, only the latest launch template version is kept, and the listener port is reachable from instances, not from your computer. The catalog is now 68 labs and 397 steps.

- HA web tier controls (see "HA web tier controls" in `docs/console-accuracy-review.md`):
  - **Load balancer console:** new Health checks and Attributes tabs for a target group: edit the health check path, success codes, thresholds, timeout and interval, the deregistration delay, and stickiness (load balancer or application cookie, with duration). Listeners have Edit (port and default action: forward to a target group or a fixed response), and the selected listener's rules are listed in priority order with Add rule, Edit (path, target group, priority) and Delete.
  - **Auto Scaling workbench:** Start instance refresh, Describe instance refreshes and Set instance health.
  - **Says what Floci doesn't apply:** stickiness and the deregistration delay are stored but not applied (requests go round robin with no cookie; deregistered targets are removed at once); a changed health check interval is reported but probing keeps the old interval; an instance refresh replaces every instance at once whatever the minimum healthy percentage; an Unhealthy instance is never replaced. Floci can't cancel or roll back a refresh, so the dashboard doesn't offer it. Settings Floci would store without checking (out-of-range delays or durations, reserved cookie names) are refused with the AWS limits.

- Emulation notes on every console (see "Inventory notes on every console" in `docs/console-accuracy-review.md`):
  - **Shown everywhere:** each service console, custom or generic, now has a collapsed "How Floci emulates this service" section with the service's notes on what Floci simulates and what it accepts without simulating. Before, only the generic workbench showed them.
  - **Corrected notes:** EC2 key pairs hold real keys and security groups are enforced only with `FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED=true`; Pipes targets include EventBridge but not Kinesis; Athena runs queries in DuckDB; EKS has node groups and Fargate profiles; ACM exports certificates requested with export enabled; Secrets Manager rotates on schedule.
  - **Removed:** notes that repeated what a console says inline (DynamoDB PITR, CloudFront caching, Secrets Manager policies, RDS snapshots, restore, replicas and failover, CloudTrail events, Route 53) and notes about the dashboard's own code or protocols.

- CloudTrail log files: a trail's new **Log files** tab lists the gzipped files the trail delivered to its S3 bucket (`<prefix>/AWSLogs/…`), newest first, a day at a time, and shows the records in the one you open (event time, name, source, user, source IP, resources, error code) with each record's raw JSON. Errors such as a missing bucket or AccessDenied are shown, not an empty list. A Floci note says that only S3 data events matching the trail's event selectors are delivered, about every 60 seconds by default, and the empty Event history page links to it. Backed by `GET /api/cloudtrail/trails/<trail>/log-files/` and `.../log-files/read/?key=`.

- RDS and Aurora resilience:
  - **Console:** a database's Actions menu adds Create read replica, Promote and Restore to point in time (latest restorable time or a custom UTC time); a cluster's Actions menu adds Add reader, Failover (to a chosen reader or the one RDS picks) and Take snapshot. The Snapshots tab lists cluster snapshots, which restore to a new cluster or delete after typed confirmation. A database's Configuration tab shows its replication role, source, replicas, replication status and latest restorable time.
  - **Says what Floci doesn't do:** point-in-time restore copies the source as it is now whatever time you choose; a replica gets one copy of its PostgreSQL source and isn't updated afterwards (and accepts writes); Multi-AZ runs no standby; a failover moves only the writer role, because a cluster's instances share one database; cluster snapshots are Aurora PostgreSQL only.
  - **New lab (SAA 2.2, 3.3):** "Design resilient databases with read replicas, point-in-time restore, and Aurora failover", 10 steps. It checks the replica link on both ends, the restored instance, the promotion, the writer and reader roles, an order row written through the Data API that survives the failover, and the cluster snapshot. The catalog is now 67 labs and 385 steps.

- Floci gap fixes (see "Floci gap fixes" in `docs/console-accuracy-review.md`):
  - **Says what Floci doesn't apply:** consoles note where Floci stores a setting without enforcing or running it: SQS, KMS, ECR, Secrets Manager and Backup policies (only S3, SNS and Lambda policies are evaluated), CloudWatch alarm actions, Auto Scaling policies, scheduled actions and lifecycle hooks, DynamoDB point-in-time restore, Route 53 routing policies and health checks, KMS automatic rotation, CloudFront caching, NAT gateways, Config evaluations and more.
  - **S3 anonymous access:** a bucket's Permissions tab tests whether an unsigned request can list it and warns that, with `FLOCI_SERVICES_S3_ENFORCE_AUTH=false` (the Compose default), Block Public Access and bucket policies don't stop anonymous reads or writes.
  - **RDS snapshots:** take a manual DB snapshot of a PostgreSQL database, restore it to a new instance, or delete it, from the new Snapshots tab.
  - **Fixed:** Cloud Map "Discover instances" works (botocore no longer adds the `data-` host prefix); CloudTrail's note now says S3 data events reach the trail bucket and `LookupEvents` returns nothing; Settings shows auth flags it can't see as "Not known" instead of "Permissive".
  - **Removed:** Cloud Map "Update instance health" (Floci doesn't implement it) and 170 inventory notes that described old Floci releases.

- Backend accuracy review (see "Backend API review" in `docs/console-accuracy-review.md`):
  - **Incomplete lists are named:** when Floci doesn't return a list (often an operation it doesn't implement), or the dashboard shows only part of one, every console now shows a "Some lists are incomplete" warning instead of presenting the list as empty.
  - **No invented results:** the EKS kubeconfig and ECR push commands are built from what Floci reports, with the configured Region and `--endpoint-url`; ECR "Inject mock image tag" reports Floci's error instead of a made-up image; CodeDeploy's no-op "Simulate lifecycle event" is removed; IAM's account-root trust policy, assume-role by name and Cognito pool ARNs use the real account.
  - **No false success:** S3 "Empty bucket" deletes every version and delete marker, as the console does, and reports objects it couldn't delete; Settings → Reset Floci reports an unreachable Floci as an error.
  - **Visible defaults:** generic forms show the values used when a field is left blank. Auto Scaling scaling policies no longer send a 60-second cooldown you didn't set.
  - **Fixed lists:** Config remediation configurations and Pricing price lists load; Cognito pages pools, clients, users and groups past 60; the S3 objects view says when a bucket has more than 1,000 objects.
  - **EKS lab:** "Generate kubeconfig" waits for the cluster to be ACTIVE instead of passing with a placeholder.
- Added exam study tracks for AWS Certified Cloud Practitioner (CLF-C02) and Solutions Architect – Associate (SAA-C03):
  - **Track switcher:** the labs page switches between Levels and each exam (`/labs/?track=SAA-C03`).
  - **Exam view:** labs are grouped by domain and task statement, with progress weighted by each domain's share of the exam.
  - **Concept cards:** objectives without a lab (pricing, support plans, data ingestion, and so on) get short study cards, each pointing to the planned lab when there is one.
  - **Badges and filter:** lab cards and headers show exam badges such as `SAA 2.1`, and the filter accepts them.
  - **API:** tags, guides, and cards come from `dashboard/labs/exams.py` and `/api/labs/exams/`.
- Re-leveled the lab curriculum so core Associate-exam topics come early:
  - **Into Level 1:** the inline-policy lab.
  - **Into Level 2:** IAM roles, STS session policies, instance profiles, the EC2 instance-role lab, S3 Block Public Access and bucket policies, and the VPC and security-group/network-ACL labs.
  - **Renamed:** levels 4 and 5 are now "Workflows, Containers & Enforcement" and "Private Networking & Platform". Level subtitles describe skills instead of naming certifications.
  - **Numbering:** lab numbers come from curriculum position, so they changed; the UI derives the lab count instead of hard-coding 66.
- Six labs now do what their titles and descriptions promise, which matters for using them as exam prep:
  - **CloudWatch metric alarms:** the alarm reaches ALARM by evaluating a real error spike instead of `set-alarm-state`, and you watch it settle in OK on healthy data first.
  - **CloudWatch Logs:** the lab now creates the metric filter its title names, plus a 30-day retention policy, and confirms only the HTTP 500 lines became metric data.
  - **Secrets Manager:** adds automatic rotation with a Lambda rotation function, five new steps.
  - **CloudFormation:** previews an update with a change set (add a dead-letter queue, modify the jobs queue in place) before applying it, three new steps.
  - **S3 lifecycle:** the rule now tiers logs to Standard-IA and Glacier before expiring them.
  - **RDS:** the instance is created encrypted with 7-day automated backups. Its create, describe, and modify steps now check real instance state instead of always passing.
  - The catalog is now 375 steps.
- Added `docs/certification-coverage.md`, mapping every lab to the AWS Cloud Practitioner (CLF-C02) and Solutions Architect – Associate (SAA-C03) exam guides, with a gap analysis, Floci 2.3.0 support findings, and a prioritized plan for new labs.
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
