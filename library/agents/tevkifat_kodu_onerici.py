"""Tevkifat kodu önerici (AI agent): fatura satırlarındaki mal/hizmet açıklamalarına (“Bina temizlik hizmeti”, “Forklift bakımı”, “Nakliye”) KDV
tevkifat kodu ve tevkif oranı önerir. Modele yalnızca tekil açıklamalar gider; tutar, alıcı ve VKN gönderilmez. Her öneri GİB kod listesindeki
kod/oran çiftlerine göre denetlenir: listede olmayan kod, koda uymayan oran, gerekçesiz öneri ve cevapsız satır reddedilir. Tevkifat
gerekmeyen satır “yok” ile işaretlenir. Kullanıcının zaten doldurduğu tevkifat_kodu değiştirilmez.
Çıktı: fatura satırları + tevkifat_kodu/tevkifat_orani (efatura-uret girdisi), tevkifat-onerileri.md."""
import argparse
import llm
import tabular
from common import here, output_dir, write_text, md_table
from ebelge import WITHHOLDING

NONE = ('yok', 'none', '-', '')


def fraction(pct):
    return 'tam' if pct == 100 else f'{pct // 10}/10'


def validator(items):
    def validate(obj):
        from ebelge import withholding_percent
        problems, out, seen = [], {}, set()
        for e in obj.get('oneriler') or []:
            if not isinstance(e, dict):
                continue
            text, code = str(e.get('urun', '')).strip(), str(e.get('kod', '')).strip().lower()
            reason = str(e.get('gerekce', '')).strip()
            seen.add(text)
            if text not in items:
                problems.append(f'listede olmayan açıklama: {text or "?"}')
                continue
            if not reason:
                problems.append(f'“{text}” için gerekçe yok')
                continue
            if code in NONE:
                out[text] = ('', None, reason)
                continue
            if code not in WITHHOLDING:
                problems.append(f'“{text}”: {code} GİB tevkifat kod listesinde yok')
                continue
            allowed = WITHHOLDING[code][1]
            try:
                pct = withholding_percent(e.get('oran')) if str(e.get('oran', '')).strip() else (next(iter(allowed)) if len(allowed) == 1 else None)
            except ValueError:
                pct = -1
            if pct not in allowed:
                problems.append(f'“{text}”: {code} için oran {e.get("oran") or "boş"} olamaz (geçerli: {", ".join(fraction(x) for x in sorted(allowed))})')
                continue
            out[text] = (code, pct, reason)
        missing = [t for t in items if t not in seen]
        if missing:
            problems.append('cevapsız açıklama: ' + ', '.join(missing[:10]))
        if problems:
            raise ValueError('; '.join(problems[:15]))
        return out
    return validate


SYSTEM = (llm.SAFETY + ' Sen Türk KDV mevzuatında kısmi ve tam tevkifatı bilen bir vergi uzmanısın. Her mal/hizmet açıklaması için KDV Genel Uygulama Tebliği’ndeki '
          'tevkifat kapsamına girip girmediğine karar ver; giriyorsa yalnızca verilen listedeki kodu ve o koda izin verilen oranı yaz. Kapsam dışıysa ya da açıklama karar '
          'vermeye yetmiyorsa kod "yok" yaz ve nedenini gerekçede söyle (ör. mal teslimi, kapsamda olmayan hizmet, açıklama belirsiz). Alıcının tevkifat yapmakla yükümlü '
          'olup olmadığını ve tutar sınırını kullanıcı ayrıca kontrol eder; sen yalnızca işlemin türüne bak. '
          'Yanıt şeması: {"oneriler": [{"urun": "...", "kod": "612", "oran": "9/10", "gerekce": "..."}]}')


def main():
    p = argparse.ArgumentParser(description='Fatura satırı açıklamalarına KDV tevkifat kodu/oranı öneren agent (yalnızca açıklamalar gönderilir).')
    p.add_argument('--input', default=str(here('ornek', 'hizmet_faturalari.csv')))
    p.add_argument('--column', default='urun', help='Mal/hizmet açıklaması sütunu')
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    if args.column not in columns:
        raise ValueError(f'{args.column} sütunu yok. Sütunlar: {", ".join(columns)}')
    style = tabular.number_style([r['kdv_orani'] for r in rows if (r.get('kdv_orani') or '').strip()]) if 'kdv_orani' in columns else None
    taxed = lambda r: style is None or not (r.get('kdv_orani') or '').strip() or tabular.parse_number(r['kdv_orani'], style) != 0
    given = lambda r: (r.get('tevkifat_kodu') or '').strip()
    items = []
    for r in rows:
        text = r[args.column].strip()
        if text and taxed(r) and not given(r) and text not in items:
            items.append(text)
    out = output_dir(args.output)
    result, model = {}, 'kullanılmadı'
    if items:
        codes = '\n'.join(f'- {c}: {name} (oran: {", ".join(fraction(x) for x in sorted(pcts))})' for c, (name, pcts) in WITHHOLDING.items() if int(c) < 800)
        user = ('Tevkifat kodu önerilecek mal/hizmet açıklamaları:\n' + '\n'.join(f'- {t}' for t in items)
                + '\n\nKısmi tevkifat kodları (GİB kod listesi):\n' + codes + '\n- 801–825: tam tevkifat (oran tam / 10/10)')
        result, meta = llm.ask(args, out, SYSTEM, user, validator(items))
        if result is None:
            return
        model = meta['model']
    new_cols = columns + [c for c in ('tevkifat_kodu', 'tevkifat_orani') if c not in columns]
    filled = 0
    for r in rows:
        r.setdefault('tevkifat_kodu', '')
        r.setdefault('tevkifat_orani', '')
        hit = result.get(r[args.column].strip())
        if hit and hit[0] and not given(r):
            r['tevkifat_kodu'], r['tevkifat_orani'] = hit[0], fraction(hit[1])
            filled += 1
    name = 'fatura_satirlari_tevkifatli.csv'
    tabular.write_csv(out / name, new_cols, rows)
    table = [[t, result[t][0] or '—', WITHHOLDING[result[t][0]][0] if result[t][0] else 'tevkifat yok', fraction(result[t][1]) if result[t][0] else '', result[t][2]] for t in items]
    kept = sum(1 for r in rows if given(r)) - filled
    md = ['# KDV tevkifat önerileri', '',
          f'{len(items)} tekil açıklama değerlendirildi: {sum(1 for t in items if result[t][0])} için tevkifat önerildi, {sum(1 for t in items if not result[t][0])} için gerekmiyor. '
          f'{filled} satıra kod yazıldı; kullanıcının önceden doldurduğu {kept} satıra dokunulmadı.', '',
          md_table(['Açıklama', 'Kod', 'Kapsam', 'Oran', 'Gerekçe'], table) if table else '_Önerilecek satır yok._', '',
          'Kontrol edin: alıcı tevkifat yapmakla yükümlü mü (belirlenmiş alıcı), fatura KDV dahil tutarı yılın kısmi tevkifat sınırını aşıyor mu? Bu agent yalnızca işlemin '
          'türüne bakar; öneriler mali müşavir onayının yerini tutmaz.', '',
          f'_Model: {model} · Kod/oran çiftleri GİB UBL-TR kod listesiyle denetlendi. {name} dosyasını efatura-uret bloğuna verin; tevkifatlı satırı olan fatura TEVKIFAT tipinde yazılır._']
    write_text(out / 'tevkifat-onerileri.md', '\n'.join(md))
    print(f'[OK] {len(items)} açıklama, {filled} satıra tevkifat kodu yazıldı. Çıktı: {out / "tevkifat-onerileri.md"}')


if __name__ == '__main__':
    llm.run(main)
