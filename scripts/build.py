"""Build only explicitly implemented native packages; planned tasks have no download."""
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'catalog'))
from catalog import DOMAINS
from native_catalog import catalog, COMMON

def build():
 dist=ROOT/'dist';dist.mkdir(exist_ok=True)
 shutil.copytree(ROOT/'site',dist,dirs_exist_ok=True)
 packages=dist/'packages';packages.mkdir(exist_ok=True)
 data=catalog(DOMAINS); checksums={};expected=set();matrix=[]
 for domain in data:
  for tool in domain['tools']:
   for product in tool['products']:
    matrix.append({'domain':domain['name'],'tool':tool['name'],'product':product['name'],**product['integration'],'implemented_operations':[t['operation'] for t in product['tasks'] if t['engine']=='native']})
    for task in product['tasks']:
     if task['engine']!='native':continue
     cfg={'operation':task['operation'],**task['config']}
     readme=f'''# {task['title']}

{domain['name']} → {tool['name']} → {product['name']}

{task['description']}

## Gerçek teslim
{task['output']}. ZIP, bağlantı bilgilerinizi girdikten sonra bunu üreten/indiren çalıştırıcıdır; içinde önceden üretilmiş müşteri raporu bulunmaz.

## Bağlantı ve izinler
{task['requirements']}
{COMMON}

## Çalıştırma
1. ZIP’i açın. `.env.example` dosyasını `.env` olarak kopyalayın; gerekli erişim değişkenlerini doldurun. Anahtarları tarayıcıya, config.json’a veya Git’e koymayın.
2. `config.json` içindeki YOUR_ alanlarını ve 0/-1 kimlik yer tutucularını doldurun. Girdi dosyalarını README'deki işleme göre düzenleyin. Token alma/yenileme kullanıcının kimlik sağlayıcısında yapılır; paket token üretmez.
3. `python native.py --plan` ile işlemi kontrol edin (ağ çağrısı yok).
4. `python native.py --execute --output output/run-001` komutunu çalıştırın. Her çalışma için yeni/boş çıktı klasörü kullanın.

## Kesinti sonrası takip
Fabric notebook job, Databricks job, Airbyte sync, ADF run, Airflow run, dbt run, Prefect run, Dataform invocation ve Power BI enhanced refresh paketleri önceki kaydı kullanabilir:
`python native.py --plan --resume-from output/run-001/run-state.json`
`python native.py --execute --resume-from output/run-001/run-state.json --output output/continued-001`
Orijinal config kaynak adreslerini koruyun. Bu seçenek yeni iş başlatmaz; yalnızca mevcut işi takip eder. İş kimliği kaydedilmediyse önce platform geçmişinden bulun. Diğer işlemlerde bu seçenek açık hata verir; otomatik yeniden gönderim yapılmaz.

## Platform etkileri
{task['effects']}
HTTP GET geçici 429/502/503/504 hatalarında sınırlı tekrar yapar; yazma çağrıları otomatik tekrarlanmaz. Sertifika denetimi açıktır; cross-origin download/redirect desteklenmez. Kimlik bilgisi farklı sunucuya iletilmez. 512 MB indirme, 16 MB metadata ve sınırlı polling uygulanır. Büyük dosyalar için streaming desteği sonraki iştir.

## Test durumu
Yerel HTTP sözleşme testleri: uygulanmış çağrı zinciri, başarısızlık ve çıktı kontrolleri.
Canlı müşteri hesabı: TEST EDİLMEDİ.
Dosyanın gerçek uygulamada açılması: TEST EDİLMEDİ.
Yerel test fixture’ları gerçek QVF/PBIX/workbook üretildiği anlamına gelmez. Satışa hazır/üretim onaylı değildir.

## Kaynaklar
'''+ '\n'.join('- '+url for url in task['sources'])+'\n'
     env='\n'.join(key+'=' for key in task['secrets'])+'\n'
     if task['kind']=='agent':env=env.replace('PROVIDER=\n','PROVIDER=openai\n');env+='OPENAI_BASE_URL=https://api.openai.com/v1\nOLLAMA_URL=http://localhost:11434\n'
     files={'config.json':json.dumps(cfg,ensure_ascii=False,indent=2),'README.md':readme,'.env.example':env,'.gitignore':'.env\noutput/\n','native.py':(ROOT/'library/native.py').read_text(encoding='utf-8'),'agent.py':(ROOT/'library/agent.py').read_text(encoding='utf-8'),'LICENSE':(ROOT/'LICENSE').read_text(encoding='utf-8'),'run.bat':'@echo off\r\ncd /d "%~dp0"\r\npython native.py --plan\r\npause\r\n'}
     files.update(task.get('input_files',{}))
     files['contract.json']=json.dumps({key:task[key] for key in ('operation','kind','output','requirements','effects','secrets','sources','verification')},ensure_ascii=False,indent=2)
     if task['operation']=='bigquery-table':files['query.sql']='-- Replace with your reviewed source query. No example business data is fabricated.\nSELECT 1 AS connection_check;\n'
     if task['kind']=='agent':files['context.md']='İş isteğini, hesaplama kurallarını ve beklenen sonucu burada tanımla. Bu metin ve tablo şeması seçtiğin modele gönderilir.\n'
     filename=task['id']+'.zip';expected.add(filename);path=packages/filename
     with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
      for name,content in sorted(files.items()):
       info=zipfile.ZipInfo(name,date_time=(2026,10,12,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,content)
     task.update(download='/packages/'+filename,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),bytes=path.stat().st_size,command='python native.py --plan')
     checksums[task['id']]=task['sha256']
 # Only delete stale generated ZIP files, directly within this project's dist/packages.
 # Source and user files are never scanned or removed.
 assert packages.resolve().parent==dist.resolve()
 for old in packages.glob('*.zip'):
  if old.name not in expected and old.resolve().parent==packages.resolve():old.unlink()
 for name,value in [('catalog.json',data),('checksums.json',checksums),('integration-plan.json',matrix)]:
  (dist/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
 print(f'Native build: {len(data)} domains, {len(matrix)} product contexts, {len(checksums)} contract-tested packages; all other tasks explicitly planned. Live tests: 0.')
if __name__=='__main__':build()
