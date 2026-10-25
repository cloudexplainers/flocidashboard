"""Cost visibility lab: a cost allocation tag strategy with an audit for untagged resources, AWS Budgets with actual and
forecast alerts, Cost Explorer by service and by tag with a forecast, a Cost Anomaly Detection monitor, Price List API
lookups, a Spot request priced against On-Demand, and a purchasing options chooser.

Floci 2.3.0 stores budgets (with notifications and subscribers), anomaly monitors and subscriptions, answers
GetCostAndUsage with synthetic near-zero amounts, serves On-Demand prices from the Price List API, reports synthetic
Spot prices, and fulfills Spot requests whatever the bid. Budgets never alert, and forecasts, anomalies, cost
allocation tag activation, Reserved Instance offerings and Savings Plans are unsupported, so the lab evaluates them
from sample spend and says so.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import date, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'costexplorer'
LAB_KEY = 'cost-visibility'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
LAB_TAG = {'Key': 'Lab', 'Value': LAB_KEY}

PLATFORM_BUCKET = 'lab-cost-platform-artifacts'
ANALYTICS_BUCKET = 'lab-cost-analytics-raw'
ORPHAN_QUEUE = 'lab-cost-orphan-jobs'
REQUIRED_TAGS = ('CostCenter', 'Environment')
RESOURCE_TAGS = {
    f'arn:aws:s3:::{PLATFORM_BUCKET}': {'CostCenter': 'platform', 'Environment': 'prod'},
    f'arn:aws:s3:::{ANALYTICS_BUCKET}': {'CostCenter': 'analytics', 'Environment': 'prod'},
    f'arn:aws:sqs:{REGION}:{ACCOUNT}:{ORPHAN_QUEUE}': {'CostCenter': 'platform', 'Environment': 'dev'},
}

TOPIC = 'lab-cost-alerts'
FINOPS_EMAIL = 'finops@example.com'
BUDGET = 'lab-cost-platform-monthly'
BUDGET_LIMIT = 100.0
BUDGET_FILTER = {'TagKeyValue': ['user:CostCenter$platform']}
NOTIFICATIONS = [
    {'NotificationType': 'ACTUAL', 'ComparisonOperator': 'GREATER_THAN', 'Threshold': 80.0,
     'ThresholdType': 'PERCENTAGE'},
    {'NotificationType': 'FORECASTED', 'ComparisonOperator': 'GREATER_THAN', 'Threshold': 100.0,
     'ThresholdType': 'PERCENTAGE'},
]
RI_BUDGET = 'lab-cost-ri-utilization'

MONITOR = 'lab-cost-platform-monitor'
SUBSCRIPTION = 'lab-cost-anomaly-alerts'
ANOMALY_THRESHOLD = 50.0

# Platform team's daily spend for the first 20 days of a 30-day month, then a runaway day 21.
DAYS_IN_MONTH = 30
SAMPLE_DAILY = [3.10, 3.25, 3.40, 3.30, 3.15, 2.90, 2.85, 3.20, 3.35, 3.45,
                3.30, 3.25, 3.10, 2.95, 2.80, 3.40, 3.55, 3.60, 3.50, 3.65]
SPIKE_DAY = 61.40

INSTANCE_TYPES = ['t3.micro', 'm5.large']
SPOT_TYPE = 't3.micro'
SPOT_IMAGE = 'ami-ubuntu-22.04'
LOW_BID = '0.001'

HOURS_PER_MONTH = 730
# Typical us-east-1 discounts off On-Demand for m5.large Linux; Floci has no RI offerings or Savings Plans API.
OPTIONS = [
    ('On-Demand', 0.0, 'none', 'Any instance, any time; pay by the second'),
    ('Compute Savings Plan, 1 year, no upfront', 0.28, '1 year of $/hour', 'Any family, size, Region, OS; also Fargate and Lambda'),
    ('EC2 Instance Savings Plan, 1 year, no upfront', 0.37, '1 year of $/hour', 'One family in one Region; any size, OS, AZ'),
    ('Standard RI, 1 year, no upfront', 0.37, '1 year, one instance type', 'Can be sold on the RI Marketplace; zonal RIs reserve capacity'),
    ('Convertible RI, 3 years, all upfront', 0.48, '3 years, exchangeable', 'Exchange for another family, OS or tenancy'),
    ('Standard RI, 3 years, all upfront', 0.60, '3 years, one instance type', 'Deepest commitment discount'),
    ('Spot', 0.70, 'none; 2-minute interruption notice', 'Fault-tolerant, flexible work only'),
]
WORKLOADS = [
    ('A web tier that runs 24x7 for three years and will not change', 'Standard RI, 3 years, all upfront'),
    ('Steady compute that may move from EC2 to Fargate and Lambda next year', 'Compute Savings Plan, 1 year, no upfront'),
    ('Steady m5 fleet in one Region whose sizes change often', 'EC2 Instance Savings Plan, 1 year, no upfront'),
    ('Nightly video transcoding that can restart if interrupted', 'Spot'),
    ('A two-week load test, then nothing', 'On-Demand'),
    ('Software licensed per physical socket', 'Dedicated Host'),
    ('Guaranteed capacity in one AZ for a launch day, no long commitment', 'On-Demand Capacity Reservation'),
    ('Steady workload for three years that may switch instance family', 'Convertible RI, 3 years, all upfront'),
]

TAGS_COMMAND = (
    f'aws s3api put-bucket-tagging --bucket {PLATFORM_BUCKET} --tagging '
    "'TagSet=[{Key=CostCenter,Value=platform},{Key=Environment,Value=prod}]'\n"
    f'aws sqs create-queue --queue-name {ORPHAN_QUEUE} --tags Environment=dev\n'
    'aws resourcegroupstaggingapi get-resources --tag-filters Key=Lab,Values=cost-visibility\n'
    f'aws sqs tag-queue --queue-url <{ORPHAN_QUEUE}-url> --tags CostCenter=platform\n'
    'aws ce update-cost-allocation-tags-status --cost-allocation-tags-status TagKey=CostCenter,Status=Active'
)
BUDGET_COMMAND = (
    f'aws sns create-topic --name {TOPIC}\n'
    f'aws budgets create-budget --account-id {ACCOUNT} --budget file://platform-budget.json '
    '--notifications-with-subscribers file://alerts.json   # ACTUAL > 80%, FORECASTED > 100%\n'
    f'aws budgets create-budget --account-id {ACCOUNT} --budget BudgetName={RI_BUDGET},BudgetType=RI_UTILIZATION,'
    'TimeUnit=MONTHLY,BudgetLimit={Amount=80,Unit=PERCENTAGE}'
)
EXPLORER_COMMAND = (
    'aws ce get-cost-and-usage --time-period Start=<first-of-last-month>,End=<first-of-this-month> '
    '--granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE\n'
    'aws ce get-cost-and-usage ... --group-by Type=TAG,Key=CostCenter\n'
    'aws ce get-cost-forecast --time-period Start=<today>,End=<first-of-next-month> --metric UNBLENDED_COST '
    '--granularity MONTHLY'
)
ANOMALY_COMMAND = (
    f'aws ce create-anomaly-monitor --anomaly-monitor MonitorName={MONITOR},MonitorType=CUSTOM,'
    "'MonitorSpecification={Tags={Key=CostCenter,Values=[platform]}}'\n"
    f'aws ce create-anomaly-subscription --anomaly-subscription file://anomaly-alerts.json   # >= ${ANOMALY_THRESHOLD:g} impact to SNS\n'
    'aws ce get-anomalies --date-interval StartDate=<first-of-month>'
)
PRICING_COMMAND = (
    'aws pricing get-products --region us-east-1 --service-code AmazonEC2 --filters '
    'Type=TERM_MATCH,Field=instanceType,Value=m5.large Type=TERM_MATCH,Field=location,Value="US East (N. Virginia)" '
    'Type=TERM_MATCH,Field=operatingSystem,Value=Linux\n'
    'aws ec2 describe-reserved-instances-offerings --instance-type m5.large'
)
SPOT_COMMAND = (
    f'aws ec2 describe-spot-price-history --instance-types {SPOT_TYPE} --product-descriptions Linux/UNIX\n'
    f'aws ec2 request-spot-instances --spot-price <on-demand-price> --launch-specification '
    f'ImageId={SPOT_IMAGE},InstanceType={SPOT_TYPE}\n'
    'aws ec2 cancel-spot-instance-requests --spot-instance-request-ids <sir-id>\n'
    'aws ec2 terminate-instances --instance-ids <instance-id>'
)
OPTIONS_COMMAND = '# Price three years of one m5.large under each purchasing option, then pick one per workload'

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Track and control AWS costs with tags, Budgets, Cost Explorer, and purchasing options',
    'description': (
        'Tag resources by cost center and find the untagged one, alert on actual and forecast spend with AWS '
        'Budgets, break costs down by service and tag in Cost Explorer, catch a spending spike with Cost Anomaly '
        'Detection, look up On-Demand prices, price a Spot request, and choose a purchasing option for each '
        'workload.'
    ),
    'steps': [
        {
            'key': 'tag-strategy',
            'title': 'Tag resources by cost center and fix the untagged one',
            'command': TAGS_COMMAND,
            'explanation': (
                'Costs can only be split by team if resources carry the same tags (here CostCenter and '
                'Environment), so audit for missing ones with the Resource Groups Tagging API and enforce them with '
                'tag policies. User-defined tags must be activated as cost allocation tags before they appear in '
                'Cost Explorer and the Cost and Usage Report, and only from then on. How Floci emulates this: '
                'tags and the tagging API work; activating cost allocation tags is unsupported, so the step records '
                'the call.'
            ),
        },
        {
            'key': 'budget-alerts',
            'title': 'Alert on actual and forecast spend with AWS Budgets',
            'command': BUDGET_COMMAND,
            'explanation': (
                'A cost budget for the platform team (filtered by its CostCenter tag) emails and notifies SNS when '
                'actual spend passes 80% of $100 or forecast spend passes 100%. Budgets can also track usage, RI '
                'and Savings Plans utilization and coverage, and run actions such as applying a deny policy. How '
                'Floci emulates this: budgets, notifications and subscribers are stored, but spend stays at $0 and '
                'nothing alerts, so the step evaluates the alerts against sample spend.'
            ),
        },
        {
            'key': 'cost-explorer',
            'title': 'Break down costs by service and by tag, and forecast the month',
            'command': EXPLORER_COMMAND,
            'explanation': (
                'Cost Explorer groups up to 13 months of spend by service, account, Region, usage type or tag, and '
                'forecasts the rest of the month. Unblended cost is what each line item cost; amortized cost spreads '
                'upfront RI and Savings Plans fees over their term. How Floci emulates this: GetCostAndUsage returns '
                'synthetic near-zero amounts and forecasts are unsupported, so the step forecasts from sample daily '
                'spend.'
            ),
        },
        {
            'key': 'anomaly-monitor',
            'title': 'Catch a spending spike with Cost Anomaly Detection',
            'command': ANOMALY_COMMAND,
            'explanation': (
                'Cost Anomaly Detection learns normal spend for a monitor (here everything tagged '
                'CostCenter=platform; an account may have only one AWS services monitor) and alerts subscribers '
                'immediately, daily or weekly when an anomaly\'s impact passes a threshold. How Floci emulates this: '
                'the monitor and subscription are stored, but GetAnomalies is unsupported, so the step finds the '
                'spike in sample spend.'
            ),
        },
        {
            'key': 'pricing-lookup',
            'title': 'Look up On-Demand prices with the Price List API',
            'command': PRICING_COMMAND,
            'explanation': (
                'The Price List API returns every SKU with its On-Demand and Reserved terms, filtered by attributes '
                'such as instance type, location and operating system (the endpoint is in us-east-1). The Pricing '
                'Calculator uses the same data to estimate an architecture before you build it. How Floci emulates '
                'this: On-Demand prices for common instance types are served; Reserved Instance offerings are '
                'unsupported.'
            ),
        },
        {
            'key': 'spot-request',
            'title': 'Request a Spot Instance and compare its price with On-Demand',
            'command': SPOT_COMMAND,
            'explanation': (
                'Spot uses spare capacity for up to 90% off; you pay the current Spot price (capped by an optional '
                'maximum) and AWS can reclaim the instance with a two-minute warning, so use it for stateless, '
                'checkpointed or batch work, ideally through an Auto Scaling group or EC2 Fleet with several '
                'instance types. How Floci emulates this: Spot prices are synthetic and every request is fulfilled '
                'whatever the bid, even one below the market; the step cancels both requests and terminates the '
                'instances straight away.'
            ),
        },
        {
            'key': 'purchasing-options',
            'title': 'Choose a purchasing option for each workload',
            'command': OPTIONS_COMMAND,
            'explanation': (
                'Savings Plans commit to a $/hour spend for 1 or 3 years (Compute: any family, Region, Fargate or '
                'Lambda; EC2 Instance: one family in one Region). Standard RIs give the deepest discount for one '
                'instance type; Convertible RIs can be exchanged. Paying all upfront saves more than no upfront. '
                'Dedicated Hosts suit per-socket licenses, and Capacity Reservations guarantee capacity without a '
                'term. How Floci emulates this: there are no RI offerings or Savings Plans, so the step prices the '
                'options with typical discounts off the On-Demand price.'
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


def _unsupported(call) -> dict[str, Any]:
    try:
        response = call()
        if isinstance(response, dict):
            response.pop('ResponseMetadata', None)
        return {'Outcome': 'answered', 'Response': response}
    except ClientError as exc:
        return {'Outcome': 'unsupported', 'Code': _code(exc)}
    except BotoCoreError as exc:
        return {'Outcome': 'unsupported', 'Code': type(exc).__name__}


# ── Pure logic ───────────────────────────────────────────────────────────────

def missing_tags(resources: dict[str, dict[str, str]], required: tuple[str, ...] = REQUIRED_TAGS) -> dict[str, list[str]]:
    return {arn: [key for key in required if not tags.get(key)]
            for arn, tags in resources.items() if any(not tags.get(key) for key in required)}


def forecast(daily: list[float], days_in_month: int = DAYS_IN_MONTH) -> float:
    """Straight-line month-end forecast from month-to-date spend."""
    if not daily:
        return 0.0
    return round(sum(daily) / len(daily) * days_in_month, 2)


def budget_alerts(limit: float, actual: float, forecasted: float,
                  notifications: list[dict[str, Any]] = NOTIFICATIONS) -> list[str]:
    fired = []
    for item in notifications:
        value = actual if item['NotificationType'] == 'ACTUAL' else forecasted
        threshold = limit * item['Threshold'] / 100 if item['ThresholdType'] == 'PERCENTAGE' else item['Threshold']
        if item['ComparisonOperator'] == 'GREATER_THAN' and value > threshold:
            fired.append(f'{item["NotificationType"]} > {item["Threshold"]:g}%')
    return fired


def find_anomalies(history: list[float], days: list[float], threshold: float = ANOMALY_THRESHOLD) -> list[dict[str, Any]]:
    """Flag a day whose spend is far above normal (mean + 3 standard deviations) and whose impact passes the
    subscription threshold."""
    mean = statistics.fmean(history)
    spread = statistics.pstdev(history)
    found = []
    for offset, spend in enumerate(days, start=len(history) + 1):
        impact = round(spend - mean, 2)
        if spend > mean + 3 * spread:
            found.append({'Day': offset, 'Spend': spend, 'Expected': round(mean, 2), 'Impact': impact,
                          'Alerts': impact >= threshold})
    return found


def on_demand_price(price_item: str) -> float | None:
    product = json.loads(price_item)
    for term in (product.get('terms') or {}).get('OnDemand', {}).values():
        for dimension in term.get('priceDimensions', {}).values():
            usd = (dimension.get('pricePerUnit') or {}).get('USD')
            if usd is not None:
                return float(usd)
    return None


def option_costs(hourly: float, years: int = 3, spot_discount: float | None = None) -> list[dict[str, Any]]:
    hours = HOURS_PER_MONTH * 12 * years
    base = hourly * hours
    rows = []
    for name, discount, commitment, notes in OPTIONS:
        if name == 'Spot' and spot_discount is not None:
            discount = spot_discount
        rows.append({'Option': name, 'DiscountVsOnDemand': f'{discount:.0%}', 'Commitment': commitment,
                     'Notes': notes, f'{years}YearCostUSD': round(base * (1 - discount), 2)})
    return rows


# ── Lookups ──────────────────────────────────────────────────────────────────

def _tagged_resources() -> dict[str, dict[str, str]]:
    mappings = _client('resourcegroupstaggingapi').get_resources(
        TagFilters=[{'Key': 'Lab', 'Values': [LAB_KEY]}]).get('ResourceTagMappingList', [])
    return {item['ResourceARN']: {tag['Key']: tag['Value'] for tag in item.get('Tags', [])} for item in mappings}


def _queue_url() -> str | None:
    try:
        return _client('sqs').get_queue_url(QueueName=ORPHAN_QUEUE)['QueueUrl']
    except ClientError as exc:
        if 'NonExistentQueue' in _code(exc) or _code(exc) == 'QueueDoesNotExist':
            return None
        raise


def _topic_arn() -> str | None:
    topics = _client('sns').list_topics().get('Topics', [])
    return next((item['TopicArn'] for item in topics if item['TopicArn'].endswith(f':{TOPIC}')), None)


def _budget(name: str) -> dict[str, Any] | None:
    try:
        return _client('budgets').describe_budget(AccountId=ACCOUNT, BudgetName=name)['Budget']
    except ClientError as exc:
        if _code(exc) == 'NotFoundException':
            return None
        raise


def _notifications() -> list[dict[str, Any]]:
    return _client('budgets').describe_notifications_for_budget(AccountId=ACCOUNT, BudgetName=BUDGET).get(
        'Notifications', [])


def _subscribers(notification: dict[str, Any]) -> list[dict[str, Any]]:
    return _client('budgets').describe_subscribers_for_notification(
        AccountId=ACCOUNT, BudgetName=BUDGET, Notification=notification).get('Subscribers', [])


def _monitor() -> dict[str, Any] | None:
    monitors = _client('ce').get_anomaly_monitors().get('AnomalyMonitors', [])
    return next((item for item in monitors if item.get('MonitorName') == MONITOR), None)


def _subscription() -> dict[str, Any] | None:
    subscriptions = _client('ce').get_anomaly_subscriptions().get('AnomalySubscriptions', [])
    return next((item for item in subscriptions if item.get('SubscriptionName') == SUBSCRIPTION), None)


def _spot_requests() -> list[dict[str, Any]]:
    return _client('ec2').describe_spot_instance_requests(Filters=[
        {'Name': 'tag:Lab', 'Values': [LAB_KEY]}]).get('SpotInstanceRequests', [])


def _instance_state(instance_id: str) -> str | None:
    try:
        reservations = _client('ec2').describe_instances(InstanceIds=[instance_id]).get('Reservations', [])
    except ClientError as exc:
        if 'NotFound' in _code(exc):
            return None
        raise
    instances = [item for reservation in reservations for item in reservation.get('Instances', [])]
    return instances[0]['State']['Name'] if instances else None


def _price(instance_type: str) -> float | None:
    items = _client('pricing').get_products(ServiceCode='AmazonEC2', Filters=[
        {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': instance_type},
        {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': 'US East (N. Virginia)'},
        {'Type': 'TERM_MATCH', 'Field': 'operatingSystem', 'Value': 'Linux'},
    ], MaxResults=10).get('PriceList', [])
    prices = [price for price in (on_demand_price(item) for item in items) if price is not None]
    return min(prices) if prices else None


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_tags() -> tuple[bool, str]:
    resources = _tagged_resources()
    for arn, expected in RESOURCE_TAGS.items():
        tags = resources.get(arn)
        if tags is None:
            return False, f'{arn.rsplit(":", 1)[-1]} was not found with the Lab tag.'
        wrong = {key: value for key, value in expected.items() if tags.get(key) != value}
        if wrong:
            return False, f'{arn.rsplit(":", 1)[-1]} needs {wrong}.'
    return True, ('Both buckets and the queue carry CostCenter and Environment; the queue was missing CostCenter and '
                  'now belongs to platform.')


def _check_budget() -> tuple[bool, str]:
    budget = _budget(BUDGET)
    if not budget:
        return False, f'Budget {BUDGET} was not found.'
    limit = budget.get('BudgetLimit') or {}
    if budget.get('BudgetType') != 'COST' or budget.get('TimeUnit') != 'MONTHLY' or \
            float(limit.get('Amount', 0)) != BUDGET_LIMIT or limit.get('Unit') != 'USD':
        return False, f'{BUDGET} must be a monthly ${BUDGET_LIMIT:g} cost budget.'
    if (budget.get('CostFilters') or {}) != BUDGET_FILTER:
        return False, f'{BUDGET} must cover only CostCenter=platform.'
    notifications = _notifications()
    topic = _topic_arn()
    for expected in NOTIFICATIONS:
        found = next((item for item in notifications if item['NotificationType'] == expected['NotificationType']
                      and float(item['Threshold']) == expected['Threshold']), None)
        if not found:
            return False, f'{BUDGET} needs a {expected["NotificationType"]} alert above {expected["Threshold"]:g}%.'
        addresses = {item['Address'] for item in _subscribers(found)}
        if topic not in addresses:
            return False, f'The {expected["NotificationType"]} alert must notify {TOPIC}.'
    if not _budget(RI_BUDGET):
        return False, f'Budget {RI_BUDGET} was not found.'
    return True, (f'{BUDGET} (${BUDGET_LIMIT:g} a month for CostCenter=platform) alerts {TOPIC} and {FINOPS_EMAIL} on '
                  f'actual spend above 80% and forecast spend above 100%; {RI_BUDGET} tracks RI utilization.')


def _check_marker(step: str, prerequisite=None) -> tuple[bool, str]:
    if prerequisite:
        ok, problem = prerequisite()
        if not ok:
            return False, problem
    marker = _marked(step)
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_explorer() -> tuple[bool, str]:
    return _check_marker('cost-explorer', _check_tags)


def _check_anomaly() -> tuple[bool, str]:
    monitor = _monitor()
    if not monitor or monitor.get('MonitorType') != 'CUSTOM' or \
            (monitor.get('MonitorSpecification') or {}).get('Tags') != {'Key': 'CostCenter', 'Values': ['platform']}:
        return False, f'Custom monitor {MONITOR} on CostCenter=platform was not found.'
    subscription = _subscription()
    if not subscription or monitor['MonitorArn'] not in subscription.get('MonitorArnList', []):
        return False, f'Subscription {SUBSCRIPTION} must watch {MONITOR}.'
    if {'Type': 'SNS', 'Address': _topic_arn()} not in [
            {'Type': item.get('Type'), 'Address': item.get('Address')} for item in subscription.get('Subscribers', [])]:
        return False, f'{SUBSCRIPTION} must notify {TOPIC}.'
    values = ((subscription.get('ThresholdExpression') or {}).get('Dimensions') or {}).get('Values') or []
    if values != [f'{ANOMALY_THRESHOLD:g}'] or subscription.get('Frequency') != 'IMMEDIATE':
        return False, f'{SUBSCRIPTION} must alert immediately on an impact of ${ANOMALY_THRESHOLD:g} or more.'
    return _check_marker('anomaly-monitor')


def _check_pricing() -> tuple[bool, str]:
    return _check_marker('pricing-lookup')


def _check_spot() -> tuple[bool, str]:
    marker = _marked('spot-request')
    if not marker:
        return False, 'Run this step.'
    requests = {item['SpotInstanceRequestId']: item for item in _spot_requests()}
    for request_id in marker['RequestIds']:
        request = requests.get(request_id)
        if request and request.get('State') in ('open', 'active'):
            return False, f'Cancel {request_id}.'
        instance_id = (request or {}).get('InstanceId')
        if instance_id and _instance_state(instance_id) in ('pending', 'running', 'stopping', 'stopped'):
            return False, f'Terminate {instance_id}.'
    return True, marker['message']


def _check_options() -> tuple[bool, str]:
    return _check_marker('purchasing-options', _check_pricing)


# ── Steps ────────────────────────────────────────────────────────────────────

def _create_bucket(name: str, tags: dict[str, str]) -> None:
    s3 = _client('s3')
    try:
        s3.create_bucket(Bucket=name)
    except ClientError as exc:
        if _code(exc) not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            raise
    tag_set = [{'Key': key, 'Value': value} for key, value in {**tags, 'Lab': LAB_KEY}.items()]
    s3.put_bucket_tagging(Bucket=name, Tagging={'TagSet': tag_set})


def step_tag_strategy() -> dict[str, Any]:
    started = time.perf_counter()
    sqs = _client('sqs')
    _create_bucket(PLATFORM_BUCKET, RESOURCE_TAGS[f'arn:aws:s3:::{PLATFORM_BUCKET}'])
    _create_bucket(ANALYTICS_BUCKET, RESOURCE_TAGS[f'arn:aws:s3:::{ANALYTICS_BUCKET}'])
    queue_url = _queue_url() or sqs.create_queue(QueueName=ORPHAN_QUEUE,
                                                 tags={'Environment': 'dev', 'Lab': LAB_KEY})['QueueUrl']
    before = missing_tags(_tagged_resources())
    sqs.tag_queue(QueueUrl=queue_url, Tags={'CostCenter': 'platform'})
    after = missing_tags(_tagged_resources())
    activation = _unsupported(lambda: _client('ce').update_cost_allocation_tags_status(
        CostAllocationTagsStatus=[{'TagKey': 'CostCenter', 'Status': 'Active'},
                                  {'TagKey': 'Environment', 'Status': 'Active'}]))
    response = {'MissingBeforeFix': before, 'MissingAfterFix': after, 'Resources': _tagged_resources(),
                'ActivateCostAllocationTags': {key: activation.get(key) for key in ('Outcome', 'Code')}}
    verified, message = _check_tags()
    if verified and not before:
        verified, message = False, 'The audit should have found the queue without CostCenter.'
    elif verified and activation['Outcome'] != 'answered':
        message += ' Floci does not support activating cost allocation tags, so the step recorded the call.'
    return _result('tag-strategy', TAGS_COMMAND, response, verified, message, started)


def step_budget_alerts() -> dict[str, Any]:
    started = time.perf_counter()
    budgets, sns = _client('budgets'), _client('sns')
    topic = sns.create_topic(Name=TOPIC, Tags=[LAB_TAG])['TopicArn']
    definition = {'BudgetName': BUDGET, 'BudgetLimit': {'Amount': f'{BUDGET_LIMIT:g}', 'Unit': 'USD'},
                  'TimeUnit': 'MONTHLY', 'BudgetType': 'COST', 'CostFilters': BUDGET_FILTER}
    if _budget(BUDGET):
        budgets.delete_budget(AccountId=ACCOUNT, BudgetName=BUDGET)
    budgets.create_budget(AccountId=ACCOUNT, Budget=definition, NotificationsWithSubscribers=[
        {'Notification': notification,
         'Subscribers': [{'SubscriptionType': 'SNS', 'Address': topic}]
         + ([{'SubscriptionType': 'EMAIL', 'Address': FINOPS_EMAIL}] if notification['NotificationType'] == 'ACTUAL'
            else [])}
        for notification in NOTIFICATIONS])
    if not _budget(RI_BUDGET):
        budgets.create_budget(AccountId=ACCOUNT, Budget={
            'BudgetName': RI_BUDGET, 'BudgetLimit': {'Amount': '80', 'Unit': 'PERCENTAGE'}, 'TimeUnit': 'MONTHLY',
            'BudgetType': 'RI_UTILIZATION'})
    stored = _budget(BUDGET) or {}
    actual = ((stored.get('CalculatedSpend') or {}).get('ActualSpend') or {}).get('Amount')
    day20 = round(sum(SAMPLE_DAILY), 2)
    day21 = round(day20 + SPIKE_DAY, 2)
    scenarios = [
        {'When': 'day 20', 'Actual': day20, 'Forecast': forecast(SAMPLE_DAILY),
         'Alerts': budget_alerts(BUDGET_LIMIT, day20, forecast(SAMPLE_DAILY))},
        {'When': 'day 21, after a runaway job', 'Actual': day21, 'Forecast': forecast([*SAMPLE_DAILY, SPIKE_DAY]),
         'Alerts': budget_alerts(BUDGET_LIMIT, day21, forecast([*SAMPLE_DAILY, SPIKE_DAY]))},
    ]
    response = {'Budget': {key: stored.get(key) for key in ('BudgetName', 'BudgetLimit', 'CostFilters', 'BudgetType')},
                'FlociActualSpend': actual, 'Notifications': _notifications(), 'Scenarios': scenarios}
    verified, message = _check_budget()
    if verified and (scenarios[0]['Alerts'] or len(scenarios[1]['Alerts']) != 2):
        verified, message = False, f'Unexpected alerts: {scenarios}.'
    elif verified:
        message += (f' On day 20 (${day20:.2f} spent, ${scenarios[0]["Forecast"]:.2f} forecast) nothing fires; after '
                    f'a ${SPIKE_DAY:.2f} day both alerts would.')
        if actual in (None, '0', '0.0'):
            message += ' Floci keeps actual spend at $0 and never alerts, so the step evaluated sample spend.'
    return _result('budget-alerts', BUDGET_COMMAND, response, verified, message, started)


def step_cost_explorer() -> dict[str, Any]:
    started = time.perf_counter()
    ce = _client('ce')
    first_this_month = date.today().replace(day=1)
    first_last_month = (first_this_month - timedelta(days=1)).replace(day=1)
    period = {'Start': first_last_month.isoformat(), 'End': first_this_month.isoformat()}
    by_service = _unsupported(lambda: ce.get_cost_and_usage(TimePeriod=period, Granularity='MONTHLY',
                                                            Metrics=['UnblendedCost'],
                                                            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}]))
    by_tag = _unsupported(lambda: ce.get_cost_and_usage(TimePeriod=period, Granularity='MONTHLY',
                                                        Metrics=['UnblendedCost'],
                                                        GroupBy=[{'Type': 'TAG', 'Key': 'CostCenter'}]))
    next_month = (first_this_month + timedelta(days=32)).replace(day=1)
    floci_forecast = _unsupported(lambda: ce.get_cost_forecast(
        TimePeriod={'Start': date.today().isoformat(), 'End': next_month.isoformat()}, Metric='UNBLENDED_COST',
        Granularity='MONTHLY'))

    def groups(result: dict[str, Any]) -> dict[str, str]:
        rows = ((result.get('Response') or {}).get('ResultsByTime') or [{}])[0].get('Groups', [])
        return {','.join(row['Keys']) or '(untagged)': row['Metrics']['UnblendedCost']['Amount'] for row in rows}

    month_to_date = round(sum(SAMPLE_DAILY), 2)
    estimate = forecast(SAMPLE_DAILY)
    response = {'TimePeriod': period, 'ByService': groups(by_service), 'ByCostCenter': groups(by_tag),
                'GetCostForecast': {key: floci_forecast.get(key) for key in ('Outcome', 'Code')},
                'SampleMonthToDate': month_to_date, 'SampleForecast': estimate}
    if by_service['Outcome'] != 'answered':
        return _result('cost-explorer', EXPLORER_COMMAND, response, False,
                       f'GetCostAndUsage failed: {by_service.get("Code")}.', started)
    message = (f'Cost Explorer split last month by {len(response["ByService"])} services and by CostCenter. The '
               f'platform team has spent ${month_to_date:.2f} in 20 days, so the month should end near '
               f'${estimate:.2f}, just under its ${BUDGET_LIMIT:g} budget.')
    if floci_forecast['Outcome'] != 'answered':
        message += (' Floci returns synthetic near-zero amounts and does not support forecasts, so the step '
                    'forecast from sample daily spend.')
    _marker('cost-explorer', {'message': message})
    verified, check = _check_explorer()
    return _result('cost-explorer', EXPLORER_COMMAND, response, verified, check, started)


def step_anomaly_monitor() -> dict[str, Any]:
    started = time.perf_counter()
    ce = _client('ce')
    topic = _topic_arn()
    if not topic:
        return _result('anomaly-monitor', ANOMALY_COMMAND, {}, False, f'Create {TOPIC} first (Budgets step).', started)
    monitor = _monitor()
    if not monitor:
        ce.create_anomaly_monitor(AnomalyMonitor={
            'MonitorName': MONITOR, 'MonitorType': 'CUSTOM',
            'MonitorSpecification': {'Tags': {'Key': 'CostCenter', 'Values': ['platform']}}},
            ResourceTags=[LAB_TAG])
        monitor = _monitor()
    if _subscription():
        ce.delete_anomaly_subscription(SubscriptionArn=_subscription()['SubscriptionArn'])
    ce.create_anomaly_subscription(AnomalySubscription={
        'SubscriptionName': SUBSCRIPTION, 'MonitorArnList': [monitor['MonitorArn']],
        'Subscribers': [{'Type': 'SNS', 'Address': topic}], 'Frequency': 'IMMEDIATE',
        'ThresholdExpression': {'Dimensions': {'Key': 'ANOMALY_TOTAL_IMPACT_ABSOLUTE',
                                               'Values': [f'{ANOMALY_THRESHOLD:g}'],
                                               'MatchOptions': ['GREATER_THAN_OR_EQUAL']}}},
        ResourceTags=[LAB_TAG])
    floci = _unsupported(lambda: ce.get_anomalies(DateInterval={'StartDate': date.today().replace(day=1).isoformat()}))
    anomalies = find_anomalies(SAMPLE_DAILY, [6.10, SPIKE_DAY])
    response = {'Monitor': monitor, 'Subscription': _subscription(),
                'GetAnomalies': {key: floci.get(key) for key in ('Outcome', 'Code')}, 'SampleAnomalies': anomalies}
    alerting = [item for item in anomalies if item['Alerts']]
    if len(alerting) != 1 or alerting[0]['Spend'] != SPIKE_DAY:
        return _result('anomaly-monitor', ANOMALY_COMMAND, response, False,
                       f'Only the ${SPIKE_DAY:.2f} day should alert, got {anomalies}.', started)
    message = (f'{MONITOR} watches CostCenter=platform and {SUBSCRIPTION} alerts {TOPIC} at once for an impact of '
               f'${ANOMALY_THRESHOLD:g} or more. A ${SPIKE_DAY:.2f} day against a normal '
               f'${alerting[0]["Expected"]:.2f} has an impact of ${alerting[0]["Impact"]:.2f} and alerts; a $6.10 day '
               'is unusual but below the threshold.')
    if floci['Outcome'] != 'answered':
        message += ' Floci does not support GetAnomalies, so the step found the spike in sample spend.'
    _marker('anomaly-monitor', {'message': message})
    verified, check = _check_anomaly()
    return _result('anomaly-monitor', ANOMALY_COMMAND, response, verified, check, started)


def step_pricing_lookup() -> dict[str, Any]:
    started = time.perf_counter()
    prices = {instance_type: _price(instance_type) for instance_type in INSTANCE_TYPES}
    offerings = _unsupported(lambda: _client('ec2').describe_reserved_instances_offerings(
        InstanceType='m5.large', ProductDescription='Linux/UNIX', MaxResults=5))
    rows = [{'InstanceType': instance_type, 'OnDemandHourlyUSD': price,
             'MonthlyUSD': round(price * HOURS_PER_MONTH, 2) if price else None}
            for instance_type, price in prices.items()]
    response = {'Prices': rows,
                'DescribeReservedInstancesOfferings': {key: offerings.get(key) for key in ('Outcome', 'Code')}}
    if not all(prices.values()):
        return _result('pricing-lookup', PRICING_COMMAND, response, False,
                       f'No On-Demand price for {[key for key, value in prices.items() if not value]}.', started)
    message = ('On-Demand Linux in us-east-1: ' + ', '.join(
        f'{row["InstanceType"]} ${row["OnDemandHourlyUSD"]:g}/hour (${row["MonthlyUSD"]:.2f}/month)' for row in rows)
        + '.')
    if offerings['Outcome'] != 'answered':
        message += ' Floci has no Reserved Instance offerings, so the next steps use typical discounts.'
    _marker('pricing-lookup', {'message': message, 'Prices': prices})
    verified, check = _check_pricing()
    return _result('pricing-lookup', PRICING_COMMAND, response, verified, check, started)


def step_spot_request() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    history = ec2.describe_spot_price_history(InstanceTypes=[SPOT_TYPE], ProductDescriptions=['Linux/UNIX'],
                                              MaxResults=10).get('SpotPriceHistory', [])
    if not history:
        return _result('spot-request', SPOT_COMMAND, {}, False, 'No Spot price history.', started)
    cheapest = min(history, key=lambda item: float(item['SpotPrice']))
    on_demand = (_marked('pricing-lookup') or {}).get('Prices', {}).get(SPOT_TYPE) or _price(SPOT_TYPE)
    tags = [{'ResourceType': 'spot-instances-request', 'Tags': [LAB_TAG]}]
    requests = []
    for label, bid in (('at the On-Demand price', f'{on_demand:g}'), ('below the market', LOW_BID)):
        made = ec2.request_spot_instances(InstanceCount=1, Type='one-time', SpotPrice=bid, TagSpecifications=tags,
                                          LaunchSpecification={'ImageId': SPOT_IMAGE, 'InstanceType': SPOT_TYPE})[
            'SpotInstanceRequests'][0]
        requests.append({'Bid': label, 'MaxPrice': bid, 'Id': made['SpotInstanceRequestId'],
                         'State': made.get('State'), 'Status': (made.get('Status') or {}).get('Code'),
                         'InstanceId': made.get('InstanceId')})
    ids = [item['Id'] for item in requests]
    ec2.cancel_spot_instance_requests(SpotInstanceRequestIds=ids)
    instance_ids = [item['InstanceId'] for item in requests if item['InstanceId']]
    if instance_ids:
        ec2.terminate_instances(InstanceIds=instance_ids)
    savings = 1 - float(cheapest['SpotPrice']) / on_demand
    response = {'CheapestSpot': {key: cheapest.get(key) for key in ('AvailabilityZone', 'SpotPrice')},
                'OnDemandUSD': on_demand, 'Savings': f'{savings:.0%}', 'Requests': requests,
                'Cleanup': {'Cancelled': ids, 'Terminated': instance_ids}}
    message = (f'{SPOT_TYPE} Spot costs ${float(cheapest["SpotPrice"]):g}/hour in {cheapest["AvailabilityZone"]}, '
               f'{savings:.0%} below On-Demand (${on_demand:g}). Both requests were cancelled and their instances '
               'terminated.')
    low = requests[1]
    if low['Status'] == 'fulfilled':
        message += f' Floci fulfilled even the ${LOW_BID} bid, which AWS would leave open as price-too-low.'
    _marker('spot-request', {'message': message, 'RequestIds': ids, 'Savings': round(savings, 2)})
    verified, check = _check_spot()
    return _result('spot-request', SPOT_COMMAND, response, verified, check, started)


def step_purchasing_options() -> dict[str, Any]:
    started = time.perf_counter()
    hourly = (_marked('pricing-lookup') or {}).get('Prices', {}).get('m5.large')
    if not hourly:
        return _result('purchasing-options', OPTIONS_COMMAND, {}, False, 'Look up prices first.', started)
    spot_discount = (_marked('spot-request') or {}).get('Savings')
    costs = option_costs(hourly, spot_discount=spot_discount)
    response = {'M5LargeOnDemandHourlyUSD': hourly, 'ThreeYears': costs,
                'Workloads': [{'Workload': text, 'Choice': choice} for text, choice in WORKLOADS]}
    on_demand, standard = costs[0]['3YearCostUSD'], costs[5]['3YearCostUSD']
    spot = costs[6]
    message = (f'Three years of one m5.large costs ${on_demand:,.2f} On-Demand, ${standard:,.2f} with a 3-year all '
               f'upfront Standard RI and ${spot["3YearCostUSD"]:,.2f} on Spot ({spot["DiscountVsOnDemand"]} less, if '
               'the work can be interrupted). Steady and unchanging: Standard RI; steady but '
               'moving to Fargate or Lambda: Compute Savings Plan; interruptible: Spot; short-lived: On-Demand; '
               'per-socket licenses: Dedicated Host; capacity without a term: Capacity Reservation.')
    _marker('purchasing-options', {'message': message})
    verified, check = _check_options()
    return _result('purchasing-options', OPTIONS_COMMAND, response, verified, check, started)


RUNNERS = {
    'tag-strategy': step_tag_strategy,
    'budget-alerts': step_budget_alerts,
    'cost-explorer': step_cost_explorer,
    'anomaly-monitor': step_anomaly_monitor,
    'pricing-lookup': step_pricing_lookup,
    'spot-request': step_spot_request,
    'purchasing-options': step_purchasing_options,
}
CHECKS = {
    'tag-strategy': _check_tags,
    'budget-alerts': _check_budget,
    'cost-explorer': _check_explorer,
    'anomaly-monitor': _check_anomaly,
    'pricing-lookup': _check_pricing,
    'spot-request': _check_spot,
    'purchasing-options': _check_options,
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
    """Cancel the Spot requests and terminate their instances, delete the anomaly subscription and monitor, the
    budgets, the topic, the buckets and the queue."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code.startswith('NoSuch') or 'NonExistent' in code or code == 'QueueDoesNotExist':
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    ec2, ce, budgets, sns, s3, sqs = (_client(name) for name in ('ec2', 'ce', 'budgets', 'sns', 's3', 'sqs'))
    try:
        spot = _spot_requests()
    except (ClientError, BotoCoreError) as exc:
        spot = []
        errors.append(f'spot lookup: {exc}')
    open_ids = [item['SpotInstanceRequestId'] for item in spot if item.get('State') in ('open', 'active')]
    if open_ids:
        attempt(f'spot-requests:{",".join(open_ids)}', lambda: ec2.cancel_spot_instance_requests(
            SpotInstanceRequestIds=open_ids))
    live = [item['InstanceId'] for item in spot if item.get('InstanceId')
            and _instance_state(item['InstanceId']) in ('pending', 'running', 'stopping', 'stopped')]
    if live:
        attempt(f'instances:{",".join(live)}', lambda: ec2.terminate_instances(InstanceIds=live))
    try:
        subscription, monitor = _subscription(), _monitor()
    except (ClientError, BotoCoreError) as exc:
        subscription, monitor = None, None
        errors.append(f'anomaly lookup: {exc}')
    if subscription:
        attempt(f'anomaly-subscription:{SUBSCRIPTION}', lambda: ce.delete_anomaly_subscription(
            SubscriptionArn=subscription['SubscriptionArn']))
    if monitor:
        attempt(f'anomaly-monitor:{MONITOR}', lambda: ce.delete_anomaly_monitor(MonitorArn=monitor['MonitorArn']))
    for name in (BUDGET, RI_BUDGET):
        attempt(f'budget:{name}', lambda name=name: budgets.delete_budget(AccountId=ACCOUNT, BudgetName=name))
    try:
        topic = _topic_arn()
    except (ClientError, BotoCoreError):
        topic = None
    if topic:
        attempt(f'topic:{TOPIC}', lambda: sns.delete_topic(TopicArn=topic))
    for bucket in (PLATFORM_BUCKET, ANALYTICS_BUCKET):
        attempt(f'bucket:{bucket}', lambda bucket=bucket: s3.delete_bucket(Bucket=bucket))
    try:
        queue_url = _queue_url()
    except (ClientError, BotoCoreError):
        queue_url = None
    if queue_url:
        attempt(f'queue:{ORPHAN_QUEUE}', lambda: sqs.delete_queue(QueueUrl=queue_url))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws ec2 cancel-spot-instance-requests --spot-instance-request-ids <sir-ids> && '
            'aws ec2 terminate-instances --instance-ids <ids>\n'
            'aws ce delete-anomaly-subscription --subscription-arn <arn> && aws ce delete-anomaly-monitor --monitor-arn <arn>\n'
            f'aws budgets delete-budget --account-id {ACCOUNT} --budget-name {BUDGET}   # and {RI_BUDGET}\n'
            f'aws sns delete-topic --topic-arn <{TOPIC}-arn>\n'
            f'aws s3 rb s3://{PLATFORM_BUCKET} && aws s3 rb s3://{ANALYTICS_BUCKET}\n'
            f'aws sqs delete-queue --queue-url <{ORPHAN_QUEUE}-url>'
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
            else 'The Spot requests, anomaly monitor, budgets, topic, buckets and queue are gone.',
        },
    }
