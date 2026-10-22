"""Kubernetes kaynak ayarlayıcı (AI agent): deployment başına CPU/bellek kullanım yüzdeliklerinden (Prometheus/metrics-server dışa
aktarımı) yeni request ve limit değerleri önerir; mevcut ayara göre aylık maliyet farkını kod hesaplar. Modele yalnızca kullanım
yüzdelikleri ve mevcut ayarlar gider. Öneri kodla denetlenir: CPU request medyan kullanımın altına inmemeli ve p95’in %30 fazlasını
aşmamalı (boşa ayrılan çekirdek), bellek request p95’in altında olmamalı, bellek limiti en yüksek kullanımın %10 fazlasından az olmamalı
(OOMKilled), CPU limiti konursa en yüksek kullanımın altında kalmamalı (throttling). Çıktı: kaynak_yamalari.yaml, kaynak_plani.csv, k8s-kaynaklari.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_text, md_table

NUMS = ('replika', 'cpu_p50_m', 'cpu_p95_m', 'cpu_max_m', 'mem_p95_mib', 'mem_max_mib', 'cpu_request_m', 'mem_request_mib', 'mem_limit_mib')


def cost(cpu_m, mem_mib, replicas, cpu_price, mem_price):
    return replicas * (cpu_m / 1000 * cpu_price + mem_mib / 1024 * mem_price)


def validator(deps):
    def validate(obj):
        seen = set()
        problems, out = [], {}
        for e in obj.get('deploymentlar') or []:
            k = str(e.get('deployment', '')).strip()
            if k not in deps or k in seen:
                problems.append(f'deployment “{k}” yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            try:
                cr, mr, ml = int(e.get('cpu_request_m')), int(e.get('mem_request_mib')), int(e.get('mem_limit_mib'))
                cl = None if e.get('cpu_limit_m') in (None, '') else int(e.get('cpu_limit_m'))
            except (TypeError, ValueError):
                problems.append(f'{k}: değerler tam sayı (milicore / MiB) olmalı')
                continue
            d = deps[k]
            if cr < d['cpu_p50_m']:
                problems.append(f'{k}: CPU request {cr}m, medyan kullanım {d["cpu_p50_m"]:g}m; pod sürekli request üstünde çalışır')
            if cr > 1.3 * d['cpu_p95_m']:
                problems.append(f'{k}: CPU request {cr}m, p95 kullanım {d["cpu_p95_m"]:g}m; en fazla {1.3 * d["cpu_p95_m"]:.0f}m olmalı')
            if mr < d['mem_p95_mib']:
                problems.append(f'{k}: bellek request {mr} MiB, p95 kullanım {d["mem_p95_mib"]:g} MiB; düğüm sıkışınca pod tahliye edilir')
            if ml < 1.1 * d['mem_max_mib']:
                problems.append(f'{k}: bellek limiti {ml} MiB, en yüksek kullanım {d["mem_max_mib"]:g} MiB; en az {1.1 * d["mem_max_mib"]:.0f} MiB olmalı (OOMKilled)')
            if ml < mr:
                problems.append(f'{k}: bellek limiti request’ten küçük olamaz')
            if cl is not None and cl < d['cpu_max_m']:
                problems.append(f'{k}: CPU limiti {cl}m, en yüksek kullanım {d["cpu_max_m"]:g}m; throttling olur (limit koymamak da seçenek)')
            out[k] = {'cr': cr, 'cl': cl, 'mr': mr, 'ml': ml, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [k for k in deps if k not in seen]
        if missing:
            problems.append('ayarı yazılmayan deployment: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir Kubernetes platform mühendisisin. Her deployment için kullanım yüzdeliklerine göre CPU request, isteğe bağlı CPU limit, bellek request '
          've bellek limit öner: boşa ayrılan kaynağı azalt ama tahliye (eviction), OOMKilled ve throttling riski yaratma. Yanıt şeması: {"deploymentlar": '
          '[{"deployment": "...", "cpu_request_m": 250, "cpu_limit_m": null, "mem_request_mib": 512, "mem_limit_mib": 768, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Kubernetes deployment’larına kullanım yüzdeliklerinden request/limit öneren agent.')
    p.add_argument('--input', default=str(here('ornek', 'k8s_pod_kullanimi.csv')), help='namespace, deployment, replika, cpu_p50_m, cpu_p95_m, cpu_max_m, mem_p95_mib, '
                   'mem_max_mib, cpu_request_m, mem_request_mib, mem_limit_mib')
    p.add_argument('--cpu-price', type=float, default=25.0, help='Bir çekirdeğin aylık maliyeti')
    p.add_argument('--mem-price', type=float, default=3.5, help='1 GiB belleğin aylık maliyeti')
    p.add_argument('--currency', default='USD')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if [c for c in ('namespace', 'deployment') + NUMS if c not in columns]:
        raise ValueError('Eksik sütun: ' + ', '.join(c for c in ('namespace', 'deployment') + NUMS if c not in columns))
    deps = {r['deployment'].strip(): {'ns': r['namespace'].strip(), **{c: float(r[c].replace(',', '.')) for c in NUMS}} for r in rows}
    out = output_dir(args.output)
    user = ('Deployment kullanımları (replika · CPU p50/p95/max m · bellek p95/max MiB · mevcut CPU request m · bellek request/limit MiB):\n'
            + '\n'.join(f'- {k}: {d["replika"]:g} · {d["cpu_p50_m"]:g}/{d["cpu_p95_m"]:g}/{d["cpu_max_m"]:g} · {d["mem_p95_mib"]:g}/{d["mem_max_mib"]:g} · '
                        f'{d["cpu_request_m"]:g} · {d["mem_request_mib"]:g}/{d["mem_limit_mib"]:g}' for k, d in deps.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(deps))
    if result is None:
        return
    plan = []
    for k, m in result.items():
        d = deps[k]
        before = cost(d['cpu_request_m'], d['mem_request_mib'], d['replika'], args.cpu_price, args.mem_price)
        after = cost(m['cr'], m['mr'], d['replika'], args.cpu_price, args.mem_price)
        plan.append({'namespace': d['ns'], 'deployment': k, 'replika': int(d['replika']), 'cpu_request_m': m['cr'], 'cpu_limit_m': m['cl'] if m['cl'] is not None else '',
                     'mem_request_mib': m['mr'], 'mem_limit_mib': m['ml'], 'aylik_once': round(before, 2), 'aylik_sonra': round(after, 2), 'fark': round(after - before, 2)})
    tabular.write_csv(out / 'kaynak_plani.csv', list(plan[0]), plan)
    yml = []
    for p_ in plan:
        limits = f'                memory: {p_["mem_limit_mib"]}Mi' + (f'\n                cpu: {p_["cpu_limit_m"]}m' if p_['cpu_limit_m'] != '' else '')
        yml += ['---', 'apiVersion: apps/v1', 'kind: Deployment', 'metadata:', f'  name: {p_["deployment"]}', f'  namespace: {p_["namespace"]}', 'spec:', '  template:', '    spec:',
                '      containers:', f'        - name: {p_["deployment"]}', '          resources:', '            requests:', f'                cpu: {p_["cpu_request_m"]}m',
                f'                memory: {p_["mem_request_mib"]}Mi', '            limits:', limits]
    write_text(out / 'kaynak_yamalari.yaml', '\n'.join(['# kubectl patch / kustomize strategic merge yamaları; konteyner adı deployment adıyla aynı varsayıldı'] + yml))
    total = sum(p_['fark'] for p_ in plan)
    md = ['# Kubernetes kaynak ayarları', '', f'{len(plan)} deployment; request’lere göre aylık maliyet farkı {total:+,.2f} {args.currency}.', '',
          md_table(['Deployment', 'CPU req (m)', 'CPU limit', 'Bellek req/limit (MiB)', 'Aylık önce', 'Aylık sonra', 'Gerekçe'],
                   [[p_['deployment'], p_['cpu_request_m'], p_['cpu_limit_m'] or '—', f'{p_["mem_request_mib"]}/{p_["mem_limit_mib"]}', f'{p_["aylik_once"]:,.2f}', f'{p_["aylik_sonra"]:,.2f}',
                     result[p_['deployment']]['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · Modele yalnızca kullanım yüzdelikleri gitti; tahliye, OOMKilled, throttling ve israf sınırları kodla denetlendi, maliyet kodla hesaplandı._']
    write_text(out / 'k8s-kaynaklari.md', '\n'.join(md))
    print(f'[OK] {len(plan)} deployment, aylık fark {total:+,.2f} {args.currency}. Çıktı: {out / "k8s-kaynaklari.md"}')


if __name__ == '__main__':
    llm.run(main)
