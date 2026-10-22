"""Incremental strateji önerici (AI agent): dbt kaynak tablolarının yükleme geçmişinden (her yüklemede gelen satırlar) model başına
materialization (table/incremental), unique_key, incremental_strategy (append/merge/delete+insert) ve geriye bakış süresini önerir. Satır
sayısı, yükleme başına satır, yüklemeler arası tekrar eden kimlikler (güncelleme) ve geç gelme süreleri kodla çıkarılır; modele yalnızca
bu profil gider. Öneri kodla denetlenir: büyük tablo full refresh, küçük tablo incremental yapılmamalı; anahtar her yüklemede tekil olmalı;
seçilen strateji tüm yüklemeler üzerinde simüle edilir ve sonuç tablosu gerçek son durumla karşılaştırılır (yinelenen, eksik ya da eski
sürümde kalan satır kabul edilmez). Çıktı: models/*.sql (config + is_incremental filtresi), sources.yml, strateji.csv, incremental-strateji.md."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_text, md_table

STRATEGIES = ('append', 'merge', 'delete+insert')


def load_table(path, updated, load):
    columns, rows = tabular.read(path)
    for c in (updated, load):
        if c not in columns:
            raise ValueError(f'{path.name}: {c} sütunu yok.')
    for r in rows:
        r['_u'] = datetime.fromisoformat(r[updated].strip())
        r['_l'] = datetime.fromisoformat(r[load].strip())
    return columns, sorted(rows, key=lambda r: (r['_l'], r['_u']))


def latest(rows, key):
    out = {}
    for r in rows:
        k = tuple(r[c] for c in key)
        if k not in out or (r['_u'], r['_l']) >= (out[k]['_u'], out[k]['_l']):
            out[k] = r
    return out


def simulate(rows, key, strategy, lookback):
    """Replay every load as dbt would: first run builds from the source, later runs select rows newer than max(updated) - lookback."""
    loads = sorted({r['_l'] for r in rows})
    target, source = [], []
    for i, L in enumerate(loads):
        source += [r for r in rows if r['_l'] == L]
        if i == 0:
            target = list(latest(source, key).values()) if key else list(source)
            continue
        edge = max(r['_u'] for r in target) - lookback
        batch = [r for r in source if r['_u'] > edge]
        if strategy == 'append':
            target += batch
        else:
            keep = latest(batch, key)
            target = [r for r in target if tuple(r[c] for c in key) not in keep] + list(keep.values())
    return target


def check(rows, ident, target):
    truth = latest(rows, [ident])
    ids = [r[ident] for r in target]
    have = latest(target, [ident])
    dup = len(ids) - len(set(ids))
    missing = sum((k not in have) for k in truth)
    stale = sum(1 for k, r in truth.items() if k in have and (have[k]['_u'], have[k]['_l']) < (r['_u'], r['_l']))
    return dup, missing, stale


def validator(tables, min_rows):
    def validate(obj):
        seen = set()
        problems, out = [], {}
        for e in obj.get('modeller') or []:
            t = str(e.get('tablo', '')).strip()
            if t not in tables or t in seen:
                problems.append(f'tablo “{t}” yok ya da iki kez yazıldı')
                continue
            seen.add(t)
            info = tables[t]
            mat, strat = str(e.get('materialized', '')).strip(), str(e.get('strateji', '') or '').strip()
            key = [str(k).strip() for k in (e.get('unique_key') or [])] if isinstance(e.get('unique_key'), list) else [s.strip() for s in str(e.get('unique_key') or '').split(',') if s.strip()]
            try:
                lb = int(e.get('geriye_bakis_gun') or 0)
            except (TypeError, ValueError):
                problems.append(f'{t}: geriye_bakis_gun tam sayı olmalı')
                continue
            n = len(info['rows'])
            if mat not in ('table', 'incremental'):
                problems.append(f'{t}: materialized table ya da incremental olmalı')
                continue
            if mat == 'table' and n >= min_rows:
                problems.append(f'{t}: {n} satır ve her yüklemede büyüyor; full refresh (table) yerine incremental olmalı (eşik {min_rows})')
            if mat == 'incremental' and n < min_rows:
                problems.append(f'{t}: {n} satırlık küçük tablo; incremental karmaşıklığı gereksiz, table yeterli')
            out[t] = {'mat': mat, 'strat': strat, 'key': key, 'lb': lb, 'gerekce': str(e.get('gerekce', '')).strip()}
            if mat != 'incremental':
                continue
            if strat not in STRATEGIES:
                problems.append(f'{t}: strateji {", ".join(STRATEGIES)} olmalı')
                continue
            if strat != 'append':
                if not key or any(k not in info['columns'] for k in key):
                    problems.append(f'{t}: {strat} için unique_key tablonun sütunlarından olmalı')
                    continue
                dup_in_load = sum(len(v) - len({tuple(r[k] for k in key) for r in v}) for v in info['by_load'].values())
                if dup_in_load:
                    problems.append(f'{t}: ({", ".join(key)}) bir yüklemede {dup_in_load} kez tekrar ediyor; anahtar tekil değil')
                    continue
            dup, missing, stale = check(info['rows'], info['ident'], simulate(info['rows'], key, strat, timedelta(days=lb)))
            if dup or missing or stale:
                problems.append(f'{t}: {strat} + {lb} gün geriye bakış {len(info["by_load"])} yükleme simülasyonunda {dup} yinelenen, {missing} eksik, '
                                f'{stale} eski sürümde kalan {info["ident"]} üretiyor')
        missing_t = [t for t in tables if t not in seen]
        if missing_t:
            problems.append('modeli yazılmayan tablo: ' + ', '.join(missing_t))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir analitik mühendisisin. Her kaynak tablo için dbt materialization (table ya da incremental) seç; incremental ise unique_key, '
          'incremental_strategy (append, merge, delete+insert) ve geç gelen/güncellenen satırları yakalayacak geriye bakış süresini (gün) öner. Yalnızca hiç '
          'güncellenmeyen ve geç gelmeyen olaylar append olabilir. Yanıt şeması: {"modeller": [{"tablo": "...", "materialized": "incremental", '
          '"unique_key": ["..."], "strateji": "merge", "geriye_bakis_gun": 2, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='dbt modelleri için materialization ve incremental strateji öneren, stratejiyi yükleme geçmişinde simüle eden agent.')
    p.add_argument('--input-dir', default=str(here('ornek', 'dbt_kaynak_yuklemeleri')), help='Tablo başına yükleme geçmişi CSV’leri (ilk sütun kimlik)')
    p.add_argument('--updated', default='guncellenme', help='Satırın güncellenme zamanı sütunu')
    p.add_argument('--load', default='yukleme_tarihi', help='Yükleme (batch) tarihi sütunu')
    p.add_argument('--source-name', default='kaynak', help='dbt source adı')
    p.add_argument('--min-rows', type=int, default=500, help='Bu satır sayısının altındaki tablolar table olarak kalır')
    llm.add_arguments(p)
    args = p.parse_args()
    files = sorted(f for f in Path(args.input_dir).iterdir() if f.suffix.lower() in ('.csv', '.xlsx'))
    if not files:
        raise ValueError(f'{args.input_dir} içinde CSV/XLSX yok.')
    tables, lines = {}, []
    for f in files:
        columns, rows = load_table(f, args.updated, args.load)
        by_load = defaultdict(list)
        for r in rows:
            by_load[r['_l']].append(r)
        ident = columns[0]
        unique_in_load = [c for c in columns if c not in (args.updated, args.load) and all(len({r[c] for r in v}) == len(v) for v in by_load.values())]
        seen = defaultdict(set)
        for L, v in by_load.items():
            for r in v:
                seen[r[ident]].add(L)
        repeats = sum(len(s) > 1 for s in seen.values())
        lag_h = sorted((r['_l'] - r['_u']).total_seconds() / 3600 for r in rows)
        tables[f.stem] = {'columns': columns, 'rows': rows, 'by_load': by_load, 'ident': ident}
        lines.append(f'- {f.stem}: sütunlar {", ".join(columns)} · {len(rows)} satır, {len(by_load)} yükleme (yükleme başına ~{len(rows) // len(by_load)}) · '
                     f'her yüklemede tekil sütunlar: {", ".join(unique_in_load) or "yok"} · {ident} değerlerinin {repeats}/{len(seen)} kadarı birden fazla yüklemede geliyor · '
                     f'yükleme ile {args.updated} arası fark: medyan {lag_h[len(lag_h) // 2]:.1f} sa, p99 {lag_h[int(.99 * (len(lag_h) - 1))]:.1f} sa, max {lag_h[-1]:.1f} sa')
    out = output_dir(args.output)
    user = (f'Kaynak tabloların yükleme profili (ilk sütun kimlik; {args.updated}: satırın güncellenme zamanı, {args.load}: yüklendiği batch). '
            f'{args.min_rows} satırın altındaki tablolar table kalır.\n' + '\n'.join(lines))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables, args.min_rows))
    if result is None:
        return
    models = out / 'models'
    models.mkdir(exist_ok=True)
    plan = []
    for t, m in result.items():
        info = tables[t]
        cols = ', '.join(c for c in info['columns'])
        if m['mat'] == 'incremental':
            key = m['key'] if m['strat'] != 'append' else []
            cfg = f"materialized='incremental', incremental_strategy='{m['strat']}'" + (f", unique_key={key if len(key) > 1 else repr(key[0])}" if key else '') + ", on_schema_change='append_new_columns'"
            body = [f'select {cols}', f"from {{{{ source('{args.source_name}', '{t}') }}}}", '{% if is_incremental() %}',
                    f"where {args.updated} > (select max({args.updated}) from {{{{ this }}}}) - interval '{m['lb']} days'", '{% endif %}']
            if key:
                body.append(f'qualify row_number() over (partition by {", ".join(key)} order by {args.updated} desc, {args.load} desc) = 1')
        else:
            cfg = "materialized='table'"
            body = [f'select {cols}', f"from {{{{ source('{args.source_name}', '{t}') }}}}",
                    f'qualify row_number() over (partition by {info["ident"]} order by {args.updated} desc, {args.load} desc) = 1']
        write_text(models / f'{t}.sql', '\n'.join([f'-- {m["gerekce"]}' if m['gerekce'] else f'-- {t}', f'{{{{ config({cfg}) }}}}', ''] + body))
        plan.append({'tablo': t, 'satir': len(info['rows']), 'materialized': m['mat'], 'strateji': m['strat'] if m['mat'] == 'incremental' else '',
                     'unique_key': ', '.join(m['key']) if m['mat'] == 'incremental' else '', 'geriye_bakis_gun': m['lb'] if m['mat'] == 'incremental' else ''})
    write_text(out / 'sources.yml', '\n'.join(['version: 2', 'sources:', f'  - name: {args.source_name}', '    tables:'] + [f'      - name: {t}' for t in result]))
    tabular.write_csv(out / 'strateji.csv', list(plan[0]), plan)
    md = ['# Incremental strateji', '', f'{len(result)} kaynak tablo; {sum(m["mat"] == "incremental" for m in result.values())} incremental model.', '',
          md_table(['Tablo', 'Satır', 'Materialization', 'Strateji', 'unique_key', 'Geriye bakış (gün)', 'Gerekçe'],
                   [[p_['tablo'], p_['satir'], p_['materialized'], p_['strateji'], p_['unique_key'], p_['geriye_bakis_gun'], result[p_['tablo']]['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca yükleme profili gitti. Her incremental strateji {", ".join(sorted({str(len(t["by_load"])) for t in tables.values()}))} '
          'yükleme üzerinde simüle edildi; yinelenen, eksik ve eski sürümde kalan satır yok._']
    write_text(out / 'incremental-strateji.md', '\n'.join(md))
    print(f'[OK] {len(result)} model. Çıktı: {out / "incremental-strateji.md"}')


if __name__ == '__main__':
    llm.run(main)
