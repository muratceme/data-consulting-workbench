"""Kesinti (cutover) planlayıcı (AI agent): sistem geçişi gecesi için görev listesinden (süre, bağımlılık, ekip, geri alınabilir mi) dakika
dakika geçiş planı yazar. Model her görevin başlangıç saatini ve “devam / geri dön” karar noktasını (go/no-go) önerir; plan kodla denetlenir:
her görev bir kez planlanmalı, bakım penceresi içinde başlayıp bitmeli, bağımlı olduğu görevler bitmeden başlamamalı, aynı ekip aynı anda
iki görevde olmamalı; karar noktası geri alınamaz ilk görevden önce ve bağımlı olduğu doğrulama görevlerinden sonra olmalı, karar noktasından
pencere sonuna kadar geri dönüş süresi (--rollback-dk) sığmalı. Çıktı: kesinti_plani.csv, kesinti-plani.md (Mermaid gantt şemasıyla)."""
import argparse
from datetime import datetime, timedelta
import llm
import tabular
from common import here, output_dir, write_text, md_table


def hm(day, text):
    t = datetime.strptime(text.strip(), '%H:%M')
    v = day.replace(hour=t.hour, minute=t.minute)
    return v


def validator(tasks, start, end, rollback):
    first_irrev = None

    def at(text):
        v = hm(start, text)
        return v + timedelta(days=1) if v < start else v  # times after midnight belong to the next day

    def validate(obj):
        plan, problems = {}, []
        for e in obj.get('plan') or []:
            g = str(e.get('gorev', '')).strip()
            if g not in tasks or g in plan:
                problems.append(f'görev “{g}” yok ya da iki kez planlandı')
                continue
            try:
                b = at(str(e.get('baslangic')))
            except ValueError:
                problems.append(f'{g}: baslangic SS:DD olmalı')
                continue
            plan[g] = (b, b + timedelta(minutes=tasks[g]['sure']))
        missing = [g for g in tasks if g not in plan]
        if missing:
            problems.append('planlanmayan görev: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        for g, (b, e) in plan.items():
            if b < start or e > end:
                problems.append(f'{g}: {b:%H:%M}–{e:%H:%M} bakım penceresi ({start:%H:%M}–{end:%H:%M}) dışında')
            for d in tasks[g]['bagimli']:
                if plan[d][1] > b:
                    problems.append(f'{g} {b:%H:%M}’da başlıyor ama bağımlı olduğu {d} {plan[d][1]:%H:%M}’da bitiyor')
        by_team = {}
        for g, (b, e) in plan.items():
            by_team.setdefault(tasks[g]['ekip'], []).append((b, e, g))
        for team, items in by_team.items():
            items.sort()
            for (b1, e1, g1), (b2, e2, g2) in zip(items, items[1:]):
                if b2 < e1:
                    problems.append(f'{team} ekibi aynı anda iki görevde: {g1} ve {g2}')
        try:
            gate = at(str(obj.get('karar_noktasi')))
        except ValueError:
            problems.append('karar_noktasi SS:DD olmalı')
            gate = None
        if gate:
            irrev = [plan[g][0] for g in tasks if not tasks[g]['geri_alinabilir']]
            after = {g for g in tasks if not tasks[g]['geri_alinabilir']}
            grew = True
            while grew:  # everything downstream of an irreversible step comes after the decision anyway
                new = {g for g, t in tasks.items() if g not in after and any(d in after for d in t['bagimli'])}
                after |= new
                grew = bool(new)
            checks = [plan[g][1] for g in tasks if tasks[g]['dogrulama'] and g not in after]
            if irrev and gate > min(irrev):
                problems.append(f'karar noktası {gate:%H:%M}, geri alınamaz ilk görev {min(irrev):%H:%M}’da başlıyor; karar ondan önce olmalı')
            if checks and gate < max(checks):
                problems.append(f'karar noktası {gate:%H:%M}, doğrulama görevleri {max(checks):%H:%M}’da bitiyor; karar doğrulamadan sonra olmalı')
            if gate + timedelta(minutes=rollback) > end:
                problems.append(f'karar noktasından sonra {rollback} dk geri dönüş süresi pencereye sığmıyor (pencere {end:%H:%M}’da bitiyor)')
        if problems:
            raise ValueError(' | '.join(problems[:20]))
        return plan, gate
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir geçiş (cutover) yöneticisisin. Görevler için başlangıç saatleri (SS:DD) ve devam/geri dön karar noktası belirle: bağımlılıkları bekle, '
          'aynı ekibe aynı anda iki görev verme, her şey bakım penceresine sığsın. Karar noktası doğrulama görevlerinden sonra, geri alınamaz ilk görevden önce '
          'olmalı ve ardından geri dönüş süresi pencereye sığmalı. Yanıt şeması: {"plan": [{"gorev": "...", "baslangic": "22:00"}], "karar_noktasi": "01:30", '
          '"notlar": "..."}')


def main():
    p = argparse.ArgumentParser(description='Geçiş gecesi görevlerinden bağımlılık, ekip ve karar noktası kurallarıyla denetlenen kesinti planı yazan agent.')
    p.add_argument('--input', default=str(here('ornek', 'gecis_gece_gorevleri.csv')), help='gorev, sure_dk, bagimli (| ile), ekip, geri_alinabilir, dogrulama')
    p.add_argument('--date', default='2026-11-14', help='Kesinti gecesinin tarihi')
    p.add_argument('--window', default='22:00-06:00', help='Bakım penceresi')
    p.add_argument('--rollback-dk', type=int, default=90, help='Geri dönüşün süresi (dk)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    need = ['gorev', 'sure_dk', 'bagimli', 'ekip', 'geri_alinabilir', 'dogrulama']
    if [c for c in need if c not in columns]:
        raise ValueError('Eksik sütun: ' + ', '.join(c for c in need if c not in columns))
    yes = lambda v: v.strip().lower() in ('evet', 'e', '1', 'true')
    tasks = {r['gorev'].strip(): {'sure': int(r['sure_dk']), 'bagimli': [x.strip() for x in r['bagimli'].split('|') if x.strip()], 'ekip': r['ekip'].strip(),
                                  'geri_alinabilir': yes(r['geri_alinabilir']), 'dogrulama': yes(r['dogrulama'])} for r in rows}
    unknown = [d for t in tasks.values() for d in t['bagimli'] if d not in tasks]
    if unknown:
        raise ValueError(f'Bilinmeyen bağımlılık: {", ".join(unknown)}')
    day = datetime.fromisoformat(args.date)
    a, _, b = args.window.partition('-')
    start = hm(day, a)
    end = hm(day, b)
    end = end + timedelta(days=1) if end <= start else end
    out = output_dir(args.output)
    user = (f'Bakım penceresi {start:%d.%m %H:%M} – {end:%d.%m %H:%M}; geri dönüş {args.rollback_dk} dk.\nGörevler (süre dk · bağımlılık · ekip · geri alınabilir · doğrulama):\n'
            + '\n'.join(f'- {g}: {t["sure"]} · {", ".join(t["bagimli"]) or "—"} · {t["ekip"]} · {"evet" if t["geri_alinabilir"] else "HAYIR"} · {"evet" if t["dogrulama"] else "—"}' for g, t in tasks.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tasks, start, end, args.rollback_dk))
    if result is None:
        return
    plan, gate = result
    order = sorted(plan, key=lambda g: plan[g][0])
    tabular.write_csv(out / 'kesinti_plani.csv', ['gorev', 'ekip', 'baslangic', 'bitis', 'sure_dk', 'geri_alinabilir'],
                      [{'gorev': g, 'ekip': tasks[g]['ekip'], 'baslangic': f'{plan[g][0]:%H:%M}', 'bitis': f'{plan[g][1]:%H:%M}', 'sure_dk': tasks[g]['sure'],
                        'geri_alinabilir': 'evet' if tasks[g]['geri_alinabilir'] else 'hayır'} for g in order])
    gantt = ['```mermaid', 'gantt', '    dateFormat YYYY-MM-DD HH:mm', '    axisFormat %H:%M', f'    title Kesinti gecesi {day:%d.%m.%Y}']
    for team in dict.fromkeys(tasks[g]['ekip'] for g in order):
        gantt.append(f'    section {team}')
        gantt += [f'    {g} :{"crit, " if not tasks[g]["geri_alinabilir"] else ""}{plan[g][0]:%Y-%m-%d %H:%M}, {tasks[g]["sure"]}m' for g in order if tasks[g]['ekip'] == team]
    gantt += ['    section Karar', f'    Devam / geri dön :milestone, {gate:%Y-%m-%d %H:%M}, 0m', '```']
    finish = max(e for _, e in plan.values())
    md = ['# Kesinti planı', '', f'{len(plan)} görev, {start:%H:%M}–{finish:%H:%M}; karar noktası **{gate:%H:%M}** (sonrasında {args.rollback_dk} dk geri dönüş payı var). '
          f'Pencere sonuna {int((end - finish).total_seconds() // 60)} dk kalıyor.', '',
          md_table(['Başlangıç', 'Bitiş', 'Görev', 'Ekip', 'Geri alınabilir'], [[f'{plan[g][0]:%H:%M}', f'{plan[g][1]:%H:%M}', g, tasks[g]['ekip'], 'evet' if tasks[g]['geri_alinabilir'] else '**hayır**'] for g in order]), '',
          *gantt, '', f'_Model: {meta["model"]} · Pencere, bağımlılık, ekip çakışması ve karar noktası kuralları kodla denetlendi._']
    write_text(out / 'kesinti-plani.md', '\n'.join(md))
    print(f'[OK] {len(plan)} görev planlandı; karar noktası {gate:%H:%M}. Çıktı: {out / "kesinti-plani.md"}')


if __name__ == '__main__':
    llm.run(main)
