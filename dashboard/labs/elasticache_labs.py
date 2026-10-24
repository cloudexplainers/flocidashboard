"""ElastiCache cache-aside lab: a Redis-compatible replication group in private subnets, an eviction policy, an Aurora
PostgreSQL source of truth, and a Lambda reader that checks the cache before the database and invalidates on writes.

Floci 2.3.0 starts one real Valkey container per replication group and proxies it on a port of its own host (6379 and
up), so the reader's GET, SET with a TTL, and DEL are real, and so are the Aurora database and the Data API calls behind
it. The rest is stored without effect: the replica is a record (the container has no replicas), security groups and
Availability Zones are not kept on the cache nodes, the parameter group's eviction policy is not applied to the
container, and Lambda's VPC settings don't change how its container reaches the cache. So each step checks those
settings against the AWS rules itself.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'elasticache'
LAB_KEY = 'cache-aside'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

VPC_NAME = 'lab-cache-vpc'
VPC_CIDR = '10.61.0.0/16'
SUBNETS = [('lab-cache-private-a', '10.61.1.0/24', 'us-east-1a'), ('lab-cache-private-b', '10.61.2.0/24', 'us-east-1b')]
APP_SG = 'lab-cache-app'
CACHE_SG = 'lab-cache-redis'
CACHE_PORT = 6379
SUBNET_GROUP = 'lab-cache-subnets'

PARAMETER_GROUP = 'lab-cache-redis7'
PARAMETER_FAMILY = 'redis7'
EVICTION_POLICY = 'allkeys-lru'

REPLICATION_GROUP = 'lab-product-cache'
NODE_TYPE = 'cache.t4g.micro'
CACHE_AZS = ['us-east-1a', 'us-east-1b']

DB_CLUSTER = 'lab-cache-products-db'
DB_WRITER = 'lab-cache-products-db-1'
DB_NAME = 'catalog'
DB_CLUSTER_ARN = f'arn:aws:rds:{REGION}:{ACCOUNT}:cluster:{DB_CLUSTER}'
PRODUCTS = [
    ('SKU-100', 'Trail running shoes', '89.00'),
    ('SKU-200', 'Insulated water bottle', '24.50'),
    ('SKU-300', 'Merino wool socks', '16.00'),
]
SEED_SQL = [
    'CREATE TABLE IF NOT EXISTS products (sku text PRIMARY KEY, name text NOT NULL, price numeric(10,2) NOT NULL)',
    'INSERT INTO products (sku, name, price) VALUES '
    + ', '.join(f"('{sku}', '{name}', {price})" for sku, name, price in PRODUCTS)
    + ' ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name, price = EXCLUDED.price',
]
LOOKUP_SKU = 'SKU-100'
NEW_PRICE = '79.00'

ROLE_NAME = 'lab-cache-reader-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'
ROLE_POLICY = 'lab-cache-reader-data-access'
VPC_ACCESS_POLICY = 'arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole'
FUNCTION = 'lab-product-reader'
RUNTIME = 'python3.12'
HANDLER = 'reader.handler'
TTL_SECONDS = 300
HIT_READS = 5

TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}

READER_SOURCE = '''"""Cache-aside (lazy loading) product reader for the ElastiCache lab."""
import json
import os
import socket
import time

import boto3

CACHE = (os.environ["CACHE_HOST"], int(os.environ["CACHE_PORT"]))
TTL = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
data_api = boto3.client("rds-data")


def _reply(stream):
    line = stream.readline().rstrip(b"\\r\\n")
    kind, rest = line[:1], line[1:]
    if kind == b"+":
        return rest.decode()
    if kind == b"-":
        raise RuntimeError(rest.decode())
    if kind == b":":
        return int(rest)
    if kind == b"$":
        size = int(rest)
        return None if size < 0 else stream.read(size + 2)[:-2].decode()
    if kind == b"*":
        size = int(rest)
        return None if size < 0 else [_reply(stream) for _ in range(size)]
    raise RuntimeError("unexpected reply from the cache: %r" % line)


def redis(*args):
    """Send one command over the Redis protocol (RESP) and read the reply."""
    payload = b"*%d\\r\\n" % len(args)
    for arg in args:
        data = str(arg).encode()
        payload += b"$%d\\r\\n%s\\r\\n" % (len(data), data)
    with socket.create_connection(CACHE, timeout=3) as conn:
        conn.sendall(payload)
        return _reply(conn.makefile("rb"))


def sql(statement, **params):
    response = data_api.execute_statement(
        resourceArn=os.environ["DB_CLUSTER_ARN"], secretArn=os.environ["DB_SECRET_ARN"],
        database=os.environ["DB_NAME"], sql=statement,
        parameters=[{"name": key, "value": {"stringValue": value}} for key, value in params.items()],
    )
    return response.get("records", [])


def key(sku):
    return "product:" + sku


def get_product(sku):
    started = time.perf_counter()
    cached = redis("GET", key(sku))
    cache_ms = round((time.perf_counter() - started) * 1000, 2)
    if cached is not None:
        return {"source": "cache", "product": json.loads(cached), "ttl": redis("TTL", key(sku)), "cache_ms": cache_ms}
    started = time.perf_counter()
    rows = sql("SELECT sku, name, price::text FROM products WHERE sku = :sku", sku=sku)
    db_ms = round((time.perf_counter() - started) * 1000, 2)
    if not rows:
        return {"source": "database", "product": None, "ttl": None, "cache_ms": cache_ms, "db_ms": db_ms}
    product = {"sku": rows[0][0]["stringValue"], "name": rows[0][1]["stringValue"], "price": rows[0][2]["stringValue"]}
    redis("SET", key(sku), json.dumps(product), "EX", TTL)  # lazy loading: fill the cache on a miss, with a TTL
    return {"source": "database", "product": product, "ttl": redis("TTL", key(sku)), "cache_ms": cache_ms, "db_ms": db_ms}


def update_price(sku, price):
    sql("UPDATE products SET price = CAST(:price AS numeric) WHERE sku = :sku", sku=sku, price=price)
    return {"updated": sku, "price": price, "invalidated": redis("DEL", key(sku))}  # write, then invalidate


def handler(event, context):
    action = event.get("action", "get")
    sku = event.get("sku", "SKU-100")
    if action == "get":
        return get_product(sku)
    if action == "update":
        return update_price(sku, event["price"])
    if action == "evict":
        return {"evicted": redis("DEL", key(sku))}
    if action == "inspect":
        return {"cached": redis("GET", key(sku)), "ttl": redis("TTL", key(sku)), "ping": redis("PING"),
                "maxmemory_policy": redis("CONFIG", "GET", "maxmemory-policy")}
    raise ValueError("unknown action " + action)
'''

NETWORK_COMMAND = (
    f'aws ec2 create-vpc --cidr-block {VPC_CIDR} --tag-specifications '
    f"'ResourceType=vpc,Tags=[{{Key=Name,Value={VPC_NAME}}}]'\n"
    + ''.join(f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {cidr} --availability-zone {zone}\n'
              for _, cidr, zone in SUBNETS)
    + f'aws ec2 create-security-group --group-name {APP_SG} --description "Product reader" --vpc-id <vpc-id>\n'
    f'aws ec2 create-security-group --group-name {CACHE_SG} --description "Product cache" --vpc-id <vpc-id>\n'
    f'aws ec2 authorize-security-group-ingress --group-id <cache-sg-id> --protocol tcp --port {CACHE_PORT} '
    '--source-group <app-sg-id>\n'
    f'aws elasticache create-cache-subnet-group --cache-subnet-group-name {SUBNET_GROUP} '
    '--cache-subnet-group-description "Private subnets in two AZs" --subnet-ids <subnet-a> <subnet-b>'
)
PARAMETER_COMMAND = (
    f'aws elasticache create-cache-parameter-group --cache-parameter-group-name {PARAMETER_GROUP} '
    f'--cache-parameter-group-family {PARAMETER_FAMILY} --description "Product cache: evict least recently used keys"\n'
    f'aws elasticache modify-cache-parameter-group --cache-parameter-group-name {PARAMETER_GROUP} '
    f'--parameter-name-values ParameterName=maxmemory-policy,ParameterValue={EVICTION_POLICY}'
)
REPLICATION_COMMAND = (
    f'aws elasticache create-replication-group --replication-group-id {REPLICATION_GROUP} '
    '--replication-group-description "Product catalog cache" --engine redis '
    f'--cache-node-type {NODE_TYPE} --num-cache-clusters 2 --automatic-failover-enabled --multi-az-enabled '
    f'--preferred-cache-cluster-a-zs {" ".join(CACHE_AZS)} --cache-subnet-group-name {SUBNET_GROUP} '
    f'--security-group-ids <cache-sg-id> --cache-parameter-group-name {PARAMETER_GROUP} '
    '--at-rest-encryption-enabled --snapshot-retention-limit 1\n'
    f'aws elasticache describe-replication-groups --replication-group-id {REPLICATION_GROUP} '
    '--query "ReplicationGroups[0].NodeGroups[0].[PrimaryEndpoint,ReaderEndpoint]"'
)
DATABASE_COMMAND = (
    f'aws rds create-db-cluster --db-cluster-identifier {DB_CLUSTER} --engine aurora-postgresql '
    f'--master-username dbadmin --manage-master-user-password --database-name {DB_NAME} --enable-http-endpoint\n'
    f'aws rds create-db-instance --db-instance-identifier {DB_WRITER} --db-cluster-identifier {DB_CLUSTER} '
    '--engine aurora-postgresql --db-instance-class db.r6g.large\n'
    f'aws rds-data execute-statement --resource-arn {DB_CLUSTER_ARN} --secret-arn <master-user-secret-arn> '
    f'--database {DB_NAME} --sql "CREATE TABLE products (sku text PRIMARY KEY, name text, price numeric(10,2))"\n'
    f'aws rds-data execute-statement --resource-arn {DB_CLUSTER_ARN} --secret-arn <master-user-secret-arn> '
    f'--database {DB_NAME} --sql "INSERT INTO products VALUES (\'SKU-100\', \'Trail running shoes\', 89.00), ..."'
)
READER_COMMAND = (
    f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws iam attach-role-policy --role-name {ROLE_NAME} --policy-arn {VPC_ACCESS_POLICY}\n'
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} '
    '--policy-document file://data-access.json\n'
    'zip function.zip reader.py\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime {RUNTIME} --handler {HANDLER} '
    f'--role {ROLE_ARN} --zip-file fileb://function.zip --memory-size 256 --timeout 10 '
    '--vpc-config SubnetIds=<subnet-a>,<subnet-b>,SecurityGroupIds=<app-sg-id> '
    f'--environment "Variables={{CACHE_HOST=<primary-endpoint>,CACHE_PORT=<port>,CACHE_TTL_SECONDS={TTL_SECONDS},'
    f'DB_CLUSTER_ARN={DB_CLUSTER_ARN},DB_SECRET_ARN=<master-user-secret-arn>,DB_NAME={DB_NAME}}}"'
)
MISS_COMMAND = (
    f'aws lambda invoke --function-name {FUNCTION} --cli-binary-format raw-in-base64-out '
    f'--payload \'{{"action":"evict","sku":"{LOOKUP_SKU}"}}\' evict.json   # start cold\n'
    f'aws lambda invoke --function-name {FUNCTION} --cli-binary-format raw-in-base64-out '
    f'--payload \'{{"action":"get","sku":"{LOOKUP_SKU}"}}\' miss.json\n'
    'cat miss.json   # "source": "database", and a TTL on the new cache entry'
)
HIT_COMMAND = (
    f'for i in $(seq {HIT_READS}); do aws lambda invoke --function-name {FUNCTION} --cli-binary-format raw-in-base64-out '
    f'--payload \'{{"action":"get","sku":"{LOOKUP_SKU}"}}\' hit-$i.json; done\n'
    'cat hit-*.json   # "source": "cache", with the TTL counting down'
)
INVALIDATE_COMMAND = (
    f'aws lambda invoke --function-name {FUNCTION} --cli-binary-format raw-in-base64-out '
    f'--payload \'{{"action":"update","sku":"{LOOKUP_SKU}","price":"{NEW_PRICE}"}}\' update.json\n'
    f'aws lambda invoke --function-name {FUNCTION} --cli-binary-format raw-in-base64-out '
    f'--payload \'{{"action":"get","sku":"{LOOKUP_SKU}"}}\' after-update.json   # a miss with the new price\n'
    f'aws lambda invoke --function-name {FUNCTION} --cli-binary-format raw-in-base64-out '
    f'--payload \'{{"action":"get","sku":"{LOOKUP_SKU}"}}\' cached-again.json   # a hit again'
)


def _data_access_policy(secret_arn: str = '<master-user-secret-arn>') -> dict[str, Any]:
    return {
        'Version': '2012-10-17',
        'Statement': [
            {'Sid': 'QueryProductsDatabase', 'Effect': 'Allow', 'Action': 'rds-data:ExecuteStatement',
             'Resource': DB_CLUSTER_ARN},
            {'Sid': 'ReadDatabaseCredentials', 'Effect': 'Allow', 'Action': 'secretsmanager:GetSecretValue',
             'Resource': secret_arn},
            # Floci 2.3.0 authorizes rds-data calls only against Resource "*". Remove this statement on AWS.
            {'Sid': 'FlociOnlyDataApi', 'Effect': 'Allow', 'Action': 'rds-data:ExecuteStatement', 'Resource': '*'},
        ],
    }


LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Speed up reads with an ElastiCache cache-aside pattern',
    'description': (
        'Put a Redis-compatible ElastiCache replication group in private subnets behind a security group, give it an '
        'eviction policy, seed an Aurora PostgreSQL product table, and deploy a Lambda reader that checks the cache '
        'before the database. Watch a miss fill the cache with a TTL, repeat reads hit the cache, and a price update '
        'invalidate the cached copy.'
    ),
    'steps': [
        {
            'key': 'cache-network',
            'title': 'Place the cache in private subnets behind a security group',
            'command': NETWORK_COMMAND,
            'explanation': (
                'ElastiCache runs inside your VPC and has no public endpoint, so its nodes go in private subnets '
                'through a cache subnet group. Give the subnet group subnets in two Availability Zones so a replica '
                'can live in a different zone from the primary. The cache security group allows the Redis port '
                f'({CACHE_PORT}) only from the application\'s security group, not from a CIDR range: any function or '
                'instance that carries the app group can connect, and nothing else can. How Floci emulates this: '
                'Floci stores the VPC, subnets, security groups, and subnet group, but security groups are not '
                'enforced and the cache is reachable from every container on Floci\'s Docker network. The step checks '
                'the subnet group spans two zones and the ingress rule names only the app group.'
            ),
        },
        {
            'key': 'eviction-policy',
            'title': 'Choose an eviction policy in a parameter group',
            'command': PARAMETER_COMMAND,
            'explanation': (
                'A cache fills up. maxmemory-policy decides what happens then: the default, volatile-lru, evicts only '
                'keys with a TTL; noeviction refuses writes; allkeys-lru evicts the least recently used key of any '
                'kind, which suits a cache where every entry can be rebuilt from the database. Parameter groups hold '
                'engine settings, as RDS parameter groups do, and the family (redis7) must match the engine version. '
                'How Floci emulates this: Floci stores the parameter group and its value, and reports the cluster '
                '"in-sync" with it, but does not apply it: the Valkey container keeps maxmemory-policy noeviction with '
                'no memory limit. The step checks the stored value itself, and the reader reports what the container '
                'really uses.'
            ),
        },
        {
            'key': 'replication-group',
            'title': 'Create a Multi-AZ replication group with a replica',
            'command': REPLICATION_COMMAND,
            'explanation': (
                'A replication group is one primary node and up to five read replicas (cluster mode disabled). '
                'Writes go to the primary endpoint, reads can use the reader endpoint, and with automatic failover '
                'and Multi-AZ ElastiCache promotes a replica in another zone when the primary fails, usually within '
                'a minute, keeping the primary endpoint name. Multi-AZ needs at least one replica. Cluster mode '
                'enabled shards keys across up to 500 node groups when one primary is not enough for writes. On the '
                'exam: Redis (and Valkey) offer replication, persistence, snapshots, and data structures; Memcached is '
                'multithreaded and simple, with no replication or persistence. Encrypt at rest and in transit, and '
                'use AUTH or role-based access control for clients. How Floci emulates this: Floci starts one real '
                'Valkey container for the group and serves both the primary and reader endpoints from it on a port '
                'of its own host. The replica is a record only (the container has no replicas), Availability Zones '
                'and security groups are not kept on the nodes, and TestFailover is unsupported. Floci also accepts '
                'Multi-AZ with a single node, which AWS refuses, so the step checks the AWS rules itself.'
            ),
        },
        {
            'key': 'source-database',
            'title': 'Seed the source of truth in Aurora PostgreSQL',
            'command': DATABASE_COMMAND,
            'explanation': (
                'The cache holds copies; the database stays the source of truth. This Aurora PostgreSQL cluster holds '
                'the product catalog, and the Data API lets the reader query it over HTTPS with the cluster\'s managed '
                'master user secret, so the function needs no database driver or connection pool. A cache in front '
                'of the database answers repeat reads in under a millisecond and takes load off it, which is often '
                'cheaper than adding read replicas; read replicas help when queries vary too much to cache. How Floci '
                'emulates this: the database is a real PostgreSQL container, and the Data API runs real SQL against it.'
            ),
        },
        {
            'key': 'deploy-reader',
            'title': 'Deploy a Lambda reader that checks the cache first',
            'command': READER_COMMAND,
            'explanation': (
                'The function does cache-aside, also called lazy loading: GET the key from the cache; on a miss, query '
                f'the database and SET the result with a {TTL_SECONDS}-second TTL; on a write, update the database and '
                'DEL the key. It runs in the private subnets with the app security group, so the cache security group '
                'lets it in, and AWSLambdaVPCAccessExecutionRole lets Lambda create its network interfaces there. The '
                'role may call the Data API on this cluster and read the database secret, nothing else. How Floci '
                'emulates this: the function runs in a real container on Floci\'s Docker network and reaches the cache '
                'and Floci directly; the VPC settings are stored but change nothing. Floci 2.3.0 authorizes Data API '
                'calls only against Resource "*", so the role policy has an extra statement, FlociOnlyDataApi, to '
                'delete on AWS. The step checks the function\'s VPC settings and environment against the cache.'
            ),
            'artifact_label': 'reader.py',
            'artifact': READER_SOURCE,
        },
        {
            'key': 'cache-miss',
            'title': 'Read through a cache miss',
            'command': MISS_COMMAND,
            'explanation': (
                'The first read of a product misses: the reader goes to the database and writes the answer to the '
                'cache with a TTL. Lazy loading only caches what is asked for, so the cache stays small, and a lost '
                'or empty cache node costs latency, not correctness. The cost is a slower first read for every key '
                'and after every expiry. How Floci emulates this: the GET, SET with EX, and TTL run on the real Valkey '
                'container, and the query runs on the real database.'
            ),
        },
        {
            'key': 'cache-hit',
            'title': 'Serve repeat reads from the cache',
            'command': HIT_COMMAND,
            'explanation': (
                f'The next {HIT_READS} reads find the key and never touch the database, and the TTL counts down. The '
                'TTL bounds how stale a cached copy can get, and it lets keys nobody reads age out. Add some random '
                'jitter to TTLs in production so many keys do not expire at once and stampede the database. How Floci '
                'emulates this: the hits come from the real Valkey container. Timings on a laptop are not AWS '
                'timings, but the database is skipped all the same.'
            ),
        },
        {
            'key': 'update-invalidate',
            'title': 'Invalidate the cached copy when the price changes',
            'command': INVALIDATE_COMMAND,
            'explanation': (
                'A price change writes the database and then deletes the cached key, so the next read misses and '
                'caches the new price. Without the delete, readers would see the old price until the TTL ran out. '
                'The other common pattern is write-through: write the new value to the cache as well as the database, '
                'so the cache is never stale, but it caches data nobody may read and still needs a TTL. On the exam, '
                'lazy loading plus a TTL is the usual answer for read-heavy data that can be slightly stale; '
                'write-through when reads must see every write. How Floci emulates this: the update and the DEL are '
                'real, so the step can watch the miss and the new price.'
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


def _marker(step: str, value: Any = True) -> None:
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


# ── Lookups ──────────────────────────────────────────────────────────────────

def _vpc() -> dict[str, Any] | None:
    vpcs = _client('ec2').describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_NAME]}]).get('Vpcs', [])
    return vpcs[0] if vpcs else None


def _subnets(vpc_id: str) -> list[dict[str, Any]]:
    subnets = _client('ec2').describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('Subnets', [])
    cidrs = {cidr for _, cidr, _ in SUBNETS}
    return sorted((subnet for subnet in subnets if subnet.get('CidrBlock') in cidrs), key=lambda s: s['CidrBlock'])


def _security_group(vpc_id: str, name: str) -> dict[str, Any] | None:
    groups = _client('ec2').describe_security_groups(
        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}, {'Name': 'group-name', 'Values': [name]}],
    ).get('SecurityGroups', [])
    return next((group for group in groups if group.get('GroupName') == name), None)


def _subnet_group() -> dict[str, Any] | None:
    try:
        groups = _client('elasticache').describe_cache_subnet_groups(CacheSubnetGroupName=SUBNET_GROUP)
    except ClientError as exc:
        if 'NotFound' in _code(exc):
            return None
        raise
    return next(iter(groups.get('CacheSubnetGroups', [])), None)


def _eviction_policy() -> str | None:
    try:
        params = _client('elasticache').describe_cache_parameters(CacheParameterGroupName=PARAMETER_GROUP)
    except ClientError as exc:
        if 'NotFound' in _code(exc):
            return None
        raise
    return next((item.get('ParameterValue') for item in params.get('Parameters', [])
                 if item.get('ParameterName') == 'maxmemory-policy'), None)


def _parameter_group_exists() -> bool:
    try:
        _client('elasticache').describe_cache_parameter_groups(CacheParameterGroupName=PARAMETER_GROUP)
        return True
    except ClientError as exc:
        if 'NotFound' in _code(exc):
            return False
        raise


def _replication_group() -> dict[str, Any] | None:
    try:
        groups = _client('elasticache').describe_replication_groups(ReplicationGroupId=REPLICATION_GROUP)
    except ClientError as exc:
        if 'NotFound' in _code(exc):
            return None
        raise
    return next(iter(groups.get('ReplicationGroups', [])), None)


def _cache_clusters() -> list[dict[str, Any]]:
    clusters = _client('elasticache').describe_cache_clusters(ShowCacheNodeInfo=True).get('CacheClusters', [])
    return [cluster for cluster in clusters if cluster.get('ReplicationGroupId') == REPLICATION_GROUP]


def _primary_endpoint(group: dict[str, Any] | None) -> dict[str, Any] | None:
    node_groups = (group or {}).get('NodeGroups') or []
    return (node_groups[0].get('PrimaryEndpoint') if node_groups else None) or None


def _db_cluster() -> dict[str, Any] | None:
    try:
        clusters = _client('rds').describe_db_clusters(DBClusterIdentifier=DB_CLUSTER).get('DBClusters', [])
    except ClientError as exc:
        if 'NotFound' in _code(exc):
            return None
        raise
    return clusters[0] if clusters else None


def _db_writer() -> dict[str, Any] | None:
    try:
        instances = _client('rds').describe_db_instances(DBInstanceIdentifier=DB_WRITER).get('DBInstances', [])
    except ClientError as exc:
        if 'NotFound' in _code(exc):
            return None
        raise
    return instances[0] if instances else None


def _secret_arn(cluster: dict[str, Any] | None) -> str | None:
    return ((cluster or {}).get('MasterUserSecret') or {}).get('SecretArn')


def _query(secret_arn: str, sql: str) -> list[list[dict[str, Any]]]:
    response = _client('rds-data').execute_statement(
        resourceArn=DB_CLUSTER_ARN, secretArn=secret_arn, database=DB_NAME, sql=sql)
    return response.get('records', [])


def _products(secret_arn: str) -> dict[str, tuple[str, str]]:
    rows = _query(secret_arn, 'SELECT sku, name, price::text FROM products ORDER BY sku')
    return {row[0].get('stringValue'): (row[1].get('stringValue'), row[2].get('stringValue')) for row in rows}


def _function() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function_configuration(FunctionName=FUNCTION)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _role_exists() -> bool:
    try:
        _client('iam').get_role(RoleName=ROLE_NAME)
        return True
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return False
        raise


def _wait(predicate, attempts: int = 60, delay: float = 2.0) -> bool:
    for attempt in range(attempts):
        if predicate():
            return True
        if attempt < attempts - 1:
            time.sleep(delay)
    return False


def _invoke(payload: dict[str, Any]) -> dict[str, Any]:
    response = _client('lambda').invoke(FunctionName=FUNCTION, Payload=json.dumps(payload).encode())
    body = response.get('Payload')
    raw = body.read() if hasattr(body, 'read') else body
    try:
        data = json.loads(raw or b'null')
    except (TypeError, ValueError):
        data = {'raw': raw.decode() if isinstance(raw, bytes) else str(raw)}
    if response.get('FunctionError'):
        message = data.get('errorMessage') if isinstance(data, dict) else data
        raise RuntimeError(f'{FUNCTION} failed: {message}')
    return data if isinstance(data, dict) else {'value': data}


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo('reader.py', date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, READER_SOURCE)
    return buffer.getvalue()


def _reader_environment() -> dict[str, str] | None:
    endpoint = _primary_endpoint(_replication_group())
    secret = _secret_arn(_db_cluster())
    if not endpoint or not secret:
        return None
    return {
        'CACHE_HOST': str(endpoint.get('Address')),
        'CACHE_PORT': str(endpoint.get('Port')),
        'CACHE_TTL_SECONDS': str(TTL_SECONDS),
        'DB_CLUSTER_ARN': DB_CLUSTER_ARN,
        'DB_SECRET_ARN': secret,
        'DB_NAME': DB_NAME,
    }


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_network() -> tuple[bool, str]:
    vpc = _vpc()
    if not vpc:
        return False, f'VPC {VPC_NAME} was not found.'
    subnets = _subnets(vpc['VpcId'])
    if len({subnet.get('AvailabilityZone') for subnet in subnets}) < 2:
        return False, 'The cache needs private subnets in two Availability Zones.'
    app, cache_sg = _security_group(vpc['VpcId'], APP_SG), _security_group(vpc['VpcId'], CACHE_SG)
    if not app or not cache_sg:
        return False, f'Security groups {APP_SG} and {CACHE_SG} are both needed.'
    rules = cache_sg.get('IpPermissions') or []
    port_rules = [rule for rule in rules if rule.get('IpProtocol') == 'tcp'
                  and rule.get('FromPort') == CACHE_PORT and rule.get('ToPort') == CACHE_PORT]
    sources = {pair.get('GroupId') for rule in port_rules for pair in rule.get('UserIdGroupPairs') or []}
    if app['GroupId'] not in sources:
        return False, f'{CACHE_SG} must allow TCP {CACHE_PORT} from {APP_SG}.'
    open_ranges = [item.get('CidrIp') for rule in rules for item in rule.get('IpRanges') or []]
    open_ranges += [item.get('CidrIpv6') for rule in rules for item in rule.get('Ipv6Ranges') or []]
    if open_ranges:
        return False, f'{CACHE_SG} also allows CIDR ranges ({", ".join(open_ranges)}); allow only {APP_SG}.'
    if any(rule not in port_rules for rule in rules):
        return False, f'{CACHE_SG} allows more than TCP {CACHE_PORT}.'
    group = _subnet_group()
    if not group:
        return False, f'Cache subnet group {SUBNET_GROUP} was not found.'
    ids = {subnet['SubnetId'] for subnet in subnets}
    members = group.get('Subnets') or []
    zones = {(item.get('SubnetAvailabilityZone') or {}).get('Name') for item in members}
    if {item.get('SubnetIdentifier') for item in members} != ids or len(zones) < 2:
        return False, f'{SUBNET_GROUP} must hold both private subnets, one in each zone.'
    return True, (f'{SUBNET_GROUP} spans {", ".join(sorted(z for z in zones if z))}, and {CACHE_SG} allows TCP '
                  f'{CACHE_PORT} only from {APP_SG}. Floci does not enforce security groups.')


def _check_eviction() -> tuple[bool, str]:
    if not _parameter_group_exists():
        return False, f'Parameter group {PARAMETER_GROUP} was not found.'
    policy = _eviction_policy()
    if policy != EVICTION_POLICY:
        return False, f'maxmemory-policy is {policy or "unset"}; set it to {EVICTION_POLICY}.'
    return True, f'{PARAMETER_GROUP} evicts with {EVICTION_POLICY}.'


def _replication_problems(group: dict[str, Any] | None) -> list[str]:
    if not group:
        return [f'Replication group {REPLICATION_GROUP} was not found.']
    problems = []
    members = group.get('MemberClusters') or []
    roles = [member.get('CurrentRole') for node_group in group.get('NodeGroups') or []
             for member in node_group.get('NodeGroupMembers') or []]
    if len(members) < 2 or 'replica' not in roles:
        problems.append('it needs a primary and at least one replica')
    if group.get('AutomaticFailover') != 'enabled':
        problems.append('automatic failover is off')
    if group.get('MultiAZ') != 'enabled':
        problems.append('Multi-AZ is off')
    if not group.get('AtRestEncryptionEnabled'):
        problems.append('encryption at rest is off')
    if not _primary_endpoint(group):
        problems.append('it has no primary endpoint')
    clusters = _cache_clusters()
    if clusters:
        if any(cluster.get('CacheSubnetGroupName') != SUBNET_GROUP for cluster in clusters):
            problems.append(f'its nodes are not in {SUBNET_GROUP}')
        if any((cluster.get('CacheParameterGroup') or {}).get('CacheParameterGroupName') != PARAMETER_GROUP
               for cluster in clusters):
            problems.append(f'its nodes do not use {PARAMETER_GROUP}')
    return problems


def _check_replication() -> tuple[bool, str]:
    group = _replication_group()
    problems = _replication_problems(group)
    if problems:
        return False, f'{REPLICATION_GROUP}: ' + '; '.join(problems) + '.'
    if group.get('Status') != 'available':
        return False, f'{REPLICATION_GROUP} is {group.get("Status")}; wait for available.'
    endpoint = _primary_endpoint(group)
    return True, (f'{REPLICATION_GROUP} has a primary and a replica with automatic failover and Multi-AZ; the primary '
                  f'endpoint is {endpoint.get("Address")}:{endpoint.get("Port")}.')


def _check_database() -> tuple[bool, str]:
    cluster = _db_cluster()
    if not cluster:
        return False, f'Aurora cluster {DB_CLUSTER} was not found.'
    if not cluster.get('HttpEndpointEnabled', True):
        return False, f'{DB_CLUSTER} has the Data API off.'
    secret = _secret_arn(cluster)
    writer = _db_writer()
    if not secret or not writer:
        return False, f'{DB_CLUSTER} needs a writer instance and a managed master user secret.'
    products = _products(secret)
    missing = [sku for sku, _, _ in PRODUCTS if sku not in products]
    if missing:
        return False, f'The products table is missing {", ".join(missing)}.'
    return True, f'{len(products)} products are in {DB_NAME}.products on {DB_CLUSTER}.'


def _check_reader() -> tuple[bool, str]:
    function = _function()
    if not function:
        return False, f'Function {FUNCTION} was not found.'
    if function.get('Role') != ROLE_ARN:
        return False, f'{FUNCTION} must run as {ROLE_NAME}.'
    expected = _reader_environment()
    if not expected:
        return False, 'Create the replication group and the database first.'
    variables = (function.get('Environment') or {}).get('Variables') or {}
    wrong = [key for key, value in expected.items() if variables.get(key) != value]
    if wrong:
        return False, f'{FUNCTION} environment does not match the cache and database: {", ".join(wrong)}.'
    vpc = _vpc()
    config = function.get('VpcConfig') or {}
    subnets = {subnet['SubnetId'] for subnet in _subnets(vpc['VpcId'])} if vpc else set()
    app = _security_group(vpc['VpcId'], APP_SG) if vpc else None
    if not subnets or set(config.get('SubnetIds') or []) != subnets:
        return False, f'{FUNCTION} must run in both private subnets.'
    if not app or app['GroupId'] not in (config.get('SecurityGroupIds') or []):
        return False, f'{FUNCTION} must carry {APP_SG}, the group the cache allows.'
    return True, f'{FUNCTION} runs in two private subnets with {APP_SG} and points at the primary endpoint.'


def _check_marker(step: str, needs: str) -> tuple[bool, str]:
    marker = _marked(step)
    if not marker:
        return False, f'Run "{needs}".'
    if not _function():
        return False, f'Function {FUNCTION} was not found.'
    return True, marker.get('message', 'Verified.')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_cache_network() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    vpc = _vpc()
    if not vpc:
        vpc = ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=[
            {'ResourceType': 'vpc', 'Tags': [{'Key': 'Name', 'Value': VPC_NAME}, {'Key': 'Lab', 'Value': LAB_KEY}]}])['Vpc']
    vpc_id = vpc['VpcId']
    existing = {subnet['CidrBlock'] for subnet in _subnets(vpc_id)}
    for name, cidr, zone in SUBNETS:
        if cidr not in existing:
            ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=zone, TagSpecifications=[
                {'ResourceType': 'subnet', 'Tags': [{'Key': 'Name', 'Value': name}]}])
    subnets = _subnets(vpc_id)
    groups = {}
    for name, description in ((APP_SG, 'Product reader (ElastiCache lab)'), (CACHE_SG, 'Product cache (ElastiCache lab)')):
        group = _security_group(vpc_id, name)
        if not group:
            ec2.create_security_group(GroupName=name, Description=description, VpcId=vpc_id)
            group = _security_group(vpc_id, name)
        groups[name] = group
    cache_sg, app_id = groups[CACHE_SG], groups[APP_SG]['GroupId']
    wanted = {'IpProtocol': 'tcp', 'FromPort': CACHE_PORT, 'ToPort': CACHE_PORT, 'UserIdGroupPairs': [{'GroupId': app_id}]}
    strays, has_rule = [], False
    for rule in cache_sg.get('IpPermissions') or []:
        pairs = [pair.get('GroupId') for pair in rule.get('UserIdGroupPairs') or []]
        exact = (rule.get('IpProtocol') == 'tcp' and rule.get('FromPort') == CACHE_PORT
                 and rule.get('ToPort') == CACHE_PORT and pairs == [app_id]
                 and not rule.get('IpRanges') and not rule.get('Ipv6Ranges'))
        has_rule = has_rule or exact
        if not exact:
            strays.append({key: value for key, value in rule.items()
                           if key in {'IpProtocol', 'FromPort', 'ToPort', 'UserIdGroupPairs', 'IpRanges', 'Ipv6Ranges'}})
    if strays:
        ec2.revoke_security_group_ingress(GroupId=cache_sg['GroupId'], IpPermissions=strays)
    if not has_rule:
        try:
            ec2.authorize_security_group_ingress(GroupId=cache_sg['GroupId'], IpPermissions=[wanted])
        except ClientError as exc:
            if _code(exc) != 'InvalidPermission.Duplicate':
                raise
    elasticache = _client('elasticache')
    group = _subnet_group()
    ids = [subnet['SubnetId'] for subnet in subnets]
    if not group:
        elasticache.create_cache_subnet_group(
            CacheSubnetGroupName=SUBNET_GROUP, CacheSubnetGroupDescription='Private subnets in two AZs (ElastiCache lab)',
            SubnetIds=ids)
    elif {item.get('SubnetIdentifier') for item in group.get('Subnets') or []} != set(ids):
        elasticache.modify_cache_subnet_group(CacheSubnetGroupName=SUBNET_GROUP, SubnetIds=ids)
    response = {
        'VpcId': vpc_id,
        'Subnets': [{key: subnet.get(key) for key in ('SubnetId', 'CidrBlock', 'AvailabilityZone')} for subnet in subnets],
        'SecurityGroups': {name: _security_group(vpc_id, name) for name in (APP_SG, CACHE_SG)},
        'CacheSubnetGroup': _subnet_group(),
    }
    verified, message = _check_network()
    return _result('cache-network', NETWORK_COMMAND, response, verified, message, started)


def step_eviction_policy() -> dict[str, Any]:
    started = time.perf_counter()
    elasticache = _client('elasticache')
    if not _parameter_group_exists():
        elasticache.create_cache_parameter_group(
            CacheParameterGroupName=PARAMETER_GROUP, CacheParameterGroupFamily=PARAMETER_FAMILY,
            Description='Product cache: evict least recently used keys (ElastiCache lab)')
    elasticache.modify_cache_parameter_group(
        CacheParameterGroupName=PARAMETER_GROUP,
        ParameterNameValues=[{'ParameterName': 'maxmemory-policy', 'ParameterValue': EVICTION_POLICY}])
    response = {'CacheParameterGroupName': PARAMETER_GROUP, 'Family': PARAMETER_FAMILY,
                'maxmemory-policy': _eviction_policy()}
    verified, message = _check_eviction()
    return _result('eviction-policy', PARAMETER_COMMAND, response, verified, message, started)


def step_replication_group() -> dict[str, Any]:
    started = time.perf_counter()
    network_ok, problem = _check_network()
    if not network_ok:
        return _result('replication-group', REPLICATION_COMMAND, {}, False,
                       f'Fix the network first: {problem}', started)
    if not _check_eviction()[0]:
        return _result('replication-group', REPLICATION_COMMAND, {}, False,
                       'Create the parameter group first: the replication group uses it.', started)
    elasticache = _client('elasticache')
    group = _replication_group()
    if group and _replication_problems(group):
        elasticache.delete_replication_group(ReplicationGroupId=REPLICATION_GROUP, RetainPrimaryCluster=False)
        _wait(lambda: _replication_group() is None, attempts=30)
        group = None
    if not group:
        cache_sg = _security_group(_vpc()['VpcId'], CACHE_SG)
        elasticache.create_replication_group(
            ReplicationGroupId=REPLICATION_GROUP, ReplicationGroupDescription='Product catalog cache (ElastiCache lab)',
            Engine='redis', CacheNodeType=NODE_TYPE, NumCacheClusters=2, AutomaticFailoverEnabled=True,
            MultiAZEnabled=True, PreferredCacheClusterAZs=CACHE_AZS, CacheSubnetGroupName=SUBNET_GROUP,
            SecurityGroupIds=[cache_sg['GroupId']], CacheParameterGroupName=PARAMETER_GROUP,
            AtRestEncryptionEnabled=True, SnapshotRetentionLimit=1,
            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    _wait(lambda: (_replication_group() or {}).get('Status') == 'available', attempts=60)
    group = _replication_group()
    response = {
        'ReplicationGroup': group,
        'CacheClusters': [{key: cluster.get(key) for key in (
            'CacheClusterId', 'CacheClusterStatus', 'PreferredAvailabilityZone', 'SecurityGroups', 'CacheSubnetGroupName',
            'CacheParameterGroup', 'CacheNodes')} for cluster in _cache_clusters()],
    }
    verified, message = _check_replication()
    if verified:
        missing = [cluster['CacheClusterId'] for cluster in response['CacheClusters']
                   if not cluster.get('PreferredAvailabilityZone') or not cluster.get('SecurityGroups')]
        if missing:
            message += (' Floci did not keep the Availability Zones or security groups on the nodes, and both nodes '
                        'share one Valkey container.')
    return _result('replication-group', REPLICATION_COMMAND, response, verified, message, started)


def step_source_database() -> dict[str, Any]:
    started = time.perf_counter()
    rds = _client('rds')
    if not _db_cluster():
        rds.create_db_cluster(
            DBClusterIdentifier=DB_CLUSTER, Engine='aurora-postgresql', MasterUsername='dbadmin',
            ManageMasterUserPassword=True, DatabaseName=DB_NAME, EnableHttpEndpoint=True,
            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    if not _db_writer():
        rds.create_db_instance(DBInstanceIdentifier=DB_WRITER, DBClusterIdentifier=DB_CLUSTER,
                               Engine='aurora-postgresql', DBInstanceClass='db.r6g.large')
    _wait(lambda: (_db_writer() or {}).get('DBInstanceStatus') == 'available', attempts=90)
    secret = _secret_arn(_db_cluster())
    if not secret:
        return _result('source-database', DATABASE_COMMAND, {}, False,
                       f'{DB_CLUSTER} reports no managed master user secret.', started)
    last_error = None
    for attempt in range(15):
        try:
            for sql in SEED_SQL:
                _query(secret, sql)
            last_error = None
            break
        except ClientError as exc:  # the database container can still be starting
            last_error = exc
            time.sleep(2)
    if last_error:
        raise last_error
    cluster = _db_cluster()
    response = {
        'DBCluster': {key: cluster.get(key) for key in ('DBClusterIdentifier', 'Status', 'Engine', 'Endpoint',
                                                        'ReaderEndpoint', 'HttpEndpointEnabled', 'MasterUserSecret')},
        'Products': [{'sku': sku, 'name': name, 'price': price} for sku, (name, price) in _products(secret).items()],
    }
    verified, message = _check_database()
    return _result('source-database', DATABASE_COMMAND, response, verified, message, started)


def step_deploy_reader() -> dict[str, Any]:
    started = time.perf_counter()
    environment = _reader_environment()
    vpc = _vpc()
    if not environment or not vpc:
        return _result('deploy-reader', READER_COMMAND, {}, False,
                       'Create the network, the replication group, and the database first.', started)
    iam = _client('iam')
    if not _role_exists():
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
                        Description='Product reader for the ElastiCache lab')
    iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=VPC_ACCESS_POLICY)
    policy = _data_access_policy(environment['DB_SECRET_ARN'])
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY, PolicyDocument=json.dumps(policy))
    subnets = [subnet['SubnetId'] for subnet in _subnets(vpc['VpcId'])]
    app = _security_group(vpc['VpcId'], APP_SG)
    settings = {
        'Role': ROLE_ARN, 'Handler': HANDLER, 'Runtime': RUNTIME, 'MemorySize': 256, 'Timeout': 10,
        'Environment': {'Variables': environment},
        'VpcConfig': {'SubnetIds': subnets, 'SecurityGroupIds': [app['GroupId']] if app else []},
    }
    lam = _client('lambda')
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_zip())
        _wait(lambda: (_function() or {}).get('LastUpdateStatus') in (None, 'Successful', 'Failed'), attempts=30, delay=1)
        lam.update_function_configuration(FunctionName=FUNCTION, **settings)
    else:
        lam.create_function(FunctionName=FUNCTION, Code={'ZipFile': _zip()},
                            Description='Cache-aside product reader (ElastiCache lab)', **settings)
    _wait(lambda: (_function() or {}).get('State') in (None, 'Active') and
          (_function() or {}).get('LastUpdateStatus') in (None, 'Successful'), attempts=30, delay=1)
    function = _function() or {}
    response = {
        'FunctionName': FUNCTION,
        'State': function.get('State'),
        'Role': function.get('Role'),
        'VpcConfig': function.get('VpcConfig'),
        'Environment': (function.get('Environment') or {}).get('Variables'),
        'RolePolicy': policy,
    }
    verified, message = _check_reader()
    if verified:
        message += (' Floci stores the VPC settings but runs the function on its Docker network, and the role\'s '
                    'FlociOnlyDataApi statement is only there because Floci matches Data API calls against "*".')
    return _result('deploy-reader', READER_COMMAND, response, verified, message, started)


def _need_reader(step: str, command: str, started: float) -> dict[str, Any] | None:
    ok, problem = _check_reader()
    if ok:
        return None
    return _result(step, command, {}, False, f'Deploy the reader first: {problem}', started)


def _expected_price() -> str | None:
    secret = _secret_arn(_db_cluster())
    if not secret:
        return None
    return (_products(secret).get(LOOKUP_SKU) or (None, None))[1]


def step_cache_miss() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _need_reader('cache-miss', MISS_COMMAND, started)
    if blocked:
        return blocked
    evicted = _invoke({'action': 'evict', 'sku': LOOKUP_SKU})
    miss = _invoke({'action': 'get', 'sku': LOOKUP_SKU})
    inspect = _invoke({'action': 'inspect', 'sku': LOOKUP_SKU})
    response = {'Evict': evicted, 'Read': miss, 'CacheEntry': inspect}
    ttl = miss.get('ttl')
    product = miss.get('product') or {}
    if miss.get('source') != 'database' or product.get('sku') != LOOKUP_SKU:
        return _result('cache-miss', MISS_COMMAND, response, False,
                       f'Expected a miss served from the database, got {miss.get("source")}.', started)
    if not isinstance(ttl, int) or not 0 < ttl <= TTL_SECONDS:
        return _result('cache-miss', MISS_COMMAND, response, False,
                       f'The cached entry should expire within {TTL_SECONDS} seconds; its TTL is {ttl}.', started)
    if not inspect.get('cached'):
        return _result('cache-miss', MISS_COMMAND, response, False, 'The reader did not fill the cache.', started)
    policy = inspect.get('maxmemory_policy') or []
    applied = policy[1] if isinstance(policy, list) and len(policy) > 1 else None
    message = (f'Miss: {LOOKUP_SKU} came from the database in {miss.get("db_ms")} ms and is cached for {ttl} seconds.')
    if applied and applied != EVICTION_POLICY:
        message += f' The Valkey container still uses maxmemory-policy {applied}: Floci does not apply parameter groups.'
    _marker('cache-miss', {'message': message})
    return _result('cache-miss', MISS_COMMAND, response, True, message, started)


def step_cache_hit() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _need_reader('cache-hit', HIT_COMMAND, started)
    if blocked:
        return blocked
    reads = [_invoke({'action': 'get', 'sku': LOOKUP_SKU}) for _ in range(HIT_READS)]
    hits = [read for read in reads if read.get('source') == 'cache']
    ttls = [read.get('ttl') for read in reads]
    response = {'Reads': reads, 'HitRatio': f'{len(hits)}/{len(reads)}'}
    if reads and reads[0].get('source') == 'database':
        response['Note'] = 'The first read missed (the entry had expired or was evicted) and refilled the cache.'
        hits = [read for read in reads[1:] if read.get('source') == 'cache']
        reads = reads[1:]
    if len(hits) != len(reads):
        return _result('cache-hit', HIT_COMMAND, response, False,
                       f'Only {len(hits)} of {len(reads)} repeat reads came from the cache.', started)
    if any(not isinstance(ttl, int) or not 0 < ttl <= TTL_SECONDS for ttl in ttls):
        return _result('cache-hit', HIT_COMMAND, response, False, f'Unexpected TTLs: {ttls}.', started)
    average = sum(float(read.get('cache_ms') or 0) for read in hits) / len(hits)
    message = (f'{len(hits)} reads came from the cache (about {average:.1f} ms each, no database query) with the TTL '
               f'at {ttls[-1]} seconds.')
    _marker('cache-hit', {'message': message})
    return _result('cache-hit', HIT_COMMAND, response, True, message, started)


def step_update_invalidate() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _need_reader('update-invalidate', INVALIDATE_COMMAND, started)
    if blocked:
        return blocked
    _invoke({'action': 'get', 'sku': LOOKUP_SKU})  # make sure a copy is cached before the write
    update = _invoke({'action': 'update', 'sku': LOOKUP_SKU, 'price': NEW_PRICE})
    after = _invoke({'action': 'get', 'sku': LOOKUP_SKU})
    again = _invoke({'action': 'get', 'sku': LOOKUP_SKU})
    response = {'Update': update, 'ReadAfterUpdate': after, 'NextRead': again, 'DatabasePrice': _expected_price()}
    price = (after.get('product') or {}).get('price')
    if after.get('source') != 'database':
        return _result('update-invalidate', INVALIDATE_COMMAND, response, False,
                       'The read after the update came from the cache: the old copy was not invalidated.', started)
    if price is None or float(price) != float(NEW_PRICE):
        return _result('update-invalidate', INVALIDATE_COMMAND, response, False,
                       f'The read after the update returned price {price}, not {NEW_PRICE}.', started)
    if again.get('source') != 'cache':
        return _result('update-invalidate', INVALIDATE_COMMAND, response, False,
                       'The next read should come from the cache again.', started)
    message = (f'The update deleted the cached copy ({update.get("invalidated")} key), the next read missed and '
               f'cached the new price {price}, and the read after that was a hit.')
    _marker('update-invalidate', {'message': message})
    return _result('update-invalidate', INVALIDATE_COMMAND, response, True, message, started)


RUNNERS = {
    'cache-network': step_cache_network,
    'eviction-policy': step_eviction_policy,
    'replication-group': step_replication_group,
    'source-database': step_source_database,
    'deploy-reader': step_deploy_reader,
    'cache-miss': step_cache_miss,
    'cache-hit': step_cache_hit,
    'update-invalidate': step_update_invalidate,
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
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    checks = {
        'cache-network': lambda: _check_network()[0],
        'eviction-policy': lambda: _check_eviction()[0],
        'replication-group': lambda: _check_replication()[0],
        'source-database': lambda: _check_database()[0],
        'deploy-reader': lambda: _check_reader()[0],
        'cache-miss': lambda: _check_marker('cache-miss', 'Read through a cache miss')[0],
        'cache-hit': lambda: _check_marker('cache-hit', 'Serve repeat reads from the cache')[0],
        'update-invalidate': lambda: _check_marker('update-invalidate', 'Invalidate the cached copy')[0],
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


def reset(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    """Delete the function and role, the replication group, parameter and subnet groups, the database, and the VPC."""
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
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
            if 'NotFound' in code or code in {'NoSuchEntity', 'ResourceNotFoundException'}:
                return False
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')
        return False

    lam, iam, elasticache, rds, ec2 = (_client(name) for name in ('lambda', 'iam', 'elasticache', 'rds', 'ec2'))
    attempt(f'function:{FUNCTION}', lambda: lam.delete_function(FunctionName=FUNCTION))
    attempt(f'role-policy:{ROLE_POLICY}', lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY))
    attempt('role-policy:AWSLambdaVPCAccessExecutionRole',
            lambda: iam.detach_role_policy(RoleName=ROLE_NAME, PolicyArn=VPC_ACCESS_POLICY))
    attempt(f'role:{ROLE_NAME}', lambda: iam.delete_role(RoleName=ROLE_NAME))

    if attempt(f'replication-group:{REPLICATION_GROUP}', lambda: elasticache.delete_replication_group(
            ReplicationGroupId=REPLICATION_GROUP, RetainPrimaryCluster=False)):
        _wait(lambda: _safe_none(_replication_group), attempts=30)
    attempt(f'parameter-group:{PARAMETER_GROUP}',
            lambda: elasticache.delete_cache_parameter_group(CacheParameterGroupName=PARAMETER_GROUP))
    attempt(f'subnet-group:{SUBNET_GROUP}',
            lambda: elasticache.delete_cache_subnet_group(CacheSubnetGroupName=SUBNET_GROUP))

    if attempt(f'db-instance:{DB_WRITER}', lambda: rds.delete_db_instance(
            DBInstanceIdentifier=DB_WRITER, SkipFinalSnapshot=True)):
        _wait(lambda: _safe_none(_db_writer), attempts=30)
    attempt(f'db-cluster:{DB_CLUSTER}', lambda: rds.delete_db_cluster(
        DBClusterIdentifier=DB_CLUSTER, SkipFinalSnapshot=True))

    try:
        vpc = _vpc()
    except (ClientError, BotoCoreError) as exc:
        vpc = None
        errors.append(f'vpc: {exc}')
    if vpc:
        vpc_id = vpc['VpcId']
        groups = [group for group in (_security_group(vpc_id, CACHE_SG), _security_group(vpc_id, APP_SG)) if group]
        for group in groups:
            if group.get('IpPermissions'):
                attempt(f"ingress:{group['GroupName']}", lambda group=group: ec2.revoke_security_group_ingress(
                    GroupId=group['GroupId'], IpPermissions=group['IpPermissions']))
        for group in groups:
            attempt(f"security-group:{group['GroupName']}",
                    lambda group=group: ec2.delete_security_group(GroupId=group['GroupId']))
        for subnet in _subnets(vpc_id):
            attempt(f"subnet:{subnet['SubnetId']}", lambda subnet=subnet: ec2.delete_subnet(SubnetId=subnet['SubnetId']))
        attempt(f'vpc:{vpc_id}', lambda: ec2.delete_vpc(VpcId=vpc_id))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws iam delete-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY}\n'
            f'aws iam detach-role-policy --role-name {ROLE_NAME} --policy-arn {VPC_ACCESS_POLICY}\n'
            f'aws iam delete-role --role-name {ROLE_NAME}\n'
            f'aws elasticache delete-replication-group --replication-group-id {REPLICATION_GROUP}\n'
            f'aws elasticache delete-cache-parameter-group --cache-parameter-group-name {PARAMETER_GROUP}\n'
            f'aws elasticache delete-cache-subnet-group --cache-subnet-group-name {SUBNET_GROUP}\n'
            f'aws rds delete-db-instance --db-instance-identifier {DB_WRITER} --skip-final-snapshot\n'
            f'aws rds delete-db-cluster --db-cluster-identifier {DB_CLUSTER} --skip-final-snapshot\n'
            'aws ec2 delete-security-group --group-id <sg-id>   # cache, then app\n'
            'aws ec2 delete-subnet --subnet-id <subnet-id>\n'
            'aws ec2 delete-vpc --vpc-id <vpc-id>'
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
            else 'The reader, its role, the cache, its parameter and subnet groups, the database, and the VPC are deleted.',
        },
    }


def _safe_none(lookup) -> bool:
    try:
        return lookup() is None
    except (ClientError, BotoCoreError):
        return True
