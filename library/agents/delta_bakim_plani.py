"""Tablo bakım planı (AI agent): Delta Lake tabloları için OPTIMIZE, ZORDER ve VACUUM planı yazar. Girdi tablo başına dosya sayısı, toplam
boyut, silinmiş (eski sürüm) dosya boyutu, kolonlar ve sorgu geçmişinde en çok filtrelenen kolonlardır. Model tablo başına işlem seçer;
kodla denetlenir: OPTIMIZE yalnızca ortalama dosya boyutu küçük (< 128 MB) olan tabloda ve ortalaması 32 MB altında olan her tabloda
zorunlu; ZORDER kolonları tablonun kolonları ve sorgularda filtrelenen kolonlar arasından, en fazla 3 tane ve bölüm (partition)
kolonu olmamalı; VACUUM saklama süresi en az 168 saat (Delta’nın varsayılan güvenlik sınırı ve zaman yolculuğu) ve yalnızca silinmiş
dosyası olan tabloda. Çıktı: bakim.sql (Databricks SQL), bakim_plani.csv, tablo-bakimi.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_text, md_table


def validator(tables):
    def validate(obj):
        problems, out = [], {}
        for e in obj.get('tablolar') or []:
            if not isinstance(e, dict):
                continue
            n = str(e.get('tablo', '')).strip()
            if n not in tables or n in out:
                problems.append(f'tablo “{n}” yok ya da iki kez yazıldı')
                continue
            t, why = tables[n], []
            opt = bool(e.get('optimize'))
            z = [str(c).strip() for c in e.get('zorder') or []]
            vac = e.get('vacuum_saat')
            avg = t['ort_mb']
            if opt and avg >= 128:
                why.append(f'ortalama dosya {avg:.0f} MB; OPTIMIZE gereksiz')
            if not opt and avg < 32:
                why.append(f'ortalama dosya {avg:.1f} MB; OPTIMIZE gerekli')
            if z and not opt:
                why.append('ZORDER yalnızca OPTIMIZE ile çalışır')
            bad = [c for c in z if c not in t['kolonlar']]
            if bad:
                why.append(f'tabloda olmayan kolon: {", ".join(bad)}')
            notfilt = [c for c in z if c in t['kolonlar'] and c not in t['filtre']]
            if notfilt:
                why.append(f'sorgularda filtrelenmeyen kolon: {", ".join(notfilt)}')
            part = [c for c in z if c in t['bolum']]
            if part:
                why.append(f'bölüm kolonu ZORDER’a girmez: {", ".join(part)}')
            if len(z) > 3:
                why.append('en fazla 3 ZORDER kolonu')
            if vac not in (None, ''):
                try:
                    vac = int(vac)
                except (TypeError, ValueError):
                    vac = -1
                if vac < 168:
                    why.append('VACUUM saklaması en az 168 saat olmalı')
                elif t['silinmis_gb'] <= 0:
                    why.append('silinmiş dosya yok; VACUUM gereksiz')
            else:
                vac = None
            if why:
                problems.append(f'{n}: ' + '; '.join(why))
            out[n] = {'optimize': opt, 'zorder': z, 'vacuum_saat': vac, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [n for n in tables if n not in out]
        if missing:
            problems.append('plansız tablo: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir Databricks/Delta Lake platform mühendisisin. Her tablo için bakım planı yaz: optimize (küçük dosyaları birleştir; ortalama '
          'dosya < 32 MB ise zorunlu, ≥ 128 MB ise gereksiz), zorder (en çok filtrelenen, bölüm olmayan en fazla 3 kolon), vacuum_saat (silinmiş dosya varsa; '
          'en az 168). Yanıt şeması: {"tablolar": [{"tablo": "...", "optimize": true, "zorder": ["musteri_id"], "vacuum_saat": 168, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Delta Lake tablo istatistiklerinden OPTIMIZE/ZORDER/VACUUM planı yazan agent.')
    p.add_argument('--input', default=str(here('ornek', 'delta_tablo_istatistikleri.csv')),
                   help='tablo, dosya_sayisi, toplam_gb, silinmis_gb, bolum_kolonlari, kolonlar, filtre_kolonlari (listeler | ile)')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    need = ['tablo', 'dosya_sayisi', 'toplam_gb', 'silinmis_gb', 'bolum_kolonlari', 'kolonlar', 'filtre_kolonlari']
    missing = [c for c in need if c not in columns]
    if missing:
        raise ValueError(f'Eksik sütun: {", ".join(missing)}')
    lst = lambda v: [x.strip() for x in v.split('|') if x.strip()]
    num = lambda v: float(v.replace(',', '.')) if v.strip() else 0.0
    tables = {}
    for r in rows:
        files, gb = num(r['dosya_sayisi']), num(r['toplam_gb'])
        tables[r['tablo'].strip()] = {'dosya': int(files), 'gb': gb, 'ort_mb': gb * 1024 / files if files else 0.0, 'silinmis_gb': num(r['silinmis_gb']),
                                      'bolum': lst(r['bolum_kolonlari']), 'kolonlar': lst(r['kolonlar']), 'filtre': lst(r['filtre_kolonlari'])}
    out = output_dir(args.output)
    user = 'Tablolar:\n' + '\n'.join(
        f'- {n}: {t["dosya"]} dosya, {t["gb"]:g} GB (ortalama {t["ort_mb"]:.1f} MB), silinmiş {t["silinmis_gb"]:g} GB; bölüm: {", ".join(t["bolum"]) or "yok"}; '
        f'kolonlar: {", ".join(t["kolonlar"])}; sorgularda en çok filtrelenen: {", ".join(t["filtre"]) or "—"}' for n, t in tables.items())
    result, meta = llm.ask(args, out, SYSTEM, user, validator(tables))
    if result is None:
        return
    sql = ['-- Databricks SQL · tablo bakımı; yoğun saatler dışında çalıştırın']
    for n, a in result.items():
        if a['optimize']:
            sql.append(f'OPTIMIZE {n}' + (f' ZORDER BY ({", ".join(a["zorder"])})' if a['zorder'] else '') + ';')
        if a['vacuum_saat']:
            sql.append(f'VACUUM {n} RETAIN {a["vacuum_saat"]} HOURS;')
    write_text(out / 'bakim.sql', '\n'.join(sql) + '\n')
    table = [{'tablo': n, 'dosya': tables[n]['dosya'], 'ort_mb': round(tables[n]['ort_mb'], 1), 'optimize': 'evet' if a['optimize'] else 'hayır', 'zorder': ', '.join(a['zorder']),
              'vacuum_saat': a['vacuum_saat'] or '', 'gerekce': a['gerekce']} for n, a in result.items()]
    tabular.write_csv(out / 'bakim_plani.csv', list(table[0]), table)
    md = ['# Delta tablo bakımı', '', f'{len(table)} tablo; {sum(t["optimize"] == "evet" for t in table)} OPTIMIZE, {sum(1 for t in table if t["vacuum_saat"])} VACUUM.', '',
          md_table(['Tablo', 'Dosya', 'Ort. MB', 'OPTIMIZE', 'ZORDER', 'VACUUM (sa)', 'Gerekçe'], [list(t.values()) for t in table]), '',
          f'_Model: {meta["model"]} · Dosya boyutu eşikleri, ZORDER kolonları ve VACUUM saklama sınırı kodla denetlendi. VACUUM’dan sonra saklama süresinden eski sürümlere '
          'zaman yolculuğu yapılamaz. Komutlar Databricks’te çalıştırılarak test edilmedi._']
    write_text(out / 'tablo-bakimi.md', '\n'.join(md))
    print(f'[OK] {len(table)} tablo planlandı. Çıktı: {out / "tablo-bakimi.md"}')


if __name__ == '__main__':
    llm.run(main)
