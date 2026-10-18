"""Şema değişikliği etki analisti (AI agent): information_schema anlık görüntülerindeki son iki tarih arasında eklenen, silinen ve tipi
değişen kolonları bulur; SQL/dbt dosyalarını tarayarak her değişikliğin hangi sorguları etkilediğini KODLA belirler. Model yalnızca her
değişiklik için etki açıklaması ve yapılacak işleri yazar. Modelin seviyesi kodun hesapladığı en düşük seviyenin altına inemez (sorguda
kullanılan silinmiş kolon “kırıcı”, daraltılan ya da kullanılan kolonun tip değişikliği en az “riskli”); her değişiklik tam bir kez
anlatılmalı, etkilenen dosya listesi kodun bulduğuyla aynı olmalı (dosya uydurulamaz, atlanamaz), riskli/kırıcı değişiklikte yapılacak
iş boş olamaz. Modele SQL dosyalarının tamamı değil, yalnızca değişen kolonun geçtiği satırlar (maskelenerek) gider.
Çıktı: etki.csv, degisiklik-etkisi.md."""
import argparse, re
from collections import defaultdict
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_text, md_table, mask_text

LEVELS = ['bilgi', 'orta', 'riskli', 'kırıcı']


def type_parts(t):
    m = re.match(r'\s*([a-z ]+?)\s*(?:\(([^)]*)\))?\s*$', t.lower())
    if not m:
        return t.lower(), []
    return m.group(1).strip(), [int(x) for x in re.findall(r'\d+', m.group(2) or '')]


def narrowing(old, new):
    (a, na), (b, nb) = type_parts(old), type_parts(new)
    if a != b:
        return True  # a different base type may not hold every old value
    return any(y < x for x, y in zip(na, nb))


def drift(rows):
    snaps = defaultdict(dict)
    for r in rows:
        snaps[r['tarih'].strip()][(r['tablo'].strip(), r['sutun'].strip().lower())] = r['tip'].strip().lower()
    days = sorted(snaps)
    if len(days) < 2:
        raise ValueError('Karşılaştırma için en az iki tarihli anlık görüntü gerekir.')
    a, b = snaps[days[-2]], snaps[days[-1]]
    out = []
    for k in sorted(set(a) | set(b)):
        if k not in b:
            out.append((k[0], k[1], 'silindi', a[k], ''))
        elif k not in a:
            out.append((k[0], k[1], 'eklendi', '', b[k]))
        elif a[k] != b[k]:
            out.append((k[0], k[1], 'tip değişti', a[k], b[k]))
    return days[-2], days[-1], out


def references(sql_files, table, column):
    short = table.split('.')[-1]
    hits = {}
    for f, text in sql_files.items():
        low = text.lower()
        if not re.search(rf'\b{re.escape(short.lower())}\b', low):
            continue
        lines = [ln.strip() for ln in text.splitlines() if re.search(rf'\b{re.escape(column.lower())}\b', ln.lower())]
        if lines:
            hits[f] = lines
    return hits


def minimum(kind, old, new, refs):
    if kind == 'silindi':
        return 'kırıcı' if refs else 'orta'
    if kind == 'tip değişti':
        return 'riskli' if refs or narrowing(old, new) else 'orta'
    return 'bilgi'


def validator(changes):
    def validate(obj):
        problems, out = [], {}
        for e in obj.get('degisiklikler') or []:
            if not isinstance(e, dict):
                continue
            try:
                no = int(e.get('no'))
            except (TypeError, ValueError):
                problems.append('“no” sayı olmalı')
                continue
            if no not in changes:
                problems.append(f'{no} numaralı değişiklik yok')
                continue
            if no in out:
                problems.append(f'{no} iki kez anlatıldı')
                continue
            ch = changes[no]
            level = str(e.get('seviye', '')).strip().lower().replace('kirici', 'kırıcı')
            files = sorted({str(x).strip() for x in (e.get('dosyalar') or [])})
            actions = [str(a).strip() for a in (e.get('aksiyonlar') or []) if str(a).strip()]
            text = str(e.get('etki', '')).strip()
            why = []
            if level not in LEVELS:
                why.append(f'seviye {"/".join(LEVELS)} olmalı')
            elif LEVELS.index(level) < LEVELS.index(ch['min']):
                why.append(f'seviye en az {ch["min"]} olmalı ({ch["neden"]})')
            if files != sorted(ch['refs']):
                extra, miss = set(files) - set(ch['refs']), set(ch['refs']) - set(files)
                why.append('dosyalar uyuşmuyor' + (f'; bu değişikliği kullanmayan: {", ".join(sorted(extra))}' if extra else '')
                           + (f'; atlanan: {", ".join(sorted(miss))}' if miss else ''))
            if not text:
                why.append('etki açıklaması boş')
            if level in ('riskli', 'kırıcı') and not actions:
                why.append('riskli/kırıcı değişiklikte yapılacak iş yazılmalı')
            if why:
                problems.append(f'{no} ({ch["tablo"]}.{ch["sutun"]} {ch["tur"]}): ' + ', '.join(why))
            out[no] = {'seviye': level, 'etki': text, 'aksiyonlar': actions}
        missing = [str(n) for n in changes if n not in out]
        if missing:
            problems.append('anlatılmayan değişiklik: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        return out, str(obj.get('ozet', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri ambarı ekibinde değişiklik yönetiminden sorumlu veri mühendisisin. Kaynak şemadaki her değişiklik için hangi sorgu '
          've raporların nasıl etkileneceğini, seviyesini (bilgi, orta, riskli, kırıcı) ve yapılacak işleri yaz. Etkilenen dosyalar kodla bulundu; listeyi '
          'aynen kullan, dosya ekleme ya da çıkarma. Seviye verilen en düşük seviyenin altında olamaz. Silinen bir kolonla eklenen bir kolon aynı veriyi '
          'taşıyor olabilir (yeniden adlandırma); emin değilsen bunu kesin bilgi gibi yazma, doğrulanacak iş olarak yaz. '
          'Yanıt şeması: {"ozet": "...", "degisiklikler": [{"no": 1, "seviye": "kırıcı", "etki": "...", "aksiyonlar": ["..."], "dosyalar": ["a.sql"]}]}')


def main():
    p = argparse.ArgumentParser(description='Şema değişikliklerinin SQL dosyalarına etkisini kodla bulup modele etki notu yazdıran agent.')
    p.add_argument('--input', default=str(here('ornek', 'sema_anliklari.csv')), help='tarih, tablo, sutun, tip sütunlu anlık görüntüler')
    p.add_argument('--sql', default=str(here('ornek', 'etki_sql')), help='Taranacak .sql dosyaları klasörü (dbt models klasörü de olur)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('tarih', 'tablo', 'sutun', 'tip'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok. Sütunlar: {", ".join(columns)}')
    before, after, found = drift(rows)
    root = Path(args.sql)
    sql_files = {str(f.relative_to(root)).replace('\\', '/'): f.read_text(encoding='utf-8', errors='replace') for f in sorted(root.rglob('*.sql'))} if root.is_dir() else {}
    out = output_dir(args.output)
    if not found:
        write_text(out / 'degisiklik-etkisi.md', f'# Şema değişikliği etkisi\n\n{before} → {after} arasında değişiklik yok.\n')
        print(f'[OK] {before} → {after}: değişiklik yok; model çağrılmadı.')
        return
    changes = {}
    for i, (t, c, kind, old, new) in enumerate(found, 1):
        refs = references(sql_files, t, c)
        lvl = minimum(kind, old, new, refs)
        reason = {'kırıcı': 'silinen kolon sorgularda kullanılıyor', 'riskli': 'tip daraltıldı ya da değişen kolon sorgularda kullanılıyor',
                  'orta': 'sorgularda kullanılmıyor ama veri kaybı/uyum riski var', 'bilgi': 'yeni kolon'}[lvl]
        changes[i] = {'tablo': t, 'sutun': c, 'tur': kind, 'eski': old, 'yeni': new, 'refs': refs, 'min': lvl, 'neden': reason}
    user = [f'Şema anlık görüntüleri: {before} → {after}. {len(sql_files)} SQL dosyası tarandı.', '']
    for i, ch in changes.items():
        user.append(f'{i}. {ch["tablo"]}.{ch["sutun"]}: {ch["tur"]}' + (f' ({ch["eski"] or "—"} → {ch["yeni"] or "—"})') + f' · en düşük seviye: {ch["min"]}')
        for f, lines in ch['refs'].items():
            user.append(f'   - {f}: ' + ' | '.join(mask_text(ln)[0][:200] for ln in lines[:3]))
        if not ch['refs']:
            user.append('   - etkilenen dosya yok')
    result, meta = llm.ask(args, out, SYSTEM, '\n'.join(user), validator(changes))
    if result is None:
        return
    notes, summary = result
    table = [{'no': i, 'tablo': ch['tablo'], 'sutun': ch['sutun'], 'degisiklik': ch['tur'], 'eski_tip': ch['eski'], 'yeni_tip': ch['yeni'], 'seviye': notes[i]['seviye'],
              'en_dusuk_seviye': ch['min'], 'etkilenen_dosyalar': ';'.join(sorted(ch['refs'])), 'yapilacaklar': ' / '.join(notes[i]['aksiyonlar'])} for i, ch in changes.items()]
    tabular.write_csv(out / 'etki.csv', list(table[0]), table)
    order = sorted(changes, key=lambda i: -LEVELS.index(notes[i]['seviye']))
    md = ['# Şema değişikliği etkisi', '', f'{before} → {after}: {len(changes)} değişiklik, {len(sql_files)} SQL dosyası tarandı; '
          + ', '.join(f'{sum(notes[i]["seviye"] == lv for i in changes)} {lv}' for lv in reversed(LEVELS) if any(notes[i]['seviye'] == lv for i in changes)) + '.', '']
    if summary:
        md += [summary, '']
    md += [md_table(['#', 'Kolon', 'Değişiklik', 'Seviye', 'Etkilenen dosyalar'], [[t['no'], f'{t["tablo"]}.{t["sutun"]}', t['degisiklik'] + (f' ({t["eski_tip"]} → {t["yeni_tip"]})' if t['degisiklik'] == 'tip değişti' else ''),
                                                                                   t['seviye'], t['etkilenen_dosyalar'].replace(';', ', ') or '—'] for t in sorted(table, key=lambda t: order.index(t['no']))]), '']
    for i in order:
        ch = changes[i]
        md += [f'## {i}. {ch["tablo"]}.{ch["sutun"]} — {ch["tur"]} ({notes[i]["seviye"]})', '', notes[i]['etki'], '']
        md += [f'- [ ] {a}' for a in notes[i]['aksiyonlar']] + ['']
    md += [f'_Model: {meta["model"]} · Etkilenen dosyalar ve en düşük seviyeler kodla bulundu (tablo ve kolon adı eşleşmesi; takma adlı ya da dinamik SQL kaçabilir). '
           'Raporlama araçlarının (Power BI, Qlik) içindeki sorgular taranmadı._']
    write_text(out / 'degisiklik-etkisi.md', '\n'.join(md))
    print(f'[OK] {len(changes)} değişiklik; {sum(1 for t in table if t["etkilenen_dosyalar"])} tanesi SQL dosyalarını etkiliyor. Çıktı: {out / "degisiklik-etkisi.md"}')


if __name__ == '__main__':
    llm.run(main)
