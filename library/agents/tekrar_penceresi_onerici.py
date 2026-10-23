"""Tekrar penceresi önerici (AI agent): akışta yeniden teslim edilen (producer retry, tüketici yeniden dengelemesi) olayları ayıklamak için
tekilleştirme anahtarını ve durum (state) saklama penceresini seçer. Aday anahtar sütunları ve her biri için kaç farklı olayın aynı
değeri paylaştığı (yanlış birleştirme), yinelenen teslimlerin gecikme dağılımı kodla çıkarılır; modele yalnızca bu özet gider, tutar ve
olay içerikleri gönderilmez. Seçim kodla teslim günlüğü üzerinde yeniden oynatılır: anahtar farklı olayları birleştirmemeli, pencere
yinelenen teslimlerin belirli payını yakalamalı ve tutulan anahtar sayısı (tepe olay/sn × pencere) durum bütçesini aşmamalı.
Çıktı: tekillestirme.sql (Flink SQL), tekrar_plani.json, tekrar-penceresi.md."""
import argparse
from collections import defaultdict
from datetime import datetime
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from analiz import quantile


def replay(rows, key, window, truth):
    """Keep a delivery unless the same key was kept within the window. Returns (missed duplicates, wrongly dropped events)."""
    last, kept_ids, missed, wrong = {}, set(), 0, 0
    for r in rows:
        k = tuple(r[c] for c in key)
        t = r['_t']
        if k in last and (t - last[k]).total_seconds() <= window:
            if r[truth] not in kept_ids:
                wrong += 1  # a different event was dropped as a duplicate
            continue
        last[k] = t
        if r[truth] in kept_ids:
            missed += 1
        kept_ids.add(r[truth])
    return missed, wrong


def validator(rows, cols, truth, dup_delays, rate, min_catch, max_state):
    def validate(obj):
        key = [str(c).strip() for c in obj.get('anahtar') or []]
        try:
            win = float(obj.get('pencere_sn'))
        except (TypeError, ValueError):
            raise ValueError('pencere_sn saniye cinsinden sayı olmalı') from None
        bad = [c for c in key if c not in cols]
        if bad or not key:
            raise ValueError(f'anahtar teslim günlüğündeki sütunlardan olmalı ({", ".join(cols)}); olmayan: {", ".join(bad) or "boş"}')
        problems = []
        missed, wrong = replay(rows, key, win, truth)
        caught = 1 - missed / len(dup_delays) if dup_delays else 1.0
        if wrong:
            problems.append(f'({", ".join(key)}) anahtarı {wrong} farklı olayı yinelenen sayıp atıyor (aynı değeri paylaşan ayrı olaylar var)')
        if caught < min_catch:
            problems.append(f'{win:g} sn pencere ile yakalanan yinelenen teslim payı %{100 * caught:.1f} (en az %{100 * min_catch:g}); {missed} yinelenen geçiyor')
        state = rate * win
        if state > max_state:
            problems.append(f'pencere boyunca ~{state:,.0f} anahtar tutulur (tepe {rate:.1f} olay/sn × {win:g} sn), durum bütçesi {max_state:,}')
        if problems:
            raise ValueError('; '.join(problems))
        return key, win, caught, state, str(obj.get('gerekce', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir akış işleme mühendisisin. Yeniden teslim edilen olayları ayıklamak için tekilleştirme anahtarını (farklı olayları asla '
          'birleştirmeyen sütunlar) ve durum saklama penceresini (saniye) seç: yinelenen teslimlerin neredeyse tamamını yakalasın, durumu gereksiz büyütmesin. '
          'Yanıt şeması: {"anahtar": ["..."], "pencere_sn": 60, "gerekce": "..."}')


def main():
    p = argparse.ArgumentParser(description='Teslim günlüğünden tekilleştirme anahtarı ve pencere öneren, seçimi günlükte yeniden oynatan agent.')
    p.add_argument('--input', default=str(here('ornek', 'odeme_olay_teslimleri.csv')), help='Teslim günlüğü (offset, aday anahtarlar, varis_zamani)')
    p.add_argument('--arrival', default='varis_zamani')
    p.add_argument('--truth', default='olay_id', help='Olayı gerçekten tanımlayan sütun (yeniden oynatmada doğruluk için)')
    p.add_argument('--candidates', default='olay_id,islem_no,tutar', help='Anahtar adayı sütunlar')
    p.add_argument('--min-catch', type=float, default=0.995)
    p.add_argument('--max-state', type=int, default=200, help='Pencere boyunca tutulabilecek en fazla anahtar')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    cands = [c.strip() for c in args.candidates.split(',') if c.strip() in columns]
    if args.truth not in columns or args.arrival not in columns:
        raise ValueError(f'{args.truth} ve {args.arrival} sütunları gerekli.')
    for r in rows:
        r['_t'] = datetime.fromisoformat(r[args.arrival].strip())
    rows.sort(key=lambda r: r['_t'])
    first, delays = {}, []
    for r in rows:
        e = r[args.truth]
        if e in first:
            delays.append((r['_t'] - first[e]).total_seconds())
        else:
            first[e] = r['_t']
    delays.sort()
    span = (rows[-1]['_t'] - rows[0]['_t']).total_seconds() or 1
    per_min = defaultdict(int)
    for r in rows:
        per_min[r['_t'].replace(second=0, microsecond=0)] += 1
    rate = max(per_min.values()) / 60
    shared = {}
    for c in cands:
        ev = defaultdict(set)
        for r in rows:
            ev[r[c]].add(r[args.truth])
        shared[c] = sum(len(v) for v in ev.values() if len(v) > 1)
    out = output_dir(args.output)
    user = (f'{len(rows)} teslim, {len(first)} farklı olay, {len(delays)} yinelenen teslim; ortalama {len(rows) / span:.2f}, tepe {rate:.2f} olay/sn. Durum bütçesi {args.max_state:,} anahtar.\n'
            'Aday anahtar sütunları (aynı değeri paylaşan farklı olay sayısı): ' + ', '.join(f'{c} {n}' for c, n in shared.items())
            + '\nYinelenen teslim gecikmesi (ilk teslimden sonra, sn) p50/p90/p99/p99,5/max: ' + '/'.join(f'{quantile(delays, q):.1f}' for q in (.5, .9, .99, .995)) + f'/{delays[-1]:.1f}'
            + f'\nYakalanması gereken yinelenen teslim payı en az %{100 * args.min_catch:g}.')
    result, meta = llm.ask(args, out, SYSTEM, user, validator(rows, columns, args.truth, delays, rate, args.min_catch, args.max_state))
    if result is None:
        return
    key, win, caught, state, why = result
    k = ', '.join(key)
    sql = [f'-- Flink SQL: ({k}) anahtarıyla tekilleştirme; durum TTL {win:g} sn. {why}', f"SET 'table.exec.state.ttl' = '{int(win * 1000)} ms';", '',
           'CREATE VIEW teslimler_tekil AS', 'SELECT * FROM (', '  SELECT *, ROW_NUMBER() OVER (', f'    PARTITION BY {k}', '    ORDER BY proctime ASC', '  ) AS rn',
           '  FROM teslimler', ') WHERE rn = 1;']
    write_text(out / 'tekillestirme.sql', '\n'.join(sql))
    write_json(out / 'tekrar_plani.json', {'anahtar': key, 'pencere_sn': win, 'yakalanan_oran': round(caught, 4), 'tahmini_durum_anahtari': round(state), 'gerekce': why})
    md = ['# Tekilleştirme penceresi', '', f'Anahtar ({k}), pencere {win:g} sn: {len(delays)} yinelenen teslimin %{100 * caught:.1f} kadarı yakalanıyor, farklı olay birleşmiyor; '
          f'tahmini durum ~{state:,.0f} anahtar (bütçe {args.max_state:,}).', '', why, '',
          md_table(['Aday sütun', 'Aynı değeri paylaşan farklı olay'], [[c, n] for c, n in shared.items()]), '',
          f'_Model: {meta["model"]} · Modele yalnızca gecikme yüzdelikleri ve anahtar özetleri gitti; seçim teslim günlüğünde kodla yeniden oynatıldı._']
    write_text(out / 'tekrar-penceresi.md', '\n'.join(md))
    print(f'[OK] anahtar {k}, pencere {win:g} sn. Çıktı: {out / "tekrar-penceresi.md"}')


if __name__ == '__main__':
    llm.run(main)
