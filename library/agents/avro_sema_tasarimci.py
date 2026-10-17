"""Avro şema tasarımcısı (AI agent): JSON Lines olay örneklerinin alan profilini (tipler, bulunma oranı, örnek değer biçimi) yerelde
çıkarır; model alan açıklamaları, isimlendirme, opsiyonel alanlar (null birleşimi ve varsayılan değer), enum ve iç içe kayıtlarla bir
Avro şeması önerir. Şema yerelde iki aşamada denetlenir: Avro kurallarına uygunluk (record/fields/name/type, birleşimde null önce ve
varsayılan null, enum sembolleri, tekil alan adları) ve TÜM örnek olayların şemaya uyması. Uymayan olay ve alan, düzeltme için modele
geri gönderilir. Değerler maskelenerek gider. Çıktı: sema.avsc, sema.md."""
import argparse, json, re
from collections import Counter, defaultdict
from pathlib import Path
import llm
from common import here, output_dir, write_json, write_text, md_table
from akis import collect, load_events

PRIMITIVE = {'null', 'boolean', 'int', 'long', 'float', 'double', 'string', 'bytes'}
NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def check_schema(s, path, problems, names):
    """Structural Avro checks (subset of the spec that event schemas use)."""
    if isinstance(s, str):
        if s not in PRIMITIVE and s not in names:
            problems.append(f'{path}: bilinmeyen tip "{s}"')
        return
    if isinstance(s, list):
        if len(s) != len(set(json.dumps(x, sort_keys=True) for x in s)):
            problems.append(f'{path}: birleşimde tekrar eden tip')
        for i, x in enumerate(s):
            if isinstance(x, list):
                problems.append(f'{path}: birleşim içinde birleşim olamaz')
            check_schema(x, f'{path}[{i}]', problems, names)
        return
    if not isinstance(s, dict) or 'type' not in s:
        problems.append(f'{path}: tip tanımı geçersiz')
        return
    t = s['type']
    if t == 'record':
        if not NAME.match(str(s.get('name', ''))):
            problems.append(f'{path}: record adı geçersiz')
        names.add(str(s.get('name')))
        fields = s.get('fields')
        if not isinstance(fields, list) or not fields:
            problems.append(f'{path}: fields listesi boş')
            return
        seen = set()
        for f in fields:
            if not isinstance(f, dict) or not NAME.match(str(f.get('name', ''))):
                problems.append(f'{path}: alan adı geçersiz ({f.get("name") if isinstance(f, dict) else f})')
                continue
            if f['name'] in seen:
                problems.append(f'{path}.{f["name"]}: alan iki kez tanımlı')
            seen.add(f['name'])
            if 'type' not in f:
                problems.append(f'{path}.{f["name"]}: type yok')
                continue
            ft = f['type']
            if isinstance(ft, list) and 'null' in ft:
                if ft[0] != 'null' or f.get('default', 'YOK') is not None:
                    problems.append(f'{path}.{f["name"]}: opsiyonel alanda birleşim "null" ile başlamalı ve default null olmalı')
            check_schema(ft, f'{path}.{f["name"]}', problems, names)
    elif t == 'enum':
        syms = s.get('symbols')
        if not NAME.match(str(s.get('name', ''))) or not isinstance(syms, list) or not syms or not all(NAME.match(str(x)) for x in syms) or len(set(syms)) != len(syms):
            problems.append(f'{path}: enum adı veya sembolleri geçersiz (semboller [A-Za-z_][A-Za-z0-9_]* ve tekil olmalı)')
        names.add(str(s.get('name')))
    elif t == 'array':
        check_schema(s.get('items'), f'{path}[]', problems, names)
    elif t == 'map':
        check_schema(s.get('values'), f'{path}{{}}', problems, names)
    elif t in PRIMITIVE:
        lt = s.get('logicalType')
        if lt in ('timestamp-millis', 'timestamp-micros') and t != 'long' or lt == 'date' and t != 'int' or lt == 'decimal' and t not in ('bytes',):
            problems.append(f'{path}: logicalType {lt} bu tiple ({t}) kullanılamaz')
    else:
        problems.append(f'{path}: bilinmeyen tip "{t}"')


def conforms(v, s, named):
    """Does a JSON value fit the Avro type? (JSON encoding of events, not Avro JSON encoding of unions.)"""
    if isinstance(s, list):
        return any(conforms(v, x, named) for x in s)
    if isinstance(s, str):
        s = named.get(s, {'type': s})
    t = s['type']
    if t == 'null':
        return v is None
    if t == 'boolean':
        return isinstance(v, bool)
    if t in ('int', 'long'):
        if s.get('logicalType', '').startswith('timestamp') or s.get('logicalType') == 'date':
            return isinstance(v, int) and not isinstance(v, bool)
        return isinstance(v, int) and not isinstance(v, bool) and (t == 'long' or -2 ** 31 <= v < 2 ** 31)
    if t in ('float', 'double'):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t in ('string', 'bytes'):
        return isinstance(v, str)
    if t == 'enum':
        return v in s.get('symbols', [])
    if t == 'array':
        return isinstance(v, list) and all(conforms(x, s['items'], named) for x in v)
    if t == 'map':
        return isinstance(v, dict) and all(conforms(x, s['values'], named) for x in v.values())
    if t == 'record':
        if not isinstance(v, dict):
            return False
        names = {f['name'] for f in s['fields']}
        if set(v) - names:
            return False
        return all(conforms(v.get(f['name']), f['type'], named) if f['name'] in v else 'default' in f for f in s['fields'])
    return False


def first_mismatch(v, s, named, path=''):
    """Human-readable reason for the first field that does not fit."""
    if isinstance(s, dict) and s.get('type') == 'record' and isinstance(v, dict):
        names = {f['name']: f for f in s['fields']}
        for k in v:
            if k not in names:
                return f'{path}{k}: şemada yok'
        for f in s['fields']:
            if f['name'] not in v:
                if 'default' not in f:
                    return f'{path}{f["name"]}: olayda yok ve default tanımsız'
            elif not conforms(v[f['name']], f['type'], named):
                inner = first_mismatch(v[f['name']], f['type'], named, f'{path}{f["name"]}.')
                return inner or f'{path}{f["name"]}: {json.dumps(f["type"], ensure_ascii=False)[:60]} tipine uymayan değer (ör. {json.dumps(v[f["name"]], ensure_ascii=False)[:40]})'
    return ''


def named_types(s, acc):
    if isinstance(s, dict):
        if s.get('type') in ('record', 'enum') and s.get('name'):
            acc[s['name']] = s
        for f in s.get('fields', []) if s.get('type') == 'record' else []:
            named_types(f.get('type'), acc)
        for k in ('items', 'values'):
            named_types(s.get(k), acc)
    elif isinstance(s, list):
        for x in s:
            named_types(x, acc)
    return acc


def validator(events):
    def validate(obj):
        schema = obj.get('sema')
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except json.JSONDecodeError:
                raise ValueError('"sema" geçerli JSON değil.') from None
        if not isinstance(schema, dict) or schema.get('type') != 'record':
            raise ValueError('"sema" en üstte bir Avro record olmalı.')
        problems = []
        check_schema(schema, schema.get('name', 'kok'), problems, set())
        if problems:
            raise ValueError('Avro kuralları: ' + '; '.join(problems[:10]))
        named = named_types(schema, {})
        bad = [(i, first_mismatch(e, schema, named) or 'uymuyor') for i, e in enumerate(events, start=1) if not conforms(e, schema, named)]
        if bad:
            reasons, example = Counter(), {}
            for _, r in bad:
                key = r.split(' (ör. ')[0]
                reasons[key] += 1
                example.setdefault(key, r[len(key):])
            raise ValueError(f'{len(bad)}/{len(events)} örnek olay şemaya uymuyor. En sık nedenler: ' + '; '.join(f'{k}{example[k]} — {n} olay' for k, n in reasons.most_common(5)))
        return {'schema': schema, 'notes': [str(x) for x in obj.get('notlar', []) if x]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri platformu mimarısın. Verilen olay alan profilinden Kafka/Schema Registry için bir Avro şeması tasarla. Her alana doc yaz. '
          'Her olayda bulunmayan veya null gelen alanları ["null", tip] birleşimi ve "default": null ile tanımla. Zaman damgası ISO metinse string bırak '
          '(logicalType ancak sayısal epoch alanlarında). Düşük kardinaliteli alanları ancak tüm değerleri geçerli Avro sembolüyse enum yap. '
          'İç içe nesneler için iç içe record kullan. Tüm örnek olaylar şemaya uymak zorunda. '
          'Yanıt şeması: {"sema": {"type": "record", "name": "...", "namespace": "...", "doc": "...", "fields": [...]}, "notlar": ["..."]}')


def profile_lines(events):
    stats = defaultdict(lambda: {'types': Counter(), 'count': 0, 'values': Counter()})
    for e in events:
        collect(e, '', stats)
    lines = []
    for path, s in sorted(stats.items()):
        parent = path.rsplit('.', 1)[0] if '.' in path else ''
        total = len(events) if not parent else stats[parent]['types']['object']
        vals = [v for v in s['values'] if isinstance(v, (str, int, float))]
        sample = ', '.join(str(v)[:30] for v in vals[:5]) if len(s['values']) <= 30 else f"{', '.join(str(v)[:30] for v in vals[:3])} … (30+ farklı)"
        lines.append(f"- {path}: tipler {dict(s['types'])}; bulunma %{round(100 * s['count'] / total, 1)}; örnek: {sample}")
    return lines


def main():
    p = argparse.ArgumentParser(description='Olay örneklerinden tüm örneklerin uyduğu Avro şeması tasarlayan agent.')
    p.add_argument('--input', default=str(here('ornek', 'olaylar.jsonl')))
    p.add_argument('--name', default='', help='Kayıt adı önerisi (isteğe bağlı)')
    p.add_argument('--namespace', default='', help='Namespace önerisi (ör. com.firma.siparis)')
    llm.add_arguments(p)
    args = p.parse_args()
    events, bad = load_events(args.input)
    out = output_dir(args.output)
    user = (f"{len(events)} örnek olay{f' ({bad} bozuk satır atlandı)' if bad else ''}. İstenen ad: {args.name or 'sen öner'}, namespace: {args.namespace or 'sen öner'}.\n"
            'Alan profili:\n' + '\n'.join(profile_lines(events)))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(events))
    if result is None:
        return
    schema = result['schema']
    write_text(out / 'sema.avsc', json.dumps(schema, ensure_ascii=False, indent=2) + '\n')

    def rows(s, prefix=''):
        for f in s.get('fields', []):
            t = f['type']
            yield [prefix + f['name'], json.dumps(t, ensure_ascii=False) if not isinstance(t, dict) or t.get('type') != 'record' else 'record', 'evet' if 'default' in f else '', f.get('doc', '')]
            inner = t if isinstance(t, dict) else next((x for x in t if isinstance(x, dict)), None) if isinstance(t, list) else None
            if isinstance(inner, dict) and inner.get('type') == 'record':
                yield from rows(inner, prefix + f['name'] + '.')
    md = [f"# Avro şeması — {schema.get('namespace', '')}.{schema.get('name', '')}", '', schema.get('doc', ''), '',
          md_table(['Alan', 'Tip', 'Opsiyonel', 'Açıklama'], list(rows(schema))), '',
          f'Yerelde doğrulandı: {len(events)} örnek olayın tamamı şemaya uyuyor.']
    if result['notes']:
        md += ['', '## Notlar', ''] + [f'- {n}' for n in result['notes']]
    md += ['', f"_Model: {meta['model']} · Kayda almadan önce mevcut sürümle uyumluluğu sema-uyumlulugu bloğuyla kontrol edin._"]
    write_text(out / 'sema.md', '\n'.join(md))
    print(f"[OK] Avro şeması {len(events)} örnek olayın tamamıyla doğrulandı. Çıktı: {out / 'sema.avsc'}")


if __name__ == '__main__':
    llm.run(main)
