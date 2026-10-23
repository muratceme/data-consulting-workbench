"""Silme talebi planlayıcı (AI agent): KVKK md. 7 / GDPR md. 17 kapsamında bir kişinin silme talebi için veri envanterindeki her tabloya
uygulanacak işlemi belirler: sil, anonimleştir (kişisel kolonları boşalt, analitik kolonları koru), sakla (yasal saklama yükümlülüğü;
erişimi kısıtla) ya da kapsam dışı (kişiye bağlanamayan tablo). Envanter kodla okunur; modele yalnızca tablo, kolon adları ve yasal
dayanaklar gider, kişinin verisi gönderilmez. Plan kodla denetlenir: kişiye bağlanamayan tablo kapsam dışı olmalı, yasal dayanağı olan
tablo silinmemeli ya da anonimleştirilmemeli, dayanağı olmayan tablo saklanmamalı, analitik kolonu olmayan tabloda anonimleştirme
yapılamaz (geriye bir şey kalmaz), her tablo cevaplanmalı. Çıktı: silme_plani.sql (parametreli), silme_plani.csv, silme-talebi.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_text, md_table

ACTIONS = ('sil', 'anonimlestir', 'sakla', 'kapsam_disi')


def validator(inv):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('tablolar') or []:
            t, a, why = str(e.get('tablo', '')).strip(), str(e.get('islem', '')).strip(), str(e.get('gerekce', '')).strip()
            if t not in inv or t in seen:
                problems.append(f'tablo “{t}” envanterde yok ya da iki kez yazıldı')
                continue
            seen.add(t)
            v = inv[t]
            if a not in ACTIONS:
                problems.append(f'{t}: işlem {", ".join(ACTIONS)} olmalı')
                continue
            if not v['kimlik'] and a != 'kapsam_disi':
                problems.append(f'{t}: kimlik kolonu yok, kişiye bağlanamaz; kapsam_disi olmalı')
            elif v['kimlik'] and a == 'kapsam_disi':
                problems.append(f'{t}: {v["kimlik"]} kolonuyla kişiye bağlanıyor; kapsam dışı olamaz')
            elif v['yasal'] and a in ('sil', 'anonimlestir'):
                problems.append(f'{t}: yasal saklama yükümlülüğü var ({v["yasal"]}); silinemez, sakla ve erişimi kısıtla')
            elif not v['yasal'] and a == 'sakla':
                problems.append(f'{t}: yasal dayanak yok; kişisel veri saklanamaz (sil ya da anonimlestir)')
            elif a == 'anonimlestir' and not v['analitik']:
                problems.append(f'{t}: analitik kolon yok; anonimleştirince geriye veri kalmaz, sil')
            if a == 'sakla' and not why:
                problems.append(f'{t}: saklama gerekçesi (dayanak ve süre) yazılmalı')
            out[t] = (a, why)
        missing = [t for t in inv if t not in seen]
        if missing:
            problems.append('işlemi yazılmayan tablo: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir KVKK/GDPR uyum uzmanısın. Bir kişinin silme talebi için her tabloya işlem belirle: sil, anonimlestir (kişisel kolonlar boşaltılır, '
          'analitik kolonlar kalır), sakla (yasal saklama yükümlülüğü; erişim kısıtlanır) ya da kapsam_disi (kişiye bağlanamayan tablo). Yanıt şeması: '
          '{"tablolar": [{"tablo": "...", "islem": "anonimlestir", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Silme talebi için tablo bazında sil/anonimleştir/sakla planı çıkaran ve yasal kuralları kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'silme_envanteri.csv')), help='tablo, kimlik_kolonu, kisisel_kolonlar, analitik_kolonlar, yasal_dayanak')
    p.add_argument('--param', default=':kisi_id', help='SQL’deki kişi kimliği parametresi')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('tablo', 'kimlik_kolonu', 'kisisel_kolonlar', 'analitik_kolonlar', 'yasal_dayanak'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    split = lambda v: [x.strip() for x in v.split('|') if x.strip()]
    inv = {r['tablo'].strip(): {'kimlik': r['kimlik_kolonu'].strip(), 'kisisel': split(r['kisisel_kolonlar']), 'analitik': split(r['analitik_kolonlar']),
                                'yasal': r['yasal_dayanak'].strip()} for r in rows}
    out = output_dir(args.output)
    user = ('Veri envanteri (tablo · kimlik kolonu · kişisel kolonlar · analitik kolonlar · yasal saklama dayanağı):\n'
            + '\n'.join(f'- {t}: {v["kimlik"] or "yok"} · {", ".join(v["kisisel"]) or "—"} · {", ".join(v["analitik"]) or "—"} · {v["yasal"] or "—"}' for t, v in inv.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(inv))
    if result is None:
        return
    sql = [f'-- Silme talebi planı; {args.param} kişinin kimliğidir. Tek işlemde (transaction) çalıştırın ve sonucu talep kaydına ekleyin.', 'BEGIN;']
    for t, (a, why) in result.items():
        v = inv[t]
        if a == 'sil':
            sql.append(f'DELETE FROM {t} WHERE {v["kimlik"]} = {args.param};')
        elif a == 'anonimlestir':
            sql.append(f'UPDATE {t} SET {", ".join(f"{c} = NULL" for c in v["kisisel"])}, {v["kimlik"]} = NULL WHERE {v["kimlik"]} = {args.param};')
        elif a == 'sakla':
            sql.append(f'-- {t}: saklanıyor ({v["yasal"]}); erişim kısıtı: INSERT INTO kvkk.erisim_kisiti (tablo, kisi_id, gerekce) VALUES (\'{t}\', {args.param}, \'{why}\');')
    sql.append('COMMIT;')
    write_text(out / 'silme_plani.sql', '\n'.join(sql))
    plan = [{'tablo': t, 'islem': a, 'kimlik_kolonu': inv[t]['kimlik'], 'yasal_dayanak': inv[t]['yasal'], 'gerekce': why} for t, (a, why) in result.items()]
    tabular.write_csv(out / 'silme_plani.csv', list(plan[0]), plan)
    md = ['# Silme talebi planı', '', f'{len(plan)} tablo: ' + ', '.join(f'{sum(p_["islem"] == a for p_ in plan)} {a}' for a in ACTIONS) + '.', '',
          md_table(['Tablo', 'İşlem', 'Yasal dayanak', 'Gerekçe'], [[p_['tablo'], p_['islem'], p_['yasal_dayanak'] or '—', p_['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca envanter (tablo ve kolon adları) gitti; kişiye bağlanabilirlik, yasal saklama ve anonimleştirilebilirlik kodla denetlendi. '
          'Yedeklerdeki kopyalar ve üçüncü taraflara aktarılan veriler ayrıca ele alınmalıdır._']
    write_text(out / 'silme-talebi.md', '\n'.join(md))
    print(f'[OK] {len(plan)} tablo. Çıktı: {out / "silme-talebi.md"}')


if __name__ == '__main__':
    llm.run(main)
