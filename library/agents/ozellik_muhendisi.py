"""Özellik mühendisi (AI agent): bir sınıflandırma/regresyon veri setine yeni model özellikleri (feature) önerir; özellikler serbest kod değil,
küçük ve güvenli bir işlem dilinde tanımlanır (oran, fark, çarpım, log, kova, eşitlik, boş mu, tarihten bu yana gün). Modele kolon profili
gider, satırlar gönderilmez. Özellikler yerelde hesaplanır ve denetlenir: kolonlar var olmalı ve sayısal işlemlerde sayısal olmalı; hedef
ve kimlik kolonu kullanılamaz; SIZINTI kolonları (değeri ya da boşluğu hedefi neredeyse tam belirleyen, ör. yalnızca kaybedilen müşteride
dolu iptal_tarihi) kodla bulunur ve kullanılamaz; özellik sabit olmamalı ve hedefle |korelasyon| ≥ 0,95 olmamalı (sızıntı şüphesi).
Çıktı: ozellikli_veri.csv, ozellikler.py (aynı hesabı yapan bağımsız Python), ozellikler.md."""
import argparse, math, statistics
from datetime import date
import llm
import tabular
from common import here, output_dir, write_text, md_table, pii_kind

OPS = {'oran': 2, 'fark': 2, 'carpim': 2, 'log': 1, 'kova': 1, 'esit': 1, 'bos_mu': 1, 'gun_once': 1}


def num(v):
    return tabular.parse_number(v, None) if v and v.strip() else None


def compute(f, row, ref_day):
    op, a, b = f['islem'], f.get('a'), f.get('b')
    x = row.get(a, '')
    if op == 'bos_mu':
        return 1.0 if not x.strip() else 0.0
    if op == 'esit':
        return 1.0 if x.strip() == str(f['deger']) else 0.0
    if op == 'gun_once':
        d = tabular.parse_date(x) if x.strip() else None
        return float((ref_day - d[0].date()).days) if d else None
    xa = num(x)
    if op == 'log':
        return math.log1p(xa) if xa is not None and xa >= 0 else None
    if op == 'kova':
        return None if xa is None else float(sum(xa > e for e in f['sinirlar']))
    xb = num(row.get(b, ''))
    if xa is None or xb is None:
        return None
    return {'oran': xa / xb if xb else None, 'fark': xa - xb, 'carpim': xa * xb}[op]


def corr(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return 0.0
    mx, my = statistics.fmean(p[0] for p in pairs), statistics.fmean(p[1] for p in pairs)
    sx = math.sqrt(sum((p[0] - mx) ** 2 for p in pairs))
    sy = math.sqrt(sum((p[1] - my) ** 2 for p in pairs))
    return sum((p[0] - mx) * (p[1] - my) for p in pairs) / (sx * sy) if sx and sy else 0.0


def leaks(columns, rows, target, y):
    """Columns whose value or emptiness predicts the target almost perfectly."""
    out = {}
    for c in columns:
        if c == target:
            continue
        empty = [0.0 if r[c].strip() else 1.0 for r in rows]
        r_empty = abs(corr(empty, y))
        vals = [num(r[c]) for r in rows]
        r_val = abs(corr(vals, y)) if sum(v is not None for v in vals) > len(rows) * 0.9 else 0.0
        if max(r_empty, r_val) >= 0.95:
            out[c] = 'boşluğu' if r_empty >= r_val else 'değeri'
    return out


def validator(columns, rows, target, id_col, numeric, leak, y, ref_day):
    def validate(obj):
        feats, problems = [], []
        names = set(columns)
        for i, f in enumerate(obj.get('ozellikler') or [], 1):
            if not isinstance(f, dict):
                continue
            name, op = str(f.get('ad', '')).strip(), str(f.get('islem', '')).strip()
            used = [f.get('a'), f.get('b')][:OPS.get(op, 0)]
            why = []
            if not name.isidentifier() or name in names:
                why.append('ad geçersiz ya da mevcut kolonla/başka özellikle aynı')
            if op not in OPS:
                why.append(f'işlem {", ".join(OPS)} olmalı')
            else:
                for c in used:
                    if c not in columns:
                        why.append(f'{c} kolonu yok')
                    elif c in (target, id_col):
                        why.append(f'{c} hedef ya da kimlik kolonu')
                    elif c in leak:
                        why.append(f'{c} sızıntı kolonu ({leak[c]} hedefi neredeyse tam belirliyor)')
                    elif op in ('oran', 'fark', 'carpim', 'log', 'kova') and c not in numeric:
                        why.append(f'{c} sayısal değil')
                if op == 'kova' and not (isinstance(f.get('sinirlar'), list) and f['sinirlar'] and all(isinstance(x, (int, float)) for x in f['sinirlar'])):
                    why.append('kova için sayısal "sinirlar" listesi gerekli')
                if op == 'esit' and 'deger' not in f:
                    why.append('esit için "deger" gerekli')
            if why:
                problems.append(f'{i}. {name or "?"}: ' + '; '.join(why))
                continue
            spec = {'ad': name, 'islem': op, 'a': f.get('a'), 'b': f.get('b'), 'sinirlar': sorted(f.get('sinirlar') or []), 'deger': f.get('deger'), 'gerekce': str(f.get('gerekce', '')).strip()}
            vals = [compute(spec, r, ref_day) for r in rows]
            filled = [v for v in vals if v is not None]
            if len(set(filled)) < 2:
                problems.append(f'{name}: sabit ya da boş özellik')
                continue
            c = corr(vals, y)
            if abs(c) >= 0.95:
                problems.append(f'{name}: hedefle korelasyon {c:.2f}; sızıntı şüphesi')
                continue
            spec['korelasyon'] = round(c, 3)
            spec['dolu_orani'] = round(len(filled) / len(rows), 3)
            feats.append((spec, vals))
            names.add(name)
        if problems:
            raise ValueError(' | '.join(problems))
        if not feats:
            raise ValueError('en az bir özellik gerekli')
        return feats
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir makine öğrenmesi mühendisisin. Hedefi tahmin etmeye yardım edecek, iş anlamı olan yeni özellikler öner. İşlemler: '
          'oran(a,b)=a/b, fark(a,b), carpim(a,b), log(a)=log(1+a), kova(a, sinirlar), esit(a, deger), bos_mu(a), gun_once(a)=referans tarihten bu yana gün. '
          'Hedef, kimlik ve sızıntı kolonlarını kullanma. Yanıt şeması: {"ozellikler": [{"ad": "sepet_gelir_orani", "islem": "oran", "a": "sepet", "b": "gelir", '
          '"gerekce": "..."}, {"ad": "yas_kova", "islem": "kova", "a": "yas", "sinirlar": [30, 45, 60]}]}')


PY = '''"""Özellik hesabı (ozellik-muhendisi agent’ının ürettiği tanımlar). Kullanım: from ozellikler import ekle; yeni = ekle(satir)"""
import math
from datetime import date, datetime

TANIMLAR = {defs}
REFERANS = date.fromisoformat("{ref}")


def _sayi(v):
    v = (v or "").strip().replace(" ", "")
    if not v:
        return None
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".") if v.rfind(",") > v.rfind(".") else v.replace(",", "")
    elif "," in v:
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _hesap(f, s):
    op, x = f["islem"], s.get(f["a"], "")
    if op == "bos_mu":
        return 1.0 if not x.strip() else 0.0
    if op == "esit":
        return 1.0 if x.strip() == str(f["deger"]) else 0.0
    if op == "gun_once":
        try:
            return float((REFERANS - datetime.fromisoformat(x.strip()[:10]).date()).days)
        except ValueError:
            return None
    a = _sayi(x)
    if op == "log":
        return math.log1p(a) if a is not None and a >= 0 else None
    if op == "kova":
        return None if a is None else float(sum(a > e for e in f["sinirlar"]))
    b = _sayi(s.get(f["b"], ""))
    if a is None or b is None:
        return None
    return {{"oran": a / b if b else None, "fark": a - b, "carpim": a * b}}[op]


def ekle(satir):
    return {{**satir, **{{f["ad"]: _hesap(f, satir) for f in TANIMLAR}}}}
'''


def main():
    p = argparse.ArgumentParser(description='Güvenli işlem diliyle özellik öneren, sızıntıyı ve sabit özellikleri kodla eleyen agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'egitim_verisi.csv')))
    p.add_argument('--target', default='churn')
    p.add_argument('--id-column', default='musteri_id')
    p.add_argument('--reference-date', default='', help='gun_once için referans tarih (varsayılan: bugün)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in (args.target, args.id_column):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok. Sütunlar: {", ".join(columns)}')
    y = [num(r[args.target]) for r in rows]
    if any(v is None for v in y):
        raise ValueError('Hedef sütunu sayısal olmalı (0/1 ya da sayı).')
    ref_day = date.fromisoformat(args.reference_date) if args.reference_date else date.today()
    numeric = {c for c in columns if sum(num(r[c]) is not None for r in rows) >= 0.9 * len(rows)}
    leak = leaks(columns, rows, args.target, y)
    out = output_dir(args.output)
    prof = []
    for c in columns:
        vals = [r[c] for r in rows if r[c].strip()]
        pii = any(pii_kind(v) for v in vals[:200])
        line = f'- {c}: {"sayısal" if c in numeric else "metin/tarih"}, dolu %{len(vals) / len(rows) * 100:.0f}, {len(set(vals))} tekil'
        if c in numeric:
            ns = [num(v) for v in vals]
            line += f', min {min(ns):g}, medyan {statistics.median(ns):g}, max {max(ns):g}, hedefle korelasyon {corr([num(r[c]) for r in rows], y):.2f}'
        elif not pii and len(set(vals)) <= 15:
            line += ', değerler: ' + ', '.join(sorted(set(vals)))
        if c == args.target:
            line += ' (HEDEF)'
        if c == args.id_column:
            line += ' (KİMLİK)'
        if c in leak:
            line += f' (SIZINTI: {leak[c]} hedefi belirliyor; kullanma)'
        prof.append(line)
    user = f'{len(rows)} satır; hedef {args.target} (ortalama {statistics.fmean(y):.3f}). Referans tarih {ref_day}.\nKolonlar:\n' + '\n'.join(prof)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(columns, rows, args.target, args.id_column, numeric, leak, y, ref_day))
    if result is None:
        return
    names = [s['ad'] for s, _ in result]
    data = [{**r, **{s['ad']: ('' if v is None else f'{v:.6g}') for s, v in zip([s for s, _ in result], vals)}} for r, vals in zip(rows, zip(*[v for _, v in result]))]
    tabular.write_csv(out / 'ozellikli_veri.csv', columns + names, data)
    defs = [{k: s[k] for k in ('ad', 'islem', 'a', 'b', 'sinirlar', 'deger')} for s, _ in result]
    write_text(out / 'ozellikler.py', PY.format(defs=repr(defs), ref=ref_day.isoformat()))
    ranked = sorted(result, key=lambda sv: -abs(sv[0]['korelasyon']))
    md = ['# Özellik önerileri', '', f'{len(result)} özellik eklendi; hedef: {args.target}. Sızıntı olarak elenen kolonlar: {", ".join(f"{c} ({w})" for c, w in leak.items()) or "yok"}.', '',
          md_table(['Özellik', 'İşlem', 'Kolonlar', 'Hedefle korelasyon', 'Dolu %', 'Gerekçe'],
                   [[s['ad'], s['islem'], ', '.join(x for x in (s['a'], s['b']) if x), s['korelasyon'], round(s['dolu_orani'] * 100), s['gerekce']] for s, _ in ranked]), '',
          f'_Model: {meta["model"]} · Özellikler kodla hesaplandı; sızıntı, sabitlik ve aşırı korelasyon denetlendi. Korelasyon tek değişkenli bir ön elemedir; '
          'özelliklerin katkısı model eğitimiyle (çapraz doğrulama) ölçülmelidir. ozellikler.py aynı hesabı bağımsız yapar._']
    write_text(out / 'ozellikler.md', '\n'.join(md))
    print(f'[OK] {len(result)} özellik. Çıktı: {out / "ozellikler.md"}')


if __name__ == '__main__':
    llm.run(main)
