"""RAG test seti üretici (AI agent): dokümanları yerelde parçalara böler (dokuman-parcala bloğuyla aynı kurallar) ve modelden her parça
için o parçadan cevaplanabilen soru, kısa cevap ve cevabın parçadaki AYNEN geçen dayanak cümlesini ister. Yanıt yerelde denetlenir: parça
kimliği geçerli olmalı, dayanak metni parçada birebir geçmeli (boşluk farkı hariç), cevap boş olmamalı, aynı soru iki kez olmamalı ve her
parçadan en az bir soru üretilmeli. Çıktı, rag-geri-getirme-degerlendirme bloğunun girdisine hazırdır (beklenen sütunu doldurulmuş).
Çıktı: test_seti.csv, test_seti.jsonl, parcalar.jsonl, test-seti.md."""
import argparse, json, re
from argparse import Namespace
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_text, md_table, mask_text
from rag import chunk


def squash(s):
    return re.sub(r'\s+', ' ', str(s)).strip().casefold()


def validator(chunks):
    by_id = {c['id']: c for c in chunks}

    def validate(obj):
        items, problems, out, seen = obj.get('sorular'), [], [], set()
        if not isinstance(items, list) or not items:
            raise ValueError('"sorular" listesi boş.')
        for i, q in enumerate(items, start=1):
            if not isinstance(q, dict):
                continue
            cid, question, answer, quote = (str(q.get(k, '')).strip() for k in ('parca', 'soru', 'cevap', 'dayanak'))
            if cid not in by_id:
                problems.append(f'{i}. soru: "{cid}" geçerli parça kimliği değil')
                continue
            if not question.endswith('?') or len(question) < 10:
                problems.append(f'{i}. soru soru cümlesi olmalı (? ile biten)')
            if not answer:
                problems.append(f'{i}. soru: cevap boş')
            text = by_id[cid]['metin']
            if len(quote) < 15 or squash(quote) not in squash(text) and squash(quote) not in squash(mask_text(text)[0]):  # the model saw masked text
                problems.append(f'{i}. soru: dayanak metni {cid} parçasında aynen geçmiyor')
            if squash(question) in seen:
                problems.append(f'{i}. soru tekrar ediyor')
            seen.add(squash(question))
            out.append({'soru_id': f'Q{len(out) + 1}', 'soru': question, 'cevap': answer, 'beklenen': cid, 'dayanak': quote, 'zorluk': str(q.get('zorluk', '')).strip()})
        covered = {o['beklenen'] for o in out}
        missing = [c for c in by_id if c not in covered]
        if missing:
            problems.append('soru üretilmeyen parça: ' + ', '.join(missing[:10]))
        if problems:
            raise ValueError('; '.join(problems[:12]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir RAG değerlendirme uzmanısın. Her doküman parçası için, YALNIZCA o parçayı okuyarak cevaplanabilen, gerçek bir kullanıcının '
          'soracağı doğallıkta sorular yaz. Her soru için kısa cevabı ve cevabın dayandığı cümleyi parçadan AYNEN kopyala (dayanak). Parçalar arası '
          'karşılaştırma gerektiren veya parçada olmayan bilgiyi soran soru yazma. zorluk: kolay, orta veya zor. '
          'Yanıt şeması: {"sorular": [{"parca": "dosya.md#1", "soru": "...?", "cevap": "...", "dayanak": "parçadan aynen cümle", "zorluk": "kolay"}]}')


def main():
    p = argparse.ArgumentParser(description='Dokümanlardan dayanağı doğrulanmış RAG soru-cevap test seti üreten agent.')
    p.add_argument('--input', default=str(here('ornek', 'dokumanlar')))
    p.add_argument('--max-tokens', type=int, default=200, help='Parça boyutu (dokuman-parcala ile aynı)')
    p.add_argument('--per-chunk', type=int, default=2, help='Parça başına istenen soru sayısı')
    llm.add_arguments(p)
    args = p.parse_args()
    res = chunk(Namespace(input=args.input, max_tokens=args.max_tokens, overlap=0))
    chunks = [json.loads(l) for l in res.files['parcalar.jsonl'].splitlines() if l.strip()]
    out = output_dir(args.output)
    write_text(out / 'parcalar.jsonl', res.files['parcalar.jsonl'] + '\n')
    user = f'Her parça için {args.per_chunk} soru üret.\n\n' + '\n\n'.join(f"[{c['id']}] ({c['baslik_yolu'] or c['kaynak']})\n{c['metin']}" for c in chunks)
    rows, meta = llm.ask(args, out, SYSTEM, user, validator(chunks))
    if rows is None:
        return
    tabular.write_csv(out / 'test_seti.csv', ['soru_id', 'soru', 'cevap', 'beklenen', 'dayanak', 'zorluk'], rows)
    write_text(out / 'test_seti.jsonl', '\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n')
    md = ['# RAG test seti', '', f'{len(chunks)} parçadan {len(rows)} soru; her sorunun dayanağı parçada birebir bulundu.', '',
          md_table(['#', 'Soru', 'Beklenen parça', 'Zorluk'], [[r['soru_id'], r['soru'], r['beklenen'], r['zorluk']] for r in rows]), '',
          'Kullanım: retriever’ınızın her soru için getirdiği parça kimliklerini `getirilen` sütununa (| ile) ekleyip rag-geri-getirme-degerlendirme bloğunu çalıştırın.',
          '', f"_Model: {meta['model']} · Sorular otomatik üretildi; yayına almadan önce bir alan uzmanı gözden geçirmeli._"]
    write_text(out / 'test-seti.md', '\n'.join(md))
    print(f"[OK] {len(rows)} soru, {len(chunks)} parçanın hepsi kapsandı. Çıktı: {out / 'test_seti.csv'}")


if __name__ == '__main__':
    llm.run(main)
