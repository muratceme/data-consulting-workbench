"""Sentetik veri tasarımcısı (AI agent): gerçek bir tablonun yerine test/geliştirme ortamında kullanılacak sahte veri üretir. Modele
satırlar değil, kolon profili gider (tip, boş oranı, tekil sayısı, min/max; düşük kardinaliteli ve kişisel olmayan kolonlarda değer
listesi). Kişisel veri içeren kolonların değerleri hiç gönderilmez. Model her kolon için bir üretici seçer; üreticiler yerelde sabit
tohumla çalıştırılır ve sonuç denetlenir: her kolon kapsanmalı, kişisel veri kolonları yalnızca sahte üreticilerle (sahte_ad,
sahte_eposta, sahte_telefon, desen) üretilebilir, kategori değerleri profilde görülen değerlerden olmalı, sayı/tarih aralığı
gerçek aralığın içinde kalmalı, anahtar kolon tekil olmalı. Üretilen veride gerçek tablodan birebir satır ya da gerçek bir kişisel
değer (e-posta, telefon, TCKN, ad) bulunursa yanıt reddedilir.
Çıktı: sentetik.csv, uretici.json, sentetik-veri.md."""
import argparse, json, random, re, statistics
from datetime import date, timedelta
import llm
import tabular
from common import here, output_dir, write_text, write_json, md_table, pii_kind

FIRST = ['Ahmet', 'Ayşe', 'Mehmet', 'Fatma', 'Mustafa', 'Emine', 'Ali', 'Hatice', 'Hüseyin', 'Zeynep', 'Murat', 'Elif', 'Can', 'Selin', 'Burak', 'Deniz']
LAST = ['Yılmaz', 'Kaya', 'Demir', 'Şahin', 'Çelik', 'Yıldız', 'Yıldırım', 'Öztürk', 'Aydın', 'Özdemir', 'Arslan', 'Doğan', 'Kılıç', 'Aslan', 'Çetin', 'Koç']
FAKE = ('sahte_ad', 'sahte_eposta', 'sahte_telefon', 'desen')
KINDS = FAKE + ('sayi', 'tarih', 'kategori', 'sira')
NAME_HINT = re.compile(r'(^|_)(ad|adi|soyad|ad_soyad|isim|unvan|musteri_adi|name)($|_)', re.I)


def profile(columns, rows):
    prof = {}
    for c in columns:
        vals = [r[c].strip() for r in rows]
        filled = [v for v in vals if v]
        nums = [tabular.parse_number(v, tabular.number_style(filled)) for v in filled]
        dates = [tabular.parse_date(v) for v in filled]
        pii = next((k for k in (pii_kind(v) for v in filled[:500]) if k), None) or ('ad' if NAME_HINT.search(c) else None)
        p = {'bos_orani': round(1 - len(filled) / max(len(vals), 1), 3), 'tekil': len(set(filled)), 'kisisel': pii}
        if pii:  # personal data: no values, no min/max, only the shape
            p.update(tip='metin', uzunluk=max((len(v) for v in filled), default=0))
        elif filled and all(d for d in dates):
            p.update(tip='tarih', min=min(d[0] for d in dates).date().isoformat(), max=max(d[0] for d in dates).date().isoformat())
        elif filled and all(n is not None for n in nums):
            p.update(tip='sayi', min=min(nums), max=max(nums), ortalama=round(statistics.fmean(nums), 2), ondalik=any('.' in str(n) and n != int(n) for n in nums))
        else:
            p.update(tip='metin', uzunluk=max((len(v) for v in filled), default=0))
        if not pii and p['tekil'] <= 30:
            counts = {}
            for v in filled:
                counts[v] = counts.get(v, 0) + 1
            p['degerler'] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
        prof[c] = p
    return prof


def shape(pattern, rnd):
    """# digit, A upper letter, ? lower letter, anything else literal."""
    return ''.join(str(rnd.randint(0, 9)) if ch == '#' else chr(rnd.randint(65, 90)) if ch == 'A' else chr(rnd.randint(97, 122)) if ch == '?' else ch for ch in pattern)


def generate(spec, n, seed, avoid=frozenset()):
    """avoid: lower-cased real personal values; a fake name/phone that happens to be real is drawn again."""
    rnd = random.Random(seed)

    def fresh(make):
        for _ in range(50):
            v = make()
            if v.lower() not in avoid:
                return v
        return v
    cols = {}
    for c, g in spec.items():
        k, out = g['tur'], []
        for i in range(n):
            if g.get('bos_orani', 0) and rnd.random() < g['bos_orani']:
                out.append('')
                continue
            if k == 'sira':
                out.append(f'{g.get("onek", "")}{int(g.get("baslangic", 1)) + i:0{int(g.get("hane", 1))}d}')
            elif k == 'sayi':
                lo, hi = float(g['min']), float(g['max'])
                v = min(hi, max(lo, rnd.gauss(float(g['ortalama']), (hi - lo) / 6))) if g.get('dagilim') == 'normal' and 'ortalama' in g else rnd.uniform(lo, hi)
                out.append(f'{v:.2f}' if g.get('ondalik') else str(int(round(v))))
            elif k == 'tarih':
                lo, hi = date.fromisoformat(g['min']), date.fromisoformat(g['max'])
                out.append((lo + timedelta(days=rnd.randint(0, (hi - lo).days))).isoformat())
            elif k == 'kategori':
                vals, w = list(g['degerler']), [float(x) for x in g['degerler'].values()]
                out.append(rnd.choices(vals, w)[0])
            elif k == 'sahte_ad':
                out.append(fresh(lambda: f'{rnd.choice(FIRST)} {rnd.choice(LAST)}'))
            elif k == 'sahte_eposta':
                out.append(f'test{i + 1:05d}@ornek.invalid')
            elif k == 'sahte_telefon':
                out.append(fresh(lambda: f'0555 000 {rnd.randint(0, 99):02d} {rnd.randint(0, 99):02d}'))
            elif k == 'desen':
                out.append(shape(g['desen'], rnd))
        cols[c] = out
    return [{c: cols[c][i] for c in spec} for i in range(n)]


def validator(columns, rows, prof, n, seed):
    real_rows = {tuple(r[c].strip() for c in columns) for r in rows}
    real_pii = {r[c].strip().lower() for r in rows for c in columns if prof[c]['kisisel'] and r[c].strip()}

    def validate(obj):
        spec, problems = {}, []
        given = obj.get('kolonlar') or {}
        for c in columns:
            g = given.get(c)
            if not isinstance(g, dict):
                problems.append(f'{c}: üretici yok')
                continue
            k, p = str(g.get('tur', '')), prof[c]
            if k not in KINDS:
                problems.append(f'{c}: tür {", ".join(KINDS)} olmalı')
                continue
            if p['kisisel'] and k not in FAKE:
                problems.append(f'{c}: kişisel veri ({p["kisisel"]}) kolonu yalnızca {", ".join(FAKE)} ile üretilebilir')
                continue
            try:
                if k == 'sayi':
                    if p['tip'] != 'sayi':
                        raise ValueError('kolon sayı değil')
                    if float(g['min']) < p['min'] or float(g['max']) > p['max'] or float(g['min']) > float(g['max']):
                        raise ValueError(f'aralık {g["min"]}–{g["max"]} gerçek aralığın ({p["min"]}–{p["max"]}) dışında')
                elif k == 'tarih':
                    if p['tip'] != 'tarih':
                        raise ValueError('kolon tarih değil')
                    if not (p['min'] <= date.fromisoformat(g['min']).isoformat() <= date.fromisoformat(g['max']).isoformat() <= p['max']):
                        raise ValueError(f'tarih aralığı gerçek aralığın ({p["min"]}–{p["max"]}) dışında')
                elif k == 'kategori':
                    vals = g.get('degerler') or {}
                    unknown = [v for v in vals if v not in p.get('degerler', {})]
                    if not vals or unknown:
                        raise ValueError('profilde olmayan değer: ' + ', '.join(unknown[:5]) if unknown else 'değer listesi boş')
                    if any(float(x) < 0 for x in vals.values()):
                        raise ValueError('ağırlıklar negatif olamaz')
                elif k == 'desen' and not str(g.get('desen', '')).strip():
                    raise ValueError('desen boş')
                bos = float(g.get('bos_orani', 0) or 0)
                if not 0 <= bos < 1:
                    raise ValueError('bos_orani 0–1 arası olmalı')
            except (KeyError, ValueError, TypeError) as exc:
                problems.append(f'{c}: {exc}')
                continue
            spec[c] = {**g, 'tur': k}
        keys = [c for c in obj.get('anahtar') or [] if c in columns]
        if problems:
            raise ValueError('; '.join(problems))
        data = generate(spec, n, seed, frozenset(real_pii))
        for c in keys:
            vals = [d[c] for d in data]
            if len(set(vals)) != len(vals) or '' in vals:
                problems.append(f'anahtar kolon {c} tekil/dolu değil (sira ya da yeterince geniş desen kullan)')
        leaked = [i for i, d in enumerate(data) if tuple(d[c] for c in columns) in real_rows]
        if leaked:
            problems.append(f'{len(leaked)} üretilen satır gerçek tabloda birebir var')
        pii = sorted({d[c] for d in data for c in columns if d[c].strip().lower() in real_pii})
        if pii:
            problems.append(f'{len(pii)} gerçek kişisel değer üretildi; sahte üretici kullan')
        if problems:
            raise ValueError('; '.join(problems))
        return spec, keys, data
    return validate


SYSTEM = (llm.SAFETY + ' Sen test verisi hazırlayan bir veri mühendisisin. Kolon profiline bakarak her kolon için bir üretici seç. Türler: '
          'sira (onek, baslangic, hane), sayi (min, max, ondalik, isteğe bağlı dagilim "normal" + ortalama), tarih (min, max: YYYY-AA-GG), '
          'kategori (degerler: {değer: ağırlık}, yalnızca profildeki değerler), sahte_ad, sahte_eposta, sahte_telefon, desen (# rakam, A büyük harf, '
          '? küçük harf, diğer karakterler aynen). Kişisel veri kolonlarında yalnızca sahte_* ya da desen kullan. Aralıklar gerçek min/max içinde kalmalı. '
          'Her kolona bos_orani (0–1) verebilirsin. Tekil olması gereken kolonları "anahtar" listesine yaz. '
          'Yanıt şeması: {"anahtar": ["musteri_no"], "kolonlar": {"musteri_no": {"tur": "sira", "onek": "T", "baslangic": 1, "hane": 6}, ...}, "notlar": "..."}')


def main():
    p = argparse.ArgumentParser(description='Gerçek tablonun profilinden sahte test verisi üreten agent (satırlar ve kişisel değerler gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'musteri_kayitlari.csv')))
    p.add_argument('--rows', type=int, default=200, help='Üretilecek satır sayısı')
    p.add_argument('--seed', type=int, default=7)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    prof = profile(columns, rows)
    out = output_dir(args.output)
    user = f'Tablo: {len(rows)} satır, {len(columns)} kolon. Üretilecek: {args.rows} satır.\nKolon profili (JSON):\n' + json.dumps(prof, ensure_ascii=False, indent=1)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(columns, rows, prof, args.rows, args.seed))
    if result is None:
        return
    spec, keys, data = result
    tabular.write_csv(out / 'sentetik.csv', columns, data)
    write_json(out / 'uretici.json', {'tohum': args.seed, 'satir': args.rows, 'anahtar': keys, 'kolonlar': spec})
    comp = []
    for c in columns:
        pr, real_vals = prof[c], [r[c] for r in rows]
        fake_vals = [d[c] for d in data]
        line = [c, spec[c]['tur'], pr['kisisel'] or '', f'{pr["bos_orani"] * 100:.0f}', f'{sum(1 for v in fake_vals if not v) / len(fake_vals) * 100:.0f}']
        if pr['tip'] == 'sayi' and spec[c]['tur'] == 'sayi':
            fn = [float(v) for v in fake_vals if v]
            line.append(f'ort. {pr["ortalama"]:g} → {statistics.fmean(fn):.2f}' if fn else '')
        elif spec[c]['tur'] == 'kategori':
            line.append(f'{len(set(v for v in real_vals if v.strip()))} → {len(set(v for v in fake_vals if v))} farklı değer')
        else:
            line.append(f'{len(set(fake_vals))} farklı değer')
        comp.append(line)
    md = ['# Sentetik veri', '', f'{len(rows)} satırlık tablonun profilinden {args.rows} satır üretildi (tohum {args.seed}; aynı tohum aynı veriyi verir).', '',
          md_table(['Kolon', 'Üretici', 'Kişisel veri', 'Gerçek boş %', 'Sentetik boş %', 'Karşılaştırma'], comp), '',
          f'Denetimler geçti: gerçek tablodan birebir satır yok, gerçek kişisel değer yok, aralıklar gerçek min/max içinde{", anahtar kolonlar tekil (" + ", ".join(keys) + ")" if keys else ""}.', '',
          f'_Model: {meta["model"]} · Modele satır gönderilmedi; kişisel veri kolonlarının değerleri profile de konmadı. Kolonlar arası ilişkiler (ör. il–ilçe uyumu, '
          'tarih sırası) korunmaz; ilişki gerektiren testler için ayrıca kural ekleyin._']
    write_text(out / 'sentetik-veri.md', '\n'.join(md))
    print(f'[OK] {args.rows} sentetik satır, {len(columns)} kolon. Çıktı: {out / "sentetik-veri.md"}')


if __name__ == '__main__':
    llm.run(main)
