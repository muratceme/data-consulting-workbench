"""ML product packages: training data profiles per framework/platform and the drift strategy agent."""
import csv
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import ml  # noqa: E402

TRAIN = 'id,yas,gelir,sehir,churn\n' + ''.join(f'{i},{20 + i % 40},{1000 + i * 3},{"A" if i % 2 else "B"},{1 if i % 5 == 0 else 0}\n' for i in range(1, 61))


class Profiles(Base):
    def rows(self):
        with open(self.out / 'egitim_verisi_profili.csv', encoding='utf-8') as fh:
            return {r['ozellik']: r for r in csv.DictReader(fh)}

    @unittest.skipUnless(__import__('importlib').util.find_spec('sklearn'), 'scikit-learn kurulu değil')
    def test_sklearn_real(self):
        (self.root / 'egitim.csv').write_text(TRAIN, encoding='utf-8')
        self.run_op('sklearn-training-profile', input_file='egitim.csv', target='churn')
        self.assertEqual(self.read('ozet.json')['hedef_ozeti']['tur'], 'sınıflandırma')
        self.assertTrue(self.rows()['gelir']['hedefle_iliski'])

    def test_sklearn_with_fake_mi(self):
        (self.root / 'egitim.csv').write_text(TRAIN, encoding='utf-8')
        fake = type('fs', (), {'mutual_info_classif': staticmethod(lambda X, y, random_state: [0.99, 0.1, 0.05])})
        with patch.object(ml, 'driver', return_value=fake):
            self.run_op('sklearn-training-profile', input_file='egitim.csv', target='churn')
        self.assertEqual(self.rows()['id']['hedefle_iliski'], '0.99')
        self.assertIn('olası sızıntı', self.rows()['id']['uyari'])
        self.assertEqual(self.read('ozet.json')['hedef_ozeti']['dengesizlik'], 4.0)

    def test_pytorch_imagefolder(self):
        for cls, n in (('kedi', 3), ('kopek', 1)):
            d = self.root / 'train' / cls
            d.mkdir(parents=True)
            for i in range(n):
                (d / f'{i}.jpg').write_bytes(b'\xff\xd8' + b'0' * 10)
        (self.root / 'train/kopek/bos.png').write_bytes(b'')
        (self.root / 'train/kopek/notlar.txt').write_text('x')
        finder = lambda root: (sorted(p.name for p in Path(root).iterdir()), {})
        fake = type('datasets', (), {'ImageFolder': type('IF', (), {'find_classes': staticmethod(finder)})})
        with patch.object(ml, 'driver', return_value=fake):
            self.run_op('pytorch-training-profile', image_root=str(self.root / 'train'))
        rows = self.rows()
        self.assertEqual((rows['kedi']['satir'], rows['kopek']['bos']), ('3', '1'))
        self.assertIn('görüntü dışı dosya', rows['kopek']['uyari'])

    def test_mlflow_dataset_inputs(self):
        run = {'info': {'run_id': 'r1'}, 'data': {'metrics': [{'key': 'auc', 'value': 0.81}]},
               'inputs': {'dataset_inputs': [{'tags': [{'key': 'mlflow.data.context', 'value': 'training'}],
                                              'dataset': {'name': 'churn', 'digest': 'd1', 'source_type': 'delta', 'profile': json.dumps({'num_rows': 1000}),
                                                          'schema': json.dumps({'mlflow_colspec': [{'name': 'yas', 'type': 'long'}]})}}]}}
        self.server.add('POST', '/api/2.0/mlflow/runs/search', {'runs': [run]})
        self.run_op('mlflow-training-profile', tracking_uri='https://m.example', experiment_ids=['1'])
        self.assertEqual(self.rows()['yas']['satir'], '1000')

    def test_sagemaker_baseline(self):
        stats = {'dataset': {'item_count': 100}, 'features': [{'name': 'yas', 'inferred_type': 'Integral', 'numerical_statistics': {'common': {'num_present': 60, 'num_missing': 40},
                                                                                                                                'mean': 35, 'std_dev': 5, 'min': 18, 'max': 70}}]}
        self.server.add('GET', '/monitoring/statistics.json', json.dumps(stats).encode())
        self.run_op('sagemaker-training-profile', region='eu-central-1', statistics_s3_uri='s3://my-bucket/monitoring/statistics.json')
        self.assertIn('yüksek eksik', self.rows()['yas']['uyari'])
        self.assertIn('/s3/aws4_request', self.server.received[0][3]['Authorization'])

    def test_azureml_datastore_asset(self):
        ws = '/subscriptions/s/resourceGroups/rg/providers/Microsoft.MachineLearningServices/workspaces/w'
        self.server.add('GET', ws + r'/data/churn/versions/2\?api-version=2024-04-01', {'properties': {'dataUri': 'azureml://datastores/blob1/paths/data/train.csv'}})
        self.server.add('GET', ws + r'/datastores/blob1\?api-version=2024-04-01', {'properties': {'accountName': 'acct', 'containerName': 'c'}})
        self.server.add('GET', '/c/data/train.csv', TRAIN.encode())
        self.run_op('azure-ml-training-profile', subscription_id='s', resource_group='rg', workspace='w', data_name='churn', data_version='2', target='churn')
        self.assertEqual(self.read('ozet.json')['hedef'], 'churn')
        self.assertEqual(self.server.received[2][3]['X-Ms-Version'], '2023-11-03')

    def test_datarobot_eda(self):
        self.server.add('GET', r'/api/v2/datasets/ds1/allFeaturesDetails/\?limit=100', {'data': [{'name': 'gelir', 'featureType': 'Numeric', 'uniqueCount': 50, 'naCount': 0,
                                                                                                  'targetLeakage': 'HIGH_RISK'}], 'next': None})
        self.run_op('datarobot-training-profile', dataset_id='ds1')
        self.assertIn('hedef sızıntısı', self.rows()['gelir']['uyari'])

    def test_vertex_bigquery_profile_query(self):
        self.server.add('GET', '/v1/projects/my-project/locations/us-central1/datasets/123', {'displayName': 'churn',
                                                                                            'metadata': {'inputConfig': {'bigquerySource': {'uri': 'bq://my-project.ml.churn'}}}})
        self.server.add('GET', '/bigquery/v2/projects/my-project/datasets/ml/tables/churn', {'schema': {'fields': [{'name': 'yas', 'type': 'INTEGER'}, {'name': 'sehir', 'type': 'STRING'}]}})
        fake = FakeSql({'profile': ([], [{'n': 10, 'm0': 0, 'd0': 8, 'lo0': 18, 'hi0': 70, 'av0': 40, 'sd0': 9, 'm1': 5, 'd1': 2}])})
        with patch.object(ml, 'BigQuerySql', return_value=fake):
            self.run_op('vertex-ai-training-profile', project_id='my-project', location='us-central1', dataset_id='123')
        self.assertIn('APPROX_COUNT_DISTINCT(`sehir`)', fake.queries[0][1])
        self.assertIn('yüksek eksik', self.rows()['sehir']['uyari'])


class Drift(Base):
    def test_vertex_monitoring_config_from_plan(self):
        self.server.add('GET', '/v1/projects/my-project/locations/us-central1/datasets/123', {'metadata': {'inputConfig': {'bigquerySource': {'uri': 'bq://my-project.ml.churn'}}}})
        self.server.add('GET', '/bigquery/v2/projects/my-project/datasets/ml/tables/churn', {'schema': {'fields': [{'name': 'sehir', 'type': 'STRING'}]}})
        fake = FakeSql({'profile': ([], [{'n': 10, 'm0': 0, 'd0': 2}])})
        bad = {'degerlendirme_metrikleri': [{'ad': 'auc', 'esik': 0.8, 'yon': 'buyuk_iyi'}], 'drift_izleme': [{'ozellik': 'gelir', 'yontem': 'psi', 'esik': 0.2}], 'yeniden_egitim': {}}
        good = {'degerlendirme_metrikleri': [{'ad': 'auc', 'esik': 0.8, 'yon': 'buyuk_iyi'}], 'drift_izleme': [{'ozellik': 'SEHIR', 'yontem': 'jensen_shannon', 'esik': 0.1}],
                'yeniden_egitim': {'tetikleyiciler': ['drift'], 'siklik': 'aylık'}}
        with patch.object(ml, 'BigQuerySql', return_value=fake), model_reply(bad, good):
            self.run_op('vertex-ai-drift-strategy', project_id='my-project', location='us-central1', dataset_id='123')
        conf = json.loads(self.read('vertex_izleme_ayari.json')) if isinstance(self.read('vertex_izleme_ayari.json'), str) else self.read('vertex_izleme_ayari.json')
        self.assertEqual(conf['objectiveConfigs'][0]['predictionDriftDetectionConfig']['driftThresholds'], {'sehir': {'value': 0.1}})
