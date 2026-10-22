"""Bağlantı havuzu boyutlandırıcı (AI agent): PostgreSQL’e bağlanan servislerin tepe istek hızı, istek başına sorgu sayısı ve sorgu
sürelerinden servis başına bağlantı havuzu (en az/en çok bağlantı, bağlantı bekleme zaman aşımı) önerir. Tepe yükte gereken eşzamanlı
bağlantı Little yasasıyla (istek/sn × sorgu/istek × sorgu süresi) kodla hesaplanır. Öneri kodla denetlenir: havuz p95 sorgu süresindeki
tepe eşzamanlılığı karşılamalı ama gereğinin çok üstünde olmamalı, tüm instance’ların toplamı veritabanının max_connections değerinden
yönetim için ayrılan bağlantılar düşüldükten sonra sığmalı, bağlantı bekleme süresi p95 sorgu süresinden kısa olmamalı.
Çıktı: havuz_ayarlari.yaml (HikariCP), havuz_plani.csv, baglanti-havuzu.md."""
import argparse, math
import llm
import tabular
from common import here, output_dir, write_text, md_table


def need_of(s):
    per = lambda ms: s['rps'] * s['q'] * ms / 1000 / s['inst']
    return per(s['avg']), per(s['p95'])


def validator(svc, budget):
    def validate(obj):
        problems, out = [], {}
        for e in obj.get('servisler') or []:
            k = str(e.get('servis', '')).strip()
            if k not in svc or k in out:
                problems.append(f'servis “{k}” yok ya da iki kez yazıldı')
                continue
            try:
                mx, mn, wait = int(e.get('havuz_max')), int(e.get('havuz_min')), int(e.get('baglanti_bekleme_ms'))
            except (TypeError, ValueError):
                problems.append(f'{k}: havuz_max, havuz_min ve baglanti_bekleme_ms tam sayı olmalı')
                continue
            s = svc[k]
            _, p95 = need_of(s)
            lo, hi = math.ceil(p95), 2 * math.ceil(p95) + 5
            if mx < lo:
                problems.append(f'{k}: instance başına havuz {mx}, tepe yükte p95 eşzamanlılık {p95:.2f}; en az {lo} olmalı')
            elif mx > hi:
                problems.append(f'{k}: instance başına havuz {mx}, gereken {p95:.2f}; {hi} üstü boşta bağlantı tutar')
            if not 1 <= mn <= mx:
                problems.append(f'{k}: 1 ≤ havuz_min ≤ havuz_max olmalı')
            if wait < s['p95'] or wait > 30000:
                problems.append(f'{k}: bağlantı bekleme {wait} ms; p95 sorgu süresi ({s["p95"]:g} ms) ile 30000 ms arasında olmalı')
            out[k] = (mx, mn, wait, str(e.get('gerekce', '')).strip())
        missing = [k for k in svc if k not in out]
        if missing:
            problems.append('havuzu yazılmayan servis: ' + ', '.join(missing))
        total = sum(v[0] * svc[k]['inst'] for k, v in out.items())
        if total > budget:
            problems.append(f'toplam bağlantı {total} (havuz × instance), kullanılabilir {budget}; max_connections aşılır')
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir PostgreSQL veritabanı yöneticisisin. Her servis için instance başına bağlantı havuzu öner (havuz_max, havuz_min, '
          'baglanti_bekleme_ms). Havuz tepe yükteki eşzamanlı sorguyu karşılasın; büyük havuz veritabanında bağlam değiştirme ve bellek yükü yaratır, toplam '
          'bağlantı max_connections sınırına sığmalı. Yanıt şeması: {"servisler": [{"servis": "...", "havuz_max": 10, "havuz_min": 2, "baglanti_bekleme_ms": 3000, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Servis metriklerinden bağlantı havuzu boyutu öneren ve max_connections bütçesini kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'servis_sorgu_metrikleri.csv')),
                   help='servis, instance, tepe_istek_sn, istek_basina_sorgu, ort_sorgu_ms, p95_sorgu_ms, mevcut_havuz')
    p.add_argument('--max-connections', type=int, default=100, help='PostgreSQL max_connections')
    p.add_argument('--reserved', type=int, default=10, help='Yönetim, replikasyon ve bakım için ayrılan bağlantı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    cols = ['servis', 'instance', 'tepe_istek_sn', 'istek_basina_sorgu', 'ort_sorgu_ms', 'p95_sorgu_ms', 'mevcut_havuz']
    if [c for c in cols if c not in columns]:
        raise ValueError('Eksik sütun: ' + ', '.join(c for c in cols if c not in columns))
    num = lambda v: float(v.replace(',', '.'))
    svc = {r['servis'].strip(): {'inst': int(num(r['instance'])), 'rps': num(r['tepe_istek_sn']), 'q': num(r['istek_basina_sorgu']), 'avg': num(r['ort_sorgu_ms']),
                                 'p95': num(r['p95_sorgu_ms']), 'cur': int(num(r['mevcut_havuz']))} for r in rows}
    budget = args.max_connections - args.reserved
    current = sum(s['cur'] * s['inst'] for s in svc.values())
    out = output_dir(args.output)
    user = (f'max_connections {args.max_connections}, ayrılan {args.reserved}; servislere kullanılabilir {budget} bağlantı. Mevcut havuzların toplamı {current}.\n'
            'Servisler (instance · tepe istek/sn · sorgu/istek · ort/p95 sorgu ms · instance başına gereken eşzamanlı bağlantı ort/p95 · mevcut havuz):\n'
            + '\n'.join(f'- {k}: {s["inst"]} · {s["rps"]:g} · {s["q"]:g} · {s["avg"]:g}/{s["p95"]:g} · {need_of(s)[0]:.2f}/{need_of(s)[1]:.2f} · {s["cur"]}' for k, s in svc.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(svc, budget))
    if result is None:
        return
    plan = [{'servis': k, 'instance': svc[k]['inst'], 'gereken_p95': round(need_of(svc[k])[1], 2), 'mevcut_havuz': svc[k]['cur'], 'havuz_max': mx, 'havuz_min': mn,
             'baglanti_bekleme_ms': w, 'toplam_baglanti': mx * svc[k]['inst']} for k, (mx, mn, w, _) in result.items()]
    total = sum(p_['toplam_baglanti'] for p_ in plan)
    tabular.write_csv(out / 'havuz_plani.csv', list(plan[0]), plan)
    yml = [f'# HikariCP havuz ayarları (servis başına, instance başına). Toplam {total} / {budget} bağlantı.']
    for k, (mx, mn, w, why) in result.items():
        yml += [f'{k}:', f'  # {why}' if why else '  #', '  spring.datasource.hikari:', f'    maximum-pool-size: {mx}', f'    minimum-idle: {mn}', f'    connection-timeout: {w}',
                '    idle-timeout: 600000', '    max-lifetime: 1800000']
    write_text(out / 'havuz_ayarlari.yaml', '\n'.join(yml))
    md = ['# Bağlantı havuzu planı', '', f'Mevcut havuzların toplamı {current} bağlantı; önerilen {total} / {budget} (max_connections {args.max_connections} − {args.reserved} ayrılan).', '',
          md_table(['Servis', 'Instance', 'Gereken (p95)', 'Mevcut', 'Önerilen max/min', 'Bekleme ms', 'Toplam', 'Gerekçe'],
                   [[p_['servis'], p_['instance'], p_['gereken_p95'], p_['mevcut_havuz'], f'{p_["havuz_max"]}/{p_["havuz_min"]}', p_['baglanti_bekleme_ms'], p_['toplam_baglanti'],
                     result[p_['servis']][3]] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Gereken eşzamanlılık Little yasasıyla kodla hesaplandı; havuz alt/üst sınırı ve max_connections bütçesi kodla denetlendi._']
    write_text(out / 'baglanti-havuzu.md', '\n'.join(md))
    print(f'[OK] {len(result)} servis, toplam {total} bağlantı. Çıktı: {out / "baglanti-havuzu.md"}')


if __name__ == '__main__':
    llm.run(main)
