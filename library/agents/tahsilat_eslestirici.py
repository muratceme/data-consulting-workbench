"""Tahsilat eşleyici (AI agent): banka ekstresindeki gelen ödemeleri açık müşteri faturalarıyla eşler. Önce kural çalışır: açıklamada
fatura numarası geçen ve tutarı faturanın kalanına eşit olan hareketler model olmadan eşlenir. Kalanlar modele gider (açıklamalar
maskelenir; yalnızca açık fatura listesi ve hareket tutarları). Modelin her eşlemesi kodla denetlenir: fatura açık listede olmalı, bir
hareketteki faturalar aynı cariye ait olmalı, faturaların kalan toplamı hareket tutarına kuruşu kuruşuna eşit olmalı (yalnızca tek
faturalı kısmi ödemeye izin verilir ve “kısmi” işaretlenmelidir), bir faturanın kalanı birden fazla harekete dağıtılıp aşılamaz.
Eşlenemeyen hareket gerekçesiyle bırakılır. Çıktı: eslesmeler.csv, kalan_faturalar.csv, tahsilat-eslesme.md."""
import argparse, re
from collections import defaultdict
from decimal import Decimal
import llm
import tabular
import turkiye as tr
from common import here, output_dir, write_text, md_table, mask_text


def money(text, style):
    v = tabular.parse_number(text, style)
    if v is None:
        raise ValueError(f'tutar okunamadı: {text}')
    return Decimal(str(v)).quantize(Decimal('0.01'))


def validator(moves, invoices, remaining):
    def validate(obj):
        problems, out, used, seen = [], {}, defaultdict(Decimal), set()
        for e in obj.get('eslesmeler') or []:
            if not isinstance(e, dict):
                continue
            mid = str(e.get('hareket', '')).strip()
            seen.add(mid)
            if mid not in moves or mid in out:
                problems.append(f'hareket {mid or "?"} listede yok ya da iki kez eşlendi')
                continue
            nos = [str(x).strip() for x in e.get('faturalar') or [] if str(x).strip()]
            if not nos:
                if not str(e.get('gerekce', '')).strip():
                    problems.append(f'{mid}: eşlenmediyse gerekçe yaz')
                out[mid] = ([], False, str(e.get('gerekce', '')).strip())
                continue
            unknown = [n for n in nos if n not in invoices]
            if unknown:
                problems.append(f'{mid}: açık listede olmayan fatura: {", ".join(unknown)}')
                continue
            parties = {invoices[n]['cari'] for n in nos}
            if len(parties) > 1:
                problems.append(f'{mid}: faturalar farklı carilere ait ({", ".join(sorted(parties))})')
                continue
            amount, total = moves[mid]['tutar'], sum((remaining[n] for n in nos), Decimal(0))
            partial = bool(e.get('kismi'))
            if partial and (len(nos) != 1 or amount >= total):
                problems.append(f'{mid}: kısmi ödeme yalnızca tek faturada ve kalandan küçük tutarda olur (hareket {amount}, kalan {total})')
                continue
            if not partial and amount != total:
                problems.append(f'{mid}: hareket {amount} ≠ faturaların kalan toplamı {total} ({", ".join(f"{n}={remaining[n]}" for n in nos)})')
                continue
            for n in nos:
                used[n] += min(remaining[n], amount) if partial else remaining[n]
            out[mid] = (nos, partial, str(e.get('gerekce', '')).strip())
        over = [n for n, u in used.items() if u > remaining[n]]
        if over:
            problems.append('kalanı aşılan fatura (birden fazla harekete dağıtılmış): ' + ', '.join(over))
        missing = [m for m in moves if m not in seen]
        if missing:
            problems.append('cevapsız hareket: ' + ', '.join(missing))
        if problems:
            raise ValueError(' | '.join(problems[:20]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir muhasebe uzmanısın ve gelen banka ödemelerini açık müşteri faturalarıyla eşleştiriyorsun. Açıklamadaki cari adı, kısaltma, '
          'fatura numarası parçası ve tutar ipuçlarını kullan; bir ödeme aynı carinin birden fazla faturasını kapatabilir. Fatura kalanlarının toplamı ödeme '
          'tutarına tam eşit olmalı; tek faturaya eksik ödeme ise "kismi": true ile işaretlenir. Emin değilsen faturalar boş, gerekçe dolu kalsın. '
          'Yanıt şeması: {"eslesmeler": [{"hareket": "H03", "faturalar": ["FTR-1001", "FTR-1002"], "kismi": false, "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Banka tahsilatlarını açık faturalarla eşleyen agent; tutar ve cari tutarlılığı kodla denetlenir.')
    p.add_argument('--bank', default=str(here('ornek', 'tahsilat_hareketleri.csv')), help='hareket_no, tarih, aciklama, tutar')
    p.add_argument('--invoices', default=str(here('ornek', 'acik_faturalar.csv')), help='fatura_no, cari, tarih, kalan')
    llm.add_arguments(p)
    args = p.parse_args()
    bc, brows = tabular.read(args.bank)
    ic, irows = tabular.read(args.invoices)
    for c, cols, name in (('hareket_no', bc, 'banka'), ('aciklama', bc, 'banka'), ('tutar', bc, 'banka'), ('fatura_no', ic, 'fatura'), ('cari', ic, 'fatura'), ('kalan', ic, 'fatura')):
        if c not in cols:
            raise ValueError(f'{name} dosyasında {c} sütunu yok.')
    bs, ist = tabular.number_style([r['tutar'] for r in brows]), tabular.number_style([r['kalan'] for r in irows])
    moves = {r['hareket_no'].strip(): {**r, 'tutar': money(r['tutar'], bs)} for r in brows if money(r['tutar'], bs) > 0}
    invoices = {r['fatura_no'].strip(): {**r, 'cari': r['cari'].strip(), 'kalan': money(r['kalan'], ist)} for r in irows}
    remaining = {n: v['kalan'] for n, v in invoices.items()}
    matched = {}
    for mid, m in moves.items():  # rule: invoice number in the description and the amount equals what is left
        text = tr.fold(m['aciklama']).replace(' ', '')
        hits = [n for n in invoices if tr.fold(n).replace(' ', '').replace('-', '') in text.replace('-', '') and remaining[n] == m['tutar']]
        if len(hits) == 1:
            matched[mid] = ([hits[0]], False, 'açıklamada fatura no ve tutar eşit (kural)')
            remaining[hits[0]] = Decimal(0)
    todo = {mid: m for mid, m in moves.items() if mid not in matched}
    open_inv = {n: v for n, v in invoices.items() if remaining[n] > 0}
    out = output_dir(args.output)
    model = 'kullanılmadı'
    if todo:
        user = ('Eşlenecek banka hareketleri (hareket: tarih · tutar · açıklama):\n' + '\n'.join(f'{k}: {m.get("tarih", "")} · {m["tutar"]} · {mask_text(m["aciklama"])[0]}' for k, m in todo.items())
                + '\n\nAçık faturalar (fatura no · cari · tarih · kalan):\n' + '\n'.join(f'{n} · {v["cari"]} · {v.get("tarih", "")} · {remaining[n]}' for n, v in open_inv.items()))
        result, meta = llm.ask(args, out, SYSTEM, user, validator(todo, open_inv, {n: remaining[n] for n in open_inv}))
        if result is None:
            return
        model = meta['model']
        for mid, (nos, partial, why) in result.items():
            matched[mid] = (nos, partial, why)
            left = todo[mid]['tutar']
            for n in nos:
                take = min(remaining[n], left) if partial else remaining[n]
                remaining[n] -= take
                left -= take
    rows = []
    for mid, m in moves.items():
        nos, partial, why = matched[mid]
        rows.append({'hareket_no': mid, 'tarih': m.get('tarih', ''), 'tutar': f'{m["tutar"]:.2f}', 'aciklama': m['aciklama'], 'faturalar': ';'.join(nos),
                     'cari': invoices[nos[0]]['cari'] if nos else '', 'tur': ('kısmi' if partial else 'tam') if nos else 'eşlenmedi',
                     'kaynak': 'kural' if '(kural)' in why else ('model' if nos else ''), 'gerekce': why})
    tabular.write_csv(out / 'eslesmeler.csv', list(rows[0]), rows)
    left_rows = [{'fatura_no': n, 'cari': v['cari'], 'ilk_kalan': f'{v["kalan"]:.2f}', 'kalan': f'{remaining[n]:.2f}'} for n, v in invoices.items() if remaining[n] > 0]
    tabular.write_csv(out / 'kalan_faturalar.csv', ['fatura_no', 'cari', 'ilk_kalan', 'kalan'], left_rows)
    total = sum((m['tutar'] for m in moves.values()), Decimal(0))
    done = sum((moves[r['hareket_no']]['tutar'] for r in rows if r['faturalar']), Decimal(0))
    md = ['# Tahsilat eşleme', '', f'{len(moves)} gelen ödeme ({total:,.2f}): {sum(1 for r in rows if r["kaynak"] == "kural")} kuralla, {sum(1 for r in rows if r["kaynak"] == "model")} modelle eşlendi, '
          f'{sum(1 for r in rows if not r["faturalar"])} eşlenmedi. Eşlenen tutar {done:,.2f}; {len(left_rows)} fatura açık kaldı.', '',
          md_table(['Hareket', 'Tutar', 'Açıklama', 'Faturalar', 'Tür', 'Kaynak'], [[r['hareket_no'], r['tutar'], r['aciklama'], r['faturalar'] or '—', r['tur'], r['kaynak'] or '—'] for r in rows]), '',
          f'_Model: {model} · Her eşlemede fatura varlığı, cari birliği ve tutar eşitliği kodla denetlendi. Muhasebe kaydı oluşturulmadı; eşlemeleri onaylayıp ERP’ye siz aktarın._']
    write_text(out / 'tahsilat-eslesme.md', '\n'.join(md))
    print(f'[OK] {len(moves)} hareket; {sum(1 for r in rows if r["faturalar"])} eşlendi. Çıktı: {out / "tahsilat-eslesme.md"}')


if __name__ == '__main__':
    llm.run(main)
