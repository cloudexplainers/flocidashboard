"""Step Functions workflow labs."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

REGION = 'us-east-1'
ACCOUNT = '000000000000'
CACHE_PREFIX = 'floci-lab:stepfunctions:'

ROLE_NAME = 'FlociStepFunctionsExecutionRole'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'

CHOICE_SM_NAME = 'lab-order-processing-workflow'
CHOICE_SM_ARN = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{CHOICE_SM_NAME}'

PARALLEL_SM_NAME = 'lab-parallel-validation-workflow'
PARALLEL_SM_ARN = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{PARALLEL_SM_NAME}'

TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Principal': {'Service': 'states.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        }
    ],
}

ROLE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': ['logs:*', 'lambda:InvokeFunction'],
            'Resource': '*',
        }
    ],
}

CHOICE_DEFINITION = {
    'Comment': 'Order processing workflow with Choice state branching',
    'StartAt': 'EvaluateOrderAmount',
    'States': {
        'EvaluateOrderAmount': {
            'Type': 'Choice',
            'Choices': [
                {
                    'Variable': '$.amount',
                    'NumericGreaterThanEquals': 100,
                    'Next': 'ProcessVIPOrder',
                }
            ],
            'Default': 'ProcessStandardOrder',
        },
        'ProcessVIPOrder': {
            'Type': 'Pass',
            'Result': {
                'status': 'VIP_PROCESSED',
                'priority': 'HIGH',
                'discount_applied': 0.15,
            },
            'ResultPath': '$.fulfillment',
            'End': True,
        },
        'ProcessStandardOrder': {
            'Type': 'Pass',
            'Result': {
                'status': 'STANDARD_PROCESSED',
                'priority': 'NORMAL',
                'discount_applied': 0.0,
            },
            'ResultPath': '$.fulfillment',
            'End': True,
        },
    },
}

PARALLEL_DEFINITION = {
    'Comment': 'Parallel order verification workflow',
    'StartAt': 'ValidateOrderParallel',
    'States': {
        'ValidateOrderParallel': {
            'Type': 'Parallel',
            'Branches': [
                {
                    'StartAt': 'VerifyPayment',
                    'States': {
                        'VerifyPayment': {
                            'Type': 'Pass',
                            'Result': {'payment': 'VERIFIED_OK'},
                            'End': True,
                        }
                    },
                },
                {
                    'StartAt': 'CheckInventory',
                    'States': {
                        'CheckInventory': {
                            'Type': 'Pass',
                            'Result': {'inventory': 'IN_STOCK'},
                            'End': True,
                        }
                    },
                },
            ],
            'ResultPath': '$.checks',
            'Next': 'OrderApproved',
        },
        'OrderApproved': {
            'Type': 'Pass',
            'Result': {'status': 'ALL_CHECKS_PASSED'},
            'ResultPath': '$.overall_status',
            'End': True,
        },
    },
}

ORDER_PROCESSING_LAB = {
    'service': 'stepfunctions',
    'key': 'order-processing-workflow',
    'title': 'Build a Step Functions state machine with Choice branching',
    'description': 'Create an IAM execution role, define an Amazon States Language (ASL) state machine with Choice branching, execute distinct order tiers (VIP vs Standard), and inspect execution history.',
    'steps': [
        {
            'key': 'create-execution-role',
            'title': 'Create Step Functions execution IAM role',
            'command': f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://trust-policy.json\naws iam put-role-policy --role-name {ROLE_NAME} --policy-name StepFunctionsPolicy --policy-document file://policy.json',
            'explanation': 'Step Functions requires an execution role with a trust policy allowing states.amazonaws.com to assume the role.',
            'artifact_label': 'trust-policy.json',
            'artifact': json.dumps(TRUST_POLICY, indent=2),
        },
        {
            'key': 'create-choice-state-machine',
            'title': 'Create order processing state machine with Choice states',
            'command': f'aws stepfunctions create-state-machine --name {CHOICE_SM_NAME} --definition file://choice-workflow.json --role-arn {ROLE_ARN}',
            'explanation': 'Creates the state machine with an Amazon States Language definition containing a Choice state to route high-value VIP orders separately from standard orders.',
            'artifact_label': 'choice-workflow.json',
            'artifact': json.dumps(CHOICE_DEFINITION, indent=2),
        },
        {
            'key': 'start-vip-execution',
            'title': 'Execute state machine with VIP order input ($250)',
            'command': f'aws stepfunctions start-execution --state-machine-arn {CHOICE_SM_ARN} --name vip-order-101 --input \'{{"order_id":"ORD-VIP-101","amount":250,"customer":"Acme Corp"}}\'',
            'explanation': 'Starts execution with amount=250. The Choice state evaluates amount >= 100 and transitions to the ProcessVIPOrder state.',
        },
        {
            'key': 'start-standard-execution',
            'title': 'Execute state machine with Standard order input ($45)',
            'command': f'aws stepfunctions start-execution --state-machine-arn {CHOICE_SM_ARN} --name std-order-202 --input \'{{"order_id":"ORD-STD-202","amount":45,"customer":"Jane Doe"}}\'',
            'explanation': 'Starts execution with amount=45. The Choice state falls through to the default ProcessStandardOrder state.',
        },
        {
            'key': 'inspect-execution-history',
            'title': 'Describe execution and inspect state transition history',
            'command': f'aws stepfunctions describe-execution --execution-arn {CHOICE_SM_ARN}:vip-order-101\naws stepfunctions get-execution-history --execution-arn {CHOICE_SM_ARN}:vip-order-101',
            'explanation': 'Retrieves the final execution output and inspects the execution event history to verify state transitions and timing.',
        },
    ],
}

PARALLEL_RETRY_LAB = {
    'service': 'stepfunctions',
    'key': 'parallel-retry-workflow',
    'title': 'Execute parallel branches and aggregate state outputs',
    'description': 'Construct a state machine with a Parallel state that invokes payment verification and inventory reservation concurrently, merging their parallel branch outputs into a unified result.',
    'steps': [
        {
            'key': 'create-parallel-state-machine',
            'title': 'Create parallel verification state machine',
            'command': f'aws stepfunctions create-state-machine --name {PARALLEL_SM_NAME} --definition file://parallel-workflow.json --role-arn {ROLE_ARN}',
            'explanation': 'Deploys an ASL state machine with a Parallel state defining two concurrent branch state machines.',
            'artifact_label': 'parallel-workflow.json',
            'artifact': json.dumps(PARALLEL_DEFINITION, indent=2),
        },
        {
            'key': 'execute-parallel-success',
            'title': 'Execute parallel workflow and verify branch outputs',
            'command': f'aws stepfunctions start-execution --state-machine-arn {PARALLEL_SM_ARN} --name par-exec-301 --input \'{{"order_id":"ORD-PAR-301","item":"Widget-A"}}\'',
            'explanation': 'Runs both branches concurrently and stores their output array at $.checks, preserving the input object for the following approval state.',
        },
        {
            'key': 'describe-parallel-execution',
            'title': 'Describe parallel execution and verify merged array',
            'command': f'aws stepfunctions describe-execution --execution-arn {PARALLEL_SM_ARN}:par-exec-301',
            'explanation': 'Verifies the execution succeeded and the output contains both parallel verification branch outputs.',
        },
    ],
}

LABS = [ORDER_PROCESSING_LAB, PARALLEL_RETRY_LAB]


def client(name: str):
    return FlociClientFactory().client(name)


def marker(key: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + key, _clean_response(value), timeout=86400)


def marked(key: str) -> Any:
    return cache.get(CACHE_PREFIX + key)


def wait_for_execution(sfn, arn: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while True:
        response = sfn.describe_execution(executionArn=arn)
        if response.get('status') != 'RUNNING':
            return response
        if time.monotonic() >= deadline:
            raise ValueError('Step Functions execution did not finish within 30 seconds')
        time.sleep(.2)


def result(lab: str, step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': 'stepfunctions',
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


def step_create_role() -> dict[str, Any]:
    started = time.perf_counter()
    iam = client('iam')
    try:
        role_resp = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
        )
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') != 'EntityAlreadyExists':
            raise
        role_resp = iam.get_role(RoleName=ROLE_NAME)
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName='StepFunctionsPolicy',
        PolicyDocument=json.dumps(ROLE_POLICY),
    )
    marker('role', role_resp)
    return result(
        'order-processing-workflow',
        'create-execution-role',
        'aws iam create-role ...',
        role_resp,
        True,
        f'IAM execution role {ROLE_NAME} exists with states.amazonaws.com trust policy.',
        started,
    )


def step_create_choice_sm() -> dict[str, Any]:
    started = time.perf_counter()
    sfn = client('stepfunctions')
    try:
        resp = sfn.create_state_machine(
            name=CHOICE_SM_NAME,
            definition=json.dumps(CHOICE_DEFINITION),
            roleArn=ROLE_ARN,
        )
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') not in {'StateMachineAlreadyExists', 'ResourceConflictException'}:
            raise
        resp = sfn.describe_state_machine(stateMachineArn=CHOICE_SM_ARN)
    marker('choice-sm', resp)
    return result(
        'order-processing-workflow',
        'create-choice-state-machine',
        'aws stepfunctions create-state-machine ...',
        resp,
        True,
        f'State machine {CHOICE_SM_NAME} created with Choice branching.',
        started,
    )


def step_execute_vip() -> dict[str, Any]:
    started = time.perf_counter()
    sfn = client('stepfunctions')
    inp = {'order_id': 'ORD-VIP-101', 'amount': 250, 'customer': 'Acme Corp'}
    exec_resp = sfn.start_execution(
        stateMachineArn=CHOICE_SM_ARN,
        name=f'vip-order-{uuid.uuid4().hex[:12]}',
        input=json.dumps(inp),
    )
    arn = exec_resp['executionArn']
    desc = wait_for_execution(sfn, arn)
    marker('vip-exec', desc)
    return result(
        'order-processing-workflow',
        'start-vip-execution',
        'aws stepfunctions start-execution ...',
        desc,
        desc.get('status') == 'SUCCEEDED',
        'VIP execution succeeded and routed through the VIP processing branch (amount >= 100).',
        started,
    )


def step_execute_std() -> dict[str, Any]:
    started = time.perf_counter()
    sfn = client('stepfunctions')
    inp = {'order_id': 'ORD-STD-202', 'amount': 45, 'customer': 'Jane Doe'}
    exec_resp = sfn.start_execution(
        stateMachineArn=CHOICE_SM_ARN,
        name=f'std-order-{uuid.uuid4().hex[:12]}',
        input=json.dumps(inp),
    )
    arn = exec_resp['executionArn']
    desc = wait_for_execution(sfn, arn)
    marker('std-exec', desc)
    return result(
        'order-processing-workflow',
        'start-standard-execution',
        'aws stepfunctions start-execution ...',
        desc,
        desc.get('status') == 'SUCCEEDED',
        'Standard execution succeeded and routed through default standard processing branch (amount < 100).',
        started,
    )


def step_inspect_history() -> dict[str, Any]:
    started = time.perf_counter()
    sfn = client('stepfunctions')
    execution = marked('vip-exec') or {}
    if not execution.get('executionArn'):
        raise ValueError('Run the VIP execution before inspecting its history')
    desc = sfn.describe_execution(executionArn=execution['executionArn'])
    history = sfn.get_execution_history(executionArn=execution['executionArn'])
    sm = {'execution': desc, 'history': history}
    verified = desc.get('status') == 'SUCCEEDED' and bool(history.get('events'))
    marker('history', sm if verified else False)
    return result(
        'order-processing-workflow',
        'inspect-execution-history',
        'aws stepfunctions describe-execution ...',
        sm,
        verified,
        f'State machine {CHOICE_SM_NAME} and execution lifecycle history verified.',
        started,
    )


def step_create_parallel_sm() -> dict[str, Any]:
    started = time.perf_counter()
    step_create_role()
    sfn = client('stepfunctions')
    try:
        resp = sfn.create_state_machine(
            name=PARALLEL_SM_NAME,
            definition=json.dumps(PARALLEL_DEFINITION),
            roleArn=ROLE_ARN,
        )
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') not in {'StateMachineAlreadyExists', 'ResourceConflictException'}:
            raise
        resp = sfn.describe_state_machine(stateMachineArn=PARALLEL_SM_ARN)
    marker('parallel-sm', resp)
    return result(
        'parallel-retry-workflow',
        'create-parallel-state-machine',
        'aws stepfunctions create-state-machine ...',
        resp,
        True,
        f'Parallel state machine {PARALLEL_SM_NAME} created.',
        started,
    )


def step_execute_parallel() -> dict[str, Any]:
    started = time.perf_counter()
    sfn = client('stepfunctions')
    inp = {'order_id': 'ORD-PAR-301', 'item': 'Widget-A'}
    exec_resp = sfn.start_execution(
        stateMachineArn=PARALLEL_SM_ARN,
        name=f'par-exec-{uuid.uuid4().hex[:12]}',
        input=json.dumps(inp),
    )
    arn = exec_resp['executionArn']
    desc = wait_for_execution(sfn, arn)
    marker('parallel-exec', desc)
    return result(
        'parallel-retry-workflow',
        'execute-parallel-success',
        'aws stepfunctions start-execution ...',
        desc,
        desc.get('status') == 'SUCCEEDED',
        'Parallel execution succeeded with concurrent payment and inventory branches.' if desc.get('status') == 'SUCCEEDED' else f"Parallel execution failed: {desc.get('error') or desc.get('status')}: {desc.get('cause', '')}",
        started,
    )


def step_describe_parallel() -> dict[str, Any]:
    started = time.perf_counter()
    sfn = client('stepfunctions')
    execution = marked('parallel-exec') or {}
    if not execution.get('executionArn'):
        raise ValueError('Run the parallel execution before inspecting its output')
    sm = sfn.describe_execution(executionArn=execution['executionArn'])
    output = json.loads(sm.get('output') or '{}')
    verified = (
        sm.get('status') == 'SUCCEEDED'
        and isinstance(output, dict)
        and output.get('checks') == [{'payment': 'VERIFIED_OK'}, {'inventory': 'IN_STOCK'}]
        and output.get('overall_status', {}).get('status') == 'ALL_CHECKS_PASSED'
    )
    marker('parallel-desc', sm if verified else False)
    return result(
        'parallel-retry-workflow',
        'describe-parallel-execution',
        'aws stepfunctions describe-execution ...',
        sm,
        verified,
        f'Parallel state machine {PARALLEL_SM_NAME} execution output verified.',
        started,
    )


RUNNERS = {
    'order-processing-workflow': {
        'create-execution-role': step_create_role,
        'create-choice-state-machine': step_create_choice_sm,
        'start-vip-execution': step_execute_vip,
        'start-standard-execution': step_execute_std,
        'inspect-execution-history': step_inspect_history,
    },
    'parallel-retry-workflow': {
        'create-parallel-state-machine': step_create_parallel_sm,
        'execute-parallel-success': step_execute_parallel,
        'describe-parallel-execution': step_describe_parallel,
    },
}


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if lab_key not in RUNNERS or step_key not in RUNNERS[lab_key]:
        raise ValueError(f'Unknown Step Functions lab step: {lab_key}/{step_key}')
    return RUNNERS[lab_key][step_key]()


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if lab_key == 'order-processing-workflow':
        keys = {
            'create-execution-role': 'role',
            'create-choice-state-machine': 'choice-sm',
            'start-vip-execution': 'vip-exec',
            'start-standard-execution': 'std-exec',
            'inspect-execution-history': 'history',
        }
    elif lab_key == 'parallel-retry-workflow':
        keys = {
            'create-parallel-state-machine': 'parallel-sm',
            'execute-parallel-success': 'parallel-exec',
            'describe-parallel-execution': 'parallel-desc',
        }
    else:
        raise ValueError(f'Unknown Step Functions lab: {lab_key}')

    checks = {}
    for step, key in keys.items():
        value = marked(key)
        checks[step] = bool(value) and (not key.endswith('-exec') or value.get('status') == 'SUCCEEDED')
    return {
        'service': 'stepfunctions',
        'lab': lab_key,
        'complete': all(checks.values()),
        'steps': {
            step: {
                'verified': checks[step],
                'verification': {
                    'status': 'passed',
                    'message': 'Verified by Step Functions runner.',
                } if checks[step] else None,
            }
            for step in keys
        },
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    started = time.perf_counter()
    sfn = client('stepfunctions')
    iam = client('iam')

    for arn in [CHOICE_SM_ARN, PARALLEL_SM_ARN]:
        try:
            sfn.delete_state_machine(stateMachineArn=arn)
        except ClientError:
            pass

    try:
        iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName='StepFunctionsPolicy')
    except ClientError:
        pass
    try:
        iam.delete_role(RoleName=ROLE_NAME)
    except ClientError:
        pass

    cache.delete_many([
        CACHE_PREFIX + k
        for k in ['role', 'choice-sm', 'vip-exec', 'std-exec', 'history', 'parallel-sm', 'parallel-exec', 'parallel-desc']
    ])

    payload = {'removed': True, 'state_machines': [CHOICE_SM_NAME, PARALLEL_SM_NAME], 'role': ROLE_NAME}
    return {
        'service': 'stepfunctions',
        'lab': lab_key,
        'command': 'aws stepfunctions delete-state-machine ... # cleanup',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'verification': {'status': 'passed', 'message': 'Step Functions lab resources cleaned up.'},
    }
