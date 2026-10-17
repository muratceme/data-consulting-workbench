"""Model kartı yazarı (AI agent): ikili sınıflandırma modelinin tahmin dosyasından AUC, eşikteki kesinlik/duyarlılık, pozitif oran
ve segment bazında performansı kodla hesaplar; bunları numaralı olgulara çevirir. Model, olgulara ve kullanım bağlamına dayanarak
model kartı yazar: amaç, kullanım alanı, kullanılmaması gereken durumlar, bulgular, sınırlamalar, riskler ve izleme planı. Metindeki
her sayının olgularda bulunduğu ve her bulgunun geçerli olgu numarasına dayandığı yerelde denetlenir. Satırlar gönderilmez.
Çıktı: model-karti.md, olgular.csv, model-karti.json."""
import argparse
from collections import defaultdict
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text
from donem_yorumu import allowed_numbers, unknown_numbers, tr
from ml import auc, confusion

SECTIONS = ('amac', 'kullanim_alani', 'kullanilmamasi_gereken', 'sinirlamalar', 'riskler', 'izleme')


def build_facts(columns, rows, actual, score, segment, threshold):
    for c in (actual, score):
        if c not in columns:
            raise ValueError(f'"{c}" sütunu yok. Sütunlar: {", ".join(columns)}')
    ys, ss, segs = [], [], []
    for r in rows:
        y, s = tabular.parse_number(r[actual]), tabular.parse_number(r[score])
        if y is not None and s is not None:
            ys.append(1 if y >= 0.5 else 0)
            ss.append(s)
            segs.append(r[segment].strip() if segment in columns else '')
    if len(set(ys)) < 2:
        raise ValueError('Gerçek etikette iki sınıf da bulunmalı.')
    facts = []

    def add(text):
        facts.append({'id': f'F{len(facts) + 1}', 'olgu': text})

    def metrics(y, s):
        tp, fp, fn, tn, prec, rec, f1 = confusion(y, [x >= threshold for x in s])
        return auc(y, s), prec, rec, f1, sum(y) / len(y), (tp + fp) / len(y)
    a, prec, rec, f1, base, sel = metrics(ys, ss)
    add(f'{len(ys)} kayıt; pozitif oranı %{tr(base * 100, 1)}')
    add(f'ROC AUC {tr(a, 3)}')
    add(f'Eşik {tr(threshold, 2)}: kesinlik %{tr(prec * 100, 1)}, duyarlılık %{tr(rec * 100, 1)}, F1 {tr(f1, 3)}, pozitif karar oranı %{tr(sel * 100, 1)}')
    if segment in columns:
        groups = defaultdict(lambda: ([], []))
        for y, s, g in zip(ys, ss, segs):
            groups[g or '(boş)'][0].append(y)
            groups[g or '(boş)'][1].append(s)
        for g, (y, s) in sorted(groups.items()):
            ga, gp, gr, _, gb, _ = metrics(y, s)
            add(f'Segment {g}: {len(y)} kayıt, pozitif oranı %{tr(gb * 100, 1)}, AUC {tr(ga, 3) if ga is not None else "hesaplanamaz (tek sınıf)"}, '
                f'eşikte kesinlik %{tr(gp * 100, 1)}, duyarlılık %{tr(gr * 100, 1)}')
    return facts, sorted({g for g in segs if g})


def validator(facts, labels):
    ids = {f['id'] for f in facts}
    values = allowed_numbers(facts, labels)

    def text_list(v):
        return [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()]

    def validate(obj):
        problems, card = [], {}
        for s in SECTIONS:
            card[s] = text_list(obj.get(s, []))
            if not card[s]:
                problems.append(f'"{s}" boş')
        findings = []
        for i, b in enumerate(obj.get('bulgular') or [], start=1):
            if not isinstance(b, dict) or not str(b.get('metin', '')).strip():
                continue
            refs = [str(r).strip().upper() for r in b.get('kanit') or []]
            if not refs or any(r not in ids for r in refs):
                problems.append(f'{i}. bulgunun kanit listesi geçerli olgu numarası içermeli')
            findings.append({'metin': str(b['metin']).strip(), 'kanit': refs})
        if not findings:
            problems.append('"bulgular" boş')
        bad = unknown_numbers(' '.join([*(x for s in SECTIONS for x in card[s]), *(f['metin'] for f in findings)]), values, labels)
        if bad:
            problems.append('Olgularda olmayan sayı: ' + ', '.join(dict.fromkeys(bad)) + '. Yalnızca olgulardaki sayıları kullan.')
        if problems:
            raise ValueError('; '.join(problems))
        return {**card, 'bulgular': findings, 'ad': str(obj.get('model_adi', '')).strip()}
    return validate


SYSTEM = (llm.SAFETY + ' Sen sorumlu yapay zekâ ve model risk yönetimi uzmanısın. Kodla hesaplanmış, numaralı performans olgularından ve kullanım '
          'bağlamından bir model kartı yaz. Metinde YALNIZCA olgulardaki sayıları kullan; her bulgunun kanit listesine olgu numaralarını yaz. '
          'Segmentler arası farkları, düşük performanslı grupları ve eşik seçiminin etkisini açıkça belirt. Bağlamda olmayan eğitim verisi, '
          'özellik veya kullanıcı bilgisi uydurma; bilinmiyorsa sinirlamalar listesinde “belirtilmemiş” de. '
          'Yanıt şeması: {"model_adi": "...", "amac": ["..."], "kullanim_alani": ["..."], "kullanilmamasi_gereken": ["..."], '
          '"bulgular": [{"metin": "...", "kanit": ["F2"]}], "sinirlamalar": ["..."], "riskler": ["..."], "izleme": ["..."]}')
TITLES = {'amac': 'Amaç', 'kullanim_alani': 'Kullanım alanı', 'kullanilmamasi_gereken': 'Kullanılmaması gereken durumlar', 'sinirlamalar': 'Sınırlamalar',
          'riskler': 'Riskler', 'izleme': 'İzleme planı'}


def main():
    p = argparse.ArgumentParser(description='Tahmin dosyasından sayıları doğrulanmış model kartı yazan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'model_tahminleri.csv')))
    p.add_argument('--actual', default='gercek')
    p.add_argument('--score', default='skor')
    p.add_argument('--segment', default='segment')
    p.add_argument('--threshold', type=float, default=0.5)
    p.add_argument('--context', default=str(here('ornek', 'model_baglami.md')), help='Modelin amacı, kullanıcıları, eğitim verisi')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    facts, segments = build_facts(columns, rows, args.actual, args.score, args.segment, args.threshold)
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    tabular.write_csv(out / 'olgular.csv', ['id', 'olgu'], facts)
    user = 'Olgular:\n' + '\n'.join(f"{f['id']}: {f['olgu']}" for f in facts) + f"\n\nKullanım bağlamı:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    card, meta = llm.ask(args, out, SYSTEM, user, validator(facts, segments))
    if card is None:
        return
    write_json(out / 'model-karti.json', {**card, 'olgular': facts})
    md = [f"# Model kartı — {card['ad'] or Path(args.input).stem}", '']
    for s in ('amac', 'kullanim_alani', 'kullanilmamasi_gereken'):
        md += [f'## {TITLES[s]}', ''] + [f'- {x}' for x in card[s]] + ['']
    md += ['## Performans bulguları', ''] + [f"- {b['metin']} ({', '.join(b['kanit'])})" for b in card['bulgular']] + ['']
    for s in ('sinirlamalar', 'riskler', 'izleme'):
        md += [f'## {TITLES[s]}', ''] + [f'- {x}' for x in card[s]] + ['']
    md += ['## Olgular (kodla hesaplandı)', ''] + [f"- {f['id']}: {f['olgu']}" for f in facts]
    md += ['', f"_Model: {meta['model']} · Sayılar yerelde hesaplandı ve metinle eşleştirildi. Kart taslaktır; model sahibi ve risk birimi onaylamalıdır._"]
    write_text(out / 'model-karti.md', '\n'.join(md))
    print(f"[OK] Model kartı yazıldı: {len(facts)} olgu, {len(card['bulgular'])} bulgu. Çıktı: {out / 'model-karti.md'}")


if __name__ == '__main__':
    llm.run(main)
