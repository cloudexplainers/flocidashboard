"""RAG knowledge base lab: Floci's S3 Vectors retrieval is real, while Bedrock answers and embeddings are stubs, so
the lab chunks, embeds, scores grounding and prices tokens itself. These tests cover that logic, the checks, and
registration."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.s3vectors_labs')


class ChunkTests(SimpleTestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(lab.chunk('a b c', size=10, overlap=2), ['a b c'])

    def test_chunks_overlap_and_cover_every_word(self):
        words = [str(index) for index in range(25)]
        chunks = lab.chunk(' '.join(words), size=10, overlap=3)
        self.assertEqual(chunks[0].split()[:3], ['0', '1', '2'])
        # The overlap repeats the last words of the previous chunk.
        self.assertEqual(chunks[0].split()[-3:], chunks[1].split()[:3])
        covered = {word for part in chunks for word in part.split()}
        self.assertEqual(covered, set(words))

    def test_corpus_chunks_carry_source_and_topic(self):
        rows = lab.corpus_chunks()
        self.assertGreaterEqual(len(rows), len(lab.DOCUMENTS))
        self.assertEqual({row['source'] for row in rows}, set(lab.DOCUMENTS))
        for row in rows:
            self.assertTrue(row['text'])
            self.assertEqual(row['key'].split('#')[0], row['source'])


class EmbeddingTests(SimpleTestCase):
    def test_embeddings_are_deterministic_unit_vectors(self):
        vector = lab.embed('refunds within 30 days')
        self.assertEqual(len(vector), lab.DIMENSION)
        self.assertAlmostEqual(sum(value * value for value in vector), 1.0, places=4)
        self.assertEqual(vector, lab.embed('refunds within 30 days'))

    def test_similar_text_is_closer_than_unrelated_text(self):
        def cosine(a, b):
            return sum(x * y for x, y in zip(a, b))

        query = lab.embed('how long do I have to return a jacket')
        returns = lab.embed(lab.DOCUMENTS['returns.md']['text'])
        shipping = lab.embed(lab.DOCUMENTS['shipping.md']['text'])
        self.assertGreater(cosine(query, returns), cosine(query, shipping))

    def test_empty_text_gives_a_zero_vector(self):
        self.assertEqual(lab.embed('!!!'), [0.0] * lab.DIMENSION)

    def test_token_count_follows_the_four_character_rule(self):
        self.assertEqual(lab.count_tokens('abcd'), 1)
        self.assertEqual(lab.count_tokens('a' * 401), 101)


class GroundingTests(SimpleTestCase):
    def setUp(self):
        self.chunks = [{'source': 'returns.md', 'text': lab.DOCUMENTS['returns.md']['text']}]

    def test_a_cited_answer_from_the_context_is_grounded(self):
        report = lab.grounding_report(lab.IDEAL_ANSWER, self.chunks)
        self.assertEqual(report['CitedSources'], ['returns.md'])
        self.assertGreaterEqual(report['WordsFromContext'], 0.6)
        self.assertTrue(report['Grounded'])

    def test_an_uncited_or_invented_answer_is_not_grounded(self):
        self.assertFalse(lab.grounding_report('Returns take 30 days and shipping is free.', self.chunks)['Grounded'])
        invented = lab.grounding_report('[returns.md] Telephone our Reykjavik warehouse before bicycling onwards.',
                                        self.chunks)
        self.assertFalse(invented['Grounded'])

    def test_prompt_carries_the_context_the_sources_and_the_question(self):
        prompt = lab.grounded_prompt('How long?', self.chunks)
        self.assertIn('[returns.md]', prompt['user'])
        self.assertIn('Question: How long?', prompt['user'])
        self.assertIn('only from the context', prompt['system'])


class CostTests(SimpleTestCase):
    def test_retrieving_chunks_is_cheaper_than_the_whole_corpus(self):
        rag = lab.cost(250, 60)
        stuffed = lab.cost(lab.WHOLE_CORPUS_TOKENS, 60)
        self.assertLess(rag['USDPerMonth'], stuffed['USDPerMonth'])
        self.assertAlmostEqual(rag['USDPerDay'] * 30, rag['USDPerMonth'], places=1)

    def test_output_tokens_cost_more_than_input_tokens(self):
        self.assertGreater(lab.cost(0, 1000)['USDPerCall'], lab.cost(1000, 0)['USDPerCall'])


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_index_check_wants_every_chunk_at_the_right_dimension(self):
        with patch.object(lab, '_index', return_value=None):
            self.assertFalse(lab._check_index()[0])
        wrong = {'dimension': 8, 'distanceMetric': 'cosine'}
        with patch.object(lab, '_index', return_value=wrong):
            self.assertFalse(lab._check_index()[0])
        right = {'dimension': lab.DIMENSION, 'distanceMetric': 'cosine'}
        with patch.object(lab, '_index', return_value=right), patch.object(lab, '_stored_keys', return_value=set()):
            ok, message = lab._check_index()
            self.assertFalse(ok)
            self.assertIn('not in the index', message)
        keys = {item['key'] for item in lab.corpus_chunks()}
        with patch.object(lab, '_index', return_value=right), patch.object(lab, '_stored_keys', return_value=keys):
            self.assertTrue(lab._check_index()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_batched(self):
        self.assertEqual([item['key'] for item in labs_for_service('s3vectors')], ['rag-knowledge-base'])
        tags = LAB_EXAM_TAGS[('s3vectors', 'rag-knowledge-base')]
        self.assertIn({'exam': 'AIF-C01', 'task': '3.1', 'primary': True}, tags)
        self.assertIn({'exam': 'AIF-C01', 'task': '2.3', 'primary': False}, tags)
        self.assertIn('s3vectors', [batch['service'] for batch in LAB_BATCH_ORDER])
        for key in ('genai-on-aws', 'fm-app-design'):
            card = next(card for card in CONCEPT_CARDS if card['key'] == key)
            self.assertIn(('s3vectors', 'rag-knowledge-base'), card['related_labs'])
            self.assertFalse(card.get('planned'))

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('s3vectors', 'rag-knowledge-base', 'nope')
