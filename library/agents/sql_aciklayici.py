"""SQL açıklayıcı (AI agent): devralınan .sql dosyalarını iş diliyle belgeler. Her ifadenin hedefini, kaynaklarını ve
risklerini yerelde kodla çıkarır (sql-soyagaci bloğu); model her ifadenin amacını ve iş kurallarını yazar. Model yanıtında
ters tırnak içinde geçen her tablo adı, kodun bulduğu tablolarla yerelde karşılaştırılır; olmayan tablo yanıtı reddettirir.
Hiçbir sorgu çalıştırılmaz. Çıktı: sql-aciklama.md, aciklama.json, lineage.json."""
import argparse, re
from pathlib import Path
import llm
from common import here, output_dir, write_json, write_text, md_table
from sql_soyagaci import analyse, collect, norm, summarise, mermaid

MAX_CHARS = 60000
MAX_STATEMENT = 6000


def strip_comments(text):
    out, i, quote = [], 0, False
    while i < len(text):
        ch = text[i]
        if ch == "'":
            quote = not quote
        if not quote and text.startswith('--', i):
            j = text.find('\n', i)
            i = len(text) if j < 0 else j
            continue
        if not quote and text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = len(text) if j < 0 else j + 2
            out.append(' ')
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def split_raw(text):
    """Split on ; and GO lines outside string literals; keeps literals (they often carry the business rule)."""
    parts, buf, quote = [], [], False
    for line in strip_comments(text).splitlines(keepends=True):
        if not quote and re.fullmatch(r'\s*GO\s*', line, re.I):
            parts.append(''.join(buf))
            buf = []
            continue
        for ch in line:
            if ch == "'":
                quote = not quote
            if ch == ';' and not quote:
                parts.append(''.join(buf))
                buf = []
            else:
                buf.append(ch)
    parts.append(''.join(buf))
    return [p.strip() for p in parts if p.strip()]


def statements(path):
    found = []
    for name, file in collect(path):
        for raw in split_raw(file.read_text(encoding='utf-8-sig', errors='replace')):
            info = analyse(raw, name)
            if not info:
                continue
            s = info[0]
            s['id'] = f'S{len(found) + 1}'
            s['sql'] = raw if len(raw) <= MAX_STATEMENT else raw[:MAX_STATEMENT] + '\n/* … kısaltıldı … */'
            found.append(s)
    if not found:
        raise ValueError('Açıklanacak SQL ifadesi bulunamadı (SELECT/INSERT/UPDATE/DELETE/MERGE/CREATE … AS).')
    return found


def validator(stmts):
    ids = [s['id'] for s in stmts]
    known = {t for s in stmts for t in [s['target'], *s['sources'], *s['ctes']] if t}
    short = {t.split('.')[-1] for t in known}

    def validate(obj):
        items = obj.get('ifadeler')
        if not isinstance(items, list):
            raise ValueError('"ifadeler" listesi yok.')
        by, problems = {}, []
        for x in items:
            if isinstance(x, dict) and str(x.get('no', '')).strip().upper() in ids:
                sid = str(x['no']).strip().upper()
                if sid in by:
                    problems.append(f'{sid} iki kez açıklandı')
                by[sid] = x
        missing = [i for i in ids if i not in by]
        if missing:
            problems.append('Şu ifadeler açıklanmadı: ' + ', '.join(missing))
        texts = [str(obj.get('genel_ozet', ''))]
        for sid, x in by.items():
            if not str(x.get('amac', '')).strip():
                problems.append(f'{sid} için "amac" boş')
            texts += [str(x.get('amac', ''))] + [str(r) for r in x.get('is_kurallari', []) or []] + [str(r) for r in x.get('dikkat', []) or []]
        invented = sorted({t for t in re.findall(r'`([\w.\[\]"]+)`', ' '.join(texts))
                           if '.' in t and norm(t) not in known and norm(t).split('.')[-1] not in short})
        if invented:
            problems.append('SQL’de olmayan tablo adı kullanıldı: ' + ', '.join(invented))
        if problems:
            raise ValueError('; '.join(problems))
        lst = lambda v: [str(r).strip() for r in (v if isinstance(v, list) else [v]) if str(r).strip()]
        return {'summary': str(obj.get('genel_ozet', '')).strip(),
                'items': [{'id': i, 'purpose': str(by[i]['amac']).strip(), 'rules': lst(by[i].get('is_kurallari', [])),
                           'notes': lst(by[i].get('dikkat', []))} for i in ids],
                'questions': lst(obj.get('sorular', []))}
    return validate


SYSTEM = (llm.SAFETY + ' Sen devralınan veri ambarı kodlarını belgeleyen kıdemli bir veri mühendisisin. Her SQL ifadesinin ne iş yaptığını '
          'iş kullanıcısının anlayacağı dille açıkla: amacı (1-2 cümle), içindeki iş kuralları (filtreler, CASE eşlemeleri, sabit değerler, '
          'birleştirme mantığı) ve dikkat edilecek noktalar. Tablo adlarını ters tırnak içinde ve SQL’de yazıldığı gibi yaz (ör. `mart.musteri_ozet`). '
          'SQL’de görmediğin bir şeyi kural gibi yazma; emin değilsen sorular listesine ekle. '
          'Yanıt şeması: {"genel_ozet": "betiklerin bütün olarak ne yaptığı", "ifadeler": [{"no": "S1", "amac": "...", '
          '"is_kurallari": ["..."], "dikkat": ["..."]}], "sorular": ["..."]}')


def markdown(stmts, result, groups, meta, source):
    by = {i['id']: i for i in result['items']}
    lines = [f'# SQL açıklaması — {source}', '', result['summary'], '',
             '## Akış', '', md_table(['Grup', 'Tablolar'], [['Ham kaynak', ', '.join(groups['raw_sources']) or '—'], ['Ara tablo', ', '.join(groups['intermediate']) or '—'],
                                                         ['Son hedef', ', '.join(groups['final_targets']) or '—']]),
             '```mermaid', mermaid(stmts), '```', '', '## İfadeler', '']
    for s in stmts:
        e = by[s['id']]
        lines += [f"### {s['id']} · {s['file']} · {s['kind']}{' → ' + s['target'] if s['target'] else ''}", '', e['purpose'], '',
                  f"- **Okuduğu tablolar (kod):** {', '.join(s['sources']) or '—'}"]
        if s['risks']:
            lines.append(f"- **Risk (kod):** {', '.join(s['risks'])}")
        lines += [f'- **İş kuralı:** {r}' for r in e['rules']] + [f'- **Dikkat:** {n}' for n in e['notes']] + ['']
    if result['questions']:
        lines += ['## Kod sahibine sorular', ''] + [f'- {q}' for q in result['questions']] + ['']
    lines.append(f"_Model: {meta['model']} · Tablo/kaynak/risk satırları kodla çıkarıldı; açıklamalar modelin taslağıdır, kod sahibiyle doğrulayın._")
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description='.sql dosyalarını iş diliyle belgeleyen agent (sorgu çalıştırılmaz).')
    p.add_argument('--input', default=str(here('ornek', 'sql')), help='.sql dosyası veya klasörü')
    p.add_argument('--context', help='İsteğe bağlı iş bağlamı dosyası')
    llm.add_arguments(p)
    args = p.parse_args()
    stmts = statements(args.input)
    body = '\n\n'.join(f"-- {s['id']} ({s['file']}, {s['kind']}{' → ' + s['target'] if s['target'] else ''}; okuduğu: {', '.join(s['sources']) or '—'})\n{s['sql']};"
                       for s in stmts)
    if len(body) > MAX_CHARS:
        raise ValueError(f'SQL metni {len(body):,} karakter; sınır {MAX_CHARS:,}. Daha az dosya verin (--input tek dosya veya alt klasör).'.replace(',', '.'))
    context = Path(args.context).read_text(encoding='utf-8-sig').strip() if args.context and Path(args.context).is_file() else ''
    out = output_dir(args.output)
    groups = summarise(stmts)
    write_json(out / 'lineage.json', {**groups, 'statements': [{k: v for k, v in s.items() if k != 'sql'} for s in stmts]})
    user = (f"{len(stmts)} ifade. Kodun çıkardığı akış: ham kaynak {', '.join(groups['raw_sources']) or '—'}; son hedef {', '.join(groups['final_targets']) or '—'}.\n\n"
            f"SQL:\n\"\"\"\n{body}\n\"\"\"\n\nİş bağlamı:\n\"\"\"\n{context or 'Verilmedi.'}\n\"\"\"")
    result, meta = llm.ask(args, out, SYSTEM, user, validator(stmts))
    if result is None:
        return
    write_json(out / 'aciklama.json', result)
    write_text(out / 'sql-aciklama.md', markdown(stmts, result, groups, meta, Path(args.input).name))
    print(f"[OK] {len(stmts)} ifade açıklandı, {len(result['questions'])} açık soru. Çıktı: {out / 'sql-aciklama.md'}")


if __name__ == '__main__':
    llm.run(main)
