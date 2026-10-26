"""AI services tour lab: match business problems to the AWS AI services, then send real requests to Comprehend,
Translate, Rekognition, Textract and Transcribe with sample reviews, a product photo, a receipt and a call recording,
compare a managed service with a keyword rule, and route low-confidence predictions to human review.

Floci 2.3.0 accepts these requests and checks their inputs (S3 objects must exist, language codes must be real), but
answers with fixed results: every text is NEUTRAL English with no PII, every image is labeled "Floci", Translate echoes
the input, and Transcribe jobs complete at once without writing a transcript. Polly, Lex, Personalize, Kendra and
Forecast are not emulated. Each step says what it would see on AWS and judges the use case from what came back.
"""

from __future__ import annotations

import io
import json
import struct
import time
import wave
import zlib
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'comprehend'
LAB_KEY = 'ai-services-tour'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
BUCKET = 'lab-ai-tour-inputs'
JOB = 'lab-ai-tour-support-call'
CONFIDENCE_THRESHOLD = 90.0

REVIEWS_KEY = 'reviews/reviews.json'
PHOTO_KEY = 'images/product-photo.png'
RECEIPT_KEY = 'documents/receipt.png'
CALL_KEY = 'audio/support-call.wav'

# Sample reviews, with the language each one is written in.
REVIEWS = [
    {'id': 'r1', 'language': 'en', 'text': 'Fast delivery and the jacket fits perfectly. Love it!'},
    {'id': 'r2', 'language': 'en', 'text': 'The zipper broke after two days. I want a refund. Email me at jane@example.com.'},
    {'id': 'r3', 'language': 'es', 'text': 'El pedido llegó roto y nadie responde mis correos.'},
    {'id': 'r4', 'language': 'fr', 'text': 'Très bonne qualité, je recommande.'},
    {'id': 'r5', 'language': 'en', 'text': 'Not bad, but not great either.'},
]

# A keyword rule, the kind of baseline you would try before reaching for ML.
NEGATIVE_WORDS = ('broke', 'refund', 'roto', 'nadie', 'terrible', 'awful')
POSITIVE_WORDS = ('love', 'perfect', 'great', 'bonne', 'recommande', 'fast')

# Business problems, the service that fits each, and two where AI is the wrong tool.
USE_CASES = [
    ('Find unhappy customers in thousands of product reviews', 'Amazon Comprehend', 'sentiment and key phrases'),
    ('Remove email addresses and phone numbers from support tickets', 'Amazon Comprehend', 'PII detection'),
    ('Show reviews in the shopper\'s language', 'Amazon Translate', 'neural machine translation'),
    ('Flag unsafe images before they appear on a product page', 'Amazon Rekognition', 'content moderation'),
    ('Pull totals and line items out of scanned receipts', 'Amazon Textract', 'forms and tables'),
    ('Search what customers said on recorded support calls', 'Amazon Transcribe', 'speech to text'),
    ('Read order updates aloud in a phone menu', 'Amazon Polly', 'text to speech'),
    ('Answer "where is my order?" in a chat window', 'Amazon Lex', 'conversational bot'),
    ('Recommend products from browsing history', 'Amazon Personalize', 'recommendations'),
    ('Search internal policy documents in plain English', 'Amazon Kendra', 'enterprise search'),
    ('Predict demand for a product line from its own sales history', 'Amazon SageMaker AI', 'a custom model'),
    ('Calculate sales tax on an order', 'No AI: a rule', 'tax rules are exact and published, so a lookup is cheaper and always right'),
    ('Decide a loan when every decision must be explained to a regulator', 'No AI, or an explainable model',
     'an opaque model cannot give the reason; use rules or an interpretable model with human review'),
]




def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A valid, solid-color PNG; Rekognition and Textract check the image format."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b'\x00' + bytes(rgb) * width
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(row * height)) + chunk(b'IEND', b''))


PHOTO_PNG = png(32, 24, (40, 90, 160))
RECEIPT_PNG = png(24, 48, (250, 250, 245))

# One cheap read per service, to see which ones Floci answers.
SERVICE_PROBES = [
    ('Amazon Comprehend', 'comprehend', 'detect_dominant_language', {'Text': 'hello'}),
    ('Amazon Translate', 'translate', 'list_languages', {}),
    ('Amazon Rekognition', 'rekognition', 'detect_labels', {'Image': {'Bytes': PHOTO_PNG}}),
    ('Amazon Textract', 'textract', 'detect_document_text', {'Document': {'Bytes': RECEIPT_PNG}}),
    ('Amazon Transcribe', 'transcribe', 'list_transcription_jobs', {}),
    ('Amazon Polly', 'polly', 'describe_voices', {}),
    ('Amazon Lex', 'lexv2-models', 'list_bots', {}),
    ('Amazon Personalize', 'personalize', 'list_datasets', {}),
    ('Amazon Kendra', 'kendra', 'list_indices', {}),
    ('Amazon SageMaker AI', 'sagemaker', 'list_endpoints', {}),
]

CHOOSER_COMMAND = (
    '# Match each business problem to an AWS AI service, or to no AI at all\n'
    'aws comprehend detect-dominant-language --text hello\n'
    'aws polly describe-voices   # one cheap call per service to see what Floci answers'
)
STAGE_COMMAND = (
    f'aws s3 mb s3://{BUCKET}\n'
    f'aws s3 cp reviews.json s3://{BUCKET}/{REVIEWS_KEY}\n'
    f'aws s3 cp product-photo.png s3://{BUCKET}/{PHOTO_KEY}\n'
    f'aws s3 cp receipt.png s3://{BUCKET}/{RECEIPT_KEY}\n'
    f'aws s3 cp support-call.wav s3://{BUCKET}/{CALL_KEY}'
)
COMPREHEND_COMMAND = (
    'aws comprehend detect-dominant-language --text "<review>"\n'
    'aws comprehend detect-sentiment --language-code en --text "<review>"\n'
    'aws comprehend detect-key-phrases --language-code en --text "<review>"\n'
    'aws comprehend detect-pii-entities --language-code en --text "<review>"'
)
TRANSLATE_COMMAND = (
    'aws translate translate-text --source-language-code es --target-language-code en --text "<review>"\n'
    'aws translate translate-text --source-language-code fr --target-language-code en --text "<review>"\n'
    'aws translate translate-text --source-language-code en --target-language-code xx --text "test"   # not a language'
)
REKOGNITION_COMMAND = (
    f'aws rekognition detect-labels --image "S3Object={{Bucket={BUCKET},Name={PHOTO_KEY}}}" --max-labels 10 '
    '--min-confidence 70\n'
    f'aws rekognition detect-moderation-labels --image "S3Object={{Bucket={BUCKET},Name={PHOTO_KEY}}}"\n'
    f'aws rekognition detect-text --image "S3Object={{Bucket={BUCKET},Name={PHOTO_KEY}}}"'
)
TEXTRACT_COMMAND = (
    f'aws textract detect-document-text --document "S3Object={{Bucket={BUCKET},Name={RECEIPT_KEY}}}"\n'
    f'aws textract analyze-document --document "S3Object={{Bucket={BUCKET},Name={RECEIPT_KEY}}}" '
    '--feature-types FORMS TABLES\n'
    f'aws textract analyze-expense --document "S3Object={{Bucket={BUCKET},Name={RECEIPT_KEY}}}"'
)
TRANSCRIBE_COMMAND = (
    f'aws transcribe start-transcription-job --transcription-job-name {JOB} --language-code en-US '
    f'--media MediaFileUri=s3://{BUCKET}/{CALL_KEY} --output-bucket-name {BUCKET}\n'
    f'aws transcribe get-transcription-job --transcription-job-name {JOB}'
)
REVIEW_COMMAND = (
    f'# Send any prediction under {CONFIDENCE_THRESHOLD:g}% confidence to a person (Amazon Augmented AI does this '
    'with a human review workflow)'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Tour the AWS AI services: match use cases to Comprehend, Translate, Rekognition, Textract, and Transcribe',
    'description': (
        'Match business problems to the right AWS AI service (or to no AI at all), then send real requests to '
        'Comprehend, Translate, Rekognition, Textract and Transcribe with sample reviews, a product photo, a receipt '
        'and a call recording. Compare a managed service with a keyword rule, and route low-confidence predictions '
        'to a person.'
    ),
    'steps': [
        {
            'key': 'use-case-chooser',
            'title': 'Match business problems to AI services, or to no AI',
            'command': CHOOSER_COMMAND,
            'explanation': (
                'AWS offers ready-made AI services for common jobs, so you call an API instead of training a model: '
                'Comprehend for text, Translate for languages, Rekognition for images, Textract for documents, '
                'Transcribe and Polly for speech, Lex for chatbots, Personalize for recommendations, Kendra for '
                'search. SageMaker AI is for custom models when nothing ready-made fits. Some problems need no AI: '
                'when the answer must be exact or explained, a rule is cheaper and safer. How Floci emulates this: '
                'the step calls each service once; Comprehend, Translate, Rekognition, Textract, Transcribe and '
                'SageMaker answer, while Polly, Lex, Personalize and Kendra are not emulated.'
            ),
        },
        {
            'key': 'stage-inputs',
            'title': 'Upload sample reviews, a photo, a receipt and a call recording to S3',
            'command': STAGE_COMMAND,
            'explanation': (
                'AI services read their inputs from S3 or from the request itself. Keeping inputs in one bucket lets '
                'each service use the same data, and lets you control access with one bucket policy. How Floci '
                'emulates this: the bucket and objects are real; the images are 1x1 PNGs and the recording is one '
                'second of silence, which is enough for Floci.'
            ),
        },
        {
            'key': 'comprehend-reviews',
            'title': 'Find language, sentiment, key phrases and PII in reviews with Comprehend',
            'command': COMPREHEND_COMMAND,
            'explanation': (
                'Comprehend is natural language processing as an API: it detects the language, the sentiment with a '
                'score for each class, key phrases, entities and personal information (PII), with no model to train. '
                'A keyword rule is the baseline to beat: it is cheap and explainable but misses sarcasm, other '
                'languages and words it was never given. How Floci emulates this: every text comes back as English '
                'and NEUTRAL (0.7) with no PII, and DetectEntities is not emulated, so the step compares the shape of '
                'the answers with the keyword rule and says what AWS would find.'
            ),
        },
        {
            'key': 'translate-reviews',
            'title': 'Translate Spanish and French reviews into English',
            'command': TRANSLATE_COMMAND,
            'explanation': (
                'Translate is neural machine translation: one call per text, with a source language you name or let '
                'it detect (auto). Custom terminology keeps brand names untranslated. How Floci emulates this: '
                'Translate returns the text unchanged and auto detection always says English, but it does reject '
                'language codes that do not exist, as AWS does.'
            ),
        },
        {
            'key': 'rekognition-images',
            'title': 'Label and moderate a product photo with Rekognition',
            'command': REKOGNITION_COMMAND,
            'explanation': (
                'Rekognition is computer vision as an API: labels for objects and scenes with a confidence for each, '
                'moderation labels for unsafe content, text in images, and faces. MinConfidence drops weak labels. '
                'How Floci emulates this: Rekognition checks that the S3 object exists and is an image, then returns '
                'one "Floci" label at 99.9% and no moderation labels.'
            ),
        },
        {
            'key': 'textract-receipt',
            'title': 'Extract text and fields from a receipt with Textract',
            'command': TEXTRACT_COMMAND,
            'explanation': (
                'Textract goes beyond OCR: DetectDocumentText finds lines and words, AnalyzeDocument also returns '
                'form fields (key and value pairs) and tables, and AnalyzeExpense understands receipts and invoices. '
                'How Floci emulates this: both calls return one "Floci" word with its bounding box; AnalyzeExpense '
                'is not emulated.'
            ),
        },
        {
            'key': 'transcribe-call',
            'title': 'Turn a support call into text with Transcribe',
            'command': TRANSCRIBE_COMMAND,
            'explanation': (
                'Transcribe is automatic speech recognition: a job reads audio from S3 and writes a transcript with '
                'timestamps and a confidence per word, and Call Analytics adds sentiment and issues for contact '
                'centers. Jobs are asynchronous, so you poll or react to an EventBridge event. How Floci emulates '
                'this: the job is stored and reports COMPLETED at once, but no transcript file is written.'
            ),
        },
        {
            'key': 'human-review',
            'title': 'Send low-confidence predictions to a person',
            'command': REVIEW_COMMAND,
            'explanation': (
                f'Every prediction comes with a confidence. Acting on it automatically above a threshold (here '
                f'{CONFIDENCE_THRESHOLD:g}%) and sending the rest to a person is how you use AI where mistakes are '
                'costly; Amazon Augmented AI (A2I) runs those human review workflows for Rekognition, Textract and '
                'custom models. How Floci emulates this: A2I is not emulated, so the step applies the threshold to '
                'the predictions the earlier steps collected.'
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


def _strip(response: dict[str, Any]) -> dict[str, Any]:
    response = dict(response)
    response.pop('ResponseMetadata', None)
    return response


# ── Pure logic ───────────────────────────────────────────────────────────────

def silent_wav(seconds: float = 1.0, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x00\x00' * int(rate * seconds))
    return buffer.getvalue()


def keyword_sentiment(text: str) -> str:
    lowered = text.lower()
    negative = sum(word in lowered for word in NEGATIVE_WORDS)
    positive = sum(word in lowered for word in POSITIVE_WORDS)
    if negative > positive:
        return 'NEGATIVE'
    if positive > negative:
        return 'POSITIVE'
    return 'NEUTRAL'


def available(outcome: Any) -> bool:
    return outcome == 'answers'


def route(predictions: list[dict[str, Any]], threshold: float = CONFIDENCE_THRESHOLD) -> dict[str, list[dict[str, Any]]]:
    """Split predictions (each with a Confidence from 0 to 100) into automatic and human review."""
    automatic = [item for item in predictions if float(item.get('Confidence') or 0) >= threshold]
    review = [item for item in predictions if float(item.get('Confidence') or 0) < threshold]
    return {'Automatic': automatic, 'HumanReview': review}


def _predictions_from_markers() -> list[dict[str, Any]]:
    predictions = []
    for item in (_marked('comprehend-reviews') or {}).get('sentiments', []):
        predictions.append({'Source': 'Comprehend sentiment', 'Item': item['id'], 'Prediction': item['sentiment'],
                            'Confidence': item['confidence']})
    for item in (_marked('rekognition-images') or {}).get('labels', []):
        predictions.append({'Source': 'Rekognition label', 'Item': PHOTO_KEY, 'Prediction': item['Name'],
                            'Confidence': item['Confidence']})
    for item in (_marked('textract-receipt') or {}).get('words', []):
        predictions.append({'Source': 'Textract word', 'Item': RECEIPT_KEY, 'Prediction': item['Text'],
                            'Confidence': item['Confidence']})
    return predictions


# ── Lookups ──────────────────────────────────────────────────────────────────

def _objects() -> set[str]:
    try:
        contents = _client('s3').list_objects_v2(Bucket=BUCKET).get('Contents', [])
    except ClientError as exc:
        if _code(exc) in ('NoSuchBucket', '404'):
            return set()
        raise
    return {item['Key'] for item in contents}


def _job() -> dict[str, Any] | None:
    try:
        return _client('transcribe').get_transcription_job(TranscriptionJobName=JOB).get('TranscriptionJob')
    except ClientError as exc:
        if _code(exc) in ('NotFoundException', 'BadRequestException'):
            return None
        raise


def _probe(client_name: str, operation: str, params: dict[str, Any]) -> str:
    try:
        getattr(_client(client_name), operation)(**params)
        return 'answers'
    except ClientError as exc:
        code = _code(exc)
        if code in ('UnknownOperationException', 'InvalidAction', 'NotImplemented') or 'Unknown' in code:
            return 'not emulated'
        return f'error: {code}'
    except BotoCoreError as exc:
        return f'error: {type(exc).__name__}'


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_marker(step: str) -> tuple[bool, str]:
    marker = _marked(step)
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_chooser() -> tuple[bool, str]:
    return _check_marker('use-case-chooser')


def _check_inputs() -> tuple[bool, str]:
    missing = [key for key in (REVIEWS_KEY, PHOTO_KEY, RECEIPT_KEY, CALL_KEY) if key not in _objects()]
    if missing:
        return False, f'Upload {", ".join(missing)} to s3://{BUCKET}.'
    return True, f'The reviews, photo, receipt and recording are in s3://{BUCKET}.'


def _needs_inputs(step: str) -> tuple[bool, str]:
    ok, message = _check_inputs()
    if not ok:
        return ok, message
    return _check_marker(step)


def _check_comprehend() -> tuple[bool, str]:
    return _needs_inputs('comprehend-reviews')


def _check_translate() -> tuple[bool, str]:
    return _needs_inputs('translate-reviews')


def _check_rekognition() -> tuple[bool, str]:
    return _needs_inputs('rekognition-images')


def _check_textract() -> tuple[bool, str]:
    return _needs_inputs('textract-receipt')


def _check_transcribe() -> tuple[bool, str]:
    job = _job()
    if not job:
        return False, f'Start the {JOB} transcription job.'
    if job.get('TranscriptionJobStatus') != 'COMPLETED':
        return False, f'{JOB} is {job.get("TranscriptionJobStatus")}; wait for COMPLETED.'
    return _check_marker('transcribe-call')


def _check_review() -> tuple[bool, str]:
    return _check_marker('human-review')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_chooser() -> dict[str, Any]:
    started = time.perf_counter()
    status = {name: _probe(client_name, operation, params) for name, client_name, operation, params in SERVICE_PROBES}
    rows = [{'Problem': problem, 'Choose': choice, 'Why': why,
             'InFloci': status.get(choice, 'not an AWS service' if choice.startswith('No AI') else 'not probed')}
            for problem, choice, why in USE_CASES]
    answering = [name for name, outcome in status.items() if available(outcome)]
    missing = [name for name, outcome in status.items() if outcome == 'not emulated']
    no_ai = [row['Problem'] for row in rows if row['Choose'].startswith('No AI')]
    message = (f'{len(USE_CASES)} problems matched: {len(USE_CASES) - len(no_ai)} to an AI service and {len(no_ai)} to '
               f'a rule. Floci answers {len(answering)} of {len(status)} services'
               + (f'; {", ".join(name.replace("Amazon ", "") for name in missing)} are not emulated.' if missing else '.'))
    _marker('use-case-chooser', {'message': message})
    verified, check = _check_chooser()
    return _result('use-case-chooser', CHOOSER_COMMAND, {'UseCases': rows, 'ServicesInFloci': status}, verified,
                   check, started)


def step_stage() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)
    s3.put_object(Bucket=BUCKET, Key=REVIEWS_KEY, Body=json.dumps(REVIEWS, ensure_ascii=False, indent=2).encode(),
                  ContentType='application/json')
    s3.put_object(Bucket=BUCKET, Key=PHOTO_KEY, Body=PHOTO_PNG, ContentType='image/png')
    s3.put_object(Bucket=BUCKET, Key=RECEIPT_KEY, Body=RECEIPT_PNG, ContentType='image/png')
    s3.put_object(Bucket=BUCKET, Key=CALL_KEY, Body=silent_wav(), ContentType='audio/wav')
    contents = s3.list_objects_v2(Bucket=BUCKET).get('Contents', [])
    response = {'Bucket': BUCKET, 'Objects': [{'Key': item['Key'], 'Size': item['Size']} for item in contents]}
    verified, check = _check_inputs()
    return _result('stage-inputs', STAGE_COMMAND, response, verified, check, started)


def step_comprehend() -> dict[str, Any]:
    started = time.perf_counter()
    comprehend = _client('comprehend')
    rows, sentiments = [], []
    for review in REVIEWS:
        text = review['text']
        languages = comprehend.detect_dominant_language(Text=text).get('Languages', [])
        detected = languages[0]['LanguageCode'] if languages else None
        # Sentiment, key phrases and PII need a supported language code; English covers all three.
        code = review['language'] if review['language'] in ('en', 'es', 'fr') else 'en'
        sentiment = comprehend.detect_sentiment(Text=text, LanguageCode=code)
        phrases = comprehend.detect_key_phrases(Text=text, LanguageCode=code).get('KeyPhrases', [])
        pii = comprehend.detect_pii_entities(Text=text, LanguageCode='en').get('Entities', []) if code == 'en' else []
        label = sentiment.get('Sentiment')
        score = (sentiment.get('SentimentScore') or {}).get((label or '').capitalize(), 0)
        rows.append({'Review': review['id'], 'Text': text, 'WrittenIn': review['language'], 'DetectedLanguage': detected,
                     'Sentiment': label, 'Score': sentiment.get('SentimentScore'),
                     'KeyPhrases': [item.get('Text') for item in phrases], 'PII': [item.get('Type') for item in pii],
                     'KeywordRule': keyword_sentiment(text)})
        sentiments.append({'id': review['id'], 'sentiment': label, 'confidence': round(float(score) * 100, 1)})
    try:
        comprehend.detect_entities(Text=REVIEWS[1]['text'], LanguageCode='en')
        entities = 'answers'
    except ClientError as exc:
        entities = 'not emulated' if 'Unknown' in _code(exc) else _code(exc)
    wrong_language = [row['Review'] for row in rows if row['DetectedLanguage'] != row['WrittenIn']]
    disagree = [row['Review'] for row in rows if row['Sentiment'] != row['KeywordRule']]
    found_pii = sum(len(row['PII']) for row in rows)
    message = (f'Comprehend analyzed {len(rows)} reviews. Floci detected the right language for '
               f'{len(rows) - len(wrong_language)} of them, found {found_pii} PII entities (AWS would find the email in r2) and '
               f'disagreed with the keyword rule on {len(disagree)}. On AWS, r2 and r3 come back NEGATIVE and r1 and '
               'r4 POSITIVE, while the rule calls r5 ("not great") POSITIVE because it cannot read negation.')
    _marker('comprehend-reviews', {'message': message, 'sentiments': sentiments})
    response = {'Reviews': rows, 'DetectEntities': entities,
                'Notes': {'LanguageMismatches': wrong_language, 'DisagreesWithKeywordRule': disagree}}
    verified, check = _check_comprehend()
    return _result('comprehend-reviews', COMPREHEND_COMMAND, response, verified, check, started)


def step_translate() -> dict[str, Any]:
    started = time.perf_counter()
    translate = _client('translate')
    rows = []
    for review in REVIEWS:
        if review['language'] == 'en':
            continue
        result = translate.translate_text(Text=review['text'], SourceLanguageCode=review['language'],
                                          TargetLanguageCode='en')
        rows.append({'Review': review['id'], 'From': review['language'], 'Original': review['text'],
                     'English': result.get('TranslatedText'), 'Changed': result.get('TranslatedText') != review['text']})
    auto = translate.translate_text(Text=REVIEWS[2]['text'], SourceLanguageCode='auto', TargetLanguageCode='en')
    try:
        translate.translate_text(Text='test', SourceLanguageCode='en', TargetLanguageCode='xx')
        bad_code = 'accepted'
    except ClientError as exc:
        bad_code = _code(exc)
    changed = sum(row['Changed'] for row in rows)
    message = (f'Translate returned {len(rows)} reviews in English; Floci changed {changed} of them (it echoes the '
               f'text), auto detection said "{auto.get("SourceLanguageCode")}" for Spanish, and the made-up code xx '
               f'was rejected with {bad_code}.')
    _marker('translate-reviews', {'message': message})
    response = {'Translations': rows, 'AutoDetect': {'Text': REVIEWS[2]['text'], 'Detected': auto.get('SourceLanguageCode')},
                'UnknownLanguageCode': bad_code}
    verified, check = _check_translate()
    return _result('translate-reviews', TRANSLATE_COMMAND, response, verified, check, started)


def step_rekognition() -> dict[str, Any]:
    started = time.perf_counter()
    rekognition = _client('rekognition')
    image = {'S3Object': {'Bucket': BUCKET, 'Name': PHOTO_KEY}}
    labels = rekognition.detect_labels(Image=image, MaxLabels=10, MinConfidence=70).get('Labels', [])
    moderation = rekognition.detect_moderation_labels(Image=image).get('ModerationLabels', [])
    text = rekognition.detect_text(Image=image).get('TextDetections', [])
    try:
        rekognition.detect_labels(Image={'S3Object': {'Bucket': BUCKET, 'Name': 'images/missing.png'}})
        missing = 'accepted'
    except ClientError as exc:
        missing = _code(exc)
    publish = not moderation
    message = (f'Rekognition found {len(labels)} label(s) ({", ".join(item["Name"] for item in labels) or "none"}) and '
               f'{len(moderation)} moderation label(s), so the photo can be {"published" if publish else "held"}; '
               f'a missing object fails with {missing}.')
    _marker('rekognition-images', {'message': message,
                                   'labels': [{'Name': item['Name'], 'Confidence': item['Confidence']} for item in labels]})
    response = {'Labels': labels, 'ModerationLabels': moderation,
                'TextLines': [item.get('DetectedText') for item in text if item.get('Type') == 'LINE'],
                'MissingObject': missing, 'Publish': publish}
    verified, check = _check_rekognition()
    return _result('rekognition-images', REKOGNITION_COMMAND, response, verified, check, started)


def step_textract() -> dict[str, Any]:
    started = time.perf_counter()
    textract = _client('textract')
    document = {'S3Object': {'Bucket': BUCKET, 'Name': RECEIPT_KEY}}
    detected = textract.detect_document_text(Document=document).get('Blocks', [])
    analyzed = textract.analyze_document(Document=document, FeatureTypes=['FORMS', 'TABLES']).get('Blocks', [])
    try:
        textract.analyze_expense(Document=document)
        expense = 'answers'
    except ClientError as exc:
        expense = 'not emulated' if 'Unknown' in _code(exc) else _code(exc)
    words = [{'Text': item.get('Text'), 'Confidence': item.get('Confidence')} for item in detected
             if item.get('BlockType') == 'WORD']
    kinds: dict[str, int] = {}
    for block in analyzed:
        kinds[block.get('BlockType', '?')] = kinds.get(block.get('BlockType', '?'), 0) + 1
    message = (f'Textract read {len(words)} word(s) ({", ".join(str(item["Text"]) for item in words) or "none"}); '
               f'AnalyzeDocument returned {", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))} '
               f'and AnalyzeExpense is {expense}. On AWS a receipt also returns KEY_VALUE_SET and TABLE blocks.')
    _marker('textract-receipt', {'message': message, 'words': words})
    response = {'Words': words, 'AnalyzeDocumentBlockTypes': kinds, 'AnalyzeExpense': expense}
    verified, check = _check_textract()
    return _result('textract-receipt', TEXTRACT_COMMAND, response, verified, check, started)


def step_transcribe() -> dict[str, Any]:
    started = time.perf_counter()
    transcribe = _client('transcribe')
    if not _job():
        transcribe.start_transcription_job(TranscriptionJobName=JOB, LanguageCode='en-US', MediaFormat='wav',
                                           Media={'MediaFileUri': f's3://{BUCKET}/{CALL_KEY}'}, OutputBucketName=BUCKET)
    deadline = time.monotonic() + 60
    job = _job() or {}
    while job.get('TranscriptionJobStatus') in ('QUEUED', 'IN_PROGRESS') and time.monotonic() < deadline:
        time.sleep(2)
        job = _job() or {}
    uri = (job.get('Transcript') or {}).get('TranscriptFileUri')
    written = bool(uri) and f'{JOB}.json' in _objects()
    message = (f'{JOB} is {job.get("TranscriptionJobStatus")}. '
               + ('The transcript is in the bucket.' if written else
                  f'Floci points at {uri} but writes no transcript; on AWS it holds the words, timestamps and confidence.'))
    _marker('transcribe-call', {'message': message})
    response = {'TranscriptionJob': {key: job.get(key) for key in (
        'TranscriptionJobName', 'TranscriptionJobStatus', 'LanguageCode', 'MediaFormat', 'Media', 'Transcript')},
        'TranscriptWritten': written}
    verified, check = _check_transcribe()
    return _result('transcribe-call', TRANSCRIBE_COMMAND, response, verified, check, started)


def step_review() -> dict[str, Any]:
    started = time.perf_counter()
    predictions = _predictions_from_markers()
    if not predictions:
        return _result('human-review', REVIEW_COMMAND, {}, False,
                       'Run the Comprehend, Rekognition and Textract steps first.', started)
    routed = route(predictions)
    message = (f'{len(routed["Automatic"])} of {len(predictions)} predictions are at or above '
               f'{CONFIDENCE_THRESHOLD:g}% and go straight through; {len(routed["HumanReview"])} go to a person. '
               'Raising the threshold buys accuracy with reviewer time.')
    _marker('human-review', {'message': message})
    verified, check = _check_review()
    return _result('human-review', REVIEW_COMMAND, {'Threshold': CONFIDENCE_THRESHOLD, **routed}, verified, check,
                   started)


RUNNERS = {
    'use-case-chooser': step_chooser,
    'stage-inputs': step_stage,
    'comprehend-reviews': step_comprehend,
    'translate-reviews': step_translate,
    'rekognition-images': step_rekognition,
    'textract-receipt': step_textract,
    'transcribe-call': step_transcribe,
    'human-review': step_review,
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
    'use-case-chooser': _check_chooser,
    'stage-inputs': _check_inputs,
    'comprehend-reviews': _check_comprehend,
    'translate-reviews': _check_translate,
    'rekognition-images': _check_rekognition,
    'textract-receipt': _check_textract,
    'transcribe-call': _check_transcribe,
    'human-review': _check_review,
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
    """Delete the transcription job and the input bucket with everything in it."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code in ('NoSuchBucket', 'NoSuchKey', 'BadRequestException', '404'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    s3, transcribe = _client('s3'), _client('transcribe')
    try:
        job = _job()
    except (ClientError, BotoCoreError):
        job = None
    if job:
        attempt(f'transcription-job:{JOB}', lambda: transcribe.delete_transcription_job(TranscriptionJobName=JOB))
    try:
        keys = sorted(_objects())
    except (ClientError, BotoCoreError):
        keys = []
    for key in keys:
        attempt(f's3://{BUCKET}/{key}', lambda key=key: s3.delete_object(Bucket=BUCKET, Key=key))
    try:
        s3.head_bucket(Bucket=BUCKET)
        exists = True
    except ClientError:
        exists = False
    if exists:
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws transcribe delete-transcription-job --transcription-job-name {JOB}\n'
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
            else 'The transcription job and the input bucket are gone.',
        },
    }
