import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from hashlib import sha256
from typing import Optional

from botocore.exceptions import BotoCoreError, ClientError
from botocore.parsers import ResponseParserError
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_POST
from .aws import FlociClientFactory, collect_load_errors, acm_inventory, amazonmq_inventory, apigateway_inventory, appconfig_inventory, appsync_inventory, athena_inventory, autoscaling_inventory, backup_inventory, batch_inventory, bcmdataexports_inventory, bedrock_inventory, bedrockruntime_inventory, cloudcontrol_inventory, comprehend_inventory, rekognition_inventory, sagemaker_inventory, translate_inventory, cloudformation_inventory, cloudfront_inventory, cloudmap_inventory, cloudtrail_inventory, cloudwatch_inventory, codebuild_inventory, codepipeline_inventory, codedeploy_inventory, config_inventory, cognito_inventory, costexplorer_inventory, cur_inventory, docdb_inventory, dynamodb_inventory, ec2_inventory, ecr_inventory, ecs_inventory, eks_inventory, elasticache_inventory, elasticbeanstalk_inventory, elasticloadbalancing_inventory, emr_inventory, eventbridge_inventory, firehose_inventory, glue_inventory, iam_inventory, iot_inventory, kafka_inventory, kinesis_inventory, kms_inventory, lambda_inventory, list_resources, memorydb_inventory, neptune_inventory, opensearch_inventory, pipes_inventory, pricing_inventory, rds_inventory, rdsdata_inventory, resourcegroupstagging_inventory, route53_inventory, s3_inventory, s3vectors_inventory, scheduler_inventory, secretsmanager_inventory, ses_inventory, sns_inventory, sqs_inventory, ssm_inventory, stepfunctions_inventory, textract_inventory, transcribe_inventory, transfer_inventory, wafv2_inventory
from .labs import get_lab, lab_status, next_lab_batch, reset_lab, run_lab_step
from .labs.exams import exam_tracks, tags_for_lab
from .labs.registry import all_labs
from .labs.snippets import get_step_snippets
from .services import get_service, services_payload


SERVICE_ALIASES = {
    'cognito-idp': 'cognito',
    'cloudcontrolapi': 'cloudcontrol',
    'events': 'eventbridge',
    'logs': 'cloudwatch',
    'monitoring': 'cloudwatch',
    'servicediscovery': 'cloudmap',
    'states': 'stepfunctions',
}

LABS_PROGRESS_CACHE_KEY = 'dashboard:labs-progress'
LABS_PROGRESS_CACHE_SECONDS = 15
LABS_PROGRESS_WORKERS = 8


# Codes Floci returns for an operation it doesn't implement.
UNSUPPORTED_OPERATION_CODES = {'InvalidAction', 'UnknownOperationException', 'UnsupportedOperation'}


def _inventory_response(loader) -> JsonResponse:
    """Serve an inventory with the lists that failed to load.

    Inventories fill optional lists through _safe_value, which falls back to an
    empty list. load_errors names each list call that failed, so the console can
    say "Floci did not return X" instead of showing it as empty.
    """
    payload, errors = collect_load_errors(loader)
    if errors and isinstance(payload, dict):
        payload = {
            **payload,
            'load_errors': [
                {**error, 'unsupported': error.get('code') in UNSUPPORTED_OPERATION_CODES}
                for error in errors
            ],
        }
    return JsonResponse(payload)


def selected_service_keys(request) -> Optional[set[str]]:
    raw_values = request.GET.getlist('services')
    if not raw_values:
        return None

    keys: set[str] = set()
    for raw_value in raw_values:
        for item in raw_value.split(','):
            key = SERVICE_ALIASES.get(item.strip(), item.strip())
            if get_service(key):
                keys.add(key)
    return keys

def _next_batch_context(service_key, lab_key, complete):
    if not complete:
        return None
    batch = next_lab_batch(service_key, lab_key)
    if not batch:
        return None
    if batch.get('service') and batch.get('lab'):
        batch = {
            **batch,
            'href': f'{reverse("dashboard:labs")}?service={batch["service"]}&lab={batch["lab"]}',
        }
    return batch


def _lab_progress(lab):
    steps = lab.get('steps', [])
    try:
        status = lab_status(lab['service'], lab['key'])
    except (BotoCoreError, ClientError, ValueError) as exc:
        return {
            'completed_steps': 0,
            'total_steps': len(steps),
            'complete': False,
            'error': str(exc),
        }
    step_statuses = status.get('steps', {})
    completed_steps = sum(
        1
        for step in steps
        if step_statuses.get(step.get('key'), {}).get('verified')
    )
    return {
        'completed_steps': completed_steps,
        'total_steps': len(steps),
        'complete': bool(status.get('complete')),
        'error': None,
    }


def _labs_progress_snapshot():
    total_labs = 0
    total_steps = 0
    completed_labs = 0
    completed_steps = 0
    progress_errors = []
    lab_progress = []

    labs = list(all_labs())
    # Each lab check is a handful of network calls to Floci, so check labs in
    # parallel. Copy the request context so endpoint and identity overrides
    # (ContextVars) apply inside the worker threads too.
    with ThreadPoolExecutor(max_workers=LABS_PROGRESS_WORKERS) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, _lab_progress, lab)
            for lab in labs
        ]
        results = [future.result() for future in futures]

    for lab, progress in zip(labs, results):
        total_labs += 1
        total_steps += progress['total_steps']
        completed_labs += 1 if progress['complete'] else 0
        completed_steps += progress['completed_steps']
        if progress['error']:
            progress_errors.append({
                'service': lab['service'],
                'lab': lab['key'],
                'message': progress['error'],
            })
        lab_progress.append({
            'service': lab['service'],
            'lab': lab['key'],
            'key': lab['key'],
            'title': lab.get('title'),
            'completed_steps': progress['completed_steps'],
            'total_steps': progress['total_steps'],
            'complete': progress['complete'],
            'error': progress['error'],
        })

    return {
        'completed_lab_count': completed_labs,
        'total_lab_count': total_labs,
        'completed_step_count': completed_steps,
        'total_step_count': total_steps,
        'progress_error_count': len(progress_errors),
        'progress_errors': progress_errors,
        'labs': lab_progress,
    }


def _labs_progress_cache_key() -> str:
    factory = FlociClientFactory()
    context = '|'.join([
        factory.endpoint_url,
        factory.region,
        factory.credential_source,
        factory.profile or '',
        factory.access_key_id or '',
    ])
    digest = sha256(context.encode('utf-8')).hexdigest()
    return f'{LABS_PROGRESS_CACHE_KEY}:{digest}'

def labs_progress(request):
    cache_key = _labs_progress_cache_key()
    snapshot = cache.get(cache_key)
    cached = snapshot is not None
    if snapshot is None:
        snapshot = _labs_progress_snapshot()
        cache.set(cache_key, snapshot, LABS_PROGRESS_CACHE_SECONDS)
    return JsonResponse({**snapshot, 'cached': cached})


def labs_catalog(request):
    """Instant catalog of all registered labs without slow botocore polling."""
    labs = all_labs()
    service_hint = request.GET.get('service')
    if service_hint:
        labs = [lab for lab in labs if lab.get('service') == service_hint]

    services_map: dict[str, list[dict]] = {}
    for lab in labs:
        svc = lab.get('service', '')
        services_map.setdefault(svc, []).append({
            'key': lab.get('key'),
            'service': svc,
            'title': lab.get('title'),
            'description': lab.get('description'),
            'step_count': len(lab.get('steps', [])),
            'exams': tags_for_lab(svc, lab.get('key')),
            'steps': [
                {
                    'key': s.get('key'),
                    'title': s.get('title'),
                    'command': s.get('command'),
                    'explanation': s.get('explanation'),
                    'artifact': s.get('artifact'),
                    'artifact_label': s.get('artifact_label'),
                    'snippets': get_step_snippets(s, svc),
                }
                for s in lab.get('steps', [])
            ],
        })

    return JsonResponse({
        'total_labs': len(labs),
        'services': [
            {
                'service_key': svc,
                'service_title': (get_service(svc).title if get_service(svc) else svc.upper()),
                'lab_count': len(svc_labs),
                'labs': svc_labs,
            }
            for svc, svc_labs in services_map.items()
        ],
        'labs': [
            {
                'key': lab.get('key'),
                'service': lab.get('service'),
                'title': lab.get('title'),
                'description': lab.get('description'),
                'step_count': len(lab.get('steps', [])),
                'exams': tags_for_lab(lab.get('service'), lab.get('key')),
                'steps': [
                    {
                        'key': s.get('key'),
                        'title': s.get('title'),
                        'command': s.get('command'),
                        'explanation': s.get('explanation'),
                        'artifact': s.get('artifact'),
                        'artifact_label': s.get('artifact_label'),
                        'snippets': get_step_snippets(s, lab.get('service')),
                    }
                    for s in lab.get('steps', [])
                ],
            }
            for lab in labs
        ],
    })

def labs_exams(request):
    """Certification exam guides with the labs and concept cards for each task statement."""
    return JsonResponse(exam_tracks(all_labs()))


@require_POST
def lab_step_run(request, service_key: str, lab_key: str, step_key: str):
    if not get_lab(service_key, lab_key):
        raise Http404('Lab not found')

    try:
        result = run_lab_step(service_key, lab_key, step_key)
        cache.delete(_labs_progress_cache_key())
        try:
            status = lab_status(service_key, lab_key)
            result['lab_complete'] = status.get('complete')
            result['next_batch'] = _next_batch_context(
                service_key,
                lab_key,
                status.get('complete'),
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            result['lab_complete'] = False
            result['next_batch'] = None
            result['status_warning'] = str(exc)
        return JsonResponse(result)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)


def api_lab_status(request, service_key: str, lab_key: str):
    """Return live completion status of all steps in a specific lab."""
    if not get_lab(service_key, lab_key):
        raise Http404('Lab not found')
    try:
        status = lab_status(service_key, lab_key)
        return JsonResponse(status)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'complete': False, 'steps': {}, 'error': str(exc)}, status=200)


@require_POST
def lab_reset(request, service_key: str, lab_key: str):
    if not get_lab(service_key, lab_key):
        raise Http404('Lab not found')

    try:
        result = reset_lab(service_key, lab_key)
        cache.delete(_labs_progress_cache_key())
        return JsonResponse(result)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)


@require_POST
def labs_global_reset(request):
    is_reset_all = request.path.rstrip('/').endswith('reset-all') or request.GET.get('all') == 'true'
    if not is_reset_all and request.body:
        try:
            body = json.loads(request.body.decode('utf-8'))
            is_reset_all = body.get('all') is True
        except Exception:
            pass

    completed = []
    skipped_errors = []

    if is_reset_all:
        target_labs = all_labs()
        completed_count = len(target_labs)
    else:
        for lab in all_labs():
            try:
                status = lab_status(lab['service'], lab['key'])
            except (BotoCoreError, ClientError, ValueError) as exc:
                skipped_errors.append({
                    'service': lab['service'],
                    'lab': lab['key'],
                    'message': str(exc),
                })
                continue
            if status.get('complete'):
                completed.append(lab)
        target_labs = completed
        completed_count = len(completed)

    reset_results = []
    reset_errors = []
    for lab in reversed(target_labs):
        try:
            result = reset_lab(lab['service'], lab['key'])
            reset_results.append({
                'service': lab['service'],
                'lab': lab['key'],
                'title': lab.get('title'),
                'result': result,
            })
        except Exception as exc:
            reset_errors.append({
                'service': lab['service'],
                'lab': lab['key'],
                'title': lab.get('title'),
                'message': str(exc),
            })

    cache.delete(_labs_progress_cache_key())
    return JsonResponse({
        'reset': not reset_errors,
        'is_reset_all': is_reset_all,
        'completed_lab_count': completed_count,
        'target_lab_count': len(target_labs),
        'reset_lab_count': len(reset_results),
        'skipped_error_count': len(skipped_errors),
        'reset_error_count': len(reset_errors),
        'results': reset_results,
        'skipped_errors': skipped_errors,
        'reset_errors': reset_errors,
    })


@cache_control(public=True, max_age=60)
def services(request):
    return JsonResponse(services_payload())


def identity(request):
    try:
        factory = FlociClientFactory()
        payload = {
            'endpoint_url': factory.endpoint_url,
            'region': factory.region,
            **factory.credential_context(),
        }
        payload['identity'] = factory.identity()
        return JsonResponse(payload)
    except (BotoCoreError, ClientError, ValueError) as exc:
        payload = locals().get('payload', {})
        factory = locals().get('factory')
        identity_hint = factory.local_identity_hint() if factory else None
        if identity_hint:
            payload['identity'] = identity_hint
            payload['identity_resolved'] = False
            payload['identity_error'] = str(exc)
            return JsonResponse(payload)
        payload['error'] = str(exc)
        return JsonResponse(payload, status=502)


def health(request):
    try:
        return JsonResponse(FlociClientFactory().health())
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


def init_lifecycle(request):
    try:
        return JsonResponse(FlociClientFactory().init_status())
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


def iam(request):
    try:
        return _inventory_response(iam_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def s3(request):
    try:
        return _inventory_response(s3_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def ec2(request):
    try:
        return _inventory_response(ec2_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def kms(request):
    try:
        return _inventory_response(kms_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def lambda_(request):
    try:
        return _inventory_response(lambda_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def sqs(request):
    try:
        return _inventory_response(sqs_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def secretsmanager(request):
    try:
        return _inventory_response(secretsmanager_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def dynamodb(request):
    try:
        return _inventory_response(dynamodb_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def docdb(request):
    try:
        return _inventory_response(docdb_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def memorydb(request):
    try:
        return _inventory_response(memorydb_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def codepipeline(request):
    try:
        return _inventory_response(codepipeline_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def s3vectors(request):
    try:
        return _inventory_response(s3vectors_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def iot(request):
    try:
        return _inventory_response(iot_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def elasticbeanstalk(request):
    try:
        return _inventory_response(elasticbeanstalk_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def cloudwatch(request):
    try:
        return _inventory_response(cloudwatch_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def cloudcontrol(request):
    try:
        return _inventory_response(cloudcontrol_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def cloudmap(request):
    try:
        return _inventory_response(cloudmap_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def cloudtrail(request):
    try:
        return _inventory_response(cloudtrail_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def config(request):
    try:
        return _inventory_response(config_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def eventbridge(request):
    try:
        return _inventory_response(eventbridge_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def cognito(request):
    try:
        return _inventory_response(cognito_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def apigateway(request):
    try:
        return _inventory_response(apigateway_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def appconfig(request):
    try:
        return _inventory_response(appconfig_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def appsync(request):
    try:
        return _inventory_response(appsync_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def amazonmq(request):
    try:
        return _inventory_response(amazonmq_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def bedrockruntime(request):
    try:
        return _inventory_response(bedrockruntime_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def sagemaker(request):
    try:
        return _inventory_response(sagemaker_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def bedrock(request):
    try:
        return _inventory_response(bedrock_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def comprehend(request):
    try:
        return _inventory_response(comprehend_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def rekognition(request):
    try:
        return _inventory_response(rekognition_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def translate(request):
    try:
        return _inventory_response(translate_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def codebuild(request):
    try:
        return _inventory_response(codebuild_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def codedeploy(request):
    try:
        return _inventory_response(codedeploy_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def ecs(request):
    try:
        return _inventory_response(ecs_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def emr(request):
    try:
        return _inventory_response(emr_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def eks(request):
    try:
        return _inventory_response(eks_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def elasticache(request):
    try:
        return _inventory_response(elasticache_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def elasticloadbalancing(request):
    try:
        return _inventory_response(elasticloadbalancing_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def firehose(request):
    try:
        return _inventory_response(firehose_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def kinesis(request):
    try:
        return _inventory_response(kinesis_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def kafka(request):
    try:
        return _inventory_response(kafka_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def opensearch(request):
    try:
        return _inventory_response(opensearch_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def pipes(request):
    try:
        return _inventory_response(pipes_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def pricing(request):
    try:
        return _inventory_response(pricing_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def costexplorer(request):
    try:
        return _inventory_response(costexplorer_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def cur(request):
    try:
        return _inventory_response(cur_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def bcmdataexports(request):
    try:
        return _inventory_response(bcmdataexports_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def neptune(request):
    try:
        return _inventory_response(neptune_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def resourcegroupstagging(request):
    try:
        return _inventory_response(resourcegroupstagging_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def ssm(request):
    try:
        return _inventory_response(ssm_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def athena(request):
    try:
        return _inventory_response(athena_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def autoscaling(request):
    try:
        return _inventory_response(autoscaling_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def backup(request):
    try:
        return _inventory_response(backup_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def batch(request):
    try:
        return _inventory_response(batch_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def route53(request):
    try:
        return _inventory_response(route53_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def transfer(request):
    try:
        return _inventory_response(transfer_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def sns(request):
    try:
        return _inventory_response(sns_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def ses(request):
    try:
        return _inventory_response(ses_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def cloudformation(request):
    try:
        return _inventory_response(cloudformation_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def cloudfront(request):
    try:
        return _inventory_response(cloudfront_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def ecr(request):
    try:
        return _inventory_response(ecr_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def rds(request):
    try:
        return _inventory_response(rds_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def rdsdata(request):
    try:
        return _inventory_response(rdsdata_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def acm(request):
    try:
        return _inventory_response(acm_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def stepfunctions(request):
    try:
        return _inventory_response(stepfunctions_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def scheduler(request):
    try:
        return _inventory_response(scheduler_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def textract(request):
    try:
        return _inventory_response(textract_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def transcribe(request):
    try:
        return _inventory_response(transcribe_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def glue(request):
    try:
        return _inventory_response(glue_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def wafv2(request):
    try:
        return _inventory_response(wafv2_inventory)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=502)


def resources(request):
    service_keys = selected_service_keys(request)
    try:
        return JsonResponse(
            {
                'resources': [asdict(result) for result in list_resources(service_keys)],
            }
        )
    except (BotoCoreError, ClientError, ResponseParserError, ValueError) as exc:
        return JsonResponse({'resources': [], 'error': str(exc)}, status=502)


def cloudscape_app(request, **kwargs):
    """Mount point for the React 18 Cloudscape Console SPA."""
    return render(
        request,
        'dashboard/console_spa.html',
        {
            'title': 'AWS Management Console (Floci)',
        },
    )
