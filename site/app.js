const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data=[],tasks=[],domain='',kind='all',page=1,savedOnly=false,saved=new Set();const pageSize=12;
try{saved=new Set(JSON.parse(localStorage.getItem('datawork-saved')||'[]'))}catch{}
const initial=new URLSearchParams(location.hash.slice(1)); domain=initial.get('domain')||'';kind=initial.get('kind')||'all';$('#search').value=initial.get('q')||'';
function notify(message){$('#toast').textContent=message;$('#toast').style.display='block';setTimeout(()=>$('#toast').style.display='none',2200)}
function show(id){setMenu(false);$('#'+id).showModal()}
function setMenu(open){
 $('.sidebar').classList.toggle('open',open);
 document.body.classList.toggle('menu-open',open);
 $('.main').inert=open;
 $('#menuBackdrop').hidden=!open;
 $('#mobileMenu').setAttribute('aria-expanded',String(open));
 if(open)$('#closeMenu').focus();
}
$('#closeMenu').onclick=$('#menuBackdrop').onclick=()=>{setMenu(false);$('#mobileMenu').focus()};
document.addEventListener('keydown',e=>{
 if(!$('.sidebar').classList.contains('open'))return;
 if(e.key==='Escape'){setMenu(false);$('#mobileMenu').focus()}
 if(e.key==='Tab'){
  const items=[...$('.sidebar').querySelectorAll('a,button')].filter(el=>el.getClientRects().length);
  const first=items[0],last=items[items.length-1];
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}
  if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
 }
});
window.matchMedia('(min-width: 801px)').addEventListener('change',e=>{if(e.matches)setMenu(false)});
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close()}}));
function options(){
 const subset=tasks.filter(t=>!domain||t.domain.id===domain), tools=[...new Set(subset.map(t=>t.tool.name))].sort((a,b)=>a.localeCompare(b));
 const selected=$('#toolFilter').value;$('#toolFilter').innerHTML='<option value="">Tüm yazılımlar</option>'+tools.map(t=>`<option>${esc(t)}</option>`).join('');if(tools.includes(selected))$('#toolFilter').value=selected;
 productOptions();
}
function productOptions(){const tool=$('#toolFilter').value, selected=$('#productFilter').value,products=[...new Set(tasks.filter(t=>(!domain||t.domain.id===domain)&&(!tool||t.tool.name===tool)).map(t=>t.product.name))].sort((a,b)=>a.localeCompare(b));$('#productFilter').innerHTML='<option value="">Tüm ürünler</option>'+products.map(p=>`<option>${esc(p)}</option>`).join('');if(products.includes(selected))$('#productFilter').value=selected;}
function selectDomain(id){domain=id;page=1;savedOnly=false;$('#toolFilter').value='';$('#productFilter').value='';options();render();setMenu(false)}
function filtered(){const q=$('#search').value.toLocaleLowerCase('tr').trim(),tool=$('#toolFilter').value,product=$('#productFilter').value,execution=$('#executionFilter').value;return tasks.filter(t=>(!domain||t.domain.id===domain)&&(kind==='all'||t.kind===kind)&&(!tool||t.tool.name===tool)&&(!product||t.product.name===product)&&(!execution||t.execution===execution)&&(!savedOnly||saved.has(t.id))&&(!q||[t.title,t.description,t.domain.name,t.tool.name,t.product.name].join(' ').toLocaleLowerCase('tr').includes(q)));}
function render(){
 const list=filtered(),pages=Math.max(1,Math.ceil(list.length/pageSize));page=Math.min(page,pages);
 const d=data.find(x=>x.id===domain);$('#sectionTitle').textContent=savedOnly?'Kaydedilen görevler':(d?d.name:'Otomasyon kütüphanesi');$('#sectionDescription').textContent=d?d.description:'Veri alanından başlayabilir veya doğrudan bir görev arayabilirsin.';
 $('#resultCount').textContent=`${list.length.toLocaleString('tr')} görev`;$('#savedCount').textContent=saved.size;$('#libraryNav').classList.toggle('active',!savedOnly);$('#savedNav').classList.toggle('active',savedOnly);
 $('#domains').innerHTML=`<button class="domain-btn ${domain?'':'selected'}" data-domain=""><span>▦</span>Tüm veri alanları<small>${data.length}</small></button>`+data.map(d=>`<button class="domain-btn ${domain===d.id?'selected':''}" data-domain="${d.id}"><span>${d.icon}</span>${esc(d.name)}</button>`).join('');
 document.querySelectorAll('[data-domain]').forEach(b=>b.onclick=()=>selectDomain(b.dataset.domain));
 document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('selected',b.dataset.kind===kind));
 $('#breadcrumbs').innerHTML=(d?esc(d.name):'Tüm veri alanları')+($('#toolFilter').value?' / '+esc($('#toolFilter').value):'')+($('#productFilter').value?' / <span>'+esc($('#productFilter').value)+'</span>':'');
 $('#cards').innerHTML=list.slice((page-1)*pageSize,page*pageSize).map(t=>`<article class="card"><div class="card-top"><span class="tool-logo">${esc(t.tool.name==='Qlik'?'Q':t.tool.name==='Microsoft Power BI'?'▥':t.tool.name.slice(0,2).toUpperCase())}</span><button class="favorite ${saved.has(t.id)?'saved':''}" data-save="${t.id}" aria-label="${saved.has(t.id)?'Kaydı kaldır':'Görevi kaydet'}" aria-pressed="${saved.has(t.id)}">${saved.has(t.id)?'★':'☆'}</button></div><div class="card-path">${esc(t.domain.name)} / ${esc(t.product.name)}</div><h3>${esc(t.title)}</h3><p>${esc(t.description)}</p><div class="card-engine">${esc(t.execution)} · ${t.kind==='agent'?'İnsan incelemesi gerekli':t.engine==='connector'?'Canlı hesap testi bekliyor':'Test edilmiş ortak motor'}</div><div class="card-bottom"><span class="pill ${t.kind==='agent'?'agent':t.engine==='connector'?'api':'code'}">${t.kind==='agent'?'✳ AI agent':t.engine==='connector'?'↗ API paketi':'&lt;/&gt; Kod bloğu'}</span><button class="card-open" data-open="${t.id}">Paketi incele <span>↗</span></button></div></article>`).join('')||'<div class="empty"><strong>Bu filtrelerde görev bulunamadı.</strong>Aramayı değiştir veya filtreleri temizle.</div>';
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
$('#search').oninput=()=>{page=1;render()};document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{kind=b.dataset.kind;page=1;render()});
$('#toolFilter').onchange=()=>{$('#productFilter').value='';productOptions();page=1;render()};$('#productFilter').onchange=$('#executionFilter').onchange=()=>{page=1;render()};
$('#prev').onclick=()=>{page--;render();$('#catalogSection').scrollIntoView({behavior:'smooth'})};$('#next').onclick=()=>{page++;render();$('#catalogSection').scrollIntoView({behavior:'smooth'})};
$('#reset').onclick=()=>{domain='';kind='all';savedOnly=false;page=1;$('#search').value='';$('#toolFilter').value='';$('#productFilter').value='';$('#executionFilter').value='';options();render()};
$('#savedNav').onclick=()=>{savedOnly=true;domain='';page=1;$('#search').value='';$('#toolFilter').value='';$('#productFilter').value='';$('#executionFilter').value='';kind='all';options();render();setMenu(false);$('#catalogSection').scrollIntoView({behavior:'smooth'})};
$('#libraryNav').onclick=()=>{$('#reset').click();setMenu(false)};['howNav','howBottom'].forEach(id=>$('#'+id).onclick=()=>show('how'));$('#explore').onclick=()=>$('#catalogSection').scrollIntoView({behavior:'smooth'});$('#demoHero').onclick=()=>show('demo');$('#mobileMenu').onclick=()=>setMenu(!$('.sidebar').classList.contains('open'));
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#search').focus();$('#catalogSection').scrollIntoView({behavior:'smooth'})}});
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
 const vendors=new Set(tasks.map(t=>t.tool.name));$('#totalBadge').textContent=tasks.length;$('#domainCount').textContent=data.length;
 $('#stats').innerHTML=[[data.length,'Veri alanı','◈','uçtan uca ekosistem'],[vendors.size,'Yazılım ailesi','▦','ERP’den GenAI’a'],[tasks.filter(t=>t.kind==='code').length,'Kod bloğu paketi','⌘','9 ortak motor + 3 API'],[tasks.filter(t=>t.kind==='agent').length,'AI agent paketi','✳','3 model bağlantısı']].map(([n,label,icon,note])=>`<div class="stat"><div class="stat-top">${label}<span>${icon}</span></div><strong>${n.toLocaleString('tr')} <small>${note}</small></strong></div>`).join('');
 options();if(initial.get('tool'))$('#toolFilter').value=initial.get('tool');productOptions();if(initial.get('product'))$('#productFilter').value=initial.get('product');if(initial.get('execution'))$('#executionFilter').value=initial.get('execution');render();
}catch(e){$('#cards').innerHTML='<div class="empty">Katalog yüklenemedi. npm run build komutunu çalıştırıp sayfayı yenileyin.</div>';$('#resultCount').textContent='Yükleme hatası';}
