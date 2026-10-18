"""Kafka konu tasarımcısı (AI agent): her konu (topic) için bölüm (partition) sayısı, çoğaltma faktörü, min.insync.replicas, saklama
süresi ve cleanup.policy önerir. Girdi konu başına ölçülmüş yükü verir: tepe mesaj/sn, ortalama mesaj boyutu, bir tüketici örneğinin
işleyebildiği mesaj/sn, anahtarlı mı ve kaç farklı anahtar var, durum (state) konusu mu, geri oynatma için kaç saat saklanmalı. En az
bölüm sayısı kodla hesaplanır (yazma: tepe MB/sn ÷ bölüm başına MB/sn; okuma: tepe mesaj/sn ÷ tüketici kapasitesi). Öneri kodla denetlenir:
bölüm sayısı en azın altında ya da 4 katının üstünde olamaz, anahtarlı konuda anahtar sayısını geçemez; çoğaltma faktörü broker sayısını
geçemez ve en az min(3, broker) olmalı; min.insync.replicas = RF − 1 (en az 1); saklama geri oynatma süresini karşılamalı; compact yalnızca
anahtarlı durum konusunda. Çıktı: konu_ayarlari.csv, kafka-konulari.sh (kafka-topics.sh komutları), kafka-konulari.md."""
import argparse, math
import llm
import tabular
from common import here, output_dir, write_text, md_table

POLICIES = ('delete', 'compact', 'compact,delete')


def minimum(t, mb_per_partition):
    write = t['tepe_mesaj_sn'] * t['ort_mesaj_kb'] / 1024 / mb_per_partition
    read = t['tepe_mesaj_sn'] / t['tuketici_mesaj_sn']
    return max(1, math.ceil(write), math.ceil(read)), round(write, 2), round(read, 2)


def validator(topics, brokers, mb):
    def validate(obj):
        problems, out = [], {}
        for e in obj.get('konular') or []:
            if not isinstance(e, dict):
                continue
            name = str(e.get('konu', '')).strip()
            if name not in topics or name in out:
                problems.append(f'konu “{name}” listede yok ya da iki kez yazıldı')
                continue
            t, why = topics[name], []
            try:
                parts, rf, isr = int(e.get('bolum')), int(e.get('cogaltma')), int(e.get('min_insync'))
                ret = int(e.get('saklama_saat')) if e.get('saklama_saat') not in (None, '', -1) else -1
            except (TypeError, ValueError):
                problems.append(f'{name}: bolum, cogaltma, min_insync ve saklama_saat sayı olmalı')
                continue
            policy = str(e.get('cleanup', '')).strip()
            need, _, _ = minimum(t, mb)
            if parts < need:
                why.append(f'bölüm {parts} < en az {need}')
            elif parts > need * 4 and parts > 6:
                why.append(f'bölüm {parts} gereğinden çok (en az {need}; 4 katını aşma)')
            if t['anahtarli'] and parts > t['anahtar_sayisi']:
                why.append(f'{t["anahtar_sayisi"]} farklı anahtar var; {parts} bölümün bir kısmı boş kalır')
            if not min(3, brokers) <= rf <= brokers:
                why.append(f'çoğaltma {rf}; {min(3, brokers)}–{brokers} arası olmalı ({brokers} broker)')
            if isr != max(1, rf - 1):
                why.append(f'min.insync.replicas {isr}; RF {rf} için {max(1, rf - 1)} olmalı')
            if policy not in POLICIES:
                why.append(f'cleanup {"/".join(POLICIES)} olmalı')
            elif 'compact' in policy and not (t['anahtarli'] and t['durum']):
                why.append('compact yalnızca anahtarlı durum konusunda')
            elif policy == 'compact' and t['durum'] is False:
                why.append('compact yalnızca durum konusunda')
            if 'delete' in policy and ret != -1 and ret < t['geri_oynatma_saat']:
                why.append(f'saklama {ret} sa < geri oynatma ihtiyacı {t["geri_oynatma_saat"]} sa')
            if policy == 'delete' and ret == -1:
                why.append('delete politikasında saklama sınırsız olamaz')
            if why:
                problems.append(f'{name}: ' + '; '.join(why))
            out[name] = {'bolum': parts, 'cogaltma': rf, 'min_insync': isr, 'saklama_saat': ret, 'cleanup': policy, 'gerekce': str(e.get('gerekce', '')).strip()}
        missing = [n for n in topics if n not in out]
        if missing:
            problems.append('yazılmayan konu: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen Kafka platform mühendisisin. Her konu için bölüm sayısı, çoğaltma faktörü, min.insync.replicas, saklama süresi (saat; '
          'sınırsız için -1) ve cleanup.policy (delete, compact, compact,delete) öner. Kodla hesaplanan en az bölüm sayısının altına inme, gereğinden çok '
          'bölüm açma (en fazla 4 katı); anahtarlı konuda bölüm sayısı anahtar sayısını geçmesin. min.insync.replicas = RF − 1. compact yalnızca anahtarlı '
          'durum konularında. Yanıt şeması: {"konular": [{"konu": "...", "bolum": 12, "cogaltma": 3, "min_insync": 2, "saklama_saat": 168, '
          '"cleanup": "delete", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Ölçülen yükten Kafka konu ayarlarını öneren ve kurallarla denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'kafka_konu_yukleri.csv')),
                   help='konu, tepe_mesaj_sn, ort_mesaj_kb, tuketici_mesaj_sn, anahtarli, anahtar_sayisi, durum_konusu, geri_oynatma_saat')
    p.add_argument('--brokers', type=int, default=3)
    p.add_argument('--partition-mb', type=float, default=10.0, help='Bir bölümün güvenli yazma hızı (MB/sn)')
    p.add_argument('--bootstrap', default='localhost:9092')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    cols = ['konu', 'tepe_mesaj_sn', 'ort_mesaj_kb', 'tuketici_mesaj_sn', 'anahtarli', 'anahtar_sayisi', 'durum_konusu', 'geri_oynatma_saat']
    missing = [c for c in cols if c not in columns]
    if missing:
        raise ValueError(f'Eksik sütun: {", ".join(missing)}')
    yes = lambda v: v.strip().lower() in ('evet', 'e', '1', 'true', 'yes')
    num = lambda v: float(v.replace(',', '.')) if v.strip() else 0.0
    topics = {r['konu'].strip(): {'tepe_mesaj_sn': num(r['tepe_mesaj_sn']), 'ort_mesaj_kb': num(r['ort_mesaj_kb']), 'tuketici_mesaj_sn': num(r['tuketici_mesaj_sn']) or 1,
                                  'anahtarli': yes(r['anahtarli']), 'anahtar_sayisi': int(num(r['anahtar_sayisi'])) or 10 ** 9, 'durum': yes(r['durum_konusu']),
                                  'geri_oynatma_saat': int(num(r['geri_oynatma_saat']))} for r in rows}
    out = output_dir(args.output)
    lines = []
    for n, t in topics.items():
        need, w, rd = minimum(t, args.partition_mb)
        lines.append(f'- {n}: tepe {t["tepe_mesaj_sn"]:g} mesaj/sn × {t["ort_mesaj_kb"]:g} KB; tüketici örneği {t["tuketici_mesaj_sn"]:g} mesaj/sn; '
                     f'{"anahtarlı (" + str(t["anahtar_sayisi"]) + " anahtar)" if t["anahtarli"] else "anahtarsız"}; {"durum konusu" if t["durum"] else "olay konusu"}; '
                     f'geri oynatma {t["geri_oynatma_saat"]} sa · en az bölüm {need} (yazma {w}, okuma {rd})')
    user = f'{args.brokers} broker; bölüm başına güvenli yazma {args.partition_mb:g} MB/sn.\nKonular:\n' + '\n'.join(lines)
    result, meta = llm.ask(args, out, SYSTEM, user, validator(topics, args.brokers, args.partition_mb))
    if result is None:
        return
    table = [{'konu': n, **result[n], 'en_az_bolum': minimum(topics[n], args.partition_mb)[0]} for n in topics]
    tabular.write_csv(out / 'konu_ayarlari.csv', list(table[0]), table)
    sh = ['#!/bin/sh', f'# {len(table)} konu · kafka-topics.sh komutları; çalıştırmadan önce BOOTSTRAP değerini kontrol edin', f'BOOTSTRAP={args.bootstrap}']
    for t in table:
        ms = -1 if t['saklama_saat'] == -1 else t['saklama_saat'] * 3600000
        sh.append(f'kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --create --if-not-exists --topic {t["konu"]} --partitions {t["bolum"]} '
                  f'--replication-factor {t["cogaltma"]} --config min.insync.replicas={t["min_insync"]} --config cleanup.policy={t["cleanup"]} --config retention.ms={ms}')
    write_text(out / 'kafka-konulari.sh', '\n'.join(sh) + '\n')
    md = ['# Kafka konu ayarları', '', f'{len(table)} konu, {args.brokers} broker. Bölüm sayıları ölçülen yükten hesaplanan en azın üstünde, saklama süreleri geri oynatma ihtiyacını karşılıyor.', '',
          md_table(['Konu', 'Bölüm (en az)', 'RF', 'min.insync', 'Saklama', 'Cleanup', 'Gerekçe'],
                   [[t['konu'], f'{t["bolum"]} ({t["en_az_bolum"]})', t['cogaltma'], t['min_insync'], 'sınırsız' if t['saklama_saat'] == -1 else f'{t["saklama_saat"]} sa', t['cleanup'], t['gerekce']] for t in table]), '',
          f'_Model: {meta["model"]} · En az bölüm sayısı ve tüm kurallar kodla denetlendi. Bölüm sayısı sonradan artırılabilir ama azaltılamaz; anahtarlı konuda artırmak '
          'anahtar–bölüm eşlemesini değiştirir. Komutlar bir Kafka kümesinde çalıştırılarak test edilmedi._']
    write_text(out / 'kafka-konulari.md', '\n'.join(md))
    print(f'[OK] {len(table)} konu ayarlandı. Çıktı: {out / "kafka-konulari.md"}')


if __name__ == '__main__':
    llm.run(main)
