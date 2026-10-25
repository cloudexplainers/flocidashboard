"""Audits for certification exam tags, concept cards, and the exams API."""

from django.test import SimpleTestCase
from django.urls import reverse

from .labs.exams import CONCEPT_CARDS, EXAM_GUIDES, LAB_EXAM_TAGS, exam_tracks
from .labs.registry import all_labs


def _task_ids(exam):
    return {task['id'] for domain in EXAM_GUIDES[exam]['domains'] for task in domain['tasks']}


class ExamGuideTests(SimpleTestCase):
    def test_domain_weights_sum_to_100(self):
        for code, guide in EXAM_GUIDES.items():
            with self.subTest(exam=code):
                self.assertEqual(sum(domain['weight'] for domain in guide['domains']), 100)

    def test_task_ids_belong_to_their_domain(self):
        for code, guide in EXAM_GUIDES.items():
            for domain in guide['domains']:
                for task in domain['tasks']:
                    with self.subTest(exam=code, task=task['id']):
                        self.assertEqual(task['id'].split('.')[0], domain['id'])


class LabExamTagTests(SimpleTestCase):
    def test_every_registered_lab_has_a_tag_entry(self):
        registered = {(lab['service'], lab['key']) for lab in all_labs()}
        self.assertEqual(registered, set(LAB_EXAM_TAGS))

    def test_every_tag_points_at_a_real_task(self):
        for lab, tags in LAB_EXAM_TAGS.items():
            for tag in tags:
                with self.subTest(lab=lab, tag=tag):
                    self.assertIn(tag['exam'], EXAM_GUIDES)
                    self.assertIn(tag['task'], _task_ids(tag['exam']))

    def test_each_lab_has_one_primary_task_per_exam(self):
        for lab, tags in LAB_EXAM_TAGS.items():
            for exam in {tag['exam'] for tag in tags}:
                with self.subTest(lab=lab, exam=exam):
                    primaries = [tag for tag in tags if tag['exam'] == exam and tag['primary']]
                    self.assertEqual(len(primaries), 1)

    def test_tags_are_not_repeated(self):
        for lab, tags in LAB_EXAM_TAGS.items():
            with self.subTest(lab=lab):
                pairs = [(tag['exam'], tag['task']) for tag in tags]
                self.assertEqual(len(pairs), len(set(pairs)))


class ConceptCardTests(SimpleTestCase):
    def test_cards_point_at_real_tasks_and_labs(self):
        registered = {(lab['service'], lab['key']) for lab in all_labs()}
        keys = [card['key'] for card in CONCEPT_CARDS]
        self.assertEqual(len(keys), len(set(keys)))
        for card in CONCEPT_CARDS:
            with self.subTest(card=card['key']):
                self.assertTrue(card['points'])
                for exam, task in card['exams']:
                    self.assertIn(task, _task_ids(exam))
                for lab in card['related_labs']:
                    self.assertIn(lab, registered)

    def test_every_task_statement_has_a_lab_or_concept_card(self):
        covered = {(tag['exam'], tag['task']) for tags in LAB_EXAM_TAGS.values() for tag in tags}
        covered |= {pair for card in CONCEPT_CARDS for pair in card['exams']}
        for code in EXAM_GUIDES:
            for task in sorted(_task_ids(code)):
                with self.subTest(exam=code, task=task):
                    self.assertIn((code, task), covered, f'{code} {task} has no lab and no concept card')


class ExamTracksApiTests(SimpleTestCase):
    def test_exam_tracks_attach_labs_and_cards_to_tasks(self):
        tracks = exam_tracks(all_labs())
        saa = next(exam for exam in tracks['exams'] if exam['code'] == 'SAA-C03')
        tasks = {task['id']: task for domain in saa['domains'] for task in domain['tasks']}

        loosely_coupled = {(lab['service'], lab['key']) for lab in tasks['2.1']['labs']}
        self.assertIn(('sns', 'sqs-fanout'), loosely_coupled)
        ingestion = {(lab['service'], lab['key']) for lab in tasks['3.5']['labs']}
        self.assertIn(('kinesis', 'streaming-data-lake'), ingestion)
        self.assertIn('data-ingestion', tasks['3.5']['concepts'])

    def test_developer_track_reuses_existing_labs(self):
        tracks = exam_tracks(all_labs())
        dva = next(exam for exam in tracks['exams'] if exam['code'] == 'DVA-C02')
        tasks = {task['id']: task for domain in dva['domains'] for task in domain['tasks']}
        self.assertEqual(len(tasks), 13)
        lambda_labs = {(lab['service'], lab['key']) for lab in tasks['1.2']['labs']}
        self.assertIn(('lambda', 'sqs-event-source'), lambda_labs)
        optimize = {(lab['service'], lab['key']) for lab in tasks['4.3']['labs']}
        self.assertIn(('sns', 'filter-policies'), optimize)
        self.assertIn('deployment-strategies', tasks['3.4']['concepts'])
        self.assertIn('tracing-xray', tasks['4.2']['concepts'])

    def test_exams_endpoint_and_catalog_include_tags(self):
        exams = self.client.get(reverse('dashboard:labs-exams')).json()
        self.assertEqual([exam['code'] for exam in exams['exams']], ['CLF-C02', 'SAA-C03', 'DVA-C02'])
        self.assertEqual(len(exams['concepts']), len(CONCEPT_CARDS))

        catalog = self.client.get(reverse('dashboard:labs-catalog')).json()
        lab = next(item for item in catalog['labs'] if item['service'] == 's3' and item['key'] == 'lifecycle-retention')
        self.assertIn({'exam': 'SAA-C03', 'task': '4.1', 'primary': True}, lab['exams'])


class PracticeQuizTests(SimpleTestCase):
    def test_quiz_questions_are_well_formed(self):
        from .labs.quizzes import QUIZZES

        for (exam, task), questions in QUIZZES.items():
            self.assertIn(task, _task_ids(exam))
            self.assertGreaterEqual(len(questions), 3, f'{exam} {task} needs at least 3 questions')
            texts = [question['question'] for question in questions]
            self.assertEqual(len(texts), len(set(texts)))
            for question in questions:
                with self.subTest(exam=exam, task=task, question=question['question'][:40]):
                    options = question['options']
                    self.assertGreaterEqual(len(options), 4)
                    self.assertEqual(len(options), len(set(options)))
                    self.assertTrue(question['answers'])
                    self.assertEqual(len(question['answers']), len(set(question['answers'])))
                    for answer in question['answers']:
                        self.assertIn(answer, range(len(options)))
                    self.assertTrue(question['explanation'])
                    if len(question['answers']) > 1:
                        self.assertIn('(Choose', question['question'])

    def test_every_cloud_practitioner_task_has_a_quiz(self):
        from .labs.quizzes import QUIZZES

        for task in sorted(_task_ids('CLF-C02')):
            with self.subTest(task=task):
                self.assertIn(('CLF-C02', task), QUIZZES)

    def test_exam_tracks_attach_quizzes(self):
        tracks = exam_tracks(all_labs())
        clf = next(exam for exam in tracks['exams'] if exam['code'] == 'CLF-C02')
        tasks = {task['id']: task for domain in clf['domains'] for task in domain['tasks']}
        self.assertGreaterEqual(len(tasks['4.3']['quiz']), 3)
        self.assertIn('answers', tasks['4.3']['quiz'][0])
