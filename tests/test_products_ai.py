"""GenAI & vector product packages: duplicate detection on store contents and the RAG design agent."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_products import Base, FakeSql, model_reply  # noqa: E402
import native  # noqa: E402
from products import ai  # noqa: E402

TEXT = 'Kurumsal izin politikası: çalışanlar yıllık izin talebini en az on beş gün önce sistem üzerinden yöneticisine iletir ve onay bekler.'


class Duplicates(Base):
    def test_analyse_exact_text_and_vector(self):
        chunks = [ai.chunk('a', TEXT, {'source': 'el-kitabi.pdf'}, [1.0, 0.0, 0.0]),
                  ai.chunk('b', TEXT.upper() + '  ', {'source': 'el-kitabi-kopya.pdf'}, [0.0, 1.0, 0.0]),
                  ai.chunk('c', TEXT.replace('onay bekler', 'onay bekliyor'), {'source': 'eski.pdf'}, [0.0, 0.0, 1.0]),
                  ai.chunk('d', 'Tamamen farklı bir konu: kargo teslim süreleri ve iade adımları.', {'source': 'x'}, [0.0, 0.0, 0.999])]
        exact, pairs, stats = ai.analyse(chunks, {'text_similarity': 0.6, 'vector_similarity': 0.99})
        self.assertEqual(stats['birebir_grup'], 1)
        kinds = {(p['parca_1'], p['parca_2'], p['tur']) for p in pairs}
        self.assertIn(('a', 'c', 'metin (Jaccard)'), kinds); self.assertIn(('c', 'd', 'vektör (kosinüs)'), kinds)
        self.assertNotIn(('a', 'b', 'metin (Jaccard)'), kinds)  # already reported as exact

    def test_mixed_dimensions_fail(self):
        with self.assertRaises(native.NativeError):
            ai.vector_pairs([ai.chunk('a', '', {}, [1.0, 0.0]), ai.chunk('b', '', {}, [1.0, 0.0, 0.0])], 0.9)

    def test_qdrant_scroll_paging(self):
        self.server.add('GET', '/collections/docs', {'result': {'points_count': 3, 'config': {'params': {'vectors': {'size': 2, 'distance': 'Cosine'}}}}})
        self.server.add('POST', '/collections/docs/points/scroll', {'result': {'points': [{'id': 1, 'payload': {'text': TEXT, 'source': 's1'}, 'vector': [1, 0]}], 'next_page_offset': 2}})
        self.server.add('POST', '/collections/docs/points/scroll', {'result': {'points': [{'id': 2, 'payload': {'text': TEXT, 'source': 's2'}, 'vector': [1, 0]}], 'next_page_offset': None}})
        self.run_op('qdrant-rag-duplicates', server='https://q.example', collection='docs')
        self.assertEqual(self.server.received[2][2]['offset'], 2)
        self.assertEqual(self.server.received[1][3]['Api-Key'], 'test-only-secret')
        self.assertEqual(self.read('ozet.json')['birebir_grup'], 1)

    def test_pinecone_list_then_fetch(self):
        self.server.add('POST', '/describe_index_stats', {'dimension': 2, 'totalVectorCount': 2})
        self.server.add('GET', r'/vectors/list\?limit=100&namespace=ns', {'vectors': [{'id': 'v1'}, {'id': 'v2'}]})
        self.server.add('GET', r'/vectors/fetch\?ids=v1&ids=v2&namespace=ns', {'vectors': {'v1': {'values': [1, 0], 'metadata': {'text': 'a b c'}},
                                                                                         'v2': {'values': [1, 0.001], 'metadata': {'text': 'x y z'}}}})
        self.run_op('pinecone-rag-duplicates', index_host='https://idx.example', namespace='ns')
        self.assertEqual(self.read('ozet.json')['benzer_cift'], 1)

    def test_openai_vector_store_contents(self):
        self.server.add('GET', '/v1/vector_stores/vs_1', {'name': 'kb', 'file_counts': {'total': 2}})
        self.server.add('GET', r'/v1/vector_stores/vs_1/files\?limit=100', {'data': [{'id': 'f1', 'status': 'completed'}, {'id': 'f2', 'status': 'completed'}], 'has_more': False})
        self.server.add('GET', '/v1/vector_stores/vs_1/files/f1/content', {'data': [{'type': 'text', 'text': TEXT}]})
        self.server.add('GET', '/v1/vector_stores/vs_1/files/f2/content', {'data': [{'type': 'text', 'text': TEXT}]})
        self.run_op('openai-rag-duplicates', vector_store_id='vs_1')
        self.assertEqual(self.read('ozet.json')['birebir_fazla'], 1)

    def test_llamaindex_docstore_and_langchain_jsonl(self):
        (self.root / 'docstore.json').write_text(json.dumps({'docstore/data': {'n1': {'__type__': '1', '__data__': json.dumps({'text': TEXT, 'metadata': {'file_name': 'a.pdf'}})},
                                                                             'n2': {'__type__': '1', '__data__': {'text': TEXT, 'metadata': {'file_name': 'b.pdf'}}}}}))
        self.run_op('llamaindex-rag-duplicates', docstore_file='docstore.json')
        self.assertEqual(self.read('ozet.json')['birebir_grup'], 1)
        self.out = self.root / 'lc'
        (self.root / 'd.jsonl').write_text(json.dumps({'lc': 1, 'kwargs': {'page_content': TEXT, 'metadata': {'source': 'x'}}}) + '\n' +
                                           json.dumps({'page_content': 'farklı', 'metadata': {}}) + '\n')
        self.run_op('langchain-rag-duplicates', documents_file='d.jsonl')
        self.assertEqual(self.read('ozet.json')['parca'], 2)

    def test_pgvector_reads_embeddings_as_text(self):
        fake = FakeSql({'chunks': ([], [{'id': '1', 'text': TEXT, 'embedding': '[1,0]', 'source': 'a'}, {'id': '2', 'text': TEXT, 'embedding': '[1,0]', 'source': 'b'}]),
                        'vector_indexes': ([], [])})
        with patch.object(ai, 'sql_source', return_value=fake):
            self.run_op('pgvector-rag-duplicates', host='h', database='d', user='u', table='public.chunks', id_column='id', text_column='content',
                        embedding_column='embedding', metadata_columns=['source'])
        self.assertIn('embedding::text', fake.queries[0][1])
        self.assertEqual(self.read('ozet.json')['vektor_boyutu'], 2)

    def test_milvus_error_code_fails(self):
        self.server.add('POST', '/v2/vectordb/collections/describe', {'code': 1100, 'message': 'collection not found'})
        with self.assertRaises(native.NativeError):
            self.run_op('milvus-rag-duplicates', server='https://m.example', collection='docs')

    def test_rag_design_validates_filters_and_builds_eval_template(self):
        (self.root / 'docstore.json').write_text(json.dumps({'docstore/data': {f'n{i}': {'__data__': {'text': f'{TEXT} {i}', 'metadata': {'file_name': f'{i}.pdf'}}} for i in range(12)}}))
        good = {'parcalama': {'strateji': 'baslik', 'boyut_token': 400, 'ortusme_token': 40, 'gerekce': 'x'},
                'getirme': {'yontem': 'hibrit', 'top_k': 8, 'metadata_filtreleri': ['file_name'], 'gerekce': 'y'},
                'degerlendirme': {'metrikler': ['recall@k', 'faithfulness'], 'soru_sayisi': 10, 'soru_turleri': ['olgusal'], 'kabul_esikleri': {'recall@k': 0.8}},
                'temizlik': [], 'riskler': [], 'sorular': []}
        bad = json.loads(json.dumps(good)); bad['getirme']['metadata_filtreleri'] = ['department']
        with model_reply(bad, good):
            self.run_op('llamaindex-rag-design', docstore_file='docstore.json')
        lines = self.read('degerlendirme_sablonu.jsonl').splitlines()
        self.assertEqual(len(lines), 10)
        self.assertTrue(json.loads(lines[0])['beklenen_kaynaklar'][0].startswith('n'))
