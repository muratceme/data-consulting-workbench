"""Dönem yorumu (AI agent): satış/finans dışa aktarımında son dönemi bir önceki dönemle karşılaştırır ve yönetim özeti yazar.
Toplamlar, değişimler ve değişime en çok katkı veren kırılımlar yerelde kodla hesaplanır; modele yalnızca bu numaralı
olgular gider (satır gönderilmez). Model metnindeki her sayı olgularla yerelde karşılaştırılır: olgularda olmayan bir sayı
yazarsa yanıt reddedilir. Çıktı: donem-yorumu.md, olgular.csv, olgular.json."""
import argparse, re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

PERIODS = {'ay': lambda d: d.strftime('%Y-%m'), 'ceyrek': lambda d: f'{d.year}-Ç{(d.month - 1) // 3 + 1}',
           'hafta': lambda d: '{}-H{:02d}'.format(*d.isocalendar()[:2]), 'yil': lambda d: str(d.year)}


def tr(n, digits=2):
    """1234567.891 -> '1.234.567,89' (Turkish style)."""
    text = f'{abs(n):,.{digits}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return ('-' if n < 0 else '') + text


def pct(new, old):
    return None if not old else (new - old) / abs(old) * 100


def aggregate(columns, rows, date_col, measure, dims, period):
    for c in [date_col, measure, *dims]:
        if c not in columns:
            raise ValueError(f'"{c}" sütunu dosyada yok. Sütunlar: {", ".join(columns)}')
    style = tabular.number_style([r[measure] for r in rows])
    totals, by_dim, skipped = defaultdict(float), {d: defaultdict(lambda: defaultdict(float)) for d in dims}, 0
    for r in rows:
        day, value = tabular.parse_date(r[date_col]), tabular.parse_number(r[measure], style)
        if not day or value is None:
            skipped += 1
            continue
        key = PERIODS[period](day[0])
        totals[key] += value
        for d in dims:
            by_dim[d][r[d].strip() or '(boş)'][key] += value
    if len(totals) < 2:
        raise ValueError('Karşılaştırma için en az iki dönem veri gerekir.')
    return totals, by_dim, skipped


def facts(totals, by_dim, measure, current=None, top=3):
    order = sorted(totals)
    current = current or order[-1]
    if current not in totals:
        raise ValueError(f'"{current}" dönemi veride yok. Dönemler: {", ".join(order)}')
    i = order.index(current)
    if i == 0:
        raise ValueError(f'"{current}" ilk dönem; önceki dönem olmadan karşılaştırma yapılamaz.')
    prev = order[i - 1]
    out = []

    def add(text, **values):
        out.append({'id': f'F{len(out) + 1}', 'olgu': text, **values})
    cur_total, prev_total = totals[current], totals[prev]
    p = pct(cur_total, prev_total)
    add(f'Toplam {measure} {current}: {tr(cur_total)}; önceki dönem {prev}: {tr(prev_total)}; değişim {tr(cur_total - prev_total)}'
        + (f' (%{tr(p, 1)})' if p is not None else ''), grup='toplam', deger=cur_total, onceki=prev_total)
    change = cur_total - prev_total
    for dim, values in by_dim.items():
        rows = []
        for name, series in values.items():
            a, b = series.get(current, 0.0), series.get(prev, 0.0)
            rows.append((name, a, b, a - b))
        share = sorted(rows, key=lambda r: -r[1])
        for name, a, b, delta in sorted(rows, key=lambda r: -abs(r[3]))[:top]:
            p = pct(a, b)
            # A share of a near-zero total change is meaningless (e.g. %-1.761), so it is shown only for a real move.
            contrib = f'; toplam değişime katkısı %{tr(delta / abs(change) * 100, 1)}' if abs(change) >= 0.01 * abs(prev_total) else ''
            add(f'{dim} = {name}: {current} {tr(a)}, {prev} {tr(b)}, değişim {tr(delta)}' + (f' (%{tr(p, 1)})' if p is not None else ' (yeni)') + contrib,
                grup=dim, deger=a, onceki=b)
        lead = share[0]
        add(f'{dim} kırılımında en büyük pay {lead[0]}: %{tr(lead[1] / cur_total * 100, 1) if cur_total else "0"} ({current})', grup=dim, deger=lead[1], onceki=None)
    if i >= 2:
        trend = ', '.join(f'{k}: {tr(totals[k])}' for k in order[max(0, i - 5):i + 1])
        add(f'Son dönemler toplam {measure}: {trend}', grup='trend', deger=None, onceki=None)
    return current, prev, out


NUMBER = re.compile(r'(?<![\w])[+-]?\d[\d.,]*\d|(?<![\w.,])\d(?![\w])')


def allowed_numbers(fact_list, labels):
    values = set()
    for f in fact_list:
        text = f['olgu']
        for label in labels:
            text = text.replace(label, ' ')
        for tok in NUMBER.findall(text):
            n = tabular.parse_number(tok, 'tr')
            if n is not None:
                values.add(abs(n))
    return values


def unknown_numbers(text, values, labels):
    for label in sorted(labels, key=len, reverse=True):
        text = text.replace(label, ' ')
    text = re.sub(r'\b\d{4}-(?:\d{2}|Ç\d|H\d{2})\b|\bF\d+\b', ' ', text)
    bad = []
    for tok in NUMBER.findall(text):
        candidates = {tabular.parse_number(tok, s) for s in ('tr', 'en')} - {None}
        if not candidates:
            continue
        tail = re.split(r'[.,]', tok)[-1]
        decimals = len(tail) if re.search(r'[.,]', tok) and len(tail) in (1, 2) else 0
        tolerance = 0.5 * 10 ** -decimals + 1e-6  # the model may round a fact to what it wrote
        ok = False
        for x in candidates:
            x = abs(x)
            if x.is_integer() and (x <= 3 or 1990 <= x <= 2100):
                ok = True  # small counts ("iki bölge" → 2) and years
            if any(abs(x - a / scale) <= tolerance for a in values for scale in (1, 1e3, 1e6, 1e9)):
                ok = True
        if not ok:
            bad.append(tok)
    return bad


def validator(fact_list, labels):
    ids = {f['id'] for f in fact_list}
    values = allowed_numbers(fact_list, labels)

    def validate(obj):
        problems = []
        summary = str(obj.get('ozet', '')).strip()
        if not summary:
            problems.append('"ozet" boş')
        findings = []
        for i, b in enumerate(obj.get('bulgular', []) or [], start=1):
            if not isinstance(b, dict) or not str(b.get('metin', '')).strip():
                continue
            refs = [str(r).strip().upper() for r in (b.get('kanit') or [])]
            wrong = [r for r in refs if r not in ids]
            if not refs or wrong:
                problems.append(f'{i}. bulgunun "kanit" listesi geçerli olgu numarası içermeli' + (f' ({", ".join(wrong)} yok)' if wrong else ''))
            findings.append({'text': str(b['metin']).strip(), 'evidence': refs})
        if not findings:
            problems.append('"bulgular" boş')
        actions = [str(a).strip() for a in obj.get('aksiyonlar', []) or [] if str(a).strip()]
        questions = [str(q).strip() for q in obj.get('sorular', []) or [] if str(q).strip()]
        title = str(obj.get('baslik', '')).strip()
        bad = unknown_numbers(' '.join([title, summary, *[f['text'] for f in findings], *actions]), values, labels)
        if bad:
            problems.append('Olgularda olmayan sayı kullanıldı: ' + ', '.join(dict.fromkeys(bad)) + '. Yalnızca olgulardaki sayıları kullan.')
        if problems:
            raise ValueError('; '.join(problems))
        return {'title': title, 'summary': summary, 'findings': findings, 'actions': actions, 'questions': questions}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir finans ve satış analistisin. Sana kodla hesaplanmış, numaralı olgular verilecek. Bunlardan yönetime kısa bir dönem '
          'yorumu yaz: ne değişti, değişimi en çok ne sürükledi, neye bakılmalı. YALNIZCA olgulardaki sayıları kullan; yeni sayı hesaplama, '
          'yuvarlarken en fazla bir ondalık bırak. Her bulgunun kanit listesine dayandığı olgu numaralarını yaz (ör. ["F1", "F3"]). '
          'Nedenini bilmediğin bir değişim için neden uydurma; soruyu sorular listesine ekle. '
          'Yanıt şeması: {"baslik": "...", "ozet": "2-3 cümle", "bulgular": [{"metin": "...", "kanit": ["F1"]}], '
          '"aksiyonlar": ["..."], "sorular": ["..."]}')


def markdown(result, fact_list, current, prev, meta, source, skipped):
    lines = [f"# {result['title'] or 'Dönem yorumu'}", '', f'_{source} · {current} ↔ {prev}_', '', result['summary'], '', '## Bulgular', '']
    lines += [f"- {f['text']} _({', '.join(f['evidence'])})_" for f in result['findings']]
    if result['actions']:
        lines += ['', '## Önerilen aksiyonlar', ''] + [f'- {a}' for a in result['actions']]
    if result['questions']:
        lines += ['', '## Açık sorular', ''] + [f'- {q}' for q in result['questions']]
    lines += ['', '## Kanıt (kodla hesaplandı)', '', md_table(['No', 'Olgu'], [[f['id'], f['olgu']] for f in fact_list])]
    if skipped:
        lines.append(f'> {skipped} satır tarih veya tutar okunamadığı için hesaba katılmadı.\n')
    lines.append(f"_Model: {meta['model']} · Metindeki her sayı olgularla karşılaştırıldı. Nedensel yorumlar modelin taslağıdır._")
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description='Son dönemi öncekiyle karşılaştırıp yönetim yorumu yazan agent (sayılar kodla hesaplanır).')
    p.add_argument('--input', default=str(here('ornek', 'gunluk_satis.csv')))
    p.add_argument('--sheet')
    p.add_argument('--date', default='tarih', help='Tarih sütunu')
    p.add_argument('--measure', default='ciro', help='Toplanacak sayısal sütun')
    p.add_argument('--dims', default='bolge,kanal', help='Kırılım sütunları (virgülle, en fazla 4)')
    p.add_argument('--period', choices=sorted(PERIODS), default='ay')
    p.add_argument('--current', help='Yorumlanacak dönem, örn. 2026-09 (varsayılan son dönem)')
    p.add_argument('--context', help='İsteğe bağlı iş bağlamı (kampanya, fiyat değişikliği…)')
    llm.add_arguments(p)
    args = p.parse_args()
    dims = [d.strip() for d in args.dims.split(',') if d.strip()]
    if len(dims) > 4:
        raise ValueError('En fazla 4 kırılım sütunu verin.')
    columns, rows = tabular.read(args.input, args.sheet)
    totals, by_dim, skipped = aggregate(columns, rows, args.date, args.measure, dims, args.period)
    current, prev, fact_list = facts(totals, by_dim, args.measure, args.current)
    out = output_dir(args.output)
    tabular.write_csv(out / 'olgular.csv', ['id', 'grup', 'olgu'], fact_list)
    write_json(out / 'olgular.json', {'source': Path(args.input).name, 'current': current, 'previous': prev, 'skipped_rows': skipped, 'facts': fact_list,
                                      'generated': datetime.now().isoformat(timespec='seconds')})
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    labels = set(totals)
    user = (f"Ölçü: {args.measure} · Dönem tipi: {args.period} · Yorumlanacak dönem: {current} (önceki: {prev})\n\nOlgular:\n"
            + '\n'.join(f"{f['id']}: {f['olgu']}" for f in fact_list) + f"\n\nİş bağlamı:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\"")
    result, meta = llm.ask(args, out, SYSTEM, user, validator(fact_list, labels))
    if result is None:
        return
    write_text(out / 'donem-yorumu.md', markdown(result, fact_list, current, prev, meta, Path(args.input).name, skipped))
    print(f"[OK] {current} ↔ {prev}: {len(result['findings'])} bulgu, {len(fact_list)} olgu. Çıktı: {out / 'donem-yorumu.md'}")


if __name__ == '__main__':
    llm.run(main)
