"""Veri sahibi önerici (AI agent): sahibi boş ya da ayrılmış çalışanda kalan tablolar için sahip ekip ve veri sorumlusu (steward) önerir.
Kanıt kodla hesaplanır: sorgu günlüğünden her tabloya hangi ekiplerin ne kadar yazdığı (INSERT/UPDATE/MERGE/DELETE) ve okuduğu (SELECT). Modele
tablo adları, ekip bazında yazma/okuma sayıları, ekip listesi ve tablo başına sorumlu adayları (tabloyu kullanan aktif ekip üyelerinin
kullanıcı adları) gider; sorgu metinleri gönderilmez. Öneri kodla denetlenir: ekip dizinde olmalı; tabloya yazan ekip varsa sahip, yazmaların en az yarısını yapan ekip olmalı,
yazan yoksa en çok okuyan iki ekipten biri; sorumlu o ekibin aktif üyesi olmalı ve tabloyu sorgulamış olmalı.
Çıktı: sahiplik_onerileri.csv, veri-sahipligi.md."""
import argparse
from collections import Counter, defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table

WRITES = {'insert', 'update', 'merge', 'delete', 'copy', 'create'}


def validator(tables, evidence, members, users_by_table):
    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('oneriler') or []:
            t, team, steward = (str(e.get(k, '')).strip() for k in ('tablo', 'ekip', 'sorumlu'))
            seen.add(t)
            if t not in tables:
                problems.append(f'tablo “{t}” listede yok')
                continue
            if team not in members:
                problems.append(f'{t}: ekip “{team}” dizinde yok')
                continue
            w, r = evidence[t]['yazma'], evidence[t]['okuma']
            why = []
            if sum(w.values()):
                share = w[team] / sum(w.values())
                if share < 0.5:
                    top = w.most_common(1)[0]
                    why.append(f'{team} yazmaların %{share * 100:.0f}’ini yapıyor; sahip yazmaların çoğunu yapan ekip olmalı ({top[0]} %{top[1] / sum(w.values()) * 100:.0f})')
            else:
                top2 = [k for k, _ in r.most_common(2)]
                if team not in top2:
                    why.append(f'tabloya yazan yok; sahip en çok okuyan iki ekipten biri olmalı ({", ".join(top2)})')
            if steward not in members[team]:
                why.append(f'sorumlu {steward or "?"} {team} ekibinin aktif üyesi değil')
            elif steward not in users_by_table[t]:
                why.append(f'sorumlu {steward} bu tabloyu hiç sorgulamamış')
            if why:
                problems.append(f'{t}: ' + '; '.join(why))
            out[t] = {'ekip': team, 'sorumlu': steward, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [t for t in tables if t not in seen]
        if missing:
            problems.append('öneri yazılmayan tablo: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri yönetişimi (data governance) sorumlususun. Sahibi olmayan tablolar için sahip ekip ve veri sorumlusu öner. Sahip, tabloyu '
          'besleyen (yazan) ekiptir; yazan yoksa en çok kullanan ekip. Sorumlu, o ekibin tabloyu kullanan bir üyesi olmalı. Yanıt şeması: {"oneriler": '
          '[{"tablo": "...", "ekip": "...", "sorumlu": "kullanici_adi", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Sorgu günlüğü kanıtıyla sahipsiz tablolara sahip ekip ve sorumlu öneren agent.')
    p.add_argument('--tables', default=str(here('ornek', 'sahipsiz_tablolar.csv')), help='tablo[, mevcut_sahip]')
    p.add_argument('--log', default=str(here('ornek', 'tablo_sorgu_gunlugu.csv')), help='kullanici, tablo, islem, adet')
    p.add_argument('--directory', default=str(here('ornek', 'ekip_dizini.csv')), help='kullanici, ekip, durum')
    llm.add_arguments(p)
    args = p.parse_args()
    _, trows = tabular.read(args.tables)
    tables = [r['tablo'].strip() for r in trows]
    _, drows = tabular.read(args.directory)
    team_of = {r['kullanici'].strip(): r['ekip'].strip() for r in drows if r.get('durum', 'aktif').strip().lower() == 'aktif'}
    members = defaultdict(set)
    for u, t in team_of.items():
        members[t].add(u)
    _, lrows = tabular.read(args.log)
    evidence = {t: {'yazma': Counter(), 'okuma': Counter()} for t in tables}
    users_by_table = defaultdict(Counter)
    for r in lrows:
        t, u = r['tablo'].strip(), r['kullanici'].strip()
        if t not in evidence:
            continue
        n = int(float(r.get('adet') or 1))
        team = team_of.get(u, 'bilinmiyor')
        kind = 'yazma' if r['islem'].strip().lower() in WRITES else 'okuma'
        evidence[t][kind][team] += n
        users_by_table[t][u] += n
    out = output_dir(args.output)
    lines = []
    for t in tables:
        w, r = evidence[t]['yazma'], evidence[t]['okuma']
        cands = [f'{u} ({team_of[u]})' for u, _ in users_by_table[t].most_common() if u in team_of][:6]
        lines.append(f'- {t}: yazma ' + (', '.join(f'{k} {v}' for k, v in w.most_common()) or 'yok') + '; okuma ' + (', '.join(f'{k} {v}' for k, v in r.most_common()) or 'yok')
                     + f'; sorumlu adayları: {", ".join(cands) or "yok"}')
    user = 'Ekipler: ' + ', '.join(sorted(members)) + '\n\nTablolar (ekip bazında işlem sayısı):\n' + '\n'.join(lines)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables, evidence, members, users_by_table))
    if result is None:
        return
    rows = [{'tablo': t, 'ekip': result[t]['ekip'], 'sorumlu': result[t]['sorumlu'],
             'yazma_payi_%': round(evidence[t]['yazma'][result[t]['ekip']] / sum(evidence[t]['yazma'].values()) * 100) if sum(evidence[t]['yazma'].values()) else '',
             'gerekce': result[t]['gerekce']} for t in tables]
    tabular.write_csv(out / 'sahiplik_onerileri.csv', list(rows[0]), rows)
    md = ['# Veri sahipliği önerileri', '', f'{len(tables)} sahipsiz tablo için sahip ekip ve sorumlu; kanıt sorgu günlüğünden kodla hesaplandı.', '',
          md_table(['Tablo', 'Ekip', 'Sorumlu', 'Yazma payı %', 'Gerekçe'], [list(r.values()) for r in rows]), '',
          f'_Model: {meta["model"]} · Sahip ekip yazma/okuma paylarıyla, sorumlu ekip üyeliği ve tablo kullanımıyla denetlendi. Atamadan önce ekiplere onaylatın._']
    write_text(out / 'veri-sahipligi.md', '\n'.join(md))
    print(f'[OK] {len(tables)} tablo için öneri. Çıktı: {out / "veri-sahipligi.md"}')


if __name__ == '__main__':
    llm.run(main)
