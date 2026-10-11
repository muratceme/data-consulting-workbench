import hashlib, json, shutil, sys, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'catalog'))
from catalog import catalog

def build():
 dist=ROOT/'dist'; dist.mkdir(exist_ok=True)
 shutil.copytree(ROOT/'site',dist,dirs_exist_ok=True); packages=dist/'packages'; packages.mkdir(exist_ok=True)
 data=catalog()
 for d in data:
  for tool in d['tools']:
   for product in tool['products']:
    connector={'Qlik Cloud':'qlik','Power BI Service':'powerbi','BigQuery':'bigquery'}.get(product['name'])
    if connector:
     product['tasks'].append({'id':product['id']+'-api','title':'API’den salt okunur envanter raporu','kind':'code','engine':'connector','connector':connector,'description':'Qlik uygulamaları / Power BI raporları / BigQuery tablolarını API’den listeler.','reason':'Listeleme ve rapor üretimi sabit kurallarla yapılır.','input':'Access token + ortam kimlikleri','execution':'Platform API','status':'ready'})
    for task in product['tasks']:
     cfg={'engine':task['engine'],'task':task['title'],'domain':d['name'],'product':product['name'],'group_by':'region','keys':['id'],'mask_fields':['email'],'rules':{'id':{'required':True,'unique':True},'amount':{'type':'number','min':0}}}
     if task['engine']=='connector': cfg.update(connector=task['connector'],tenant='https://YOUR-TENANT.eu.qlikcloud.com',workspace_id='YOUR-WORKSPACE-ID',project_id='YOUR-PROJECT',dataset_id='YOUR-DATASET')
     if task.get('brief'): cfg['brief']=task['brief']
     agent=task['kind']=='agent'; entry='agent.py' if agent else ('connector.py' if task['engine']=='connector' else 'runner.py')
     flags=' --dry-run' if agent else (' --input sql' if task['engine']=='sql' else (' --compare compare.csv' if task['engine'] in ('schema','reconcile') else ''))
     readme=f'''# {task['title']}

{d['name']} → {tool['name']} → {product['name']}

## Çalışma biçimi
{task['execution']}. {task['description']}
{'Model API anahtarı ve model adı .env içinde seçilir. Sağlayıcıya yalnızca sizin verdiğiniz bağlam ve kanıt dosyası gönderilir. Çıktı danışmanlık taslağıdır; platformda işlem yapmaz.' if agent else ('ACCESS_TOKEN ortam değişkeni ve config.json içindeki ortam kimlikleri gerekir. GET istekleriyle salt okunur envanter çıkarır. Canlı tenant testi yapılmadı.' if task['engine']=='connector' else 'Bu paket ürünün CSV dışa aktarımı üzerinden çalışır. Ürüne bağlanan native connector değildir. Aynı açık kaynak motor farklı ürün bağlamlarında kullanılır.')}

## Başlat
Python 3.10+ gerekir. Harici Python bağımlılığı yoktur.
ZIP’i bir klasöre açın, terminali o klasörde açın:

```sh
python {entry}{flags}
```

Windows’ta python yerine py kullanabilirsiniz. run.bat örnek çalışmayı başlatır.
config.json alan adlarını kendi verinize göre düzenleyin. Sonuçlar output/ içine yazılır.
{'Önce dry-run ile isteği inceleyin; gerçek çalıştırmada --dry-run bayrağını kaldırın. OpenAI uyumlu API, Anthropic ve Ollama desteklenir. Model erişimi ve ağ bağlantısı kullanıcı ortamında sağlanır.' if agent else 'CSV kodlaması UTF-8, ayırıcı virgül, ondalık ayırıcı nokta olmalıdır. Kaynak dosya değiştirilmez. Maskelenmiş çıktı yalnızca seçilen alanları değiştirir; otomatik PII keşfi veya anonimlik garantisi vermez.'}

## Doğrulama
Yerel motorlar örnek ve uç durum testlerinden geçer. API bağlayıcıları mock sayfalama testinden geçer; canlı hesap testi yapılmadı. Agent için ağsız dry-run doğrulanır; model yanıt kalitesi kullanıcı verisiyle değerlendirilmelidir.
'''
     files={'config.json':json.dumps(cfg,ensure_ascii=False,indent=2),'README.md':readme,'sample.csv':'id,region,amount,email\n1,Marmara,1200,ayse@example.test\n2,Ege,850,ali@example.test\n3,Marmara,640,\n4,Ege,-20,test@example.test\n','compare.csv':'id,region,amount,email\n1,Marmara,1200,ayse@example.test\n2,Ege,900,ali@example.test\n5,Ege,100,veli@example.test\n','context.md':f'Ürün: {product["name"]}\nGörev: {task["title"]}\nİş amacı: Haftalık veri danışmanlığı teslimi.\nKısıt: Üretim sistemine yazma yapılmayacak.\nKendi proje bağlamınızı buraya ekleyin.','evidence.json':json.dumps({'source':'sample.csv','row_count':4,'issues':[{'field':'amount','row':5,'rule':'min:0'}]},ensure_ascii=False),'sql/example.sql':'select region, sum(amount) from analytics.sales group by region;','run.bat':f'@echo off\r\ncd /d "%~dp0"\r\npython {entry}{flags}\r\npause\r\n','.env.example':'PROVIDER=openai\nOPENAI_API_KEY=\nOPENAI_BASE_URL=https://api.openai.com/v1\nANTHROPIC_API_KEY=\nOLLAMA_URL=http://localhost:11434\n# MODEL değerini sağlayıcınıza uygun seçin\nMODEL=\n'}
     files['LICENSE']=(ROOT/'LICENSE').read_text(encoding='utf-8')
     files[entry]=(ROOT/'library'/entry).read_text(encoding='utf-8')
     if entry=='connector.py': files['runner.py']=(ROOT/'library/runner.py').read_text(encoding='utf-8')
     path=packages/(task['id']+'.zip')
     with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
      for name,content in sorted(files.items()):
       info=zipfile.ZipInfo(name,date_time=(2026,10,11,0,0,0)); info.compress_type=zipfile.ZIP_DEFLATED; z.writestr(info,content)
     task['download']='/packages/'+path.name; task['sha256']=hashlib.sha256(path.read_bytes()).hexdigest(); task['bytes']=path.stat().st_size; task['command']='python '+entry+flags
 (dist/'catalog.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
 (dist/'checksums.json').write_text(json.dumps({t['id']:t['sha256'] for d in data for tool in d['tools'] for p in tool['products'] for t in p['tasks']},indent=2),encoding='utf-8')
 print(f'Build: {len(data)} domains, {sum(len(t["products"]) for d in data for t in d["tools"])} product contexts, {sum(len(p["tasks"]) for d in data for t in d["tools"] for p in t["products"])} ZIP packages')
if __name__=='__main__': build()
