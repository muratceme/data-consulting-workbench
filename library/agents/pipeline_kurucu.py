"""Pipeline kurucu (AI agent): gece/saatlik bir veri hattının serbest metinle anlatımını (kaynaklar, sıra, saat, son teslim) okur; model bunu görev
listesine (tür, komut, bağımlılıklar, havuz, deneme, tahmini süre) ve zamanlamaya çevirir. Liste kodla denetlenir: geçerli cron, döngü ve bilinmeyen
bağımlılık yok, python görevi “modül:fonksiyon” biçiminde, yıkıcı komut (rm -rf, DROP, TRUNCATE, curl | sh) yok, kritik yol son saati kaçırmıyor.
Onaylanan listeyle Airflow DAG kurulum bloğu DAG dosyasını ve havuz ayarlarını kodla üretir. Hiçbir sisteme bağlanmaz, komut çalıştırmaz.
Çıktı: pipeline-plani.md, gorevler.csv, dags/<dag_id>.py, pools.json."""
import argparse, re
from argparse import Namespace
from pathlib import Path
import llm
import tabular
from common import here, output_dir, write_json, write_text, md_table
import platform_kurulum as pk

COLUMNS = ['gorev', 'tur', 'komut', 'bagimli', 'havuz', 'deneme', 'sure_dk']
DANGER = re.compile(r'\brm\s+-[a-z]*r[a-z]*f|\bDROP\s+(TABLE|DATABASE|SCHEMA)\b|\bTRUNCATE\b|\bcurl\b[^|]*\|\s*(ba)?sh|\bmkfs\b|\bdd\s+if=|>\s*/dev/sd', re.I)


def validator(args):
    def validate(obj):
        problems = []
        dag_id = str(obj.get('dag_id', '')).strip()
        schedule = str(obj.get('schedule', '')).strip()
        tz = str(obj.get('timezone', '') or 'Europe/Istanbul').strip()
        deadline = str(obj.get('deadline', '') or '').strip()
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,60}', dag_id):
            problems.append('dag_id küçük harf, rakam ve alt çizgiden oluşmalı (3–60 karakter)')
        if not pk.cron_ok(schedule):
            problems.append(f'schedule geçerli 5 alanlı cron olmalı (ör. "0 2 * * *"): {schedule or "?"}')
        if deadline and not re.fullmatch(r'\d{1,2}:\d{2}', deadline):
            problems.append('deadline SS:DD biçiminde olmalı')
        if not re.fullmatch(r'[A-Za-z]+/[A-Za-z_]+', tz):
            problems.append('timezone IANA adı olmalı (ör. Europe/Istanbul)')
        rows = []
        for i, t in enumerate(obj.get('gorevler') or [], start=1):
            if not isinstance(t, dict):
                problems.append(f'{i}. görev nesne değil')
                continue
            name, cmd = str(t.get('gorev', '')).strip(), str(t.get('komut', '')).strip()
            deps = t.get('bagimli') or []
            deps = [d.strip() for d in deps.split(',')] if isinstance(deps, str) else [str(d).strip() for d in deps]
            try:
                minutes, retries = int(t.get('sure_dk', 0)), int(t.get('deneme', 0))
            except (TypeError, ValueError):
                problems.append(f'{name or i}: sure_dk ve deneme tam sayı olmalı')
                continue
            if not cmd:
                problems.append(f'{name or i}: komut boş')
            elif DANGER.search(cmd):
                problems.append(f'{name}: yıkıcı komut içeriyor; silme/boşaltma işlerini hatta koymayın, ayrı ve onaylı bir işle yapın')
            if not 1 <= minutes <= 600:
                problems.append(f'{name}: sure_dk 1–600 olmalı')
            if not 0 <= retries <= 5:
                problems.append(f'{name}: deneme 0–5 olmalı')
            rows.append({'gorev': name, 'tur': str(t.get('tur', '')).strip(), 'komut': cmd, 'bagimli': ','.join(d for d in deps if d),
                         'havuz': str(t.get('havuz', '') or 'default_pool').strip(), 'deneme': str(retries), 'sure_dk': str(minutes)})
        if not rows and not problems:
            problems.append('en az bir görev gerekli')
        if problems:
            raise ValueError('; '.join(problems))
        ns = Namespace(dag_id=dag_id, schedule=schedule, timezone=tz, start='2026-01-01', deadline=deadline or '23:59', sql_conn=args.sql_conn, dbt_dir=args.dbt_dir, owner=args.owner)
        res = pk.airflow_dag(COLUMNS, rows, ns)  # cycles, unknown dependencies, bad task types raise ValueError and go back to the model
        if deadline and res.summary['sure_payi_dk'] is not None and res.summary['sure_payi_dk'] < 0:
            raise ValueError(f'kritik yol {res.summary["kritik_yol_dk"]} dk; hat {res.summary["bitis"]}’te biter ve {deadline} son saatini kaçırır. Bağımsız görevleri paralel yapın '
                             'veya başlangıcı öne alın')
        return {'dag_id': dag_id, 'schedule': schedule, 'timezone': tz, 'deadline': deadline, 'satirlar': rows, 'sonuc': res,
                'gerekce': str(obj.get('gerekce', '')).strip(), 'sorular': [str(q).strip() for q in obj.get('sorular') or [] if str(q).strip()]}
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri mühendisisin. Kullanıcının anlattığı veri hattını Airflow görevlerine çevir. Görev türleri: sensor (dosya bekler; komut dosya yolu, '
          'tarih için {{ ds_nodash }} kullan), bash (komut satırı), python (komut "modul.yol:fonksiyon"), sql (komut .sql dosya yolu), dbt (komut dbt seçimi, ör. tag:mart), '
          'dbt_test (aynı seçimin testleri). Her görev için bagimli listesi, havuz (aynı sistemi yoran görevler aynı havuzda), deneme (0–5) ve tahmini sure_dk ver. Birbirine '
          'bağlı olmayan işleri paralel bırak. Zamanlama cron olsun; metinde son saat varsa deadline’a yaz. Metinde olmayan sistem veya betik uydurma; bilinmeyen yolu sorular’a yaz. '
          'Silme/boşaltma komutu yazma. Yanıt şeması: {"dag_id": "gece_veri_hatti", "schedule": "0 2 * * *", "timezone": "Europe/Istanbul", "deadline": "05:00", '
          '"gorevler": [{"gorev": "erp_yukle", "tur": "bash", "komut": "python /etl/erp.py", "bagimli": [], "havuz": "erp", "deneme": 2, "sure_dk": 25}], "gerekce": "...", "sorular": ["..."]}')


def main():
    p = argparse.ArgumentParser(description='Serbest metindeki veri hattı isteğini doğrulanmış Airflow DAG’ine çeviren agent.')
    p.add_argument('--request', default=str(here('ornek', 'pipeline_istek.md')), help='İstek metni veya .md dosyası')
    p.add_argument('--sql-conn', default='ambar')
    p.add_argument('--dbt-dir', default='/opt/airflow/dbt')
    p.add_argument('--owner', default='veri-ekibi')
    llm.add_arguments(p)
    args = p.parse_args()
    request = Path(args.request).read_text(encoding='utf-8-sig').strip() if Path(args.request).is_file() else args.request
    out = output_dir(args.output)
    user = f'Veri hattı isteği:\n"""\n{request}\n"""'
    plan, meta = llm.ask(args, out, SYSTEM, user, validator(args))
    if plan is None:
        return
    res = plan['sonuc']
    for name, content in res.files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text(path, content)
    tabular.write_csv(out / 'gorevler.csv', COLUMNS, plan['satirlar'])
    write_json(out / 'pipeline-plani.json', {k: v for k, v in plan.items() if k != 'sonuc'} | {'ozet': res.summary})
    md = [f"# Veri hattı — {plan['dag_id']}", '', f'**İstek:** {request}', '', plan['gerekce'], '',
          f"Zamanlama: `{plan['schedule']}` ({plan['timezone']}) · Son saat: {plan['deadline'] or 'verilmedi'} · Tahmini bitiş: {res.summary['bitis']}"
          + (f" · Pay: {res.summary['sure_payi_dk']} dk" if res.summary['sure_payi_dk'] is not None else ''), '',
          md_table(['Görev', 'Tür', 'Komut', 'Bağımlı', 'Havuz', 'Deneme', 'Süre'], [[r['gorev'], r['tur'], r['komut'], r['bagimli'] or '—', r['havuz'], r['deneme'], r['sure_dk']]
                                                                                     for r in plan['satirlar']]), '',
          '## Zaman çizelgesi (kodla hesaplandı)', '', md_table(['Görev', 'Başlangıç (dk)', 'Bitiş (dk)', 'Kritik'], [[r['gorev'], r['baslangic_dk'], r['bitis_dk'], r['kritik']] for r in res.rows]), '']
    md += [f'- {n}' for n in res.notes]
    if plan['sorular']:
        md += ['', '## Netleştirilecekler', ''] + [f'- {q}' for q in plan['sorular']]
    md += ['', f"_Model: {meta['model']} · Görev listesi kodla denetlendi; DAG’i Airflow DAG kurulum bloğu üretti. Komutları çalıştırmadan önce gözden geçirin._"]
    write_text(out / 'pipeline-plani.md', '\n'.join(md))
    print(f"[OK] {plan['dag_id']}: {res.headline}. Çıktı: {out / 'pipeline-plani.md'}")


if __name__ == '__main__':
    llm.run(main)
