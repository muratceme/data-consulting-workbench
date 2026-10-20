"""Autovacuum ayarlayıcı (AI agent): PostgreSQL tablolarının satır sayısı ve günlük değişim hızından (pg_stat_user_tables’tan alınabilir) tablo
başına autovacuum_vacuum_scale_factor / threshold ve autovacuum_analyze_scale_factor önerir. Modele tablo adları ve sayılar gider. Öneri kodla
denetlenir: vacuum tetikleme eşiği (threshold + scale_factor × canlı satır) günlük ölü satır sayısını aşmamalı (tablo en az günde bir
temizlenmeli), ama saatlik ölü satırın altına da inmemeli (sürekli vacuum yükü); analyze eşiği günlük değişimi aşmamalı; scale_factor 0–0,2,
threshold 50–1.000.000 olmalı. Çıktı: autovacuum.sql (ALTER TABLE … SET), autovacuum_plani.csv, autovacuum.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_text, md_table


def validator(tables):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('tablolar') or []:
            t = str(e.get('tablo', '')).strip()
            seen.add(t)
            if t not in tables:
                problems.append(f'tablo “{t}” yok')
                continue
            s = tables[t]
            try:
                sf, th, asf = float(e.get('vacuum_scale_factor')), int(e.get('vacuum_threshold')), float(e.get('analyze_scale_factor'))
            except (TypeError, ValueError):
                problems.append(f'{t}: vacuum_scale_factor, vacuum_threshold, analyze_scale_factor sayı olmalı')
                continue
            why = []
            if not 0 <= sf <= 0.2 or not 0 <= asf <= 0.2:
                why.append('scale_factor 0–0,2 arasında olmalı')
            if not 50 <= th <= 1_000_000:
                why.append('threshold 50–1.000.000 arasında olmalı')
            trigger = th + sf * s['canli']
            if s['olu_gun'] >= 50 and trigger > s['olu_gun']:  # tiny tables: the minimum threshold is already enough
                why.append(f'vacuum eşiği {trigger:,.0f} ölü satır, günlük ölü satır {s["olu_gun"]:,.0f}; tablo günde bir kez bile temizlenmez')
            if s['olu_gun'] >= 24 * 50 and trigger < s['olu_gun'] / 24:
                why.append(f'vacuum eşiği {trigger:,.0f}, saatlik ölü satır {s["olu_gun"] / 24:,.0f}; saatte birden sık vacuum gereksiz yük')
            atrig = 50 + asf * s['canli']
            if s['degisim_gun'] >= 50 and atrig > s['degisim_gun']:
                why.append(f'analyze eşiği {atrig:,.0f}, günlük değişim {s["degisim_gun"]:,.0f}; istatistikler günlerce eskir')
            if why:
                problems.append(f'{t}: ' + '; '.join(why))
            out[t] = {'sf': sf, 'th': th, 'asf': asf, 'trigger': trigger, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [t for t in tables if t not in seen]
        if missing:
            problems.append('ayarı yazılmayan tablo: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir PostgreSQL DBA’sın. Her tablo için autovacuum ayarı öner: büyük ve sık güncellenen tablolarda varsayılan scale_factor (0,2) çok geç '
          'tetikler; tetikleme eşiği (threshold + scale_factor × canlı satır) günlük ölü satırın altında ama saatlik ölü satırın üstünde olmalı. Analyze eşiği '
          '(50 + analyze_scale_factor × canlı satır) günlük değişimin altında olmalı. Yanıt şeması: {"tablolar": [{"tablo": "...", "vacuum_scale_factor": 0.01, '
          '"vacuum_threshold": 1000, "analyze_scale_factor": 0.005, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Tablo istatistiklerinden autovacuum ayarı öneren ve tetikleme sıklığını kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'pg_tablo_istatistikleri.csv')), help='tablo, canli_satir, gunluk_olu_satir, gunluk_degisim')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('tablo', 'canli_satir', 'gunluk_olu_satir', 'gunluk_degisim'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    num = lambda v: float(v.replace('.', '').replace(',', '.')) if ',' in v else float(v)
    tables = {r['tablo'].strip(): {'canli': num(r['canli_satir']), 'olu_gun': num(r['gunluk_olu_satir']), 'degisim_gun': num(r['gunluk_degisim'])} for r in rows}
    out = output_dir(args.output)
    user = 'Tablolar (canlı satır · günlük ölü satır (UPDATE+DELETE) · günlük değişim (INSERT+UPDATE+DELETE) · varsayılanla vacuum eşiği):\n' + '\n'.join(
        f'- {t}: {s["canli"]:,.0f} · {s["olu_gun"]:,.0f} · {s["degisim_gun"]:,.0f} · {50 + 0.2 * s["canli"]:,.0f}' for t, s in tables.items())
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables))
    if result is None:
        return
    sql = ['-- PostgreSQL · tablo başına autovacuum ayarları (autovacuum-ayarlayici agent’ı)']
    sql += [f'ALTER TABLE {t} SET (autovacuum_vacuum_scale_factor = {a["sf"]:g}, autovacuum_vacuum_threshold = {a["th"]}, '
            f'autovacuum_analyze_scale_factor = {a["asf"]:g});' for t, a in result.items()]
    write_text(out / 'autovacuum.sql', '\n'.join(sql))
    rows_out = [{'tablo': t, 'scale_factor': a['sf'], 'threshold': a['th'], 'analyze_scale_factor': a['asf'], 'tetik_olu_satir': round(a['trigger']),
                 'gunde_tahmini_vacuum': round(tables[t]['olu_gun'] / a['trigger'], 1) if a['trigger'] else '', 'gerekce': a['gerekce']} for t, a in result.items()]
    tabular.write_csv(out / 'autovacuum_plani.csv', list(rows_out[0]), rows_out)
    md = ['# Autovacuum ayarları', '', f'{len(rows_out)} tablo; her tablo günde en az bir, saatte en fazla bir kez vacuum’lanacak şekilde ayarlandı.', '',
          md_table(['Tablo', 'Scale factor', 'Threshold', 'Analyze SF', 'Tetik (ölü satır)', 'Günde vacuum', 'Gerekçe'], [list(r.values()) for r in rows_out]), '',
          f'_Model: {meta["model"]} · Tetikleme eşikleri günlük/saatlik ölü satır sayısıyla kodla denetlendi. autovacuum.sql PostgreSQL’de çalıştırılarak test edilmedi; '
          'uzun süren işlemler (idle in transaction) vacuum’u yine engelleyebilir._']
    write_text(out / 'autovacuum.md', '\n'.join(md))
    print(f'[OK] {len(rows_out)} tablo. Çıktı: {out / "autovacuum.md"}')


if __name__ == '__main__':
    llm.run(main)
