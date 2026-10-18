"""Vardiya planlayıcı (AI agent): çağrı merkezi ya da mağaza için yarım saatlik talep tahmininden vardiya planı çıkarır. Her aralıkta gereken
kişi sayısı kodla hesaplanır (cagri-merkezi-personel bloğuyla aynı Erlang C hesabı, mola/devamsızlık payı dahil). Model yalnızca vardiyaları
(başlangıç, süre, kişi) önerir; plan kodla denetlenir: vardiyalar açılış–kapanış saatleri içinde ve aralık sınırlarında başlamalı, süreler
izinli uzunluklardan olmalı, HER aralıkta planlanan kişi gereken kişiden az olmamalı ve toplam adam-saat gerekenin en fazla --slack kadar
üstünde olmalı. Eksik kalan aralıklar ve fazla adam-saat sayılarıyla geri gönderilir. Çıktı: vardiyalar.csv, kapsama.csv, vardiya-plani.md."""
import argparse
from datetime import datetime, timedelta
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_text, md_table
from planlama import erlang


def hm(text):
    return datetime.strptime(text.strip(), '%H:%M')


def validator(slots, need, length_min, lengths, slack):
    start, end = slots[0], slots[-1] + timedelta(minutes=length_min)
    total_need = sum(need) * length_min / 60

    def validate(obj):
        shifts, problems = [], []
        for i, s in enumerate(obj.get('vardiyalar') or [], 1):
            try:
                b, h, k = hm(str(s.get('baslangic'))), float(s.get('sure_saat')), int(s.get('kisi'))
            except (TypeError, ValueError, AttributeError):
                problems.append(f'{i}. vardiya: baslangic SS:DD, sure_saat ve kisi gerekli')
                continue
            if h not in lengths:
                problems.append(f'{i}. vardiya: süre {h:g} saat izinli değil ({", ".join(f"{x:g}" for x in lengths)})')
            elif b not in slots:
                problems.append(f'{i}. vardiya: {b:%H:%M} bir aralık başlangıcı değil')
            elif b + timedelta(hours=h) > end:
                problems.append(f'{i}. vardiya: {b:%H:%M}+{h:g} saat kapanıştan ({end:%H:%M}) sonra bitiyor')
            elif k <= 0:
                problems.append(f'{i}. vardiya: kişi sayısı pozitif olmalı')
            else:
                shifts.append((b, h, k))
        if problems or not shifts:
            raise ValueError('; '.join(problems) or 'vardiya yok')
        cover = [sum(k for b, h, k in shifts if b <= t < b + timedelta(hours=h)) for t in slots]
        short = [(t, c, n) for t, c, n in zip(slots, cover, need) if c < n]
        planned = sum(h * k for _, h, k in shifts)
        if short:
            problems.append('eksik aralıklar: ' + ', '.join(f'{t:%H:%M} ({c}/{n})' for t, c, n in short[:10]))
        if planned > total_need * (1 + slack):
            problems.append(f'planlanan {planned:g} adam-saat, gereken {total_need:g}; fazlalık %{(planned / total_need - 1) * 100:.0f} (en fazla %{slack * 100:.0f})')
        if problems:
            raise ValueError('; '.join(problems))
        return shifts, cover, planned
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir iş gücü planlama uzmanısın. Yarım saatlik aralıklarda gereken kişi sayısını karşılayan, gereksiz fazla mesai yaratmayan vardiyalar '
          'öner: başlangıç saati aralık sınırında, süre izinli uzunluklardan biri. Her aralıkta planlanan kişi ≥ gereken kişi olmalı. '
          'Yanıt şeması: {"vardiyalar": [{"baslangic": "08:00", "sure_saat": 8, "kisi": 6}], "aciklama": "..."}')


def main():
    p = argparse.ArgumentParser(description='Yarım saatlik talepten Erlang C ihtiyacını hesaplayıp model önerisi vardiyaları kapsama açısından denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'gunluk_cagri_tahmini.csv')), help='aralik (SS:DD), cagri')
    p.add_argument('--aht', type=float, default=180.0)
    p.add_argument('--sl', type=float, default=0.80)
    p.add_argument('--target', type=float, default=20.0)
    p.add_argument('--shrinkage', type=float, default=0.30)
    p.add_argument('--lengths', default='4,6,8', help='İzinli vardiya uzunlukları (saat)')
    p.add_argument('--slack', type=float, default=0.25, help='Gerekene göre izin verilen fazla adam-saat oranı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    req = erlang(columns, rows, SimpleNamespace(interval='aralik', calls='cagri', aht=args.aht, length=1800.0, sl=args.sl, target=args.target, shrinkage=args.shrinkage))
    slots = [hm(o['aralik']) for o in req.rows]
    if any(b - a != timedelta(minutes=30) for a, b in zip(slots, slots[1:])):
        raise ValueError('Aralıklar ardışık yarım saatler olmalı.')
    need = [o['vardiya_kisi'] for o in req.rows]
    lengths = [float(x) for x in args.lengths.split(',') if x.strip()]
    out = output_dir(args.output)
    user = (f'Açılış {slots[0]:%H:%M}, kapanış {slots[-1] + timedelta(minutes=30):%H:%M}. İzinli vardiya uzunlukları: {", ".join(f"{x:g}" for x in lengths)} saat. '
            f'Fazla adam-saat sınırı %{args.slack * 100:.0f}.\nAralık · çağrı · gereken kişi (Erlang C, %{args.sl * 100:.0f}/{args.target:g} sn, mola payı %{args.shrinkage * 100:.0f}):\n'
            + '\n'.join(f'{o["aralik"]} · {o["cagri"]} · {o["vardiya_kisi"]}' for o in req.rows))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(slots, need, 30, lengths, args.slack))
    if result is None:
        return
    shifts, cover, planned = result
    tabular.write_csv(out / 'vardiyalar.csv', ['baslangic', 'bitis', 'sure_saat', 'kisi'],
                      [{'baslangic': f'{b:%H:%M}', 'bitis': f'{b + timedelta(hours=h):%H:%M}', 'sure_saat': f'{h:g}', 'kisi': k} for b, h, k in sorted(shifts)])
    tabular.write_csv(out / 'kapsama.csv', ['aralik', 'cagri', 'gereken', 'planlanan', 'fazla'],
                      [{'aralik': o['aralik'], 'cagri': o['cagri'], 'gereken': n, 'planlanan': c, 'fazla': c - n} for o, n, c in zip(req.rows, need, cover)])
    total_need = sum(need) / 2
    md = ['# Vardiya planı', '', f'{len(shifts)} vardiya, {sum(k for *_, k in shifts)} kişi; planlanan {planned:g} adam-saat, gereken {total_need:g} '
          f'(fazlalık %{(planned / total_need - 1) * 100:.0f}). Her aralıkta gereken kişi karşılanıyor.', '',
          md_table(['Başlangıç', 'Bitiş', 'Süre', 'Kişi'], [[f'{b:%H:%M}', f'{b + timedelta(hours=h):%H:%M}', f'{h:g} sa', k] for b, h, k in sorted(shifts)]), '',
          md_table(['Aralık', 'Gereken', 'Planlanan'], [[o['aralik'], n, c] for o, n, c in zip(req.rows, need, cover)]), '',
          f'_Model: {meta["model"]} · İhtiyaç Erlang C ile kodla hesaplandı; kapsama ve fazla adam-saat kodla denetlendi. Mola saatleri, yasal çalışma süreleri ve kişi bazında '
          'atama bu planın dışındadır._']
    write_text(out / 'vardiya-plani.md', '\n'.join(md))
    print(f'[OK] {len(shifts)} vardiya; {planned:g} adam-saat. Çıktı: {out / "vardiya-plani.md"}')


if __name__ == '__main__':
    llm.run(main)
