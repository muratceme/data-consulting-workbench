"""Rol madenciliği (AI agent): kullanıcılara tek tek verilmiş yetkilerden (kullanıcı, departman, yetki) az sayıda iş rolü tasarlar ve
kullanıcıları rollere atar (RBAC’ye geçiş). Aynı yetki kümesine sahip kullanıcılar kodla gruplanır; modele yalnızca yetki kümeleri,
kişi sayıları ve departmanlar gider, kullanıcı adları ve e-postalar gönderilmez. Öneri kodla denetlenir: her kümenin rollerinin
birleşimi o kümenin yetkileriyle birebir aynı olmalı (eksik yetki işi durdurur, fazla yetki en az yetki ilkesini bozar), rol sayısı
sınırı aşmamalı, her rol kullanılmalı ve yalnızca mevcut yetkileri içermeli. Çıktı: roller.yaml, rol_yetkileri.csv, kullanici_rolleri.csv, rol-madenciligi.md."""
import argparse, re
from collections import Counter, defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table


def validator(clusters, perms, max_roles):
    def validate(obj):
        seen = set()
        problems, roles = [], {}
        for e in obj.get('roller') or []:
            name = str(e.get('ad', '')).strip()
            ps = {str(x).strip() for x in e.get('yetkiler') or []}
            if not re.fullmatch(r'[a-z][a-z0-9_]*', name) or name in roles:
                problems.append(f'rol adı “{name}” geçersiz ya da tekrar ediyor (küçük harf, rakam, _)')
                continue
            unknown = sorted(ps - perms)
            if not ps or unknown:
                problems.append(f'{name}: ' + (f'olmayan yetki: {", ".join(unknown)}' if unknown else 'yetkisi yok'))
                continue
            roles[name] = ps
        if len(roles) > max_roles:
            problems.append(f'{len(roles)} rol tanımlanmış; en fazla {max_roles}')
        assign, used = {}, set()
        for e in obj.get('atamalar') or []:
            k = str(e.get('kume', '')).strip()
            rs = [str(x).strip() for x in e.get('roller') or []]
            if k not in clusters or k in seen:
                problems.append(f'küme “{k}” yok ya da iki kez atanmış')
                continue
            seen.add(k)
            bad = [r for r in rs if r not in roles]
            if bad:
                problems.append(f'{k}: tanımsız rol: {", ".join(bad)}')
                continue
            got = set().union(*(roles[r] for r in rs)) if rs else set()
            want = clusters[k]['yetkiler']
            if want - got:
                problems.append(f'{k}: eksik yetki: {", ".join(sorted(want - got))}')
            if got - want:
                problems.append(f'{k}: fazla yetki (en az yetki ilkesi): {", ".join(sorted(got - want))}')
            assign[k] = rs
            used |= set(rs)
        missing = [k for k in clusters if k not in seen]
        if missing:
            problems.append('rol atanmayan küme: ' + ', '.join(missing))
        idle = sorted(set(roles) - used)
        if idle:
            problems.append('hiçbir kümeye atanmayan rol: ' + ', '.join(idle))
        if problems:
            raise ValueError('; '.join(problems))
        return roles, assign
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir kimlik ve erişim yönetimi (IAM) uzmanısın. Kullanıcı yetki kümelerinden az sayıda, iş anlamı olan rol tasarla (ör. satis_temel, '
          'satis_yonetici_ek) ve her kümeye rol ata. Her kümenin rollerinin birleşimi o kümenin yetkileriyle birebir aynı olmalı: eksik ya da fazla yetki olmasın. '
          'Ortak yetkileri temel rollerde topla. Yanıt şeması: {"roller": [{"ad": "satis_temel", "yetkiler": ["..."], "aciklama": "..."}], '
          '"atamalar": [{"kume": "K1", "roller": ["satis_temel"]}]}')


def main():
    p = argparse.ArgumentParser(description='Kullanıcı yetkilerinden rol tasarlayan ve rol atamalarını en az yetki ilkesiyle kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'kullanici_yetkileri.csv')), help='kullanici, departman, yetki')
    p.add_argument('--user', default='kullanici')
    p.add_argument('--department', default='departman')
    p.add_argument('--permission', default='yetki')
    p.add_argument('--max-roles', type=int, default=7)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in (args.user, args.permission):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    grants, dept = defaultdict(set), {}
    for r in rows:
        u = r[args.user].strip()
        grants[u].add(r[args.permission].strip())
        dept[u] = r.get(args.department, '').strip()
    by_set = defaultdict(list)
    for u, ps in grants.items():
        by_set[frozenset(ps)].append(u)
    clusters = {f'K{i + 1}': {'yetkiler': set(ps), 'kullanicilar': us} for i, (ps, us) in enumerate(sorted(by_set.items(), key=lambda kv: (-len(kv[1]), sorted(kv[0]))))}
    perms = set().union(*grants.values())
    out = output_dir(args.output)
    user = (f'{len(grants)} kullanıcı, {len(perms)} farklı yetki, {len(clusters)} farklı yetki kümesi; en fazla {args.max_roles} rol.\n'
            'Kümeler (kişi sayısı · departmanlar · yetkiler):\n'
            + '\n'.join(f'- {k}: {len(v["kullanicilar"])} · {", ".join(f"{d} {n}" for d, n in Counter(dept[u] for u in v["kullanicilar"]).most_common())} · '
                        f'{", ".join(sorted(v["yetkiler"]))}' for k, v in clusters.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(clusters, perms, args.max_roles))
    if result is None:
        return
    roles, assign = result
    tabular.write_csv(out / 'rol_yetkileri.csv', ['rol', 'yetki'], [{'rol': r, 'yetki': p_} for r, ps in roles.items() for p_ in sorted(ps)])
    tabular.write_csv(out / 'kullanici_rolleri.csv', ['kullanici', 'departman', 'kume', 'rol'],
                      [{'kullanici': u, 'departman': dept[u], 'kume': k, 'rol': r} for k, v in clusters.items() for u in sorted(v['kullanicilar']) for r in assign[k]])
    yml = ['# RBAC rolleri (kimlik sağlayıcıda grup olarak oluşturulabilir)', 'roller:']
    for r, ps in roles.items():
        yml += [f'  {r}:', '    yetkiler:'] + [f'      - {p_}' for p_ in sorted(ps)]
    write_text(out / 'roller.yaml', '\n'.join(yml))
    direct = sum(len(v) for v in grants.values())
    via = sum(len(assign[k]) * len(v['kullanicilar']) for k, v in clusters.items())
    md = ['# Rol madenciliği', '', f'{len(grants)} kullanıcının {direct} doğrudan yetkisi {len(roles)} role indirildi; {via} rol ataması. Her kullanıcının erişimi birebir korunuyor '
          '(eksik ya da fazla yetki yok).', '',
          md_table(['Rol', 'Yetki sayısı', 'Yetkiler'], [[r, len(ps), ', '.join(sorted(ps))] for r, ps in roles.items()]), '',
          md_table(['Küme', 'Kişi', 'Roller'], [[k, len(v['kullanicilar']), ', '.join(assign[k])] for k, v in clusters.items()]), '',
          f'_Model: {meta["model"]} · Modele yalnızca yetki kümeleri ve kişi sayıları gitti; kullanıcı adları gönderilmedi. Erişimin birebir korunması kodla denetlendi. '
          'Kullanılmayan yetkilerin temizliği ayrı bir gözden geçirme konusudur._']
    write_text(out / 'rol-madenciligi.md', '\n'.join(md))
    print(f'[OK] {len(roles)} rol, {len(grants)} kullanıcı. Çıktı: {out / "rol-madenciligi.md"}')


if __name__ == '__main__':
    llm.run(main)
