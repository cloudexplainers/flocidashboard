"""Data lake governance: an encrypted lake bucket, the location registered with Lake Formation, LF-Tags on the
database, table and personal-data columns, a tag-based grant for analysts, personal data found and pseudonymized
into a curated zone, an IAM policy that limits the analyst to that zone (enforced), a Region guardrail, and an
audit of data access from CloudTrail data events.

Floci 2.4.0 stores Lake Formation settings, LF-Tags, tag assignments and grants, and lists the grants back, but it
does not enforce them: Athena and S3 answer as if Lake Formation were off. GetResourceLFTags, SearchTablesByLFTags
and Amazon Macie are not routed. So the lab evaluates the tag-based grant itself, finds personal data with the same
patterns a Macie custom data identifier would use, and relies on IAM, which Floci does enforce, for the access that
must really be refused. CloudTrail delivers S3 data events for the trail's selectors, including AccessDenied calls.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'lakeformation'
LAB_KEY = 'lake-governance'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
OTHER_REGION = 'eu-west-1'
ACCOUNT = '000000000000'

BUCKET = 'lab-gov-lake'
TRAIL_BUCKET = 'lab-gov-audit-logs'
TRAIL = 'lab-gov-data-access'
KEY_ALIAS = 'alias/lab-gov-lake'
RAW_KEY = 'raw/customers/customers.csv'
CURATED_KEY = 'curated/customers/customers.csv'
FINDINGS_KEY = 'governance/pii-findings.json'
DATABASE = 'lab_gov'
RAW_TABLE = 'customers_raw'
CURATED_TABLE = 'customers_curated'
ANALYST_ROLE = 'lab-gov-analyst'
ACCESS_POLICY = 'curated-zone-only'
REGION_POLICY = 'region-guardrail'
TAG_CLASSIFICATION = 'classification'
TAG_DOMAIN = 'domain'
CLASSIFICATIONS = ['pii', 'internal', 'public']
ANALYST_CAN_SEE = ['internal', 'public']

ANALYST_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ANALYST_ROLE}'
ADMIN_ARN = f'arn:aws:iam::{ACCOUNT}:root'
LAKE_ARN = f'arn:aws:s3:::{BUCKET}'
FLOCI = 'How Floci emulates this: '

# ── Data ─────────────────────────────────────────────────────────────────────

COLUMNS = [('customer_id', 'int', 'public'), ('full_name', 'string', 'pii'), ('email', 'string', 'pii'),
           ('phone', 'string', 'pii'), ('country', 'string', 'internal'), ('lifetime_spend', 'double', 'internal')]
CUSTOMERS = [
    (1, 'Ana Silva', 'ana.silva@example.com', '+1-555-0101', 'US', 120.5),
    (2, 'Ben Okafor', 'ben.okafor@example.com', '+44-20-5550-0102', 'GB', 89.0),
    (3, 'Chen Wei', 'chen.wei@example.com', '+1-555-0103', 'US', 342.25),
    (4, 'Dara Novak', 'dara.novak@example.com', '+49-30-5550-0104', 'DE', 15.75),
]
PATTERNS = {
    'EMAIL_ADDRESS': re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
    'PHONE_NUMBER': re.compile(r'\+\d{1,3}(?:-\d{2,4}){2,4}'),
}
CURATED_COLUMNS = [('customer_key', 'string'), ('country', 'string'), ('lifetime_spend', 'double')]


def raw_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow([name for name, _, _ in COLUMNS])
    writer.writerows(CUSTOMERS)
    return out.getvalue()


def find_pii(text: str) -> dict[str, int]:
    """Counts per data type, as a Macie sensitive data discovery job would report them."""
    return {name: len(pattern.findall(text)) for name, pattern in PATTERNS.items() if pattern.findall(text)}


def pseudonym(email: str, salt: str = LAB_KEY) -> str:
    """A stable key that joins the same customer across tables without exposing the email."""
    return hashlib.sha256(f'{salt}:{email.lower()}'.encode()).hexdigest()[:16]


def curated_csv(text: str) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow([name for name, _ in CURATED_COLUMNS])
    for row in csv.DictReader(io.StringIO(text)):
        writer.writerow([pseudonym(row['email']), row['country'], row['lifetime_spend']])
    return out.getvalue()


def visible_columns(grant_values: list[str]) -> list[str]:
    """Columns a tag-based SELECT grant on these classification values allows, as Lake Formation evaluates it."""
    return [name for name, _, tag in COLUMNS if tag in grant_values]


# ── Policies ─────────────────────────────────────────────────────────────────

ANALYST_TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'AWS': ADMIN_ARN}, 'Action': 'sts:AssumeRole'}]}


def access_policy(key_arn: str) -> dict[str, Any]:
    return {'Version': '2012-10-17', 'Statement': [
        {'Sid': 'ReadCuratedZone', 'Effect': 'Allow', 'Action': 's3:GetObject',
         'Resource': f'{LAKE_ARN}/curated/*'},
        {'Sid': 'ListCuratedZone', 'Effect': 'Allow', 'Action': 's3:ListBucket', 'Resource': LAKE_ARN,
         'Condition': {'StringLike': {'s3:prefix': ['curated/*']}}},
        {'Sid': 'DecryptLakeObjects', 'Effect': 'Allow', 'Action': 'kms:Decrypt', 'Resource': key_arn,
         'Condition': {'StringEquals': {'kms:ViaService': f's3.{REGION}.amazonaws.com'}}},
    ]}


REGION_GUARDRAIL = {'Version': '2012-10-17', 'Statement': [
    {'Sid': 'DenyOutsideApprovedRegions', 'Effect': 'Deny', 'NotAction': ['iam:*', 'sts:*', 'organizations:*'],
     'Resource': '*', 'Condition': {'StringNotEquals': {'aws:RequestedRegion': [REGION]}}}]}


def bucket_policy() -> dict[str, Any]:
    return {'Version': '2012-10-17', 'Statement': [
        {'Sid': 'DenyInsecureTransport', 'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
         'Resource': [LAKE_ARN, f'{LAKE_ARN}/*'], 'Condition': {'Bool': {'aws:SecureTransport': 'false'}}},
        {'Sid': 'DenyUnencryptedUploads', 'Effect': 'Deny', 'Principal': '*', 'Action': 's3:PutObject',
         'Resource': f'{LAKE_ARN}/*',
         'Condition': {'StringNotEqualsIfExists': {'s3:x-amz-server-side-encryption': 'aws:kms'}}},
    ]}


# ── Commands ─────────────────────────────────────────────────────────────────

ENCRYPT_COMMAND = (
    'aws kms create-key --description "Data lake key"\n'
    f'aws kms create-alias --alias-name {KEY_ALIAS} --target-key-id <key-id>\n'
    'aws kms enable-key-rotation --key-id <key-id>\n'
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    f'aws s3api put-bucket-encryption --bucket {BUCKET} --server-side-encryption-configuration '
    f'\'{{"Rules":[{{"ApplyServerSideEncryptionByDefault":{{"SSEAlgorithm":"aws:kms","KMSMasterKeyID":"{KEY_ALIAS}"}},'
    '"BucketKeyEnabled":true}]}\'\n'
    f'aws s3api put-bucket-policy --bucket {BUCKET} --policy file://lake-bucket-policy.json   # TLS only\n'
    f'aws s3 cp customers.csv s3://{BUCKET}/{RAW_KEY} --sse aws:kms --sse-kms-key-id {KEY_ALIAS}'
)
REGISTER_COMMAND = (
    f'aws lakeformation put-data-lake-settings --data-lake-settings file://lake-settings.json   # admins, no '
    'IAMAllowedPrincipals defaults\n'
    f'aws lakeformation register-resource --resource-arn {LAKE_ARN} --use-service-linked-role\n'
    f'aws glue create-database --database-input Name={DATABASE}\n'
    f'aws glue create-table --database-name {DATABASE} --table-input file://customers_raw.json'
)
TAGS_COMMAND = (
    f'aws lakeformation create-lf-tag --tag-key {TAG_CLASSIFICATION} --tag-values {" ".join(CLASSIFICATIONS)}\n'
    f'aws lakeformation create-lf-tag --tag-key {TAG_DOMAIN} --tag-values sales marketing\n'
    f'aws lakeformation add-lf-tags-to-resource --resource \'{{"Database":{{"Name":"{DATABASE}"}}}}\' '
    f'--lf-tags TagKey={TAG_DOMAIN},TagValues=sales\n'
    f'aws lakeformation add-lf-tags-to-resource --resource \'{{"TableWithColumns":{{"DatabaseName":"{DATABASE}",'
    f'"Name":"{RAW_TABLE}","ColumnNames":["full_name","email","phone"]}}}}\' '
    f'--lf-tags TagKey={TAG_CLASSIFICATION},TagValues=pii'
)
GRANT_COMMAND = (
    f'aws iam create-role --role-name {ANALYST_ROLE} --assume-role-policy-document file://analyst-trust.json\n'
    f'aws lakeformation grant-permissions --principal DataLakePrincipalIdentifier={ANALYST_ROLE_ARN} '
    f'--permissions SELECT DESCRIBE --resource \'{{"LFTagPolicy":{{"ResourceType":"COLUMN","Expression":['
    f'{{"TagKey":"{TAG_CLASSIFICATION}","TagValues":["internal","public"]}},'
    f'{{"TagKey":"{TAG_DOMAIN}","TagValues":["sales"]}}]}}}}\'\n'
    f'aws lakeformation list-permissions --principal DataLakePrincipalIdentifier={ANALYST_ROLE_ARN}'
)
MASK_COMMAND = (
    'aws macie2 create-classification-job --job-type ONE_TIME --name lab-gov-pii-scan '
    f'--s3-job-definition \'{{"bucketDefinitions":[{{"accountId":"{ACCOUNT}","buckets":["{BUCKET}"]}}]}}\'\n'
    f'aws s3 cp customers-curated.csv s3://{BUCKET}/{CURATED_KEY}   # PII replaced by a salted hash\n'
    f'aws glue create-table --database-name {DATABASE} --table-input file://customers_curated.json'
)
ACCESS_COMMAND = (
    f'aws iam put-role-policy --role-name {ANALYST_ROLE} --policy-name {ACCESS_POLICY} '
    '--policy-document file://curated-zone-only.json\n'
    f'aws sts assume-role --role-arn {ANALYST_ROLE_ARN} --role-session-name analyst\n'
    f'aws s3api get-object --bucket {BUCKET} --key {CURATED_KEY} curated.csv   # allowed\n'
    f'aws s3api get-object --bucket {BUCKET} --key {RAW_KEY} raw.csv   # AccessDenied'
)
REGION_COMMAND = (
    f'aws iam put-role-policy --role-name {ANALYST_ROLE} --policy-name {REGION_POLICY} '
    '--policy-document file://region-guardrail.json   # in production, an SCP on the OU\n'
    f'aws s3api get-object --bucket {BUCKET} --key {CURATED_KEY} curated.csv --region {OTHER_REGION}   # denied'
)
AUDIT_COMMAND = (
    f'aws s3api create-bucket --bucket {TRAIL_BUCKET}\n'
    f'aws cloudtrail create-trail --name {TRAIL} --s3-bucket-name {TRAIL_BUCKET} --enable-log-file-validation\n'
    f'aws cloudtrail put-event-selectors --trail-name {TRAIL} --advanced-event-selectors file://lake-data-events.json\n'
    f'aws cloudtrail start-logging --name {TRAIL}\n'
    f'aws s3 ls s3://{TRAIL_BUCKET}/AWSLogs/{ACCOUNT}/CloudTrail/ --recursive'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Govern a data lake: encryption, Lake Formation tags and grants, PII masking, and access audits',
    'description': (
        'Encrypt a lake bucket with a KMS key, register it with Lake Formation, tag the database, table and '
        'personal-data columns with LF-Tags, grant analysts the non-sensitive columns by tag, find and pseudonymize '
        'personal data into a curated zone, limit the analyst role to that zone with IAM, add a Region guardrail, '
        'and audit data access from CloudTrail data events.'
    ),
    'steps': [
        {
            'key': 'encrypted-lake',
            'title': f'Create the {BUCKET} bucket with SSE-KMS by default and a TLS-only policy',
            'command': ENCRYPT_COMMAND,
            'explanation': (
                f'A customer managed key ({KEY_ALIAS}, rotated yearly) encrypts every object by default, and an '
                'S3 Bucket Key cuts KMS calls and cost. Because S3 must ask KMS to decrypt, a reader needs both '
                's3:GetObject and kms:Decrypt on the key, which gives a second, auditable gate. The bucket policy '
                'refuses any request without TLS (aws:SecureTransport) and any upload that is not SSE-KMS. The raw '
                'customer file lands in raw/. '
                + FLOCI + 'the key, rotation, default encryption, and policy are stored and read back. Floci does '
                'not check kms:Decrypt on S3 reads, so the lab grants it but cannot show it refused.'
            ),
        },
        {
            'key': 'register-lake',
            'title': f'Register the lake with Lake Formation and catalog {RAW_TABLE}',
            'command': REGISTER_COMMAND,
            'explanation': (
                'Registering an S3 location hands its access to Lake Formation: engines such as Athena, Redshift '
                'Spectrum, EMR and Glue ask Lake Formation for temporary credentials instead of using the caller\'s '
                'S3 permissions. Data lake administrators manage grants. New databases and tables must not get the '
                'IAMAllowedPrincipals default (which means "IAM decides"), or Lake Formation grants are ignored for '
                f'them. {RAW_TABLE} catalogs the raw file. '
                + FLOCI + 'the settings, the registration and the catalog are stored; Floci does not hand out '
                'Lake Formation credentials.'
            ),
        },
        {
            'key': 'lf-tags',
            'title': 'Tag the database, the table, and the personal-data columns with LF-Tags',
            'command': TAGS_COMMAND,
            'explanation': (
                f'LF-Tags scale better than named grants: tag the {DATABASE} database {TAG_DOMAIN}=sales, the '
                f'table {TAG_CLASSIFICATION}=internal, and the name, email and phone columns '
                f'{TAG_CLASSIFICATION}=pii. Columns inherit the table\'s tag unless they have their own, so new '
                'columns are internal until someone marks them otherwise. A grant on a tag expression then covers '
                'every matching table, including ones created later. '
                + FLOCI + 'the tags and their assignments are stored. GetResourceLFTags is not routed in Floci, so '
                'the lab keeps the column classification it assigned and reads it back from its own record.'
            ),
        },
        {
            'key': 'tag-grant',
            'title': f'Grant {ANALYST_ROLE} SELECT on internal and public columns by tag',
            'command': GRANT_COMMAND,
            'explanation': (
                'One grant gives analysts SELECT on every column tagged internal or public in the sales domain. '
                f'On {RAW_TABLE} that is {", ".join(visible_columns(ANALYST_CAN_SEE))}; name, email and phone stay '
                'hidden, and a query that asks for them fails. Data cell filters go further, with row filters such '
                'as country = \'US\'. Cross-account sharing uses the same grants through AWS RAM. '
                + FLOCI + 'the grant is stored and listed back, but not enforced: the lab evaluates which columns '
                'the grant allows. CreateDataCellsFilter is not routed in Floci.'
            ),
        },
        {
            'key': 'mask-pii',
            'title': 'Find personal data in the raw zone and publish a pseudonymized curated copy',
            'command': MASK_COMMAND,
            'explanation': (
                'Amazon Macie scans S3 for sensitive data (names, email addresses, phone numbers, credentials) and '
                'reports findings per object. Here the scan finds emails and phone numbers in raw/. The curated copy '
                'drops the direct identifiers and replaces the email with a salted SHA-256 key, so analysts can still '
                'join and count customers without seeing who they are (pseudonymization, not anonymization: keep the '
                f'salt secret). {CURATED_TABLE} catalogs it. Glue DataBrew and Glue ETL transforms offer the same '
                'masking as built-in recipes. '
                + FLOCI + 'Macie is not routed in Floci, so the lab scans the file with the same patterns a Macie '
                'custom data identifier would use and stores the findings in the bucket.'
            ),
        },
        {
            'key': 'curated-only-access',
            'title': f'Limit {ANALYST_ROLE} to the curated zone with IAM and test it',
            'command': ACCESS_COMMAND,
            'explanation': (
                'Lake Formation governs table access through the engines; IAM still decides direct S3 access. The '
                'analyst role may read and list only curated/, and decrypt with the lake key only through S3 '
                '(kms:ViaService). Assuming the role and reading both zones shows the difference: the curated '
                'object downloads, the raw one is refused. '
                + FLOCI + 'IAM is enforced: Floci refuses the raw read with AccessDenied.'
            ),
        },
        {
            'key': 'region-guardrail',
            'title': f'Deny requests outside {REGION} with a Region guardrail',
            'command': REGION_COMMAND,
            'explanation': (
                'Data residency rules often say where data may be processed. A Deny on aws:RequestedRegion (with '
                'global services such as IAM and STS excluded) stops any call to another Region, whatever other '
                'policies allow. In an organization this is a service control policy on the OU, so no account '
                f'administrator can remove it. The same read that works in {REGION} is refused when sent to '
                f'{OTHER_REGION}. '
                + FLOCI + 'the explicit Deny and its condition are enforced on the assumed role.'
            ),
        },
        {
            'key': 'audit-access',
            'title': f'Record data access with the {TRAIL} trail and find the refused read',
            'command': AUDIT_COMMAND,
            'explanation': (
                'Management events show who changed the lake; data events show who read which object. Data events '
                f'cost extra, so the selector covers only {BUCKET}. Log file validation adds signed digests that '
                'prove the logs were not changed. Reading the delivered log finds the analyst\'s GetObject calls, '
                'including the refused raw read with errorCode AccessDenied. Lake Formation also records '
                'GetDataAccess events for every credential it vends, and CloudTrail Lake can query all of it with '
                'SQL. '
                + FLOCI + 'the trail delivers real S3 data event records for its selectors, with errorCode set. '
                'Floci records the caller as an anonymous user instead of the assumed role.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _factory() -> FlociClientFactory:
    return FlociClientFactory()


def _client(name: str):
    return _factory().client(name)


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


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Floci lookups ────────────────────────────────────────────────────────────

def _trail() -> dict[str, Any] | None:
    # Floci has no GetTrail, so look the trail up with DescribeTrails.
    trails = _safe_call(lambda: _client('cloudtrail').describe_trails(trailNameList=[TRAIL])['trailList'], []) or []
    return next((trail for trail in trails if trail.get('Name') == TRAIL), None)


def _key_arn() -> str | None:
    aliases = _safe_call(lambda: _client('kms').list_aliases()['Aliases'], []) or []
    alias = next((item for item in aliases if item.get('AliasName') == KEY_ALIAS), None)
    if not alias or not alias.get('TargetKeyId'):
        return None
    return _safe_call(lambda: _client('kms').describe_key(KeyId=alias['TargetKeyId'])['KeyMetadata']['Arn'])


def _bucket_exists(bucket: str) -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=bucket), None) is not None


def _object(key: str) -> str | None:
    return _safe_call(lambda: _client('s3').get_object(Bucket=BUCKET, Key=key)['Body'].read().decode())


def _encryption() -> dict[str, Any]:
    rules = _safe_call(lambda: _client('s3').get_bucket_encryption(Bucket=BUCKET)[
        'ServerSideEncryptionConfiguration']['Rules'], []) or []
    return (rules[0] if rules else {}).get('ApplyServerSideEncryptionByDefault', {})


def _registered() -> bool:
    resources = _safe_call(lambda: _client('lakeformation').list_resources()['ResourceInfoList'], []) or []
    return any(item.get('ResourceArn') == LAKE_ARN for item in resources)


def _table(name: str) -> dict[str, Any] | None:
    return _safe_call(lambda: _client('glue').get_table(DatabaseName=DATABASE, Name=name)['Table'])


def _lf_tags() -> dict[str, list[str]]:
    tags = _safe_call(lambda: _client('lakeformation').list_lf_tags()['LFTags'], []) or []
    return {item['TagKey']: sorted(item.get('TagValues', [])) for item in tags}


def analyst_grants() -> list[dict[str, Any]]:
    permissions = _safe_call(lambda: _client('lakeformation').list_permissions()[
        'PrincipalResourcePermissions'], []) or []
    return [item for item in permissions
            if (item.get('Principal') or {}).get('DataLakePrincipalIdentifier') == ANALYST_ROLE_ARN]


def granted_classifications(grants: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for grant in grants:
        if 'SELECT' not in grant.get('Permissions', []):
            continue
        for term in ((grant.get('Resource') or {}).get('LFTagPolicy') or {}).get('Expression', []):
            if term.get('TagKey') == TAG_CLASSIFICATION:
                values.update(term.get('TagValues', []))
    return sorted(values)


def _role_policy(name: str) -> dict[str, Any] | None:
    return _safe_call(lambda: _client('iam').get_role_policy(RoleName=ANALYST_ROLE, PolicyName=name)['PolicyDocument'])


def _analyst_s3(region: str = REGION):
    """An S3 client signed with the analyst role's temporary credentials."""
    factory = _factory()
    credentials = factory.client('sts').assume_role(RoleArn=ANALYST_ROLE_ARN, RoleSessionName='analyst')[
        'Credentials']
    return boto3.client('s3', endpoint_url=factory.endpoint_url, region_name=region,
                        aws_access_key_id=credentials['AccessKeyId'],
                        aws_secret_access_key=credentials['SecretAccessKey'],
                        aws_session_token=credentials['SessionToken'], config=factory.config)


def _try_read(s3, key: str) -> str:
    try:
        s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()
        return 'allowed'
    except ClientError as exc:
        return _code(exc) or 'error'


def trail_records() -> list[dict[str, Any]]:
    s3 = _client('s3')
    keys = [item['Key'] for item in (_safe_call(lambda: s3.list_objects_v2(
        Bucket=TRAIL_BUCKET, Prefix=f'AWSLogs/{ACCOUNT}/CloudTrail/'), {}) or {}).get('Contents', [])
        if item['Key'].endswith('.json.gz')]
    records: list[dict[str, Any]] = []
    for key in keys:
        body = s3.get_object(Bucket=TRAIL_BUCKET, Key=key)['Body'].read()
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
        try:
            records.extend(json.loads(body).get('Records', []))
        except ValueError:
            continue
    return records


def lake_reads(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{'key': (record.get('requestParameters') or {}).get('key'), 'errorCode': record.get('errorCode'),
             'caller': (record.get('userIdentity') or {}).get('arn')}
            for record in records if record.get('eventName') == 'GetObject'
            and (record.get('requestParameters') or {}).get('bucketName') in (BUCKET, None)]


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_encrypted() -> tuple[bool, str]:
    if not _bucket_exists(BUCKET) or not _key_arn():
        return False, f'Create {KEY_ALIAS} and {BUCKET}.'
    if _encryption().get('SSEAlgorithm') != 'aws:kms':
        return False, f'Encrypt {BUCKET} with SSE-KMS by default.'
    if not _safe_call(lambda: _client('s3').get_bucket_policy(Bucket=BUCKET)):
        return False, 'Add the TLS-only bucket policy.'
    if _object(RAW_KEY) is None:
        return False, f'Land {RAW_KEY}.'
    return True, f'{BUCKET} encrypts with {KEY_ALIAS} by default, refuses plain HTTP, and holds the raw file.'


def _check_registered() -> tuple[bool, str]:
    if not _registered():
        return False, f'Register {LAKE_ARN} with Lake Formation.'
    if not _table(RAW_TABLE):
        return False, f'Catalog {DATABASE}.{RAW_TABLE}.'
    return True, f'{BUCKET} is registered, and {DATABASE}.{RAW_TABLE} is cataloged.'


def _check_tags() -> tuple[bool, str]:
    tags = _lf_tags()
    if sorted(tags.get(TAG_CLASSIFICATION, [])) != sorted(CLASSIFICATIONS) or TAG_DOMAIN not in tags:
        return False, f'Create the {TAG_CLASSIFICATION} and {TAG_DOMAIN} LF-Tags.'
    marker = _marked('lf-tags') or {}
    if sorted(marker.get('pii') or []) != ['email', 'full_name', 'phone']:
        return False, 'Tag the name, email, and phone columns pii.'
    return True, f'LF-Tags exist, and {", ".join(sorted(marker["pii"]))} are tagged pii.'


def _check_grant() -> tuple[bool, str]:
    values = granted_classifications(analyst_grants())
    if values != sorted(ANALYST_CAN_SEE):
        return False, f'Grant {ANALYST_ROLE} SELECT on {TAG_CLASSIFICATION} internal and public.'
    columns = visible_columns(values)
    if any(tag == 'pii' for name, _, tag in COLUMNS if name in columns):
        return False, 'The grant must not reach pii columns.'
    return True, f'{ANALYST_ROLE} may select {", ".join(columns)}; personal data stays hidden.'


def _check_mask() -> tuple[bool, str]:
    curated = _object(CURATED_KEY)
    if not curated or not _table(CURATED_TABLE):
        return False, f'Publish {CURATED_KEY} and catalog {CURATED_TABLE}.'
    if find_pii(curated):
        return False, f'The curated copy still has personal data: {find_pii(curated)}.'
    findings = json.loads(_object(FINDINGS_KEY) or '{}')
    if not findings.get('EMAIL_ADDRESS') or not findings.get('PHONE_NUMBER'):
        return False, 'Scan the raw zone and record the findings.'
    return True, f'Raw zone findings {findings}; the curated copy has none.'


def _check_access() -> tuple[bool, str]:
    marker = _marked('curated-only-access') or {}
    if marker.get('curated') != 'allowed' or marker.get('raw') != 'AccessDenied':
        return False, f'The analyst should read curated/ and be refused raw/; got {marker or "nothing yet"}.'
    return True, f'{ANALYST_ROLE} reads curated/ and is refused raw/ (AccessDenied).'


def _check_region() -> tuple[bool, str]:
    marker = _marked('region-guardrail') or {}
    if not _role_policy(REGION_POLICY):
        return False, f'Attach {REGION_POLICY} to {ANALYST_ROLE}.'
    if marker.get('home') != 'allowed' or marker.get('other') != 'AccessDenied':
        return False, f'The read should work in {REGION} and be refused in {OTHER_REGION}; got {marker}.'
    return True, f'The same read works in {REGION} and is refused in {OTHER_REGION}.'


def _check_audit() -> tuple[bool, str]:
    status = _safe_call(lambda: _client('cloudtrail').get_trail_status(Name=TRAIL)) or {}
    if not status.get('IsLogging'):
        return False, f'Create and start {TRAIL}.'
    reads = (_marked('audit-access') or {}).get('reads') or []
    if not any(item.get('key') == RAW_KEY and item.get('errorCode') == 'AccessDenied' for item in reads):
        return False, 'Find the refused raw read in the delivered trail logs.'
    return True, f'{TRAIL} recorded {len(reads)} lake reads, including the refused read of {RAW_KEY}.'


CHECKS = {
    'encrypted-lake': _check_encrypted,
    'register-lake': _check_registered,
    'lf-tags': _check_tags,
    'tag-grant': _check_grant,
    'mask-pii': _check_mask,
    'curated-only-access': _check_access,
    'region-guardrail': _check_region,
    'audit-access': _check_audit,
}


def _blocked(step: str, command: str, started: float, needs: str) -> dict[str, Any] | None:
    ready = {
        'bucket': lambda: _bucket_exists(BUCKET),
        'table': lambda: _table(RAW_TABLE) is not None,
        'curated': lambda: _object(CURATED_KEY) is not None,
        'role': lambda: _safe_call(lambda: _client('iam').get_role(RoleName=ANALYST_ROLE)) is not None,
    }[needs]()
    if ready:
        return None
    return _result(step, command, {}, False, 'Run the earlier steps first.', started)


def _table_input(name: str, columns: list[tuple[str, str]], location: str) -> dict[str, Any]:
    return {'Name': name, 'TableType': 'EXTERNAL_TABLE',
            'Parameters': {'classification': 'csv', 'skip.header.line.count': '1'},
            'StorageDescriptor': {
                'Columns': [{'Name': column, 'Type': kind} for column, kind in columns],
                'Location': location,
                'InputFormat': 'org.apache.hadoop.mapred.TextInputFormat',
                'OutputFormat': 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
                'SerdeInfo': {'SerializationLibrary': 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe',
                              'Parameters': {'field.delim': ','}}}}


# ── Steps ────────────────────────────────────────────────────────────────────

def step_encrypted_lake() -> dict[str, Any]:
    started = time.perf_counter()
    kms, s3 = _client('kms'), _client('s3')
    key_arn = _key_arn()
    if not key_arn:
        key = kms.create_key(Description='Data lake key', Tags=[{'TagKey': 'Lab', 'TagValue': LAB_KEY}])
        key_arn = key['KeyMetadata']['Arn']
        kms.create_alias(AliasName=KEY_ALIAS, TargetKeyId=key['KeyMetadata']['KeyId'])
    _safe_call(lambda: kms.enable_key_rotation(KeyId=key_arn))
    if not _bucket_exists(BUCKET):
        s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_encryption(Bucket=BUCKET, ServerSideEncryptionConfiguration={'Rules': [{
        'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'aws:kms', 'KMSMasterKeyID': key_arn},
        'BucketKeyEnabled': True}]})
    s3.put_public_access_block(Bucket=BUCKET, PublicAccessBlockConfiguration={
        'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True})
    s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(bucket_policy()))
    s3.put_object(Bucket=BUCKET, Key=RAW_KEY, Body=raw_csv().encode(), ContentType='text/csv',
                  ServerSideEncryption='aws:kms', SSEKMSKeyId=key_arn)
    rotation = _safe_call(lambda: kms.get_key_rotation_status(KeyId=key_arn).get('KeyRotationEnabled'))
    verified, message = _check_encrypted()
    return _result('encrypted-lake', ENCRYPT_COMMAND, {
        'Key': key_arn, 'Alias': KEY_ALIAS, 'Rotation': rotation, 'DefaultEncryption': _encryption(),
        'BucketPolicy': bucket_policy(), 'RawObject': RAW_KEY,
    }, verified, message, started)


def step_register_lake() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('register-lake', REGISTER_COMMAND, started, 'bucket')
    if blocked:
        return blocked
    lf, glue = _client('lakeformation'), _client('glue')
    settings = {'DataLakeAdmins': [{'DataLakePrincipalIdentifier': ADMIN_ARN}],
                'CreateDatabaseDefaultPermissions': [], 'CreateTableDefaultPermissions': []}
    lf.put_data_lake_settings(DataLakeSettings=settings)
    if not _registered():
        lf.register_resource(ResourceArn=LAKE_ARN, UseServiceLinkedRole=True)
    if not _safe_call(lambda: glue.get_database(Name=DATABASE)):
        glue.create_database(DatabaseInput={'Name': DATABASE, 'Description': 'Governed customer data'})
    if not _table(RAW_TABLE):
        glue.create_table(DatabaseName=DATABASE, TableInput=_table_input(
            RAW_TABLE, [(name, kind) for name, kind, _ in COLUMNS], f's3://{BUCKET}/raw/customers/'))
    verified, message = _check_registered()
    return _result('register-lake', REGISTER_COMMAND, {
        'DataLakeSettings': settings, 'Registered': _registered(), 'Table': f'{DATABASE}.{RAW_TABLE}',
    }, verified, message, started)


def step_lf_tags() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('lf-tags', TAGS_COMMAND, started, 'table')
    if blocked:
        return blocked
    lf = _client('lakeformation')
    existing = _lf_tags()
    for key, values in ((TAG_CLASSIFICATION, CLASSIFICATIONS), (TAG_DOMAIN, ['sales', 'marketing'])):
        if key not in existing:
            lf.create_lf_tag(TagKey=key, TagValues=values)
        elif set(values) - set(existing[key]):
            lf.update_lf_tag(TagKey=key, TagValuesToAdd=sorted(set(values) - set(existing[key])))
    lf.add_lf_tags_to_resource(Resource={'Database': {'Name': DATABASE}},
                               LFTags=[{'TagKey': TAG_DOMAIN, 'TagValues': ['sales']}])
    lf.add_lf_tags_to_resource(Resource={'Table': {'DatabaseName': DATABASE, 'Name': RAW_TABLE}},
                               LFTags=[{'TagKey': TAG_CLASSIFICATION, 'TagValues': ['internal']}])
    assigned: dict[str, list[str]] = {}
    for tag in CLASSIFICATIONS:
        names = [name for name, _, column_tag in COLUMNS if column_tag == tag and column_tag != 'internal']
        if names:
            lf.add_lf_tags_to_resource(
                Resource={'TableWithColumns': {'DatabaseName': DATABASE, 'Name': RAW_TABLE, 'ColumnNames': names}},
                LFTags=[{'TagKey': TAG_CLASSIFICATION, 'TagValues': [tag]}])
            assigned[tag] = names
    _marker('lf-tags', assigned)
    verified, message = _check_tags()
    return _result('lf-tags', TAGS_COMMAND, {
        'LFTags': _lf_tags(), 'Database': {TAG_DOMAIN: 'sales'}, 'Table': {TAG_CLASSIFICATION: 'internal'},
        'Columns': {name: tag for name, _, tag in COLUMNS},
    }, verified, message, started)


def step_tag_grant() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('tag-grant', GRANT_COMMAND, started, 'table')
    if blocked:
        return blocked
    iam, lf = _client('iam'), _client('lakeformation')
    try:
        iam.get_role(RoleName=ANALYST_ROLE)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=ANALYST_ROLE, AssumeRolePolicyDocument=json.dumps(ANALYST_TRUST),
                        Description='Sales analysts')
    if granted_classifications(analyst_grants()) != sorted(ANALYST_CAN_SEE):
        lf.grant_permissions(
            Principal={'DataLakePrincipalIdentifier': ANALYST_ROLE_ARN}, Permissions=['SELECT', 'DESCRIBE'],
            Resource={'LFTagPolicy': {'ResourceType': 'COLUMN', 'Expression': [
                {'TagKey': TAG_CLASSIFICATION, 'TagValues': ANALYST_CAN_SEE},
                {'TagKey': TAG_DOMAIN, 'TagValues': ['sales']}]}})
    grants = analyst_grants()
    verified, message = _check_grant()
    return _result('tag-grant', GRANT_COMMAND, {
        'Grants': grants, 'VisibleColumns': visible_columns(granted_classifications(grants)),
        'HiddenColumns': [name for name, _, tag in COLUMNS if tag == 'pii'],
    }, verified, message, started)


def step_mask_pii() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('mask-pii', MASK_COMMAND, started, 'table')
    if blocked:
        return blocked
    raw = _object(RAW_KEY) or ''
    findings = find_pii(raw)
    macie = _safe_call(lambda: _client('macie2').get_macie_session(), None)
    s3, glue = _client('s3'), _client('glue')
    s3.put_object(Bucket=BUCKET, Key=FINDINGS_KEY, Body=json.dumps(findings).encode(),
                  ContentType='application/json', ServerSideEncryption='aws:kms')
    curated = curated_csv(raw)
    s3.put_object(Bucket=BUCKET, Key=CURATED_KEY, Body=curated.encode(), ContentType='text/csv',
                  ServerSideEncryption='aws:kms')
    if _table(CURATED_TABLE):
        glue.delete_table(DatabaseName=DATABASE, Name=CURATED_TABLE)
    glue.create_table(DatabaseName=DATABASE, TableInput=_table_input(
        CURATED_TABLE, CURATED_COLUMNS, f's3://{BUCKET}/curated/customers/'))
    verified, message = _check_mask()
    return _result('mask-pii', MASK_COMMAND, {
        'Findings': findings, 'MacieOnFloci': 'available' if macie else 'not routed',
        'CuratedPreview': curated.splitlines()[:3],
    }, verified, message, started)


def step_curated_only_access() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('curated-only-access', ACCESS_COMMAND, started, 'curated') or \
        _blocked('curated-only-access', ACCESS_COMMAND, started, 'role')
    if blocked:
        return blocked
    iam = _client('iam')
    policy = access_policy(_key_arn() or '*')
    iam.put_role_policy(RoleName=ANALYST_ROLE, PolicyName=ACCESS_POLICY, PolicyDocument=json.dumps(policy))
    analyst = _analyst_s3()
    outcome = {'curated': _try_read(analyst, CURATED_KEY), 'raw': _try_read(analyst, RAW_KEY)}
    _marker('curated-only-access', outcome)
    verified, message = _check_access()
    return _result('curated-only-access', ACCESS_COMMAND, {'Policy': policy, 'Reads': outcome},
                   verified, message, started)


def step_region_guardrail() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('region-guardrail', REGION_COMMAND, started, 'role')
    if blocked:
        return blocked
    iam = _client('iam')
    _safe_call(lambda: iam.delete_role_policy(RoleName=ANALYST_ROLE, PolicyName=REGION_POLICY))
    before = _try_read(_analyst_s3(OTHER_REGION), CURATED_KEY)
    iam.put_role_policy(RoleName=ANALYST_ROLE, PolicyName=REGION_POLICY, PolicyDocument=json.dumps(REGION_GUARDRAIL))
    outcome = {'otherBefore': before, 'home': _try_read(_analyst_s3(), CURATED_KEY),
               'other': _try_read(_analyst_s3(OTHER_REGION), CURATED_KEY)}
    _marker('region-guardrail', outcome)
    verified, message = _check_region()
    return _result('region-guardrail', REGION_COMMAND, {'Guardrail': REGION_GUARDRAIL, 'Reads': outcome},
                   verified, message, started)


def step_audit_access() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('audit-access', AUDIT_COMMAND, started, 'role')
    if blocked:
        return blocked
    s3, cloudtrail = _client('s3'), _client('cloudtrail')
    if not _bucket_exists(TRAIL_BUCKET):
        s3.create_bucket(Bucket=TRAIL_BUCKET)
    if not _trail():
        cloudtrail.create_trail(Name=TRAIL, S3BucketName=TRAIL_BUCKET, EnableLogFileValidation=True,
                                TagsList=[{'Key': 'Lab', 'Value': LAB_KEY}])
    cloudtrail.put_event_selectors(TrailName=TRAIL, EventSelectors=[{
        'ReadWriteType': 'All', 'IncludeManagementEvents': True,
        'DataResources': [{'Type': 'AWS::S3::Object', 'Values': [f'{LAKE_ARN}/']}]}])
    cloudtrail.start_logging(Name=TRAIL)
    analyst = _analyst_s3()
    attempts = {'curated': _try_read(analyst, CURATED_KEY), 'raw': _try_read(analyst, RAW_KEY)}
    reads: list[dict[str, Any]] = []

    def delivered() -> bool:
        reads[:] = lake_reads(trail_records())
        return any(item.get('key') == RAW_KEY and item.get('errorCode') == 'AccessDenied' for item in reads)
    _wait(delivered, 60, delay=3)
    _marker('audit-access', {'reads': reads})
    verified, message = _check_audit()
    return _result('audit-access', AUDIT_COMMAND, {
        'Trail': TRAIL, 'Attempts': attempts, 'LakeReads': reads,
    }, verified, message, started)


RUNNERS = {
    'encrypted-lake': step_encrypted_lake,
    'register-lake': step_register_lake,
    'lf-tags': step_lf_tags,
    'tag-grant': step_tag_grant,
    'mask-pii': step_mask_pii,
    'curated-only-access': step_curated_only_access,
    'region-guardrail': step_region_guardrail,
    'audit-access': step_audit_access,
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
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError):
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
    """Remove the trail, grants, tags, registration, catalog, role, buckets, and schedule the key for deletion."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code in (
                    'ResourceNotFoundException', 'EntityNotFoundException', 'TrailNotFoundException',
                    'InvalidInputException', 'KMSInvalidStateException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cloudtrail = _client('cloudtrail')
    if _trail():
        attempt(f'trail:{TRAIL}', lambda: cloudtrail.delete_trail(Name=TRAIL))
    lf = _client('lakeformation')
    for grant in analyst_grants():
        attempt('grant', lambda grant=grant: lf.revoke_permissions(
            Principal=grant['Principal'], Resource=grant['Resource'], Permissions=grant.get('Permissions', [])))
    for key in (TAG_CLASSIFICATION, TAG_DOMAIN):
        if key in _lf_tags():
            attempt(f'lf-tag:{key}', lambda key=key: lf.delete_lf_tag(TagKey=key))
    if _registered():
        attempt('registration', lambda: lf.deregister_resource(ResourceArn=LAKE_ARN))
    glue = _client('glue')
    for table in (RAW_TABLE, CURATED_TABLE):
        if _table(table):
            attempt(f'table:{table}', lambda table=table: glue.delete_table(DatabaseName=DATABASE, Name=table))
    if _safe_call(lambda: glue.get_database(Name=DATABASE)):
        attempt(f'database:{DATABASE}', lambda: glue.delete_database(Name=DATABASE))
    iam = _client('iam')
    if _safe_call(lambda: iam.get_role(RoleName=ANALYST_ROLE)):
        for name in (ACCESS_POLICY, REGION_POLICY):
            if _role_policy(name):
                attempt(f'policy:{name}', lambda name=name: iam.delete_role_policy(RoleName=ANALYST_ROLE,
                                                                                    PolicyName=name))
        attempt(f'role:{ANALYST_ROLE}', lambda: iam.delete_role(RoleName=ANALYST_ROLE))
    s3 = _client('s3')
    for bucket in (BUCKET, TRAIL_BUCKET):
        if not _bucket_exists(bucket):
            continue
        if bucket == BUCKET:
            attempt('bucket-policy', lambda: s3.delete_bucket_policy(Bucket=BUCKET))
        keys = [item['Key'] for item in (_safe_call(lambda bucket=bucket: s3.list_objects_v2(Bucket=bucket),
                                                    {}) or {}).get('Contents', [])]
        if keys:
            attempt(f'objects:{bucket}', lambda bucket=bucket, keys=keys: s3.delete_objects(
                Bucket=bucket, Delete={'Objects': [{'Key': key} for key in keys], 'Quiet': True}))
        attempt(f'bucket:{bucket}', lambda bucket=bucket: s3.delete_bucket(Bucket=bucket))
    key_arn = _key_arn()
    if key_arn:
        kms = _client('kms')
        attempt(f'alias:{KEY_ALIAS}', lambda: kms.delete_alias(AliasName=KEY_ALIAS))
        attempt('key', lambda: kms.schedule_key_deletion(KeyId=key_arn, PendingWindowInDays=7))
    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': f'aws lakeformation deregister-resource --resource-arn {LAKE_ARN}  # and the rest',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'verified': not errors,
        'verification': {
            'status': 'passed' if not errors else 'failed',
            'message': 'Lake governance lab resources removed.' if not errors else '; '.join(errors)[:500],
        },
    }
