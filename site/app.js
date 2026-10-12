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
 const list=filtered().sort((a,b)=>(a.status==='planned')-(b.status==='planned')),pages=Math.max(1,Math.ceil(list.length/pageSize));page=Math.min(page,pages);
 const d=data.find(x=>x.id===domain);$('#sectionTitle').textContent=savedOnly?'Kaydedilen görevler':($('#toolFilter').value|| (d?d.name:'Arama sonuçları'));$('#sectionDescription').textContent=d?d.description:'Veri alanından başlayabilir veya doğrudan bir görev arayabilirsin.';
 $('#resultCount').textContent=`${list.length.toLocaleString('tr')} görev`;$('#savedCount').textContent=saved.size;$('#libraryNav').classList.toggle('active',!savedOnly);$('#savedNav').classList.toggle('active',savedOnly);
 $('#domainFilter').value=domain;
 document.querySelectorAll('[data-kind]').forEach(b=>{b.classList.toggle('selected',b.dataset.kind===kind);b.setAttribute('aria-pressed',String(b.dataset.kind===kind))});
 $('#breadcrumbs').innerHTML=(d?esc(d.name):'Tüm veri alanları')+($('#toolFilter').value?' / '+esc($('#toolFilter').value):'')+($('#productFilter').value?' / <span>'+esc($('#productFilter').value)+'</span>':'');
 $('#cards').innerHTML=list.slice((page-1)*pageSize,page*pageSize).map(t=>`<article class="task-row"><div class="task-info"><h3><button data-open="${t.id}">${esc(t.title)}</button></h3><p>${esc(t.description)}</p></div><div class="task-product"><strong>${esc(t.tool.name)}</strong><span>${esc(t.product.name)}</span><small>${esc(t.domain.name)}</small></div><div class="task-type"><span class="pill ${t.kind==='agent'?'agent':t.engine==='connector'?'api':'code'}">${t.kind==='agent'?'AI agent':t.engine==='connector'?'API paketi':'Kod bloğu'}</span><small>${t.status==='planned'?'Geliştirme planında':'Sözleşme testli · canlı test yok'}</small></div><div class="row-actions"><button class="favorite ${saved.has(t.id)?'saved':''}" data-save="${t.id}" aria-label="${saved.has(t.id)?'Kaydı kaldır':'Görevi kaydet'}" aria-pressed="${saved.has(t.id)}">${saved.has(t.id)?'★':'☆'}</button><button class="card-more" data-open="${t.id}">Ayrıntılar</button>${t.download?`<a class="download" href="${t.download}" download aria-label="${esc(t.title)} paketini indir">Paketi indir ↓</a>`:`<span class="planned-label">Henüz hazır değil</span>`}</div></article>`).join('')||'<div class="empty"><strong>Bu filtrelerde görev bulunamadı.</strong>Aramayı değiştir veya filtreleri temizle.</div>';
 document.querySelectorAll('[data-save]').forEach(b=>b.onclick=()=>{saved.has(b.dataset.save)?saved.delete(b.dataset.save):saved.add(b.dataset.save);try{localStorage.setItem('datawork-saved',JSON.stringify([...saved]))}catch{}render()});
 document.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>detail(b.dataset.open));
 $('#pageInfo').textContent=`${page} / ${pages}`;$('#prev').disabled=page===1;$('#next').disabled=page===pages;
 history.replaceState(null,'','#'+new URLSearchParams({...(domain?{domain}:{}),...(kind!=='all'?{kind}:{}),...($('#toolFilter').value?{tool:$('#toolFilter').value}:{}),...($('#productFilter').value?{product:$('#productFilter').value}:{}),...($('#executionFilter').value?{execution:$('#executionFilter').value}:{}),...($('#search').value?{q:$('#search').value}:{})}));
}
function detail(id){
 const t=tasks.find(t=>t.id===id);if(!t)return;const implemented=Boolean(t.download);
 $('#detailEyebrow').textContent=implemented?(t.kind==='agent'?'PLATFORM API + AGENT':'PLATFORM API · KOD BLOĞU'):'GELİŞTİRME PLANINDA';
 $('#detailContent').innerHTML=`<div class="dialog-path">${esc(t.domain.name)} → ${esc(t.tool.name)} → ${esc(t.product.name)}</div><h2>${esc(t.title)}</h2><p>${esc(t.description)}</p><div class="detail-grid"><div><small>TESLİM</small>${esc(t.output)}</div><div><small>ÇALIŞMA BİÇİMİ</small>${esc(t.execution)}</div></div><h3>Bağlantı ve gereksinimler</h3><p>${esc(t.requirements||t.input)}</p><h3>Neden ${t.kind==='agent'?'agent':'kod bloğu'}?</h3><p>${esc(t.reason)}</p>${implemented?`<p class="note">API çağrı zinciri yerel HTTP sözleşme testlerinden geçti. Gerçek müşteri hesabında ve dosyanın hedef uygulamada açılmasıyla test edilmedi.</p><h3>Platformda ne yapar?</h3><p>${esc(t.effects)}</p><p>Erişim bilgilerini paketteki .env dosyasına gir. Anahtarlar bu siteye yüklenmez. ZIP, uygulamana bağlanan çalıştırıcıdır; kişisel rapor dosyan çalıştırdıktan sonra üretilir.</p><pre>python native.py --plan
python native.py --execute --output output/run-001</pre><div class="dialog-actions"><a class="primary" href="${t.download}" download>Çalıştırıcı paketini indir ↓</a><button class="outline" id="copyCommand">Komutu kopyala</button></div><p class="hash">SHA-256 · ${t.sha256}<br>${(t.bytes/1024).toFixed(1)} KB · README, config ve kaynak kod dahil</p><details><summary>API kaynakları</summary>${t.sources.map(url=>`<p><a href="${esc(url)}" target="_blank" rel="noopener">${esc(new URL(url).hostname)} ↗</a></p>`).join('')}</details>`:`<p class="note">Bu görev için çalışır entegrasyon henüz yok. API/SDK yolu, ürün sürümü, yetkiler ve yerel çıktı biçimi doğrulanmadan indirilebilir paket yayımlanmayacak.</p><p>Planlanan bağlantı: ${esc(t.interface_target)}. Üstteki teslim biçimi bir tasarım hedefidir.</p>`}`;
 if($('#copyCommand'))$('#copyCommand').onclick=async()=>{try{await navigator.clipboard.writeText(t.command);notify('Komut kopyalandı')}catch{notify('Komutu seçip kopyalayabilirsin')}};
 show('detail');
}
$('#domainFilter').onchange=()=>selectDomain($('#domainFilter').value);
$('#search').oninput=()=>{domain='';kind='all';savedOnly=false;$('#toolFilter').value='';$('#productFilter').value='';$('#executionFilter').value='';options();view=$('#search').value.trim()?'results':'home';page=1;render()};document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{kind=b.dataset.kind;page=1;render()});
$('#toolFilter').onchange=()=>{$('#productFilter').value='';productOptions();page=1;render()};$('#productFilter').onchange=$('#executionFilter').onchange=()=>{page=1;render()};
$('#prev').onclick=()=>{page--;render();$('#catalogSection').scrollIntoView({behavior:'smooth'})};$('#next').onclick=()=>{page++;render();$('#catalogSection').scrollIntoView({behavior:'smooth'})};
$('#reset').onclick=()=>{view='home';domain='';kind='all';savedOnly=false;page=1;$('#search').value='';$('#toolFilter').value='';$('#productFilter').value='';$('#executionFilter').value='';options();render()};
$('#savedNav').onclick=()=>{view='results';savedOnly=true;domain='';page=1;$('#search').value='';$('#toolFilter').value='';$('#productFilter').value='';$('#executionFilter').value='';kind='all';options();render();$('#catalogSection').scrollIntoView({behavior:'smooth'})};
$('#libraryNav').onclick=()=>{$('#reset').click()};['howNav'].forEach(id=>$('#'+id).onclick=()=>show('how'));
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#search').focus();}});
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
