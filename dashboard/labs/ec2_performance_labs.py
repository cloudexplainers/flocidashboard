"""Performance tuning lab: an app instance whose workers saturate its vCPUs and whose report job runs out of memory,
both measured from the instance and alarmed on, a right-sized replacement chosen from the instance type catalog and
launched from a new launch template version, a gp2 volume moved to gp3 with the IOPS and throughput it needs, an RDS
database with storage autoscaling, Performance Insights, a larger class and an RDS Proxy, and S3 multipart uploads,
byte-range reads and Transfer Acceleration.

Floci 2.4.0 runs each instance as a container with the CPU and memory limits of its instance type (for the types in
its catalog), so a busy instance is really throttled and a job larger than its memory is really killed. It runs Run
Command, evaluates the alarms, creates launch template versions, runs a real PostgreSQL database behind RDS, registers
RDS Proxy targets, and runs S3 multipart uploads and ranged reads. What Floci does not do: it publishes no AWS/EC2,
AWS/EBS or AWS/RDS metrics (the lab measures the instance itself), it keeps a stopped instance's CPU and memory limits
when its type changes (so the lab replaces the instance), it records volume modifications without changing disk
performance or checking gp3 limits, and it stores Performance Insights, storage autoscaling and Transfer Acceleration
settings without acting on them.
"""

from __future__ import annotations

import io
import json
import math
import time
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'ec2'
LAB_KEY = 'performance-tuning'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

APP = 'lab-perf-tuning'
IMAGE_ID = 'ami-ubuntu2204'
TEMPLATE = 'lab-perf-app'
SMALL_TYPE = 't3.micro'
SMALL_NAME = 'lab-perf-app-small'
RIGHT_NAME = 'lab-perf-app-right'
WORKERS = 3
TARGET_CPU = 75
CPU_THRESHOLD = 85
REPORT_MIB = 1200
MEMORY_HEADROOM = 1.25
ARCHITECTURE = 'x86_64'

NAMESPACE = 'LabPerf'
CPU_METRIC = 'cpu_used_percent'
THROTTLE_METRIC = 'cpu_throttled_periods'
MEM_METRIC = 'mem_used_percent'
OOM_METRIC = 'oom_kills'
CPU_ALARM = 'lab-perf-cpu-high'
OOM_ALARM = 'lab-perf-memory-oom'

VOLUME_NAME = 'lab-perf-data'
VOLUME_SIZE = 100
VOLUME_DEVICE = '/dev/sdf'
NEEDED_IOPS = 4000
NEEDED_THROUGHPUT = 250
INVALID_IOPS = 20000

DB_ID = 'lab-perf-db'
DB_CLASS = 'db.t3.micro'
DB_TUNED_CLASS = 'db.t3.medium'
DB_STORAGE = 20
DB_MAX_STORAGE = 100
DB_USER = 'labadmin'
DB_PASSWORD = 'LabPerfPassw0rd!'
PI_RETENTION_DAYS = 7
DB_SECRET = 'lab-perf-db-credentials'
PROXY = 'lab-perf-proxy'
PROXY_ROLE = 'lab-perf-proxy-role'
PROXY_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{PROXY_ROLE}'
INLINE_POLICY = 'lab-perf'

BUCKET = 'lab-perf-transfer'
BIG_KEY = 'exports/daily-report.bin'
SMALL_KEY = 'exports/summary.bin'
MIB = 1024 * 1024
BIG_MIB = 20
SMALL_MIB = 5
PART_MIB = 8
RANGE_BYTES = MIB

COMMAND_TIMEOUT = 90
BOOT_TIMEOUT = 150
ALARM_WAIT_SECONDS = 120
DB_WAIT_SECONDS = 240
FLOCI = 'How Floci emulates this: '

# The shell helpers read cgroup v1 (Docker on Linux) or cgroup v2 (Docker Desktop) files.
CGROUP_HELPERS = (
    'cg2() { [ -f /sys/fs/cgroup/cpu.max ]; }\n'
    'usage_ns() { if cg2; then echo $(( $(awk \'/^usage_usec/{print $2}\' /sys/fs/cgroup/cpu.stat) * 1000 )); '
    'else cat /sys/fs/cgroup/cpuacct/cpuacct.usage; fi; }\n'
    'throttled() { if cg2; then awk \'/^nr_throttled/{print $2}\' /sys/fs/cgroup/cpu.stat; '
    'else awk \'/^nr_throttled/{print $2}\' /sys/fs/cgroup/cpu/cpu.stat; fi; }\n'
    'oom_kills() { if cg2; then awk \'/^oom_kill /{print $2}\' /sys/fs/cgroup/memory.events; '
    'else awk \'/^oom_kill /{print $2}\' /sys/fs/cgroup/memory/memory.oom_control; fi; }\n'
)
LIMITS_SCRIPT = CGROUP_HELPERS + (
    'if cg2; then read q p < /sys/fs/cgroup/cpu.max; echo "cpu_quota=$q"; echo "cpu_period=$p"; '
    'echo "mem_limit=$(cat /sys/fs/cgroup/memory.max)"\n'
    'else echo "cpu_quota=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us)"; '
    'echo "cpu_period=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)"; '
    'echo "mem_limit=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)"; fi'
)
WORKLOAD_SCRIPT = CGROUP_HELPERS + (
    f'for w in $(seq 1 {WORKERS}); do (timeout 16 perl -e \'$0="lab-perf-worker"; while (1) {{}}\' &); done\n'
    'sleep 3\n'
    'a=$(usage_ns); t1=$(throttled); s=$(date +%s%N)\n'
    'sleep 10\n'
    'b=$(usage_ns); t2=$(throttled); e=$(date +%s%N)\n'
    'echo "cpu_ns=$((b - a))"\n'
    'echo "wall_ns=$((e - s))"\n'
    'echo "throttled=$((t2 - t1))"'
)
SETTLED_SCRIPT = 'echo "busy=$(ps -eo comm= | grep -cE \'^(apt|apt-get|dpkg|http|gpgv)$\')"'
# The job writes every byte it holds. A zero-filled buffer (vec) can finish inside a 1 GiB limit when Docker emulates the
# instance's architecture, as Docker Desktop on Apple silicon does for x86 AMIs, so the kill never came.
REPORT_SCRIPT = CGROUP_HELPERS + (
    'before=$(oom_kills)\n'
    f'perl -e \'$0="lab-perf-report"; my $data = "r" x ({REPORT_MIB} * 1048576); print "report=done\\n"\'\n'
    'echo "exit=$?"\n'
    'echo "oom=$(( $(oom_kills) - before ))"'
)


# ── Pure helpers ─────────────────────────────────────────────────────────────

def parse_values(stdout: str) -> dict[str, str]:
    """name=value lines from a script's output."""
    values = {}
    for line in (stdout or '').splitlines():
        name, sep, value = line.strip().partition('=')
        if sep and name:
            values[name] = value.strip()
    return values


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_limits(stdout: str) -> dict[str, float | None]:
    """vCPUs and memory (MiB) the instance's container may use; None means no limit."""
    values = parse_values(stdout)
    quota, period = _number(values.get('cpu_quota')), _number(values.get('cpu_period'))
    memory = _number(values.get('mem_limit'))
    vcpus = round(quota / period, 2) if quota and period and quota > 0 else None
    memory_mib = round(memory / MIB) if memory and memory < 2 ** 50 else None
    return {'vcpus': vcpus, 'memory_mib': memory_mib}


def cpu_percent(stdout: str, vcpus: float) -> dict[str, float] | None:
    """CPU used over the sample as a share of the instance's vCPUs, and how often the limit throttled it."""
    values = parse_values(stdout)
    used, wall = _number(values.get('cpu_ns')), _number(values.get('wall_ns'))
    if not used or not wall or not vcpus:
        return None
    return {'cpu': round(min(100.0, used / (wall * vcpus) * 100), 1),
            'throttled': _number(values.get('throttled')) or 0.0}


def report_outcome(stdout: str) -> dict[str, Any]:
    values = parse_values(stdout)
    return {'finished': values.get('report') == 'done', 'exit': values.get('exit'),
            'oom_kills': int(_number(values.get('oom')) or 0)}


def recommend(instance_types: list[dict[str, Any]], workers: int = WORKERS, peak_mib: int = REPORT_MIB) -> dict | None:
    """The smallest x86_64 type with enough vCPUs to keep the workers under the target and room for the job."""
    vcpus_needed = math.ceil(workers / (TARGET_CPU / 100))
    memory_needed = math.ceil(peak_mib * MEMORY_HEADROOM)
    fits = []
    for item in instance_types:
        architectures = (item.get('ProcessorInfo') or {}).get('SupportedArchitectures') or []
        vcpus = (item.get('VCpuInfo') or {}).get('DefaultVCpus') or 0
        memory = (item.get('MemoryInfo') or {}).get('SizeInMiB') or 0
        if ARCHITECTURE in architectures and vcpus >= vcpus_needed and memory >= memory_needed:
            fits.append({'type': item['InstanceType'], 'vcpus': vcpus, 'memory_mib': memory})
    if not fits:
        return None
    best = min(fits, key=lambda fit: (fit['vcpus'], fit['memory_mib'], fit['type']))
    return {**best, 'vcpus_needed': vcpus_needed, 'memory_needed_mib': memory_needed}


def gp2_baseline_iops(size_gib: int) -> int:
    """gp2 earns 3 IOPS per GiB, at least 100 and at most 16,000."""
    return min(16000, max(100, 3 * size_gib))


def gp2_size_for(iops: int) -> int:
    return math.ceil(iops / 3)


def gp3_problems(size_gib: int, iops: int, throughput: int) -> list[str]:
    """gp3 limits: 3,000-16,000 IOPS at up to 500 per GiB, 125-1,000 MiB/s at up to 0.25 MiB/s per IOPS."""
    problems = []
    if not 3000 <= iops <= 16000:
        problems.append(f'{iops} IOPS is outside 3,000-16,000')
    if iops > 500 * size_gib:
        problems.append(f'{iops} IOPS is more than 500 per GiB for {size_gib} GiB')
    if not 125 <= throughput <= 1000:
        problems.append(f'{throughput} MiB/s is outside 125-1,000')
    if throughput > iops / 4:
        problems.append(f'{throughput} MiB/s is more than 0.25 MiB/s per IOPS')
    return problems


def part_count(etag: str) -> int:
    """A multipart upload's ETag ends in -<parts>; a single PUT's has no suffix."""
    tail = (etag or '').strip('"').rpartition('-')[2]
    return int(tail) if '-' in (etag or '') and tail.isdigit() else 1


def accelerate_name_ok(bucket: str) -> bool:
    """Transfer Acceleration needs a DNS-compatible bucket name without dots."""
    return '.' not in bucket and bucket == bucket.lower() and 3 <= len(bucket) <= 63


def proxy_role_policy(secret_arn: str) -> dict[str, Any]:
    return {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': 'secretsmanager:GetSecretValue', 'Resource': secret_arn}]}


# ── Commands shown in the console ───────────────────────────────────────────

BASELINE_COMMAND = (
    f'aws ec2 create-launch-template --launch-template-name {TEMPLATE} '
    f'--launch-template-data \'{{"ImageId":"{IMAGE_ID}","InstanceType":"{SMALL_TYPE}"}}\'\n'
    f'aws ec2 run-instances --launch-template LaunchTemplateName={TEMPLATE},Version=1 '
    f'--tag-specifications \'ResourceType=instance,Tags=[{{Key=Name,Value={SMALL_NAME}}},{{Key=App,Value={APP}}}]\'\n'
    f'aws cloudwatch put-metric-alarm --alarm-name {CPU_ALARM} --namespace {NAMESPACE} --metric-name {CPU_METRIC} '
    f'--dimensions Name=InstanceId,Value=<id> --statistic Maximum --period 60 --evaluation-periods 1 '
    f'--threshold {CPU_THRESHOLD} --comparison-operator GreaterThanThreshold\n'
    f'aws cloudwatch put-metric-alarm --alarm-name {OOM_ALARM} --namespace {NAMESPACE} --metric-name {OOM_METRIC} '
    '--dimensions Name=InstanceId,Value=<id> --statistic Maximum --period 60 --evaluation-periods 1 '
    '--threshold 1 --comparison-operator GreaterThanOrEqualToThreshold\n'
    'aws ssm send-command --document-name AWS-RunShellScript --instance-ids <id> '
    '--parameters commands=["cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max"]'
)
CPU_COMMAND = (
    f'aws ssm send-command --document-name AWS-RunShellScript --instance-ids <id> '
    f'--parameters commands=["<start {WORKERS} workers; sample CPU time and throttling for 10 s>"]\n'
    f'aws cloudwatch put-metric-data --namespace {NAMESPACE} --metric-data '
    f'"MetricName={CPU_METRIC},Value=<percent>,Dimensions=[{{Name=InstanceId,Value=<id>}}]" '
    f'"MetricName={THROTTLE_METRIC},Value=<periods>,Dimensions=[{{Name=InstanceId,Value=<id>}}]"\n'
    f'aws cloudwatch describe-alarms --alarm-names {CPU_ALARM}'
)
MEMORY_COMMAND = (
    f'aws ssm send-command --document-name AWS-RunShellScript --instance-ids <id> '
    f'--parameters commands=["<run the {REPORT_MIB} MiB report job; read oom_kill>"]\n'
    f'aws cloudwatch put-metric-data --namespace {NAMESPACE} --metric-data '
    f'"MetricName={OOM_METRIC},Value=1,Dimensions=[{{Name=InstanceId,Value=<id>}}]"\n'
    f'aws cloudwatch describe-alarms --alarm-names {OOM_ALARM}'
)
RIGHT_SIZE_COMMAND = (
    'aws ec2 describe-instance-types --query "InstanceTypes[].[InstanceType,VCpuInfo.DefaultVCpus,'
    'MemoryInfo.SizeInMiB,ProcessorInfo.SupportedArchitectures]"\n'
    f'aws ec2 create-launch-template-version --launch-template-name {TEMPLATE} --source-version 1 '
    '--launch-template-data \'{"InstanceType":"<recommended>"}\'\n'
    f'aws ec2 modify-launch-template --launch-template-name {TEMPLATE} --default-version 2\n'
    f'aws ec2 run-instances --launch-template LaunchTemplateName={TEMPLATE},Version=\'$Default\' '
    f'--tag-specifications \'ResourceType=instance,Tags=[{{Key=Name,Value={RIGHT_NAME}}},{{Key=App,Value={APP}}}]\'\n'
    'aws ec2 terminate-instances --instance-ids <small-id>\n'
    '# in place instead: stop-instances, modify-instance-attribute --instance-type, start-instances'
)
VERIFY_COMMAND = (
    f'aws cloudwatch put-metric-alarm --alarm-name {CPU_ALARM} ... --dimensions Name=InstanceId,Value=<right-id>\n'
    f'aws cloudwatch put-metric-alarm --alarm-name {OOM_ALARM} ... --dimensions Name=InstanceId,Value=<right-id>\n'
    'aws ssm send-command ... <the same workers and report job on the new instance>\n'
    f'aws cloudwatch put-metric-data --namespace {NAMESPACE} ...\n'
    f'aws cloudwatch describe-alarms --alarm-names {CPU_ALARM} {OOM_ALARM}'
)
VOLUME_COMMAND = (
    f'aws ec2 create-volume --availability-zone <az> --size {VOLUME_SIZE} --volume-type gp2\n'
    f'aws ec2 attach-volume --volume-id <vol> --instance-id <right-id> --device {VOLUME_DEVICE}\n'
    f'# gp2 at {VOLUME_SIZE} GiB earns {gp2_baseline_iops(VOLUME_SIZE)} IOPS; {NEEDED_IOPS} would need '
    f'{gp2_size_for(NEEDED_IOPS)} GiB\n'
    f'aws ec2 modify-volume --volume-id <vol> --volume-type gp3 --iops {NEEDED_IOPS} '
    f'--throughput {NEEDED_THROUGHPUT}\n'
    'aws ec2 describe-volumes-modifications --volume-ids <vol>'
)
RDS_COMMAND = (
    f'aws rds create-db-instance --db-instance-identifier {DB_ID} --engine postgres --db-instance-class {DB_CLASS} '
    f'--storage-type gp3 --allocated-storage {DB_STORAGE} --max-allocated-storage {DB_MAX_STORAGE} '
    f'--enable-performance-insights --performance-insights-retention-period {PI_RETENTION_DAYS} ...\n'
    f'aws rds modify-db-instance --db-instance-identifier {DB_ID} --db-instance-class {DB_TUNED_CLASS} '
    '--apply-immediately\n'
    f'aws secretsmanager create-secret --name {DB_SECRET} --secret-string file://credentials.json\n'
    f'aws rds create-db-proxy --db-proxy-name {PROXY} --engine-family POSTGRESQL '
    f'--auth AuthScheme=SECRETS,SecretArn=<secret>,IAMAuth=DISABLED --role-arn {PROXY_ROLE_ARN} '
    '--vpc-subnet-ids <subnets>\n'
    f'aws rds register-db-proxy-targets --db-proxy-name {PROXY} --db-instance-identifiers {DB_ID}\n'
    f'aws rds describe-db-proxy-targets --db-proxy-name {PROXY}'
)
S3_COMMAND = (
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    f'aws s3api put-bucket-accelerate-configuration --bucket {BUCKET} --accelerate-configuration Status=Enabled\n'
    f'aws configure set default.s3.multipart_threshold {PART_MIB}MB\n'
    f'aws configure set default.s3.multipart_chunksize {PART_MIB}MB\n'
    f'aws s3 cp daily-report.bin s3://{BUCKET}/{BIG_KEY}   # {BIG_MIB} MiB: multipart\n'
    f'aws s3 cp summary.bin s3://{BUCKET}/{SMALL_KEY}   # {SMALL_MIB} MiB: one PUT\n'
    f'aws s3api get-object --bucket {BUCKET} --key {BIG_KEY} --range bytes=0-{RANGE_BYTES - 1} part.bin\n'
    f'# accelerated endpoint: https://{BUCKET}.s3-accelerate.amazonaws.com'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Tune performance from metrics: right-size an instance, move a volume to gp3, and tune RDS and S3',
    'description': (
        'Measure an app instance whose workers saturate its vCPUs and whose report job runs out of memory, alarm on '
        'both, pick a right-sized type from the instance type catalog and launch it from a new launch template '
        'version, move a gp2 volume to gp3 with the IOPS it needs, give an RDS database storage autoscaling, '
        'Performance Insights, a larger class and an RDS Proxy, and speed up S3 transfers with multipart uploads, '
        'byte-range reads, and Transfer Acceleration.'
    ),
    'steps': [
        {
            'key': 'launch-baseline',
            'title': f'Launch the app on {SMALL_TYPE} from a launch template and alarm on CPU and memory',
            'command': BASELINE_COMMAND,
            'explanation': (
                f'The launch template {TEMPLATE} holds the app\'s image and instance type, so a later version can '
                f'change the type without touching anything else. {SMALL_NAME} starts on {SMALL_TYPE} (2 vCPUs, 1 GiB). '
                f'Two alarms watch it: {CPU_ALARM} when CPU passes {CPU_THRESHOLD}%, and {OOM_ALARM} when a process '
                'is killed for running out of memory. EC2 reports CPU by itself; memory needs the CloudWatch agent. '
                'The step also reads the CPU and memory limits the instance really has. '
                + FLOCI + 'the template, instance and alarms are real. Floci runs the instance as a container limited '
                'to its type\'s vCPUs and memory, and publishes no AWS/EC2 metrics, so the lab measures the instance '
                f'itself and publishes to the {NAMESPACE} namespace.'
            ),
        },
        {
            'key': 'cpu-saturation',
            'title': f'Run {WORKERS} workers and watch the CPU alarm fire',
            'command': CPU_COMMAND,
            'explanation': (
                f'The app runs {WORKERS} busy workers on 2 vCPUs. The step samples the instance\'s CPU time for 10 '
                'seconds: it is pinned at its limit, and the kernel throttles it many times, which is what a '
                f'CPUUtilization near 100% and slow requests look like. {CPU_ALARM} goes to ALARM. On a burstable '
                'type like t3 you would also watch CPUCreditBalance, since an instance out of credits drops to its '
                'baseline (unless it runs in unlimited mode). '
                + FLOCI + 'the CPU limit and the throttling are real; Floci does not emulate CPU credits.'
            ),
        },
        {
            'key': 'memory-pressure',
            'title': f'Run the {REPORT_MIB} MiB report job and catch the out-of-memory kill',
            'command': MEMORY_COMMAND,
            'explanation': (
                f'The nightly report holds {REPORT_MIB} MiB in memory, more than the instance\'s 1 GiB. The kernel '
                'kills it (exit 137) and counts an out-of-memory kill. Without the agent\'s mem_used_percent this '
                f'failure would be invisible to CloudWatch; with it, {OOM_ALARM} goes to ALARM. '
                + FLOCI + 'the memory limit and the kill are real.'
            ),
        },
        {
            'key': 'right-size',
            'title': 'Pick a right-sized type and replace the instance from a new template version',
            'command': RIGHT_SIZE_COMMAND,
            'explanation': (
                f'Keeping {WORKERS} workers under {TARGET_CPU}% needs {math.ceil(WORKERS / (TARGET_CPU / 100))} vCPUs, '
                f'and the report needs {math.ceil(REPORT_MIB * MEMORY_HEADROOM)} MiB with 25% headroom. The step reads '
                'the instance type catalog and picks the smallest x86_64 type that fits (an Arm type such as m6g '
                'would not boot this x86_64 image). Version 2 of the template uses that type and becomes the default, '
                f'{RIGHT_NAME} launches from it, and {SMALL_NAME} is terminated. AWS Compute Optimizer makes the same '
                'kind of recommendation from 14 days of metrics. You can also resize in place: stop an EBS-backed '
                'instance, change its type, and start it. '
                + FLOCI + 'the catalog, template versions and launch are real, and Floci refuses an image whose '
                'architecture the type does not support. Floci records an in-place type change but keeps the '
                'container\'s old CPU and memory limits, so the lab replaces the instance, and waits for the package '
                'installs Floci runs at boot to finish before measuring. Compute Optimizer is not emulated.'
            ),
        },
        {
            'key': 'verify-right-size',
            'title': 'Point the alarms at the new instance and rerun the workload',
            'command': VERIFY_COMMAND,
            'explanation': (
                f'The alarms are moved to {RIGHT_NAME} and the same workers and report run there. CPU stays near '
                f'{TARGET_CPU}% without throttling, the report finishes, and both alarms return to OK. Checking the '
                'result with the same measurement is what proves the change worked. '
                + FLOCI + 'the limits, the measurements and the alarms are real.'
            ),
        },
        {
            'key': 'gp3-volume',
            'title': f'Move a gp2 volume to gp3 with {NEEDED_IOPS:,} IOPS and {NEEDED_THROUGHPUT} MiB/s',
            'command': VOLUME_COMMAND,
            'explanation': (
                f'The app\'s data volume is gp2 at {VOLUME_SIZE} GiB, which earns {gp2_baseline_iops(VOLUME_SIZE)} '
                f'IOPS (3 per GiB) and bursts above that only while credits last. The app needs {NEEDED_IOPS:,} IOPS: '
                f'gp2 would need {gp2_size_for(NEEDED_IOPS):,} GiB, while gp3 sets IOPS and throughput apart from size '
                '(3,000 IOPS and 125 MiB/s included, up to 16,000 IOPS at 500 per GiB and 1,000 MiB/s). The step '
                f'checks the target against the gp3 limits, refuses {INVALID_IOPS:,} IOPS, and modifies the volume '
                'while attached. In AWS the volume goes through modifying and optimizing, and you wait six hours '
                'before the next change. '
                + FLOCI + 'the volume, the attachment and the modification record are real. Floci completes the '
                'modification at once, does not change disk speed, and accepts IOPS above the gp3 limits, so the lab '
                'checks them first.'
            ),
        },
        {
            'key': 'rds-tuning',
            'title': 'Tune the database: storage autoscaling, Performance Insights, a larger class, and RDS Proxy',
            'command': RDS_COMMAND,
            'explanation': (
                f'{DB_ID} runs PostgreSQL with {DB_STORAGE} GiB of gp3 storage that may grow to {DB_MAX_STORAGE} GiB '
                'by itself (storage autoscaling), and Performance Insights on for '
                f'{PI_RETENTION_DAYS} days to show which queries add database load. The class moves from {DB_CLASS} '
                f'to {DB_TUNED_CLASS} at once. {PROXY} pools connections, so many short Lambda or app connections '
                'share a few database connections and a failover keeps clients connected; it reads the credentials '
                f'from {DB_SECRET} through {PROXY_ROLE}. '
                + FLOCI + 'the database is a real PostgreSQL, the class change applies, and the proxy target reports '
                'AVAILABLE. Floci stores storage autoscaling and Performance Insights settings without acting on '
                'them, has no Performance Insights API, and publishes no AWS/RDS metrics.'
            ),
        },
        {
            'key': 's3-transfer',
            'title': 'Speed up S3 transfers with multipart uploads, ranged reads, and Transfer Acceleration',
            'command': S3_COMMAND,
            'explanation': (
                f'Above the {PART_MIB} MiB threshold the SDK splits an upload into parts sent in parallel, so the '
                f'{BIG_MIB} MiB report goes up in {math.ceil(BIG_MIB / PART_MIB)} parts (its ETag ends in -'
                f'{math.ceil(BIG_MIB / PART_MIB)}) and the {SMALL_MIB} MiB summary in one PUT. A ranged GET reads '
                f'only the first {RANGE_BYTES // MIB} MiB, which also lets clients download parts in parallel. '
                'Transfer Acceleration sends uploads from far away through the nearest edge location; the bucket '
                'name must have no dots. Multipart upload is recommended above 100 MB and required above 5 GB. '
                + FLOCI + 'the multipart upload, the ETag and the ranged read are real. Floci stores the '
                'acceleration setting but has no accelerated endpoint.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _marker(step: str, value: Any) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'step': step,
        'command': command,
        'exit_code': 0,
        'stdout': json.dumps(clean, indent=2, default=str),
        'stderr': '',
        'json': clean,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verified,
        'verification': {'status': 'passed' if verified else 'failed', 'message': message},
    }


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError, IndexError):
        return default


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Floci lookups ────────────────────────────────────────────────────────────

def _instances() -> dict[str, dict[str, Any]]:
    """Live lab instances by Name tag."""
    reservations = _safe_call(lambda: _client('ec2').describe_instances(Filters=[
        {'Name': 'tag:App', 'Values': [APP]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
    ])['Reservations'], [])
    found = {}
    for reservation in reservations:
        for instance in reservation.get('Instances', []):
            name = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Name'), None)
            if name in (SMALL_NAME, RIGHT_NAME):
                found[name] = instance
    return found


def _instance(name: str) -> dict[str, Any] | None:
    return _instances().get(name)


def _template() -> dict[str, Any] | None:
    templates = _safe_call(lambda: _client('ec2').describe_launch_templates(
        LaunchTemplateNames=[TEMPLATE])['LaunchTemplates'], [])
    return templates[0] if templates else None


def _template_versions() -> dict[int, dict[str, Any]]:
    versions = _safe_call(lambda: _client('ec2').describe_launch_template_versions(
        LaunchTemplateName=TEMPLATE)['LaunchTemplateVersions'], [])
    return {item['VersionNumber']: item for item in versions}


def _run_shell(instance_id: str, script: str) -> dict[str, Any]:
    """Run a script through Run Command and return the finished invocation."""
    ssm = _client('ssm')
    command_id = ssm.send_command(InstanceIds=[instance_id], DocumentName='AWS-RunShellScript',
                                  Parameters={'commands': [script]}, Comment=LAB_KEY)['Command']['CommandId']
    outcome: dict[str, Any] = {}

    def finished() -> bool:
        try:
            outcome.update(ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id))
        except ClientError as exc:
            if _code(exc) == 'InvocationDoesNotExist':
                return False
            raise
        return outcome.get('Status') not in ('Pending', 'InProgress', 'Delayed')

    if not _wait(finished, timeout=COMMAND_TIMEOUT, delay=1):
        outcome['Status'] = 'TimedOut'
    return {'InstanceId': instance_id, 'Status': outcome.get('Status'),
            'Stdout': (outcome.get('StandardOutputContent') or '').strip(),
            'Stderr': (outcome.get('StandardErrorContent') or '').strip()}


def _alarms() -> dict[str, dict[str, Any]]:
    response = _safe_call(lambda: _client('cloudwatch').describe_alarms(AlarmNames=[CPU_ALARM, OOM_ALARM]), {})
    return {alarm['AlarmName']: alarm for alarm in response.get('MetricAlarms') or []}


def _alarm_instance(alarm: dict[str, Any]) -> str | None:
    return next((item['Value'] for item in alarm.get('Dimensions') or [] if item['Name'] == 'InstanceId'), None)


def _wait_alarm(name: str, state: str, timeout: float = ALARM_WAIT_SECONDS) -> str | None:
    seen: dict[str, str] = {}

    def reached() -> bool:
        seen['state'] = (_alarms().get(name) or {}).get('StateValue')
        return seen['state'] == state

    _wait(reached, timeout, delay=3)
    return seen.get('state')


def _volume() -> dict[str, Any] | None:
    volumes = _safe_call(lambda: _client('ec2').describe_volumes(Filters=[
        {'Name': 'tag:Name', 'Values': [VOLUME_NAME]}])['Volumes'], [])
    live = [item for item in volumes if item.get('State') not in ('deleting', 'deleted')]
    return live[0] if live else None


def _db() -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('rds').describe_db_instances(DBInstanceIdentifier=DB_ID)['DBInstances'], [])
    return items[0] if items else None


def _proxy() -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('rds').describe_db_proxies(DBProxyName=PROXY)['DBProxies'], [])
    return items[0] if items else None


def _proxy_targets() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('rds').describe_db_proxy_targets(DBProxyName=PROXY)['Targets'], [])


def _secret_arn() -> str | None:
    return _safe_call(lambda: _client('secretsmanager').describe_secret(SecretId=DB_SECRET)['ARN'])


def _bucket_exists() -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=BUCKET) is not None, False)


def _limits(instance_id: str) -> dict[str, Any]:
    run = _run_shell(instance_id, LIMITS_SCRIPT)
    return parse_limits(run['Stdout']) if run['Status'] == 'Success' else {}


def _vcpus_of(instance: dict[str, Any], limits: dict[str, Any]) -> float:
    if limits.get('vcpus'):
        return limits['vcpus']
    types = _safe_call(lambda: _client('ec2').describe_instance_types(
        InstanceTypes=[instance['InstanceType']])['InstanceTypes'], [])
    return float(((types[0] if types else {}).get('VCpuInfo') or {}).get('DefaultVCpus') or 2)


def _publish(instance_id: str, values: dict[str, float]) -> None:
    now = datetime.now(timezone.utc)
    data = [{'MetricName': name, 'Value': float(value), 'Timestamp': now, 'Unit': unit,
             'Dimensions': [{'Name': 'InstanceId', 'Value': instance_id}]}
            for name, value, unit in (
                (CPU_METRIC, values.get('cpu'), 'Percent'), (THROTTLE_METRIC, values.get('throttled'), 'Count'),
                (MEM_METRIC, values.get('mem'), 'Percent'), (OOM_METRIC, values.get('oom_kills'), 'Count'))
            if value is not None]
    if data:
        _client('cloudwatch').put_metric_data(Namespace=NAMESPACE, MetricData=data)


def _put_alarms(instance_id: str) -> None:
    cloudwatch = _client('cloudwatch')
    dimensions = [{'Name': 'InstanceId', 'Value': instance_id}]
    cloudwatch.put_metric_alarm(
        AlarmName=CPU_ALARM, AlarmDescription='The app instance is out of CPU.', Namespace=NAMESPACE,
        MetricName=CPU_METRIC, Dimensions=dimensions, Statistic='Maximum', Period=60, EvaluationPeriods=1,
        Threshold=CPU_THRESHOLD, ComparisonOperator='GreaterThanThreshold', TreatMissingData='ignore')
    cloudwatch.put_metric_alarm(
        AlarmName=OOM_ALARM, AlarmDescription='A process on the app instance ran out of memory.', Namespace=NAMESPACE,
        MetricName=OOM_METRIC, Dimensions=dimensions, Statistic='Maximum', Period=60, EvaluationPeriods=1,
        Threshold=1, ComparisonOperator='GreaterThanOrEqualToThreshold', TreatMissingData='ignore')


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_baseline() -> tuple[bool, str]:
    template = _template()
    if not template:
        return False, f'The launch template {TEMPLATE} does not exist yet.'
    marker = _marked('launch-baseline') or {}
    small = _instance(SMALL_NAME)
    if not small and not _instance(RIGHT_NAME):
        return False, f'{SMALL_NAME} is not running.'
    if set(_alarms()) != {CPU_ALARM, OOM_ALARM}:
        return False, f'Create {CPU_ALARM} and {OOM_ALARM}.'
    limits = marker.get('limits') or {}
    if not limits.get('memory_mib'):
        return False, f'Read the CPU and memory limits of {SMALL_NAME}.'
    return True, f'{SMALL_NAME} runs on {SMALL_TYPE} with {limits.get("vcpus")} vCPUs and {limits["memory_mib"]} MiB.'


def _check_cpu() -> tuple[bool, str]:
    marker = _marked('cpu-saturation') or {}
    sample = marker.get('sample') or {}
    if (sample.get('cpu') or 0) < CPU_THRESHOLD:
        return False, f'CPU reached {sample.get("cpu", "no")}% under the workers; it should pass {CPU_THRESHOLD}%.'
    if marker.get('alarm') != 'ALARM':
        return False, f'{CPU_ALARM} is {marker.get("alarm") or "not evaluated"}, not ALARM.'
    return True, f'CPU was {sample["cpu"]}% and throttled {int(sample.get("throttled") or 0)} times; {CPU_ALARM} fired.'


def _check_memory() -> tuple[bool, str]:
    marker = _marked('memory-pressure') or {}
    outcome = marker.get('outcome') or {}
    if outcome.get('finished') or not outcome.get('oom_kills'):
        return False, f'The {REPORT_MIB} MiB report job should be killed for lack of memory: {outcome or "not run"}.'
    if marker.get('alarm') != 'ALARM':
        return False, f'{OOM_ALARM} is {marker.get("alarm") or "not evaluated"}, not ALARM.'
    return True, f'The report job was killed (exit {outcome.get("exit")}) and {OOM_ALARM} fired.'


def _check_right_size() -> tuple[bool, str]:
    marker = _marked('right-size') or {}
    choice = marker.get('recommendation') or {}
    if not choice:
        return False, 'No type in the catalog fits yet.'
    template = _template() or {}
    versions = _template_versions()
    data = (versions.get(2) or {}).get('LaunchTemplateData') or {}
    if data.get('InstanceType') != choice['type'] or template.get('DefaultVersionNumber') != 2:
        return False, f'Version 2 of {TEMPLATE} should use {choice["type"]} and be the default.'
    right = _instance(RIGHT_NAME)
    if not right or right.get('InstanceType') != choice['type']:
        return False, f'{RIGHT_NAME} is not running on {choice["type"]}.'
    if _instance(SMALL_NAME):
        return False, f'Terminate {SMALL_NAME}.'
    return True, f'{RIGHT_NAME} runs on {choice["type"]} ({choice["vcpus"]} vCPUs, {choice["memory_mib"]} MiB).'


def _check_verified() -> tuple[bool, str]:
    marker = _marked('verify-right-size') or {}
    right = _instance(RIGHT_NAME) or {}
    alarms = _alarms()
    if any(_alarm_instance(alarm) != right.get('InstanceId') for alarm in alarms.values()) or not alarms:
        return False, f'Point both alarms at {RIGHT_NAME}.'
    sample = marker.get('sample') or {}
    outcome = marker.get('outcome') or {}
    if not outcome.get('finished'):
        return False, f'The report job did not finish on {RIGHT_NAME}: {outcome or "not run"}.'
    if (sample.get('cpu') or 100) >= CPU_THRESHOLD:
        return False, f'CPU was {sample.get("cpu", "not measured")}% on {RIGHT_NAME}.'
    states = marker.get('states') or {}
    if states.get(CPU_ALARM) != 'OK' or states.get(OOM_ALARM) != 'OK':
        return False, f'The alarms are {states or "not evaluated"}; both should be OK.'
    return True, f'CPU {sample["cpu"]}%, the report finished, and both alarms are OK.'


def _check_volume() -> tuple[bool, str]:
    volume = _volume()
    if not volume:
        return False, f'The {VOLUME_NAME} volume does not exist yet.'
    if volume.get('VolumeType') != 'gp3' or volume.get('Iops') != NEEDED_IOPS \
            or volume.get('Throughput') != NEEDED_THROUGHPUT:
        return False, f'{VOLUME_NAME} is {volume.get("VolumeType")} with {volume.get("Iops")} IOPS.'
    right = _instance(RIGHT_NAME) or {}
    if not any(item.get('InstanceId') == right.get('InstanceId') for item in volume.get('Attachments') or []):
        return False, f'{VOLUME_NAME} is not attached to {RIGHT_NAME}.'
    if not (_marked('gp3-volume') or {}).get('refused'):
        return False, f'Check {INVALID_IOPS:,} IOPS against the gp3 limits.'
    return True, f'{VOLUME_NAME} is gp3 with {NEEDED_IOPS:,} IOPS and {NEEDED_THROUGHPUT} MiB/s, still attached.'


def _check_rds() -> tuple[bool, str]:
    db = _db()
    if not db:
        return False, f'{DB_ID} does not exist yet.'
    if db.get('MaxAllocatedStorage') != DB_MAX_STORAGE:
        return False, f'{DB_ID} has no storage autoscaling up to {DB_MAX_STORAGE} GiB.'
    if not db.get('PerformanceInsightsEnabled'):
        return False, f'Turn on Performance Insights for {DB_ID}.'
    if db.get('DBInstanceClass') != DB_TUNED_CLASS:
        return False, f'{DB_ID} is still {db.get("DBInstanceClass")}.'
    targets = _proxy_targets()
    if not any((item.get('TargetHealth') or {}).get('State') == 'AVAILABLE' and item.get('RdsResourceId') == DB_ID
               for item in targets):
        return False, f'{PROXY} has no available target for {DB_ID}.'
    return True, f'{DB_ID} is {DB_TUNED_CLASS} with autoscaling, Performance Insights, and {PROXY}.'


def _check_s3() -> tuple[bool, str]:
    marker = _marked('s3-transfer') or {}
    if marker.get('accelerate') != 'Enabled':
        return False, f'Transfer Acceleration is not on for {BUCKET}.'
    if marker.get('bigParts') != math.ceil(BIG_MIB / PART_MIB) or marker.get('smallParts') != 1:
        return False, f'Expected {math.ceil(BIG_MIB / PART_MIB)} parts and 1 part: {marker or "not uploaded"}.'
    if marker.get('rangeBytes') != RANGE_BYTES:
        return False, 'The ranged read did not return the first MiB.'
    return True, f'{BIG_KEY} went up in {marker["bigParts"]} parts, and a ranged read returned 1 MiB.'


CHECKS = {
    'launch-baseline': _check_baseline,
    'cpu-saturation': _check_cpu,
    'memory-pressure': _check_memory,
    'right-size': _check_right_size,
    'verify-right-size': _check_verified,
    'gp3-volume': _check_volume,
    'rds-tuning': _check_rds,
    's3-transfer': _check_s3,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _tags(name: str) -> list[dict[str, Any]]:
    return [{'ResourceType': 'instance', 'Tags': [{'Key': 'Name', 'Value': name}, {'Key': 'App', 'Value': APP}]}]


def _launch(name: str, version: str) -> str:
    instance = _instance(name)
    if instance:
        return instance['InstanceId']
    response = _client('ec2').run_instances(
        LaunchTemplate={'LaunchTemplateName': TEMPLATE, 'Version': version}, MinCount=1, MaxCount=1,
        TagSpecifications=_tags(name))
    return response['Instances'][0]['InstanceId']


def _ready(instance_id: str) -> bool:
    """Running and answering Run Command."""
    def running() -> bool:
        reservations = _safe_call(lambda: _client('ec2').describe_instances(InstanceIds=[instance_id])
                                  ['Reservations'], [])
        states = [item['State']['Name'] for reservation in reservations for item in reservation['Instances']]
        return states == ['running']

    if not _wait(running, BOOT_TIMEOUT, delay=3):
        return False
    # Boot installs packages for a while; measuring CPU during it would count that work too.
    return _wait(lambda: _run_shell(instance_id, SETTLED_SCRIPT)['Stdout'] == 'busy=0', BOOT_TIMEOUT, delay=5)


def _need(name: str, step: str, command: str, started: float) -> tuple[dict | None, dict | None]:
    instance = _instance(name)
    if instance and instance['State']['Name'] == 'running':
        return instance, None
    previous = 'launch-baseline' if name == SMALL_NAME else 'right-size'
    return None, _result(step, command, {}, False, f'{name} is not running; run {previous} first.', started)


def step_launch_baseline() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    if not _template():
        ec2.create_launch_template(LaunchTemplateName=TEMPLATE, VersionDescription='app on the original size',
                                   LaunchTemplateData={'ImageId': IMAGE_ID, 'InstanceType': SMALL_TYPE})
    if _instance(RIGHT_NAME) and not _instance(SMALL_NAME):
        verified, message = _check_baseline()
        return _result('launch-baseline', BASELINE_COMMAND, {}, verified, message, started)
    instance_id = _launch(SMALL_NAME, '1')
    if not _ready(instance_id):
        return _result('launch-baseline', BASELINE_COMMAND, {'InstanceId': instance_id}, False,
                       f'{SMALL_NAME} did not answer Run Command in time.', started)
    _put_alarms(instance_id)
    limits = _limits(instance_id)
    _marker('launch-baseline', {'instanceId': instance_id, 'limits': limits})
    verified, message = _check_baseline()
    return _result('launch-baseline', BASELINE_COMMAND, {'InstanceId': instance_id, 'InstanceType': SMALL_TYPE,
                                                         'Limits': limits, 'Alarms': [CPU_ALARM, OOM_ALARM]},
                   verified, message, started)


def _sample_cpu(instance: dict[str, Any], limits: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    run = _run_shell(instance['InstanceId'], WORKLOAD_SCRIPT)
    return cpu_percent(run['Stdout'], _vcpus_of(instance, limits)), run


def step_cpu_saturation() -> dict[str, Any]:
    started = time.perf_counter()
    instance, error = _need(SMALL_NAME, 'cpu-saturation', CPU_COMMAND, started)
    if error:
        return error
    limits = (_marked('launch-baseline') or {}).get('limits') or _limits(instance['InstanceId'])
    sample, run = _sample_cpu(instance, limits)
    if sample:
        _publish(instance['InstanceId'], sample)
    alarm = _wait_alarm(CPU_ALARM, 'ALARM') if sample and sample['cpu'] > CPU_THRESHOLD else None
    _marker('cpu-saturation', {'sample': sample, 'alarm': alarm})
    verified, message = _check_cpu()
    return _result('cpu-saturation', CPU_COMMAND, {'Sample': sample, 'Alarm': alarm, 'Run': run},
                   verified, message, started)


def step_memory_pressure() -> dict[str, Any]:
    started = time.perf_counter()
    instance, error = _need(SMALL_NAME, 'memory-pressure', MEMORY_COMMAND, started)
    if error:
        return error
    run = _run_shell(instance['InstanceId'], REPORT_SCRIPT)
    outcome = report_outcome(run['Stdout'])
    _publish(instance['InstanceId'], {'oom_kills': outcome['oom_kills'],
                                      'mem': 100.0 if outcome['oom_kills'] else None})
    alarm = _wait_alarm(OOM_ALARM, 'ALARM') if outcome['oom_kills'] else None
    _marker('memory-pressure', {'outcome': outcome, 'alarm': alarm})
    verified, message = _check_memory()
    return _result('memory-pressure', MEMORY_COMMAND, {'Outcome': outcome, 'Alarm': alarm, 'Run': run},
                   verified, message, started)


def step_right_size() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    if not _template():
        return _result('right-size', RIGHT_SIZE_COMMAND, {}, False, 'Run launch-baseline first.', started)
    catalog = ec2.describe_instance_types()['InstanceTypes']
    choice = recommend(catalog)
    if not choice:
        return _result('right-size', RIGHT_SIZE_COMMAND, {'Catalog': len(catalog)}, False,
                       'No x86_64 type in the catalog fits the workers and the report.', started)
    versions = _template_versions()
    if 2 not in versions:
        ec2.create_launch_template_version(
            LaunchTemplateName=TEMPLATE, SourceVersion='1', VersionDescription=f'right-sized to {choice["type"]}',
            LaunchTemplateData={'InstanceType': choice['type']})
    ec2.modify_launch_template(LaunchTemplateName=TEMPLATE, DefaultVersion='2')
    instance_id = _launch(RIGHT_NAME, '$Default')
    ready = _ready(instance_id)
    limits = _limits(instance_id) if ready else {}
    small = _instance(SMALL_NAME)
    if small and ready:
        ec2.terminate_instances(InstanceIds=[small['InstanceId']])
        _wait(lambda: _instance(SMALL_NAME) is None, 60, delay=3)
    _marker('right-size', {'recommendation': choice, 'instanceId': instance_id, 'limits': limits})
    verified, message = _check_right_size()
    if not ready:
        verified, message = False, f'{RIGHT_NAME} did not answer Run Command in time.'
    return _result('right-size', RIGHT_SIZE_COMMAND, {'Recommendation': choice, 'InstanceId': instance_id,
                                                      'Limits': limits, 'CatalogTypes': len(catalog)},
                   verified, message, started)


def step_verify_right_size() -> dict[str, Any]:
    started = time.perf_counter()
    instance, error = _need(RIGHT_NAME, 'verify-right-size', VERIFY_COMMAND, started)
    if error:
        return error
    instance_id = instance['InstanceId']
    _put_alarms(instance_id)
    limits = (_marked('right-size') or {}).get('limits') or _limits(instance_id)
    sample, _ = _sample_cpu(instance, limits)
    outcome = report_outcome(_run_shell(instance_id, REPORT_SCRIPT)['Stdout'])
    memory = limits.get('memory_mib')
    _publish(instance_id, {**(sample or {}), 'oom_kills': outcome['oom_kills'],
                           'mem': round(REPORT_MIB / memory * 100, 1) if memory and outcome['finished'] else None})
    states = {CPU_ALARM: _wait_alarm(CPU_ALARM, 'OK'), OOM_ALARM: _wait_alarm(OOM_ALARM, 'OK')}
    _marker('verify-right-size', {'sample': sample, 'outcome': outcome, 'states': states})
    verified, message = _check_verified()
    return _result('verify-right-size', VERIFY_COMMAND, {'Sample': sample, 'Outcome': outcome, 'Alarms': states},
                   verified, message, started)


def step_gp3_volume() -> dict[str, Any]:
    started = time.perf_counter()
    instance, error = _need(RIGHT_NAME, 'gp3-volume', VOLUME_COMMAND, started)
    if error:
        return error
    ec2 = _client('ec2')
    volume = _volume()
    if not volume:
        volume = ec2.create_volume(
            AvailabilityZone=instance['Placement']['AvailabilityZone'], Size=VOLUME_SIZE, VolumeType='gp2',
            TagSpecifications=[{'ResourceType': 'volume', 'Tags': [{'Key': 'Name', 'Value': VOLUME_NAME},
                                                                  {'Key': 'App', 'Value': APP}]}])
        _wait(lambda: (_volume() or {}).get('State') == 'available', 30)
        volume = _volume() or volume
    if not any(item.get('InstanceId') == instance['InstanceId'] for item in volume.get('Attachments') or []):
        ec2.attach_volume(VolumeId=volume['VolumeId'], InstanceId=instance['InstanceId'], Device=VOLUME_DEVICE)
        _wait(lambda: any(item.get('State') == 'attached' for item in (_volume() or {}).get('Attachments') or []), 30)
    refused = gp3_problems(VOLUME_SIZE, INVALID_IOPS, NEEDED_THROUGHPUT)
    problems = gp3_problems(VOLUME_SIZE, NEEDED_IOPS, NEEDED_THROUGHPUT)
    modification = {}
    if not problems and (_volume() or {}).get('VolumeType') != 'gp3':
        modification = ec2.modify_volume(VolumeId=volume['VolumeId'], VolumeType='gp3', Iops=NEEDED_IOPS,
                                         Throughput=NEEDED_THROUGHPUT)['VolumeModification']
    modifications = _safe_call(lambda: ec2.describe_volumes_modifications(VolumeIds=[volume['VolumeId']])
                               ['VolumesModifications'], [])
    _marker('gp3-volume', {'refused': refused, 'problems': problems})
    verified, message = _check_volume()
    return _result('gp3-volume', VOLUME_COMMAND, {
        'gp2BaselineIops': gp2_baseline_iops(VOLUME_SIZE), 'gp2SizeForNeededIops': gp2_size_for(NEEDED_IOPS),
        'RefusedTarget': {'Iops': INVALID_IOPS, 'Problems': refused}, 'Modification': modification,
        'Modifications': modifications}, verified, message, started)


def _ensure_proxy_role(secret_arn: str) -> None:
    iam = _client('iam')
    trust = {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Principal': {'Service': 'rds.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
    try:
        iam.get_role(RoleName=PROXY_ROLE)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=PROXY_ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                        Description='RDS Proxy reads the database credentials')
    iam.put_role_policy(RoleName=PROXY_ROLE, PolicyName=INLINE_POLICY,
                        PolicyDocument=json.dumps(proxy_role_policy(secret_arn)))


def step_rds_tuning() -> dict[str, Any]:
    started = time.perf_counter()
    rds = _client('rds')
    if not _db():
        rds.create_db_instance(
            DBInstanceIdentifier=DB_ID, Engine='postgres', DBInstanceClass=DB_CLASS, StorageType='gp3',
            AllocatedStorage=DB_STORAGE, MaxAllocatedStorage=DB_MAX_STORAGE, MasterUsername=DB_USER,
            MasterUserPassword=DB_PASSWORD, EnablePerformanceInsights=True,
            PerformanceInsightsRetentionPeriod=PI_RETENTION_DAYS, Tags=[{'Key': 'App', 'Value': APP}])
    if not _wait(lambda: (_db() or {}).get('DBInstanceStatus') == 'available', DB_WAIT_SECONDS, delay=5):
        return _result('rds-tuning', RDS_COMMAND, {'DBInstance': _db()}, False, f'{DB_ID} is not available yet.',
                       started)
    if (_db() or {}).get('DBInstanceClass') != DB_TUNED_CLASS:
        rds.modify_db_instance(DBInstanceIdentifier=DB_ID, DBInstanceClass=DB_TUNED_CLASS, ApplyImmediately=True)
        _wait(lambda: (_db() or {}).get('DBInstanceClass') == DB_TUNED_CLASS
              and (_db() or {}).get('DBInstanceStatus') == 'available', DB_WAIT_SECONDS, delay=5)
    secret_arn = _secret_arn()
    if not secret_arn:
        secret_arn = _client('secretsmanager').create_secret(
            Name=DB_SECRET, SecretString=json.dumps({'username': DB_USER, 'password': DB_PASSWORD}))['ARN']
    _ensure_proxy_role(secret_arn)
    if not _proxy():
        subnets = [item['SubnetId'] for item in _client('ec2').describe_subnets(
            Filters=[{'Name': 'default-for-az', 'Values': ['true']}]).get('Subnets', [])][:2]
        rds.create_db_proxy(DBProxyName=PROXY, EngineFamily='POSTGRESQL', RoleArn=PROXY_ROLE_ARN,
                            Auth=[{'AuthScheme': 'SECRETS', 'SecretArn': secret_arn, 'IAMAuth': 'DISABLED'}],
                            VpcSubnetIds=subnets, RequireTLS=False, IdleClientTimeout=1800)
        _wait(lambda: (_proxy() or {}).get('Status') == 'available', 60)
    if not any(item.get('RdsResourceId') == DB_ID for item in _proxy_targets()):
        rds.register_db_proxy_targets(DBProxyName=PROXY, DBInstanceIdentifiers=[DB_ID])
    _wait(lambda: any((item.get('TargetHealth') or {}).get('State') == 'AVAILABLE' for item in _proxy_targets()), 60)
    db = _db() or {}
    verified, message = _check_rds()
    return _result('rds-tuning', RDS_COMMAND, {
        'DBInstance': {key: db.get(key) for key in ('DBInstanceClass', 'StorageType', 'AllocatedStorage',
                                                    'MaxAllocatedStorage', 'PerformanceInsightsEnabled',
                                                    'PerformanceInsightsRetentionPeriod', 'DBInstanceStatus')},
        'Proxy': {key: (_proxy() or {}).get(key) for key in ('DBProxyName', 'Status', 'Endpoint', 'EngineFamily')},
        'Targets': _proxy_targets()}, verified, message, started)


def step_s3_transfer() -> dict[str, Any]:
    started = time.perf_counter()
    from boto3.s3.transfer import TransferConfig

    s3 = _client('s3')
    if not _bucket_exists():
        s3.create_bucket(Bucket=BUCKET)
    if accelerate_name_ok(BUCKET):
        s3.put_bucket_accelerate_configuration(Bucket=BUCKET, AccelerateConfiguration={'Status': 'Enabled'})
    accelerate = _safe_call(lambda: s3.get_bucket_accelerate_configuration(Bucket=BUCKET).get('Status'))
    config = TransferConfig(multipart_threshold=PART_MIB * MIB, multipart_chunksize=PART_MIB * MIB, max_concurrency=4)
    s3.upload_fileobj(io.BytesIO(b'r' * (BIG_MIB * MIB)), BUCKET, BIG_KEY, Config=config)
    s3.upload_fileobj(io.BytesIO(b's' * (SMALL_MIB * MIB)), BUCKET, SMALL_KEY, Config=config)
    big = s3.head_object(Bucket=BUCKET, Key=BIG_KEY)
    small = s3.head_object(Bucket=BUCKET, Key=SMALL_KEY)
    ranged = s3.get_object(Bucket=BUCKET, Key=BIG_KEY, Range=f'bytes=0-{RANGE_BYTES - 1}')
    range_bytes = len(ranged['Body'].read())
    marker = {'accelerate': accelerate, 'bigParts': part_count(big['ETag']), 'smallParts': part_count(small['ETag']),
              'rangeBytes': range_bytes, 'contentRange': ranged.get('ContentRange')}
    _marker('s3-transfer', marker)
    verified, message = _check_s3()
    return _result('s3-transfer', S3_COMMAND, {**marker, 'BigETag': big['ETag'], 'SmallETag': small['ETag'],
                                               'AcceleratedEndpoint': f'https://{BUCKET}.s3-accelerate.amazonaws.com'},
                   verified, message, started)


RUNNERS = {
    'launch-baseline': step_launch_baseline,
    'cpu-saturation': step_cpu_saturation,
    'memory-pressure': step_memory_pressure,
    'right-size': step_right_size,
    'verify-right-size': step_verify_right_size,
    'gp3-volume': step_gp3_volume,
    'rds-tuning': step_rds_tuning,
    's3-transfer': step_s3_transfer,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check()[0])
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError, StopIteration):
        return False


def status() -> dict[str, Any]:
    steps = {}
    for step in STEP_KEYS:
        verified = _safe(CHECKS[step])
        steps[step] = {
            'verified': verified,
            'verification': {'status': 'passed', 'message': 'Verified against Floci.'} if verified else None,
        }
    passed = sum(1 for item in steps.values() if item['verified'])
    complete = passed == len(steps)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def reset() -> dict[str, Any]:
    """Remove the bucket, proxy, database, secret, role, volume, instances, template, and alarms."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code in ('ResourceNotFoundException',
                                                                  'InvalidLaunchTemplateName.NotFoundException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    s3 = _client('s3')
    if _bucket_exists():
        def empty_and_delete() -> None:
            for item in s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
                s3.delete_object(Bucket=BUCKET, Key=item['Key'])
            s3.delete_bucket(Bucket=BUCKET)
        attempt(f'bucket:{BUCKET}', empty_and_delete)
    rds = _client('rds')
    if _proxy():
        attempt(f'db-proxy:{PROXY}', lambda: rds.delete_db_proxy(DBProxyName=PROXY))
    if _db():
        attempt(f'db:{DB_ID}', lambda: rds.delete_db_instance(DBInstanceIdentifier=DB_ID, SkipFinalSnapshot=True,
                                                              DeleteAutomatedBackups=True))
    if _secret_arn():
        attempt(f'secret:{DB_SECRET}', lambda: _client('secretsmanager').delete_secret(
            SecretId=DB_SECRET, ForceDeleteWithoutRecovery=True))
    iam = _client('iam')
    if _safe_call(lambda: iam.get_role(RoleName=PROXY_ROLE)):
        def delete_role() -> None:
            for name in iam.list_role_policies(RoleName=PROXY_ROLE).get('PolicyNames', []):
                iam.delete_role_policy(RoleName=PROXY_ROLE, PolicyName=name)
            iam.delete_role(RoleName=PROXY_ROLE)
        attempt(f'role:{PROXY_ROLE}', delete_role)
    ec2 = _client('ec2')
    volume = _volume()
    if volume:
        def delete_volume() -> None:
            if volume.get('Attachments'):
                ec2.detach_volume(VolumeId=volume['VolumeId'], Force=True)
                _wait(lambda: not (_volume() or {}).get('Attachments'), 30)
            ec2.delete_volume(VolumeId=volume['VolumeId'])
        attempt(f'volume:{VOLUME_NAME}', delete_volume)
    instances = [item['InstanceId'] for item in _instances().values()]
    if instances:
        attempt('instances:' + ','.join(instances), lambda: ec2.terminate_instances(InstanceIds=instances))
    if _template():
        attempt(f'launch-template:{TEMPLATE}', lambda: ec2.delete_launch_template(LaunchTemplateName=TEMPLATE))
    alarms = list(_alarms())
    if alarms:
        attempt('alarms:' + ','.join(alarms), lambda: _client('cloudwatch').delete_alarms(AlarmNames=alarms))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws s3 rb s3://{BUCKET} --force\n'
            f'aws rds delete-db-proxy --db-proxy-name {PROXY}\n'
            f'aws rds delete-db-instance --db-instance-identifier {DB_ID} --skip-final-snapshot\n'
            f'aws secretsmanager delete-secret --secret-id {DB_SECRET} --force-delete-without-recovery\n'
            f'aws iam delete-role-policy --role-name {PROXY_ROLE} --policy-name {INLINE_POLICY}\n'
            f'aws iam delete-role --role-name {PROXY_ROLE}\n'
            'aws ec2 detach-volume --volume-id <vol> && aws ec2 delete-volume --volume-id <vol>\n'
            'aws ec2 terminate-instances --instance-ids <ids>\n'
            f'aws ec2 delete-launch-template --launch-template-name {TEMPLATE}\n'
            f'aws cloudwatch delete-alarms --alarm-names {CPU_ALARM} {OOM_ALARM}'
        ),
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'errors': errors,
        'verification': {
            'status': 'failed' if errors else 'passed',
            'message': 'Some lab resources could not be removed: ' + '; '.join(errors) if errors
            else 'The bucket, proxy, database, secret, role, volume, instances, template, and alarms are gone.',
        },
    }
