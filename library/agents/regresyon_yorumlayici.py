"""Regresyon yorumlayıcı (AI agent): bir hedef değişkeni (haftalık satış gibi) açıklayıcı değişkenlerle (reklam harcaması, indirim oranı)
en küçük kareler yöntemiyle yerelde modeller (dogrusal-regresyon bloğuyla aynı hesap) ve modele yalnızca katsayı tablosunu ve R²’yi verir;
satırlar gönderilmez. Model her değişken için etki yönünü ve kısa bir yorum yazar. Her iddia kodla denetlenir: p < 0,05 olan değişken
“pozitif/negatif” (katsayının işaretiyle) olmalı, p ≥ 0,05 olan “anlamsız” olmalı; metindeki her sayı tablodan gelmeli; nedensellik
iddiası (“artırır”, “neden olur”, “sağlar”) reddedilir, çünkü gözlemsel regresyon ilişki gösterir.
Çıktı: katsayilar.csv, regresyon-yorumu.md."""
import argparse, re
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_text, md_table
from analiz import regresyon
from donem_yorumu import unknown_numbers

CAUSAL = re.compile(r'\b(artır\w*|azalt\w*|neden ol\w*|sebep ol\w*|yol aç\w*|sağla\w*|etkiler\b|etkilemekte\w*)', re.I)


def validator(coef, r2, names):
    nums = {abs(x) for c in coef for x in c[1:]} | {round(r2, 3), round(r2 * 100, 1), 0.05, 5}

    def validate(obj):
        problems, out = [], {}
        for e in obj.get('degiskenler') or []:
            if not isinstance(e, dict):
                continue
            name = str(e.get('ad', '')).strip()
            row = next((c for c in coef if c[0] == name and name != 'sabit'), None)
            if not row:
                problems.append(f'“{name}” modelde yok')
                continue
            beta, p = row[1], row[4]
            want = 'anlamsız' if p >= 0.05 else ('pozitif' if beta > 0 else 'negatif')
            said = str(e.get('etki', '')).strip().lower().replace('anlamsiz', 'anlamsız')
            if said != want:
                problems.append(f'{name}: katsayı {beta}, p={p} → etki “{want}” olmalı (“{said}” denmiş)')
            out[name] = (want, str(e.get('yorum', '')).strip())
        missing = [n for n in names if n not in out]
        if missing:
            problems.append('yorumlanmayan değişken: ' + ', '.join(missing))
        text = ' '.join([str(obj.get('ozet', ''))] + [v[1] for v in out.values()])
        hit = CAUSAL.search(text)
        if hit:
            problems.append(f'nedensellik iddiası (“{hit.group(0)}”): gözlemsel regresyon yalnızca ilişki gösterir, “ilişkili / birlikte hareket ediyor” de')
        bad = unknown_numbers(text, nums, names)
        if bad:
            problems.append('tabloda olmayan sayılar: ' + ', '.join(bad[:8]))
        if problems:
            raise ValueError('; '.join(problems))
        return out, str(obj.get('ozet', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir istatistikçisin. Kodla hesaplanmış çoklu doğrusal regresyon tablosunu iş diline çevir. Her değişken için etki: pozitif, negatif '
          '(p < 0,05 ve katsayının işareti) ya da anlamsız (p ≥ 0,05). Katsayıyı “diğer değişkenler sabitken bir birim artışa eşlik eden değişim” olarak anlat; '
          'nedensellik kurma (artırır, sağlar, neden olur deme). Yalnızca tablodaki sayıları kullan. '
          'Yanıt şeması: {"degiskenler": [{"ad": "reklam_harcamasi", "etki": "pozitif", "yorum": "..."}], "ozet": "..."}')


def main():
    p = argparse.ArgumentParser(description='Regresyonu yerelde kurup katsayıları kodla denetlenen biçimde yorumlatan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'pazarlama_haftalik.csv')))
    p.add_argument('--y', default='satis')
    p.add_argument('--x', default='reklam_harcamasi,indirim_orani')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    res = regresyon(columns, rows, SimpleNamespace(y=args.y, x=args.x))
    coef = res.tables[0][2]
    names = [c[0] for c in coef if c[0] != 'sabit']
    r2, adj, n = res.summary['r2'], res.summary['duzeltilmis_r2'], res.summary['n']
    out = output_dir(args.output)
    user = (f'Hedef: {args.y}; n = {n}; R² = {r2}; düzeltilmiş R² = {adj}.\nKatsayılar (değişken · katsayı · std. hata · t · p):\n'
            + '\n'.join(' · '.join(str(x) for x in c) for c in coef))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(coef, r2, names))
    if result is None:
        return
    effects, summary = result
    tabular.write_csv(out / 'katsayilar.csv', ['degisken', 'katsayi', 'std_hata', 't', 'p', 'etki'],
                      [{'degisken': c[0], 'katsayi': c[1], 'std_hata': c[2], 't': c[3], 'p': c[4], 'etki': effects.get(c[0], ('', ''))[0]} for c in coef])
    md = ['# Regresyon yorumu', '', f'Hedef **{args.y}**, {n} gözlem; R² {r2}, düzeltilmiş R² {adj}.', '',
          md_table(['Değişken', 'Katsayı', 'p', 'Etki', 'Yorum'], [[c[0], c[1], c[4], effects[c[0]][0], effects[c[0]][1]] for c in coef if c[0] in effects]), '',
          summary, '', f'_Model: {meta["model"]} · Katsayılar kodla hesaplandı; etki yönleri p-değeri ve işaretle, metindeki sayılar tabloyla denetlendi, nedensellik dili reddedildi. '
          'Doğrusallık, artıkların dağılımı ve çoklu doğrusallık ayrıca kontrol edilmelidir._']
    write_text(out / 'regresyon-yorumu.md', '\n'.join(md))
    print(f'[OK] {len(names)} değişken yorumlandı (R² {r2}). Çıktı: {out / "regresyon-yorumu.md"}')


if __name__ == '__main__':
    llm.run(main)
