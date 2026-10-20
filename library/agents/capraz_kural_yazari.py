"""Çapraz kural yazarı (AI agent): bir tablodaki kolonlar arası iş kurallarını yazar: “teslim tarihi sipariş tarihinden önce olamaz”, “tutar =
adet × birim fiyat − indirim”, “iptal edilen siparişin iptal tarihi dolu olmalı”. Kurallar satir-kurallari bloğunun güvenli ifade dilinde
yazılır, böylece her gün aynı blokla çalıştırılabilir. Modele kolon profili gider, satırlar gönderilmez. Her kural kodla denetlenir: ifade
derlenmeli; “kesin” kural satırların en az %98’inde, “uyarı” kuralı en az %80’inde tutmalı (bozan satırlar veri hatasıdır); ve kural bir
şey söylemeli: kullandığı kolonlardan biri satırlar arasında karıştırıldığında hâlâ %99 tutuyorsa “boş kural” sayılıp reddedilir.
Çıktı: kurallar.txt (satir-kurallari --rules girdisi), ihlaller.csv, capraz-kurallar.md."""
import argparse, random
import llm
import tabular
from common import here, output_dir, write_text, md_table, pii_kind
from temizlik import Expr

LIMITS = {'kesin': 0.98, 'uyari': 0.80}


def rate(expr, rows):
    ok = sum(1 for r in rows if expr(r, rows) is True)
    return ok / len(rows), ok


def validator(columns, rows, seed=7):
    def validate(obj):
        rules, problems = [], []
        for i, k in enumerate(obj.get('kurallar') or [], 1):
            text, kind = str(k.get('kural', '')).strip(), str(k.get('tur', '')).strip().lower().replace('uyarı', 'uyari')
            if kind not in LIMITS:
                problems.append(f'{i}. kural: tur kesin ya da uyari olmalı')
                continue
            try:
                expr = Expr(text, columns)
            except ValueError as exc:
                problems.append(f'{i}. kural “{text}”: {exc}')
                continue
            share, ok = rate(expr, rows)
            if share < LIMITS[kind]:
                bad = [n for n, r in enumerate(rows, 2) if expr(r, rows) is not True][:5]
                problems.append(f'“{text}” satırların %{share * 100:.1f}’inde tutuyor ({kind} için en az %{LIMITS[kind] * 100:.0f}); bozan satırlar: {", ".join(map(str, bad))}')
                continue
            used = sorted(set(expr.names.values()))
            trivial = True
            for c in used:
                vals = [r[c] for r in rows]
                random.Random(seed).shuffle(vals)
                shuffled = [{**r, c: v} for r, v in zip(rows, vals)]
                if rate(Expr(text, columns), shuffled)[0] < 0.99:
                    trivial = False
                    break
            if trivial:
                problems.append(f'“{text}” kolonlar karıştırılınca da tutuyor; kolonlar arası bir ilişki söylemiyor (tek kolonlu sınırlar için kalite-kurali agent’ını kullanın)')
                continue
            rules.append({'kural': text, 'tur': kind, 'aciklama': str(k.get('aciklama', '')).strip(), 'tutan': ok, 'kolonlar': used, 'expr': expr})
        if problems:
            raise ValueError(' | '.join(problems))
        if len(rules) < 2:
            raise ValueError('en az 2 kural gerekli')
        return rules
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri kalitesi analistisin. Kolon profiline bakarak kolonlar arası iş kuralları yaz (tarih sırası, tutar hesabı, durum–tarih '
          'tutarlılığı, koşullu zorunluluk). Dil: kolon adları ya da [Boşluklu Ad], sayılar, \'metin\', + - * / %, karşılaştırmalar, and/or/not, abs, round, '
          'min, max, len, lower, upper, contains, startswith, num, empty. ISO tarihler (YYYY-AA-GG) metin olarak karşılaştırılabilir. Boş olabilen kolonları '
          'empty(...) ile koru. tur: kesin (her satırda tutmalı) ya da uyari (çoğunlukla tutmalı). Yanıt şeması: {"kurallar": [{"kural": '
          '"empty(teslim_tarihi) or teslim_tarihi >= siparis_tarihi", "tur": "kesin", "aciklama": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Kolonlar arası iş kurallarını satir-kurallari bloğunun dilinde yazan ve tüm satırlarda deneyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'siparis_teslimat.csv')))
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if len(rows) < 10:
        raise ValueError('Kural çıkarmak için en az 10 satır gerekli.')
    out = output_dir(args.output)
    prof = []
    for c in columns:
        vals = [r[c] for r in rows if r[c].strip()]
        style = tabular.number_style(vals)
        nums = [tabular.parse_number(v, style) for v in vals]
        line = f'- {c}: dolu %{len(vals) / len(rows) * 100:.0f}, {len(set(vals))} tekil'
        if vals and all(n is not None for n in nums):
            line += f', sayı min {min(nums):g} max {max(nums):g}'
        elif vals and all(tabular.parse_date(v) for v in vals):
            line += f', tarih {min(vals)} – {max(vals)}'
        elif not any(pii_kind(v) for v in vals[:200]) and len(set(vals)) <= 12:
            line += ', değerler: ' + ', '.join(sorted(set(vals)))
        prof.append(line)
    user = f'{len(rows)} satır.\nKolonlar:\n' + '\n'.join(prof)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(columns, rows))
    if result is None:
        return
    write_text(out / 'kurallar.txt', '; '.join(r['kural'] for r in result) + '\n')
    viol = [{'satir': n, 'kural': r['kural'], 'tur': r['tur'], columns[0]: row[columns[0]]} for r in result for n, row in enumerate(rows, 2) if r['expr'](row, rows) is not True]
    tabular.write_csv(out / 'ihlaller.csv', ['satir', 'kural', 'tur', columns[0]], viol)
    md = ['# Kolonlar arası kurallar', '', f'{len(result)} kural, {len(rows)} satır; {len(viol)} ihlal (veri hatası adayı).', '',
          md_table(['Kural', 'Tür', 'Tutan', 'İhlal', 'Açıklama'], [[f'`{r["kural"]}`', r['tur'], f'%{r["tutan"] / len(rows) * 100:.1f}', len(rows) - r['tutan'], r['aciklama']] for r in result]), '',
          'Her gün çalıştırmak için: satir-kurallari kod bloğuna `--rules "$(cat kurallar.txt)"` verin.', '',
          f'_Model: {meta["model"]} · Kurallar tüm satırlarda kodla çalıştırıldı; tutma oranı ve kolon karıştırma (boş kural) testi uygulandı._']
    write_text(out / 'capraz-kurallar.md', '\n'.join(md))
    print(f'[OK] {len(result)} kural, {len(viol)} ihlal. Çıktı: {out / "capraz-kurallar.md"}')


if __name__ == '__main__':
    llm.run(main)
