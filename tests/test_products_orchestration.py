"""Orchestration product packages: run histories per orchestrator and the root-cause agent."""
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


def iso(hours):
    return (NOW - dt.timedelta(hours=hours)).isoformat()


class Metrics(Base):
    def metrics(self):
        with open(self.out / 'pipeline_metrikleri.csv', encoding='utf-8') as fh:
            return {r['pipeline']: r for r in csv.DictReader(fh)}

    def test_dagster_graphql_runs(self):
        runs = {'data': {'runsOrError': {'__typename': 'Runs', 'results': [
            {'runId': 'r1', 'jobName': 'etl', 'status': 'SUCCESS', 'startTime': 100.0, 'endTime': 160.0},
            {'runId': 'r2', 'jobName': 'etl', 'status': 'FAILURE', 'startTime': 200.0, 'endTime': 230.0}]}}}
        self.server.add('POST', '/prod/graphql', runs)
        with patch.dict('os.environ', {'DAGSTER_CLOUD_API_TOKEN': 'dt'}):
            self.run_op('dagster-pipeline-metrics', server='https://o.dagster.cloud/prod')
        m = self.metrics()['etl']
        self.assertEqual((m['calisma'], m['basarili'], m['basari_orani'], m['max_sn']), ('2', '1', '0.5', '60.0'))
        self.assertEqual(self.server.received[0][3]['Dagster-Cloud-Api-Token'], 'dt')

    def test_prefect_runs_with_flow_names(self):
        self.server.add('POST', '/api/accounts/a/workspaces/w/flow_runs/filter', [{'id': 'f1', 'flow_id': 'fl', 'state_type': 'COMPLETED', 'total_run_time': 12.5},
                                                                                 {'id': 'f2', 'flow_id': 'fl', 'state_type': 'CRASHED', 'total_run_time': 3}])
        self.server.add('POST', '/api/accounts/a/workspaces/w/flows/filter', [{'id': 'fl', 'name': 'yukleme'}])
        self.run_op('prefect-pipeline-metrics', api_url='https://api.prefect.cloud/api/accounts/a/workspaces/w')
        self.assertEqual(self.metrics()['yukleme']['basarisiz'], '1')

    def test_github_and_jenkins(self):
        self.server.add('GET', r'/repos/o/r/actions/runs\?created=%3E%3D.*', {'workflow_runs': [
            {'id': 1, 'name': 'CI', 'status': 'completed', 'conclusion': 'success', 'run_started_at': iso(2), 'updated_at': iso(1.5)}]})
        self.run_op('github-actions-pipeline-metrics', repository='o/r')
        self.assertEqual(self.metrics()['CI']['p50_sn'], '1800.0')
        self.out = self.root / 'j'
        ts = int((NOW - dt.timedelta(hours=1)).timestamp() * 1000)
        self.server.add('GET', r'/job/folder/job/build/api/json\?tree=.*', {'builds': [{'number': 5, 'result': 'FAILURE', 'duration': 4000, 'timestamp': ts},
                                                                               {'number': 4, 'result': 'SUCCESS', 'duration': 2000, 'timestamp': 0}]})
        self.run_op('jenkins-pipeline-metrics', server='https://j.example', jobs=['folder/build'])
        self.assertEqual(self.metrics()['folder/build']['calisma'], '1')

    def test_kestra_and_astro(self):
        self.server.add('GET', r'/api/v1/main/executions/search\?.*', {'results': [{'id': 'e1', 'namespace': 'prod', 'flowId': 'load',
                                                                                   'state': {'current': 'SUCCESS', 'startDate': iso(3), 'endDate': iso(2.9)}}]})
        self.run_op('kestra-pipeline-metrics', server='https://k.example', tenant='main')
        self.assertIn('prod.load', self.metrics())
        self.out = self.root / 'astro'
        self.server.add('GET', r'/d1/api/v2/dags/~/dagRuns\?run_after_gte=.*', {'dag_runs': [{'dag_id': 'x', 'dag_run_id': 'm1', 'state': 'failed', 'start_date': iso(2), 'end_date': iso(1)}],
                                                                              'total_entries': 1})
        self.run_op('astro-pipeline-metrics', deployment_url='https://o.astronomer.run/d1')
        self.assertEqual(self.server.received[-1][3]['Authorization'], 'Bearer test-only-secret')

    def test_no_runs_fails(self):
        self.server.add('POST', '/prod/graphql', {'data': {'runsOrError': {'results': []}}})
        with self.assertRaises(native.NativeError):
            self.run_op('dagster-pipeline-metrics', server='https://o.dagster.cloud/prod')


class RootCause(Base):
    def test_airflow_failed_tasks_logs_and_validation(self):
        self.server.add('GET', r'/api/v2/dags/~/dagRuns\?run_after_gte=.*', {'dag_runs': [{'dag_id': 'd', 'dag_run_id': 'run_1', 'state': 'failed'}], 'total_entries': 1})
        self.server.add('GET', r'/api/v2/dags/~/dagRuns/~/taskInstances\?state=failed.*', {'task_instances': [{'dag_id': 'd', 'dag_run_id': 'run_1', 'task_id': 'load', 'try_number': 2}],
                                                                                           'total_entries': 1})
        self.server.add('GET', r'/api/v2/dags/d/dagRuns/run_1/taskInstances/load/logs/2\?full_content=false',
                        {'content': [{'event': 'psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "pk"'}]})
        bad = {'olaylar': [{'calisma_id': 'run_9', 'kategori': 'veri', 'kok_neden': 'x', 'duzeltme': 'y', 'oncelik': 'orta'}]}
        good = {'olaylar': [{'calisma_id': 'run_1', 'pipeline': 'd', 'adim': 'load', 'kategori': 'veri', 'kok_neden': 'tekrarlı anahtar', 'kanit': 'UniqueViolation',
                             'duzeltme': 'MERGE kullan', 'onleme': 'unique testi', 'oncelik': 'yuksek'}]}
        with model_reply(bad, good):
            self.run_op('airflow-root-cause', server='https://af.example')
        self.assertIn('UniqueViolation', json.dumps(self.read('kanit.json')))
        self.assertIn('run_1', self.read('kok_neden.csv'))

    def test_github_annotations_and_jenkins_console(self):
        self.server.add('GET', r'/repos/o/r/actions/runs\?.*', {'workflow_runs': [{'id': 7, 'name': 'CI', 'status': 'completed', 'conclusion': 'failure', 'run_started_at': iso(2), 'updated_at': iso(1)}]})
        self.server.add('GET', r'/repos/o/r/actions/runs/7/jobs\?per_page=50', {'jobs': [{'id': 70, 'name': 'test', 'conclusion': 'failure', 'steps': [{'name': 'pytest', 'conclusion': 'failure'}]}]})
        self.server.add('GET', r'/repos/o/r/check-runs/70/annotations\?per_page=30', [{'annotation_level': 'failure', 'message': 'AssertionError in test_x'}])
        reply = {'olaylar': [{'calisma_id': '7', 'kategori': 'kod', 'kok_neden': 'test', 'duzeltme': 'düzelt', 'oncelik': 'orta'}]}
        with model_reply(reply):
            self.run_op('github-actions-root-cause', repository='o/r')
        self.assertIn('AssertionError', json.dumps(self.read('kanit.json')))
        self.out = self.root / 'j'
        ts = int((NOW - dt.timedelta(hours=1)).timestamp() * 1000)
        self.server.add('GET', r'/job/build/api/json\?tree=.*', {'builds': [{'number': 5, 'result': 'FAILURE', 'duration': 4000, 'timestamp': ts}]})
        self.server.add('GET', '/job/build/5/consoleText', b'line1\nERROR: disk full\nFinished: FAILURE\n')
        with model_reply({'olaylar': [{'calisma_id': 'build#5', 'kategori': 'altyapi', 'kok_neden': 'disk', 'duzeltme': 'temizle', 'oncelik': 'yuksek'}]}):
            self.run_op('jenkins-root-cause', server='https://j.example', jobs=['build'])
        self.assertIn('disk full', json.dumps(self.read('kanit.json')))

    def test_no_failures_means_no_agent(self):
        self.server.add('POST', '/prod/graphql', {'data': {'runsOrError': {'results': [{'runId': 'r', 'jobName': 'j', 'status': 'SUCCESS'}]}}})
        with self.assertRaises(native.NativeError):
            self.run_op('dagster-root-cause', server='https://o.dagster.cloud/prod')
