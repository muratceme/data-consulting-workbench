"""Toplu çekim planlayıcı (AI agent): gece çalışan REST API çekimi için uç nokta başına çekim modu (tam / artımlı), sayfa boyutu ve
dakikalık istek payı önerir. İstek sayısı, süre ve paylaşılan hız sınırı (rate limit) kodla hesaplanır; modele yalnızca uç nokta
istatistikleri gider. Plan kodla denetlenir: sayfa boyutu API sınırını aşmamalı, artımlı mod yalnızca destekleyen uç noktada
kullanılmalı, artımlı desteklenirken tam çekim gereksiz yere çok istek üretmemeli, uç noktaların dakikalık payları toplamı hız sınırını
aşmamalı ve her çekim zaman penceresine sığmalı. Çıktı: cekim_plani.yaml, cekim_plani.csv, toplu-cekim.md."""
import argparse, math
import llm
import tabular
from common import here, output_dir, write_text, md_table


def requests_of(ep, mode, page):
    return math.ceil((ep['kayit'] if mode == 'tam' else ep['degisen']) / page)


def validator(eps, limit, window):
    def validate(obj):
        seen = set()
        problems, out = [], {}
        for e in obj.get('uc_noktalar') or []:
            k = str(e.get('uc_nokta', '')).strip()
            if k not in eps or k in seen:
                problems.append(f'uç nokta “{k}” yok ya da iki kez yazıldı')
                continue
            seen.add(k)
            mode = str(e.get('mod', '')).strip()
            try:
                page, rpm = int(e.get('sayfa_boyutu')), float(e.get('dakikada_istek'))
            except (TypeError, ValueError):
                problems.append(f'{k}: sayfa_boyutu ve dakikada_istek sayı olmalı')
                continue
            ep = eps[k]
            if mode not in ('tam', 'artimli'):
                problems.append(f'{k}: mod tam ya da artimli olmalı')
                continue
            if not 1 <= page <= ep['max_sayfa']:
                problems.append(f'{k}: sayfa boyutu 1–{ep["max_sayfa"]} olmalı')
                continue
            if mode == 'artimli' and not ep['artimli']:
                problems.append(f'{k}: API artımlı çekimi (updated_since) desteklemiyor')
                continue
            n = requests_of(ep, mode, page)
            if mode == 'tam' and ep['artimli']:
                inc = requests_of(ep, 'artimli', ep['max_sayfa'])
                if n > 10 * max(inc, 1):
                    problems.append(f'{k}: artımlı çekim destekleniyor; tam çekim {n} istek, artımlı {inc} istek')
            if rpm <= 0:
                problems.append(f'{k}: dakikada_istek pozitif olmalı')
                continue
            if n / rpm > window:
                problems.append(f'{k}: {n} istek dakikada {rpm:g} istekle {n / rpm:.1f} dk sürer; pencere {window:g} dk')
            out[k] = {'mod': mode, 'sayfa': page, 'rpm': rpm, 'istek': n, 'sure': n / rpm, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [k for k in eps if k not in seen]
        if missing:
            problems.append('planı yazılmayan uç nokta: ' + ', '.join(missing))
        total = sum(v['rpm'] for v in out.values())
        if total > limit:
            problems.append(f'dakikalık istek payları toplamı {total:g}, API hız sınırı {limit:g}')
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir entegrasyon mühendisisin. Her uç nokta için çekim modu (tam ya da artimli), sayfa boyutu ve paylaşılan hız sınırından dakikalık istek '
          'payı öner; tüm çekimler zaman penceresinde bitsin. Artımlı çekimi destekleyen büyük uç noktalarda tam çekim yapma. Yanıt şeması: '
          '{"uc_noktalar": [{"uc_nokta": "/x", "mod": "artimli", "sayfa_boyutu": 500, "dakikada_istek": 10, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='REST API toplu çekimi için mod, sayfa boyutu ve hız sınırı payı öneren, planı kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'api_uc_noktalari.csv')),
                   help='uc_nokta, kayit_sayisi, max_sayfa_boyutu, ort_yanit_ms, artimli_destek, gunluk_degisen')
    p.add_argument('--rate-limit', type=float, default=60, help='API’nin dakikalık istek sınırı (tüm uç noktalar paylaşır)')
    p.add_argument('--window', type=float, default=30, help='Çekim penceresi (dakika)')
    p.add_argument('--base-url', default='https://api.ornek.com.tr/v1')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    need = ['uc_nokta', 'kayit_sayisi', 'max_sayfa_boyutu', 'ort_yanit_ms', 'artimli_destek', 'gunluk_degisen']
    if [c for c in need if c not in columns]:
        raise ValueError('Eksik sütun: ' + ', '.join(c for c in need if c not in columns))
    num = lambda v: float(v.replace(',', '.') or 0)
    eps = {r['uc_nokta'].strip(): {'kayit': num(r['kayit_sayisi']), 'max_sayfa': int(num(r['max_sayfa_boyutu'])), 'ms': num(r['ort_yanit_ms']),
                                   'artimli': r['artimli_destek'].strip().lower() in ('evet', 'e', '1', 'true'), 'degisen': num(r['gunluk_degisen'])} for r in rows}
    out = output_dir(args.output)
    user = (f'API hız sınırı dakikada {args.rate_limit:g} istek (paylaşılan), çekim penceresi {args.window:g} dk.\n'
            'Uç noktalar (kayıt · max sayfa · ort yanıt ms · artımlı destek · günlük değişen · tam çekim istek sayısı · artımlı istek sayısı, max sayfa ile):\n'
            + '\n'.join(f'- {k}: {e["kayit"]:.0f} · {e["max_sayfa"]} · {e["ms"]:g} · {"evet" if e["artimli"] else "hayır"} · {e["degisen"]:.0f} · '
                        f'{requests_of(e, "tam", e["max_sayfa"])} · {requests_of(e, "artimli", e["max_sayfa"]) if e["artimli"] else "—"}' for k, e in eps.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(eps, args.rate_limit, args.window))
    if result is None:
        return
    plan = [{'uc_nokta': k, 'mod': v['mod'], 'sayfa_boyutu': v['sayfa'], 'dakikada_istek': v['rpm'], 'istek': v['istek'], 'sure_dk': round(v['sure'], 1),
             'paralel_baglanti': max(1, math.ceil(v['rpm'] * eps[k]['ms'] / 60000))} for k, v in result.items()]
    tabular.write_csv(out / 'cekim_plani.csv', list(plan[0]), plan)
    yml = [f'# Toplu çekim planı: hız sınırı {args.rate_limit:g}/dk, pencere {args.window:g} dk', f'base_url: {args.base_url}', 'uc_noktalar:']
    for p_ in plan:
        yml += [f'  - yol: "{p_["uc_nokta"]}"', f'    mod: {p_["mod"]}' + ('  # updated_since = son başarılı çekim zamanı' if p_['mod'] == 'artimli' else ''),
                f'    sayfa_boyutu: {p_["sayfa_boyutu"]}', f'    dakikada_istek: {p_["dakikada_istek"]:g}', f'    paralel_baglanti: {p_["paralel_baglanti"]}',
                '    yeniden_deneme: {deneme: 3, bekleme_sn: 30, durum_kodlari: [429, 500, 502, 503]}']
    write_text(out / 'cekim_plani.yaml', '\n'.join(yml))
    md = ['# Toplu çekim planı', '', f'{len(plan)} uç nokta, toplam {sum(p_["istek"] for p_ in plan)} istek; dakikalık pay toplamı {sum(p_["dakikada_istek"] for p_ in plan):g} / '
          f'{args.rate_limit:g}, en uzun çekim {max(p_["sure_dk"] for p_ in plan):g} dk (pencere {args.window:g} dk).', '',
          md_table(['Uç nokta', 'Mod', 'Sayfa', 'İstek/dk', 'İstek', 'Süre (dk)', 'Gerekçe'],
                   [[p_['uc_nokta'], p_['mod'], p_['sayfa_boyutu'], f'{p_["dakikada_istek"]:g}', p_['istek'], p_['sure_dk'], result[p_['uc_nokta']]['gerekce']] for p_ in plan]), '',
          f'_Model: {meta["model"]} · İstek sayısı, süre ve hız sınırı kodla hesaplandı; artımlı destek ve sayfa sınırı kodla denetlendi._']
    write_text(out / 'toplu-cekim.md', '\n'.join(md))
    print(f'[OK] {len(plan)} uç nokta. Çıktı: {out / "toplu-cekim.md"}')


if __name__ == '__main__':
    llm.run(main)
