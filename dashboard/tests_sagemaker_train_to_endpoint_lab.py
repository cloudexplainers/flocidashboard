"""SageMaker AI lab: Floci runs the training and hosting containers for real, so these tests cover the dataset,
scoring and drift math, the checks, and registration."""

import importlib
import io
import json
import tarfile
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.sagemaker_labs')

FIT = {'w': 1.0, 'b': 0.0, 'mean_x': 0.0, 'std_x': 1.0, 'mean_y': 0.0, 'std_y': 1.0}


class DatasetTests(SimpleTestCase):
    def test_dataset_is_deterministic_and_roughly_linear(self):
        first, second = lab.dataset(), lab.dataset()
        self.assertEqual(first, second)
        self.assertEqual(len(first), lab.ROWS)
        for area, price in first:
            self.assertGreaterEqual(area, 600)
            self.assertLessEqual(area, 3200)
            self.assertLess(abs(price - (40000 + 150 * area)), 80000)

    def test_split_holds_back_a_validation_set(self):
        train, validation = lab.split(lab.dataset())
        self.assertEqual(len(train), 160)
        self.assertEqual(len(validation), 40)
        self.assertEqual(set(train) & set(validation), set())

    def test_csv_has_one_row_per_sale(self):
        rows = lab.to_csv([(900, 170000), (1200, 230000)]).decode().splitlines()
        self.assertEqual(rows, ['900,170000', '1200,230000'])


class ScoringTests(SimpleTestCase):
    def test_a_perfect_model_scores_r2_of_one(self):
        rows = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
        self.assertEqual(lab.metrics(FIT, rows), {'MAE': 0.0, 'RMSE': 0.0, 'R2': 1.0})

    def test_errors_show_up_in_every_metric(self):
        scores = lab.metrics(FIT, [(1.0, 2.0), (2.0, 4.0)])
        self.assertEqual(scores['MAE'], 1.5)
        self.assertGreater(scores['RMSE'], scores['MAE'])
        self.assertLess(scores['R2'], 0)

    def test_predict_undoes_the_standardization(self):
        model = {'w': 1.0, 'b': 0.0, 'mean_x': 100.0, 'std_x': 10.0, 'mean_y': 500.0, 'std_y': 50.0}
        self.assertAlmostEqual(lab.predict(model, 110), 550.0)

    def test_drift_compares_the_live_mean_with_training(self):
        training = [float(x) for x in range(100, 200)]
        self.assertFalse(lab.drift(training, [150.0, 151.0])['Drifted'])
        drifted = lab.drift(training, [400.0, 420.0])
        self.assertTrue(drifted['Drifted'])
        self.assertGreater(drifted['ShiftInStdDevs'], 1.0)

    def test_read_model_opens_the_artifact(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
            for name, body in (('model.json', json.dumps(FIT).encode()), ('inference.js', b'// server')):
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
        model, names = lab.read_model(buffer.getvalue())
        self.assertEqual(model, FIT)
        self.assertIn('inference.js', names)


class ScriptTests(SimpleTestCase):
    def test_training_script_creates_the_model_directory_itself(self):
        # Floci 2.3.0 does not create /opt/ml/model, so the script must.
        self.assertIn("os.makedirs(f'{base}/model', exist_ok=True)", lab.TRAIN_SCRIPT)
        self.assertIn('hyperparameters.json', lab.TRAIN_SCRIPT)

    def test_inference_server_answers_ping_and_invocations_and_stops_on_sigterm(self):
        for needle in ("req.url === '/ping'", "req.url === '/invocations'", 'listen(8080)',
                       "process.on('SIGTERM'", 'MODULE_NOT_FOUND'):
            self.assertIn(needle, lab.INFERENCE_JS)


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_data_check_names_what_is_missing(self):
        with patch.object(lab, '_objects', return_value={'train/train.csv'}):
            ok, message = lab._check_data()
            self.assertFalse(ok)
            self.assertIn('code/inference.js', message)

    def test_training_check_wants_a_completed_job_with_an_artifact(self):
        job = {'TrainingJobName': 'j', 'TrainingJobStatus': 'InProgress'}
        with patch.object(lab, '_latest_job', return_value=None):
            self.assertFalse(lab._check_train()[0])
        with patch.object(lab, '_latest_job', return_value=job):
            self.assertFalse(lab._check_train()[0])
        done = {**job, 'TrainingJobStatus': 'Completed',
                'ModelArtifacts': {'S3ModelArtifacts': f's3://{lab.BUCKET}/models/j/output/model.tar.gz'}}
        with patch.object(lab, '_latest_job', return_value=done), patch.object(lab, '_objects', return_value=set()):
            self.assertFalse(lab._check_train()[0])
        with patch.object(lab, '_latest_job', return_value=done), \
                patch.object(lab, '_objects', return_value={'models/j/output/model.tar.gz'}):
            self.assertTrue(lab._check_train()[0])

    def test_update_check_wants_the_endpoint_on_the_second_config(self):
        lab._marker('retrain-and-update', {'message': 'done'})
        with patch.object(lab, '_describe', return_value={'EndpointStatus': 'InService',
                                                          'EndpointConfigName': lab.CONFIG[1]}):
            ok, message = lab._check_update()
            self.assertFalse(ok)
            self.assertIn(lab.CONFIG[2], message)
        with patch.object(lab, '_describe', return_value={'EndpointStatus': 'InService',
                                                          'EndpointConfigName': lab.CONFIG[2]}):
            self.assertTrue(lab._check_update()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_batched(self):
        self.assertEqual([item['key'] for item in labs_for_service('sagemaker')], ['train-to-endpoint'])
        tags = LAB_EXAM_TAGS[('sagemaker', 'train-to-endpoint')]
        self.assertIn({'exam': 'AIF-C01', 'task': '1.3', 'primary': True}, tags)
        self.assertIn('sagemaker', [batch['service'] for batch in LAB_BATCH_ORDER])
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'ml-lifecycle')
        self.assertIn(('sagemaker', 'train-to-endpoint'), card['related_labs'])
        self.assertFalse(card.get('planned'))

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('sagemaker', 'train-to-endpoint', 'nope')
