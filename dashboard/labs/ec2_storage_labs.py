"""Block and file storage lab: two instances, a gp3 volume sized within its limits, an io2 volume priced against gp3,
formatting and mounting through SSM, growing the volume in place, moving it to another instance, and an EFS file
system with mount targets in two AZs, lifecycle tiering, a TLS-only policy, and an access point.

Floci 2.3.0 backs an attached EBS volume with a real loop device inside the instance container, so the filesystem,
the data, the resize, and the move are real; it refuses attaching across AZs, shrinking a volume, and deleting an
attached one. It does not check IOPS or throughput limits, and CreateSnapshot is unsupported. EFS is configuration
only: there is no NFS server, and every mount target reports the same AZ and IP address, so the lab checks the EFS
settings itself.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'ec2'
LAB_KEY = 'block-file-storage'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
AZ_A, AZ_B = 'us-east-1a', 'us-east-1b'

TAG = {'Key': 'Lab', 'Value': LAB_KEY}
IMAGE_ID = 'ami-ubuntu2204'
INSTANCE_TYPE = 't3.micro'
APP_1, APP_2 = 'lab-storage-app-1', 'lab-storage-app-2'
APP_SG = 'lab-storage-app'
EFS_SG = 'lab-storage-efs'
NFS_PORT = 2049

GP3_NAME = 'lab-storage-gp3'
GP3_SIZE, GP3_IOPS, GP3_THROUGHPUT = 20, 3000, 125
GROWN_SIZE, GROWN_IOPS = 40, 6000
IO2_NAME = 'lab-storage-io2'
IO2_SIZE, IO2_IOPS = 100, 16000
DEVICE_1, DEVICE_2 = '/dev/sdf', '/dev/sdg'
MOUNT_POINT = '/data'
ORDERS_LINE = 'order-1001,59.90'

# us-east-1 list prices per month.
GP3_GB, GP3_IOPS_PRICE, GP3_MBPS_PRICE = 0.08, 0.005, 0.04
IO2_GB, IO2_IOPS_PRICE = 0.125, 0.065

EFS_TOKEN = 'lab-storage-shared'
EFS_NAME = 'lab-storage-shared'
LIFECYCLE = [{'TransitionToIA': 'AFTER_30_DAYS'}, {'TransitionToArchive': 'AFTER_90_DAYS'},
             {'TransitionToPrimaryStorageClass': 'AFTER_1_ACCESS'}]
ACCESS_POINT_PATH = '/orders'
POSIX_ID = 1000

SSM_TIMEOUT_SECONDS = 90

FORMAT_SCRIPT = (
    'set -e\n'
    f'DEV={DEVICE_1}\n'
    'blkid "$DEV" >/dev/null 2>&1 || mkfs.ext4 -q -L orders "$DEV"\n'
    f'mkdir -p {MOUNT_POINT}\n'
    f'mountpoint -q {MOUNT_POINT} || mount "$DEV" {MOUNT_POINT}\n'
    f'echo "{ORDERS_LINE}" > {MOUNT_POINT}/orders.csv\n'
    'sync\n'
    f'cat {MOUNT_POINT}/orders.csv\n'
    f'echo "size_gb=$(df -BG --output=size {MOUNT_POINT} | tail -1 | tr -dc 0-9)"'
)
GROW_SCRIPT = (
    'set -e\n'
    f'DEV={DEVICE_1}\n'
    f'mountpoint -q {MOUNT_POINT} && umount {MOUNT_POINT}\n'
    'e2fsck -fy "$DEV" >/dev/null 2>&1 || true\n'
    'resize2fs "$DEV"\n'
    f'mount "$DEV" {MOUNT_POINT}\n'
    f'cat {MOUNT_POINT}/orders.csv\n'
    f'echo "size_gb=$(df -BG --output=size {MOUNT_POINT} | tail -1 | tr -dc 0-9)"'
)
UNMOUNT_SCRIPT = f'mountpoint -q {MOUNT_POINT} && umount {MOUNT_POINT}; echo unmounted'
MOVED_SCRIPT = (
    'set -e\n'
    f'mkdir -p {MOUNT_POINT}\n'
    f'mountpoint -q {MOUNT_POINT} || mount {DEVICE_2} {MOUNT_POINT}\n'
    f'cat {MOUNT_POINT}/orders.csv\n'
    f'echo "size_gb=$(df -BG --output=size {MOUNT_POINT} | tail -1 | tr -dc 0-9)"'
)

INSTANCES_COMMAND = (
    f'aws ec2 create-security-group --group-name {APP_SG} --description "Storage lab app servers"\n'
    f'aws ec2 run-instances --image-id {IMAGE_ID} --instance-type {INSTANCE_TYPE} --count 1 '
    f'--subnet-id <default subnet in {AZ_A}> --security-group-ids <app-sg> '
    f'--tag-specifications \'ResourceType=instance,Tags=[{{Key=Name,Value={APP_1}}}]\'\n'
    f'# the same for {APP_2}, also in {AZ_A}'
)
GP3_COMMAND = (
    f'aws ec2 create-volume --availability-zone {AZ_A} --volume-type gp3 --size {GP3_SIZE} --iops {GP3_IOPS} '
    f'--throughput {GP3_THROUGHPUT} --encrypted '
    f'--tag-specifications \'ResourceType=volume,Tags=[{{Key=Name,Value={GP3_NAME}}}]\''
)
IO2_COMMAND = (
    f'aws ec2 create-volume --availability-zone {AZ_A} --volume-type io2 --size {IO2_SIZE} --iops {IO2_IOPS} '
    f'--encrypted --tag-specifications \'ResourceType=volume,Tags=[{{Key=Name,Value={IO2_NAME}}}]\''
)
ATTACH_COMMAND = (
    f'aws ec2 attach-volume --volume-id <gp3-volume-id> --instance-id <{APP_1}-id> --device {DEVICE_1}\n'
    f'aws ssm send-command --instance-ids <{APP_1}-id> --document-name AWS-RunShellScript '
    '--parameters commands=["sudo mkfs.ext4 /dev/nvme1n1","sudo mount /dev/nvme1n1 /data", ...]'
)
GROW_COMMAND = (
    f'aws ec2 modify-volume --volume-id <gp3-volume-id> --size {GROWN_SIZE} --iops {GROWN_IOPS}\n'
    'aws ec2 describe-volumes-modifications --volume-ids <gp3-volume-id>   # optimizing, then completed\n'
    f'aws ssm send-command ... commands=["sudo resize2fs /dev/nvme1n1","df -h {MOUNT_POINT}"]'
)
MOVE_COMMAND = (
    f'aws ssm send-command --instance-ids <{APP_1}-id> ... commands=["sudo umount {MOUNT_POINT}"]\n'
    'aws ec2 detach-volume --volume-id <gp3-volume-id>\n'
    f'aws ec2 attach-volume --volume-id <gp3-volume-id> --instance-id <{APP_2}-id> --device {DEVICE_2}\n'
    f'aws ssm send-command --instance-ids <{APP_2}-id> ... commands=["sudo mount /dev/nvme1n1 {MOUNT_POINT}",'
    f'"cat {MOUNT_POINT}/orders.csv"]\n'
    'aws ec2 create-snapshot --volume-id <gp3-volume-id>   # to copy it to another AZ or Region'
)
EFS_COMMAND = (
    f'aws ec2 create-security-group --group-name {EFS_SG} --description "NFS from app servers"\n'
    f'aws ec2 authorize-security-group-ingress --group-id <efs-sg> --protocol tcp --port {NFS_PORT} '
    '--source-group <app-sg>\n'
    f'aws efs create-file-system --creation-token {EFS_TOKEN} --performance-mode generalPurpose '
    f'--throughput-mode elastic --encrypted --tags Key=Name,Value={EFS_NAME}\n'
    f'aws efs create-mount-target --file-system-id <fs-id> --subnet-id <subnet in {AZ_A}> --security-groups <efs-sg>\n'
    f'aws efs create-mount-target --file-system-id <fs-id> --subnet-id <subnet in {AZ_B}> --security-groups <efs-sg>'
)
LIFECYCLE_COMMAND = (
    'aws efs put-lifecycle-configuration --file-system-id <fs-id> --lifecycle-policies '
    'TransitionToIA=AFTER_30_DAYS TransitionToArchive=AFTER_90_DAYS TransitionToPrimaryStorageClass=AFTER_1_ACCESS\n'
    'aws efs put-backup-policy --file-system-id <fs-id> --backup-policy Status=ENABLED\n'
    'aws efs put-file-system-policy --file-system-id <fs-id> --policy file://tls-only.json\n'
    f'aws efs create-access-point --file-system-id <fs-id> --posix-user Uid={POSIX_ID},Gid={POSIX_ID} '
    f'--root-directory \'Path={ACCESS_POINT_PATH},CreationInfo={{OwnerUid={POSIX_ID},OwnerGid={POSIX_ID},'
    'Permissions=750}\''
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Choose and use block and file storage with EBS gp3, io2, and EFS',
    'description': (
        'Size a gp3 volume within its IOPS and throughput limits, price an io2 volume against it, format and mount '
        'the gp3 volume on an instance, grow it without detaching, move it with its data to another instance, then '
        'build a shared EFS file system with mount targets in two AZs, lifecycle tiering, a TLS-only policy, and an '
        'access point.'
    ),
    'steps': [
        {
            'key': 'launch-instances',
            'title': f'Launch two app servers in {AZ_A}',
            'command': INSTANCES_COMMAND,
            'explanation': (
                'An EBS volume lives in one Availability Zone and attaches only to instances in that AZ, so both app '
                'servers start in the same AZ. Instance store, by contrast, is disk on the host that is lost when the '
                'instance stops. How Floci emulates this: each instance is a real container, and SSM Run Command runs '
                'shell commands in it.'
            ),
        },
        {
            'key': 'gp3-volume',
            'title': 'Create an encrypted gp3 volume within its limits',
            'command': GP3_COMMAND,
            'explanation': (
                'gp3 is the default SSD volume: a 3,000 IOPS and 125 MiB/s baseline at any size, with IOPS (up to '
                '16,000, at most 500 per GiB) and throughput (up to 1,000 MiB/s, at most 0.25 MiB/s per IOPS) bought '
                'separately from capacity. gp2 ties IOPS to size (3 per GiB), so gp3 is usually about 20% cheaper. '
                'st1 and sc1 are HDD for large sequential reads at low cost and cannot boot. How Floci emulates this: '
                'the volume is created, but Floci accepts IOPS and throughput beyond the limits, so the step checks '
                'them itself.'
            ),
        },
        {
            'key': 'io2-volume',
            'title': 'Price an io2 volume for a database against gp3',
            'command': IO2_COMMAND,
            'explanation': (
                f'io2 Block Express is for databases that need more than gp3\'s 16,000 IOPS, sub-millisecond latency, '
                '99.999% durability, or Multi-Attach to several instances; it allows up to 1,000 IOPS per GiB and '
                f'256,000 per volume. For {IO2_IOPS:,} IOPS both types work, and the step compares the monthly cost: '
                'gp3 wins until you need what only io2 offers. How Floci emulates this: the volume is created; '
                'Floci does not check the IOPS limits, so the step does.'
            ),
        },
        {
            'key': 'attach-and-format',
            'title': f'Attach, format, and mount the gp3 volume on {APP_1}',
            'command': ATTACH_COMMAND,
            'explanation': (
                f'A new volume is a raw block device: attach it as {DEVICE_1} (Nitro instances show it as '
                '/dev/nvme1n1), create a filesystem once, mount it, and add it to /etc/fstab with nofail so the '
                'instance still boots without it. How Floci emulates this: the attachment is a real loop device in '
                'the instance container, so mkfs, mount, and the file are real.'
            ),
        },
        {
            'key': 'grow-volume',
            'title': f'Grow the volume to {GROWN_SIZE} GiB and {GROWN_IOPS:,} IOPS without detaching',
            'command': GROW_COMMAND,
            'explanation': (
                'Elastic Volumes change size, type, IOPS, and throughput while the volume stays attached; then the '
                'filesystem is extended (resize2fs or xfs_growfs). Volumes only grow, and the next change must wait '
                'six hours. How Floci emulates this: the modification completes at once and the loop device really '
                'grows. Floci refuses shrinking but not a second change within six hours. Online resize2fs does not '
                'take effect in Floci\'s container, so the step unmounts to resize.'
            ),
        },
        {
            'key': 'move-volume',
            'title': f'Move the volume and its data to {APP_2}',
            'command': MOVE_COMMAND,
            'explanation': (
                'Because EBS is network storage, a volume detached from one instance keeps its data and can attach '
                'to another in the same AZ, for example to recover from a failed instance. To use it in another AZ '
                'or Region, snapshot it to S3 and create a new volume from the snapshot; Data Lifecycle Manager or '
                'AWS Backup schedules snapshots. How Floci emulates this: the data really moves with the volume, and '
                'attaching across AZs is refused. CreateSnapshot is unsupported, so the step records the attempt.'
            ),
        },
        {
            'key': 'efs-file-system',
            'title': f'Create an EFS file system with mount targets in {AZ_A} and {AZ_B}',
            'command': EFS_COMMAND,
            'explanation': (
                'EFS is a regional NFS file system that many instances, containers, and functions mount at once, '
                'across AZs, growing and shrinking with the data. Each AZ gets a mount target in a subnet, guarded by '
                f'a security group that allows NFS (TCP {NFS_PORT}) only from the app servers. Elastic throughput '
                'scales with demand and bills per use. How Floci emulates this: the file system and mount targets '
                'are stored, but there is no NFS server, and every mount target reports the same AZ and IP, so the '
                'step checks the subnets\' AZs and the security group rule itself.'
            ),
        },
        {
            'key': 'efs-lifecycle',
            'title': 'Tier, protect, and scope the file system',
            'command': LIFECYCLE_COMMAND,
            'explanation': (
                'Lifecycle management moves files not read for 30 days to Infrequent Access and after 90 days to '
                'Archive, each far cheaper per GB with a per-read charge, and back to Standard on first access. '
                'Automatic backups use AWS Backup. The file system policy denies connections without TLS, and the '
                f'access point makes the app enter at {ACCESS_POINT_PATH} as user {POSIX_ID}. How Floci emulates '
                'this: the settings are stored but nothing is tiered, backed up, or enforced, so the step checks '
                'them.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    factory = FlociClientFactory()
    if name in ('ec2', 'ssm'):
        # Attaching, detaching, and deleting a volume moves a real block device, which can outlast the 5 s default.
        factory.config = factory.config.merge(Config(read_timeout=60))
    return factory.client(name)


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


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _tags(name: str, resource: str) -> list[dict[str, Any]]:
    return [{'ResourceType': resource, 'Tags': [{'Key': 'Name', 'Value': name}, TAG]}]


def gp3_limits(size: int, iops: int, throughput: int) -> str | None:
    if not 3000 <= iops <= min(16000, 500 * size):
        return f'gp3 allows 3,000 to {min(16000, 500 * size):,} IOPS at {size} GiB, not {iops:,}.'
    if not 125 <= throughput <= min(1000, iops // 4):
        return f'gp3 allows 125 to {min(1000, iops // 4)} MiB/s at {iops:,} IOPS, not {throughput}.'
    return None


def io2_limits(size: int, iops: int) -> str | None:
    if not 100 <= iops <= min(256000, 1000 * size):
        return f'io2 allows 100 to {min(256000, 1000 * size):,} IOPS at {size} GiB, not {iops:,}.'
    return None


def monthly_cost(volume_type: str, size: int, iops: int, throughput: int = 125) -> float:
    if volume_type == 'gp3':
        return round(size * GP3_GB + max(0, iops - 3000) * GP3_IOPS_PRICE
                     + max(0, throughput - 125) * GP3_MBPS_PRICE, 2)
    return round(size * IO2_GB + iops * IO2_IOPS_PRICE, 2)


# ── Lookups ──────────────────────────────────────────────────────────────────

def _subnet(az: str) -> str | None:
    subnets = _client('ec2').describe_subnets(Filters=[{'Name': 'availability-zone', 'Values': [az]}]).get('Subnets', [])
    subnets.sort(key=lambda subnet: not subnet.get('DefaultForAz'))
    return subnets[0]['SubnetId'] if subnets else None


def _subnet_azs(subnet_ids: list[str]) -> dict[str, str]:
    if not subnet_ids:
        return {}
    subnets = _client('ec2').describe_subnets(SubnetIds=subnet_ids).get('Subnets', [])
    return {subnet['SubnetId']: subnet['AvailabilityZone'] for subnet in subnets}


def _group(name: str) -> dict[str, Any] | None:
    groups = _client('ec2').describe_security_groups(Filters=[{'Name': 'group-name', 'Values': [name]}]).get(
        'SecurityGroups', [])
    return groups[0] if groups else None


def _instance(name: str) -> dict[str, Any] | None:
    reservations = _client('ec2').describe_instances(Filters=[
        {'Name': 'tag:Name', 'Values': [name]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running']},
    ]).get('Reservations', [])
    instances = [instance for reservation in reservations for instance in reservation.get('Instances', [])]
    return instances[0] if instances else None


def _volume(name: str) -> dict[str, Any] | None:
    volumes = _client('ec2').describe_volumes(Filters=[{'Name': 'tag:Name', 'Values': [name]}]).get('Volumes', [])
    volumes = [volume for volume in volumes if volume.get('State') not in ('deleting', 'deleted')]
    return volumes[0] if volumes else None


def _attachment(volume: dict[str, Any] | None) -> dict[str, Any]:
    attached = [item for item in (volume or {}).get('Attachments') or [] if item.get('State') in ('attaching', 'attached')]
    return attached[0] if attached else {}


def _file_system() -> dict[str, Any] | None:
    try:
        systems = _client('efs').describe_file_systems(CreationToken=EFS_TOKEN).get('FileSystems', [])
    except ClientError as exc:
        if _code(exc) == 'FileSystemNotFound':  # Floci; AWS returns an empty list
            return None
        raise
    systems = [item for item in systems if item.get('LifeCycleState') not in ('deleting', 'deleted')]
    return systems[0] if systems else None


def _mount_targets(file_system_id: str) -> list[dict[str, Any]]:
    return _client('efs').describe_mount_targets(FileSystemId=file_system_id).get('MountTargets', [])


def _access_points(file_system_id: str) -> list[dict[str, Any]]:
    return _client('efs').describe_access_points(FileSystemId=file_system_id).get('AccessPoints', [])


def _policy(file_system_id: str) -> dict[str, Any] | None:
    try:
        policy = _client('efs').describe_file_system_policy(FileSystemId=file_system_id).get('Policy')
    except ClientError as exc:
        if _code(exc) == 'PolicyNotFound':
            return None
        raise
    return json.loads(policy) if isinstance(policy, str) else policy


def _run_shell(instance_id: str, script: str) -> tuple[str, str, str]:
    """Run a script through SSM Run Command and return (status, stdout, stderr)."""
    ssm = _client('ssm')
    command_id = ssm.send_command(InstanceIds=[instance_id], DocumentName='AWS-RunShellScript',
                                  Parameters={'commands': [script]})['Command']['CommandId']
    outcome: dict[str, Any] = {}

    def finished() -> bool:
        try:
            outcome.update(ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id))
        except ClientError as exc:
            if _code(exc) == 'InvocationDoesNotExist':
                return False
            raise
        return outcome.get('Status') not in ('Pending', 'InProgress', 'Delayed')
    if not _wait(finished, timeout=SSM_TIMEOUT_SECONDS, delay=1):
        return 'TimedOut', outcome.get('StandardOutputContent') or '', 'The command did not finish.'
    return outcome.get('Status') or '', outcome.get('StandardOutputContent') or '', outcome.get('StandardErrorContent') or ''


def _size_gb(stdout: str) -> int | None:
    for line in stdout.splitlines():
        if line.startswith('size_gb='):
            digits = line.split('=', 1)[1].strip()
            return int(digits) if digits.isdigit() else None
    return None


def _wait_volume(volume_id: str, states: set[str], timeout: float = 60) -> bool:
    def ready() -> bool:
        volumes = _client('ec2').describe_volumes(VolumeIds=[volume_id]).get('Volumes', [])
        if not volumes:
            return False
        attachment = _attachment(volumes[0])
        state = volumes[0].get('State')
        return state in states and (state != 'in-use' or attachment.get('State') == 'attached')
    return _wait(ready, timeout=timeout, delay=1)


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_instances() -> tuple[bool, str]:
    for name in (APP_1, APP_2):
        instance = _instance(name)
        if not instance:
            return False, f'Instance {name} is not running.'
        if instance.get('State', {}).get('Name') != 'running':
            return False, f'{name} is still {instance.get("State", {}).get("Name")}.'
        if (instance.get('Placement') or {}).get('AvailabilityZone') != AZ_A:
            return False, f'{name} must run in {AZ_A}.'
    return True, f'{APP_1} and {APP_2} are running in {AZ_A}.'


def _check_gp3() -> tuple[bool, str]:
    volume = _volume(GP3_NAME)
    if not volume:
        return False, f'Volume {GP3_NAME} was not found.'
    if volume.get('VolumeType') != 'gp3' or volume.get('AvailabilityZone') != AZ_A or not volume.get('Encrypted'):
        return False, f'{GP3_NAME} must be an encrypted gp3 volume in {AZ_A}.'
    problem = gp3_limits(int(volume.get('Size') or 0), int(volume.get('Iops') or 0), int(volume.get('Throughput') or 0))
    if problem:
        return False, problem
    return True, (f'{GP3_NAME} is an encrypted gp3 volume of {volume["Size"]} GiB with {volume["Iops"]:,} IOPS and '
                  f'{volume["Throughput"]} MiB/s.')


def _check_io2() -> tuple[bool, str]:
    volume = _volume(IO2_NAME)
    if not volume:
        return False, f'Volume {IO2_NAME} was not found.'
    if volume.get('VolumeType') != 'io2' or not volume.get('Encrypted'):
        return False, f'{IO2_NAME} must be an encrypted io2 volume.'
    problem = io2_limits(int(volume.get('Size') or 0), int(volume.get('Iops') or 0))
    if problem:
        return False, problem
    if volume.get('Iops') != IO2_IOPS:
        return False, f'{IO2_NAME} should provision {IO2_IOPS:,} IOPS.'
    io2 = monthly_cost('io2', IO2_SIZE, IO2_IOPS)
    gp3 = monthly_cost('gp3', IO2_SIZE, IO2_IOPS)
    return True, (f'{IO2_NAME} provisions {IO2_IOPS:,} IOPS on {IO2_SIZE} GiB for about ${io2:,.2f} a month; gp3 with '
                  f'the same IOPS would be about ${gp3:,.2f}.')


def _check_attached() -> tuple[bool, str]:
    volume = _volume(GP3_NAME)
    if not volume or not _attachment(volume):
        return False, f'{GP3_NAME} is not attached.'
    marker = _marked('attach-and-format')
    if not marker:
        return False, f'Format and mount {GP3_NAME} on {APP_1}.'
    return True, marker['message']


def _check_grown() -> tuple[bool, str]:
    volume = _volume(GP3_NAME)
    if not volume:
        return False, f'Volume {GP3_NAME} was not found.'
    if volume.get('Size') != GROWN_SIZE or volume.get('Iops') != GROWN_IOPS:
        return False, f'{GP3_NAME} has {volume.get("Size")} GiB and {volume.get("Iops")} IOPS, not {GROWN_SIZE} and {GROWN_IOPS}.'
    marker = _marked('grow-volume')
    if not marker:
        return False, 'Extend the filesystem after the modification.'
    return True, marker['message']


def _check_moved() -> tuple[bool, str]:
    volume, app_2 = _volume(GP3_NAME), _instance(APP_2)
    if not volume or not app_2:
        return False, f'{GP3_NAME} and {APP_2} must both exist.'
    attachment = _attachment(volume)
    if attachment.get('InstanceId') != app_2['InstanceId']:
        return False, f'{GP3_NAME} is not attached to {APP_2}.'
    marker = _marked('move-volume')
    if not marker:
        return False, f'Mount the volume on {APP_2} and read the file.'
    return True, marker['message']


def _check_efs() -> tuple[bool, str]:
    file_system = _file_system()
    if not file_system:
        return False, f'File system {EFS_TOKEN} was not found.'
    if not file_system.get('Encrypted') or file_system.get('ThroughputMode') != 'elastic':
        return False, f'{EFS_TOKEN} must be encrypted with elastic throughput.'
    targets = _mount_targets(file_system['FileSystemId'])
    azs = set(_subnet_azs([target['SubnetId'] for target in targets]).values())
    if not {AZ_A, AZ_B} <= azs:
        return False, f'{EFS_TOKEN} needs mount targets in subnets in {AZ_A} and {AZ_B}.'
    efs_group, app_group = _group(EFS_SG), _group(APP_SG)
    if not efs_group or not app_group:
        return False, f'Security groups {EFS_SG} and {APP_SG} must exist.'
    nfs_from_app = any(
        rule.get('IpProtocol') == 'tcp' and rule.get('FromPort') == NFS_PORT and rule.get('ToPort') == NFS_PORT
        and any(pair.get('GroupId') == app_group['GroupId'] for pair in rule.get('UserIdGroupPairs') or [])
        for rule in efs_group.get('IpPermissions') or [])
    open_to_world = any(ip.get('CidrIp') == '0.0.0.0/0' for rule in efs_group.get('IpPermissions') or []
                        for ip in rule.get('IpRanges') or [])
    if not nfs_from_app or open_to_world:
        return False, f'{EFS_SG} must allow TCP {NFS_PORT} only from {APP_SG}.'
    for target in targets:
        groups = _client('efs').describe_mount_target_security_groups(MountTargetId=target['MountTargetId'])
        if efs_group['GroupId'] not in groups.get('SecurityGroups', []):
            return False, f'Mount target {target["MountTargetId"]} must use {EFS_SG}.'
    return True, f'{EFS_TOKEN} has mount targets in {AZ_A} and {AZ_B} that accept NFS only from {APP_SG}.'


def _check_lifecycle() -> tuple[bool, str]:
    file_system = _file_system()
    if not file_system:
        return False, f'File system {EFS_TOKEN} was not found.'
    file_system_id = file_system['FileSystemId']
    efs = _client('efs')
    policies = efs.describe_lifecycle_configuration(FileSystemId=file_system_id).get('LifecyclePolicies', [])
    if sorted(json.dumps(item, sort_keys=True) for item in policies) != sorted(
            json.dumps(item, sort_keys=True) for item in LIFECYCLE):
        return False, 'Lifecycle should move files to IA after 30 days, Archive after 90, and back on first access.'
    if (efs.describe_backup_policy(FileSystemId=file_system_id).get('BackupPolicy') or {}).get('Status') != 'ENABLED':
        return False, 'Automatic backups are off.'
    policy = _policy(file_system_id) or {}
    tls_only = any(statement.get('Effect') == 'Deny' and (statement.get('Condition') or {}).get('Bool', {}).get(
        'aws:SecureTransport') == 'false' for statement in policy.get('Statement', []))
    if not tls_only:
        return False, 'The file system policy must deny connections without TLS.'
    points = [point for point in _access_points(file_system_id)
              if (point.get('RootDirectory') or {}).get('Path') == ACCESS_POINT_PATH
              and (point.get('PosixUser') or {}).get('Uid') == POSIX_ID]
    if not points:
        return False, f'Create an access point at {ACCESS_POINT_PATH} for user {POSIX_ID}.'
    return True, (f'Files tier to IA after 30 days and Archive after 90, backups are on, TLS is required, and the app '
                  f'enters at {ACCESS_POINT_PATH} as user {POSIX_ID}.')


# ── Steps ────────────────────────────────────────────────────────────────────

def _ensure_group(name: str, description: str) -> str:
    group = _group(name)
    if group:
        return group['GroupId']
    return _client('ec2').create_security_group(GroupName=name, Description=description,
                                                TagSpecifications=_tags(name, 'security-group'))['GroupId']


def step_launch_instances() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    subnet = _subnet(AZ_A)
    if not subnet:
        return _result('launch-instances', INSTANCES_COMMAND, {}, False, f'No subnet in {AZ_A}.', started)
    group_id = _ensure_group(APP_SG, 'Storage lab app servers')
    for name in (APP_1, APP_2):
        if not _instance(name):
            ec2.run_instances(ImageId=IMAGE_ID, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1, SubnetId=subnet,
                              SecurityGroupIds=[group_id], TagSpecifications=_tags(name, 'instance'))
    _wait(lambda: all((_instance(name) or {}).get('State', {}).get('Name') == 'running' for name in (APP_1, APP_2)),
          timeout=120, delay=3)
    response = {name: {key: (_instance(name) or {}).get(key) for key in ('InstanceId', 'State', 'Placement', 'SubnetId')}
                for name in (APP_1, APP_2)}
    verified, message = _check_instances()
    return _result('launch-instances', INSTANCES_COMMAND, response, verified, message, started)


def step_gp3_volume() -> dict[str, Any]:
    started = time.perf_counter()
    if not _volume(GP3_NAME):
        _client('ec2').create_volume(AvailabilityZone=AZ_A, VolumeType='gp3', Size=GP3_SIZE, Iops=GP3_IOPS,
                                     Throughput=GP3_THROUGHPUT, Encrypted=True, TagSpecifications=_tags(GP3_NAME, 'volume'))
    volume = _volume(GP3_NAME) or {}
    _wait_volume(volume.get('VolumeId', ''), {'available', 'in-use'})
    volume = _volume(GP3_NAME) or {}
    response = {key: volume.get(key) for key in (
        'VolumeId', 'State', 'VolumeType', 'Size', 'Iops', 'Throughput', 'Encrypted', 'AvailabilityZone')}
    response['Limits'] = {'MaxIops': min(16000, 500 * int(volume.get('Size') or 0)),
                          'MaxThroughput': min(1000, int(volume.get('Iops') or 0) // 4)}
    response['MonthlyCost'] = monthly_cost('gp3', int(volume.get('Size') or 0), int(volume.get('Iops') or 0),
                                           int(volume.get('Throughput') or 0))
    verified, message = _check_gp3()
    if verified:
        message += f' About ${response["MonthlyCost"]:.2f} a month; Floci does not check the IOPS or throughput limits.'
    return _result('gp3-volume', GP3_COMMAND, response, verified, message, started)


def step_io2_volume() -> dict[str, Any]:
    started = time.perf_counter()
    if not _volume(IO2_NAME):
        _client('ec2').create_volume(AvailabilityZone=AZ_A, VolumeType='io2', Size=IO2_SIZE, Iops=IO2_IOPS,
                                     Encrypted=True, TagSpecifications=_tags(IO2_NAME, 'volume'))
    volume = _volume(IO2_NAME) or {}
    response = {key: volume.get(key) for key in ('VolumeId', 'State', 'VolumeType', 'Size', 'Iops', 'Encrypted')}
    response['MonthlyCost'] = {'io2': monthly_cost('io2', IO2_SIZE, IO2_IOPS),
                               'gp3SameIops': monthly_cost('gp3', IO2_SIZE, IO2_IOPS)}
    verified, message = _check_io2()
    return _result('io2-volume', IO2_COMMAND, response, verified, message, started)


def step_attach_and_format() -> dict[str, Any]:
    started = time.perf_counter()
    app_1, volume = _instance(APP_1), _volume(GP3_NAME)
    if not app_1 or not volume:
        return _result('attach-and-format', ATTACH_COMMAND, {}, False,
                       f'Launch the instances and create {GP3_NAME} first.', started)
    attachment = _attachment(volume)
    if attachment.get('InstanceId') != app_1['InstanceId']:
        if attachment:
            # A rerun after the move: unmount it on the other instance and bring it back.
            _run_shell(attachment['InstanceId'], UNMOUNT_SCRIPT)
            _client('ec2').detach_volume(VolumeId=volume['VolumeId'])
            _wait_volume(volume['VolumeId'], {'available'})
        _client('ec2').attach_volume(VolumeId=volume['VolumeId'], InstanceId=app_1['InstanceId'], Device=DEVICE_1)
        _wait_volume(volume['VolumeId'], {'in-use'})
    status, stdout, stderr = _run_shell(app_1['InstanceId'], FORMAT_SCRIPT)
    response = {'Attachment': _attachment(_volume(GP3_NAME)), 'Script': FORMAT_SCRIPT,
                'Run': {'Status': status, 'Output': stdout, 'Error': stderr}}
    size = _size_gb(stdout)
    if status != 'Success' or ORDERS_LINE not in stdout:
        return _result('attach-and-format', ATTACH_COMMAND, response, False,
                       f'Formatting and mounting on {APP_1} failed ({status}): {stderr.strip()[:300]}', started)
    message = (f'{GP3_NAME} is mounted at {MOUNT_POINT} on {APP_1} ({size} GiB filesystem) and holds orders.csv. Floci '
               'backs the attachment with a real block device.')
    _marker('attach-and-format', {'message': message})
    verified, check = _check_attached()
    return _result('attach-and-format', ATTACH_COMMAND, response, verified, message if verified else check, started)


def step_grow_volume() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_attached()
    volume, app_1 = _volume(GP3_NAME), _instance(APP_1)
    if not ok or not volume or not app_1:
        return _result('grow-volume', GROW_COMMAND, {}, False, f'Mount the volume first: {problem}', started)
    if _attachment(volume).get('InstanceId') != app_1['InstanceId']:
        return _result('grow-volume', GROW_COMMAND, {}, False,
                       f'{GP3_NAME} is no longer on {APP_1}; run the attach step again first.', started)
    ec2 = _client('ec2')
    if volume.get('Size') != GROWN_SIZE or volume.get('Iops') != GROWN_IOPS:
        ec2.modify_volume(VolumeId=volume['VolumeId'], Size=GROWN_SIZE, Iops=GROWN_IOPS)

    def modified() -> bool:
        items = ec2.describe_volumes_modifications(VolumeIds=[volume['VolumeId']]).get('VolumesModifications', [])
        return any(item.get('ModificationState') in ('optimizing', 'completed') for item in items)
    _wait(modified, timeout=120, delay=2)
    shrink: dict[str, Any]
    try:
        ec2.modify_volume(VolumeId=volume['VolumeId'], Size=GP3_SIZE)
        shrink = {'Status': 'accepted'}
    except ClientError as exc:
        shrink = {'Status': 'refused', 'Code': _code(exc)}
    status, stdout, stderr = _run_shell(app_1['InstanceId'], GROW_SCRIPT)
    size = _size_gb(stdout)
    modifications = ec2.describe_volumes_modifications(VolumeIds=[volume['VolumeId']]).get('VolumesModifications', [])
    response = {'Modifications': modifications, 'ShrinkAttempt': shrink,
                'Run': {'Status': status, 'Output': stdout, 'Error': stderr}}
    if status != 'Success' or ORDERS_LINE not in stdout or not size or size < GROWN_SIZE - 2:
        return _result('grow-volume', GROW_COMMAND, response, False,
                       f'The filesystem did not grow to about {GROWN_SIZE} GiB ({status}, {size} GiB).', started)
    message = (f'{GP3_NAME} grew to {GROWN_SIZE} GiB and {GROWN_IOPS:,} IOPS while attached, the filesystem now has '
               f'{size} GiB, and orders.csv is intact.')
    if shrink['Status'] == 'refused':
        message += ' Shrinking it back was refused, as on AWS.'
    _marker('grow-volume', {'message': message})
    verified, check = _check_grown()
    return _result('grow-volume', GROW_COMMAND, response, verified, message if verified else check, started)


def step_move_volume() -> dict[str, Any]:
    started = time.perf_counter()
    volume, app_1, app_2 = _volume(GP3_NAME), _instance(APP_1), _instance(APP_2)
    if not volume or not app_1 or not app_2 or not _marked('attach-and-format'):
        return _result('move-volume', MOVE_COMMAND, {}, False, f'Format and mount {GP3_NAME} on {APP_1} first.', started)
    ec2 = _client('ec2')
    attachment = _attachment(volume)
    if attachment.get('InstanceId') != app_2['InstanceId']:
        if attachment.get('InstanceId') == app_1['InstanceId']:
            _run_shell(app_1['InstanceId'], UNMOUNT_SCRIPT)
            ec2.detach_volume(VolumeId=volume['VolumeId'])
            _wait_volume(volume['VolumeId'], {'available'})
        ec2.attach_volume(VolumeId=volume['VolumeId'], InstanceId=app_2['InstanceId'], Device=DEVICE_2)
        _wait_volume(volume['VolumeId'], {'in-use'})
    status, stdout, stderr = _run_shell(app_2['InstanceId'], MOVED_SCRIPT)
    snapshot: dict[str, Any]
    try:
        created = ec2.create_snapshot(VolumeId=volume['VolumeId'], Description=f'{GP3_NAME} copy',
                                      TagSpecifications=_tags(f'{GP3_NAME}-snapshot', 'snapshot'))
        snapshot = {'Status': 'created', 'SnapshotId': created.get('SnapshotId')}
    except ClientError as exc:
        snapshot = {'Status': 'refused', 'Code': _code(exc)}
    response = {'Attachment': _attachment(_volume(GP3_NAME)), 'Run': {'Status': status, 'Output': stdout, 'Error': stderr},
                'SnapshotAttempt': snapshot}
    if status != 'Success' or ORDERS_LINE not in stdout:
        return _result('move-volume', MOVE_COMMAND, response, False,
                       f'{APP_2} could not read orders.csv from the volume ({status}): {stderr.strip()[:300]}', started)
    message = f'{APP_2} mounted {GP3_NAME} and read orders.csv written on {APP_1}.'
    if snapshot['Status'] == 'refused':
        message += f' CreateSnapshot was refused ({snapshot["Code"]}): Floci does not support EBS snapshots.'
    _marker('move-volume', {'message': message})
    verified, check = _check_moved()
    return _result('move-volume', MOVE_COMMAND, response, verified, message if verified else check, started)


def step_efs_file_system() -> dict[str, Any]:
    started = time.perf_counter()
    app_group = _group(APP_SG)
    subnets = {az: _subnet(az) for az in (AZ_A, AZ_B)}
    if not app_group or not all(subnets.values()):
        return _result('efs-file-system', EFS_COMMAND, {}, False,
                       f'Launch the instances first (and make sure {AZ_A} and {AZ_B} have subnets).', started)
    ec2, efs = _client('ec2'), _client('efs')
    efs_group_id = _ensure_group(EFS_SG, 'NFS from storage lab app servers')
    efs_group = _group(EFS_SG) or {}
    has_rule = any(rule.get('FromPort') == NFS_PORT and any(
        pair.get('GroupId') == app_group['GroupId'] for pair in rule.get('UserIdGroupPairs') or [])
        for rule in efs_group.get('IpPermissions') or [])
    if not has_rule:
        ec2.authorize_security_group_ingress(GroupId=efs_group_id, IpPermissions=[{
            'IpProtocol': 'tcp', 'FromPort': NFS_PORT, 'ToPort': NFS_PORT,
            'UserIdGroupPairs': [{'GroupId': app_group['GroupId'], 'Description': 'NFS from app servers'}]}])
    file_system = _file_system()
    if not file_system:
        efs.create_file_system(CreationToken=EFS_TOKEN, PerformanceMode='generalPurpose', ThroughputMode='elastic',
                               Encrypted=True, Tags=[{'Key': 'Name', 'Value': EFS_NAME}, TAG])
        _wait(lambda: (_file_system() or {}).get('LifeCycleState') == 'available', timeout=60, delay=1)
        file_system = _file_system() or {}
    existing = {target['SubnetId'] for target in _mount_targets(file_system['FileSystemId'])}
    for subnet in subnets.values():
        if subnet not in existing:
            efs.create_mount_target(FileSystemId=file_system['FileSystemId'], SubnetId=subnet,
                                    SecurityGroups=[efs_group_id])
    targets = _mount_targets(file_system['FileSystemId'])
    subnet_azs = _subnet_azs([target['SubnetId'] for target in targets])
    response = {'FileSystem': {key: file_system.get(key) for key in (
        'FileSystemId', 'LifeCycleState', 'PerformanceMode', 'ThroughputMode', 'Encrypted')},
        'MountTargets': [{**{key: target.get(key) for key in (
            'MountTargetId', 'SubnetId', 'AvailabilityZoneName', 'IpAddress', 'LifeCycleState')},
            'SubnetAvailabilityZone': subnet_azs.get(target['SubnetId'])} for target in targets]}
    verified, message = _check_efs()
    reported = {target.get('AvailabilityZoneName') for target in targets}
    if verified and (len(reported) < len(targets) or len({target.get('IpAddress') for target in targets}) < len(targets)):
        message += (' Floci reports the same AZ and IP for every mount target and runs no NFS server, so the step '
                    'checked the subnets instead.')
    return _result('efs-file-system', EFS_COMMAND, response, verified, message, started)


def step_efs_lifecycle() -> dict[str, Any]:
    started = time.perf_counter()
    file_system = _file_system()
    if not file_system:
        return _result('efs-lifecycle', LIFECYCLE_COMMAND, {}, False, f'Create {EFS_TOKEN} first.', started)
    efs = _client('efs')
    file_system_id = file_system['FileSystemId']
    efs.put_lifecycle_configuration(FileSystemId=file_system_id, LifecyclePolicies=LIFECYCLE)
    efs.put_backup_policy(FileSystemId=file_system_id, BackupPolicy={'Status': 'ENABLED'})
    tls_policy = {
        'Version': '2012-10-17',
        'Statement': [{'Sid': 'DenyWithoutTls', 'Effect': 'Deny', 'Principal': {'AWS': '*'}, 'Action': '*',
                       'Resource': file_system['FileSystemArn'],
                       'Condition': {'Bool': {'aws:SecureTransport': 'false'}}}],
    }
    efs.put_file_system_policy(FileSystemId=file_system_id, Policy=json.dumps(tls_policy))
    points = [point for point in _access_points(file_system_id)
              if (point.get('RootDirectory') or {}).get('Path') == ACCESS_POINT_PATH]
    if not points:
        efs.create_access_point(
            FileSystemId=file_system_id, PosixUser={'Uid': POSIX_ID, 'Gid': POSIX_ID},
            RootDirectory={'Path': ACCESS_POINT_PATH,
                           'CreationInfo': {'OwnerUid': POSIX_ID, 'OwnerGid': POSIX_ID, 'Permissions': '750'}},
            Tags=[{'Key': 'Name', 'Value': f'{EFS_NAME}-orders'}, TAG])
    response = {
        'LifecyclePolicies': efs.describe_lifecycle_configuration(FileSystemId=file_system_id).get('LifecyclePolicies'),
        'BackupPolicy': efs.describe_backup_policy(FileSystemId=file_system_id).get('BackupPolicy'),
        'FileSystemPolicy': _policy(file_system_id),
        'AccessPoints': [{key: point.get(key) for key in ('AccessPointId', 'PosixUser', 'RootDirectory')}
                         for point in _access_points(file_system_id)],
    }
    verified, message = _check_lifecycle()
    if verified:
        message += ' Floci stores these settings; nothing is tiered, backed up, or enforced.'
    return _result('efs-lifecycle', LIFECYCLE_COMMAND, response, verified, message, started)


RUNNERS = {
    'launch-instances': step_launch_instances,
    'gp3-volume': step_gp3_volume,
    'io2-volume': step_io2_volume,
    'attach-and-format': step_attach_and_format,
    'grow-volume': step_grow_volume,
    'move-volume': step_move_volume,
    'efs-file-system': step_efs_file_system,
    'efs-lifecycle': step_efs_lifecycle,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def status() -> dict[str, Any]:
    checks = {
        'launch-instances': lambda: _check_instances()[0],
        'gp3-volume': lambda: _check_gp3()[0],
        'io2-volume': lambda: _check_io2()[0],
        'attach-and-format': lambda: _check_attached()[0],
        'grow-volume': lambda: _check_grown()[0],
        'move-volume': lambda: _check_moved()[0],
        'efs-file-system': lambda: _check_efs()[0],
        'efs-lifecycle': lambda: _check_lifecycle()[0],
    }
    steps = {}
    for step, check in checks.items():
        verified = _safe(check)
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
    """Delete the EFS access points, mount targets and file system, the instances, the volumes, and the groups."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> bool:
        try:
            call()
            deleted.append(label)
            return True
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NotAttached' in code:
                return False
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')
        return False

    ec2, efs = _client('ec2'), _client('efs')
    try:
        file_system = _file_system()
    except (ClientError, BotoCoreError) as exc:
        file_system = None
        errors.append(f'file system: {exc}')
    if file_system:
        file_system_id = file_system['FileSystemId']
        for point in _access_points(file_system_id):
            attempt(f'access-point:{point["AccessPointId"]}',
                    lambda point=point: efs.delete_access_point(AccessPointId=point['AccessPointId']))
        for target in _mount_targets(file_system_id):
            attempt(f'mount-target:{target["MountTargetId"]}',
                    lambda target=target: efs.delete_mount_target(MountTargetId=target['MountTargetId']))
        _wait(lambda: not _mount_targets(file_system_id), timeout=60, delay=1)
        attempt(f'file-system:{file_system_id}', lambda: efs.delete_file_system(FileSystemId=file_system_id))

    # Terminate the instances first: Floci cannot delete a volume's backing file while an instance has it mounted.
    instance_ids = [instance['InstanceId'] for instance in (_instance(APP_1), _instance(APP_2)) if instance]
    if instance_ids:
        attempt(f'instances:{",".join(instance_ids)}', lambda: ec2.terminate_instances(InstanceIds=instance_ids))
        _wait(lambda: not _instance(APP_1) and not _instance(APP_2), timeout=120, delay=2)
    for name in (GP3_NAME, IO2_NAME):
        volume = _volume(name)
        if not volume:
            continue
        if _attachment(volume):
            attempt(f'detach:{name}', lambda volume=volume: ec2.detach_volume(VolumeId=volume['VolumeId'], Force=True))
            _wait_volume(volume['VolumeId'], {'available'}, timeout=60)
        attempt(f'volume:{name}', lambda volume=volume: ec2.delete_volume(VolumeId=volume['VolumeId']))
    for name in (EFS_SG, APP_SG):
        group = _group(name)
        if group:
            attempt(f'security-group:{name}', lambda group=group: ec2.delete_security_group(GroupId=group['GroupId']))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws efs delete-access-point --access-point-id <ap-id>\n'
            'aws efs delete-mount-target --mount-target-id <mt-id>   # each AZ\n'
            'aws efs delete-file-system --file-system-id <fs-id>\n'
            f'aws ec2 terminate-instances --instance-ids <{APP_1}-id> <{APP_2}-id>\n'
            'aws ec2 delete-volume --volume-id <volume-id>   # gp3 and io2, once detached\n'
            f'aws ec2 delete-security-group --group-id <{EFS_SG}> ; and <{APP_SG}>'
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
            else 'The file system, volumes, instances, and security groups are deleted.',
        },
    }
