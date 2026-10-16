"""KVKK maskeleme: CSV/XLSX dosyasındaki kişisel veri sütunlarını bulur ve paylaşılabilir bir kopya üretir.
Sütunlar değerlerine (e-posta, TCKN, IBAN, telefon, kart no) ve adlarına (ad soyad, adres, doğum tarihi) göre bulunur.
Yöntemler: takma (tuzlu HMAC; aynı değer hep aynı takma ada döner, birleştirmeler çalışır), kismi (ilk/son karakterler kalır),
sil (boşaltır). Serbest metin sütunlarındaki gömülü kişisel veriler de maskelenir. Çıktı yeniden taranır.
İnternete bağlanmaz; girdi değiştirilmez. Çıktı: maskeli.csv, maskeleme-raporu.md, ozet.json."""
import argparse, base64, hashlib, hmac, os, re, secrets
from collections import Counter
from pathlib import Path
from common import console, here, output_dir, write_json, write_text, md_table, pii_kind, mask_text
import tabular

METHODS = ('takma', 'kismi', 'sil')
NAME_HINTS = re.compile(r'(^(ad|adı|adi|isim|name)$|soyad|isim|ad_?soyad|adsoyad|full_?name|first_?name|last_?name|adres|address|dogum|doğum|birth|'
                        r'(musteri|müşteri|kisi|kişi|calisan|çalışan|personel|yetkili|hasta)_?ad[iı]?$)', re.I)
PLACEHOLDER = re.compile(r'\[(E-POSTA|IBAN|TCKN|TELEFON|KART)\]')
NAME_KIND = {'adres': 'adres', 'address': 'adres', 'dogum': 'doğum tarihi', 'doğum': 'doğum tarihi', 'birth': 'doğum tarihi'}


def detect(columns, rows):
    """Return {column: kind} for columns that hold personal data (by values or by name)."""
    found = {}
    for name in columns:
        values = [r[name] for r in rows if r[name].strip()][:5000]
        # A value counts only when personal data is (almost) the whole cell; notes with an embedded phone stay free text.
        kinds = Counter(pii_kind(v) for v in values if pii_kind(v) and len(PLACEHOLDER.sub('', mask_text(v)[0]).strip(' -;,.')) <= 2)
        if kinds and sum(kinds.values()) >= max(1, 0.2 * len(values)):
            found[name] = kinds.most_common(1)[0][0]
            continue
        m = NAME_HINTS.search(name)
        if m:
            found[name] = next((v for k, v in NAME_KIND.items() if k in name.lower()), 'ad soyad')
    return found


def pseudonym(value, salt, prefix):
    digest = hmac.new(salt.encode('utf-8'), value.strip().casefold().encode('utf-8'), hashlib.sha256).digest()
    # Base32 letters keep pseudonyms from ever looking like a phone, TCKN or card number.
    return f"{prefix}-{base64.b32encode(digest).decode('ascii')[:12].lower()}"


def partial(value, kind):
    v = value.strip()
    if kind == 'e-posta' and '@' in v:
        user, domain = v.split('@', 1)
        return user[:1] + '***@' + domain
    digits = re.sub(r'\D', '', v)
    if kind in ('telefon', 'kart no') and digits:
        return '*' * (len(digits) - 4) + digits[-4:] if kind == 'kart no' else '*** *** ** ' + digits[-2:]
    if kind == 'TCKN' and digits:
        return digits[:2] + '*******' + digits[-2:]
    if kind == 'IBAN':
        compact = v.replace(' ', '')
        return compact[:4] + '*' * (len(compact) - 8) + compact[-4:]
    if kind == 'doğum tarihi':
        parsed = tabular.parse_date(v)
        return parsed[0].strftime('%Y') if parsed else '****'
    if kind == 'adres':
        return '[ADRES]'
    return ' '.join(w[:1] + '***' for w in v.split())


PREFIX = {'e-posta': 'EPOSTA', 'telefon': 'TEL', 'TCKN': 'TCKN', 'IBAN': 'IBAN', 'kart no': 'KART', 'adres': 'ADRES', 'doğum tarihi': 'DT', 'ad soyad': 'KISI'}


def mask_rows(columns, rows, targets, method, salt, free_text=True):
    """targets: {column: kind}. Returns (new_rows, stats)."""
    stats = {c: {'kind': k, 'method': method, 'changed': 0} for c, k in targets.items()}
    embedded = Counter()
    out = []
    for row in rows:
        new = dict(row)
        for c in columns:
            value = row[c]
            if not value.strip():
                continue
            if c in targets:
                kind = targets[c]
                new[c] = '' if method == 'sil' else partial(value, kind) if method == 'kismi' else pseudonym(value, salt, PREFIX.get(kind, 'KV'))
                stats[c]['changed'] += 1
            elif free_text:
                masked, counts = mask_text(value)
                if counts:
                    new[c] = masked
                    embedded[c] += sum(counts.values())
        out.append(new)
    return out, stats, dict(embedded)


def residual(columns, rows, targets):
    """Re-scan the masked rows: personal data left in any column (pseudonyms and partial masks must not match)."""
    left = Counter()
    for row in rows:
        for c in columns:
            if row[c].strip() and pii_kind(row[c]):
                left[c] += 1
    return dict(left)


def markdown(source, targets, stats, embedded, left, method, rows):
    lines = [f'# KVKK maskeleme — {source}', '', f'- Satır: **{rows}** · Maskelenen sütun: **{len(targets)}** · Yöntem: **{method}**',
             f"- Serbest metinde maskelenen gömülü değer: **{sum(embedded.values())}**",
             f"- Maskeleme sonrası tarama: **{'temiz' if not left else 'KALAN KİŞİSEL VERİ VAR: ' + ', '.join(f'{c} ({n})' for c, n in left.items())}**", '',
             '## Sütunlar', '', md_table(['Sütun', 'Bulunan tür', 'Yöntem', 'Değişen hücre'], [[c, s['kind'], s['method'], s['changed']] for c, s in stats.items()])]
    if embedded:
        lines += ['## Serbest metin', '', md_table(['Sütun', 'Maskelenen değer'], list(embedded.items()))]
    lines += ['## Notlar', '',
              '- Takma adlar tuzlu HMAC-SHA256 ile üretilir; tuz bilinmeden geri çevrilemez. Aynı tuzla aynı değer her dosyada aynı takma ada döner.',
              '- Takma ad verisi KVKK’ya göre hâlâ kişisel veri sayılabilir (anonim değildir). Paylaşmadan önce amaç ve alıcıyı değerlendirin.',
              '- Ad/adres sütunları sütun adından tahmin edilir; listede olmayan bir sütun varsa --columns ile ekleyin.']
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='CSV/XLSX dosyasındaki kişisel verileri maskeler (internet bağlantısı yok).')
    p.add_argument('--input', default=str(here('ornek', 'musteriler.csv')))
    p.add_argument('--sheet')
    p.add_argument('--method', choices=METHODS, default='takma', help='takma (varsayılan), kismi veya sil')
    p.add_argument('--columns', help='Ayrıca maskelenecek sütunlar (virgülle)')
    p.add_argument('--keep', help='Maskelenmeyecek sütunlar (virgülle)')
    p.add_argument('--salt', help='Takma ad tuzu; yoksa MASK_SALT ortam değişkeni, o da yoksa her çalışmada rastgele')
    p.add_argument('--no-free-text', action='store_true', help='Diğer sütunlardaki gömülü kişisel verileri tarama')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        columns, rows = tabular.read(args.input, args.sheet)
        if not columns:
            raise tabular.InputError('Dosyada başlık satırı yok.')
        targets = detect(columns, rows)
        for c in filter(None, (x.strip() for x in (args.columns or '').split(','))):
            if c not in columns:
                raise ValueError(f'--columns: "{c}" sütunu dosyada yok.')
            targets.setdefault(c, 'ad soyad')
        for c in filter(None, (x.strip() for x in (args.keep or '').split(','))):
            if c not in columns:
                raise ValueError(f'--keep: "{c}" sütunu dosyada yok.')
            targets.pop(c, None)
        salt = args.salt or os.environ.get('MASK_SALT') or ''
        random_salt = args.method == 'takma' and not salt
        salt = salt or secrets.token_hex(16)
        masked, stats, embedded = mask_rows(columns, rows, targets, args.method, salt, not args.no_free_text)
        left = residual(columns, masked, targets)
        out = output_dir(args.output)
        tabular.write_csv(out / 'maskeli.csv', columns, masked)
        write_json(out / 'ozet.json', {'source': Path(args.input).name, 'rows': len(rows), 'method': args.method, 'columns': stats,
                                       'embedded': embedded, 'residual': left, 'random_salt': random_salt})
        write_text(out / 'maskeleme-raporu.md', markdown(Path(args.input).name, targets, stats, embedded, left, args.method, len(rows)))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    if random_salt:
        print('[i] Rastgele tuz kullanıldı: takma adlar bu çalışmaya özel. Dosyalar arası tutarlılık için --salt veya MASK_SALT verin.')
    print(f"[{'OK' if not left else '!'}] {len(targets)} sütun maskelendi ({', '.join(targets) or '—'}), serbest metinde {sum(embedded.values())} değer. "
          f"Kalan kişisel veri: {sum(left.values())}. Çıktı: {out / 'maskeli.csv'}")
    if left:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
