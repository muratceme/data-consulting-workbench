"""Sipariş önerici (AI agent): stok kalemleri için ABC sınıfına göre hizmet seviyesi seçer ve bu dönemin satın alma siparişlerini önerir. Emniyet
stoku ve yeniden sipariş noktası kodla hesaplanır (emniyet stoku = z × talep std × √tedarik süresi; ROP = ort. talep × tedarik süresi + emniyet
stoku; yeniden-siparis-noktasi bloğuyla aynı formül). Modele kalem istatistikleri gider. Öneri kodla denetlenir: hizmet seviyeleri A ≥ B ≥ C
ve %80–%99,5 arasında; stok pozisyonu (eldeki + yoldaki) ROP’un altına düşen her kalem sipariş edilmeli, üstündekiler edilmemeli; miktar koli
katı ve en az MOQ olmalı, sipariş sonrası pozisyon ROP + gözden geçirme dönemi talebini karşılamalı ama bunu bir koli + MOQ’dan fazla aşmamalı.
Çıktı: siparis_onerileri.csv, siparis-onerileri.md."""
import argparse, math
from statistics import NormalDist
import llm
import tabular
from common import here, output_dir, write_text, md_table


def targets(items, levels, review):
    out = {}
    for k, it in items.items():
        z = NormalDist().inv_cdf(levels[it['abc']])
        ss = z * it['std'] * math.sqrt(it['lead'])
        rop = it['d'] * it['lead'] + ss
        out[k] = {'ss': ss, 'rop': rop, 'hedef': rop + it['d'] * review, 'pozisyon': it['eldeki'] + it['yoldaki']}
    return out


def validator(items, review):
    def validate(obj):
        lv = obj.get('hizmet_seviyeleri') or {}
        try:
            levels = {c: float(lv[c]) for c in ('A', 'B', 'C')}
        except (KeyError, TypeError, ValueError):
            raise ValueError('hizmet_seviyeleri A, B, C (0–1) gerekli') from None
        if not all(0.8 <= v <= 0.995 for v in levels.values()) or not levels['A'] >= levels['B'] >= levels['C']:
            raise ValueError('hizmet seviyeleri %80–%99,5 arasında ve A ≥ B ≥ C olmalı')
        tgt = targets(items, levels, review)
        orders = {}
        problems = []
        for e in obj.get('siparisler') or []:
            k = str(e.get('urun', '')).strip()
            try:
                q = int(e.get('miktar'))
            except (TypeError, ValueError):
                problems.append(f'{k}: miktar tam sayı olmalı')
                continue
            if k not in items:
                problems.append(f'{k}: kalem yok')
            elif q > 0:
                orders[k] = q
        for k, it in items.items():
            t, q = tgt[k], orders.get(k, 0)
            if t['pozisyon'] <= t['rop'] and not q:
                problems.append(f'{k}: pozisyon {t["pozisyon"]:g} ≤ ROP {t["rop"]:.0f}; sipariş gerekli')
            if t['pozisyon'] > t['rop'] and q:
                problems.append(f'{k}: pozisyon {t["pozisyon"]:g} > ROP {t["rop"]:.0f}; bu dönem sipariş gerekmez')
            elif q:
                why = []
                if q % it['koli']:
                    why.append(f'{it["koli"]} koli katı değil')
                if q < it['moq']:
                    why.append(f'MOQ {it["moq"]} altında')
                after = t['pozisyon'] + q
                if after < t['hedef']:
                    why.append(f'sipariş sonrası {after:g} < hedef {t["hedef"]:.0f}')
                elif after > t['hedef'] + it['koli'] + it['moq']:
                    why.append(f'sipariş sonrası {after:g}, hedefi ({t["hedef"]:.0f}) gereğinden çok aşıyor')
                if why:
                    problems.append(f'{k} ({q}): ' + ', '.join(why))
        if problems:
            raise ValueError(' | '.join(problems))
        return levels, tgt, orders
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir tedarik planlamacısısın. ABC sınıfına göre hizmet seviyesi seç (A ≥ B ≥ C, %80–%99,5) ve stok pozisyonu (eldeki + yoldaki) yeniden '
          'sipariş noktasının (ROP = ort. talep × tedarik süresi + z × std × √tedarik süresi) altında kalan kalemler için sipariş miktarı öner: sipariş sonrası '
          'pozisyon ROP + gözden geçirme dönemi talebini karşılasın, miktar koli katı ve en az MOQ olsun, gereğinden fazla stok yapma. '
          'Yanıt şeması: {"hizmet_seviyeleri": {"A": 0.98, "B": 0.95, "C": 0.9}, "siparisler": [{"urun": "...", "miktar": 120, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Hizmet seviyesi ve sipariş miktarlarını öneren, ROP ve koli/MOQ kurallarını kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'stok_politikasi.csv')),
                   help='urun, abc, ort_gunluk_talep, talep_std, tedarik_gun, eldeki, yoldaki, moq, koli')
    p.add_argument('--review-days', type=float, default=7.0, help='Gözden geçirme (sipariş) dönemi, gün')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    need = ['urun', 'abc', 'ort_gunluk_talep', 'talep_std', 'tedarik_gun', 'eldeki', 'yoldaki', 'moq', 'koli']
    if [c for c in need if c not in columns]:
        raise ValueError('Eksik sütun: ' + ', '.join(c for c in need if c not in columns))
    num = lambda v: float(v.replace(',', '.'))
    items = {r['urun'].strip(): {'abc': r['abc'].strip().upper(), 'd': num(r['ort_gunluk_talep']), 'std': num(r['talep_std']), 'lead': num(r['tedarik_gun']),
                                 'eldeki': num(r['eldeki']), 'yoldaki': num(r['yoldaki']), 'moq': int(num(r['moq'])), 'koli': int(num(r['koli']))} for r in rows}
    out = output_dir(args.output)
    user = (f'Gözden geçirme dönemi {args.review_days:g} gün.\nKalemler (ABC · günlük talep ort/std · tedarik gün · eldeki · yoldaki · MOQ · koli):\n'
            + '\n'.join(f'- {k}: {v["abc"]} · {v["d"]:g}/{v["std"]:g} · {v["lead"]:g} · {v["eldeki"]:g} · {v["yoldaki"]:g} · {v["moq"]} · {v["koli"]}' for k, v in items.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(items, args.review_days))
    if result is None:
        return
    levels, tgt, orders = result
    rows_out = [{'urun': k, 'abc': v['abc'], 'hizmet_seviyesi': levels[v['abc']], 'emniyet_stoku': round(tgt[k]['ss']), 'rop': round(tgt[k]['rop']), 'pozisyon': tgt[k]['pozisyon'],
                 'siparis': orders.get(k, 0)} for k, v in items.items()]
    tabular.write_csv(out / 'siparis_onerileri.csv', list(rows_out[0]), rows_out)
    md = ['# Sipariş önerileri', '', f'Hizmet seviyeleri: A %{levels["A"] * 100:g}, B %{levels["B"] * 100:g}, C %{levels["C"] * 100:g}. '
          f'{len(orders)} kalem için sipariş, toplam {sum(orders.values())} birim.', '',
          md_table(['Ürün', 'ABC', 'Emniyet stoku', 'ROP', 'Pozisyon', 'Sipariş'], [[r['urun'], r['abc'], r['emniyet_stoku'], r['rop'], r['pozisyon'], r['siparis'] or '—'] for r in rows_out]), '',
          f'_Model: {meta["model"]} · Emniyet stoku ve ROP kodla hesaplandı; sipariş gerekliliği, koli/MOQ ve hedef pozisyon kodla denetlendi. Talep normal dağılım varsayımıyla; '
          'kampanya ve mevsimsellik ayrıca değerlendirilmelidir._']
    write_text(out / 'siparis-onerileri.md', '\n'.join(md))
    print(f'[OK] {len(orders)} kalem sipariş edilecek. Çıktı: {out / "siparis-onerileri.md"}')


if __name__ == '__main__':
    llm.run(main)
