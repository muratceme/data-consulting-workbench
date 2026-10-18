"""Log ayrıştırıcı (AI agent): karışık biçimli bir log dosyasından (uygulama logu, nginx erişim logu, Java/Python yığın izi) her biçim
için adlandırılmış gruplu bir regex yazdırır ve dosyanın tamamını tabloya çevirir. Modele yalnızca her biçimden birkaç örnek satır gider
(kişisel veriler maskelenir). Desenler yerelde derlenir ve DOSYANIN TAMAMINDA denenir: derlenmeyen, iç içe niceleyici içeren, zaman ya
da mesaj grubu olmayan, zaman alanı tarih olarak okunamayan desen ve satırların --kapsama oranından azını ayrıştıran desen seti
gerekçesiyle geri gönderilir. Boşlukla başlayan satırlar (yığın izi) önceki kaydın devamı sayılır.
Çıktı: log_tablosu.csv, eslesmeyen.txt, desenler.json, fluent-bit-parsers.conf, log-desenleri.md."""
import argparse, re
from collections import Counter
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table, mask_text

NESTED = re.compile(r'\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]')
NAME = re.compile(r'^[a-z][a-z0-9_]{0,40}$')
TIME_FORMATS = ('%d/%b/%Y:%H:%M:%S %z', '%b %d %H:%M:%S', '%b  %d %H:%M:%S', '%d.%m.%Y %H:%M:%S', '%d/%m/%Y %H:%M:%S', '%Y/%m/%d %H:%M:%S')


def parse_time(text):
    """ISO 8601 (with ',' or '.' fractions, T, Z or offset), nginx/Apache, syslog and Turkish day-first stamps -> datetime or None."""
    from datetime import datetime
    t = (text or '').strip().strip('[]')
    if not t:
        return None
    iso = re.sub(r'(\d{2}:\d{2}:\d{2}),(\d+)', r'\1.\2', t).replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        pass
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


def records(lines):
    """Join continuation lines (leading whitespace) to the previous record. Returns [(line_no, first_line, extra_lines)]."""
    out = []
    for no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if line[:1] in (' ', '\t') and out:
            out[-1][2].append(line.strip())
        else:
            out.append((no, line.rstrip('\r\n'), []))
    return out


def shape(line):
    """Rough format key: digits → 9, letters → a, so lines of the same format group together."""
    s = re.sub(r'\d+', '9', line[:60])
    return re.sub(r'[A-Za-zÇĞİÖŞÜçğıöşü]+', 'a', s)


def samples(recs, per_shape=3, limit=40):
    groups = {}
    for _, line, _ in recs:
        groups.setdefault(shape(line), []).append(line)
    picked = []
    for lines in sorted(groups.values(), key=len, reverse=True):
        step = max(1, len(lines) // per_shape)
        picked += lines[::step][:per_shape]
        if len(picked) >= limit:
            break
    return picked[:limit], len(groups)


def validator(recs, coverage):
    lines = [line for _, line, _ in recs]

    def validate(obj):
        pats = obj.get('desenler')
        if not isinstance(pats, list) or not pats:
            raise ValueError('"desenler" boş.')
        compiled, names = [], set()
        for i, p in enumerate(pats, 1):
            if not isinstance(p, dict):
                raise ValueError(f'{i}. desen nesne değil')
            name, rx = str(p.get('ad', '')).strip(), str(p.get('desen', '')).strip()
            if not NAME.match(name) or name in names:
                raise ValueError(f'{i}. desenin adı “{name}” geçersiz ya da tekrar ediyor (küçük harf, rakam, alt çizgi)')
            names.add(name)
            if NESTED.search(rx):
                raise ValueError(f'{name}: iç içe niceleyici var ({NESTED.search(rx).group(0)}); felaket geri izleme riski')
            try:
                c = re.compile(rx)
            except re.error as exc:
                raise ValueError(f'{name}: desen derlenmiyor ({exc})') from None
            groups = set(c.groupindex)
            missing = {'zaman', 'mesaj'} - groups
            if missing:
                raise ValueError(f'{name}: {", ".join(sorted(missing))} adlı grup yok; her desende (?P<zaman>...) ve (?P<mesaj>...) olmalı')
            bad = [g for g in groups if not NAME.match(g)]
            if bad:
                raise ValueError(f'{name}: grup adları küçük harf/rakam/alt çizgi olmalı ({", ".join(bad)})')
            compiled.append((name, c, str(p.get('aciklama', '')).strip()))
        parsed, unmatched, bad_time, used = [], [], [], Counter()
        for line in lines:
            for name, c, _ in compiled:
                m = c.fullmatch(line)
                if m:
                    used[name] += 1
                    if not parse_time(m.group('zaman')):
                        bad_time.append((name, m.group('zaman')))
                    break
            else:
                unmatched.append(line)
        if bad_time:
            raise ValueError('zaman grubu tarih olarak okunamadı: ' + '; '.join(f'{n}: “{mask_text(v)[0]}”' for n, v in bad_time[:5])
                             + '. zaman grubu yalnızca tarih/saat metnini içermeli')
        rate = 1 - len(unmatched) / len(lines)
        if rate < coverage:
            raise ValueError(f'desenler satırların %{rate * 100:.1f}’ini ayrıştırdı (gereken %{coverage * 100:.0f}). Eşleşmeyen örnekler: '
                             + ' | '.join(mask_text(u[:200])[0] for u in unmatched[:6]))
        idle = [n for n, _, _ in compiled if not used[n]]
        if idle:
            raise ValueError(f'hiçbir satıra uymayan desen: {", ".join(idle)}; kaldır ya da düzelt')
        return compiled, used, unmatched
    return validate


SYSTEM = (llm.SAFETY + ' Sen log ayrıştırma uzmanısın. Verilen örnek satırlardaki her log biçimi için Python re ile derlenen, satırın TAMAMINI '
          '(fullmatch) eşleyen bir desen yaz. Her desende (?P<zaman>...) yalnızca tarih/saat metnini, (?P<mesaj>...) serbest metni alsın; '
          'seviye, servis, ip, metot, yol, durum, sure_ms gibi diğer alanlar için de adlandırılmış grup kullan (küçük harf, alt çizgi). '
          'İç içe niceleyici kullanma; mümkünse [^ ]+ ve [^"]* gibi sınırlı sınıflar kullan. [E-POSTA], [TELEFON] gibi maskeli değerler gerçek '
          'dosyada gerçek değerdir; onları genel bir sınıfla yakala. Boşlukla başlayan devam satırları (yığın izi) zaten önceki kayda eklenir, '
          'onlar için desen yazma. Yanıt şeması: {"desenler": [{"ad": "uygulama", "desen": "(?P<zaman>...) ...", "aciklama": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Karışık log dosyasını adlandırılmış gruplu regex desenleriyle tabloya çeviren agent.')
    p.add_argument('--input', default=str(here('ornek', 'servis_loglari.log')))
    p.add_argument('--coverage', type=float, default=0.98, help='Ayrıştırılması gereken satır oranı (0-1)')
    p.add_argument('--encoding', default='utf-8')
    llm.add_arguments(p)
    args = p.parse_args()
    text = Path(args.input).read_text(encoding=args.encoding, errors='replace')
    recs = records(text.splitlines())
    if not recs:
        raise ValueError('Log dosyası boş.')
    picked, formats = samples(recs)
    out = output_dir(args.output)
    user = (f'Log dosyası: {len(recs)} kayıt, yaklaşık {formats} farklı satır biçimi. Örnek satırlar (her biçimden birkaç tane):\n'
            + '\n'.join(picked))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(recs, args.coverage))
    if result is None:
        return
    compiled, used, unmatched = result
    fields = []
    for _, c, _ in compiled:
        fields += [g for g in c.groupindex if g not in fields and g not in ('zaman', 'mesaj')]
    cols = ['satir', 'desen', 'zaman'] + fields + ['mesaj', 'devam_satiri']
    rows = []
    for no, line, extra in recs:
        for name, c, _ in compiled:
            m = c.fullmatch(line)
            if m:
                d = parse_time(m.group('zaman'))
                row = {k: '' for k in cols}
                row.update({k: (v or '') for k, v in m.groupdict().items()})
                row.update({'satir': no, 'desen': name, 'zaman': d.isoformat(sep=' ') if d else m.group('zaman'),
                            'mesaj': ' ⏎ '.join([m.group('mesaj') or ''] + extra), 'devam_satiri': len(extra)})
                rows.append(row)
                break
    tabular.write_csv(out / 'log_tablosu.csv', cols, rows)
    write_text(out / 'eslesmeyen.txt', '\n'.join(unmatched))
    write_json(out / 'desenler.json', [{'ad': n, 'desen': c.pattern, 'aciklama': a} for n, c, a in compiled])
    conf = []
    for n, c, _ in compiled:  # Fluent Bit (Onigmo) uses (?<name>...) for named groups
        conf += ['[PARSER]', f'    Name        {n}', '    Format      regex', f'    Regex       ^{re.sub(r"[(][?]P<", "(?<", c.pattern)}$',
                 '    # zaman alanını kayıt zamanı yapmak için Time_Key zaman ve biçiminize uygun Time_Format ekleyin', '']
    write_text(out / 'fluent-bit-parsers.conf', '\n'.join(conf))
    levels = Counter((r.get('seviye') or '').upper() for r in rows if r.get('seviye'))
    md = ['# Log desenleri', '', f'{len(recs)} kayıt, {len(rows)} ayrıştırıldı (%{len(rows) / len(recs) * 100:.1f}), {len(unmatched)} eşleşmedi; '
          f'{sum(r["devam_satiri"] for r in rows)} devam satırı (yığın izi) kayıtlara eklendi.', '',
          md_table(['Desen', 'Kayıt', 'Alanlar', 'Açıklama'], [[n, used[n], ', '.join(c.groupindex), a] for n, c, a in compiled]), '']
    if levels:
        md += ['## Seviye dağılımı', '', md_table(['Seviye', 'Kayıt'], levels.most_common()), '']
    md += ['## Desenler', ''] + [f'**{n}**\n\n```\n{c.pattern}\n```\n' for n, c, _ in compiled]
    md += [f'_Model: {meta["model"]} · Desenler dosyanın tamamında denendi (kapsama eşiği %{args.coverage * 100:.0f}). '
           'fluent-bit-parsers.conf Fluent Bit parser biçimindedir; Fluent Bit ile çalıştırılarak test edilmedi._']
    write_text(out / 'log-desenleri.md', '\n'.join(md))
    print(f'[OK] {len(rows)}/{len(recs)} kayıt ayrıştırıldı, {len(compiled)} desen. Çıktı: {out / "log-desenleri.md"}')


if __name__ == '__main__':
    llm.run(main)
