"""Streaming product packages: broker sampling without offset commits, engine metrics and the event architecture agent."""
import base64
import datetime as dt
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, model_reply  # noqa: E402
import native  # noqa: E402
from products import stream  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


def event(i, **extra):
    return json.dumps({'id': i, 'ts': (NOW - dt.timedelta(minutes=10 - i)).isoformat(), 'amount': 1.5, **extra}).encode()


class FakeMessage:
    def __init__(self, partition, offset, value, key=b'k'):
        self.p, self.o, self.v, self.k = partition, offset, value, key

    def partition(self): return self.p
    def offset(self): return self.o
    def value(self): return self.v
    def key(self): return self.k
    def error(self): return None
    def timestamp(self): return (1, int(NOW.timestamp() * 1000))


class FakeKafka:
    commits = []

    class TopicPartition:
        def __init__(self, topic, partition, offset=-1):
            self.topic, self.partition, self.offset = topic, partition, offset

    class Consumer:
        def __init__(self, conf):
            FakeKafka.conf = conf
            self.queue = [FakeMessage(0, 8, event(1)), FakeMessage(0, 9, event(1)), FakeMessage(1, 3, b'not json'), FakeMessage(1, 4, event(2, amount='x'))]

        def list_topics(self, topic, timeout):
            md = type('T', (), {'partitions': {0: None, 1: None}, 'error': None})()
            return type('M', (), {'topics': {topic: md}})()

        def get_watermark_offsets(self, tp, timeout):
            return (0, 10) if tp.partition == 0 else (3, 5)

        def assign(self, parts):
            FakeKafka.assigned = [(p.partition, p.offset) for p in parts]

        def poll(self, timeout):
            return self.queue.pop(0) if self.queue else None

        def commit(self, *a, **k):
            FakeKafka.commits.append(1)

        def close(self):
            FakeKafka.closed = True


class Quality(Base):
    def test_kafka_sampling_never_commits(self):
        with patch.object(stream, 'driver', return_value=FakeKafka):
            self.run_op('kafka-event-quality', bootstrap_servers='b:9093', topic='orders', messages_per_partition=2, id_field='id', event_time_field='ts',
                        required_fields=['amount'], max_seconds=5)
        self.assertEqual(FakeKafka.assigned, [(0, 8), (1, 3)])
        self.assertFalse(FakeKafka.conf['enable.auto.commit']); self.assertEqual(FakeKafka.commits, []); self.assertTrue(FakeKafka.closed)
        rows = self.read('kontrol_sonuclari.csv')
        for part in ('tekrarlanan olay kimliği,1', 'çözümlenemeyen mesaj (json değil),1', 'alan tipi tutarsız,1'):
            self.assertIn(part, rows)
        self.assertNotIn('1.5', self.read('alan_profili.csv'))

    def test_event_hubs_uses_connection_string_sasl(self):
        with patch.object(stream, 'driver', return_value=FakeKafka):
            self.run_op('event-hubs-event-quality', bootstrap_servers='ns.servicebus.windows.net:9093', topic='hub', max_seconds=5)
        self.assertEqual((FakeKafka.conf['sasl.username'], FakeKafka.conf['sasl.password']), ('$ConnectionString', 'test-only-secret'))

    def test_kinesis_shards(self):
        self.server.add('POST', '/', {'Shards': [{'ShardId': 's-1'}]})
        self.server.add('POST', '/', {'ShardIterator': 'it1'})
        self.server.add('POST', '/', {'Records': [{'SequenceNumber': '1', 'PartitionKey': 'a', 'ApproximateArrivalTimestamp': NOW.timestamp(), 'Data': base64.b64encode(event(1)).decode()}],
                                      'NextShardIterator': None})
        self.run_op('kinesis-event-quality', region='eu-central-1', stream_name='clicks')
        self.assertEqual(self.server.received[1][2]['ShardIteratorType'], 'AT_TIMESTAMP')
        self.assertEqual(self.read('ozet.json')['cozumlenen'], 1)

    def test_pubsub_nacks_instead_of_ack(self):
        sub = 'projects/my-project/subscriptions/quality-sub'
        self.server.add('POST', f'/v1/{sub}:pull', {'receivedMessages': [{'ackId': 'a1', 'message': {'messageId': 'm1', 'publishTime': NOW.isoformat(), 'data': base64.b64encode(event(1)).decode()}}]})
        self.server.add('POST', f'/v1/{sub}:modifyAckDeadline', {})
        self.server.add('POST', f'/v1/{sub}:pull', {})
        self.run_op('pubsub-event-quality', subscription=sub)
        self.assertEqual(self.server.received[1][2], {'ackIds': ['a1'], 'ackDeadlineSeconds': 0})
        self.assertFalse(any(':acknowledge' in r[1] for r in self.server.received))

    def test_flink_metrics(self):
        job = 'a' * 32
        self.server.add('GET', f'/jobs/{job}', {'name': 'orders', 'state': 'RUNNING', 'vertices': [{'id': 'v1', 'name': 'Window'}]})
        self.server.add('GET', rf'/jobs/{job}/vertices/v1/subtasks/metrics\?get=.*', [{'id': 'numRecordsIn', 'sum': 100}, {'id': 'numLateRecordsDropped', 'sum': 4}])
        self.server.add('GET', f'/jobs/{job}/vertices/v1/backpressure', {'backpressureLevel': 'high'})
        self.server.add('GET', f'/jobs/{job}/vertices/v1/watermarks', [{'id': '0.currentInputWatermark', 'value': str(int((NOW.timestamp() - 7200) * 1000))}])
        self.server.add('GET', f'/jobs/{job}/checkpoints', {'counts': {'failed': 2}})
        self.run_op('flink-event-quality', server='https://f.example', job_id=job, max_lag_seconds=3600)
        rows = self.read('kontrol_sonuclari.csv')
        for part in ('geç geldiği için atılan kayıt,4', 'yüksek backpressure,1', 'filigran gecikmesi eşik üstü', 'başarısız checkpoint,2'):
            self.assertIn(part, rows)

    def test_spark_progress_events(self):
        line = json.dumps({'Event': stream.SPARK_PROGRESS, 'progress': {'name': 'q', 'batchId': 3, 'numInputRows': 100, 'inputRowsPerSecond': 50, 'processedRowsPerSecond': 20,
                                                                        'durationMs': {'triggerExecution': 90000}, 'stateOperators': [{'numRowsDroppedByWatermark': 7}]}})
        (self.root / 'log').write_text('{"Event":"SparkListenerApplicationStart"}\n' + line + '\n')
        self.run_op('spark-streaming-event-quality', event_log_file='log', trigger_interval_seconds=60)
        rows = self.read('kontrol_sonuclari.csv')
        self.assertIn('filigran nedeniyle atılan satır,7', rows); self.assertIn('işlem hızı girdi hızının altında,1', rows); self.assertIn('batch süresi', rows)


class Architecture(Base):
    def test_avro_and_topic_config_from_validated_plan(self):
        bad = {'olaylar': [{'ad': 'Order', 'konu': 'orders', 'uyumluluk': 'BACKWARD', 'alanlar': [{'alan': 'customer', 'tip': 'string'}]}]}
        good = {'olaylar': [{'ad': 'OrderPlaced', 'konu': 'orders', 'anahtar': 'id', 'uyumluluk': 'BACKWARD', 'alanlar': [{'alan': 'id', 'tip': 'long', 'zorunlu': True},
                                                                                                                       {'alan': 'amount', 'tip': 'double'}]}],
                'konular': [{'konu': 'orders', 'bolum': 6, 'saklama_saat': 168, 'temizleme': 'delete'}], 'akislar': []}
        with patch.object(stream, 'driver', return_value=FakeKafka), model_reply(bad, good):
            self.run_op('kafka-event-architecture', bootstrap_servers='b:9093', topic='orders', max_seconds=5)
        avsc = self.read('OrderPlaced.avsc')
        self.assertIn('"type": "long"', avsc); self.assertIn('"default": null', avsc)
        self.assertEqual(self.read('konu_ayarlari.json')[0]['configs'][0]['value'], '604800000')
