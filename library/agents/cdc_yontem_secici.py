"""CDC yöntem seçici (AI agent): kaynak veritabanından ambara taşınacak tablolar için değişiklik yakalama yöntemini seçer: log tabanlı CDC
(Debezium/binlog/WAL), sorgu tabanlı artımlı çekim (updated_at > son çekim) ya da tam kopya. Tablo özellikleri (satır sayısı, günlük
değişim, birincil anahtar, updated_at, fiziksel silme) kodla okunur; modele yalnızca bu özet gider. Seçim kodla denetlenir: sorgu tabanlı
çekim updated_at ister ve fiziksel silmeleri kaçırır, log tabanlı CDC birincil anahtar ister (log erişimi yoksa kullanılamaz), tam kopya
yalnızca küçük tablolarda kabul edilir ve günlük tam kopya hacmi bütçeyi aşmamalı. Çıktı: cdc_plani.csv, debezium_baglayici.json, cdc-yontemi.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table

METHODS = ('log', 'sorgu', 'tam')


def validator(tables, log_ok, full_max, full_budget):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('tablolar') or []:
            t, m = str(e.get('tablo', '')).strip(), str(e.get('yontem', '')).strip()
            if t not in tables or t in seen:
                problems.append(f'tablo “{t}” yok ya da iki kez yazıldı')
                continue
            seen.add(t)
            v = tables[t]
            if m not in METHODS:
                problems.append(f'{t}: yöntem {", ".join(METHODS)} olmalı')
                continue
            if m == 'sorgu':
                if not v['updated_at']:
                    problems.append(f'{t}: updated_at yok; sorgu tabanlı çekim değişiklikleri göremez')
                if v['delete']:
                    problems.append(f'{t}: fiziksel silme var; sorgu tabanlı çekim silinen satırları kaçırır')
            if m == 'log':
                if not log_ok:
                    problems.append(f'{t}: kaynakta log (binlog/WAL) erişimi yok')
                elif not v['pk']:
                    problems.append(f'{t}: birincil anahtar yok; log tabanlı CDC güncellemeleri satırla eşleyemez')
            if m == 'tam' and v['rows'] > full_max:
                problems.append(f'{t}: {v["rows"]:,.0f} satır; tam kopya yalnızca {full_max:,.0f} satırın altındaki tablolarda')
            out[t] = (m, str(e.get('gerekce', '')).strip())
        missing = [t for t in tables if t not in seen]
        if missing:
            problems.append('yöntemi yazılmayan tablo: ' + ', '.join(missing))
        full = sum(tables[t]['rows'] for t, (m, _) in out.items() if m == 'tam')
        if full > full_budget:
            problems.append(f'günlük tam kopya {full:,.0f} satır, bütçe {full_budget:,.0f}')
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri entegrasyon mimarısın. Her tablo için değişiklik yakalama yöntemi seç: log (binlog/WAL üzerinden CDC; birincil anahtar ister), '
          'sorgu (updated_at ile artımlı; fiziksel silmeleri göremez) ya da tam (her gün tam kopya; yalnızca küçük tablolar). Yanıt şeması: '
          '{"tablolar": [{"tablo": "...", "yontem": "log", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Tablo başına log/sorgu/tam kopya CDC yöntemi seçen ve kısıtları kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'cdc_tablolari.csv')), help='tablo, satir_sayisi, gunluk_degisim, pk_var, updated_at_var, hard_delete')
    p.add_argument('--no-log-access', action='store_true', help='Kaynakta binlog/WAL erişimi yok')
    p.add_argument('--full-max-rows', type=float, default=1_000_000)
    p.add_argument('--full-budget-rows', type=float, default=2_000_000, help='Günlük tam kopya toplam satır bütçesi')
    p.add_argument('--server', default='erp-db', help='Debezium topic öneki')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    yes = lambda v: v.strip().lower() in ('evet', 'e', '1', 'true')
    tables = {r['tablo'].strip(): {'rows': float(r['satir_sayisi'].replace(',', '.')), 'change': float(r['gunluk_degisim'].replace(',', '.')), 'pk': yes(r['pk_var']),
                                   'updated_at': yes(r['updated_at_var']), 'delete': yes(r['hard_delete'])} for r in rows}
    out = output_dir(args.output)
    user = (f'Kaynakta log erişimi: {"yok" if args.no_log_access else "var"}. Tam kopya en fazla {args.full_max_rows:,.0f} satırlık tablolarda, günlük toplam {args.full_budget_rows:,.0f} satır.\n'
            'Tablolar (satır · günlük değişim · PK · updated_at · fiziksel silme):\n'
            + '\n'.join(f'- {t}: {v["rows"]:,.0f} · {v["change"]:,.0f} · {"evet" if v["pk"] else "hayır"} · {"evet" if v["updated_at"] else "hayır"} · {"evet" if v["delete"] else "hayır"}'
                        for t, v in tables.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables, not args.no_log_access, args.full_max_rows, args.full_budget_rows))
    if result is None:
        return
    plan = [{'tablo': t, 'yontem': m, 'satir': int(tables[t]['rows']), 'gunluk_degisim': int(tables[t]['change']), 'gerekce': why} for t, (m, why) in result.items()]
    tabular.write_csv(out / 'cdc_plani.csv', list(plan[0]), plan)
    log_tables = [t for t, (m, _) in result.items() if m == 'log']
    if log_tables:
        write_json(out / 'debezium_baglayici.json', {'name': f'{args.server}-cdc', 'config': {
            'connector.class': 'io.debezium.connector.postgresql.PostgresConnector', 'topic.prefix': args.server, 'table.include.list': ','.join(log_tables),
            'snapshot.mode': 'initial', 'tombstones.on.delete': 'true', 'database.hostname': '${file:/secrets/db.properties:host}',
            'database.user': '${file:/secrets/db.properties:user}', 'database.password': '${file:/secrets/db.properties:password}'}})
    md = ['# CDC yöntemi', '', f'{len(plan)} tablo: ' + ', '.join(f'{sum(p_["yontem"] == m for p_ in plan)} {m}' for m in METHODS) + '.', '',
          md_table(['Tablo', 'Yöntem', 'Satır', 'Günlük değişim', 'Gerekçe'], [[p_['tablo'], p_['yontem'], f'{p_["satir"]:,}', f'{p_["gunluk_degisim"]:,}', p_['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca tablo özellikleri gitti; updated_at, silme, birincil anahtar ve tam kopya sınırları kodla denetlendi._']
    write_text(out / 'cdc-yontemi.md', '\n'.join(md))
    print(f'[OK] {len(plan)} tablo. Çıktı: {out / "cdc-yontemi.md"}')


if __name__ == '__main__':
    llm.run(main)
