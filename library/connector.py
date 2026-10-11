"""Read-only API inventory. Access token is provided through the environment."""
import argparse, json, os, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from runner import save

def fetch(url):
 token=os.environ.get('ACCESS_TOKEN')
 if not token: raise ValueError('ACCESS_TOKEN ortam değişkeni gereklidir.')
 req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=60) as r: return json.load(r)
 except urllib.error.HTTPError as e: raise ValueError(f'API HTTP {e.code}; yetki ve yapılandırmayı kontrol edin.') from None

def inventory(cfg):
 kind=cfg['connector']; records=[]
 if kind=='qlik':
  tenant=cfg['tenant']; parts=urlsplit(tenant)
  if parts.scheme!='https' or not parts.hostname or not parts.hostname.endswith('.qlikcloud.com') or parts.path not in ('','/'): raise ValueError('tenant HTTPS Qlik Cloud adresi olmalıdır.')
  url=tenant.rstrip('/')+'/api/v1/items?resourceType=app&limit=100'
  while url:
   data=fetch(url); records.extend(data.get('data',[])); next_url=data.get('links',{}).get('next',{}).get('href')
   if next_url:
    if next_url.startswith('/'): next_url=tenant.rstrip('/')+next_url
    if urlsplit(next_url).netloc!=parts.netloc: raise ValueError('Beklenmeyen sayfalama adresi.')
   url=next_url
 elif kind=='powerbi':
  group=quote(cfg['workspace_id'],safe=''); data=fetch(f'https://api.powerbi.com/v1.0/myorg/groups/{group}/reports'); records=data.get('value',[])
 elif kind=='bigquery':
  base='https://bigquery.googleapis.com/bigquery/v2/projects/'+quote(cfg['project_id'],safe='')+'/datasets/'+quote(cfg['dataset_id'],safe='')+'/tables'
  page=None
  while True:
   data=fetch(base+'?'+urlencode({'maxResults':1000,**({'pageToken':page} if page else {})})); records.extend(data.get('tables',[])); page=data.get('nextPageToken')
   if not page: break
 else: raise ValueError('Bilinmeyen connector.')
 return {'connector':kind,'count':len(records),'records':records,'operation':'Salt okunur envanter; platforma yazma yapılmadı.'}

if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--config',default='config.json'); p.add_argument('--output',default='output'); a=p.parse_args()
 try: result=inventory(json.loads(Path(a.config).read_text(encoding='utf-8'))); save(result,'api-inventory',a.output)
 except (ValueError,KeyError,urllib.error.URLError) as e: p.exit(1,str(e)+'\n')
 print('API envanter raporu hazır: '+a.output)
