"""Bulut maliyeti tasarruf planı (AI agent): maliyet dışa aktarımından son 30 günü önceki 30 günle kodla karşılaştırır; servis ve
kaynak bazında en büyük kalemleri, artışları ve etiketsiz maliyeti numaralı olgulara çevirir. Model yalnızca bu olgulara dayanarak
önceliklendirilmiş tasarruf aksiyonları yazar. Her aksiyonun kanıt olgusu, tahmini tasarrufun dayandığı kalemin aylık maliyetini
aşmaması, toplam tasarrufun toplam maliyeti aşmaması ve metindeki her sayının olgularda bulunması yerelde denetlenir.
Satırlar gönderilmez, yalnızca toplamlar. Çıktı: tasarruf-plani.md, aksiyonlar.csv, olgular.csv."""
import argparse
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from donem_yorumu import allowed_numbers, unknown_numbers, tr

EFFORT = ('düşük', 'orta', 'yüksek')


def build_facts(columns, rows, date_col, cost_col, service_col, resource_col, tag_col, top):
    for c in (date_col, cost_col, service_col, resource_col):
        if c not in columns:
            raise ValueError(f'"{c}" sütunu yok. Sütunlar: {", ".join(columns)}')
    style = tabular.number_style([r[cost_col] for r in rows if r[cost_col].strip()])
    pts = []
    for r in rows:
        d, c = tabular.parse_date(r[date_col]), tabular.parse_number(r[cost_col], style) if r[cost_col].strip() else None
        if d and c is not None:
            pts.append((d[0], c, r[service_col].strip() or '(boş)', r[resource_col].strip() or '(boş)', r[tag_col].strip() if tag_col in columns else 'x'))
    if not pts:
        raise ValueError('Tarihi ve maliyeti okunabilen satır yok.')
    last = max(p[0] for p in pts)
    cur_start, prev_start = last - timedelta(days=29), last - timedelta(days=59)
    cur, prev = defaultdict(float), defaultdict(float)
    for d, c, s, res, tag in pts:
        bucket = cur if d >= cur_start else prev if d >= prev_start else None
        if bucket is not None:
            bucket[('servis', s)] += c
            bucket[('kaynak', res)] += c
            bucket[('toplam', '')] += c
            if not tag:
                bucket[('etiketsiz', '')] += c
    facts = []

    def add(text, kind, name, value):
        facts.append({'id': f'F{len(facts) + 1}', 'olgu': text, 'tur': kind, 'kalem': name, 'aylik_maliyet': round(value, 2)})

    def change(a, b):
        return f'; önceki 30 gün {tr(b)}, değişim %{tr((a - b) / b * 100, 1)}' if b else '; önceki dönemde yok'
    total = cur[('toplam', '')]
    add(f'Son 30 gün ({cur_start:%Y-%m-%d} – {last:%Y-%m-%d}) toplam maliyet {tr(total)}' + change(total, prev[('toplam', '')]), 'toplam', 'toplam', total)
    for kind in ('servis', 'kaynak'):
        items = sorted(((k[1], v) for k, v in cur.items() if k[0] == kind), key=lambda kv: -kv[1])[:top]
        for name, v in items:
            add(f'{kind} {name}: son 30 gün {tr(v)} (toplamın %{tr(v / total * 100, 1)})' + change(v, prev[(kind, name)]), kind, name, v)
    if tag_col in columns:
        untagged = cur[('etiketsiz', '')]
        add(f'Proje etiketi olmayan maliyet: {tr(untagged)} (toplamın %{tr(untagged / total * 100, 1)})', 'etiketsiz', 'etiketsiz', untagged)
    return facts, total


def validator(facts, total):
    by_id = {f['id']: f for f in facts}
    labels = sorted({f['kalem'] for f in facts if f['kalem']}, key=len, reverse=True)
    values = allowed_numbers(facts, labels)

    def validate(obj):
        actions, problems = [], []
        for i, a in enumerate(obj.get('aksiyonlar') or [], start=1):
            if not isinstance(a, dict) or not str(a.get('baslik', '')).strip():
                continue
            refs = [str(r).strip().upper() for r in a.get('kanit') or []]
            wrong = [r for r in refs if r not in by_id]
            if not refs or wrong:
                problems.append(f'{i}. aksiyonun kanit listesi geçerli olgu içermeli' + (f' ({", ".join(wrong)} yok)' if wrong else ''))
                continue
            saving = tabular.parse_number(str(a.get('tahmini_aylik_tasarruf', '')).replace('$', '').strip(), 'en')
            ceiling = max(by_id[r]['aylik_maliyet'] for r in refs) if any(by_id[r]['tur'] != 'toplam' for r in refs) else total
            if saving is None or saving < 0:
                problems.append(f'{i}. aksiyonda tahmini_aylik_tasarruf sayı olmalı')
                continue
            if saving > ceiling + 0.01:
                problems.append(f'{i}. aksiyonun tasarrufu ({saving}) dayandığı kalemin aylık maliyetini ({ceiling}) aşıyor')
            effort = str(a.get('efor', '')).strip().lower()
            if effort not in EFFORT:
                problems.append(f'{i}. aksiyonda efor şunlardan biri olmalı: {", ".join(EFFORT)}')
            actions.append({'no': len(actions) + 1, 'baslik': str(a['baslik']).strip(), 'aciklama': str(a.get('aciklama', '')).strip(), 'kanit': ', '.join(refs),
                            'tahmini_aylik_tasarruf': round(saving, 2), 'efor': effort, 'risk': str(a.get('risk', '')).strip()})
        if not actions:
            problems.append('"aksiyonlar" boş')
        if sum(a['tahmini_aylik_tasarruf'] for a in actions) > total:
            problems.append('toplam tasarruf toplam maliyeti aşıyor')
        summary = str(obj.get('ozet', '')).strip()
        allowed = values | {a['tahmini_aylik_tasarruf'] for a in actions} | {round(sum(a['tahmini_aylik_tasarruf'] for a in actions), 2)}
        bad = unknown_numbers(' '.join([summary] + [a['baslik'] + ' ' + a['aciklama'] for a in actions]), allowed, labels)
        if bad:
            problems.append('Olgularda olmayan sayı: ' + ', '.join(dict.fromkeys(bad)) + '. Yalnızca olgulardaki sayıları ve kendi tasarruf tahminlerini kullan.')
        if problems:
            raise ValueError('; '.join(problems))
        return {'summary': summary, 'actions': actions, 'questions': [str(q) for q in obj.get('sorular', []) if q]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir FinOps danışmanısın. Kodla hesaplanmış, numaralı maliyet olgularına dayanarak önceliklendirilmiş tasarruf aksiyonları yaz '
          '(rezervasyon/taahhüt, boyutlandırma, kapatma, depolama katmanı, etiketleme…). Her aksiyonun kanit listesine dayandığı olgu numaralarını yaz; '
          'tahmini_aylik_tasarruf dayandığı kalemin aylık maliyetini aşamaz ve bir tahmindir. Metinde olgularda olmayan sayı kullanma. '
          f'efor: {", ".join(EFFORT)}. Yanıt şeması: {{"ozet": "...", "aksiyonlar": [{{"baslik": "...", "aciklama": "...", "kanit": ["F2"], '
          '"tahmini_aylik_tasarruf": 120, "efor": "düşük", "risk": "..."}], "sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Bulut maliyet dökümünden olgulara dayalı tasarruf planı yazan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'bulut_maliyet.csv')))
    p.add_argument('--date', default='tarih')
    p.add_argument('--cost', default='maliyet_usd')
    p.add_argument('--service', default='servis')
    p.add_argument('--resource', default='kaynak')
    p.add_argument('--tag', default='etiket_proje', help='Proje/maliyet merkezi etiketi (etiketsiz maliyeti ölçmek için)')
    p.add_argument('--top', type=int, default=5)
    p.add_argument('--context', help='İş bağlamı (isteğe bağlı .md)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    facts, total = build_facts(columns, rows, args.date, args.cost, args.service, args.resource, args.tag, args.top)
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    tabular.write_csv(out / 'olgular.csv', ['id', 'olgu', 'tur', 'kalem', 'aylik_maliyet'], facts)
    user = 'Olgular:\n' + '\n'.join(f"{f['id']}: {f['olgu']}" for f in facts) + f"\n\nBağlam:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\""
    result, meta = llm.ask(args, out, SYSTEM, user, validator(facts, total))
    if result is None:
        return
    acts = result['actions']
    tabular.write_csv(out / 'aksiyonlar.csv', list(acts[0]), acts)
    saving = round(sum(a['tahmini_aylik_tasarruf'] for a in acts), 2)
    write_json(out / 'tasarruf-plani.json', {**result, 'olgular': facts, 'toplam_tasarruf': saving})
    md = ['# Bulut maliyeti tasarruf planı', '', result['summary'], '',
          f"Son 30 gün maliyet: **{tr(total)}** · Önerilen aksiyonların tahmini aylık tasarrufu: **{tr(saving)}** (%{tr(saving / total * 100, 1) if total else '0'})", '',
          md_table(['#', 'Aksiyon', 'Tasarruf/ay', 'Efor', 'Kanıt', 'Risk'], [[a['no'], a['baslik'], tr(a['tahmini_aylik_tasarruf']), a['efor'], a['kanit'], a['risk'] or '—'] for a in acts])]
    for a in acts:
        if a['aciklama']:
            md += ['', f"**{a['no']}. {a['baslik']}** — {a['aciklama']}"]
    md += ['', '## Olgular (kodla hesaplandı)', ''] + [f"- {f['id']}: {f['olgu']}" for f in facts]
    if result['questions']:
        md += ['', '## Sorular', ''] + [f'- {q}' for q in result['questions']]
    md += ['', f"_Model: {meta['model']} · Tasarruflar tahmindir; uygulamadan önce kaynak sahibiyle doğrulayın ve sonraki ay gerçekleşeni ölçün._"]
    write_text(out / 'tasarruf-plani.md', '\n'.join(md))
    print(f"[OK] {len(acts)} aksiyon, tahmini aylık tasarruf {tr(saving)} / {tr(total)}. Çıktı: {out / 'tasarruf-plani.md'}")


if __name__ == '__main__':
    llm.run(main)
