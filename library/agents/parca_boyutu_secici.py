"""Parça boyutu seçici (AI agent): RAG için doküman parçalama (chunk) boyutunu ve örtüşmesini seçer. Bir soru–cevap seti (soru, belge, cevabın
belgedeki alıntısı) verilir; kod, olası boyut/örtüşme ızgarasında her ayar için “cevap alıntısının tamamı tek bir parçanın içinde mi” oranını
(kapsama), parça sayısını ve örtüşmeden doğan fazla metni hesaplar. Modele bu tablo ve belge boyutları gider (belgelerin metni gönderilmez).
Seçim kodla yeniden hesaplanıp denetlenir: kapsama en az --min-coverage olmalı, parça boyu --max-chars’ı (embedding/bağlam bütçesi) aşmamalı,
örtüşme parçanın yarısından az olmalı, toplam parça metni belgelerin 1,5 katını geçmemeli. Çıktı: parcalama.json, kapsama_tablosu.csv, parca-boyutu.md."""
import argparse, json, re
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_text, md_table

SIZES = (100, 150, 200, 300, 400, 600, 800)
OVERLAPS = (0.0, 0.1, 0.2, 0.3)


def norm(t):
    return re.sub(r'\s+', ' ', t).strip()


def chunk(text, size, overlap):
    words, out, cur = text.split(' '), [], ''
    i = 0
    while i < len(words):
        cur, j = '', i
        while j < len(words) and len(cur) + len(words[j]) + 1 <= size or (j == i and j < len(words)):
            cur = (cur + ' ' + words[j]).strip()
            j += 1
        out.append(cur)
        if j >= len(words):
            break
        back, k = 0, j
        while k > i + 1 and back + len(words[k - 1]) + 1 <= overlap:  # step back by ~overlap characters
            k -= 1
            back += len(words[k]) + 1
        i = k
    return out


def evaluate(docs, qa, size, overlap_chars):
    chunks = {d: chunk(t, size, overlap_chars) for d, t in docs.items()}
    hit = sum(1 for q in qa if any(norm(q['alinti']) in c for c in chunks.get(q['belge'], [])))
    total = sum(len(c) for cs in chunks.values() for c in cs)
    return hit / len(qa), sum(len(c) for c in chunks.values()), total / sum(len(t) for t in docs.values())


def validator(docs, qa, min_cov, max_chars):
    def validate(obj):
        try:
            size, overlap = int(obj.get('parca_boyu')), int(obj.get('ortusme'))
        except (TypeError, ValueError):
            raise ValueError('parca_boyu ve ortusme (karakter) sayı olmalı') from None
        problems = []
        if not 50 <= size <= max_chars:
            problems.append(f'parça boyu 50–{max_chars} karakter olmalı')
        if not 0 <= overlap < size / 2:
            problems.append('örtüşme parçanın yarısından az olmalı')
        if problems:
            raise ValueError('; '.join(problems))
        cov, n, ratio = evaluate(docs, qa, size, overlap)
        if cov < min_cov:
            problems.append(f'{size}/{overlap} ayarında cevapların %{cov * 100:.0f}’i tek parçaya sığıyor (gereken %{min_cov * 100:.0f})')
        if ratio > 1.5:
            problems.append(f'örtüşme toplam metni {ratio:.2f} katına çıkarıyor (en fazla 1,5)')
        if problems:
            raise ValueError('; '.join(problems))
        return size, overlap, cov, n, ratio, str(obj.get('gerekce', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir RAG mühendisisin. Kodla hesaplanmış kapsama tablosuna bakarak parça boyu ve örtüşmesini (karakter) seç: cevap alıntılarının tek parçaya '
          'sığma oranı hedefi karşılamalı; daha küçük parça daha isabetli arama ve daha az bağlam maliyeti demektir, gereğinden büyük seçme. '
          'Yanıt şeması: {"parca_boyu": 400, "ortusme": 40, "gerekce": "..."}')


def main():
    p = argparse.ArgumentParser(description='Soru–cevap setiyle parça boyu/örtüşme kapsamasını ölçüp RAG parçalama ayarı seçen agent (belge metni gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'dokumanlar')), help='.md/.txt belgeler klasörü')
    p.add_argument('--qa', default=str(here('ornek', 'soru_cevap_seti.csv')), help='soru, belge, alinti')
    p.add_argument('--min-coverage', type=float, default=0.95)
    p.add_argument('--max-chars', type=int, default=800)
    llm.add_arguments(p)
    args = p.parse_args()
    docs = {f.name: norm(f.read_text(encoding='utf-8', errors='replace')) for f in sorted(Path(args.input).glob('*')) if f.suffix.lower() in ('.md', '.txt')}
    _, qa = tabular.read(args.qa)
    missing = [q['belge'] for q in qa if q['belge'] not in docs]
    if missing:
        raise ValueError(f'Soru setinde olmayan belge: {", ".join(sorted(set(missing)))}')
    absent = [q['soru'] for q in qa if norm(q['alinti']) not in docs[q['belge']]]
    if absent:
        raise ValueError(f'Alıntısı belgede geçmeyen soru: {absent[0]}')
    grid = []
    for size in SIZES:
        for ov in OVERLAPS:
            cov, n, ratio = evaluate(docs, qa, size, int(size * ov))
            grid.append({'parca_boyu': size, 'ortusme': int(size * ov), 'kapsama_%': round(cov * 100, 1), 'parca_sayisi': n, 'metin_carpani': round(ratio, 2)})
    out = output_dir(args.output)
    user = (f'{len(docs)} belge ({sum(len(t) for t in docs.values())} karakter, en uzun {max(len(t) for t in docs.values())}); {len(qa)} soru; cevap alıntısı uzunlukları: '
            + ', '.join(str(len(norm(q['alinti']))) for q in qa) + f'.\nHedef kapsama %{args.min_coverage * 100:.0f}, en fazla {args.max_chars} karakter.\n'
            'Izgara (boy · örtüşme · kapsama % · parça sayısı · metin çarpanı):\n' + '\n'.join(f'{g["parca_boyu"]} · {g["ortusme"]} · {g["kapsama_%"]} · {g["parca_sayisi"]} · {g["metin_carpani"]}' for g in grid))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(docs, qa, args.min_coverage, args.max_chars))
    if result is None:
        return
    size, overlap, cov, n, ratio, why = result
    write_text(out / 'parcalama.json', json.dumps({'parca_boyu_karakter': size, 'ortusme_karakter': overlap, 'kapsama': round(cov, 3), 'parca_sayisi': n}, ensure_ascii=False, indent=1))
    tabular.write_csv(out / 'kapsama_tablosu.csv', list(grid[0]), grid)
    md = ['# Parça boyutu', '', f'Seçim: **{size} karakter, {overlap} karakter örtüşme** — cevapların %{cov * 100:.0f}’i tek parçaya sığıyor; {n} parça, metin çarpanı {ratio:.2f}.', '',
          why, '', md_table(['Boy', 'Örtüşme', 'Kapsama %', 'Parça', 'Metin çarpanı'], [[g['parca_boyu'], g['ortusme'], g['kapsama_%'], g['parca_sayisi'], g['metin_carpani']] for g in grid if g['ortusme'] in (0, int(g['parca_boyu'] * 0.1))]), '',
          f'_Model: {meta["model"]} · Kapsama ızgarası ve seçilen ayar kodla hesaplandı. Kapsama, cevabın tek parçada bulunabilirliğidir; arama isabeti embedding modeline de bağlıdır._']
    write_text(out / 'parca-boyutu.md', '\n'.join(md))
    print(f'[OK] {size}/{overlap}: kapsama %{cov * 100:.0f}. Çıktı: {out / "parca-boyutu.md"}')


if __name__ == '__main__':
    llm.run(main)
