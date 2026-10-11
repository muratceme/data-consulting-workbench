"""Dependency-free, reusable deterministic data consulting engines."""
import argparse, csv, html, json, math, re, sys
from collections import Counter, defaultdict
from pathlib import Path
if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')

def load(path):
 with open(path, encoding='utf-8-sig', newline='') as f:
  reader=csv.DictReader(f)
  fields=reader.fieldnames or []
  if not fields or len(fields)!=len(set(fields)): raise ValueError('CSV başlıkları boş veya mükerrer.')
  rows=list(reader)
  if any(None in r or any(v is None for v in r.values()) for r in rows): raise ValueError('CSV satır uzunluğu başlıkla uyuşmuyor.')
  return fields, rows

def number(value):
 try:
  v=float(value)
  return v if math.isfinite(v) else None
 except (ValueError,TypeError): return None

def profile(fields,rows):
 columns=[]
 for field in fields:
  values=[r[field] for r in rows if r[field].strip()]
  nums=[number(v) for v in values]
  numeric=bool(values) and all(v is not None for v in nums)
  col={'name':field,'type':'number' if numeric else ('text' if values else 'empty'),'missing':len(rows)-len(values),'distinct':len(set(values))}
  if numeric: col.update(min=min(nums),max=max(nums),sum=sum(nums),mean=sum(nums)/len(nums))
  columns.append(col)
 return {'row_count':len(rows),'column_count':len(fields),'columns':columns}

def require_fields(fields,selected):
 missing=set(selected)-set(fields)
 if missing: raise ValueError('Eksik alan: '+', '.join(sorted(missing)))

def execute(engine,input_path,config=None,compare=None):
 cfg=config or {}
 if engine=='sql':
  root=Path(input_path)
  files=sorted(root.rglob('*.sql')) if root.is_dir() else [root]
  return {'files':[{'file':p.name,'tables':sorted(set(re.findall(r'\b(?:from|join)\s+([\w.]+)',p.read_text(encoding='utf-8'),re.I)))} for p in files], 'limitation':'Regex envanteri; dinamik SQL ve CTE lineage çözülmez.'}
 fields,rows=load(input_path)
 stats=profile(fields,rows)
 if engine in ('profile','dictionary'): return stats
 if engine=='report':
  group=cfg.get('group_by')
  if group: require_fields(fields,[group])
  groups=defaultdict(list)
  for row in rows: groups[row[group] if group else 'Tümü'].append(row)
  return {**stats,'groups':[{'name':key,**profile(fields,value)} for key,value in sorted(groups.items())]}
 if engine=='quality':
  rules=cfg.get('rules',{})
  require_fields(fields,list(rules))
  issues=[]
  for field,rule in rules.items():
   counts=Counter(r[field] for r in rows)
   for i,row in enumerate(rows,2):
    value=row[field]; failures=[]; n=number(value)
    if rule.get('required') and not value.strip(): failures.append('required')
    if value.strip() and rule.get('type')=='number' and n is None: failures.append('type')
    if rule.get('unique') and counts[value]>1: failures.append('unique')
    if value.strip() and ('min' in rule or 'max' in rule):
     if n is None: failures.append('range_type')
     elif n<rule.get('min',float('-inf')) or n>rule.get('max',float('inf')): failures.append('range')
    if failures: issues.append({'row':i,'field':field,'rules':failures})
  return {**stats,'rule_count':len(rules),'issues':issues,'passed':not issues,'note':'Özel kural tanımlanmadı; yalnızca profil çıkarıldı.' if not rules else 'Yalnızca tanımlanan kurallar kontrol edildi.'}
 if engine=='dedupe':
  keys=cfg.get('keys') or fields; require_fields(fields,keys)
  groups=defaultdict(list)
  for i,row in enumerate(rows,2): groups[tuple(row[k] for k in keys)].append(i)
  return {'keys':keys,'duplicate_groups':[{'key':list(k),'rows':v} for k,v in groups.items() if len(v)>1]}
 if engine=='mask':
  selected=cfg.get('mask_fields',[])
  if not selected: raise ValueError('mask_fields içinde maskelenecek alanları belirtin.')
  require_fields(fields,selected)
  return {'fields':fields,'rows':[{k:('[MASKED]' if k in selected else v) for k,v in row.items()} for row in rows], 'masked_fields':selected}
 if engine in ('schema','reconcile'):
  if not compare: raise ValueError('--compare ikinci CSV dosyası gereklidir.')
  fields2,rows2=load(compare)
  if engine=='schema':
   types={c['name']:c['type'] for c in stats['columns']}; types2={c['name']:c['type'] for c in profile(fields2,rows2)['columns']}
   return {'added':sorted(set(fields2)-set(fields)),'removed':sorted(set(fields)-set(fields2)),'type_changes':[{'field':k,'before':types[k],'after':types2[k]} for k in types.keys()&types2.keys() if types[k]!=types2[k]],'note':'Türler dosyadaki değerlerden çıkarılır; fiziksel veritabanı şeması değildir.'}
  keys=cfg.get('keys',[])
  if not keys: raise ValueError('Mutabakat için config.keys gereklidir.')
  require_fields(fields,keys); require_fields(fields2,keys)
  def index(items):
   result={}
   for r in items:
    key=tuple(r[k] for k in keys)
    if any(not v.strip() for v in key): raise ValueError('Mutabakat anahtarı boş olamaz.')
    if key in result: raise ValueError('Mutabakat anahtarı benzersiz olmalıdır.')
    result[key]=r
   return result
  left,right=index(rows),index(rows2)
  common=sorted(set(fields)&set(fields2)-set(keys))
  return {'only_source':[list(k) for k in sorted(left.keys()-right.keys())],'only_target':[list(k) for k in sorted(right.keys()-left.keys())],'changed':[{'key':list(k),'fields':{f:{'source':left[k][f],'target':right[k][f]} for f in common if left[k][f]!=right[k][f]}} for k in sorted(left.keys()&right.keys()) if any(left[k][f]!=right[k][f] for f in common)],'source_rows':len(rows),'target_rows':len(rows2),'schema_added':sorted(set(fields2)-set(fields)),'schema_removed':sorted(set(fields)-set(fields2))}
 raise ValueError('Bilinmeyen engine: '+engine)

def save(result,engine,out):
 out=Path(out); out.mkdir(parents=True,exist_ok=True)
 (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 if engine=='mask':
  with (out/'masked.csv').open('w',encoding='utf-8',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=result['fields']); writer.writeheader(); writer.writerows(result['rows'])
 if engine=='dictionary':
  text='| Alan | Tür | Boş | Benzersiz |\n|---|---|---|---|\n'+'\n'.join('| '+' | '.join(str(c[k]).replace('|','\\|') for k in ('name','type','missing','distinct'))+' |' for c in result['columns'])
  (out/'dictionary.md').write_text(text,encoding='utf-8')
 body=html.escape(json.dumps(result,ensure_ascii=False,indent=2))
 columns=result.get('columns',[])
 summary=f'<section><article>Kayıt<strong>{result.get("row_count","—")}</strong></article><article>Alan<strong>{result.get("column_count","—")}</strong></article><article>Kural ihlali<strong>{len(result["issues"]) if "issues" in result else "—"}</strong></article></section>'
 table='<h2>Alan profili</h2><table><tr><th>Alan</th><th>Tür</th><th>Boş</th><th>Benzersiz</th><th>Toplam</th><th>Ortalama</th></tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(c.get(k,'—')))+'</td>' for k in ('name','type','missing','distinct','sum','mean'))+'</tr>' for c in columns)+'</table>' if columns else ''
 groups=''
 for group in result.get('groups',[]):
  groups+='<h3>'+html.escape(str(group['name']))+' · '+str(group['row_count'])+' kayıt</h3><ul>'+''.join('<li>'+html.escape(c['name'])+': '+str(c['sum'])+'</li>' for c in group['columns'] if c['type']=='number')+'</ul>'
 (out/'report.html').write_text('<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Veri danışmanlığı raporu</title><style>body{font:14px system-ui;max-width:1100px;margin:40px auto;padding:24px;background:#f5f5f2;color:#192a25}h1{font-size:32px}pre{background:white;padding:24px;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere}section{display:flex;gap:15px}article{flex:1;background:#e5ecd7;padding:24px;border-radius:10px}strong{display:block;font-size:30px;margin-top:8px}table{width:100%;background:white;border-collapse:collapse}td,th{text-align:left;padding:14px;border-bottom:1px solid #eee}@media print{details{display:none}}</style><small>DATAWORK / DANIŞMANLIK TESLİMİ</small><h1>Veri danışmanlığı raporu</h1><p>'+html.escape(engine)+'</p>'+summary+table+groups+'<details><summary>Hesaplanan sonucun tamamı</summary><pre>'+body+'</pre></details></html>',encoding='utf-8')

def main():
 p=argparse.ArgumentParser(); p.add_argument('--input',default='sample.csv'); p.add_argument('--compare'); p.add_argument('--config',default='config.json'); p.add_argument('--output',default='output'); p.add_argument('--engine')
 args=p.parse_args(); cfg=json.loads(Path(args.config).read_text(encoding='utf-8')); engine=args.engine or cfg['engine']
 try: result=execute(engine,args.input,cfg,args.compare); save(result,engine,args.output)
 except (ValueError,OSError) as e: p.exit(1,str(e)+'\n')
 print('Tamamlandı: '+str(Path(args.output).resolve()))
if __name__=='__main__': main()
