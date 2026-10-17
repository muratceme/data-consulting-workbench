"""Bölümleme danışmanı (AI agent): bir lakehouse/ambar tablosunun örnek dışa aktarımından sütun profilini, toplam boyutu ve sorgu
filtrelerinde hangi sütunların ne sıklıkla kullanıldığını okur; model bölümleme (partition) sütunu ve ayrıntı düzeyi (gün, ay, yıl
veya değer) ile kümeleme (Z-order / clustering) sütunlarını önerir. Öneri yerelde ölçülür: sütunlar dosyada olmalı, tarih ayrıntısı
yalnızca tarih sütununda kullanılabilir, örnekteki farklı bölüm sayısı ve tahmini bölüm başına boyut sınırlar içinde kalmalı (çok
küçük bölüm = küçük dosya sorunu), kümeleme sütunu bölüm sütunuyla aynı olamaz ve en fazla dört olmalı. Satırlar gönderilmez.
Çıktı: bolumleme.md, bolumleme.json, ddl.sql."""
import argparse
from collections import Counter
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from veri_profili import profile
from veri_sozlugu import profile_text

GRAINS = ('gün', 'ay', 'yıl', 'değer', 'yok')


def bucket(v, grain):
    if grain == 'değer':
        return v.strip()
    d = tabular.parse_date(v)
    if not d:
        return None
    return d[0].strftime({'gün': '%Y-%m-%d', 'ay': '%Y-%m', 'yıl': '%Y'}[grain])


def validator(columns, rows, data, size_gb, min_gb, max_parts, filters):
    types = {c['name']: c['type'] for c in data['columns']}

    def validate(obj):
        problems = []
        col = str(obj.get('bolum_sutunu', '') or '').strip()
        grain = str(obj.get('ayrinti', 'yok')).strip().lower()
        clus = [str(x).strip() for x in obj.get('kume_sutunlari') or [] if str(x).strip()]
        if grain not in GRAINS:
            problems.append(f'ayrinti şunlardan biri olmalı: {", ".join(GRAINS)}')
        parts, per_gb = None, None
        if grain != 'yok':
            if col not in columns:
                problems.append(f'bölüm sütunu dosyada yok: {col or "?"}')
            elif grain in ('gün', 'ay', 'yıl') and types.get(col) != 'tarih':
                problems.append(f'{col} tarih değil ({types.get(col)}); {grain} ayrıntısı kullanılamaz')
            else:
                values = [b for b in (bucket(r[col], grain) for r in rows) if b]
                parts = len(set(values))
                per_gb = size_gb / parts if parts else None
                if parts > max_parts:
                    problems.append(f'örnekte {parts} bölüm oluşuyor; sınır {max_parts}. Daha kaba ayrıntı seç')
                elif per_gb is not None and per_gb < min_gb:
                    problems.append(f'bölüm başına tahmini {per_gb:.2f} GB; en az {min_gb} GB olmalı (küçük dosya sorunu). Daha kaba ayrıntı seç ya da bölümleme yapma')
        for c in clus:
            if c not in columns:
                problems.append(f'kümeleme sütunu dosyada yok: {c}')
            elif c == col and grain != 'yok':
                problems.append(f'{c} hem bölüm hem kümeleme sütunu olamaz')
        if len(clus) > 4:
            problems.append('en fazla 4 kümeleme sütunu')
        if problems:
            raise ValueError('; '.join(problems))
        unused = [c for c in clus if not filters.get(c)]
        return {'column': col if grain != 'yok' else '', 'grain': grain, 'cluster': clus, 'partitions': parts, 'per_gb': round(per_gb, 2) if per_gb else None,
                'summary': str(obj.get('gerekce', '')).strip(), 'unused_cluster': unused, 'risks': [str(x) for x in obj.get('riskler', []) if x]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir lakehouse performans mimarısın. Tablonun sütun profilinden, toplam boyutundan ve sorgu filtrelerindeki sütun kullanımından bölümleme ve '
          f'kümeleme öner. ayrinti: {", ".join(GRAINS)} ("yok" = bölümleme yapma; küçük tablolarda doğru cevap budur). Bölümleri çok küçük yapma; her bölüm en az '
          'verilen GB kadar olmalı. Kümeleme sütunlarını sık filtrelenen, yüksek kardinaliteli sütunlardan seç (en fazla 4). '
          'Yanıt şeması: {"bolum_sutunu": "...", "ayrinti": "ay", "kume_sutunlari": ["..."], "gerekce": "...", "riskler": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Tablo için ölçülerle doğrulanmış bölümleme ve kümeleme önerisi veren agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'satislar.csv')), help='Tablonun temsilî örnek dışa aktarımı')
    p.add_argument('--size-gb', type=float, default=120, help='Tablonun gerçek toplam boyutu (GB)')
    p.add_argument('--filters', default=str(here('ornek', 'sorgu_filtreleri.csv')), help='Sütun bazında filtre kullanım sayısı (sutun, kullanim)')
    p.add_argument('--min-partition-gb', type=float, default=1.0)
    p.add_argument('--max-partitions', type=int, default=10000)
    p.add_argument('--table', default='dwh.satis', help='DDL’deki tablo adı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    data = {'source': Path(args.input).name, **profile(columns, rows)}
    filters = Counter()
    if args.filters and Path(args.filters).is_file():
        fc, fr = tabular.read(args.filters)
        for r in fr:
            filters[r[fc[0]].strip()] += int(tabular.parse_number(r[fc[1]]) or 0)
    out = output_dir(args.output)
    user = (f"Tablo toplam boyutu: {args.size_gb} GB; bölüm başına en az {args.min_partition_gb} GB, en fazla {args.max_partitions} bölüm.\n"
            f"Sorgu filtrelerinde sütun kullanımı: {', '.join(f'{k}={v}' for k, v in filters.most_common()) or 'bilinmiyor'}\n\nSütun profili:\n{profile_text(data, include_values=False)}")
    result, meta = llm.ask(args, out, SYSTEM, user, validator(columns, rows, data, args.size_gb, args.min_partition_gb, args.max_partitions, filters))
    if result is None:
        return
    c, grain = result['column'], result['grain']
    gen = {'gün': ('gun', 'DATE', f'CAST({c} AS DATE)'), 'ay': ('ay', 'STRING', f"DATE_FORMAT({c}, 'yyyy-MM')"), 'yıl': ('yil', 'INT', f'YEAR({c})')}
    ddl = [f"-- Taslak (Delta Lake), model: {meta['model']}; bölüm sayısı yerelde ölçüldü", f'CREATE TABLE {args.table} (', '  -- mevcut sütunlar']
    if c and grain in gen:
        suffix, kind, expr = gen[grain]
        ddl += [f'  , {c}_{suffix} {kind} GENERATED ALWAYS AS ({expr})', ')', f'USING DELTA PARTITIONED BY ({c}_{suffix});']
    elif c:
        ddl += [')', f'USING DELTA PARTITIONED BY ({c});']
    else:
        ddl += [')', 'USING DELTA;']
    if result['cluster']:
        ddl.append(f"OPTIMIZE {args.table} ZORDER BY ({', '.join(result['cluster'])});")
    write_text(out / 'ddl.sql', '\n'.join(ddl) + '\n')
    write_json(out / 'bolumleme.json', result)
    md = ['# Bölümleme ve kümeleme önerisi', '', result['summary'], '',
          md_table(['Karar', 'Değer'], [['Bölüm sütunu', result['column'] or 'bölümleme yok'], ['Ayrıntı', result['grain']], ['Örnekte bölüm sayısı', result['partitions'] or '—'],
                                        ['Bölüm başına tahmini boyut (GB)', result['per_gb'] or '—'], ['Kümeleme sütunları', ', '.join(result['cluster']) or '—']])]
    if result['unused_cluster']:
        md += ['', f"Not: {', '.join(result['unused_cluster'])} sorgu filtrelerinde görünmüyor; kümelemeden fayda sınırlı olabilir."]
    if result['risks']:
        md += ['', '## Riskler', ''] + [f'- {r}' for r in result['risks']]
    md += ['', f"_Model: {meta['model']} · Bölüm sayısı örnek dosyadan ölçüldü; tam tabloda tarih aralığı daha genişse bölüm sayısı artar._"]
    write_text(out / 'bolumleme.md', '\n'.join(md))
    print(f"[OK] Bölüm: {result['column'] or 'yok'} ({result['grain']}), kümeleme: {', '.join(result['cluster']) or '—'}. Çıktı: {out / 'bolumleme.md'}")


if __name__ == '__main__':
    llm.run(main)
