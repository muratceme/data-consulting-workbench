"""Veri kayması yorumlayıcı (AI agent): eğitim (referans) verisi ile canlı veri arasındaki dağılım değişimini her özellik için PSI ile yerelde
ölçer (veri-kaymasi-psi bloğuyla aynı hesap) ve modele yalnızca PSI tablosunu ve dilim paylarını verir; satırlar gönderilmez. Model her
özellik için durum, olası neden ve aksiyon yazar, “yeniden eğitim gerekli mi” kararını verir. Kodla denetlenir: durum PSI bandına uymalı
(< 0,1 kararlı, 0,1–0,25 izlenmeli, > 0,25 belirgin kayma), belirgin kayma varsa yeniden eğitim kararı “evet” olmalı ve her belirgin
kaymalı özelliğe aksiyon yazılmalı, metindeki sayılar tablodan gelmeli. Çıktı: kayma.csv, kayma-raporu.md."""
import argparse, re
from types import SimpleNamespace
import llm
import tabular
from common import here, output_dir, write_text, md_table
from ml import drift
from donem_yorumu import unknown_numbers


def band(v):
    return 'belirgin kayma' if v > 0.25 else 'izlenmeli' if v > 0.1 else 'kararlı'


def validator(table, detail, names):
    psi = {o['ozellik']: o['psi'] for o in table}
    nums = {abs(float(o['psi'])) for o in table} | {abs(float(d[2])) for d in detail} | {abs(float(d[3])) for d in detail} | {0.1, 0.25}
    nums |= {float(x.replace(',', '.')) for d in detail for x in re.findall(r'\d+(?:[.,]\d+)?', str(d[1]))}  # bucket edges such as ≤34

    def validate(obj):
        problems, out = [], {}
        for e in obj.get('ozellikler') or []:
            if not isinstance(e, dict):
                continue
            f = str(e.get('ozellik', '')).strip()
            if f not in psi or f in out:
                problems.append(f'özellik “{f}” yok ya da iki kez yazıldı')
                continue
            said = str(e.get('durum', '')).strip().lower().replace('kararli', 'kararlı')
            if said != band(psi[f]):
                problems.append(f'{f}: PSI {psi[f]} → durum “{band(psi[f])}” olmalı (“{said}” denmiş)')
            if band(psi[f]) == 'belirgin kayma' and not str(e.get('aksiyon', '')).strip():
                problems.append(f'{f}: belirgin kaymada aksiyon yazılmalı')
            out[f] = {'durum': said, 'neden': str(e.get('olasi_neden', '')).strip(), 'aksiyon': str(e.get('aksiyon', '')).strip()}
        missing = [f for f in psi if f not in out]
        if missing:
            problems.append('yorumlanmayan özellik: ' + ', '.join(missing))
        need = any(v > 0.25 for v in psi.values())
        if bool(obj.get('yeniden_egit')) != need:
            problems.append(f'yeniden_egit {"true" if need else "false"} olmalı ({"belirgin kayma var" if need else "belirgin kayma yok"})')
        text = ' '.join([str(obj.get('ozet', ''))] + [v['neden'] + ' ' + v['aksiyon'] for v in out.values()])
        bad = unknown_numbers(text, nums, names)
        if bad:
            problems.append('tabloda olmayan sayılar: ' + ', '.join(bad[:8]))
        if problems:
            raise ValueError('; '.join(problems))
        return out, need, str(obj.get('ozet', '')).strip()
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir MLOps mühendisisin. Kodla hesaplanmış PSI tablosunu yorumla: her özellik için durum (kararlı < 0,1; izlenmeli 0,1–0,25; '
          'belirgin kayma > 0,25), dilim paylarına bakarak olası neden ve aksiyon. Belirgin kayma varsa yeniden_egit true. Yalnızca tablodaki sayıları kullan. '
          'Yanıt şeması: {"ozellikler": [{"ozellik": "...", "durum": "izlenmeli", "olasi_neden": "...", "aksiyon": "..."}], "yeniden_egit": true, "ozet": "..."}')


def main():
    p = argparse.ArgumentParser(description='PSI ile ölçülen veri kaymasını kodla denetlenen biçimde yorumlatan agent (satırlar gönderilmez).')
    p.add_argument('--input', default=str(here('ornek', 'egitim_verisi.csv')), help='Referans (eğitim) verisi')
    p.add_argument('--current', default=str(here('ornek', 'canli_veri.csv')), help='Canlı veri')
    p.add_argument('--exclude', default='musteri_id,churn,iptal_tarihi')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    res = drift(columns, rows, SimpleNamespace(current=args.current, columns=None, exclude=args.exclude))
    table, detail = res.rows, res.tables[0][2]
    names = [o['ozellik'] for o in table]
    out = output_dir(args.output)
    user = ('PSI tablosu (özellik · tür · PSI):\n' + '\n'.join(f'{o["ozellik"]} · {o["tur"]} · {o["psi"]}' for o in table)
            + '\n\nDilim payları (özellik · dilim · referans % · canlı %):\n' + '\n'.join(' · '.join(str(x) for x in d) for d in detail))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(table, detail, names))
    if result is None:
        return
    notes, retrain, summary = result
    rows_out = [{'ozellik': o['ozellik'], 'tur': o['tur'], 'psi': o['psi'], 'durum': band(o['psi']), 'olasi_neden': notes[o['ozellik']]['neden'], 'aksiyon': notes[o['ozellik']]['aksiyon']} for o in table]
    tabular.write_csv(out / 'kayma.csv', list(rows_out[0]), rows_out)
    md = ['# Veri kayması', '', f'{len(table)} özellik; {sum(r["durum"] == "belirgin kayma" for r in rows_out)} belirgin kayma, {sum(r["durum"] == "izlenmeli" for r in rows_out)} izlenmeli. '
          f'Yeniden eğitim: **{"gerekli" if retrain else "gerekmiyor"}**.', '', summary, '',
          md_table(['Özellik', 'PSI', 'Durum', 'Olası neden', 'Aksiyon'], [[r['ozellik'], r['psi'], r['durum'], r['olasi_neden'], r['aksiyon']] for r in rows_out]), '',
          f'_Model: {meta["model"]} · PSI kodla hesaplandı; durumlar, yeniden eğitim kararı ve metindeki sayılar tabloyla denetlendi. Neden yorumları hipotezdir; '
          'kaynak sistem değişikliğiyle doğrulanmalıdır._']
    write_text(out / 'kayma-raporu.md', '\n'.join(md))
    print(f'[OK] {len(table)} özellik; yeniden eğitim {"gerekli" if retrain else "gerekmiyor"}. Çıktı: {out / "kayma-raporu.md"}')


if __name__ == '__main__':
    llm.run(main)
