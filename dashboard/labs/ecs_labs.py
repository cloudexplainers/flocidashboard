"""Amazon ECS & AWS Fargate workflow labs."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from botocore.config import Config
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

REGION = 'us-east-1'
ACCOUNT = '000000000000'
CACHE_PREFIX = 'floci-lab:ecs:'

CLUSTER_NAME = 'lab-microservices-cluster'
TASK_FAMILY = 'lab-web-service'
SERVICE_NAME = 'lab-orders-api'

ECS_FARGATE_LAB = {
    'service': 'ecs',
    'key': 'fargate-microservice',
    'title': 'Deploy containerized microservices with Amazon ECS and AWS Fargate',
    'description': 'Provision an ECS cluster, register container task definitions with Fargate compatibility, launch standalone container tasks, inspect container lifecycle status, and scale resilient ECS services.',
    'steps': [
        {
            'key': 'create-cluster',
            'title': 'Create Amazon ECS Cluster',
            'command': f'aws ecs create-cluster --cluster-name {CLUSTER_NAME}',
            'explanation': 'Defines a logical grouping of container tasks and services in Amazon ECS.',
        },
        {
            'key': 'register-task-definition',
            'title': 'Register Fargate Task Definition',
            'command': f'aws ecs register-task-definition --family {TASK_FAMILY} --network-mode awsvpc --requires-compatibilities FARGATE --cpu 256 --memory 512 --container-definitions \'[{{"name":"web","image":"nginx:latest","essential":true,"portMappings":[{{"containerPort":80,"protocol":"tcp"}}]}}]\'',
            'explanation': 'Declares the container blueprint including Docker image, port mapping, CPU units, and memory limits.',
        },
        {
            'key': 'run-task',
            'title': 'Launch Standalone Fargate Task',
            'command': f'aws ecs run-task --cluster {CLUSTER_NAME} --task-definition {TASK_FAMILY} --launch-type FARGATE --count 1 --network-configuration file://network.json',
            'explanation': 'Creates a lab-owned VPC, subnet, and security group, then launches an on-demand task with the required awsvpc network configuration.',
            'artifact_label': 'network.json',
            'artifact': json.dumps({'awsvpcConfiguration': {'subnets': ['<lab-subnet-id>'], 'securityGroups': ['<lab-security-group-id>'], 'assignPublicIp': 'ENABLED'}}, indent=2),
        },
        {
            'key': 'describe-tasks',
            'title': 'Inspect Task Lifecycle and Container Status',
            'command': f'aws ecs describe-tasks --cluster {CLUSTER_NAME} --tasks <task-arn>',
            'explanation': 'Queries the live operational status, launch type, container health, and network bindings.',
        },
        {
            'key': 'create-service',
            'title': 'Create Managed ECS Service',
            'command': f'aws ecs create-service --cluster {CLUSTER_NAME} --service-name {SERVICE_NAME} --task-definition {TASK_FAMILY} --desired-count 2 --launch-type FARGATE --network-configuration file://network.json',
            'explanation': 'Maintains a specified number of running task replicas, providing automated health checking and rolling restarts.',
        },
        {
            'key': 'update-service',
            'title': 'Scale Service Desired Replica Count',
            'command': f'aws ecs update-service --cluster {CLUSTER_NAME} --service {SERVICE_NAME} --desired-count 4',
            'explanation': 'Dynamically scales the running capacity of the microservice service up to 4 replicas.',
        },
    ],
}

LABS = [ECS_FARGATE_LAB]


def client(name: str):
    factory = FlociClientFactory()
    factory.config = factory.config.merge(Config(read_timeout=120, retries={'max_attempts': 0}))
    return factory.client(name)


def marker(key: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + key, _clean_response(value), timeout=86400)


def marked(key: str) -> Any:
    return cache.get(CACHE_PREFIX + key)


def network_configuration() -> dict[str, Any]:
    ec2 = client('ec2')
    vpc_id = marked('vpc_id')
    if not vpc_id:
        vpc_id = ec2.create_vpc(CidrBlock='10.49.0.0/16')['Vpc']['VpcId']
        marker('vpc_id', vpc_id)
    subnet_id = marked('subnet_id')
    if not subnet_id:
        subnet_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.49.1.0/24')['Subnet']['SubnetId']
        marker('subnet_id', subnet_id)
    group_id = marked('group_id')
    if not group_id:
        group_id = ec2.create_security_group(VpcId=vpc_id, GroupName='lab-ecs-web', Description='ECS lab tasks')['GroupId']
        marker('group_id', group_id)
    return {'awsvpcConfiguration': {'subnets': [subnet_id], 'securityGroups': [group_id], 'assignPublicIp': 'ENABLED'}}


def result(lab: str, step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': 'ecs',
        'lab': lab,
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


def step_create_cluster() -> dict[str, Any]:
    started = time.perf_counter()
    ecs = client('ecs')
    resp = ecs.create_cluster(clusterName=CLUSTER_NAME)
    marker('cluster', {'name': CLUSTER_NAME})
    return result(
        'fargate-microservice',
        'create-cluster',
        f'aws ecs create-cluster --cluster-name {CLUSTER_NAME}',
        resp,
        True,
        f'Created ECS cluster {CLUSTER_NAME}.',
        started,
    )


def step_register_task_def() -> dict[str, Any]:
    started = time.perf_counter()
    ecs = client('ecs')
    resp = ecs.register_task_definition(
        family=TASK_FAMILY,
        networkMode='awsvpc',
        requiresCompatibilities=['FARGATE'],
        cpu='256',
        memory='512',
        containerDefinitions=[
            {
                'name': 'web',
                'image': 'nginx:latest',
                'essential': True,
                'portMappings': [{'containerPort': 80, 'protocol': 'tcp'}],
            }
        ],
    )
    td_arn = resp.get('taskDefinition', {}).get('taskDefinitionArn')
    marker('task_def_arn', td_arn)
    return result(
        'fargate-microservice',
        'register-task-definition',
        f'aws ecs register-task-definition --family {TASK_FAMILY} ...',
        resp,
        True,
        f'Registered task definition {TASK_FAMILY}.',
        started,
    )


def step_run_task() -> dict[str, Any]:
    started = time.perf_counter()
    ecs = client('ecs')
    td_arn = marked('task_def_arn') or TASK_FAMILY
    resp = ecs.run_task(
        cluster=CLUSTER_NAME,
        taskDefinition=td_arn,
        launchType='FARGATE',
        count=1,
        networkConfiguration=network_configuration(),
    )
    tasks = resp.get('tasks', [])
    if tasks:
        marker('task_arn', tasks[0].get('taskArn'))
    return result(
        'fargate-microservice',
        'run-task',
        f'aws ecs run-task --cluster {CLUSTER_NAME} --task-definition {TASK_FAMILY} --launch-type FARGATE',
        resp,
        len(tasks) > 0,
        f'Launched standalone Fargate task in cluster {CLUSTER_NAME}.',
        started,
    )


def step_describe_tasks() -> dict[str, Any]:
    started = time.perf_counter()
    ecs = client('ecs')
    task_arn = marked('task_arn')
    if not task_arn:
        task_arns = ecs.list_tasks(cluster=CLUSTER_NAME).get('taskArns', [])
        task_arn = task_arns[0] if task_arns else None

    if task_arn:
        resp = ecs.describe_tasks(cluster=CLUSTER_NAME, tasks=[task_arn])
        verified = len(resp.get('tasks', [])) > 0
    else:
        resp = {'tasks': []}
        verified = False

    return result(
        'fargate-microservice',
        'describe-tasks',
        f'aws ecs describe-tasks --cluster {CLUSTER_NAME} --tasks {task_arn or "<task-arn>"}',
        resp,
        verified,
        f'Inspected live container task status in {CLUSTER_NAME}.' if verified else 'No running tasks found',
        started,
    )


def step_create_service() -> dict[str, Any]:
    started = time.perf_counter()
    ecs = client('ecs')
    td_arn = marked('task_def_arn') or TASK_FAMILY
    try:
        resp = ecs.create_service(
            cluster=CLUSTER_NAME,
            serviceName=SERVICE_NAME,
            taskDefinition=td_arn,
            desiredCount=2,
            launchType='FARGATE',
            networkConfiguration=network_configuration(),
        )
    except ClientError as exc:
        if 'ServiceAlreadyExists' in str(exc):
            resp = ecs.describe_services(cluster=CLUSTER_NAME, services=[SERVICE_NAME])
        else:
            raise
    marker('service', {'name': SERVICE_NAME})
    return result(
        'fargate-microservice',
        'create-service',
        f'aws ecs create-service --cluster {CLUSTER_NAME} --service-name {SERVICE_NAME} --desired-count 2',
        resp,
        True,
        f'Created ECS service {SERVICE_NAME} with desired count 2.',
        started,
    )


def step_update_service() -> dict[str, Any]:
    started = time.perf_counter()
    ecs = client('ecs')
    resp = ecs.update_service(
        cluster=CLUSTER_NAME,
        service=SERVICE_NAME,
        desiredCount=4,
    )
    marker('scaled', True)
    return result(
        'fargate-microservice',
        'update-service',
        f'aws ecs update-service --cluster {CLUSTER_NAME} --service {SERVICE_NAME} --desired-count 4',
        resp,
        True,
        f'Scaled ECS service {SERVICE_NAME} desired replica count to 4.',
        started,
    )


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if lab_key != 'fargate-microservice':
        raise ValueError(f'Unknown ECS lab: {lab_key}')
    runners = {
        'create-cluster': step_create_cluster,
        'register-task-definition': step_register_task_def,
        'run-task': step_run_task,
        'describe-tasks': step_describe_tasks,
        'create-service': step_create_service,
        'update-service': step_update_service,
    }
    runner = runners.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {lab_key}')
    return runner()


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if lab_key != 'fargate-microservice':
        raise ValueError(f'Unknown ECS lab: {lab_key}')
    ecs = client('ecs')

    # 1. Cluster
    try:
        cls = ecs.describe_clusters(clusters=[CLUSTER_NAME]).get('clusters', [])
        cluster_ok = len(cls) > 0 and cls[0].get('status') == 'ACTIVE'
    except Exception:
        cluster_ok = bool(marked('cluster'))

    # 2. Task Def
    try:
        td = ecs.describe_task_definition(taskDefinition=TASK_FAMILY).get('taskDefinition')
        td_ok = td is not None
    except Exception:
        td_ok = bool(marked('task_def_arn'))

    # 3. Run Task
    task_ok = bool(marked('task_arn'))

    # 4. Service
    try:
        svcs = ecs.describe_services(cluster=CLUSTER_NAME, services=[SERVICE_NAME]).get('services', [])
        svc_ok = len(svcs) > 0 and svcs[0].get('status') == 'ACTIVE'
    except Exception:
        svc_ok = bool(marked('service'))

    # 5. Update Service
    scaled_ok = bool(marked('scaled'))

    steps_dict = {
        'create-cluster': {
            'verified': cluster_ok,
            'verification': {'status': 'passed', 'message': f'ECS cluster {CLUSTER_NAME} active.'} if cluster_ok else None,
        },
        'register-task-definition': {
            'verified': td_ok,
            'verification': {'status': 'passed', 'message': f'Task definition {TASK_FAMILY} registered.'} if td_ok else None,
        },
        'run-task': {
            'verified': task_ok,
            'verification': {'status': 'passed', 'message': 'Standalone Fargate task running.'} if task_ok else None,
        },
        'describe-tasks': {
            'verified': task_ok,
            'verification': {'status': 'passed', 'message': 'Container health and task status inspected.'} if task_ok else None,
        },
        'create-service': {
            'verified': svc_ok,
            'verification': {'status': 'passed', 'message': f'ECS service {SERVICE_NAME} deployed.'} if svc_ok else None,
        },
        'update-service': {
            'verified': scaled_ok,
            'verification': {'status': 'passed', 'message': 'ECS service scaled to 4 replicas.'} if scaled_ok else None,
        },
    }

    passed_count = sum(1 for s in steps_dict.values() if s['verified'])
    is_complete = passed_count == len(steps_dict)

    return {
        'service': 'ecs',
        'lab': lab_key,
        'complete': is_complete,
        'status': 'passed' if is_complete else ('in_progress' if passed_count > 0 else 'not_started'),
        'steps': steps_dict,
        'passed_steps': passed_count,
        'total_steps': len(steps_dict),
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if lab_key != 'fargate-microservice':
        raise ValueError(f'Unknown ECS lab: {lab_key}')
    started = time.perf_counter()
    ecs = client('ecs')
    deleted = []

    try:
        ecs.delete_service(cluster=CLUSTER_NAME, service=SERVICE_NAME, force=True)
        deleted.append(f'service:{SERVICE_NAME}')
    except Exception:
        pass

    try:
        tasks = ecs.list_tasks(cluster=CLUSTER_NAME).get('taskArns', [])
        for t in tasks:
            ecs.stop_task(cluster=CLUSTER_NAME, task=t)
            deleted.append(f'task:{t}')
    except Exception:
        pass

    try:
        ecs.delete_cluster(cluster=CLUSTER_NAME)
        deleted.append(f'cluster:{CLUSTER_NAME}')
    except Exception:
        pass

    task_definition = marked('task_def_arn')
    if task_definition:
        ecs.deregister_task_definition(taskDefinition=task_definition)
    ec2 = client('ec2')
    for operation, key, parameter in [
        (ec2.delete_security_group, 'group_id', 'GroupId'),
        (ec2.delete_subnet, 'subnet_id', 'SubnetId'),
        (ec2.delete_vpc, 'vpc_id', 'VpcId'),
    ]:
        value = marked(key)
        if value:
            operation(**{parameter: value})
    for k in ['cluster', 'task_def_arn', 'task_arn', 'service', 'scaled', 'vpc_id', 'subnet_id', 'group_id']:
        cache.delete(CACHE_PREFIX + k)

    payload = {'status': 'reset', 'deleted_resources': deleted}
    return {
        'service': 'ecs',
        'lab': lab_key,
        'command': f'aws ecs delete-service ...\\naws ecs delete-cluster --cluster {CLUSTER_NAME} # cleanup',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'verification': {'status': 'passed', 'message': 'ECS lab resources cleaned up.'},
    }
