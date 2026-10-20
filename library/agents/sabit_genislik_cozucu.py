"""Sabit genişlik çözücü (AI agent): alan tanımı kaybolmuş sabit genişlikli bir dosyanın (banka, SGK, ana bilgisayar dışa aktarımı) alan
düzenini çıkarır. Modele ilk satırlar cetvelle birlikte gider (rakamlar 9, harfler A olarak gösterilir; kişisel veri gönderilmez). Model
alanları (ad, başlangıç, uzunluk, tip) önerir; tanım sabit-genislik bloğunun kuralıyla dosyanın TAMAMINA uygulanır ve denetlenir: alanlar
çakışmamalı, hiçbir satırda boşluk olmayan bir karakter alan dışında kalmamalı, tarih/sayı alanlarında çevrilemeyen değer olmamalı, sayı
alanı harf içermemeli, adlar tekil snake_case olmalı. Çıktı: alan_tanimi.csv (sabit-genislik bloğunun --spec girdisi), sonuc.csv, sabit-genislik.md."""
import argparse, re
from pathlib import Path
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_text, md_table
from bicim import sabit, read_text


def shape(line):
    return ''.join('9' if c.isdigit() else 'A' if c.isalpha() else c for c in line)


def validator(path, encoding, lines, tmp):
    width = max(len(x) for x in lines)

    def validate(obj):
        fields, problems = [], []
        for i, f in enumerate(obj.get('alanlar') or [], 1):
            try:
                name, start, length, kind = str(f['alan']).strip(), int(f['baslangic']), int(f['uzunluk']), str(f.get('tip', 'metin')).strip() or 'metin'
            except (KeyError, TypeError, ValueError):
                problems.append(f'{i}. alan: alan, baslangic (1’den), uzunluk gerekli')
                continue
            if not re.fullmatch(r'[a-z][a-z0-9]*(_[a-z0-9]+)*', name) or name in [x[0] for x in fields]:
                problems.append(f'{name}: ad tekil snake_case olmalı')
            if start < 1 or length < 1 or start - 1 + length > width:
                problems.append(f'{name}: {start}+{length} satır genişliği ({width}) dışında')
            if not re.fullmatch(r'metin|tarih(:%[A-Za-z%]+)*|sayi(:\d)?', kind):
                problems.append(f'{name}: tip metin, tarih:%Y%m%d ya da sayi:N olmalı')
            fields.append((name, start, length, kind))
        if problems or not fields:
            raise ValueError('; '.join(problems) or 'alan yok')
        spans = sorted((s - 1, s - 1 + n, a) for a, s, n, _ in fields)
        overlap = [f'{a}–{b}' for (s1, e1, a), (s2, e2, b) in zip(spans, spans[1:]) if s2 < e1]
        if overlap:
            raise ValueError('çakışan alanlar: ' + ', '.join(overlap))
        covered = set()
        for s, e, _ in spans:
            covered |= set(range(s, e))
        loose = [(n, j) for n, line in enumerate(lines, 1) for j, ch in enumerate(line) if ch.strip() and j not in covered]
        if loose:
            cols = sorted({j + 1 for _, j in loose})
            problems.append(f'hiçbir alana girmeyen dolu karakterler: sütun {", ".join(map(str, cols[:12]))} ({len(loose)} karakter, ilk satır {loose[0][0]})')
        spec = tmp / 'alan_tanimi.csv'
        tabular.write_csv(spec, ['alan', 'baslangic', 'uzunluk', 'tip'], [dict(zip(('alan', 'baslangic', 'uzunluk', 'tip'), f)) for f in fields])
        res = sabit(SimpleNamespace(input=path, spec=str(spec), encoding=encoding))
        if res.summary['hatali_alan']:
            errs = res.tables[0][2]
            problems.append(f'{len(errs)} alan değeri çevrilemedi: ' + ', '.join(f'satır {r} {a} “{shape(v)}”' for r, a, v in errs[:5]))
        if problems:
            raise ValueError('; '.join(problems))
        return fields, res
    return validate


SYSTEM = (llm.SAFETY + ' Sen eski sistem dosya biçimlerini çözen bir veri mühendisisin. Sabit genişlikli satırların biçim görüntüsüne (9 rakam, A harf, '
          'boşluk ve diğer karakterler aynen) ve cetvele bakarak alanları çıkar: alan (snake_case), baslangic (1’den), uzunluk, tip (metin, tarih:%Y%m%d gibi '
          'biçim, sayi:N; N son N hanenin ondalık olduğu). Dolu her karakter bir alana girmeli, alanlar çakışmamalı. '
          'Yanıt şeması: {"alanlar": [{"alan": "hesap_no", "baslangic": 1, "uzunluk": 10, "tip": "metin"}]}')


def main():
    p = argparse.ArgumentParser(description='Tanımı olmayan sabit genişlikli dosyanın alan düzenini çıkaran agent; tanım tüm dosyada denenir.')
    p.add_argument('--input', default=str(here('ornek', 'eski_sistem_hareket.txt')))
    p.add_argument('--encoding', default='utf-8')
    p.add_argument('--preview', type=int, default=25)
    llm.add_arguments(p)
    args = p.parse_args()
    lines = [x for x in read_text(args.input, args.encoding).splitlines() if x.strip()]
    if not lines:
        raise ValueError('Dosya boş.')
    width = max(len(x) for x in lines)
    out = output_dir(args.output)
    ruler = ''.join(str((i // 10) % 10) if i % 10 == 0 else ' ' for i in range(1, width + 1)) + '\n' + ''.join(str(i % 10) for i in range(1, width + 1))
    lengths = sorted({len(x) for x in lines})
    user = (f'{len(lines)} satır; uzunluklar: {", ".join(map(str, lengths[:10]))}. Biçim görüntüsü (9 rakam, A harf):\n{ruler}\n'
            + '\n'.join(shape(x) for x in lines[:args.preview]))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(args.input, args.encoding, lines, out))
    if result is None:
        return
    fields, res = result
    tabular.write_csv(out / 'sonuc.csv', res.columns, res.rows)
    md = ['# Sabit genişlikli dosya düzeni', '', f'{len(lines)} satır {len(fields)} alana bölündü; dolu her karakter bir alanda, çevrilemeyen değer yok.', '',
          md_table(['Alan', 'Başlangıç', 'Uzunluk', 'Tip', 'Örnek'], [[a, s, n, k, res.rows[0].get(a, '')] for a, s, n, k in fields]), '',
          'alan_tanimi.csv dosyasını sabit-genislik kod bloğuna `--spec` olarak verip aynı dosyaları her gün çevirebilirsiniz.', '',
          f'_Model: {meta["model"]} · Modele yalnızca biçim görüntüsü gitti; tanım dosyanın tamamında kodla denendi. Alan adlarının anlamı kaynağın dokümanıyla doğrulanmalıdır._']
    write_text(out / 'sabit-genislik.md', '\n'.join(md))
    print(f'[OK] {len(fields)} alan, {len(lines)} satır. Çıktı: {out / "sabit-genislik.md"}')


if __name__ == '__main__':
    llm.run(main)
