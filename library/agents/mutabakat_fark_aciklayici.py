"""Mutabakat farkı açıklayıcı (AI agent): geçiş sonrası eski ve yeni sistemin rapor çıktılarını anahtarla karşılaştırır (anahtar-mutabakati
bloğuyla aynı hesap) ve fark gruplarının (sütun bazında değişen değerler, hedefte eksik ve fazla satırlar) nedenini modele sınıflandırtır:
yuvarlama, saat dilimi, birim/ölçek, büyük-küçük harf/boşluk, filtre (koşulla) ya da gerçek hata. Modele yalnızca grup özetleri ve her
mekanik nedenin grubu ne kadar açıkladığı gider. Sınıflandırma kodla denetlenir: iddia edilen neden gruptaki farkların en az belirli
payını gerçekten açıklamalı (filtre koşulu eksik satırların hepsinde doğru, eşleşen satırların hiçbirinde doğru olmamalı), bir mekanik
neden grubu açıklıyorsa “gerçek hata” denemez; açıklanamayan satırlar gerçek hata olarak listelenir. Çıktı: fark_siniflari.csv,
gercek_hatalar.csv, mutabakat-farklari.md."""
import argparse, re
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table
from gecis import key_recon

CAUSES = ('yuvarlama', 'saat_dilimi', 'birim', 'yazim', 'filtre', 'gercek_hata')


def num(v):
    return tabular.parse_number(v) if v else None


def when(v):
    try:
        return datetime.fromisoformat(v.strip())
    except (ValueError, AttributeError):
        return None


def fits(cause, d, cond=None, src_rows=None):
    a, b = d['kaynak'], d['hedef']
    if cause == 'yuvarlama':
        x, y = num(a), num(b)
        return x is not None and y is not None and abs(x - y) <= 0.011
    if cause == 'saat_dilimi':
        x, y = when(a), when(b)
        return bool(x and y) and (x - y).total_seconds() % 3600 == 0 and 0 < abs((x - y).total_seconds()) <= 14 * 3600
    if cause == 'birim':
        x, y = num(a), num(b)
        return bool(x and y) and any(abs(x * f - y) <= 1e-9 * max(1, abs(y)) or abs(y * f - x) <= 1e-9 * max(1, abs(x)) for f in (100, 1000, 1e6))
    if cause == 'yazim':
        return a != b and ' '.join(tr.fold(a).split()) == ' '.join(tr.fold(b).split())
    if cause == 'filtre':
        return bool(cond) and src_rows is not None and src_rows.get(d['anahtar'], {}).get(cond[0], '').strip() == cond[1]
    return True


def validator(groups, src_by_key, matched_keys, min_share):
    auto = {g: {c: sum(fits(c, d) for d in ds) / len(ds) for c in ('yuvarlama', 'saat_dilimi', 'birim', 'yazim')} for g, ds in groups.items()}

    def validate(obj):
        problems, out, seen = [], {}, set()
        for e in obj.get('gruplar') or []:
            g, c = str(e.get('grup', '')).strip(), str(e.get('neden', '')).strip()
            if g not in groups or g in seen:
                problems.append(f'grup “{g}” yok ya da iki kez yazıldı')
                continue
            seen.add(g)
            if c not in CAUSES:
                problems.append(f'{g}: neden {", ".join(CAUSES)} olmalı')
                continue
            cond = None
            if c == 'filtre':
                k = e.get('kosul') or {}
                cond = (str(k.get('kolon', '')).strip(), str(k.get('deger', '')).strip())
                hits = [m for m in matched_keys if src_by_key.get(m, {}).get(cond[0], '').strip() == cond[1]]
                if hits:
                    problems.append(f'{g}: {cond[0]} = {cond[1]} koşulu eşleşen {len(hits)} satırda da doğru; filtreyi açıklamaz')
            share = sum(fits(c, d, cond, src_by_key) for d in groups[g]) / len(groups[g])
            if c == 'gercek_hata':
                best = max(auto[g].items(), key=lambda kv: kv[1])
                if best[1] >= min_share:
                    problems.append(f'{g}: farkların %{100 * best[1]:.0f} kadarı {best[0]} ile açıklanıyor; gerçek hata denemez')
            elif share < min_share:
                problems.append(f'{g}: {c} farkların yalnızca %{100 * share:.0f} kadarını açıklıyor (en az %{100 * min_share:g})')
            out[g] = (c, cond, share, str(e.get('aciklama', '')).strip())
        missing = [g for g in groups if g not in seen]
        if missing:
            problems.append('nedeni yazılmayan grup: ' + ', '.join(missing))
        if problems:
            raise ValueError('; '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri geçişi test uzmanısın. Eski ve yeni sistem çıktıları arasındaki her fark grubu için neden seç: yuvarlama, saat_dilimi, birim '
          '(100/1000 kat), yazim (büyük-küçük harf/boşluk), filtre (eksik/fazla satırlar; kosul ile: hangi kolon hangi değer) ya da gercek_hata. Yanıt şeması: '
          '{"gruplar": [{"grup": "tutar", "neden": "yuvarlama", "aciklama": "..."}, {"grup": "HEDEFTE EKSİK", "neden": "filtre", "kosul": {"kolon": "durum", "deger": "iptal"}}]}')


def main():
    p = argparse.ArgumentParser(description='Eski ve yeni rapor çıktısındaki farkları gruplayıp nedenini sınıflandıran ve iddiaları kodla sınayan agent.')
    p.add_argument('--input', default=str(here('ornek', 'eski_rapor.csv')), help='Eski sistem çıktısı')
    p.add_argument('--target', default=str(here('ornek', 'yeni_rapor.csv')), help='Yeni sistem çıktısı')
    p.add_argument('--key', default='fatura_no')
    p.add_argument('--min-share', type=float, default=0.9, help='Nedenin açıklaması gereken en az fark payı')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    res = key_recon(columns, rows, SimpleNamespace(target=args.target, key=args.key, ignore_case=False))
    groups = defaultdict(list)
    for d in res.rows:
        groups[d['sutun'] or d['durum']].append(d)
    if not groups:
        raise ValueError('Fark yok; iki çıktı aynı.')
    src_by_key = {r[args.key].strip(): r for r in rows}
    missing_keys = {d['anahtar'] for d in res.rows if d['durum'] == 'HEDEFTE EKSİK'}
    changed = {d['anahtar'] for d in res.rows if d['sutun']}
    matched = [k for k in src_by_key if k not in missing_keys]
    cats = {c: sorted({r[c].strip() for r in rows}) for c in columns if c != args.key and len({r[c].strip() for r in rows}) <= 8}
    out = output_dir(args.output)
    lines = []
    for g, ds in groups.items():
        shares = ', '.join(f'{c} %{100 * sum(fits(c, d) for d in ds) / len(ds):.0f}' for c in ('yuvarlama', 'saat_dilimi', 'birim', 'yazim'))
        ex = '; '.join(f'{d["kaynak"]} → {d["hedef"]}' for d in ds[:3] if d['sutun'])
        lines.append(f'- {g}: {len(ds)} fark · mekanik nedenlerin açıkladığı pay: {shares}' + (f' · örnek: {ex}' if ex else ''))
        if not ds[0]['sutun'] and g == 'HEDEFTE EKSİK':
            for c, vals in cats.items():
                dist = {v: sum(src_by_key[k][c].strip() == v for k in missing_keys) for v in vals}
                lines.append(f'  - eksik satırlarda {c}: ' + ', '.join(f'{v} {n}' for v, n in dist.items() if n))
    user = (f'{len(rows)} kaynak satır; {res.summary.get("ayni", 0)} satır aynı, {len(changed)} satırda değer farkı, {len(missing_keys)} satır hedefte eksik.\n'
            'Fark grupları:\n' + '\n'.join(lines) + '\nKaynaktaki kategorik sütunların değerleri: '
            + '; '.join(f'{c}: ' + ', '.join(vals) for c, vals in cats.items()))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(groups, src_by_key, matched, args.min_share))
    if result is None:
        return
    table, errors = [], []
    for g, (c, cond, share, why) in result.items():
        for d in groups[g]:
            ok = fits(c, d, cond, src_by_key) and c != 'gercek_hata'
            if not ok:
                errors.append({'anahtar': d['anahtar'], 'grup': g, 'kaynak': d['kaynak'], 'hedef': d['hedef']})
        table.append({'grup': g, 'fark': len(groups[g]), 'neden': c, 'kosul': f'{cond[0]} = {cond[1]}' if cond else '', 'acikladigi_pay': round(share, 3), 'aciklama': why})
    tabular.write_csv(out / 'fark_siniflari.csv', list(table[0]), table)
    tabular.write_csv(out / 'gercek_hatalar.csv', ['anahtar', 'grup', 'kaynak', 'hedef'], errors)
    md = ['# Mutabakat farkları', '', f'{len(rows)} satır karşılaştırıldı; {sum(t_["fark"] for t_ in table)} fark {len(table)} grupta. {len(errors)} fark mekanik bir nedenle '
          'açıklanmıyor ve gerçek hata olarak incelenmeli.', '',
          md_table(['Grup', 'Fark', 'Neden', 'Koşul', 'Açıkladığı pay', 'Açıklama'], [[t_['grup'], t_['fark'], t_['neden'], t_['kosul'] or '—', f'%{100 * t_["acikladigi_pay"]:.0f}', t_['aciklama']] for t_ in table]), '',
          md_table(['Anahtar', 'Grup', 'Eski', 'Yeni'], [list(e.values()) for e in errors]) if errors else 'Gerçek hata yok.', '',
          f'_Model: {meta["model"]} · Farklar anahtar-mutabakati bloğuyla kodla bulundu; nedenler satır satır kodla sınandı._']
    write_text(out / 'mutabakat-farklari.md', '\n'.join(md))
    print(f'[OK] {len(table)} grup, {len(errors)} gerçek hata. Çıktı: {out / "mutabakat-farklari.md"}')


if __name__ == '__main__':
    llm.run(main)
