"""Ambar kurucu (AI agent): veri platformunun sıfırdan nasıl kurulacağını anlatan serbest metni (ortamlar, ekipler, kim hangi katmanı okur/yazar,
bütçe) okur; model bunu “ekip, katman, erişim, depo boyutu, aylık kredi” planına çevirir. Plan kodla denetlenir (geçerli adlar, erişim düzeyleri, depo
boyları, ekip başına tek bütçe) ve seçilen platform için kurulum bloğu çalıştırılır: Snowflake SQL’i, Unity Catalog SQL’i veya BigQuery Terraform’u.
Üretilen yetkilerin etkisi hesaplanıp planla karşılaştırılır; fark varsa yanıt reddedilir. Hiçbir hesaba bağlanmaz.
Çıktı: ambar-plani.md, ekipler.csv (planın düzenlenebilir hâli) ve platformun kurulum dosyaları."""
import argparse, re
from argparse import Namespace
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
import platform_kurulum as pk

PLATFORMS = {'snowflake': (pk.snowflake_setup, 'Snowflake'), 'unity': (pk.uc_setup, 'Databricks Unity Catalog'), 'bigquery': (pk.bq_setup, 'BigQuery (Terraform)')}
NAME = re.compile(r'^[a-z][a-z0-9_]{0,29}$')
COLUMNS = ['ekip', 'katman', 'erisim', 'depo_boyutu', 'aylik_kredi']


def validator(platform, args):
    run, _ = PLATFORMS[platform]

    def validate(obj):
        problems = []
        envs = [str(e).strip().lower() for e in obj.get('ortamlar') or []]
        if not 1 <= len(envs) <= 4 or any(not NAME.match(e) for e in envs) or len(set(envs)) != len(envs):
            problems.append('ortamlar 1–4 farklı küçük harfli ad olmalı (ör. dev, prod)')
        rows, budget = [], {}
        for i, x in enumerate(obj.get('ekipler') or [], start=1):
            if not isinstance(x, dict):
                problems.append(f'{i}. satır nesne değil')
                continue
            team, layer, acc = (str(x.get(k, '')).strip().lower() for k in ('ekip', 'katman', 'erisim'))
            size = str(x.get('depo_boyutu') or '').strip().upper()
            credit = x.get('aylik_kredi')
            if not NAME.match(team) or not NAME.match(layer):
                problems.append(f'{i}. satır: ekip ve katman küçük harf, rakam ve alt çizgiden oluşmalı ({team or "?"}, {layer or "?"})')
                continue
            if acc not in pk.LEVELS:
                problems.append(f'{team}.{layer}: erisim okuma, yazma veya sahip olmalı')
                continue
            if platform == 'snowflake' and size not in pk.SIZES:
                problems.append(f'{team}: depo_boyutu şunlardan biri olmalı: {", ".join(pk.SIZES)}')
                continue
            if credit not in (None, ''):
                try:
                    credit = float(credit)
                except (TypeError, ValueError):
                    credit = -1
                if not 0 < credit <= 100000:
                    problems.append(f'{team}: aylik_kredi 0–100.000 arası sayı olmalı')
                    continue
            if team in budget and budget[team] != (size, credit) and credit not in (None, ''):
                problems.append(f'{team}: ekibin depo boyutu ve kredisi her satırda aynı olmalı')
                continue
            budget.setdefault(team, (size, credit))
            if any(r['ekip'] == team and r['katman'] == layer for r in rows):
                problems.append(f'{team}.{layer} iki kez yazılmış; tek satırda en yüksek erişimi verin')
                continue
            rows.append({'ekip': team, 'katman': layer, 'erisim': acc, 'depo_boyutu': budget[team][0], 'aylik_kredi': '' if budget[team][1] in (None, '') else f'{budget[team][1]:g}'})
        if not rows and not problems:
            problems.append('en az bir ekip satırı gerekli')
        if problems:
            raise ValueError('; '.join(problems))
        ns = Namespace(envs=','.join(envs), prod_human_write=bool(obj.get('prod_insan_yazma')), dev_share=args.dev_share, storage_root=args.storage_root,
                       project_prefix=args.project_prefix, domain=args.domain, location='EU', sandbox_days=30)
        res = run(COLUMNS, rows, ns)
        if res.summary.get('fark'):
            raise ValueError(f'üretilen yetkiler planla {res.summary["fark"]} hücrede farklı; planı sadeleştirin')
        owners = {r['katman'] for r in rows if r['erisim'] == 'sahip'}
        gaps = sorted({r['katman'] for r in rows} - owners)
        return {'ortamlar': envs, 'satirlar': rows, 'prod_insan_yazma': ns.prod_human_write, 'sonuc': res, 'sahipsiz': gaps,
                'gerekce': str(obj.get('gerekce', '')).strip(), 'sorular': [str(q).strip() for q in obj.get('sorular') or [] if str(q).strip()]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri platformu mimarısın. Kullanıcının anlattığı kurulumu bir erişim planına çevir: ortamlar (ör. dev, prod) ve her ekip–katman için tek '
          'satır: erisim okuma (yalnızca okur), yazma (tablo yazar/oluşturur) veya sahip (katmanı yönetir, şema açar). Katman adları raw, staging, mart, sandbox gibi kısa '
          've küçük harfli olsun; ekip adları alt çizgili (veri_muhendisligi). depo_boyutu XSMALL, SMALL, MEDIUM, LARGE, XLARGE, XXLARGE; aylik_kredi metinde verilmişse sayı, '
          'verilmemişse boş bırak ve sorular’a yaz. Metinde olmayan ekip veya katman uydurma. Prod’da insanların yazmaması isteniyorsa prod_insan_yazma false. '
          'Yanıt şeması: {"ortamlar": ["dev", "prod"], "ekipler": [{"ekip": "analitik", "katman": "mart", "erisim": "okuma", "depo_boyutu": "SMALL", "aylik_kredi": 150}], '
          '"prod_insan_yazma": false, "gerekce": "...", "sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Serbest metindeki kurulum isteğini erişim planına çevirip Snowflake / Unity Catalog / BigQuery kurulum dosyalarını üreten agent.')
    p.add_argument('--request', default=str(here('ornek', 'ambar_istek.md')), help='İstek metni veya .md dosyası')
    p.add_argument('--platform', choices=list(PLATFORMS), default='snowflake')
    p.add_argument('--dev-share', type=float, default=0.25)
    p.add_argument('--storage-root', default='abfss://lake@hesap.dfs.core.windows.net')
    p.add_argument('--project-prefix', default='sirket-veri')
    p.add_argument('--domain', default='sirket.com')
    llm.add_arguments(p)
    args = p.parse_args()
    request = Path(args.request).read_text(encoding='utf-8-sig').strip() if Path(args.request).is_file() else args.request
    out = output_dir(args.output)
    user = f'Platform: {PLATFORMS[args.platform][1]}\n\nKurulum isteği:\n"""\n{request}\n"""'
    plan, meta = llm.ask(args, out, SYSTEM, user, validator(args.platform, args))
    if plan is None:
        return
    res = plan['sonuc']
    for name, content in res.files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, tuple):
            tabular.write_csv(path, content[0], content[1])
        else:
            write_text(path, content)
    tabular.write_csv(out / 'ekipler.csv', COLUMNS, plan['satirlar'])
    write_json(out / 'ambar-plani.json', {k: v for k, v in plan.items() if k != 'sonuc'} | {'ozet': res.summary, 'platform': args.platform})
    md = [f'# Ambar kurulum planı — {PLATFORMS[args.platform][1]}', '', f'**İstek:** {request}', '', plan['gerekce'], '',
          f"Ortamlar: {', '.join(plan['ortamlar'])} · Prod’da insan yazması: {'evet' if plan['prod_insan_yazma'] else 'hayır (servis kimlikleri)'}", '',
          md_table(['Ekip', 'Katman', 'Erişim', 'Depo', 'Aylık kredi'], [[r['ekip'], r['katman'], r['erisim'], r['depo_boyutu'] or '—', r['aylik_kredi'] or '—'] for r in plan['satirlar']]), '',
          '## Kurulum (kodla üretildi)', '', f'**{res.headline}**', '', md_table(['Ölçü', 'Değer'], [[k, v] for k, v in res.summary.items()]), '']
    md += [f'- {n}' for n in res.notes]
    if plan['sahipsiz']:
        md.append(f"- Sahibi olmayan katman: {', '.join(plan['sahipsiz'])}; şema açma ve nesne sahipliği yalnızca platform yöneticisinde kalır.")
    if plan['sorular']:
        md += ['', '## Netleştirilecekler', ''] + [f'- {q}' for q in plan['sorular']]
    md += ['', f"_Model: {meta['model']} · Plan kodla denetlendi; kurulum dosyalarını platform kurulum bloğu üretti ve yetkiler planla karşılaştırıldı. Çalıştırmadan önce gözden geçirin._"]
    write_text(out / 'ambar-plani.md', '\n'.join(md))
    print(f"[OK] {PLATFORMS[args.platform][1]}: {res.headline}. Çıktı: {out / 'ambar-plani.md'}")


if __name__ == '__main__':
    llm.run(main)
