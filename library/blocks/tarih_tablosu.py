"""Tarih tablosu: Power BI, Qlik, Tableau ve veri ambarı için Türkçe tarih boyutu üretir. Türkçe ay/gün adları, ISO hafta,
çeyrek, mali yıl, hafta sonu, resmî tatil (sabit ulusal tatiller kodda; dinî bayramlar tatiller.csv ile) ve iş günü sırası içerir.
İnternete bağlanmaz. Çıktı: tarih-tablosu.csv, tarih-tablosu.sql (CREATE + INSERT), tarih-ozeti.md."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from common import console, here, output_dir, write_text, md_table
import tabular

AYLAR = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']
GUNLER = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar']
# Fixed national holidays (Law 2429). 28 Ekim is a half day from 13:00.
SABIT = {(1, 1): 'Yılbaşı', (4, 23): 'Ulusal Egemenlik ve Çocuk Bayramı', (5, 1): 'Emek ve Dayanışma Günü',
         (5, 19): 'Atatürk’ü Anma, Gençlik ve Spor Bayramı', (7, 15): 'Demokrasi ve Millî Birlik Günü',
         (8, 30): 'Zafer Bayramı', (10, 29): 'Cumhuriyet Bayramı'}
YARIM = {(10, 28): 'Cumhuriyet Bayramı arifesi'}
COLUMNS = ['tarih', 'tarih_anahtari', 'yil', 'ceyrek', 'yil_ceyrek', 'ay', 'ay_adi', 'ay_kisa', 'yil_ay', 'ayin_gunu', 'yilin_gunu',
           'iso_yil', 'iso_hafta', 'haftanin_gunu', 'gun_adi', 'hafta_sonu', 'resmi_tatil', 'yarim_gun', 'tatil_adi', 'is_gunu',
           'aydaki_is_gunu_sirasi', 'ay_basi', 'ay_sonu', 'mali_yil', 'mali_ceyrek', 'mali_ay']


def parse_day(text, label):
    try:
        return datetime.strptime(text.strip(), '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f'{label} YYYY-AA-GG biçiminde olmalı: {text}') from None


def load_holidays(path):
    """CSV with columns tarih (YYYY-AA-GG or GG.AA.YYYY), ad and optional yarim_gun (evet/hayır)."""
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        raise ValueError(f'Tatil dosyası bulunamadı: {p}')
    columns, rows = tabular.read(p)
    if 'tarih' not in columns or 'ad' not in columns:
        raise ValueError('Tatil dosyasında "tarih" ve "ad" sütunları olmalı.')
    out = {}
    for n, r in enumerate(rows, start=2):
        parsed = tabular.parse_date(r['tarih'])
        if not parsed:
            raise ValueError(f'Tatil dosyası {n}. satır: tarih okunamadı ({r["tarih"]}).')
        half = r.get('yarim_gun', '').strip().lower() in ('evet', 'e', '1', 'true', 'yes')
        out[parsed[0].date()] = (r['ad'].strip(), half)
    return out


def build(start, end, holidays, fiscal_start=1, weekend=(5, 6)):
    if end < start:
        raise ValueError('Bitiş tarihi başlangıçtan önce olamaz.')
    if (end - start).days > 366 * 60:
        raise ValueError('En fazla 60 yıllık tablo üretilir.')
    rows, work_index = [], Counter()
    d = start
    while d <= end:
        name, half = '', False
        if (d.month, d.day) in SABIT:
            name = SABIT[(d.month, d.day)]
        elif (d.month, d.day) in YARIM:
            name, half = YARIM[(d.month, d.day)], True
        if d in holidays:
            extra, extra_half = holidays[d]
            name, half = (f'{name} / {extra}' if name else extra), (half or extra_half) and not (d.month, d.day) in SABIT
        full_holiday = bool(name) and not half
        weekend_day = d.weekday() in weekend
        work = not weekend_day and not full_holiday
        if work:
            work_index[(d.year, d.month)] += 1
        next_day = d + timedelta(days=1)
        fm = (d.month - fiscal_start) % 12 + 1
        fy = d.year + (1 if fiscal_start > 1 and d.month >= fiscal_start else 0)
        iso = d.isocalendar()
        q = (d.month - 1) // 3 + 1
        rows.append({'tarih': d.isoformat(), 'tarih_anahtari': d.strftime('%Y%m%d'), 'yil': d.year, 'ceyrek': q, 'yil_ceyrek': f'{d.year}-Ç{q}',
                     'ay': d.month, 'ay_adi': AYLAR[d.month - 1], 'ay_kisa': AYLAR[d.month - 1][:3], 'yil_ay': d.strftime('%Y-%m'),
                     'ayin_gunu': d.day, 'yilin_gunu': d.timetuple().tm_yday, 'iso_yil': iso[0], 'iso_hafta': iso[1],
                     'haftanin_gunu': d.weekday() + 1, 'gun_adi': GUNLER[d.weekday()], 'hafta_sonu': int(weekend_day),
                     'resmi_tatil': int(full_holiday), 'yarim_gun': int(half), 'tatil_adi': name, 'is_gunu': int(work),
                     'aydaki_is_gunu_sirasi': work_index[(d.year, d.month)] if work else '',
                     'ay_basi': int(d.day == 1), 'ay_sonu': int(next_day.month != d.month),
                     'mali_yil': fy, 'mali_ceyrek': (fm - 1) // 3 + 1, 'mali_ay': fm})
        d = next_day
    return rows


SQL_TYPES = {'tarih': 'DATE', 'tarih_anahtari': 'INT', 'yil_ceyrek': 'VARCHAR(8)', 'ay_adi': 'VARCHAR(10)', 'ay_kisa': 'VARCHAR(3)', 'yil_ay': 'CHAR(7)',
             'gun_adi': 'VARCHAR(10)', 'tatil_adi': 'VARCHAR(120)'}


def sql(rows, table):
    cols = ',\n'.join(f"    {c} {SQL_TYPES.get(c, 'SMALLINT')}{' NOT NULL PRIMARY KEY' if c == 'tarih_anahtari' else ''}" for c in COLUMNS)
    lines = [f'-- tarih-tablosu tarafından üretildi. {len(rows)} gün.', f'CREATE TABLE {table} (\n{cols}\n);', '']
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        values = ',\n'.join('(' + ', '.join(("NULL" if r[c] == '' else str(r[c])) if c not in SQL_TYPES or c == 'tarih_anahtari'
                                             else "'" + str(r[c]).replace("'", "''") + "'" for c in COLUMNS) + ')' for r in chunk)
        lines.append(f"INSERT INTO {table} ({', '.join(COLUMNS)}) VALUES\n{values};")
    return '\n'.join(lines)


def markdown(rows, start, end, fiscal_start, holiday_file):
    by_month = Counter()
    days = Counter()
    for r in rows:
        days[r['yil_ay']] += 1
        by_month[r['yil_ay']] += r['is_gunu']
    holidays = [r for r in rows if r['tatil_adi']]
    lines = [f'# Tarih tablosu — {start} → {end}', '', f'- Gün: **{len(rows)}** · İş günü: **{sum(r["is_gunu"] for r in rows)}** · '
             f'Tatil/yarım gün: **{len(holidays)}** · Mali yıl başlangıcı: **{AYLAR[fiscal_start - 1]}**',
             f"- Dinî bayramlar: {'`' + holiday_file + '` dosyasından' if holiday_file else '**eklenmedi** (--holidays ile ekleyin)'}", '',
             '## Aylık iş günü', '', md_table(['Ay', 'Gün', 'İş günü'], [[m, days[m], by_month[m]] for m in sorted(days)][:36]),
             '## Tatiller', '', md_table(['Tarih', 'Gün', 'Tatil', 'Yarım gün'], [[r['tarih'], r['gun_adi'], r['tatil_adi'], 'evet' if r['yarim_gun'] else ''] for r in holidays][:200]),
             '## Kullanım', '', '- **Power BI:** tarih-tablosu.csv’yi alın, `tarih` sütununu Tarih tipine çevirin, *Tarih tablosu olarak işaretle* deyin ve otomatik tarih/saati kapatın.',
             '- **Veri ambarı:** tarih-tablosu.sql’i lehçenize göre gözden geçirip çalıştırın; olgu tablolarını `tarih_anahtari` (YYYYAAGG) ile bağlayın.',
             '- Dinî bayram tarihleri her yıl Diyanet takvimine göre değişir; tatil dosyasını resmî kaynaktan doğrulayın.']
    return '\n'.join(lines)


def main():
    console()
    p = argparse.ArgumentParser(description='Türkçe tarih boyutu (tarih tablosu) üretir (internet bağlantısı yok).')
    p.add_argument('--start', default=f'{date.today().year - 1}-01-01', help='YYYY-AA-GG (varsayılan geçen yılın başı)')
    p.add_argument('--end', default=f'{date.today().year + 1}-12-31', help='YYYY-AA-GG (varsayılan gelecek yılın sonu)')
    p.add_argument('--holidays', default=str(here('ornek', 'tatiller.csv')), help='Ek tatil dosyası (tarih, ad, yarim_gun); "" ile kapatılır')
    p.add_argument('--fiscal-start', type=int, default=1, help='Mali yılın başladığı ay (1-12)')
    p.add_argument('--table', default='dim_tarih', help='SQL tablo adı')
    p.add_argument('--output', default='output')
    args = p.parse_args()
    try:
        if not 1 <= args.fiscal_start <= 12:
            raise ValueError('--fiscal-start 1 ile 12 arasında olmalı.')
        start, end = parse_day(args.start, '--start'), parse_day(args.end, '--end')
        holidays = load_holidays(args.holidays)
        rows = build(start, end, holidays, args.fiscal_start)
        out = output_dir(args.output)
        tabular.write_csv(out / 'tarih-tablosu.csv', COLUMNS, rows)
        write_text(out / 'tarih-tablosu.sql', sql(rows, args.table))
        write_text(out / 'tarih-ozeti.md', markdown(rows, start, end, args.fiscal_start, Path(args.holidays).name if args.holidays else ''))
    except (ValueError, OSError) as exc:
        p.exit(1, f'[X] {exc}\n')
    print(f"[OK] {len(rows)} gün, {sum(r['is_gunu'] for r in rows)} iş günü, {sum(bool(r['tatil_adi']) for r in rows)} tatil. Çıktı: {out / 'tarih-tablosu.csv'}")


if __name__ == '__main__':
    main()
