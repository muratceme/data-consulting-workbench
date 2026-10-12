const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data=[],tasks=[],domain='',kind='all',page=1,savedOnly=false,saved=new Set();const pageSize=9;let view='home',allAreas=false;
try{saved=new Set(JSON.parse(localStorage.getItem('datawork-saved')||'[]'))}catch{}
const initial=new URLSearchParams(location.hash.slice(1)); domain=initial.get('domain')||'';kind=initial.get('kind')||'all';$('#search').value=initial.get('q')||'';
function notify(message){$('#toast').textContent=message;$('#toast').style.display='block';setTimeout(()=>$('#toast').style.display='none',2200)}
function show(id){$('#'+id).showModal()}
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close()}}));
function options(){
 const subset=tasks.filter(t=>!domain||t.domain.id===domain), tools=[...new Set(subset.map(t=>t.tool.name))].sort((a,b)=>a.localeCompare(b));
 const selected=$('#toolFilter').value;$('#toolFilter').innerHTML='<option value="">Tüm yazılımlar</option>'+tools.map(t=>`<option>${esc(t)}</option>`).join('');if(tools.includes(selected))$('#toolFilter').value=selected;
 productOptions();
}
function productOptions(){const tool=$('#toolFilter').value, selected=$('#productFilter').value,products=[...new Set(tasks.filter(t=>(!domain||t.domain.id===domain)&&(!tool||t.tool.name===tool)).map(t=>t.product.name))].sort((a,b)=>a.localeCompare(b));$('#productFilter').innerHTML='<option value="">Tüm ürünler</option>'+products.map(p=>`<option>${esc(p)}</option>`).join('');if(products.includes(selected))$('#productFilter').value=selected;}
function selectDomain(id){view='results';domain=id;page=1;savedOnly=false;$('#toolFilter').value='';$('#productFilter').value='';options();render()}
function filtered(){const q=$('#search').value.toLocaleLowerCase('tr').trim(),tool=$('#toolFilter').value,product=$('#productFilter').value,execution=$('#executionFilter').value;return tasks.filter(t=>(!domain||t.domain.id===domain)&&(kind==='all'||t.kind===kind)&&(!tool||t.tool.name===tool)&&(!product||t.product.name===product)&&(!execution||t.execution===execution)&&(!savedOnly||saved.has(t.id))&&(!q||q.split(/\s+/).every(word=>[t.title,t.description,t.domain.name,t.tool.name,t.product.name].join(' ').toLocaleLowerCase('tr').includes(word))));}
function render(){
 $('#discover').hidden=view!=='home';$('#softwareStep').hidden=view!=='software';$('#catalogSection').hidden=view!=='results';$('#clearSearch').hidden=!$('#search').value;
 const list=filtered(),pages=Math.max(1,Math.ceil(list.length/pageSize));page=Math.min(page,pages);
 const d=data.find(x=>x.id===domain);$('#sectionTitle').textContent=savedOnly?'Kaydedilen görevler':($('#toolFilter').value|| (d?d.name:'Arama sonuçları'));$('#sectionDescription').textContent=d?d.description:'Veri alanından başlayabilir veya doğrudan bir görev arayabilirsin.';
 $('#resultCount').textContent=`${list.length.toLocaleString('tr')} görev`;$('#savedCount').textContent=saved.size;$('#libraryNav').classList.toggle('active',!savedOnly);$('#savedNav').classList.toggle('active',savedOnly);
 $('#domainFilter').value=domain;
 document.querySelectorAll('[data-kind]').forEach(b=>{b.classList.toggle('selected',b.dataset.kind===kind);b.setAttribute('aria-pressed',String(b.dataset.kind===kind))});
 $('#breadcrumbs').innerHTML=(d?esc(d.name):'Tüm veri alanları')+($('#toolFilter').value?' / '+esc($('#toolFilter').value):'')+($('#productFilter').value?' / <span>'+esc($('#productFilter').value)+'</span>':'');
 $('#cards').innerHTML=list.slice((page-1)*pageSize,page*pageSize).map(t=>`<article class="task-row"><div class="task-info"><h3><button data-open="${t.id}">${esc(t.title)}</button></h3><p>${esc(t.description)}</p></div><div class="task-product"><strong>${esc(t.tool.name)}</strong><span>${esc(t.product.name)}</span><small>${esc(t.domain.name)}</small></div><div class="task-type"><span class="pill ${t.kind==='agent'?'agent':t.engine==='connector'?'api':'code'}">${t.kind==='agent'?'AI agent':t.engine==='connector'?'API paketi':'Kod bloğu'}</span><small>${esc(t.execution)}</small></div><div class="row-actions"><button class="favorite ${saved.has(t.id)?'saved':''}" data-save="${t.id}" aria-label="${saved.has(t.id)?'Kaydı kaldır':'Görevi kaydet'}" aria-pressed="${saved.has(t.id)}">${saved.has(t.id)?'★':'☆'}</button><button class="card-more" data-open="${t.id}">Ayrıntılar</button><a class="download" href="${t.download}" download aria-label="${esc(t.title)} paketini indir">İndir ↓</a></div></article>`).join('')||'<div class="empty"><strong>Bu filtrelerde görev bulunamadı.</strong>Aramayı değiştir veya filtreleri temizle.</div>';
 document.querySelectorAll('[data-save]').forEach(b=>b.onclick=()=>{saved.has(b.dataset.save)?saved.delete(b.dataset.save):saved.add(b.dataset.save);try{localStorage.setItem('datawork-saved',JSON.stringify([...saved]))}catch{}render()});
 document.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>detail(b.dataset.open));
 $('#pageInfo').textContent=`${page} / ${pages}`;$('#prev').disabled=page===1;$('#next').disabled=page===pages;
 history.replaceState(null,'','#'+new URLSearchParams({...(domain?{domain}:{}),...(kind!=='all'?{kind}:{}),...($('#toolFilter').value?{tool:$('#toolFilter').value}:{}),...($('#productFilter').value?{product:$('#productFilter').value}:{}),...($('#executionFilter').value?{execution:$('#executionFilter').value}:{}),...($('#search').value?{q:$('#search').value}:{})}));
}
function detail(id){
 const t=tasks.find(t=>t.id===id);if(!t)return;
 $('#detailEyebrow').textContent=t.kind==='agent'?'AI AGENT PAKETİ':t.engine==='connector'?'SALT OKUNUR API PAKETİ':'HAZIR KOD BLOĞU';
 $('#detailContent').innerHTML=`<div class="dialog-path">${esc(t.domain.name)} → ${esc(t.tool.name)} → ${esc(t.product.name)}</div><h2>${esc(t.title)}</h2><p>${esc(t.description)}</p><div class="detail-grid"><div><small>GİRDİ</small>${esc(t.input)}</div><div><small>ÇALIŞMA BİÇİMİ</small>${esc(t.execution)}</div><div><small>GEREKSİNİM</small>Python 3.10+ ${t.kind==='agent'?'· model / API erişimi':t.engine==='connector'?'· access token':''}</div><div><small>TESLİM</small>${t.kind==='agent'?'Markdown danışmanlık taslağı':t.engine==='mask'?'Maskelenmiş CSV + JSON':t.engine==='dictionary'?'Markdown + JSON + HTML':'JSON + HTML rapor'}</div></div><h3>Neden ${t.kind==='agent'?'agent':'kod bloğu'}?</h3><p>${esc(t.reason)}</p><p class="note">${t.kind==='agent'?'OpenAI uyumlu API, Anthropic ve Ollama desteklenir. .env içinde sağlayıcı ve model seçilir. Önce dry-run isteğini incele. Model yalnızca sağlanan bağlam ve kanıtları değerlendirir; üretimde işlem yapmaz. Canlı model yanıtı henüz test edilmedi.':t.engine==='connector'?'Bu paket platform API’sinden GET istekleriyle envanter alır. Token ve ortam kimlikleri gerekir. Mock testleri geçti; canlı tenant testi yapılmadı.':'Bu ürün için CSV dışa aktarımı üzerinden çalışan ortak bir veri motorudur. Ürüne doğrudan bağlanmaz. config.json içindeki alan isimlerini ve kuralları kendi verine göre düzenle.'}</p><pre>${esc(t.command)}</pre><div class="dialog-actions"><a class="primary" href="${t.download}" download>ZIP paketini indir <span>↓</span></a><button class="outline" id="copyCommand">Komutu kopyala</button>${!['agent','connector','sql','schema','reconcile','mask'].includes(t.engine)?'<button class="text-btn" id="detailDemo">Yerelde dene →</button>':''}</div><p class="hash">SHA-256 · ${t.sha256}<br>Paket · ${(t.bytes/1024).toFixed(1)} KB · Kaynak ve README dahil</p>`;
 $('#copyCommand').onclick=async()=>{try{await navigator.clipboard.writeText(t.command);notify('Komut kopyalandı')}catch{notify('Komutu metin alanından seçip kopyalayabilirsin')}};
 if($('#detailDemo'))$('#detailDemo').onclick=()=>{$('#detail').close();$('#demoEngine').value=t.engine;show('demo')};show('detail');
}
$('#domainFilter').onchange=()=>selectDomain($('#domainFilter').value);
$('#search').oninput=()=>{domain='';kind='all';savedOnly=false;$('#toolFilter').value='';$('#productFilter').value='';$('#executionFilter').value='';options();view=$('#search').value.trim()?'results':'home';page=1;render()};document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{kind=b.dataset.kind;page=1;render()});
$('#toolFilter').onchange=()=>{$('#productFilter').value='';productOptions();page=1;render()};$('#productFilter').onchange=$('#executionFilter').onchange=()=>{page=1;render()};
$('#prev').onclick=()=>{page--;render();$('#catalogSection').scrollIntoView({behavior:'smooth'})};$('#next').onclick=()=>{page++;render();$('#catalogSection').scrollIntoView({behavior:'smooth'})};
$('#reset').onclick=()=>{view='home';domain='';kind='all';savedOnly=false;page=1;$('#search').value='';$('#toolFilter').value='';$('#productFilter').value='';$('#executionFilter').value='';options();render()};
$('#savedNav').onclick=()=>{view='results';savedOnly=true;domain='';page=1;$('#search').value='';$('#toolFilter').value='';$('#productFilter').value='';$('#executionFilter').value='';kind='all';options();render();$('#catalogSection').scrollIntoView({behavior:'smooth'})};
$('#libraryNav').onclick=()=>{$('#reset').click()};['howNav'].forEach(id=>$('#'+id).onclick=()=>show('how'));$('#demoHero').onclick=()=>show('demo');
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#search').focus();}});
$('#csvInput').onchange=()=>{$('#runDemo').textContent=$('#csvInput').files.length?'Seçilen CSV’de çalıştır →':'Örnek veride çalıştır →'};
$('#runDemo').onclick=async()=>{
 $('#runDemo').disabled=true;$('#demoStatus').textContent='Yerel motor çalışıyor…';$('#demoOutput').innerHTML='';
 try{
  const file=$('#csvInput').files[0];if(file&&file.size>2*1024*1024)throw Error('En fazla 2 MB CSV seçin.');
  const res=await fetch('/api/demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({engine:$('#demoEngine').value,...(file?{csv:await file.text()}: {})})});const result=await res.json();if(!res.ok)throw Error(result.error||'İşlem başarısız.');
  $('#demoStatus').textContent='Tamamlandı · Dosya yerelde işlendi, geçici girdi silindi.';
  $('#demoOutput').innerHTML=`<div class="demo-metrics"><div>Kayıt<strong>${result.row_count??'—'}</strong></div><div>Alan<strong>${result.column_count??'—'}</strong></div><div>Kural ihlali<strong>${result.issues?.length??'—'}</strong></div></div>`+(result.columns?`<table><thead><tr><th>Alan</th><th>Tür</th><th>Boş</th><th>Benzersiz</th></tr></thead><tbody>${result.columns.map(c=>`<tr><td>${esc(c.name)}</td><td>${c.type}</td><td>${c.missing}</td><td>${c.distinct}</td></tr>`).join('')}</tbody></table>`:'')+`<details><summary>JSON sonucunu görüntüle</summary><pre>${esc(JSON.stringify(result,null,2))}</pre></details><button class="outline" id="downloadResult">JSON raporunu indir ↓</button>`;
  $('#downloadResult').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='datawork-result.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
 }catch(e){$('#demoStatus').textContent=e.message}finally{$('#runDemo').disabled=false}
};
try{
 const response=await fetch('/catalog.json');if(!response.ok)throw Error('Katalog yüklenemedi');data=await response.json();
 tasks=data.flatMap(d=>d.tools.flatMap(tool=>tool.products.flatMap(product=>product.tasks.map(t=>({...t,domain:d,tool,product})))));
 if(!data.some(d=>d.id===domain))domain='';if(!['all','code','agent'].includes(kind))kind='all';
 $('#domainFilter').innerHTML='<option value="">Tüm veri alanları</option>'+data.map(d=>`<option value="${d.id}">${esc(d.name)}</option>`).join('');
 buildDiscovery();
 options();if(initial.get('tool'))$('#toolFilter').value=initial.get('tool');productOptions();if(initial.get('product'))$('#productFilter').value=initial.get('product');if(initial.get('execution'))$('#executionFilter').value=initial.get('execution');view=initial.size?'results':'home';render();
}catch(e){$('#catalogSection').hidden=false;$('#cards').innerHTML='<div class="empty">Katalog yüklenemedi. npm run build komutunu çalıştırıp sayfayı yenileyin.</div>';$('#resultCount').textContent='Yükleme hatası';}

function pickTool(name,area='',product=''){
 domain=area;kind='all';savedOnly=false;page=1;$('#search').value='';$('#executionFilter').value='';$('#productFilter').value='';options();$('#toolFilter').value=name;productOptions();$('#productFilter').value=product;view='results';render();$('#sectionTitle').focus({preventScroll:true});$('#catalogSection').scrollIntoView({behavior:'smooth'});
}
function areaStep(id){
 domain=id;const area=data.find(d=>d.id===id);view='software';$('#search').value='';render();$('#softwareTitle').textContent=area.name;
 $('#softwareTiles').innerHTML=area.tools.map(t=>`<button class="software-tile" data-tool="${esc(t.name)}"><span class="software-monogram">${esc(t.name.slice(0,2))}</span><strong>${esc(t.name)}</strong><span>Görevleri gör ›</span></button>`).join('');
 document.querySelectorAll('[data-tool]').forEach(b=>b.onclick=()=>pickTool(b.dataset.tool,id));$('#softwareTitle').focus({preventScroll:true});$('#softwareStep').scrollIntoView({behavior:'smooth'});
}
function buildDiscovery(){
 const popular=[['Qlik','Qlik','Q',''],['Microsoft Power BI','Power BI','BI',''],['Tableau','Tableau','T',''],['SAP','SAP','S',''],['Logo','Logo','L',''],['Google','BigQuery','BQ','BigQuery']];
 $('#popular').innerHTML=popular.map(([tool,label,mark,product],i)=>`<button class="popular-tool color-${i}" data-popular="${esc(tool)}" data-product="${product}"><span>${mark}</span><strong>${label}</strong></button>`).join('');
 document.querySelectorAll('[data-popular]').forEach(b=>b.onclick=()=>pickTool(b.dataset.popular,'',b.dataset.product));renderAreas();
}
function renderAreas(){
 const featured=['bi','erp','warehouse','integration','quality','ai'];const descriptions={bi:'Raporlar ve panolar',erp:'İş süreçleri ve kurumsal veri',warehouse:'Verini tek yerde topla',integration:'Sistemlerini birbirine bağla',quality:'Temiz, güvenilir veri',ai:'Verinle çalışan yapay zekâ'};
 const areas=allAreas?data:featured.map(id=>data.find(d=>d.id===id));
 $('#areaTiles').innerHTML=areas.map(d=>`<button class="area-tile" data-area="${d.id}"><span class="area-icon" aria-hidden="true">${d.icon}</span><strong>${esc(d.name)}</strong><span>${esc(descriptions[d.id]||d.description)}</span><b aria-hidden="true">↗</b></button>`).join('');document.querySelectorAll('[data-area]').forEach(b=>b.onclick=()=>areaStep(b.dataset.area));
 $('#allAreas').textContent=allAreas?'Daha az göster ↑':'Tüm 23 veri alanını gör ↓';$('#allAreas').setAttribute('aria-expanded',String(allAreas));
}
$('#allAreas').onclick=()=>{allAreas=!allAreas;renderAreas()};
$('#backHome').onclick=$('#backDiscover').onclick=()=>{$('#reset').click();$('#search').focus({preventScroll:true})};
$('#clearSearch').onclick=()=>{$('#reset').click();$('#search').focus()};
