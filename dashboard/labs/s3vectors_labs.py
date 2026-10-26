"""Retrieval augmented generation (RAG) lab: turn a handful of policy documents into a searchable knowledge base and
ground a foundation model's answer in them. Upload the documents to S3, split them into overlapping chunks, embed the
chunks and store them in an S3 Vectors index with metadata, retrieve the nearest chunks for a question (with and
without a metadata filter), build a grounded prompt and send it with the Converse API, check the answer for citations,
ask something the documents do not cover, and price the design by tokens.

Floci 2.3.0 runs S3 Vectors for real: cosine ranking and metadata filters are genuine, so retrieval, the part this
lab is about, really works. Bedrock Runtime is a stub (`Floci stub response for model=<id>`) and so is InvokeModel for
embedding models, so the lab embeds chunks itself with a small, deterministic bag-of-words vectorizer and scores the
answer itself. Bedrock Knowledge Bases, which do the chunking, embedding and retrieval for you on AWS, are not
emulated, so the lab builds each piece by hand, which is also the clearest way to see what a knowledge base does.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 's3vectors'
LAB_KEY = 'rag-knowledge-base'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
BUCKET = 'lab-ai-rag-documents'
VECTOR_BUCKET = 'lab-ai-rag-vectors'
INDEX = 'policies'
DIMENSION = 64
CHUNK_WORDS, CHUNK_OVERLAP = 40, 10
TOP_K = 3
DISTANCE_LIMIT = 0.5   # cosine distance past which the lab treats retrieval as a miss
EMBED_MODEL = 'amazon.titan-embed-text-v2:0'
CHAT_MODEL = 'anthropic.claude-3-5-sonnet-20241022-v2:0'

QUESTION = 'How long do I have to return a jacket, and who pays the return shipping?'
FILTERED_QUESTION = 'What does the warranty cover?'
UNANSWERABLE = 'Which courier carries the store\'s parcels in Japan, and what is its claims phone number?'

# Three short policy documents, the kind of content a support assistant should answer from.
DOCUMENTS = {
    'returns.md': {
        'topic': 'returns',
        'text': (
            'Returns and refunds policy. Customers may return any unworn item within 30 days of delivery for a full '
            'refund to the original payment method. Jackets and other outerwear follow the same 30 day window. The '
            'store pays return shipping when the item arrived damaged or was the wrong item; otherwise the customer '
            'pays a flat 7 dollar return shipping fee, deducted from the refund. Refunds are issued within 5 business '
            'days of the warehouse receiving the item. Items marked final sale cannot be returned, but they can be '
            'exchanged for store credit within 14 days.'
        ),
    },
    'warranty.md': {
        'topic': 'warranty',
        'text': (
            'Warranty policy. Every jacket carries a two year warranty against manufacturing defects such as failed '
            'zippers, split seams and delaminated waterproof membranes. The warranty does not cover normal wear, '
            'tears from accidents, or damage from washing against the care label. A warranty claim needs the order '
            'number and photographs of the defect; the store repairs the item when it can and replaces it when it '
            'cannot. Warranty repairs are free, and the store pays shipping both ways on an approved claim.'
        ),
    },
    'shipping.md': {
        'topic': 'shipping',
        'text': (
            'Shipping policy. Standard shipping takes 3 to 5 business days and is free on orders over 75 dollars. '
            'Express shipping costs 15 dollars and arrives the next business day when ordered before 2 pm. Orders '
            'ship from the warehouse within one business day; the store emails a tracking number as soon as the '
            'parcel leaves. International orders take 7 to 14 business days, and the customer pays any customs duty.'
        ),
    },
}

# Prices per 1,000 tokens for a mid-size model, used to price the design; Floci serves no price list for Bedrock.
INPUT_PRICE_PER_1K = 0.003
OUTPUT_PRICE_PER_1K = 0.015
QUESTIONS_PER_DAY = 2000
WHOLE_CORPUS_TOKENS = 1800  # roughly what stuffing every document into every prompt would cost

STAGE_COMMAND = (
    f'aws s3 mb s3://{BUCKET}\n'
    + '\n'.join(f'aws s3 cp {name} s3://{BUCKET}/{name}' for name in DOCUMENTS)
)
CHUNK_COMMAND = (
    f'# Split each document into {CHUNK_WORDS}-word chunks with a {CHUNK_OVERLAP}-word overlap\n'
    f'aws bedrock-runtime invoke-model --model-id {EMBED_MODEL} --body \'{{"inputText":"<chunk>"}}\' out.json'
)
INDEX_COMMAND = (
    f'aws s3vectors create-vector-bucket --vector-bucket-name {VECTOR_BUCKET}\n'
    f'aws s3vectors create-index --vector-bucket-name {VECTOR_BUCKET} --index-name {INDEX} --data-type float32 '
    f'--dimension {DIMENSION} --distance-metric cosine\n'
    f'aws s3vectors put-vectors --vector-bucket-name {VECTOR_BUCKET} --index-name {INDEX} --vectors '
    '\'[{"key":"returns.md#0","data":{"float32":[...]},"metadata":{"topic":"returns","source":"returns.md",'
    '"text":"..."}}]\''
)
RETRIEVE_COMMAND = (
    f'aws s3vectors query-vectors --vector-bucket-name {VECTOR_BUCKET} --index-name {INDEX} --top-k {TOP_K} '
    '--query-vector \'{"float32":[...]}\' --return-distance --return-metadata\n'
    f'aws s3vectors query-vectors ... --filter \'{{"topic":{{"$eq":"warranty"}}}}\'   # metadata filter'
)
GROUND_COMMAND = (
    f'aws bedrock-runtime converse --model-id {CHAT_MODEL} \\\n'
    '  --system \'[{"text":"Answer only from the context. Cite each source. Say you do not know if the context '
    'does not answer."}]\' \\\n'
    '  --messages \'[{"role":"user","content":[{"text":"Context:\\n<chunks>\\n\\nQuestion: <question>"}]}]\' \\\n'
    '  --inference-config \'{"temperature":0,"maxTokens":400}\''
)
GROUNDED_CHECK_COMMAND = '# Check the answer: does every claim appear in the retrieved chunks, and are sources cited?'
REFUSE_COMMAND = f'# Ask something the documents do not answer: "{UNANSWERABLE}"'
COST_COMMAND = (
    '# Price the design: retrieved chunks vs the whole corpus in every prompt, at '
    f'${INPUT_PRICE_PER_1K:g} per 1K input and ${OUTPUT_PRICE_PER_1K:g} per 1K output tokens'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Build a RAG knowledge base with S3 Vectors and ground a Bedrock answer in it',
    'description': (
        'Turn policy documents into a searchable knowledge base and ground a foundation model in it: upload the '
        'documents, chunk and embed them, store the vectors in an S3 Vectors index with metadata, retrieve the '
        'nearest chunks for a question with and without a metadata filter, build a grounded prompt for the Converse '
        'API, check the answer for citations, ask something the documents do not cover, and price the design by '
        'tokens.'
    ),
    'steps': [
        {
            'key': 'stage-documents',
            'title': 'Upload the policy documents to S3',
            'command': STAGE_COMMAND,
            'explanation': (
                'A foundation model knows nothing about your business and its training data has a cutoff. Retrieval '
                'augmented generation (RAG) fixes both by looking up your own documents at question time and putting '
                'them in the prompt, with no training involved. The documents live in S3, which is where Bedrock '
                'Knowledge Bases read them from too. How Floci emulates this: the bucket and documents are real.'
            ),
        },
        {
            'key': 'chunk-and-embed',
            'title': 'Split the documents into chunks and embed them',
            'command': CHUNK_COMMAND,
            'explanation': (
                'Retrieval works on chunks, not whole documents: a chunk is small enough to be about one thing and '
                'to fit in a prompt, and an overlap keeps a sentence from being cut in half. An embedding model turns '
                'each chunk into a vector whose direction captures its meaning, so similar text ends up close '
                'together. Chunk size, overlap and the embedding model are the main knobs in a RAG design. How Floci '
                f'emulates this: InvokeModel on {EMBED_MODEL} returns the stub text instead of a vector, so the lab '
                f'embeds each chunk itself with a deterministic {DIMENSION}-dimension bag-of-words vectorizer. The '
                'retrieval that follows is real.'
            ),
        },
        {
            'key': 'build-index',
            'title': 'Store the vectors in an S3 Vectors index with metadata',
            'command': INDEX_COMMAND,
            'explanation': (
                'A vector store keeps the vectors and finds the nearest ones to a query. S3 Vectors is the '
                'low-cost option in S3 itself; OpenSearch Serverless, Aurora PostgreSQL with pgvector, Neptune '
                'Analytics, Kendra and MemoryDB are the alternatives. An index fixes the dimension and the distance '
                'metric (cosine here), and each vector carries metadata you can filter on and the chunk text you '
                'will put in the prompt. How Floci emulates this: S3 Vectors is real, including the dimension check.'
            ),
        },
        {
            'key': 'retrieve-chunks',
            'title': 'Retrieve the nearest chunks, with and without a metadata filter',
            'command': RETRIEVE_COMMAND,
            'explanation': (
                'Retrieval embeds the question with the same model and asks the index for the top K nearest chunks. '
                'K trades recall for prompt size and cost. A metadata filter narrows the search before ranking, '
                'which is how you scope answers to one product, one tenant or one language. How Floci emulates this: '
                'cosine ranking and the filter operators are real, and the step checks that the right chunks come '
                'back first.'
            ),
        },
        {
            'key': 'ground-the-answer',
            'title': 'Build a grounded prompt and send it with the Converse API',
            'command': GROUND_COMMAND,
            'explanation': (
                'The prompt carries the retrieved chunks as context, a system prompt that tells the model to answer '
                'only from that context and to cite its sources, and the question. Converse is the one API for every '
                'Bedrock model, with the same shape for messages, system prompts and inference settings. Temperature '
                '0 keeps a factual answer from wandering. How Floci emulates this: Converse accepts all of it and '
                'answers with the stub text and fixed token counts, so the step shows the exact prompt that AWS '
                'would answer and measures its size.'
            ),
        },
        {
            'key': 'check-grounding',
            'title': 'Check the answer against the retrieved context',
            'command': GROUNDED_CHECK_COMMAND,
            'explanation': (
                'A model can state something fluently and wrongly; that is a hallucination. The defenses are '
                'grounding (answer only from retrieved text), citations so a reader can check, and an automated '
                'check that every claim appears in the context. On AWS, Bedrock Guardrails can also score contextual '
                'grounding and block an ungrounded answer. How Floci emulates this: the stub answer cites nothing, '
                'so the check fails on purpose and the step scores a correct, cited answer beside it to show the '
                'difference.'
            ),
        },
        {
            'key': 'handle-unanswerable',
            'title': 'Ask a question the documents do not answer',
            'command': REFUSE_COMMAND,
            'explanation': (
                'A good RAG system says "I do not know" when retrieval comes back weak, rather than inventing an '
                'answer. A distance threshold on the best match is the usual gate: if nothing is close enough, '
                'answer from a fallback or hand the question to a person. How Floci emulates this: retrieval is '
                'real, so the distances are real; the step applies the threshold itself.'
            ),
        },
        {
            'key': 'price-the-design',
            'title': 'Price the design by tokens',
            'command': COST_COMMAND,
            'explanation': (
                'Bedrock bills per 1,000 input and output tokens, and output costs several times more than input. '
                'That makes prompt size a cost decision: retrieving a few chunks is far cheaper than stuffing every '
                'document into every prompt, and it usually answers better. Provisioned throughput and batch '
                'inference change the rates for steady or offline work. How Floci emulates this: Floci returns fixed '
                'token counts and no prices, so the step counts tokens from the real prompt and prices them at '
                'typical list rates.'
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


# ── Pure logic: chunking, embedding, scoring ─────────────────────────────────

def chunk(text: str, size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [' '.join(words)]
    chunks, start = [], 0
    while start < len(words):
        chunks.append(' '.join(words[start:start + size]))
        if start + size >= len(words):
            break
        start += size - overlap
    return chunks


def tokens(text: str) -> list[str]:
    return re.findall(r'[a-z0-9]+', text.lower())


def embed(text: str, dimension: int = DIMENSION) -> list[float]:
    """A deterministic bag-of-words embedding: hash each word into a bucket, then normalize. Real embedding models
    learn meaning; this only matches words, which is enough to show retrieval working."""
    vector = [0.0] * dimension
    for word in tokens(text):
        digest = hashlib.sha256(word.encode()).digest()
        bucket = int.from_bytes(digest[:4], 'big') % dimension
        sign = 1.0 if digest[4] % 2 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    return [round(value / norm, 6) for value in vector] if norm else vector


def count_tokens(text: str) -> int:
    """About four characters per token, the usual rule of thumb for English."""
    return max(1, math.ceil(len(text) / 4))


def corpus_chunks() -> list[dict[str, Any]]:
    rows = []
    for name, document in DOCUMENTS.items():
        for index, part in enumerate(chunk(document['text'])):
            rows.append({'key': f'{name}#{index}', 'source': name, 'topic': document['topic'], 'text': part})
    return rows


def grounded_prompt(question: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    context = '\n\n'.join(f'[{item["source"]}] {item["text"]}' for item in chunks)
    return {
        'system': ('Answer only from the context below. Cite the source in square brackets after each claim. If the '
                   'context does not answer the question, say that you do not know.'),
        'user': f'Context:\n{context}\n\nQuestion: {question}',
    }


def grounding_report(answer: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Score an answer against its context: are the sources cited, and do its words come from the context?"""
    context_words = set()
    for item in chunks:
        context_words |= set(tokens(item['text']))
    answer_words = [word for word in tokens(answer) if len(word) > 4]
    supported = [word for word in answer_words if word in context_words]
    cited = [item['source'] for item in chunks if f'[{item["source"]}]' in answer]
    share = round(len(supported) / len(answer_words), 2) if answer_words else 0.0
    return {'CitedSources': sorted(set(cited)), 'WordsFromContext': share,
             'Grounded': bool(cited) and share >= 0.6}


def cost(input_tokens: int, output_tokens: int, questions: int = QUESTIONS_PER_DAY) -> dict[str, float]:
    per_call = input_tokens / 1000 * INPUT_PRICE_PER_1K + output_tokens / 1000 * OUTPUT_PRICE_PER_1K
    return {'InputTokens': input_tokens, 'OutputTokens': output_tokens, 'USDPerCall': round(per_call, 6),
            'USDPerDay': round(per_call * questions, 2), 'USDPerMonth': round(per_call * questions * 30, 2)}


# A correct, cited answer, for comparison with the stub Floci returns.
IDEAL_ANSWER = (
    'You have 30 days from delivery to return an unworn jacket for a full refund [returns.md]. The store pays return '
    'shipping if the jacket arrived damaged or was the wrong item; otherwise a flat 7 dollar return shipping fee is '
    'deducted from your refund [returns.md].'
)


# ── Lookups ──────────────────────────────────────────────────────────────────

def _objects() -> set[str]:
    try:
        contents = _client('s3').list_objects_v2(Bucket=BUCKET).get('Contents', [])
    except ClientError as exc:
        if _code(exc) in ('NoSuchBucket', '404'):
            return set()
        raise
    return {item['Key'] for item in contents}


def _index() -> dict[str, Any] | None:
    try:
        return _client('s3vectors').get_index(vectorBucketName=VECTOR_BUCKET, indexName=INDEX).get('index')
    except ClientError as exc:
        if _code(exc) in ('NotFoundException', 'ResourceNotFoundException', 'ValidationException', 'NoSuchBucket'):
            return None
        raise


def _stored_keys() -> set[str]:
    try:
        vectors = _client('s3vectors').list_vectors(vectorBucketName=VECTOR_BUCKET, indexName=INDEX).get('vectors', [])
    except ClientError:
        return set()
    return {item['key'] for item in vectors}


def _query(question: str, top_k: int = TOP_K, metadata_filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {'vectorBucketName': VECTOR_BUCKET, 'indexName': INDEX,
                              'queryVector': {'float32': embed(question)}, 'topK': top_k,
                              'returnDistance': True, 'returnMetadata': True}
    if metadata_filter:
        params['filter'] = metadata_filter
    hits = _client('s3vectors').query_vectors(**params).get('vectors', [])
    return [{'key': item['key'], 'distance': round(item.get('distance', 0), 4),
             'source': (item.get('metadata') or {}).get('source'),
             'topic': (item.get('metadata') or {}).get('topic'),
             'text': (item.get('metadata') or {}).get('text', '')} for item in hits]


def _converse(prompt: dict[str, Any]) -> dict[str, Any]:
    return _client('bedrock-runtime').converse(
        modelId=CHAT_MODEL, system=[{'text': prompt['system']}],
        messages=[{'role': 'user', 'content': [{'text': prompt['user']}]}],
        inferenceConfig={'temperature': 0, 'maxTokens': 400})


def _answer_text(response: dict[str, Any]) -> str:
    content = ((response.get('output') or {}).get('message') or {}).get('content') or []
    return ' '.join(item.get('text', '') for item in content)


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_marker(step: str) -> tuple[bool, str]:
    marker = _marked(step)
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_documents() -> tuple[bool, str]:
    missing = [name for name in DOCUMENTS if name not in _objects()]
    if missing:
        return False, f'Upload {", ".join(missing)} to s3://{BUCKET}.'
    return True, f'{len(DOCUMENTS)} policy documents are in s3://{BUCKET}.'


def _check_chunks() -> tuple[bool, str]:
    ok, message = _check_documents()
    if not ok:
        return ok, message
    return _check_marker('chunk-and-embed')


def _check_index() -> tuple[bool, str]:
    index = _index()
    if not index:
        return False, f'Create the {INDEX} index in the {VECTOR_BUCKET} vector bucket.'
    if index.get('dimension') != DIMENSION or index.get('distanceMetric') != 'cosine':
        return False, f'{INDEX} should be {DIMENSION}-dimension cosine.'
    expected = {item['key'] for item in corpus_chunks()}
    missing = expected - _stored_keys()
    if missing:
        return False, f'{len(missing)} of {len(expected)} chunks are not in the index yet.'
    return True, f'All {len(expected)} chunks are in the {INDEX} index.'


def _needs_index(step: str) -> tuple[bool, str]:
    ok, message = _check_index()
    if not ok:
        return ok, message
    return _check_marker(step)


def _check_retrieve() -> tuple[bool, str]:
    return _needs_index('retrieve-chunks')


def _check_ground() -> tuple[bool, str]:
    return _needs_index('ground-the-answer')


def _check_grounding() -> tuple[bool, str]:
    return _needs_index('check-grounding')


def _check_unanswerable() -> tuple[bool, str]:
    return _needs_index('handle-unanswerable')


def _check_cost() -> tuple[bool, str]:
    return _check_marker('price-the-design')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_stage() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)
    for name, document in DOCUMENTS.items():
        s3.put_object(Bucket=BUCKET, Key=name, Body=document['text'].encode(), ContentType='text/markdown')
    response = {'Bucket': BUCKET, 'Documents': [
        {'Key': name, 'Topic': document['topic'], 'Words': len(document['text'].split())}
        for name, document in DOCUMENTS.items()]}
    verified, check = _check_documents()
    return _result('stage-documents', STAGE_COMMAND, response, verified, check, started)


def step_chunk() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_documents()
    if not ok:
        return _result('chunk-and-embed', CHUNK_COMMAND, {}, False, message, started)
    rows = corpus_chunks()
    bedrock = _client('bedrock-runtime')
    try:
        raw = bedrock.invoke_model(modelId=EMBED_MODEL, body=json.dumps({'inputText': rows[0]['text']}))
        payload = json.loads(raw['body'].read())
        real = isinstance(payload.get('embedding'), list)
        outcome = 'returned an embedding' if real else f'returned {json.dumps(payload)[:80]}'
    except ClientError as exc:
        outcome = f'failed with {_code(exc)}'
    first = embed(rows[0]['text'])
    message = (f'{len(DOCUMENTS)} documents became {len(rows)} chunks of up to {CHUNK_WORDS} words with a '
               f'{CHUNK_OVERLAP}-word overlap. Floci\'s {EMBED_MODEL} {outcome}, so the lab embedded each chunk into '
               f'{DIMENSION} dimensions itself.')
    _marker('chunk-and-embed', {'message': message})
    response = {'Chunks': [{'Key': item['key'], 'Source': item['source'], 'Topic': item['topic'],
                            'Words': len(item['text'].split()), 'Tokens': count_tokens(item['text']),
                            'Text': item['text']} for item in rows],
                'EmbeddingModelInFloci': outcome,
                'ExampleVector': {'Key': rows[0]['key'], 'Dimension': DIMENSION, 'First8': first[:8]}}
    verified, check = _check_chunks()
    return _result('chunk-and-embed', CHUNK_COMMAND, response, verified, check, started)


def step_index() -> dict[str, Any]:
    started = time.perf_counter()
    vectors = _client('s3vectors')
    try:
        vectors.create_vector_bucket(vectorBucketName=VECTOR_BUCKET)
    except ClientError as exc:
        if _code(exc) not in ('ConflictException', 'BucketAlreadyExists', 'BucketAlreadyOwnedByYou'):
            raise
    index = _index()
    if index and (index.get('dimension') != DIMENSION or index.get('distanceMetric') != 'cosine'):
        vectors.delete_index(vectorBucketName=VECTOR_BUCKET, indexName=INDEX)
        index = None
    if not index:
        vectors.create_index(vectorBucketName=VECTOR_BUCKET, indexName=INDEX, dataType='float32',
                             dimension=DIMENSION, distanceMetric='cosine')
    rows = corpus_chunks()
    vectors.put_vectors(vectorBucketName=VECTOR_BUCKET, indexName=INDEX, vectors=[
        {'key': item['key'], 'data': {'float32': embed(item['text'])},
         'metadata': {'source': item['source'], 'topic': item['topic'], 'text': item['text']}} for item in rows])
    try:
        vectors.put_vectors(vectorBucketName=VECTOR_BUCKET, indexName=INDEX,
                            vectors=[{'key': 'wrong-size', 'data': {'float32': [1.0, 0.0]}}])
        mismatch = 'accepted'
    except ClientError as exc:
        mismatch = _code(exc)
    response = {'Index': _index(), 'VectorsStored': len(_stored_keys()),
                'WrongDimension': mismatch,
                'Metadata': ['source', 'topic', 'text']}
    verified, check = _check_index()
    return _result('build-index', INDEX_COMMAND, response, verified, check, started)


def step_retrieve() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_index()
    if not ok:
        return _result('retrieve-chunks', RETRIEVE_COMMAND, {}, False, message, started)
    hits = _query(QUESTION)
    unfiltered = _query(FILTERED_QUESTION, top_k=6)
    filtered = _query(FILTERED_QUESTION, metadata_filter={'topic': {'$eq': 'warranty'}})
    on_topic = hits and hits[0]['topic'] == 'returns'
    narrowed = all(item['topic'] == 'warranty' for item in filtered) and bool(filtered)
    message = (f'The top {len(hits)} chunks for the returns question come from '
               f'{", ".join(sorted({str(item["source"]) for item in hits}))}, nearest first at distance '
               f'{hits[0]["distance"]:g}. Filtering the warranty question to topic=warranty leaves '
               f'{len(filtered)} of the {len(unfiltered)} nearest chunks, all from the warranty policy.')
    if on_topic and narrowed:
        _marker('retrieve-chunks', {'message': message, 'hits': hits})
    response = {'Question': QUESTION, 'TopChunks': hits,
                'FilterDemo': {'Question': FILTERED_QUESTION, 'UnfilteredTop6': unfiltered,
                               'Filter': {'topic': {'$eq': 'warranty'}}, 'Filtered': filtered}}
    verified, check = _check_retrieve()
    if not on_topic:
        verified, check = False, 'The nearest chunk did not come from the returns policy.'
    return _result('retrieve-chunks', RETRIEVE_COMMAND, response, verified, check, started)


def step_ground() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_index()
    if not ok:
        return _result('ground-the-answer', GROUND_COMMAND, {}, False, message, started)
    hits = _query(QUESTION)
    prompt = grounded_prompt(QUESTION, hits)
    response = _converse(prompt)
    answer = _answer_text(response)
    prompt_tokens = count_tokens(prompt['system']) + count_tokens(prompt['user'])
    usage = response.get('usage') or {}
    message = (f'The grounded prompt carries {len(hits)} chunks, about {prompt_tokens} tokens in all, with a system '
               f'prompt that allows only the context. Floci answered "{answer[:60]}" and reported '
               f'{usage.get("inputTokens")} input tokens whatever the prompt; AWS would answer from the context and '
               'count the real tokens.')
    _marker('ground-the-answer', {'message': message, 'prompt_tokens': prompt_tokens,
                                  'output_tokens': usage.get('outputTokens') or 0})
    payload = {'Prompt': prompt, 'PromptTokensByTheLab': prompt_tokens, 'Answer': answer,
               'Usage': usage, 'StopReason': response.get('stopReason')}
    verified, check = _check_ground()
    return _result('ground-the-answer', GROUND_COMMAND, payload, verified, check, started)


def step_check_grounding() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_index()
    if not ok:
        return _result('check-grounding', GROUNDED_CHECK_COMMAND, {}, False, message, started)
    hits = _query(QUESTION)
    answer = _answer_text(_converse(grounded_prompt(QUESTION, hits)))
    live = grounding_report(answer, hits)
    ideal = grounding_report(IDEAL_ANSWER, hits)
    message = (f'Floci\'s answer cites {len(live["CitedSources"])} sources and takes '
               f'{live["WordsFromContext"]:.0%} of its words from the context, so the grounding check fails, as it '
               f'should for an answer that ignores the context. A correct answer cites '
               f'{", ".join(ideal["CitedSources"])} and scores {ideal["WordsFromContext"]:.0%}.')
    _marker('check-grounding', {'message': message})
    response = {'FlociAnswer': {'Text': answer, **live},
                'CorrectAnswer': {'Text': IDEAL_ANSWER, **ideal},
                'Rule': 'Grounded when at least one source is cited and 60% of the longer words come from the context'}
    verified, check = _check_grounding()
    if ideal['Grounded'] is not True:
        verified, check = False, 'The grounding check rejected a correct, cited answer; the rule is wrong.'
    return _result('check-grounding', GROUNDED_CHECK_COMMAND, response, verified, check, started)


def step_unanswerable() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_index()
    if not ok:
        return _result('handle-unanswerable', REFUSE_COMMAND, {}, False, message, started)
    answerable = _query(QUESTION, top_k=1)
    unanswerable = _query(UNANSWERABLE, top_k=TOP_K)
    best_known = answerable[0]['distance'] if answerable else 1.0
    best_unknown = unanswerable[0]['distance'] if unanswerable else 1.0
    threshold = DISTANCE_LIMIT
    refuse = best_unknown > threshold >= best_known
    message = (f'The best match for a question the policies answer is {best_known:g} away; for one they do not, '
               f'{best_unknown:g}. With the lab\'s threshold of {threshold:g} the system '
               + ('refuses the unanswerable question and hands it to a person.' if refuse
                  else 'would still answer, which is how a RAG system hallucinates.'))
    if refuse:
        _marker('handle-unanswerable', {'message': message})
    response = {'Answerable': {'Question': QUESTION, 'BestDistance': best_known},
                'Unanswerable': {'Question': UNANSWERABLE, 'BestDistance': best_unknown, 'Chunks': unanswerable},
                'Threshold': threshold, 'Refused': refuse,
                'Rule': f'Answer only when the nearest chunk is within {DISTANCE_LIMIT:g} cosine distance',
                'Fallback': 'Say you do not know and offer a support handoff'}
    verified, check = _check_unanswerable()
    if not refuse:
        verified, check = False, 'Retrieval found the unanswerable question as close as a real one.'
    return _result('handle-unanswerable', REFUSE_COMMAND, response, verified, check, started)


def step_cost() -> dict[str, Any]:
    started = time.perf_counter()
    marker = _marked('ground-the-answer')
    if not marker:
        return _result('price-the-design', COST_COMMAND, {}, False, 'Send the grounded prompt first.', started)
    output_tokens = count_tokens(IDEAL_ANSWER)
    rag = cost(marker['prompt_tokens'], output_tokens)
    stuffed = cost(WHOLE_CORPUS_TOKENS + count_tokens(QUESTION), output_tokens)
    saving = round((stuffed['USDPerMonth'] - rag['USDPerMonth']) / stuffed['USDPerMonth'] * 100, 1)
    message = (f'At {QUESTIONS_PER_DAY:,} questions a day, retrieving {TOP_K} chunks costs about '
               f'${rag["USDPerMonth"]:,.2f} a month against ${stuffed["USDPerMonth"]:,.2f} for putting every document '
               f'in every prompt: {saving:g}% less, and no training cost either way.')
    _marker('price-the-design', {'message': message})
    response = {'PricesPer1KTokens': {'Input': INPUT_PRICE_PER_1K, 'Output': OUTPUT_PRICE_PER_1K},
                'QuestionsPerDay': QUESTIONS_PER_DAY, 'RetrievedChunks': rag, 'WholeCorpusInPrompt': stuffed,
                'SavingPercent': saving,
                'Alternatives': {'Fine-tuning': 'teaches style and format, not fresh facts, and costs training time',
                                 'Provisioned throughput': 'fixed hourly price for steady, high-volume traffic',
                                 'A smaller model': 'cheaper per token; check quality on your own questions'}}
    verified, check = _check_cost()
    return _result('price-the-design', COST_COMMAND, response, verified, check, started)


RUNNERS = {
    'stage-documents': step_stage,
    'chunk-and-embed': step_chunk,
    'build-index': step_index,
    'retrieve-chunks': step_retrieve,
    'ground-the-answer': step_ground,
    'check-grounding': step_check_grounding,
    'handle-unanswerable': step_unanswerable,
    'price-the-design': step_cost,
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
    'stage-documents': _check_documents,
    'chunk-and-embed': _check_chunks,
    'build-index': _check_index,
    'retrieve-chunks': _check_retrieve,
    'ground-the-answer': _check_ground,
    'check-grounding': _check_grounding,
    'handle-unanswerable': _check_unanswerable,
    'price-the-design': _check_cost,
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
    """Delete the vector index and vector bucket, and the document bucket with everything in it."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code in ('NoSuchBucket', 'NoSuchKey', 'ValidationException', '404'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    vectors, s3 = _client('s3vectors'), _client('s3')
    try:
        index = _index()
    except (ClientError, BotoCoreError):
        index = None
    if index:
        attempt(f'vector-index:{INDEX}', lambda: vectors.delete_index(vectorBucketName=VECTOR_BUCKET, indexName=INDEX))
    attempt(f'vector-bucket:{VECTOR_BUCKET}', lambda: vectors.delete_vector_bucket(vectorBucketName=VECTOR_BUCKET))
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
            f'aws s3vectors delete-index --vector-bucket-name {VECTOR_BUCKET} --index-name {INDEX}\n'
            f'aws s3vectors delete-vector-bucket --vector-bucket-name {VECTOR_BUCKET}\n'
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
            else 'The vector index, vector bucket and document bucket are gone.',
        },
    }
