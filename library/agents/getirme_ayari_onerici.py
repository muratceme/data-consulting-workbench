"""Getirme ayarı önerici (AI agent): RAG sisteminde modele verilecek parça sayısını (top_k) ve en düşük benzerlik skorunu (min_score)
önerir. Değerlendirme setindeki her soru için sıralı getirilen parçalar ve skorlar kodla okunur; her (top_k, min_score) ayarında doğru
parçanın bağlama girme oranı rag-geri-getirme-degerlendirme bloğuyla aynı hesapla, ortalama bağlam boyutu da token olarak kodla
hesaplanır. Modele yalnızca bu tarama tablosu gider; sorular ve parça içerikleri gönderilmez. Seçim kodla denetlenir: bulma oranı hedefin
altında kalmamalı, ortalama bağlam token bütçesini aşmamalı ve top_k gereğinden büyük olmamalı (bir küçüğü de hedefi sağlıyorsa reddedilir).
Çıktı: getirme_ayari.json, ayar_taramasi.csv, bulunamayan_sorular.csv, getirme-ayari.md."""
import argparse
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
from rag import retrieval

SCORES = (0.0, 0.5, 0.6, 0.65, 0.7, 0.75)


def evaluate(items, k, ms, tokens):
    rows = [{'soru_id': q, 'beklenen': exp, 'getirilen': '|'.join([c for c, s in zip(got, sc) if s >= ms][:k])} for q, exp, got, sc in items]
    res = retrieval(['soru_id', 'beklenen', 'getirilen'], rows, SimpleNamespace(expected='beklenen', retrieved='getirilen', sep='|', k=str(k)))
    ctx = sum(len([s for s in sc if s >= ms][:k]) for _, _, _, sc in items) / len(items) * tokens
    return {'top_k': k, 'min_score': ms, 'bulma_orani': res.summary[f'hit@{k}'], 'mrr': res.summary['mrr'], 'ort_baglam_token': round(ctx)}, res.rows


def validator(items, args):
    def validate(obj):
        try:
            k, ms = int(obj.get('top_k')), float(obj.get('min_score'))
        except (TypeError, ValueError):
            raise ValueError('top_k tam sayı, min_score 0–1 arası sayı olmalı') from None
        if not 1 <= k <= args.max_k or not 0 <= ms < 1:
            raise ValueError(f'top_k 1–{args.max_k}, min_score 0–1 arasında olmalı')
        r, rows = evaluate(items, k, ms, args.chunk_tokens)
        problems = []
        if r['bulma_orani'] < args.min_hit:
            problems.append(f'top_k {k}, min_score {ms:g}: doğru parçanın bağlamda olduğu soru payı %{100 * r["bulma_orani"]:.1f} (en az %{100 * args.min_hit:g})')
        if r['ort_baglam_token'] > args.token_budget:
            problems.append(f'ortalama bağlam {r["ort_baglam_token"]} token, bütçe {args.token_budget}')
        if k > 1 and not problems:
            smaller, _ = evaluate(items, k - 1, ms, args.chunk_tokens)
            if smaller['bulma_orani'] >= args.min_hit:
                problems.append(f'top_k {k - 1} ile de bulma oranı %{100 * smaller["bulma_orani"]:.1f}; top_k gereğinden büyük')
        if problems:
            raise ValueError('; '.join(problems))
        return r, rows, str(obj.get('gerekce', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir RAG mühendisisin. Tarama tablosuna bakarak modele verilecek parça sayısını (top_k) ve en düşük benzerlik skorunu (min_score) seç: '
          'doğru parça hedeflenen oranda bağlama girsin, bağlam token bütçesini aşmasın ve gereğinden büyük olmasın. Yanıt şeması: {"top_k": 4, "min_score": 0.6, "gerekce": "..."}')


def main():
    p = argparse.ArgumentParser(description='RAG değerlendirme setinden top_k ve min_score öneren, seçimi bulma oranı ve token bütçesiyle denetleyen agent.')
    p.add_argument('--input', default=str(here('ornek', 'getirme_skorlari.csv')), help='soru_id, beklenen, getirilen (| ile sıralı), skorlar (| ile)')
    p.add_argument('--chunk-tokens', type=int, default=400, help='Ortalama parça boyutu (token)')
    p.add_argument('--token-budget', type=int, default=2400, help='Ortalama bağlam token bütçesi')
    p.add_argument('--min-hit', type=float, default=0.9, help='Doğru parçanın bağlama girme oranı en az')
    p.add_argument('--max-k', type=int, default=10)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in ('soru_id', 'beklenen', 'getirilen', 'skorlar'):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok.')
    items = []
    for r in rows:
        got = [x.strip() for x in r['getirilen'].split('|') if x.strip()]
        sc = [float(x.replace(',', '.')) for x in r['skorlar'].split('|') if x.strip()]
        if len(got) != len(sc):
            raise ValueError(f'{r["soru_id"]}: getirilen ve skorlar aynı uzunlukta olmalı')
        items.append((r['soru_id'].strip(), r['beklenen'].strip(), got, sc))
    grid = [evaluate(items, k, ms, args.chunk_tokens)[0] for k in range(1, args.max_k + 1) for ms in SCORES]
    out = output_dir(args.output)
    user = (f'{len(items)} soruluk değerlendirme seti, parça başına ~{args.chunk_tokens} token. Hedef: bulma oranı ≥ %{100 * args.min_hit:g}, ortalama bağlam ≤ {args.token_budget} token.\n'
            'Tarama (top_k · min_score · bulma oranı · MRR · ortalama bağlam token):\n' + '\n'.join(' · '.join(f'{v:g}' for v in g.values()) for g in grid))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(items, args))
    if result is None:
        return
    r, qrows, why = result
    tabular.write_csv(out / 'ayar_taramasi.csv', list(grid[0]), grid)
    miss = [{'soru_id': o['soru_id'], 'beklenen': o['beklenen'], 'ham_sira': next((i for i, (q, e, g, _) in enumerate(items) if q == o['soru_id']), None)} for o in qrows if o['bulundu'] == 'hayır']
    for m_ in miss:
        q, e, g, _ = items[m_['ham_sira']]
        m_['ham_sira'] = g.index(e) + 1 if e in g else ''
    tabular.write_csv(out / 'bulunamayan_sorular.csv', ['soru_id', 'beklenen', 'ham_sira'], miss)
    write_json(out / 'getirme_ayari.json', {'top_k': r['top_k'], 'min_score': r['min_score'], 'bulma_orani': r['bulma_orani'], 'mrr': r['mrr'],
                                            'ort_baglam_token': r['ort_baglam_token'], 'gerekce': why})
    md = ['# Getirme ayarı', '', f'top_k **{r["top_k"]}**, min_score **{r["min_score"]:g}**: doğru parçanın bağlamda olduğu soru payı %{100 * r["bulma_orani"]:.1f}, MRR {r["mrr"]:.3f}, '
          f'ortalama bağlam {r["ort_baglam_token"]} token (bütçe {args.token_budget}).', '', why, '',
          md_table(['Soru', 'Beklenen parça', 'Ham sıradaki yeri'], [[m_['soru_id'], m_['beklenen'], m_['ham_sira'] or 'getirilmedi'] for m_ in miss]) if miss else 'Bütün soruların doğru parçası bağlamda.', '',
          f'_Model: {meta["model"]} · Modele yalnızca ayar taraması gitti; bulma oranı rag-geri-getirme-degerlendirme bloğuyla, bağlam boyutu ve top_k’nın en küçük olması kodla denetlendi._']
    write_text(out / 'getirme-ayari.md', '\n'.join(md))
    print(f'[OK] top_k {r["top_k"]}, min_score {r["min_score"]:g}. Çıktı: {out / "getirme-ayari.md"}')


if __name__ == '__main__':
    llm.run(main)
