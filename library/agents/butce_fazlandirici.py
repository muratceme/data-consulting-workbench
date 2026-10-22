"""Bütçe fazlandırıcı (AI agent): yıllık bütçeyi hesap başına aylara dağıtmak için ağırlık önerir; geçen yılın aylık dağılımını temel
alır, bütçe notlarındaki bilinen olayları (peşin lisans ödemesi, fuar, kampanya) ilgili aylara yansıtır. Geçen yılın aylık payları kodla
hesaplanır; modele yalnızca paylar, yıllık tutar ve notlar gider. Öneri kodla denetlenir: 12 ağırlık negatif olmamalı ve toplamı 1 olmalı;
bir ayın payı geçen yılın payından %25’ten fazla saparsa o ay istisna olarak işaretlenmeli ve hesabın notunda adıyla geçmeli. Tutarlar
butce-dagit bloğundaki gibi kuruşa yuvarlanır, fark son aya eklenir; toplam yıllık bütçeye eşittir. Çıktı: aylik_butce.csv, butce-fazlandirma.md."""
import argparse
from collections import defaultdict
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table

MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']


def validator(shares, notes, tolerance):
    def validate(obj):
        seen = set()
        problems, out = [], {}
        for e in obj.get('hesaplar') or []:
            h = str(e.get('hesap', '')).strip()
            if h not in shares or h in seen:
                problems.append(f'hesap “{h}” yok ya da iki kez yazıldı')
                continue
            seen.add(h)
            try:
                w = [float(x) for x in e.get('agirliklar') or []]
                exc = {int(x) for x in e.get('istisna_aylar') or []}
            except (TypeError, ValueError):
                problems.append(f'{h}: agirliklar sayı, istisna_aylar ay numarası (1–12) olmalı')
                continue
            if len(w) != 12 or min(w) < 0 or abs(sum(w) - 1) > 0.001:
                problems.append(f'{h}: 12 negatif olmayan ağırlık gerekli, toplamı 1 olmalı (toplam {sum(w):.4f})')
                continue
            note = tr.fold(notes.get(h, ''))
            for m in sorted(exc):
                if not 1 <= m <= 12 or tr.fold(MONTHS[m - 1]) not in note:
                    problems.append(f'{h}: {MONTHS[m - 1] if 1 <= m <= 12 else m} istisna denmiş ama hesabın notunda bu ay geçmiyor')
            for m in range(12):
                s = shares[h][m]
                if m + 1 not in exc and s and abs(w[m] - s) / s > tolerance:
                    problems.append(f'{h} {MONTHS[m]}: pay %{100 * w[m]:.1f}, geçen yıl %{100 * s:.1f}; %{100 * tolerance:g}’ten fazla sapma notta gerekçelenmeli (istisna_aylar)')
            out[h] = (w, sorted(exc), str(e.get('gerekce', '')).strip())
        missing = [h for h in shares if h not in seen]
        if missing:
            problems.append('ağırlığı yazılmayan hesap: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems[:20]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir FP&A analistisin. Her hesabın yıllık bütçesini aylara dağıtmak için 12 ağırlık öner (toplam 1). Geçen yılın aylık dağılımını '
          'temel al; yalnızca notlarda adı geçen aylarda bilinen olayları (peşin ödeme, fuar, kampanya) yansıtarak sap ve bu ayları istisna_aylar listesine '
          'yaz. Yanıt şeması: {"hesaplar": [{"hesap": "770", "agirliklar": [0.08, ...], "istisna_aylar": [1], "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Yıllık bütçeyi geçen yıl mevsimselliği ve bütçe notlarıyla aylara dağıtan agent.')
    p.add_argument('--input', default=str(here('ornek', 'yillik_butce.csv')), help='hesap, hesap_adi, departman, yillik_butce')
    p.add_argument('--history', default=str(here('ornek', 'gecen_yil_aylik_gider.csv')), help='hesap, ay (YYYY-AA), tutar')
    p.add_argument('--notes', default=str(here('ornek', 'butce_notlari.csv')), help='hesap, not')
    p.add_argument('--year', type=int, default=2026)
    p.add_argument('--tolerance', type=float, default=0.25, help='İstisna olmadan izin verilen göreli sapma')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('hesap', 'yillik_butce'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    style = tabular.number_style([r['yillik_butce'] for r in rows])
    budget = {r['hesap'].strip(): (r, tabular.parse_number(r['yillik_butce'], style) or 0.0) for r in rows}
    hc, hr = tabular.read(args.history)
    hs = tabular.number_style([r['tutar'] for r in hr])
    hist = defaultdict(lambda: [0.0] * 12)
    for r in hr:
        m = int(r['ay'].strip()[-2:])
        hist[r['hesap'].strip()][m - 1] += tabular.parse_number(r['tutar'], hs) or 0.0
    shares = {h: ([v / sum(hist[h]) for v in hist[h]] if sum(hist.get(h, [])) else [1 / 12] * 12) for h in budget}
    _, nr = tabular.read(args.notes) if args.notes else ([], [])
    notes = {r['hesap'].strip(): r['not'].strip() for r in nr}
    out = output_dir(args.output)
    user = (f'{args.year} bütçesi. Hesaplar (yıllık bütçe · geçen yıl aylık paylar % Ocak→Aralık · not):\n'
            + '\n'.join(f'- {h} {r.get("hesap_adi", "")}: {v:,.0f} · ' + '/'.join(f'{100 * s:.1f}' for s in shares[h]) + f' · {notes.get(h, "—")}'
                        for h, (r, v) in budget.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(shares, notes, args.tolerance))
    if result is None:
        return
    long, table = [], []
    for h, (w, exc, why) in result.items():
        r, v = budget[h]
        parts = [round(v * x, 2) for x in w]
        parts[-1] = round(v - sum(parts[:-1]), 2)
        base = {c: r[c] for c in columns if c != 'yillik_butce'}
        long += [{**base, 'donem': f'{args.year}-{i + 1:02d}', 'tutar': p_} for i, p_ in enumerate(parts)]
        table.append([h, r.get('hesap_adi', ''), f'{v:,.0f}', ', '.join(MONTHS[m - 1] for m in exc) or '—',
                      f'{MONTHS[max(range(12), key=lambda i: parts[i])]} {max(parts):,.0f}', why])
    tabular.write_csv(out / 'aylik_butce.csv', [c for c in columns if c != 'yillik_butce'] + ['donem', 'tutar'], long)
    md = ['# Bütçe fazlandırma', '', f'{len(result)} hesap {args.year} yılının aylarına dağıtıldı; her hesabın aylık toplamı yıllık bütçeye eşit.', '',
          md_table(['Hesap', 'Ad', 'Yıllık', 'İstisna aylar', 'En yüksek ay', 'Gerekçe'], table), '',
          f'_Model: {meta["model"]} · Modele geçen yıl payları, yıllık tutar ve notlar gitti; ağırlık toplamı, sapma sınırı ve istisna ayların notta geçmesi kodla denetlendi._']
    write_text(out / 'butce-fazlandirma.md', '\n'.join(md))
    print(f'[OK] {len(result)} hesap, {len(long)} aylık satır. Çıktı: {out / "butce-fazlandirma.md"}')


if __name__ == '__main__':
    llm.run(main)
