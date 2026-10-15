"""Qlik load script soy ağacı: .qvs (veya uygulamadan kopyalanan script) dosyasından bağlantıları, değişkenleri,
tabloları, kaynaklarını (dosya, SQL, RESIDENT, INLINE), JOIN/CONCATENATE hedeflerini, STORE çıktılarını ve DROP
adımlarını çıkarır; Mermaid akış şeması üretir. Migrasyon ve dokümantasyon içindir. İnternete bağlanmaz.
Çıktı: qlik-soyagaci.md, lineage.json."""
import argparse, re
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table

PREFIX = re.compile(r'^(?:(NOCONCATENATE|MAPPING|GENERIC|BUFFER(?:\s*\([^)]*\))?|FIRST\s+\d+|CROSSTABLE\s*\([^)]*\)|HIERARCHY\w*\s*\([^)]*\))\s+'
                    r'|(CONCATENATE|(?:LEFT\s+|RIGHT\s+|INNER\s+|OUTER\s+)?(?:JOIN|KEEP))\s*(?:\(\s*\[?([^)\]]+)\]?\s*\))?\s+)', re.I)
LABEL = re.compile(r'^(\[[^\]]+\]|"[^"]+"|[\w.\-]+)\s*:\s*(?!//)', re.U)


def strip_comments(text):
    out, i, n = [], 0, len(text)
    quote = None
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', '['):
            quote = ']' if ch == '[' else ch
            out.append(ch)
            i += 1
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = n if end < 0 else end + 2
        elif text.startswith('//', i) and (i == 0 or text[i - 1] != ':'):
            end = text.find('\n', i)
            i = n if end < 0 else end
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


def statements(text):
    """Split on ; outside quotes/brackets. Returns (line_number, statement)."""
    result, buf, quote, line, start = [], [], None, 1, 1
    for ch in text:
        if ch == '\n':
            line += 1
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"', '['):
            quote = ']' if ch == '[' else ch
        if ch == ';':
            s = ''.join(buf).strip()
            if s:
                result.append((start, s))
            buf, start = [], line
            continue
        if not buf and ch.isspace():
            start = line + (0 if ch != '\n' else 0)
            continue
        if not buf:
            start = line
        buf.append(ch)
    s = ''.join(buf).strip()
    if s:
        result.append((start, s))
    return result


def clean(name):
    return (name or '').strip().strip('[]"\'').strip()


def source_of(stmt):
    m = re.search(r'\bRESIDENT\s+(\[[^\]]+\]|"[^"]+"|[\w.\-]+)', stmt, re.I)
    if m:
        return 'resident', clean(m.group(1))
    if re.search(r'\bINLINE\s*[\[\'"]', stmt, re.I):
        return 'inline', 'INLINE'
    if re.search(r'\bAUTOGENERATE\b', stmt, re.I):
        return 'autogenerate', 'AUTOGENERATE'
    if re.match(r'^\s*(SQL\s+)?SELECT\b', stmt, re.I) or re.match(r'^\s*SQL\b', stmt, re.I):
        tables = re.findall(r'\b(?:FROM|JOIN)\s+((?:"[^"]+"|\[[^\]]+\]|[\w$#]+)(?:\.(?:"[^"]+"|\[[^\]]+\]|[\w$#]+))*)', stmt, re.I)
        return 'sql', ', '.join(dict.fromkeys(clean(t).replace('"', '') for t in tables)) or 'SQL'
    m = re.search(r'\bFROM\s+(\[[^\]]+\]|\'[^\']+\'|"[^"]+"|\S+)', stmt, re.I)
    if m:
        return 'file', clean(m.group(1))
    return None, None


def parse(text):
    text = strip_comments(text)
    result = {'connections': [], 'includes': [], 'variables': [], 'tables': [], 'stores': [], 'drops': [], 'renames': [], 'mappings_used': [], 'warnings': []}
    tables = {}
    pending = None  # preceding LOAD waiting for its source in the next statement
    for line, stmt in statements(text):
        for inc in re.findall(r'\$\((?:Must_)?Include\s*=\s*([^)]+)\)', stmt, re.I):
            result['includes'].append(inc.strip())
        body = re.sub(r'\$\((?:Must_)?Include\s*=[^)]+\)', '', stmt, flags=re.I).strip()
        if not body:
            continue
        upper = body.upper()
        m = re.match(r'^(SET|LET)\s+([\w.]+)\s*=\s*(.*)$', body, re.I | re.S)
        if m:
            result['variables'].append({'name': m.group(2), 'kind': m.group(1).upper(), 'value': m.group(3).strip()[:200], 'line': line})
            continue
        m = re.match(r'^LIB\s+CONNECT\s+TO\s+(.+)$', body, re.I | re.S)
        if m:
            result['connections'].append(clean(m.group(1)))
            continue
        m = re.match(r'^(?:ODBC|OLEDB)\s+CONNECT\w*\s+TO\s+(\[[^\]]+\]|\S+)', body, re.I)
        if m:
            result['connections'].append(clean(m.group(1)))
            result['warnings'].append(f'{line}. satır: kodda doğrudan ODBC/OLEDB bağlantısı; LIB CONNECT tercih edin, kimlik bilgisi script’te olmamalı.')
            continue
        m = re.match(r'^STORE\s+(?:\*\s+FROM\s+)?(\[[^\]]+\]|"[^"]+"|[\w.\-]+)\s+INTO\s+(\[[^\]]+\]|\'[^\']+\'|\S+)', body, re.I)
        if m:
            result['stores'].append({'table': clean(m.group(1)), 'target': clean(m.group(2)), 'line': line})
            if clean(m.group(1)) in tables:
                tables[clean(m.group(1))]['stored_to'].append(clean(m.group(2)))
            continue
        m = re.match(r'^DROP\s+TABLES?\s+(.+)$', body, re.I | re.S)
        if m:
            for name in re.split(r'\s*,\s*', m.group(1).strip()):
                result['drops'].append(clean(name))
                if clean(name) in tables:
                    tables[clean(name)]['dropped'] = True
            continue
        m = re.match(r'^RENAME\s+TABLE\s+(\[[^\]]+\]|[\w.\-]+)\s+TO\s+(\[[^\]]+\]|[\w.\-]+)', body, re.I)
        if m:
            result['renames'].append({'from': clean(m.group(1)), 'to': clean(m.group(2))})
            continue
        if re.match(r'^(DROP\s+FIELDS?|QUALIFY|UNQUALIFY|TRACE|EXIT|SLEEP|CALL|SUB|END\s+SUB|IF|ELSE|END\s*IF|FOR|NEXT|DO|LOOP|SECTION|BINARY|DIRECTORY|ALIAS|COMMENT|TAG|UNTAG|MAP\s|UNMAP|SEARCH|LOOSEN|SEMANTIC|STAR\s)', body, re.I):
            if upper.startswith('BINARY'):
                result['tables'].append({'name': '(binary)', 'kind': 'binary', 'source': clean(body[6:]), 'prefix': '', 'target': '', 'line': line, 'stored_to': [], 'dropped': False, 'load_star': False})
            continue
        label = None
        lm = LABEL.match(body)
        if lm and not re.match(r'^(LOAD|SELECT|SQL)$', lm.group(1), re.I):
            label, body = clean(lm.group(1)), body[lm.end():].lstrip()
        prefix, target = '', ''
        while True:
            pm = PREFIX.match(body)
            if not pm:
                break
            word = (pm.group(1) or pm.group(2) or '').upper()
            prefix = (prefix + ' ' + re.sub(r'\s+', ' ', word.split('(')[0])).strip()
            if pm.group(3):
                target = clean(pm.group(3))
            body = body[pm.end():]
        if not re.match(r'^(LOAD|SELECT|SQL)\b', body, re.I):
            continue
        kind, src = source_of(body)
        load_star = bool(re.match(r'^LOAD\s+(DISTINCT\s+)?\*', body, re.I))
        if kind is None and re.match(r'^LOAD\b', body, re.I):
            pending = {'label': label, 'prefix': prefix, 'target': target, 'line': line, 'load_star': load_star}
            continue
        if pending and not label:
            label, prefix, target = pending['label'], pending['prefix'] or prefix, pending['target'] or target
            load_star, line = pending['load_star'] and load_star, pending['line']
        pending = None
        for mp in re.findall(r'ApplyMap\s*\(\s*\'([^\']+)\'', body, re.I):
            result['mappings_used'].append(mp)
        if 'JOIN' in prefix or 'KEEP' in prefix or 'CONCATENATE' == prefix.split(' ')[-1] and target:
            name = target or (result['tables'][-1]['name'] if result['tables'] else '?')
        else:
            name = label or (Path(src).stem if kind == 'file' and src else (src.split(',')[0].split('.')[-1] if kind == 'sql' else '(etiketsiz)'))
            if not label:
                result['warnings'].append(f'{line}. satır: etiketsiz tablo; Qlik adı kaynaktan türetir ({name}). Açık etiket kullanın.')
        if kind == 'file' and src and not re.match(r'^(lib://|\$\()', src, re.I):
            result['warnings'].append(f'{line}. satır: lib:// dışında dosya yolu ({src}); standart (güvenli) modda çalışmaz.')
        if kind == 'resident' and src not in tables:
            result['warnings'].append(f'{line}. satır: RESIDENT {src} bu noktada henüz yüklenmemiş (veya silinmiş); script hata verir.')
        if kind == 'inline':
            load_star = False
        entry = {'name': name, 'kind': kind, 'source': src, 'prefix': prefix, 'target': target, 'line': line,
                 'stored_to': [], 'dropped': False, 'load_star': load_star}
        result['tables'].append(entry)
        tables.setdefault(name, entry)
    if pending:
        result['warnings'].append(f"{pending['line']}. satır: kaynağı bulunamayan önceki (preceding) LOAD.")
    for t in result['tables']:
        t['dropped'] = t['name'] in result['drops'] or t['dropped']
        defining = not t['prefix'] or t['prefix'] in ('NOCONCATENATE', 'MAPPING')
        t['stored_to'] = [s['target'] for s in result['stores'] if s['table'] == t['name']] if defining else []
    return result


def mermaid(r):
    ids, lines = {}, ['flowchart LR']

    def node(label, shape):
        key = (label, shape)
        if key not in ids:
            ids[key] = f'n{len(ids)}'
            safe = label.replace('"', "'")
            lines.append(f'  {ids[key]}{shape[0]}"{safe}"{shape[1]}')
        return ids[key]
    for t in r['tables']:
        tid = node(t['name'], ('[', ']'))
        if t['kind'] == 'resident':
            lines.append(f"  {node(t['source'], ('[', ']'))} -->|resident| {tid}")
        elif t['kind'] in ('file', 'sql', 'binary'):
            for s in (t['source'] or '').split(', '):
                lines.append(f"  {node(s, ('[(', ')]'))} -->|{t['prefix'].lower() or t['kind']}| {tid}")
        for target in t['stored_to']:
            lines.append(f"  {tid} -->|store| {node(target, ('[/', '/]'))}")
    return '\n'.join(dict.fromkeys(lines))


def markdown(r, source):
    final = [t for t in r['tables'] if not t['dropped'] and 'JOIN' not in t['prefix'] and t['prefix'] != 'CONCATENATE' and 'MAPPING' not in t['prefix']]
    lines = [f'# Qlik script soy ağacı — {source}', '',
             f"- Yükleme adımı: **{len(r['tables'])}** · Uygulamada kalan tablo: **{len({t['name'] for t in final})}** · STORE: **{len(r['stores'])}** · Değişken: **{len(r['variables'])}**",
             f"- Bağlantılar: {', '.join(dict.fromkeys(r['connections'])) or '—'}", '']
    if r['includes']:
        lines += [f"- Include edilen dosyalar (içerikleri bu raporda yok): {', '.join(r['includes'])}", '']
    if r['warnings']:
        lines += ['## Uyarılar', ''] + [f'- {w}' for w in r['warnings']] + ['']
    lines += ['## Akış', '', '```mermaid', mermaid(r), '```', '',
              '## Yükleme adımları', '',
              md_table(['Satır', 'Tablo', 'Önek', 'Kaynak türü', 'Kaynak', 'STORE', 'DROP', 'LOAD *'],
                       [[t['line'], t['name'], t['prefix'], t['kind'], t['source'], ', '.join(t['stored_to']), 'evet' if t['dropped'] else '', 'evet' if t['load_star'] else ''] for t in r['tables']])]
    if r['variables']:
        lines += ['## Değişkenler', '', md_table(['Satır', 'Ad', 'Tür', 'Değer'], [[v['line'], v['name'], v['kind'], f"`{v['value']}`"] for v in r['variables']])]
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='Qlik load script soy ağacı (internet bağlantısı yok).')
    p.add_argument('--input', default=str(here('ornek', 'satis.qvs')), help='.qvs veya script metni (.txt)')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        raw = Path(args.input).read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1254')
        r = parse(text)
        if not r['tables']:
            raise ValueError('Script içinde LOAD/SELECT adımı bulunamadı.')
        out = output_dir(args.output)
        write_json(out / 'lineage.json', r)
        write_text(out / 'qlik-soyagaci.md', markdown(r, Path(args.input).name))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    print(f"[OK] {len(r['tables'])} yükleme adımı, {len(r['stores'])} STORE, {len(r['warnings'])} uyarı. Çıktı: {out / 'qlik-soyagaci.md'}")


if __name__ == '__main__':
    main()
