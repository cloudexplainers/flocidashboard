"""SageMaker AI lab: the machine learning lifecycle from data to a live endpoint. Prepare and split a dataset in S3,
train a model in a real training container, evaluate it on held-out data, register it as a SageMaker model, host it on
a real-time endpoint, get predictions, retrain with better hyperparameters and swap the endpoint to the new model
without downtime, and choose an inference option and a monitoring plan.

Floci 2.3.0 runs SageMaker training jobs and endpoints as real Docker containers under the SageMaker contract
(/opt/ml/input, /opt/ml/model, GET /ping and POST /invocations on port 8080), so everything here really runs. The lab
uses stock python and node images instead of the AWS framework images: the training script and the inference server
travel in the model artifact. Batch transform, Model Monitor, the model registry, Pipelines and tuning jobs are not
emulated, and training jobs cannot be deleted (on AWS either), so each run gets a new job name.
"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import tarfile
import time
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'sagemaker'
LAB_KEY = 'train-to-endpoint'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
ACCOUNT = '000000000000'
BUCKET = 'lab-ai-sagemaker-data'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/lab-ai-sagemaker-execution'
TRAIN_IMAGE = 'python:3.12-alpine'
SERVE_IMAGE = 'node:22-alpine'
INSTANCE_TYPE = 'ml.m5.large'
JOB_PREFIX = 'lab-ai-price-train'
MODEL = {1: 'lab-ai-price-model-v1', 2: 'lab-ai-price-model-v2'}
CONFIG = {1: 'lab-ai-price-config-v1', 2: 'lab-ai-price-config-v2'}
ENDPOINT = 'lab-ai-price-endpoint'
HYPERPARAMETERS = {1: {'epochs': '5', 'learning_rate': '0.1'}, 2: {'epochs': '200', 'learning_rate': '0.1'}}
TARGET_R2 = 0.9
ROWS, SEED = 200, 7
SAMPLE_HOMES = [850, 1500, 2400]

TRAIN_SCRIPT = '''\
"""Linear regression with gradient descent: price from floor area. SageMaker script mode, without the SDK."""
import csv, json, math, os, shutil

base = '/opt/ml'
params = json.load(open(f'{base}/input/config/hyperparameters.json'))
epochs, rate = int(params.get('epochs', 50)), float(params.get('learning_rate', 0.1))
version = int(params.get('model_version', 1))
rows = [(float(x), float(y)) for x, y in csv.reader(open(f'{base}/input/data/train/train.csv'))]
xs, ys = [x for x, _ in rows], [y for _, y in rows]
mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / len(xs))
sy = math.sqrt(sum((y - my) ** 2 for y in ys) / len(ys))
w = b = 0.0
for epoch in range(epochs):
    gw = gb = 0.0
    for x, y in rows:
        error = w * (x - mx) / sx + b - (y - my) / sy
        gw += error * (x - mx) / sx
        gb += error
    w -= rate * gw / len(rows)
    b -= rate * gb / len(rows)
rmse = math.sqrt(sum(((w * (x - mx) / sx + b) * sy + my - y) ** 2 for x, y in rows) / len(rows))
print(f'epochs={epochs} learning_rate={rate} train_rmse={rmse:.2f}')
os.makedirs(f'{base}/model', exist_ok=True)  # Floci 2.3.0 does not create it
json.dump({'version': version, 'w': w, 'b': b, 'mean_x': mx, 'std_x': sx, 'mean_y': my, 'std_y': sy,
           'epochs': epochs, 'learning_rate': rate, 'train_rmse': rmse}, open(f'{base}/model/model.json', 'w'))
shutil.copy(f'{base}/input/data/code/inference.js', f'{base}/model/inference.js')
'''

INFERENCE_JS = '''\
// SageMaker hosting contract: answer GET /ping and POST /invocations on port 8080.
// The endpoint runs the stock node image with NODE_OPTIONS=--require /opt/ml/model/inference.js.
const http = require('http');
const fs = require('fs');
const m = JSON.parse(fs.readFileSync('/opt/ml/model/model.json', 'utf8'));
const predict = (x) => (m.w * (x - m.mean_x) / m.std_x + m.b) * m.std_y + m.mean_y;
const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/ping') { res.writeHead(200); return res.end('ok'); }
  if (req.method === 'POST' && req.url === '/invocations') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      const xs = body.split(/[\\s,]+/).filter(Boolean).map(Number);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ model_version: m.version, predictions: xs.map((x) => Math.round(predict(x))) }));
    });
    return;
  }
  res.writeHead(404); res.end();
});
server.listen(8080);
// The image runs `serve`, which is not a node script; the server above is already running, so ignore that error.
process.on('uncaughtException', (error) => { if (error.code !== 'MODULE_NOT_FOUND') throw error; });
process.on('SIGTERM', () => server.close(() => process.exit(0)));
'''

# Inference options: which fits each workload.
WORKLOADS = [
    ('Price a home while the buyer waits on the page', 'Real-time endpoint', 'low latency, always on'),
    ('Re-price all 2 million listings every night', 'Batch transform', 'large offline job, no endpoint to pay for'),
    ('A few hundred price checks a day, in bursts', 'Serverless inference', 'scales to zero between bursts'),
    ('Appraise from 500 MB of photos, answer in minutes', 'Asynchronous inference', 'large payloads, queued requests'),
]

DATA_COMMAND = (
    f'aws s3 mb s3://{BUCKET}\n'
    f'aws s3 cp train.csv s3://{BUCKET}/train/train.csv            # 80% of the rows\n'
    f'aws s3 cp validation.csv s3://{BUCKET}/validation/validation.csv   # 20%, held out\n'
    f'aws s3 cp train.py inference.js s3://{BUCKET}/code/ --recursive'
)
TRAIN_COMMAND = (
    f'aws sagemaker create-training-job --training-job-name {JOB_PREFIX}-v1-<run> --role-arn {ROLE_ARN} \\\n'
    f'  --algorithm-specification TrainingImage={TRAIN_IMAGE},TrainingInputMode=File,'
    'ContainerEntrypoint=python,/opt/ml/input/data/code/train.py \\\n'
    '  --hyper-parameters epochs=5,learning_rate=0.1,model_version=1 \\\n'
    f'  --input-data-config ChannelName=train,DataSource={{S3DataSource={{S3Uri=s3://{BUCKET}/train/}}}} '
    f'ChannelName=code,DataSource={{S3DataSource={{S3Uri=s3://{BUCKET}/code/}}}} \\\n'
    f'  --output-data-config S3OutputPath=s3://{BUCKET}/models/ \\\n'
    f'  --resource-config InstanceType={INSTANCE_TYPE},InstanceCount=1,VolumeSizeInGB=5 '
    '--stopping-condition MaxRuntimeInSeconds=600\n'
    f'aws sagemaker describe-training-job --training-job-name {JOB_PREFIX}-v1-<run>'
)
EVALUATE_COMMAND = (
    f'aws s3 cp s3://{BUCKET}/models/{JOB_PREFIX}-v1-<run>/output/model.tar.gz . && tar xzf model.tar.gz\n'
    '# Score model.json on validation.csv: MAE, RMSE and R squared'
)
MODEL_COMMAND = (
    f'aws sagemaker create-model --model-name {MODEL[1]} --execution-role-arn {ROLE_ARN} \\\n'
    f'  --primary-container Image={SERVE_IMAGE},ModelDataUrl=s3://{BUCKET}/models/{JOB_PREFIX}-v1-<run>/output/model.tar.gz,'
    '"Environment={NODE_OPTIONS=--require /opt/ml/model/inference.js}"'
)
DEPLOY_COMMAND = (
    f'aws sagemaker create-endpoint-config --endpoint-config-name {CONFIG[1]} --production-variants '
    f'VariantName=AllTraffic,ModelName={MODEL[1]},InitialInstanceCount=1,InstanceType={INSTANCE_TYPE}\n'
    f'aws sagemaker create-endpoint --endpoint-name {ENDPOINT} --endpoint-config-name {CONFIG[1]}\n'
    f'aws sagemaker wait endpoint-in-service --endpoint-name {ENDPOINT}'
)
INVOKE_COMMAND = (
    f'aws sagemaker-runtime invoke-endpoint --endpoint-name {ENDPOINT} --content-type text/csv '
    f'--body "{",".join(str(x) for x in SAMPLE_HOMES)}" --cli-binary-format raw-in-base64-out out.json'
)
UPDATE_COMMAND = (
    f'aws sagemaker create-training-job --training-job-name {JOB_PREFIX}-v2-<run> ... '
    '--hyper-parameters epochs=200,learning_rate=0.1,model_version=2\n'
    f'aws sagemaker create-model --model-name {MODEL[2]} ...\n'
    f'aws sagemaker create-endpoint-config --endpoint-config-name {CONFIG[2]} ...\n'
    f'aws sagemaker update-endpoint --endpoint-name {ENDPOINT} --endpoint-config-name {CONFIG[2]}'
)
OPERATE_COMMAND = (
    f'aws sagemaker create-endpoint-config --endpoint-config-name lab-ai-price-serverless --production-variants '
    f'VariantName=AllTraffic,ModelName={MODEL[2]},"ServerlessConfig={{MemorySizeInMB=2048,MaxConcurrency=5}}"\n'
    'aws sagemaker create-transform-job ...           # batch transform\n'
    'aws sagemaker create-monitoring-schedule ...     # Model Monitor'
)
SERVERLESS_CONFIG = 'lab-ai-price-serverless'

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Train a model and deploy it to an endpoint with SageMaker AI',
    'description': (
        'Walk the machine learning lifecycle: prepare and split data in S3, train a model in a real training '
        'container, evaluate it on held-out data, host it on a real-time endpoint, get predictions, retrain with '
        'better hyperparameters and swap the endpoint to the new model without downtime, then pick an inference '
        'option and a monitoring plan.'
    ),
    'steps': [
        {
            'key': 'prepare-data',
            'title': 'Prepare the data and split it into training and validation sets',
            'command': DATA_COMMAND,
            'explanation': (
                'Every ML project starts with data: here 200 home sales, with floor area as the feature and price as '
                'the label. Holding back 20% as a validation set lets you measure the model on data it never saw, '
                'which is how you catch overfitting. SageMaker reads training data from S3. How Floci emulates this: '
                'the bucket and files are real; the training script and the inference server go in a code/ folder '
                'because the lab uses stock python and node images instead of the AWS framework images.'
            ),
        },
        {
            'key': 'train-model',
            'title': 'Train a model in a SageMaker training job',
            'command': TRAIN_COMMAND,
            'explanation': (
                'A training job starts a container on the instance type you pick, copies each S3 channel to '
                '/opt/ml/input/data/<channel>, passes hyperparameters (settings you choose, such as epochs and '
                'learning rate, unlike the weights the model learns), and uploads whatever the script writes to '
                '/opt/ml/model as model.tar.gz. You pay only while it runs. How Floci emulates this: the job really '
                'runs in a Docker container; the first run downloads the python image, so it can take a minute.'
            ),
        },
        {
            'key': 'evaluate-model',
            'title': 'Evaluate the model on the validation set',
            'command': EVALUATE_COMMAND,
            'explanation': (
                'Evaluation compares predictions with known answers on held-out data. For regression, mean absolute '
                'error (MAE) and root mean squared error (RMSE) are in the label\'s units and R squared says how much '
                'of the variation the model explains (1 is perfect). Classification uses accuracy, precision, recall, '
                f'F1 and AUC instead. This lab\'s bar is R squared of {TARGET_R2:g}. How Floci emulates this: the '
                'artifact is real; the step downloads it and scores it.'
            ),
        },
        {
            'key': 'create-model',
            'title': 'Register the trained artifact as a SageMaker model',
            'command': MODEL_COMMAND,
            'explanation': (
                'A SageMaker model ties an inference container image to a model artifact in S3, an execution role, '
                'and environment variables. It is a definition, not a running server, so it costs nothing until '
                'you deploy it. How Floci emulates this: the model is stored as on AWS; the environment variable '
                'makes the stock node image load the inference server from the artifact.'
            ),
        },
        {
            'key': 'deploy-endpoint',
            'title': 'Host the model on a real-time endpoint',
            'command': DEPLOY_COMMAND,
            'explanation': (
                'An endpoint configuration says which models run on which instance types and how many instances; '
                'the endpoint is the HTTPS address in front of them. SageMaker starts the container with the '
                'command serve, waits for GET /ping, and bills per instance-hour while the endpoint is up. How Floci '
                'emulates this: the endpoint is a real container answering /ping and /invocations.'
            ),
        },
        {
            'key': 'invoke-endpoint',
            'title': 'Get predictions from the endpoint',
            'command': INVOKE_COMMAND,
            'explanation': (
                'Inference is using a trained model on new data. InvokeEndpoint sends the request body to the '
                'container\'s /invocations and returns its answer; the content type tells the container how to read '
                'the body. How Floci emulates this: the request really reaches the container, and the step checks '
                'the answer against the model\'s own math.'
            ),
        },
        {
            'key': 'retrain-and-update',
            'title': 'Retrain with better hyperparameters and update the endpoint without downtime',
            'command': UPDATE_COMMAND,
            'explanation': (
                'MLOps repeats the lifecycle: when a model is not good enough, or the data drifts, you retrain, '
                'evaluate, and deploy the new version. UpdateEndpoint switches the endpoint to a new configuration '
                'blue/green: the new model starts, traffic moves, and the old one stops, with no downtime. Keeping '
                'every model version lets you roll back. How Floci emulates this: the second job and the swap are '
                'real; during the update DescribeEndpoint reads Creating instead of Updating.'
            ),
        },
        {
            'key': 'choose-inference-and-monitoring',
            'title': 'Choose an inference option and plan monitoring',
            'command': OPERATE_COMMAND,
            'explanation': (
                'Real-time endpoints suit interactive, low-latency requests; serverless inference suits bursty '
                'traffic and scales to zero; asynchronous inference suits large payloads and long processing; batch '
                'transform scores a whole dataset with no endpoint. After deployment, SageMaker Model Monitor '
                'compares live requests with the training data to catch drift. How Floci emulates this: serverless '
                'endpoint configurations are stored, while batch transform and Model Monitor are not emulated, so '
                'the step checks drift itself.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    # UpdateEndpoint and DeleteEndpoint block while Floci stops the old container; a retry would repeat the call.
    factory = FlociClientFactory()
    factory.config = factory.config.merge(Config(read_timeout=60, retries={'max_attempts': 0}))
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


# ── Pure logic ───────────────────────────────────────────────────────────────

def dataset(rows: int = ROWS, seed: int = SEED) -> list[tuple[int, int]]:
    """Home sales: price is about $150 per square foot plus $40,000, with noise."""
    rng = random.Random(seed)
    data = []
    for _ in range(rows):
        area = rng.randint(600, 3200)
        data.append((area, round(40000 + 150 * area + rng.gauss(0, 15000))))
    return data


def split(data: list[tuple[int, int]], validation_share: float = 0.2) -> tuple[list, list]:
    cut = len(data) - int(len(data) * validation_share)
    return data[:cut], data[cut:]


def to_csv(rows: list[tuple[int, int]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator='\n').writerows(rows)
    return buffer.getvalue().encode()


def predict(model: dict[str, float], area: float) -> float:
    return (model['w'] * (area - model['mean_x']) / model['std_x'] + model['b']) * model['std_y'] + model['mean_y']


def metrics(model: dict[str, float], rows: list[tuple[int, int]]) -> dict[str, float]:
    errors = [predict(model, x) - y for x, y in rows]
    mean_y = sum(y for _, y in rows) / len(rows)
    total = sum((y - mean_y) ** 2 for _, y in rows)
    return {
        'MAE': round(sum(abs(item) for item in errors) / len(errors), 2),
        'RMSE': round(math.sqrt(sum(item * item for item in errors) / len(errors)), 2),
        'R2': round(1 - sum(item * item for item in errors) / total, 4) if total else 0.0,
    }


def drift(training: list[float], live: list[float]) -> dict[str, Any]:
    """How far the live mean moved, in training standard deviations (Model Monitor compares richer statistics)."""
    mean = sum(training) / len(training)
    std = math.sqrt(sum((x - mean) ** 2 for x in training) / len(training)) or 1.0
    live_mean = sum(live) / len(live)
    shift = abs(live_mean - mean) / std
    return {'TrainingMean': round(mean, 1), 'LiveMean': round(live_mean, 1), 'ShiftInStdDevs': round(shift, 2),
            'Drifted': shift > 1.0}


def read_model(archive: bytes) -> tuple[dict[str, Any], list[str]]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        names = tar.getnames()
        member = next(name for name in names if name.endswith('model.json'))
        handle = tar.extractfile(member)
        return json.loads(handle.read() if handle else b'{}'), names


# ── Lookups ──────────────────────────────────────────────────────────────────

def _objects() -> set[str]:
    try:
        contents = _client('s3').list_objects_v2(Bucket=BUCKET).get('Contents', [])
    except ClientError as exc:
        if _code(exc) in ('NoSuchBucket', '404'):
            return set()
        raise
    return {item['Key'] for item in contents}


def _latest_job(version: int) -> dict[str, Any] | None:
    sagemaker = _client('sagemaker')
    summaries = sagemaker.list_training_jobs(NameContains=f'{JOB_PREFIX}-v{version}-', SortBy='CreationTime',
                                             SortOrder='Descending').get('TrainingJobSummaries', [])
    if not summaries:
        return None
    return sagemaker.describe_training_job(TrainingJobName=summaries[0]['TrainingJobName'])


def _artifact_key(job: dict[str, Any] | None) -> str | None:
    uri = ((job or {}).get('ModelArtifacts') or {}).get('S3ModelArtifacts') or ''
    prefix = f's3://{BUCKET}/'
    return uri[len(prefix):] if uri.startswith(prefix) else None


def _describe(kind: str, name: str) -> dict[str, Any] | None:
    sagemaker = _client('sagemaker')
    call = {'model': lambda: sagemaker.describe_model(ModelName=name),
            'config': lambda: sagemaker.describe_endpoint_config(EndpointConfigName=name),
            'endpoint': lambda: sagemaker.describe_endpoint(EndpointName=name)}[kind]
    try:
        return call()
    except ClientError as exc:
        if _code(exc) in ('ValidationException', 'ResourceNotFound', 'ResourceNotFoundException'):
            return None
        raise


def _invoke(body: str) -> dict[str, Any]:
    response = _client('sagemaker-runtime').invoke_endpoint(EndpointName=ENDPOINT, ContentType='text/csv',
                                                            Accept='application/json', Body=body.encode())
    return json.loads(response['Body'].read())


def _download_model(job: dict[str, Any] | None) -> dict[str, Any] | None:
    key = _artifact_key(job)
    if not key or key not in _objects():
        return None
    model, _ = read_model(_client('s3').get_object(Bucket=BUCKET, Key=key)['Body'].read())
    return model


def _train(version: int) -> dict[str, Any]:
    """Start a training job for this version (unless one is running or finished with its artifact) and wait."""
    job = _latest_job(version)
    reusable = job and (job['TrainingJobStatus'] == 'InProgress'
                        or (job['TrainingJobStatus'] == 'Completed' and _artifact_key(job) in _objects()))
    if not reusable:
        name = f'{JOB_PREFIX}-v{version}-{int(time.time())}'
        _client('sagemaker').create_training_job(
            TrainingJobName=name, RoleArn=ROLE_ARN,
            AlgorithmSpecification={'TrainingImage': TRAIN_IMAGE, 'TrainingInputMode': 'File',
                                    'ContainerEntrypoint': ['python', '/opt/ml/input/data/code/train.py']},
            HyperParameters={**HYPERPARAMETERS[version], 'model_version': str(version)},
            InputDataConfig=[
                {'ChannelName': 'train', 'DataSource': {'S3DataSource': {
                    'S3DataType': 'S3Prefix', 'S3Uri': f's3://{BUCKET}/train/', 'S3DataDistributionType': 'FullyReplicated'}}},
                {'ChannelName': 'code', 'DataSource': {'S3DataSource': {
                    'S3DataType': 'S3Prefix', 'S3Uri': f's3://{BUCKET}/code/', 'S3DataDistributionType': 'FullyReplicated'}}},
            ],
            OutputDataConfig={'S3OutputPath': f's3://{BUCKET}/models/'},
            ResourceConfig={'InstanceType': INSTANCE_TYPE, 'InstanceCount': 1, 'VolumeSizeInGB': 5},
            StoppingCondition={'MaxRuntimeInSeconds': 600})
    _wait(lambda: (_latest_job(version) or {}).get('TrainingJobStatus') != 'InProgress', timeout=240, delay=3)
    return _latest_job(version) or {}


def _deploy_model(version: int, job: dict[str, Any]) -> None:
    sagemaker = _client('sagemaker')
    model = _describe('model', MODEL[version])
    url = f's3://{BUCKET}/{_artifact_key(job)}'
    if model and model.get('PrimaryContainer', {}).get('ModelDataUrl') != url:
        sagemaker.delete_model(ModelName=MODEL[version])
        model = None
    if not model:
        sagemaker.create_model(ModelName=MODEL[version], ExecutionRoleArn=ROLE_ARN, PrimaryContainer={
            'Image': SERVE_IMAGE, 'ModelDataUrl': url,
            'Environment': {'NODE_OPTIONS': '--require /opt/ml/model/inference.js'}})


def _ensure_config(version: int) -> None:
    if not _describe('config', CONFIG[version]):
        _client('sagemaker').create_endpoint_config(EndpointConfigName=CONFIG[version], ProductionVariants=[{
            'VariantName': 'AllTraffic', 'ModelName': MODEL[version], 'InitialInstanceCount': 1,
            'InstanceType': INSTANCE_TYPE}])


def _endpoint_status() -> str | None:
    endpoint = _describe('endpoint', ENDPOINT)
    return endpoint.get('EndpointStatus') if endpoint else None


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_marker(step: str) -> tuple[bool, str]:
    marker = _marked(step)
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_data() -> tuple[bool, str]:
    needed = ['train/train.csv', 'validation/validation.csv', 'code/train.py', 'code/inference.js']
    missing = [key for key in needed if key not in _objects()]
    if missing:
        return False, f'Upload {", ".join(missing)} to s3://{BUCKET}.'
    return True, f'Training data, validation data and code are in s3://{BUCKET}.'


def _check_job(version: int) -> tuple[bool, str]:
    job = _latest_job(version)
    if not job:
        return False, f'Start a {JOB_PREFIX}-v{version} training job.'
    if job['TrainingJobStatus'] != 'Completed':
        reason = f': {job.get("FailureReason")}' if job.get('FailureReason') else ''
        return False, f'{job["TrainingJobName"]} is {job["TrainingJobStatus"]}{reason}.'
    if _artifact_key(job) not in _objects():
        return False, f'{job["TrainingJobName"]} has no model.tar.gz in s3://{BUCKET}; train again.'
    return True, f'{job["TrainingJobName"]} completed and saved its model artifact.'


def _check_train() -> tuple[bool, str]:
    return _check_job(1)


def _check_evaluate() -> tuple[bool, str]:
    ok, message = _check_job(1)
    if not ok:
        return ok, message
    return _check_marker('evaluate-model')


def _check_model() -> tuple[bool, str]:
    if not _describe('model', MODEL[1]) and not _describe('model', MODEL[2]):
        return False, f'Create the {MODEL[1]} model.'
    return True, 'The model is registered.'


def _check_deploy() -> tuple[bool, str]:
    status = _endpoint_status()
    if status != 'InService':
        return False, f'{ENDPOINT} is {status or "missing"}; create it and wait for InService.'
    return True, f'{ENDPOINT} is InService.'


def _check_invoke() -> tuple[bool, str]:
    ok, message = _check_deploy()
    if not ok:
        return ok, message
    return _check_marker('invoke-endpoint')


def _check_update() -> tuple[bool, str]:
    endpoint = _describe('endpoint', ENDPOINT)
    if not endpoint or endpoint.get('EndpointStatus') != 'InService':
        return False, f'{ENDPOINT} is not InService.'
    if endpoint.get('EndpointConfigName') != CONFIG[2]:
        return False, f'{ENDPOINT} still uses {endpoint.get("EndpointConfigName")}; update it to {CONFIG[2]}.'
    return _check_marker('retrain-and-update')


def _check_operate() -> tuple[bool, str]:
    return _check_marker('choose-inference-and-monitoring')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_prepare() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)
    train, validation = split(dataset())
    s3.put_object(Bucket=BUCKET, Key='train/train.csv', Body=to_csv(train), ContentType='text/csv')
    s3.put_object(Bucket=BUCKET, Key='validation/validation.csv', Body=to_csv(validation), ContentType='text/csv')
    s3.put_object(Bucket=BUCKET, Key='code/train.py', Body=TRAIN_SCRIPT.encode(), ContentType='text/x-python')
    s3.put_object(Bucket=BUCKET, Key='code/inference.js', Body=INFERENCE_JS.encode(), ContentType='text/javascript')
    areas = [x for x, _ in train]
    response = {'Bucket': BUCKET, 'Rows': len(train) + len(validation), 'TrainingRows': len(train),
                'ValidationRows': len(validation), 'Feature': 'floor area (sq ft)', 'Label': 'sale price (USD)',
                'FeatureRange': [min(areas), max(areas)], 'FirstRows': [{'Area': x, 'Price': y} for x, y in train[:3]],
                'Objects': sorted(_objects())}
    verified, check = _check_data()
    return _result('prepare-data', DATA_COMMAND, response, verified, check, started)


def step_train() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_data()
    if not ok:
        return _result('train-model', TRAIN_COMMAND, {}, False, message, started)
    job = _train(1)
    response = {key: job.get(key) for key in ('TrainingJobName', 'TrainingJobStatus', 'SecondaryStatus',
                                              'FailureReason', 'ModelArtifacts', 'TrainingStartTime', 'TrainingEndTime')}
    response['HyperParameters'] = HYPERPARAMETERS[1]
    verified, check = _check_train()
    return _result('train-model', TRAIN_COMMAND, response, verified, check, started)


def step_evaluate() -> dict[str, Any]:
    started = time.perf_counter()
    job = _latest_job(1)
    model = _download_model(job)
    if not model:
        verified, check = _check_train()
        return _result('evaluate-model', EVALUATE_COMMAND, {}, False, check if not verified else 'No model found.', started)
    train, validation = split(dataset())
    scores = {'Training': metrics(model, train), 'Validation': metrics(model, validation)}
    good = scores['Validation']['R2'] >= TARGET_R2
    message = (f'Model v1 ({model["epochs"]} epochs) scores R squared {scores["Validation"]["R2"]:g} on validation '
               f'with an average error of ${scores["Validation"]["MAE"]:,.0f}: '
               + ('good enough to deploy.' if good else
                  f'below the {TARGET_R2:g} bar, so deploy it for now and retrain with more epochs in step 7.'))
    _marker('evaluate-model', {'message': message, 'scores': scores})
    response = {'Artifact': f's3://{BUCKET}/{_artifact_key(job)}', 'Model': model, 'Scores': scores,
                'MeetsTarget': good}
    verified, check = _check_evaluate()
    return _result('evaluate-model', EVALUATE_COMMAND, response, verified, check, started)


def step_create_model() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_job(1)
    if not ok:
        return _result('create-model', MODEL_COMMAND, {}, False, message, started)
    _deploy_model(1, _latest_job(1) or {})
    model = _describe('model', MODEL[1]) or {}
    response = {key: model.get(key) for key in ('ModelName', 'ModelArn', 'ExecutionRoleArn', 'PrimaryContainer')}
    verified, check = _check_model()
    return _result('create-model', MODEL_COMMAND, response, verified, check, started)


def step_deploy() -> dict[str, Any]:
    started = time.perf_counter()
    if not _describe('model', MODEL[1]):
        return _result('deploy-endpoint', DEPLOY_COMMAND, {}, False, f'Create the {MODEL[1]} model first.', started)
    _ensure_config(1)
    if not _describe('endpoint', ENDPOINT):
        _client('sagemaker').create_endpoint(EndpointName=ENDPOINT, EndpointConfigName=CONFIG[1])
    _wait(lambda: _endpoint_status() not in ('Creating', 'Updating'), timeout=180, delay=2)
    endpoint = _describe('endpoint', ENDPOINT) or {}
    config = _describe('config', endpoint.get('EndpointConfigName') or CONFIG[1]) or {}
    response = {'Endpoint': {key: endpoint.get(key) for key in ('EndpointName', 'EndpointConfigName', 'EndpointStatus',
                                                                 'FailureReason')},
                'ProductionVariants': config.get('ProductionVariants'),
                'Seconds': round(time.perf_counter() - started, 1)}
    verified, check = _check_deploy()
    return _result('deploy-endpoint', DEPLOY_COMMAND, response, verified, check, started)


def step_invoke() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_deploy()
    if not ok:
        return _result('invoke-endpoint', INVOKE_COMMAND, {}, False, message, started)
    answer = _invoke(','.join(str(x) for x in SAMPLE_HOMES))
    version = answer.get('model_version')
    model = _download_model(_latest_job(version or 1)) or {}
    expected = [round(predict(model, x)) for x in SAMPLE_HOMES] if model else []
    matches = bool(expected) and expected == answer.get('predictions')
    rows = [{'Area': x, 'PredictedPrice': y} for x, y in zip(SAMPLE_HOMES, answer.get('predictions') or [])]
    if matches:
        _marker('invoke-endpoint', {'message': (
            f'{ENDPOINT} (model v{version}) priced {len(rows)} homes, from ${rows[0]["PredictedPrice"]:,} for '
            f'{rows[0]["Area"]} sq ft to ${rows[-1]["PredictedPrice"]:,} for {rows[-1]["Area"]} sq ft, matching the '
            'model\'s own math.')})
    response = {'Request': {'ContentType': 'text/csv', 'Body': ','.join(str(x) for x in SAMPLE_HOMES)},
                'Response': answer, 'Predictions': rows, 'MatchesModel': matches}
    verified, check = _check_invoke()
    if not matches:
        verified, check = False, 'The endpoint answered, but not with the model\'s predictions.'
    return _result('invoke-endpoint', INVOKE_COMMAND, response, verified, check, started)


def step_update() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_deploy()
    if not ok:
        return _result('retrain-and-update', UPDATE_COMMAND, {}, False, message, started)
    job = _train(2)
    ok, message = _check_job(2)
    if not ok:
        return _result('retrain-and-update', UPDATE_COMMAND, {'TrainingJob': job.get('TrainingJobName')}, False,
                       message, started)
    _deploy_model(2, job)
    _ensure_config(2)
    endpoint = _describe('endpoint', ENDPOINT) or {}
    if endpoint.get('EndpointConfigName') != CONFIG[2]:
        _client('sagemaker').update_endpoint(EndpointName=ENDPOINT, EndpointConfigName=CONFIG[2])
    _wait(lambda: _endpoint_status() == 'InService' and
          (_describe('endpoint', ENDPOINT) or {}).get('EndpointConfigName') == CONFIG[2], timeout=180, delay=2)
    _, validation = split(dataset())
    v1, v2 = _download_model(_latest_job(1)), _download_model(job)
    scores = {'v1': metrics(v1, validation) if v1 else None, 'v2': metrics(v2, validation) if v2 else None}
    answer: dict[str, Any] = {}
    if _endpoint_status() == 'InService':
        answer = _invoke(','.join(str(x) for x in SAMPLE_HOMES))
    if answer.get('model_version') == 2 and scores['v2']:
        _marker('retrain-and-update', {'message': (
            f'Model v2 ({HYPERPARAMETERS[2]["epochs"]} epochs) scores R squared {scores["v2"]["R2"]:g} on validation '
            f'against {scores["v1"]["R2"] if scores["v1"] else "?"} for v1, and {ENDPOINT} now serves v2 from '
            f'{CONFIG[2]}; {CONFIG[1]} and {MODEL[1]} are kept for rollback.')})
    response = {'TrainingJob': job.get('TrainingJobName'), 'ValidationScores': scores,
                'Endpoint': {key: (_describe('endpoint', ENDPOINT) or {}).get(key) for key in (
                    'EndpointName', 'EndpointConfigName', 'EndpointStatus')},
                'Response': answer, 'Seconds': round(time.perf_counter() - started, 1)}
    verified, check = _check_update()
    return _result('retrain-and-update', UPDATE_COMMAND, response, verified, check, started)


def step_operate() -> dict[str, Any]:
    started = time.perf_counter()
    sagemaker = _client('sagemaker')
    model_name = MODEL[2] if _describe('model', MODEL[2]) else MODEL[1]
    if not _describe('model', model_name):
        return _result('choose-inference-and-monitoring', OPERATE_COMMAND, {}, False, 'Create a model first.', started)
    if not _describe('config', SERVERLESS_CONFIG):
        sagemaker.create_endpoint_config(EndpointConfigName=SERVERLESS_CONFIG, ProductionVariants=[{
            'VariantName': 'AllTraffic', 'ModelName': model_name,
            'ServerlessConfig': {'MemorySizeInMB': 2048, 'MaxConcurrency': 5}}])
    serverless = (_describe('config', SERVERLESS_CONFIG) or {}).get('ProductionVariants')
    unsupported = {}
    for name, call in (('CreateTransformJob', lambda: sagemaker.create_transform_job(
            TransformJobName=f'lab-ai-price-batch-{int(time.time())}', ModelName=model_name,
            TransformInput={'DataSource': {'S3DataSource': {'S3DataType': 'S3Prefix', 'S3Uri': f's3://{BUCKET}/validation/'}}},
            TransformOutput={'S3OutputPath': f's3://{BUCKET}/batch/'},
            TransformResources={'InstanceType': INSTANCE_TYPE, 'InstanceCount': 1})),
            ('ListMonitoringSchedules', lambda: sagemaker.list_monitoring_schedules())):
        try:
            call()
            unsupported[name] = 'answers'
        except ClientError as exc:
            unsupported[name] = 'not emulated' if 'not supported' in str(exc) else _code(exc)
    train, _ = split(dataset())
    # Live traffic this month: buyers are now looking at much larger homes than the model was trained on.
    live = [3800, 4100, 4400, 3900, 4600, 4200]
    check_drift = drift([float(x) for x, _ in train], [float(x) for x in live])
    message = (f'Matched {len(WORKLOADS)} workloads to real-time, batch, serverless and asynchronous inference and '
               f'saved a serverless configuration. Live requests average {check_drift["LiveMean"]:,.0f} sq ft against '
               f'{check_drift["TrainingMean"]:,.0f} in training ({check_drift["ShiftInStdDevs"]:g} standard deviations): '
               + ('drift, so retrain on newer sales.' if check_drift['Drifted'] else 'no drift.'))
    _marker('choose-inference-and-monitoring', {'message': message})
    response = {'Workloads': [{'Workload': w, 'Choose': c, 'Why': why} for w, c, why in WORKLOADS],
                'ServerlessConfig': serverless, 'InFloci': unsupported, 'DriftCheck': check_drift}
    verified, check = _check_operate()
    return _result('choose-inference-and-monitoring', OPERATE_COMMAND, response, verified, check, started)


RUNNERS = {
    'prepare-data': step_prepare,
    'train-model': step_train,
    'evaluate-model': step_evaluate,
    'create-model': step_create_model,
    'deploy-endpoint': step_deploy,
    'invoke-endpoint': step_invoke,
    'retrain-and-update': step_update,
    'choose-inference-and-monitoring': step_operate,
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
    'prepare-data': _check_data,
    'train-model': _check_train,
    'evaluate-model': _check_evaluate,
    'create-model': _check_model,
    'deploy-endpoint': _check_deploy,
    'invoke-endpoint': _check_invoke,
    'retrain-and-update': _check_update,
    'choose-inference-and-monitoring': _check_operate,
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
    """Delete the endpoint, endpoint configurations, models and the data bucket. Training jobs cannot be deleted;
    they stay listed and the next run uses new names."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code in ('ValidationException', 'NoSuchBucket', 'NoSuchKey', '404'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    sagemaker, s3 = _client('sagemaker'), _client('s3')
    try:
        endpoint = _describe('endpoint', ENDPOINT)
    except (ClientError, BotoCoreError):
        endpoint = None
    if endpoint:
        attempt(f'endpoint:{ENDPOINT}', lambda: sagemaker.delete_endpoint(EndpointName=ENDPOINT))
        if not _wait(lambda: _describe('endpoint', ENDPOINT) is None, timeout=60, delay=2):
            errors.append(f'endpoint:{ENDPOINT}: still reported after delete')
    for name in (CONFIG[1], CONFIG[2], SERVERLESS_CONFIG):
        if _describe('config', name):
            attempt(f'endpoint-config:{name}', lambda name=name: sagemaker.delete_endpoint_config(EndpointConfigName=name))
    for name in MODEL.values():
        if _describe('model', name):
            attempt(f'model:{name}', lambda name=name: sagemaker.delete_model(ModelName=name))
    try:
        keys = sorted(_objects())
    except (ClientError, BotoCoreError):
        keys = []
    for key in keys:
        attempt(f's3://{BUCKET}/{key}', lambda key=key: s3.delete_object(Bucket=BUCKET, Key=key))
    try:
        s3.head_bucket(Bucket=BUCKET)
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))
    except ClientError:
        pass

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws sagemaker delete-endpoint --endpoint-name {ENDPOINT}\n'
            f'aws sagemaker delete-endpoint-config --endpoint-config-name {CONFIG[1]}   # and v2, serverless\n'
            f'aws sagemaker delete-model --model-name {MODEL[1]}   # and v2\n'
            f'aws s3 rb s3://{BUCKET} --force'
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
            else 'The endpoint, configurations, models and bucket are gone; training jobs stay listed, as on AWS.',
        },
    }
