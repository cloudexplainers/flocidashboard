"""Cloud economics lab: look up prices with the Price List API, start an estimate in the AWS Pricing Calculator, compare
three years of on-premises cost with the cloud, scale an Auto Scaling group out and back in instead of buying for the
peak, rightsize an oversized server from its CPU history, count licenses on a Dedicated Host for bring your own license,
and compare a fixed-price Lightsail bundle with pay-as-you-go EC2.

Floci 2.3.0 serves On-Demand Linux prices for t3.micro, m5.large and c5.large, stores Pricing Calculator workload
estimates but prices no usage (total stays 0), runs Auto Scaling instances as real containers, stores CloudWatch metric
data, allocates Dedicated Hosts with their sockets and cores, and lists Lightsail bundles with prices. Compute Optimizer
is missing and other instance types have no prices, so the lab uses typical us-east-1 list prices for those and says so.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'pricing'
LAB_KEY = 'cloud-economics'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
LAB_TAG = {'Key': 'Lab', 'Value': LAB_KEY}
LOCATION = 'US East (N. Virginia)'
HOURS_PER_MONTH = 730
YEARS = 3

PRICED_TYPES = ['t3.micro', 'm5.large', 'c5.large']
# Typical us-east-1 On-Demand Linux prices for sizes Floci's Price List does not carry.
LIST_PRICES = {'t3.small': 0.0208, 't3.medium': 0.0416, 't3.large': 0.0832, 'm5.xlarge': 0.192, 'm5.2xlarge': 0.384}
SIZES = [  # (type, vCPU, memory GiB)
    ('t3.micro', 2, 1), ('t3.small', 2, 2), ('t3.medium', 2, 4), ('t3.large', 2, 8), ('m5.large', 2, 8),
    ('m5.xlarge', 4, 16), ('m5.2xlarge', 8, 32),
]

ESTIMATE = 'lab-econ-orders-platform'

# On premises, sized for the yearly peak of 8 servers and bought up front.
ON_PREM = {
    'servers': 8,
    'server_price': 9000.0,
    'storage_and_network_hardware': 18000.0,
    'facilities_power_cooling_per_month': 1400.0,
    'admin_fte_share': 0.5,
    'admin_salary_per_year': 120000.0,
    'licenses_per_server_per_year': 600.0,
}
# The same workload in the cloud: 3 m5.large on average, plus storage, transfer, and a little admin time.
CLOUD = {
    'average_instances': 3,
    'instance_type': 'm5.large',
    'storage_and_transfer_per_month': 220.0,
    'admin_fte_share': 0.15,
}

LAUNCH_TEMPLATE = 'lab-econ-web'
ASG = 'lab-econ-web-asg'
IMAGE_ID = 'ami-ubuntu2204'
ASG_TYPE = 't3.micro'
MIN_SIZE, PEAK_SIZE, MAX_SIZE = 1, 2, 3
# Instances a web tier needs for each hour of a day (busy at lunch and in the evening).
HOURLY_DEMAND = [1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 2, 2, 2, 2, 2, 3, 3, 2, 2, 1, 1]

METRIC_NAMESPACE = 'Lab/Economics'
REPORT_SERVER = 'reports-01'
REPORT_TYPE = 'm5.2xlarge'
CPU_TARGET_PEAK = 70.0

HOST_TYPE = 'm5.large'
HOST_TAG = 'lab-econ-byol-host'
LICENSE_PER_CORE_PER_YEAR = 450.0
LICENSE_INCLUDED_PER_HOUR = 0.092  # typical Windows license premium on an m5.large
LICENSED_VMS = 10

SITE_TRANSFER_GB = 1500
EBS_GP3_PER_GB_MONTH = 0.08
TRANSFER_OUT_PER_GB = 0.09
FREE_TRANSFER_GB = 100

PRICES_COMMAND = (
    'aws pricing describe-services --region us-east-1\n'
    'aws pricing get-attribute-values --service-code AmazonEC2 --attribute-name instanceType\n'
    'aws pricing get-products --service-code AmazonEC2 --filters Type=TERM_MATCH,Field=instanceType,Value=m5.large '
    f'"Type=TERM_MATCH,Field=location,Value={LOCATION}" Type=TERM_MATCH,Field=operatingSystem,Value=Linux'
)
ESTIMATE_COMMAND = (
    f'aws bcm-pricing-calculator create-workload-estimate --name {ESTIMATE}\n'
    'aws bcm-pricing-calculator batch-create-workload-estimate-usage --workload-estimate-id <id> --usage '
    '\'[{"serviceCode":"AmazonEC2","usageType":"BoxUsage:m5.large","operation":"RunInstances","key":"web",'
    '"amount":2190,"usageAccountId":"<account>"}]\'\n'
    'aws bcm-pricing-calculator get-workload-estimate --identifier <id>'
)
TCO_COMMAND = '# Compare three years of an on-premises data center sized for the peak with pay-as-you-go cloud'
ELASTIC_COMMAND = (
    f'aws ec2 create-launch-template --launch-template-name {LAUNCH_TEMPLATE} --launch-template-data '
    f'ImageId={IMAGE_ID},InstanceType={ASG_TYPE}\n'
    f'aws autoscaling create-auto-scaling-group --auto-scaling-group-name {ASG} --launch-template '
    f'LaunchTemplateName={LAUNCH_TEMPLATE} --min-size {MIN_SIZE} --max-size {MAX_SIZE} --desired-capacity {MIN_SIZE} '
    '--vpc-zone-identifier <subnet-a>,<subnet-b>\n'
    f'aws autoscaling set-desired-capacity --auto-scaling-group-name {ASG} --desired-capacity {PEAK_SIZE}   # lunch peak\n'
    f'aws autoscaling set-desired-capacity --auto-scaling-group-name {ASG} --desired-capacity {MIN_SIZE}   # night'
)
RIGHTSIZE_COMMAND = (
    f'aws cloudwatch put-metric-data --namespace {METRIC_NAMESPACE} --metric-name CPUUtilization '
    f'--dimensions Server={REPORT_SERVER} --value <percent> --timestamp <hour>   # 14 days of hourly samples\n'
    f'aws cloudwatch get-metric-statistics --namespace {METRIC_NAMESPACE} --metric-name CPUUtilization '
    f'--dimensions Name=Server,Value={REPORT_SERVER} --statistics Average Maximum --period 86400 '
    '--start-time <14-days-ago> --end-time <now>\n'
    'aws compute-optimizer get-ec2-instance-recommendations'
)
BYOL_COMMAND = (
    f'aws ec2 allocate-hosts --instance-type {HOST_TYPE} --availability-zone us-east-1a --quantity 1\n'
    'aws ec2 describe-hosts --host-ids <host-id>\n'
    'aws ec2 release-hosts --host-ids <host-id>'
)
LIGHTSAIL_COMMAND = (
    'aws lightsail get-bundles\n'
    '# Compare a fixed monthly bundle with t3.micro + 20 GiB gp3 + data transfer out on EC2'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Work out cloud economics: TCO, elasticity, rightsizing, licensing, and fixed-price bundles',
    'description': (
        'Look up prices with the Price List API and start a Pricing Calculator estimate, compare three years of an '
        'on-premises data center with the cloud, scale an Auto Scaling group out and back in instead of buying for '
        'the peak, rightsize an oversized server from its CPU history, count licenses on a Dedicated Host, and '
        'compare a fixed-price Lightsail bundle with pay-as-you-go EC2.'
    ),
    'steps': [
        {
            'key': 'price-list',
            'title': 'Look up On-Demand prices with the Price List API',
            'command': PRICES_COMMAND,
            'explanation': (
                'Every AWS price is public and can be queried: the Price List API filters products by attributes '
                'such as instance type, Region and operating system, and the Pricing Calculator and Cost Explorer '
                'use the same data. How Floci emulates this: the Price List API serves On-Demand Linux prices for '
                't3.micro, m5.large and c5.large; other types have no prices, so later steps use typical us-east-1 '
                'list prices for them and say so.'
            ),
        },
        {
            'key': 'pricing-calculator',
            'title': 'Start an estimate in the AWS Pricing Calculator',
            'command': ESTIMATE_COMMAND,
            'explanation': (
                'The AWS Pricing Calculator estimates the cost of a workload before you build it, and can include '
                'your discounts and commitments. Workload estimates hold usage lines (for example 2,190 m5.large '
                'hours a month). How Floci emulates this: the estimate is stored, but usage lines are rejected for '
                'missing pricing data and the total stays $0, so the step prices the lines from the Price List '
                'itself.'
            ),
        },
        {
            'key': 'tco-comparison',
            'title': 'Compare three years on premises with the cloud',
            'command': TCO_COMMAND,
            'explanation': (
                'Total cost of ownership on premises includes servers bought for the peak, storage and network '
                'hardware, data center space, power and cooling, the staff who run it, and licenses. In the cloud '
                'most of that becomes a variable monthly cost for what you use, and AWS carries the facilities. How '
                'Floci emulates this: nothing to emulate; the model uses the m5.large price from the Price List.'
            ),
        },
        {
            'key': 'elastic-capacity',
            'title': 'Scale out for the peak and back in, instead of buying for it',
            'command': ELASTIC_COMMAND,
            'explanation': (
                'Elasticity means capacity follows demand: an Auto Scaling group adds instances for the lunch and '
                'evening peaks and removes them at night, so you pay for the instance-hours you need instead of '
                'the peak all day. How Floci emulates this: the group launches and terminates real instances (Docker '
                'containers); the step counts instance-hours from a sample day of demand.'
            ),
        },
        {
            'key': 'rightsizing',
            'title': 'Rightsize an oversized server from its CPU history',
            'command': RIGHTSIZE_COMMAND,
            'explanation': (
                'Rightsizing matches the instance type and size to what the workload uses: look at at least two '
                'weeks of CPU and memory, size for the peak with headroom, and pick the smallest type that fits. '
                'AWS Compute Optimizer and Cost Explorer make these recommendations. How Floci emulates this: '
                'metric data is stored and statistics are real; Compute Optimizer is missing, so the step '
                'recommends the size itself.'
            ),
        },
        {
            'key': 'byol-dedicated-host',
            'title': 'Count licenses on a Dedicated Host for bring your own license',
            'command': BYOL_COMMAND,
            'explanation': (
                'Software licensed per socket or core (some Windows Server, SQL Server and Oracle licenses) can be '
                'brought to AWS (BYOL) on a Dedicated Host, a physical server whose sockets and cores you can see. '
                'The alternative is license-included pricing billed by the hour. How Floci emulates this: the host '
                'is allocated with its sockets and cores and released straight away; there are no Windows prices, '
                'so the step uses a typical license-included premium.'
            ),
        },
        {
            'key': 'lightsail-vs-ec2',
            'title': 'Compare a fixed-price Lightsail bundle with pay-as-you-go EC2',
            'command': LIGHTSAIL_COMMAND,
            'explanation': (
                'Amazon Lightsail bundles a virtual server, SSD storage, a static IP and a data transfer allowance '
                'into one predictable monthly price, which suits small sites and simple apps. EC2 bills compute, '
                'storage and transfer out separately and gives you every instance type and service integration. '
                'How Floci emulates this: Lightsail bundles and the t3.micro price are real; EBS and transfer use '
                'typical list prices.'
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


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _money(value: float) -> str:
    return f'${value:,.0f}'


# ── Pure logic ───────────────────────────────────────────────────────────────

def on_demand_price(price_item: str) -> float | None:
    product = json.loads(price_item)
    for term in (product.get('terms') or {}).get('OnDemand', {}).values():
        for dimension in term.get('priceDimensions', {}).values():
            usd = (dimension.get('pricePerUnit') or {}).get('USD')
            if usd is not None:
                return float(usd)
    return None


def price_for(instance_type: str, prices: dict[str, float]) -> tuple[float, str]:
    if instance_type in prices:
        return prices[instance_type], 'Price List API'
    return LIST_PRICES[instance_type], 'typical list price (not in Floci\'s Price List)'


def tco(hourly: float, on_prem: dict[str, float] = ON_PREM, cloud: dict[str, Any] = CLOUD, years: int = YEARS) -> dict[str, Any]:
    months = 12 * years
    prem = {
        'Servers bought for the peak (up front)': on_prem['servers'] * on_prem['server_price'],
        'Storage and network hardware (up front)': on_prem['storage_and_network_hardware'],
        'Space, power and cooling': on_prem['facilities_power_cooling_per_month'] * months,
        'Operations staff': on_prem['admin_fte_share'] * on_prem['admin_salary_per_year'] * years,
        'Licenses': on_prem['licenses_per_server_per_year'] * on_prem['servers'] * years,
    }
    aws = {
        f'{cloud["average_instances"]} x {cloud["instance_type"]} On-Demand, scaled to demand':
            cloud['average_instances'] * hourly * HOURS_PER_MONTH * months,
        'Storage and data transfer': cloud['storage_and_transfer_per_month'] * months,
        'Operations staff': cloud['admin_fte_share'] * on_prem['admin_salary_per_year'] * years,
    }
    prem_total, aws_total = sum(prem.values()), sum(aws.values())
    upfront = on_prem['servers'] * on_prem['server_price'] + on_prem['storage_and_network_hardware']
    return {
        'OnPremises': {key: round(value, 2) for key, value in prem.items()},
        'OnPremisesTotal': round(prem_total, 2),
        'OnPremisesUpfront': round(upfront, 2),
        'Cloud': {key: round(value, 2) for key, value in aws.items()},
        'CloudTotal': round(aws_total, 2),
        'CloudUpfront': 0.0,
        'Savings': round(prem_total - aws_total, 2),
        'SavingsPercent': round((prem_total - aws_total) / prem_total * 100, 1),
    }


def elastic_hours(demand: list[int] = HOURLY_DEMAND) -> dict[str, Any]:
    peak = max(demand)
    fixed = peak * len(demand)
    elastic = sum(demand)
    return {'PeakInstances': peak, 'FixedForPeakHours': fixed, 'ElasticHours': elastic,
            'SavedPercent': round((fixed - elastic) / fixed * 100, 1)}


def recommend_size(peak_cpu: float, current: str, target_peak: float = CPU_TARGET_PEAK,
                   sizes: list[tuple[str, int, int]] = SIZES) -> str:
    """Pick the smallest size in the same or a burstable family whose vCPUs keep the observed peak under the target,
    without dropping below half the current memory."""
    by_type = {name: (vcpu, memory) for name, vcpu, memory in sizes}
    vcpu, memory = by_type[current]
    needed_vcpu = vcpu * peak_cpu / target_peak
    candidates = [(name, cpus, mem) for name, cpus, mem in sizes if cpus >= needed_vcpu and mem >= memory / 2]
    candidates.sort(key=lambda item: (item[1], item[2]))
    return candidates[0][0] if candidates else current


def cpu_samples(days: int = 14) -> list[float]:
    """Hourly CPU of a reporting server: quiet most of the day, a nightly report job at 02:00."""
    samples = []
    for hour in range(days * 24):
        at = hour % 24
        samples.append(31.0 if at == 2 else 22.0 if at in (1, 3) else 6.0 + (hour * 7 % 5))
    return samples


def license_costs(cores: int, vms: int = LICENSED_VMS, years: int = YEARS) -> dict[str, Any]:
    byol = cores * LICENSE_PER_CORE_PER_YEAR * years
    included = vms * LICENSE_INCLUDED_PER_HOUR * HOURS_PER_MONTH * 12 * years
    return {'HostCores': cores, 'BYOLLicenseCost': round(byol, 2), 'LicenseIncludedVMs': vms,
            'LicenseIncludedCost': round(included, 2),
            'Cheaper': 'BYOL on the Dedicated Host' if byol < included else 'License included'}


def ec2_month(hourly: float, storage_gb: int = 20, transfer_gb: int = SITE_TRANSFER_GB) -> dict[str, float]:
    compute = hourly * HOURS_PER_MONTH
    storage = storage_gb * EBS_GP3_PER_GB_MONTH
    transfer = max(0, transfer_gb - FREE_TRANSFER_GB) * TRANSFER_OUT_PER_GB
    return {'Compute': round(compute, 2), 'Storage': round(storage, 2), 'TransferOut': round(transfer, 2),
            'Total': round(compute + storage + transfer, 2)}


def pick_bundle(bundles: list[dict[str, Any]], memory_gb: float = 1.0, transfer_gb: int = SITE_TRANSFER_GB) -> dict[str, Any] | None:
    fits = [item for item in bundles if item.get('isActive', True) and item.get('ramSizeInGb', 0) >= memory_gb
            and item.get('transferPerMonthInGb', 0) >= transfer_gb]
    return min(fits, key=lambda item: item.get('price', math.inf)) if fits else None


# ── Lookups ──────────────────────────────────────────────────────────────────

def _prices() -> dict[str, float]:
    pricing = _client('pricing')
    prices = {}
    for instance_type in PRICED_TYPES:
        items = pricing.get_products(ServiceCode='AmazonEC2', Filters=[
            {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type},
            {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': LOCATION},
            {'Type': 'TERM_MATCH', 'Field': 'operatingSystem', 'Value': 'Linux'},
        ], MaxResults=10).get('PriceList', [])
        for item in items:
            price = on_demand_price(item)
            if price is not None:
                prices[instance_type] = price
                break
    return prices


def _estimates() -> list[dict[str, Any]]:
    try:
        return _client('bcm-pricing-calculator').list_workload_estimates().get('items', [])
    except ClientError:
        marker = _marked('pricing-calculator') or {}
        return [{'id': marker['id'], 'name': ESTIMATE}] if marker.get('id') else []


def _estimate() -> dict[str, Any] | None:
    for item in _estimates():
        if item.get('name') == ESTIMATE:
            try:
                return _client('bcm-pricing-calculator').get_workload_estimate(identifier=item['id'])
            except ClientError as exc:
                if 'NotFound' in _code(exc):
                    return None
                raise
    return None


def _launch_template() -> dict[str, Any] | None:
    try:
        items = _client('ec2').describe_launch_templates(LaunchTemplateNames=[LAUNCH_TEMPLATE]).get('LaunchTemplates', [])
    except ClientError as exc:
        if 'NotFound' in _code(exc) or _code(exc) == 'InvalidLaunchTemplateName.NotFoundException':
            return None
        raise
    return items[0] if items else None


def _asg() -> dict[str, Any] | None:
    groups = _client('autoscaling').describe_auto_scaling_groups(AutoScalingGroupNames=[ASG]).get('AutoScalingGroups', [])
    return groups[0] if groups else None


def _in_service(group: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [item for item in (group or {}).get('Instances') or [] if item.get('LifecycleState') == 'InService']


def _default_subnets() -> list[str]:
    subnets = _client('ec2').describe_subnets(Filters=[{'Name': 'default-for-az', 'Values': ['true']}]).get('Subnets', [])
    return [item['SubnetId'] for item in sorted(subnets, key=lambda item: item['AvailabilityZone'])][:2]


def _hosts() -> list[dict[str, Any]]:
    hosts = _client('ec2').describe_hosts(Filters=[{'Name': 'tag:Name', 'Values': [HOST_TAG]}]).get('Hosts', [])
    return [item for item in hosts if item.get('State') not in ('released', 'released-permanent-failure')]


def _cpu_stats() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    return _client('cloudwatch').get_metric_statistics(
        Namespace=METRIC_NAMESPACE, MetricName='CPUUtilization', Dimensions=[{'Name': 'Server', 'Value': REPORT_SERVER}],
        StartTime=now - timedelta(days=15), EndTime=now + timedelta(minutes=5), Period=86400,
        Statistics=['Average', 'Maximum']).get('Datapoints', [])


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_marker(step: str) -> tuple[bool, str]:
    marker = _marked(step)
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_prices() -> tuple[bool, str]:
    return _check_marker('price-list')


def _check_estimate() -> tuple[bool, str]:
    if not _estimate():
        return False, f'Create the {ESTIMATE} workload estimate.'
    return _check_marker('pricing-calculator')


def _check_tco() -> tuple[bool, str]:
    return _check_marker('tco-comparison')


def _check_elastic() -> tuple[bool, str]:
    group = _asg()
    if not group:
        return False, f'Create the {ASG} Auto Scaling group.'
    if group.get('MinSize') != MIN_SIZE or group.get('MaxSize') != MAX_SIZE:
        return False, f'{ASG} should scale between {MIN_SIZE} and {MAX_SIZE} instances.'
    if group.get('DesiredCapacity') != MIN_SIZE:
        return False, f'{ASG} wants {group.get("DesiredCapacity")} instances; scale back in to {MIN_SIZE} after the peak.'
    marker = _marked('elastic-capacity')
    if not marker:
        return False, f'Scale {ASG} out to {PEAK_SIZE} and back in to {MIN_SIZE}.'
    return True, marker['message']


def _check_rightsizing() -> tuple[bool, str]:
    if not _cpu_stats():
        return False, f'Publish CPU history for {REPORT_SERVER}.'
    return _check_marker('rightsizing')


def _check_byol() -> tuple[bool, str]:
    if _hosts():
        return False, f'Release the {HOST_TAG} Dedicated Host when you are done; hosts bill by the hour.'
    return _check_marker('byol-dedicated-host')


def _check_lightsail() -> tuple[bool, str]:
    return _check_marker('lightsail-vs-ec2')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_price_list() -> dict[str, Any]:
    started = time.perf_counter()
    pricing = _client('pricing')
    services = [item['ServiceCode'] for item in pricing.describe_services(MaxResults=100).get('Services', [])]
    types = [item['Value'] for item in pricing.get_attribute_values(
        ServiceCode='AmazonEC2', AttributeName='instanceType').get('AttributeValues', [])]
    prices = _prices()
    response = {'Services': services, 'InstanceTypes': types,
                'OnDemandLinuxUSDPerHour': prices,
                'MonthlyAt730Hours': {key: round(value * HOURS_PER_MONTH, 2) for key, value in prices.items()}}
    if 'm5.large' in prices:
        message = (f'An m5.large costs ${prices["m5.large"]:g} an hour On-Demand in {LOCATION} '
                   f'({_money(prices["m5.large"] * HOURS_PER_MONTH)} a month); {len(prices)} types have prices.')
        _marker('price-list', {'message': message, 'prices': prices})
    verified, check = _check_prices()
    if 'm5.large' not in prices:
        verified, check = False, 'The Price List returned no m5.large price.'
    return _result('price-list', PRICES_COMMAND, response, verified, check, started)


def step_pricing_calculator() -> dict[str, Any]:
    started = time.perf_counter()
    calculator = _client('bcm-pricing-calculator')
    estimate = _estimate()
    if not estimate:
        estimate = calculator.create_workload_estimate(name=ESTIMATE, rateType='BEFORE_DISCOUNTS')
        estimate.pop('ResponseMetadata', None)
    usage = [{'serviceCode': 'AmazonEC2', 'usageType': 'BoxUsage:m5.large', 'operation': 'RunInstances', 'key': 'web',
              'amount': CLOUD['average_instances'] * HOURS_PER_MONTH, 'group': 'web', 'usageAccountId': ACCOUNT}]
    try:
        lines: Any = calculator.batch_create_workload_estimate_usage(workloadEstimateId=estimate['id'], usage=usage)
        lines.pop('ResponseMetadata', None)
    except ClientError as exc:
        lines = {'Outcome': 'unsupported', 'Code': _code(exc)}
    _marker('pricing-calculator', {'id': estimate['id'], 'message': ''})
    prices = (_marked('price-list') or {}).get('prices') or _prices()
    hourly = prices.get('m5.large')
    estimate = _estimate() or estimate
    response = {'Estimate': {key: estimate.get(key) for key in ('id', 'name', 'status', 'rateType', 'totalCost')},
                'UsageLines': lines}
    if hourly:
        monthly = round(usage[0]['amount'] * hourly, 2)
        response['PricedByTheLab'] = {'web': {'Hours': usage[0]['amount'], 'USDPerHour': hourly, 'MonthlyUSD': monthly}}
        message = (f'{ESTIMATE} is saved; Floci leaves its total at ${estimate.get("totalCost", 0):g}, so the lab '
                   f'prices {usage[0]["amount"]:,} m5.large hours at {_money(monthly)} a month.')
        _marker('pricing-calculator', {'id': estimate['id'], 'message': message})
    verified, check = _check_estimate()
    if not hourly:
        verified, check = False, 'Look up the m5.large price first.'
    return _result('pricing-calculator', ESTIMATE_COMMAND, response, verified, check, started)


def step_tco() -> dict[str, Any]:
    started = time.perf_counter()
    prices = (_marked('price-list') or {}).get('prices') or _prices()
    hourly = prices.get('m5.large')
    if not hourly:
        return _result('tco-comparison', TCO_COMMAND, {}, False, 'Look up the m5.large price first.', started)
    model = tco(hourly)
    message = (f'Over {YEARS} years the data center costs {_money(model["OnPremisesTotal"])} '
               f'({_money(model["OnPremisesUpfront"])} up front) and the cloud {_money(model["CloudTotal"])} with '
               f'nothing up front: {model["SavingsPercent"]:g}% less, mostly from not buying for the peak and not '
               'running the facility.')
    _marker('tco-comparison', {'message': message})
    verified, check = _check_tco()
    return _result('tco-comparison', TCO_COMMAND, model, verified, check, started)


def step_elastic_capacity() -> dict[str, Any]:
    started = time.perf_counter()
    ec2, autoscaling = _client('ec2'), _client('autoscaling')
    if not _launch_template():
        ec2.create_launch_template(LaunchTemplateName=LAUNCH_TEMPLATE, LaunchTemplateData={
            'ImageId': IMAGE_ID, 'InstanceType': ASG_TYPE,
            'TagSpecifications': [{'ResourceType': 'instance', 'Tags': [LAB_TAG, {'Key': 'Name', 'Value': 'lab-econ-web'}]}]})
    if not _asg():
        autoscaling.create_auto_scaling_group(
            AutoScalingGroupName=ASG, LaunchTemplate={'LaunchTemplateName': LAUNCH_TEMPLATE, 'Version': '$Latest'},
            MinSize=MIN_SIZE, MaxSize=MAX_SIZE, DesiredCapacity=MIN_SIZE, VPCZoneIdentifier=','.join(_default_subnets()),
            Tags=[{**LAB_TAG, 'PropagateAtLaunch': True}])
    timeline = []

    def scale(desired: int, label: str) -> bool:
        autoscaling.set_desired_capacity(AutoScalingGroupName=ASG, DesiredCapacity=desired, HonorCooldown=False)
        ok = _wait(lambda: len(_in_service(_asg())) == desired and len((_asg() or {}).get('Instances') or []) == desired,
                   timeout=120, delay=2)
        timeline.append({'Moment': label, 'Desired': desired, 'InService': len(_in_service(_asg())),
                         'Seconds': round(time.perf_counter() - started, 1)})
        return ok

    ok = _wait(lambda: len(_in_service(_asg())) >= MIN_SIZE, timeout=120, delay=2)
    timeline.append({'Moment': 'Night', 'Desired': MIN_SIZE, 'InService': len(_in_service(_asg())),
                     'Seconds': round(time.perf_counter() - started, 1)})
    ok = ok and scale(PEAK_SIZE, 'Lunch peak') and scale(MIN_SIZE, 'After the peak')
    hours = elastic_hours()
    response = {'Timeline': timeline, 'SampleDay': hours,
                'Group': {key: (_asg() or {}).get(key) for key in ('MinSize', 'MaxSize', 'DesiredCapacity')}}
    if ok:
        prices = (_marked('price-list') or {}).get('prices') or {}
        hourly = prices.get(ASG_TYPE)
        saved = f' (about {_money((hours["FixedForPeakHours"] - hours["ElasticHours"]) * hourly * 365)} a year at {ASG_TYPE} prices)' \
            if hourly else ''
        _marker('elastic-capacity', {'message': (
            f'{ASG} went from {MIN_SIZE} to {PEAK_SIZE} instances and back in {timeline[-1]["Seconds"]:g} s. On the '
            f'sample day, following demand needs {hours["ElasticHours"]} instance-hours instead of '
            f'{hours["FixedForPeakHours"]} for a fixed peak fleet: {hours["SavedPercent"]:g}% fewer{saved}.')})
    verified, check = _check_elastic()
    return _result('elastic-capacity', ELASTIC_COMMAND, response, verified, check, started)


def step_rightsizing() -> dict[str, Any]:
    started = time.perf_counter()
    cloudwatch = _client('cloudwatch')
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    samples = cpu_samples()
    data = [{'MetricName': 'CPUUtilization', 'Dimensions': [{'Name': 'Server', 'Value': REPORT_SERVER}],
             'Timestamp': now - timedelta(hours=len(samples) - index), 'Value': value, 'Unit': 'Percent'}
            for index, value in enumerate(samples)]
    # 20 data points per call: Floci 2.3.0 fails to parse much larger PutMetricData requests.
    for offset in range(0, len(data), 20):
        cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=data[offset:offset + 20])
    stats = sorted(_cpu_stats(), key=lambda item: item['Timestamp'])
    peak = max((item.get('Maximum', 0) for item in stats), default=0)
    average = round(sum(item.get('Average', 0) for item in stats) / len(stats), 1) if stats else 0
    try:
        optimizer: Any = _client('compute-optimizer').get_ec2_instance_recommendations()
        optimizer.pop('ResponseMetadata', None)
    except (ClientError, BotoCoreError) as exc:
        optimizer = {'Outcome': 'unsupported', 'Code': _code(exc) if isinstance(exc, ClientError) else type(exc).__name__}
    response: dict[str, Any] = {'Server': REPORT_SERVER, 'CurrentType': REPORT_TYPE, 'Days': len(samples) // 24,
                                'AverageCPU': average, 'PeakCPU': peak, 'ComputeOptimizer': optimizer}
    if stats:
        prices = (_marked('price-list') or {}).get('prices') or {}
        target = recommend_size(peak, REPORT_TYPE)
        current_price, current_source = price_for(REPORT_TYPE, prices)
        target_price, target_source = price_for(target, prices)
        saving = (current_price - target_price) * HOURS_PER_MONTH * 12
        response['Recommendation'] = {'Type': target, 'CurrentUSDPerHour': current_price, 'CurrentPriceSource': current_source,
                                      'TargetUSDPerHour': target_price, 'TargetPriceSource': target_source,
                                      'YearlySavingUSD': round(saving, 2)}
        _marker('rightsizing', {'message': (
            f'{REPORT_SERVER} ({REPORT_TYPE}, 8 vCPU) averaged {average:g}% CPU and peaked at {peak:g}% over '
            f'{len(samples) // 24} days, so {"an" if target[0] in "aeio" else "a"} {target} keeps the peak under {CPU_TARGET_PEAK:g}% and saves about '
            f'{_money(saving)} a year.')})
    verified, check = _check_rightsizing()
    return _result('rightsizing', RIGHTSIZE_COMMAND, response, verified, check, started)


def step_byol() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    hosts = _hosts()
    if not hosts:
        host_ids = ec2.allocate_hosts(InstanceType=HOST_TYPE, AvailabilityZone=f'{REGION}a', Quantity=1,
                                      TagSpecifications=[{'ResourceType': 'dedicated-host',
                                                          'Tags': [LAB_TAG, {'Key': 'Name', 'Value': HOST_TAG}]}])['HostIds']
        hosts = ec2.describe_hosts(HostIds=host_ids).get('Hosts', [])
    host = hosts[0] if hosts else {}
    properties = host.get('HostProperties') or {}
    cores, sockets = int(properties.get('Cores') or 0), int(properties.get('Sockets') or 0)
    released: Any = None
    if host.get('HostId'):
        released = ec2.release_hosts(HostIds=[host['HostId']])
        released.pop('ResponseMetadata', None)
    response = {'Host': {'HostId': host.get('HostId'), 'InstanceType': properties.get('InstanceType'),
                         'Sockets': sockets, 'Cores': cores, 'TotalVCpus': properties.get('TotalVCpus')},
                'Released': released}
    if cores:
        costs = license_costs(cores)
        response['Licensing'] = costs
        _marker('byol-dedicated-host', {'message': (
            f'The Dedicated Host shows {sockets} sockets and {cores} cores, so per-core licenses cost '
            f'{_money(costs["BYOLLicenseCost"])} over {YEARS} years against {_money(costs["LicenseIncludedCost"])} '
            f'license-included for {LICENSED_VMS} VMs: {costs["Cheaper"].lower()} is cheaper. The host was released.')})
    verified, check = _check_byol()
    return _result('byol-dedicated-host', BYOL_COMMAND, response, verified, check, started)


def step_lightsail() -> dict[str, Any]:
    started = time.perf_counter()
    bundles = _client('lightsail').get_bundles().get('bundles', [])
    prices = (_marked('price-list') or {}).get('prices') or _prices()
    hourly = prices.get('t3.micro')
    bundle = pick_bundle(bundles)
    response: dict[str, Any] = {'Bundles': [{key: item.get(key) for key in (
        'bundleId', 'price', 'cpuCount', 'ramSizeInGb', 'diskSizeInGb', 'transferPerMonthInGb')} for item in bundles]}
    if bundle and hourly:
        ec2 = ec2_month(hourly)
        response['Comparison'] = {'Lightsail': {'Bundle': bundle['bundleId'], 'MonthlyUSD': bundle['price']},
                                  'EC2': ec2, 'SiteTransferGB': SITE_TRANSFER_GB}
        cheaper = 'Lightsail' if bundle['price'] < ec2['Total'] else 'EC2'
        _marker('lightsail-vs-ec2', {'message': (
            f'For a small site serving {SITE_TRANSFER_GB:,} GB a month, the {bundle["bundleId"]} bundle is a fixed '
            f'${bundle["price"]:g}; t3.micro on EC2 costs ${ec2["Total"]:g} (${ec2["TransferOut"]:g} of it data '
            f'transfer out). {cheaper} is cheaper here; EC2 wins when you need other instance types or services.')})
    verified, check = _check_lightsail()
    if not bundle or not hourly:
        verified, check = False, 'No Lightsail bundle or t3.micro price was found.'
    return _result('lightsail-vs-ec2', LIGHTSAIL_COMMAND, response, verified, check, started)


RUNNERS = {
    'price-list': step_price_list,
    'pricing-calculator': step_pricing_calculator,
    'tco-comparison': step_tco,
    'elastic-capacity': step_elastic_capacity,
    'rightsizing': step_rightsizing,
    'byol-dedicated-host': step_byol,
    'lightsail-vs-ec2': step_lightsail,
}


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check()[0])
    except (ClientError, BotoCoreError, KeyError, TypeError):
        return False


CHECKS = {
    'price-list': _check_prices,
    'pricing-calculator': _check_estimate,
    'tco-comparison': _check_tco,
    'elastic-capacity': _check_elastic,
    'rightsizing': _check_rightsizing,
    'byol-dedicated-host': _check_byol,
    'lightsail-vs-ec2': _check_lightsail,
}


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
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


def reset(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    """Delete the Auto Scaling group (and its instances), the launch template, any Dedicated Host left allocated,
    and the Pricing Calculator estimate. Metric data cannot be deleted and ages out."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code in ('ValidationError',):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    ec2, autoscaling, calculator = _client('ec2'), _client('autoscaling'), _client('bcm-pricing-calculator')
    try:
        group = _asg()
    except (ClientError, BotoCoreError) as exc:
        group = None
        errors.append(f'asg lookup: {exc}')
    if group:
        try:
            autoscaling.delete_auto_scaling_group(AutoScalingGroupName=ASG, ForceDelete=True)
        except (ClientError, BotoCoreError):
            pass  # Floci can take longer than the client's read timeout to stop the instances; check below.
        if _wait(lambda: _asg() is None, timeout=120, delay=2):
            deleted.append(f'auto-scaling-group:{ASG}')
        else:
            errors.append(f'auto-scaling-group:{ASG}: still reported after delete')
    try:
        template = _launch_template()
    except (ClientError, BotoCoreError):
        template = None
    if template:
        attempt(f'launch-template:{LAUNCH_TEMPLATE}', lambda: ec2.delete_launch_template(LaunchTemplateName=LAUNCH_TEMPLATE))
    try:
        hosts = _hosts()
    except (ClientError, BotoCoreError):
        hosts = []
    if hosts:
        ids = [item['HostId'] for item in hosts]
        attempt(f'hosts:{",".join(ids)}', lambda: ec2.release_hosts(HostIds=ids))
    try:
        estimate = _estimate()
    except (ClientError, BotoCoreError):
        estimate = None
    if estimate:
        attempt(f'estimate:{ESTIMATE}', lambda: calculator.delete_workload_estimate(identifier=estimate['id']))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws autoscaling delete-auto-scaling-group --auto-scaling-group-name {ASG} --force-delete\n'
            f'aws ec2 delete-launch-template --launch-template-name {LAUNCH_TEMPLATE}\n'
            'aws ec2 release-hosts --host-ids <host-id>\n'
            'aws bcm-pricing-calculator delete-workload-estimate --identifier <id>'
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
            else 'The Auto Scaling group, launch template, Dedicated Host and estimate are gone.',
        },
    }
