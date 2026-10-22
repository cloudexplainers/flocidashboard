"""Amazon Elastic Kubernetes Service (EKS) workflow labs."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

REGION = 'us-east-1'
ACCOUNT = '000000000000'
CACHE_PREFIX = 'floci-lab:eks:'

CLUSTER_NAME = 'lab-k8s-cluster'
NODEGROUP_NAME = 'lab-workers'
FARGATE_PROFILE_NAME = 'lab-fargate-profile'
CLUSTER_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/eks-cluster-role'
NODE_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/eks-node-role'
FARGATE_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/eks-fargate-pod-role'

EKS_WORKFLOW_LAB = {
    'service': 'eks',
    'key': 'control-plane-nodegroup',
    'title': 'Provision Kubernetes control planes, node groups, and Fargate profiles with Amazon EKS',
    'description': 'Create an EKS Kubernetes cluster control plane, inspect cluster endpoints and certificate authority, attach managed EC2 worker node groups, configure serverless Fargate pod execution profiles, and generate local kubeconfig access.',
    'steps': [
        {
            'key': 'create-cluster',
            'title': 'Provision EKS Kubernetes Control Plane',
            'command': f'aws eks create-cluster --name {CLUSTER_NAME} --role-arn {CLUSTER_ROLE_ARN} --resources-vpc-config subnetIds=<subnets>',
            'explanation': 'Launches an Amazon EKS managed Kubernetes control plane with automated etcd and API server configuration.',
        },
        {
            'key': 'describe-cluster',
            'title': 'Inspect Cluster Endpoint, Version, and CA',
            'command': f'aws eks describe-cluster --name {CLUSTER_NAME}',
            'explanation': 'Retrieves the secure TLS Kubernetes API server endpoint, platform version, and certificate authority data.',
        },
        {
            'key': 'create-nodegroup',
            'title': 'Attach Managed EC2 Worker Node Group',
            'command': f'aws eks create-nodegroup --cluster-name {CLUSTER_NAME} --nodegroup-name {NODEGROUP_NAME} --node-role {NODE_ROLE_ARN} --subnets <subnets> --scaling-config minSize=1,maxSize=4,desiredSize=2',
            'explanation': 'Provisions auto-scaling worker nodes running container runtime, kubelet, and kube-proxy.',
        },
        {
            'key': 'create-fargate-profile',
            'title': 'Attach Serverless Fargate Pod Profile',
            'command': f'aws eks create-fargate-profile --cluster-name {CLUSTER_NAME} --fargate-profile-name {FARGATE_PROFILE_NAME} --pod-execution-role-arn {FARGATE_ROLE_ARN} --subnets <subnets> --selectors namespace=default',
            'explanation': 'Enables serverless execution of pods in the default namespace without requiring EC2 instances.',
        },
        {
            'key': 'generate-kubeconfig',
            'title': 'Generate Local Kubeconfig & kubectl Access',
            'command': f'aws eks update-kubeconfig --name {CLUSTER_NAME} --region {REGION}',
            'explanation': 'Generates client configuration context in ~/.kube/config to interact with the local EKS cluster using kubectl.',
        },
    ],
}

LABS = [EKS_WORKFLOW_LAB]


def client(name: str):
    return FlociClientFactory().client(name)


def marker(key: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + key, _clean_response(value), timeout=86400)


def marked(key: str) -> Any:
    return cache.get(CACHE_PREFIX + key)


def _get_or_create_subnets() -> list[str]:
    ec2 = client('ec2')
    subnets = ec2.describe_subnets().get('Subnets', [])
    if len(subnets) >= 2:
        return [subnets[0]['SubnetId'], subnets[1]['SubnetId']]
    vpcs = ec2.describe_vpcs().get('Vpcs', [])
    vpc_id = vpcs[0]['VpcId'] if vpcs else ec2.create_vpc(CidrBlock='10.0.0.0/16')['Vpc']['VpcId']
    s1 = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.0.200.0/24', AvailabilityZone='us-east-1a')['Subnet']['SubnetId']
    s2 = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.0.201.0/24', AvailabilityZone='us-east-1b')['Subnet']['SubnetId']
    return [s1, s2]


def result(lab: str, step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': 'eks',
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
    eks = client('eks')
    subnets = _get_or_create_subnets()
    try:
        resp = eks.create_cluster(
            name=CLUSTER_NAME,
            roleArn=CLUSTER_ROLE_ARN,
            resourcesVpcConfig={'subnetIds': subnets},
        )
    except ClientError as exc:
        if 'ResourceInUseException' in str(exc):
            resp = eks.describe_cluster(name=CLUSTER_NAME)
        else:
            raise
    marker('cluster', {'name': CLUSTER_NAME, 'subnets': subnets})
    return result(
        'control-plane-nodegroup',
        'create-cluster',
        f'aws eks create-cluster --name {CLUSTER_NAME} --role-arn {CLUSTER_ROLE_ARN}',
        resp,
        True,
        f'Provisioned EKS Kubernetes cluster {CLUSTER_NAME}.',
        started,
    )


def step_describe_cluster() -> dict[str, Any]:
    started = time.perf_counter()
    eks = client('eks')
    resp = eks.describe_cluster(name=CLUSTER_NAME)
    cluster = resp.get('cluster', {})
    verified = bool(cluster.get('name') == CLUSTER_NAME)
    return result(
        'control-plane-nodegroup',
        'describe-cluster',
        f'aws eks describe-cluster --name {CLUSTER_NAME}',
        resp,
        verified,
        f'Inspected EKS cluster {CLUSTER_NAME} endpoint: {cluster.get("endpoint")}.' if verified else 'Cluster not found',
        started,
    )


def step_create_nodegroup() -> dict[str, Any]:
    started = time.perf_counter()
    eks = client('eks')
    subnets = _get_or_create_subnets()
    try:
        resp = eks.create_nodegroup(
            clusterName=CLUSTER_NAME,
            nodegroupName=NODEGROUP_NAME,
            nodeRole=NODE_ROLE_ARN,
            subnets=subnets,
            scalingConfig={'minSize': 1, 'maxSize': 4, 'desiredSize': 2},
        )
    except ClientError as exc:
        if 'ResourceInUseException' in str(exc):
            resp = eks.describe_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=NODEGROUP_NAME)
        else:
            raise
    marker('nodegroup', {'name': NODEGROUP_NAME})
    return result(
        'control-plane-nodegroup',
        'create-nodegroup',
        f'aws eks create-nodegroup --cluster-name {CLUSTER_NAME} --nodegroup-name {NODEGROUP_NAME} ...',
        resp,
        True,
        f'Attached managed node group {NODEGROUP_NAME} to cluster {CLUSTER_NAME}.',
        started,
    )


def step_create_fargate_profile() -> dict[str, Any]:
    started = time.perf_counter()
    eks = client('eks')
    subnets = _get_or_create_subnets()
    try:
        resp = eks.create_fargate_profile(
            clusterName=CLUSTER_NAME,
            fargateProfileName=FARGATE_PROFILE_NAME,
            podExecutionRoleArn=FARGATE_ROLE_ARN,
            subnets=[subnets[0]],
            selectors=[{'namespace': 'default'}],
        )
    except ClientError as exc:
        if 'ResourceInUseException' in str(exc):
            resp = eks.describe_fargate_profile(clusterName=CLUSTER_NAME, fargateProfileName=FARGATE_PROFILE_NAME)
        else:
            raise
    marker('fargate', {'name': FARGATE_PROFILE_NAME})
    return result(
        'control-plane-nodegroup',
        'create-fargate-profile',
        f'aws eks create-fargate-profile --cluster-name {CLUSTER_NAME} --fargate-profile-name {FARGATE_PROFILE_NAME} ...',
        resp,
        True,
        f'Attached Fargate profile {FARGATE_PROFILE_NAME} to cluster {CLUSTER_NAME}.',
        started,
    )


def step_generate_kubeconfig() -> dict[str, Any]:
    started = time.perf_counter()
    from dashboard.eks_api import get_kubeconfig
    resp = get_kubeconfig(CLUSTER_NAME, region=REGION)
    marker('kubeconfig', True)
    return result(
        'control-plane-nodegroup',
        'generate-kubeconfig',
        f'aws eks update-kubeconfig --name {CLUSTER_NAME} --region {REGION}',
        resp,
        True,
        f'Generated kubeconfig context for {CLUSTER_NAME}.',
        started,
    )


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if lab_key != 'control-plane-nodegroup':
        raise ValueError(f'Unknown EKS lab: {lab_key}')
    runners = {
        'create-cluster': step_create_cluster,
        'describe-cluster': step_describe_cluster,
        'create-nodegroup': step_create_nodegroup,
        'create-fargate-profile': step_create_fargate_profile,
        'generate-kubeconfig': step_generate_kubeconfig,
    }
    runner = runners.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {lab_key}')
    return runner()


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if lab_key != 'control-plane-nodegroup':
        raise ValueError(f'Unknown EKS lab: {lab_key}')
    eks = client('eks')

    # 1. Cluster
    try:
        c = eks.describe_cluster(name=CLUSTER_NAME).get('cluster')
        cluster_ok = c is not None
    except Exception:
        cluster_ok = bool(marked('cluster'))

    # 2. Node Group
    try:
        ng = eks.describe_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=NODEGROUP_NAME).get('nodegroup')
        ng_ok = ng is not None
    except Exception:
        ng_ok = bool(marked('nodegroup'))

    # 3. Fargate Profile
    try:
        fp = eks.describe_fargate_profile(clusterName=CLUSTER_NAME, fargateProfileName=FARGATE_PROFILE_NAME).get('fargateProfile')
        fp_ok = fp is not None
    except Exception:
        fp_ok = bool(marked('fargate'))

    # 4. Kubeconfig
    kube_ok = bool(marked('kubeconfig'))

    steps_dict = {
        'create-cluster': {
            'verified': cluster_ok,
            'verification': {'status': 'passed', 'message': f'EKS control plane {CLUSTER_NAME} active.'} if cluster_ok else None,
        },
        'describe-cluster': {
            'verified': cluster_ok,
            'verification': {'status': 'passed', 'message': 'EKS endpoint and CA inspected.'} if cluster_ok else None,
        },
        'create-nodegroup': {
            'verified': ng_ok,
            'verification': {'status': 'passed', 'message': f'Managed Node Group {NODEGROUP_NAME} attached.'} if ng_ok else None,
        },
        'create-fargate-profile': {
            'verified': fp_ok,
            'verification': {'status': 'passed', 'message': f'Fargate Profile {FARGATE_PROFILE_NAME} active.'} if fp_ok else None,
        },
        'generate-kubeconfig': {
            'verified': kube_ok,
            'verification': {'status': 'passed', 'message': f'Local kubeconfig generated for {CLUSTER_NAME}.'} if kube_ok else None,
        },
    }

    passed_count = sum(1 for s in steps_dict.values() if s['verified'])
    is_complete = passed_count == len(steps_dict)

    return {
        'service': 'eks',
        'lab': lab_key,
        'complete': is_complete,
        'status': 'passed' if is_complete else ('in_progress' if passed_count > 0 else 'not_started'),
        'steps': steps_dict,
        'passed_steps': passed_count,
        'total_steps': len(steps_dict),
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if lab_key != 'control-plane-nodegroup':
        raise ValueError(f'Unknown EKS lab: {lab_key}')
    started = time.perf_counter()
    eks = client('eks')
    deleted = []

    try:
        eks.delete_fargate_profile(clusterName=CLUSTER_NAME, fargateProfileName=FARGATE_PROFILE_NAME)
        deleted.append(f'fargate-profile:{FARGATE_PROFILE_NAME}')
    except Exception:
        pass

    try:
        eks.delete_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=NODEGROUP_NAME)
        deleted.append(f'nodegroup:{NODEGROUP_NAME}')
    except Exception:
        pass

    try:
        eks.delete_cluster(name=CLUSTER_NAME)
        deleted.append(f'cluster:{CLUSTER_NAME}')
    except Exception:
        pass

    for k in ['cluster', 'nodegroup', 'fargate', 'kubeconfig']:
        cache.delete(CACHE_PREFIX + k)

    payload = {'status': 'reset', 'deleted_resources': deleted}
    return {
        'service': 'eks',
        'lab': lab_key,
        'command': f'aws eks delete-cluster --name {CLUSTER_NAME} # cleanup',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'verification': {'status': 'passed', 'message': 'EKS lab resources cleaned up.'},
    }
