"""Stok transfer önerici (AI agent): mağazalar arasında ürün transferi önerir; stoğu birkaç günlük satışa yetmeyen mağazaları, fazla stoklu
mağazalardan besler. Mağaza ve ürün başına stok gün sayısı (stok ÷ günlük satış tahmini), açık ve fazla kodla hesaplanır; modele yalnızca
bu tablo gider. Öneri kodla denetlenir: veren mağaza transfer sonrası en az belirli gün stokta kalmalı, alan mağaza bu sınırın altındaysa
(ürünün ağdaki fazlası yetiyorsa) sınıra ulaşmalı, zaten yeterli stoğu olan mağazaya ya da üst sınırı aşacak kadar transfer yapılmamalı,
miktarlar pozitif tam sayı olmalı. Çıktı: transferler.csv, stok_gunleri.csv, stok-transferi.md."""
import argparse, math
from collections import defaultdict
import llm
import tabular
from common import here, output_dir, write_text, md_table


def cover(stock, sales):
    return stock / sales if sales else math.inf


def validator(inv, min_c, max_c):
    def validate(obj):
        problems = []
        moves = []
        for e in obj.get('transferler') or []:
            u, a, b = str(e.get('urun', '')).strip(), str(e.get('kaynak', '')).strip(), str(e.get('hedef', '')).strip()
            try:
                q = int(e.get('adet'))
            except (TypeError, ValueError):
                problems.append(f'{u} {a} → {b}: adet tam sayı olmalı')
                continue
            if (u, a) not in inv or (u, b) not in inv or a == b or q <= 0:
                problems.append(f'{u} {a} → {b} ({q}): ürün/mağaza yok, kaynak ve hedef aynı ya da adet pozitif değil')
                continue
            moves.append((u, a, b, q))
        if problems:
            raise ValueError('; '.join(problems))
        after = {k: v['stok'] for k, v in inv.items()}
        for u, a, b, q in moves:
            after[(u, a)] -= q
            after[(u, b)] += q
        recv = {(u, b) for u, _, b, _ in moves}
        give = {(u, a) for u, a, _, _ in moves}
        for k in give:
            if cover(after[k], inv[k]['satis']) < min_c:
                problems.append(f'{k[0]} {k[1]}: transfer sonrası {after[k]} adet = {cover(after[k], inv[k]["satis"]):.1f} gün; veren mağaza en az {min_c:g} gün tutmalı')
        for k in recv & give:
            problems.append(f'{k[0]} {k[1]} hem veriyor hem alıyor')
        for k in recv:
            before = cover(inv[k]['stok'], inv[k]['satis'])
            if before >= min_c:
                problems.append(f'{k[0]} {k[1]}: zaten {before:.1f} gün stoklu; transfer gereksiz')
            elif cover(after[k], inv[k]['satis']) > max_c:
                problems.append(f'{k[0]} {k[1]}: transfer sonrası {cover(after[k], inv[k]["satis"]):.1f} gün; üst sınır {max_c:g}')
        by_u = defaultdict(list)
        for k in inv:
            by_u[k[0]].append(k)
        for u, keys in by_u.items():
            surplus = sum(max(0, math.floor(inv[k]['stok'] - min_c * inv[k]['satis'])) for k in keys)
            need = {k: math.ceil(min_c * inv[k]['satis'] - inv[k]['stok']) for k in keys if cover(inv[k]['stok'], inv[k]['satis']) < min_c}
            if need and surplus >= sum(need.values()):
                short = [k for k in need if cover(after[k], inv[k]['satis']) < min_c]
                if short:
                    problems.append(f'{u}: ağdaki fazla ({surplus} adet) yetiyor ama ' + ', '.join(f'{k[1]} {cover(after[k], inv[k]["satis"]):.1f} gün' for k in short)
                                    + f' {min_c:g} günün altında kalıyor')
        if problems:
            raise ValueError('; '.join(problems))
        return moves, after
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir perakende stok planlamacısısın. Stoğu yetmeyen mağazaları fazla stoklu mağazalardan besleyecek transferleri öner: veren mağaza '
          'en az sınır kadar gün stokta kalsın, alan mağaza sınıra ulaşsın ama üst sınırı aşmasın; az sayıda transfer tercih et. Yanıt şeması: '
          '{"transferler": [{"urun": "...", "kaynak": "...", "hedef": "...", "adet": 20, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Mağazalar arası stok transferi öneren ve stok gün kurallarını kodla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'magaza_stoklari.csv')), help='magaza, urun, stok, gunluk_satis_tahmini')
    p.add_argument('--min-cover', type=float, default=7, help='Mağazada tutulacak en az stok günü')
    p.add_argument('--max-cover', type=float, default=14, help='Transfer sonrası alan mağazada en fazla stok günü')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    inv = {(r['urun'].strip(), r['magaza'].strip()): {'stok': int(float(r['stok'].replace(',', '.'))), 'satis': float(r['gunluk_satis_tahmini'].replace(',', '.'))} for r in rows}
    out = output_dir(args.output)
    by_u = defaultdict(list)
    for (u, m), v in inv.items():
        by_u[u].append((m, v))
    lines = []
    for u, ms in by_u.items():
        lines.append(f'- {u}: ' + '; '.join(f'{m} {v["stok"]} adet, {v["satis"]:g}/gün = {cover(v["stok"], v["satis"]):.1f} gün' for m, v in ms))
    user = (f'Stok günü = stok ÷ günlük satış tahmini. En az {args.min_cover:g} gün, alan mağazada transfer sonrası en fazla {args.max_cover:g} gün.\n' + '\n'.join(lines))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(inv, args.min_cover, args.max_cover))
    if result is None:
        return
    moves, after = result
    tabular.write_csv(out / 'transferler.csv', ['urun', 'kaynak', 'hedef', 'adet'], [{'urun': u, 'kaynak': a, 'hedef': b, 'adet': q} for u, a, b, q in moves])
    table = [{'urun': u, 'magaza': m, 'stok_once': inv[(u, m)]['stok'], 'stok_sonra': after[(u, m)], 'gun_once': round(cover(inv[(u, m)]['stok'], inv[(u, m)]['satis']), 1),
              'gun_sonra': round(cover(after[(u, m)], inv[(u, m)]['satis']), 1)} for u, m in inv]
    tabular.write_csv(out / 'stok_gunleri.csv', list(table[0]), table)
    low_before = sum(t_['gun_once'] < args.min_cover for t_ in table)
    low_after = sum(t_['gun_sonra'] < args.min_cover for t_ in table)
    md = ['# Stok transferi', '', f'{len(moves)} transfer, toplam {sum(q for *_, q in moves)} adet. {args.min_cover:g} günün altındaki mağaza-ürün sayısı {low_before} → {low_after}.', '',
          md_table(['Ürün', 'Kaynak', 'Hedef', 'Adet'], [list(m_) for m_ in moves]), '',
          md_table(['Ürün', 'Mağaza', 'Stok önce', 'Stok sonra', 'Gün önce', 'Gün sonra'], [list(t_.values()) for t_ in table if t_['stok_once'] != t_['stok_sonra']]), '',
          f'_Model: {meta["model"]} · Stok günleri, ağdaki fazla ve transfer sonrası durum kodla hesaplandı. Transfer maliyeti ve sevkiyat süresi ayrıca değerlendirilmelidir._']
    write_text(out / 'stok-transferi.md', '\n'.join(md))
    print(f'[OK] {len(moves)} transfer. Çıktı: {out / "stok-transferi.md"}')


if __name__ == '__main__':
    llm.run(main)
