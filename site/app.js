// datawork — catalog of code blocks and AI agents. Ready packages are the list; planned work lives on the coverage map.
document.documentElement.classList.add('js');
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Turkish lowercasing turns I into ı (PBIX → pbıx); fold ı into i so searches and format tags match either way.
const norm = s => String(s ?? '').toLocaleLowerCase('tr').replace(/ı/g, 'i');
const plus = '<span class="plus" aria-hidden="true"></span>';

const ALL = 'Tümü', SAVED = 'Kaydedilenler';
let domains = [], tasks = [], ready = [];
let platform = ALL, query = '', openId = '', openDomain = '', kind = 'all';
const KINDS = [['all', 'Tümü'], ['code', 'Kod blokları'], ['agent', 'AI agent’lar']];
let saved = new Set();
try { saved = new Set(JSON.parse(localStorage.getItem('datawork-saved') || '[]')); } catch {}

// One short platform name per vendor product, used for the tabs.
const BY_TOOL = {'Qlik':'Qlik','Microsoft Power BI':'Power BI','Tableau':'Tableau','SAP':'SAP','Oracle':'Oracle Fusion','Snowflake':'Snowflake','Databricks':'Databricks','Fivetran':'Fivetran','Airbyte':'Airbyte','dbt':'dbt Cloud','Prefect':'Prefect','Apache':'Airflow','GitHub':'GitHub Actions'};
const BY_PRODUCT = {'Fabric Lakehouse':'Fabric','Azure Data Factory':'Data Factory','BigQuery':'BigQuery','Dataform':'Dataform','dbt Core':'dbt','dbt Cloud':'dbt','SQL dosyaları':'SQL','CSV / Excel':'CSV / Excel','Rapor gereksinimleri':'Rapor gereksinimleri','Airflow':'Airflow','Actions':'GitHub Actions','Kafka':'Kafka','Iceberg':'Iceberg','Delta Lake':'Delta Lake','SQL Server':'SQL Server','PostgreSQL':'PostgreSQL','SSIS':'SSIS','Looker':'Looker','Snowflake':'Snowflake'};
// Local packages say what they hand over in one or two words.
const LOCAL_TAG = {'veri-profili':'profil','kalite-kontrol':'kalite','tablo-mutabakati':'mutabakat','sql-soyagaci':'soy ağacı','pbit-model':'model dokümanı','tableau-envanter':'envanter','qlik-soyagaci':'soy ağacı','dbt-denetim':'denetim','sql-yazari':'.sql','dax-yazari':'.dax','veri-sozlugu':'sözlük','kalite-kurali':'rules.json','qlik-powerbi':'.m','kpi-tanimlayici':'KPI','kvkk-maskeleme':'maskeleme','sema-farki':'şema farkı','tarih-tablosu':'takvim','dosya-birlestir':'birleştirme','sql-aciklayici':'açıklama','donem-yorumu':'yorum'};
const platformOf = t => BY_PRODUCT[t.product.name] || BY_TOOL[t.tool.name] || t.product.name;

// The delivered thing, as short as a file extension.
function formatOf(t) {
  if (t.engine === 'local') return LOCAL_TAG[t.local_id] || t.tag || 'rapor';
  const title = norm(t.title), all = norm(t.output + ' ' + t.title);
  if (title.includes('takip')) return 'takip';
  const rules = [[/qvf/, '.qvf'], [/pbix/, '.pbix'], [/twb/, '.twbx'], [/edmx/, '.edmx'], [/ipynb/, '.ipynb'], [/run_results|describe json/, '.json'], [/taslağ|taslak/, 'taslak'], [/view/, 'view'], [/\.sql|standard sql/, '.sql'], [/yenile/, 'yenile'], [/çaliştir/, 'çalışma'], [/tablo/, 'tablo'], [/notebook/, 'notebook'], [/senkron/, 'sync'], [/derleme/, 'derleme'], [/yayimla/, 'yayın']];
  for (const [re, tag] of rules) if (re.test(all)) return tag;
  return 'çalışma';
}

function toast(message) {
  const el = $('#toast'); el.textContent = message; el.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('show'), 2200);
}
function persist() { try { localStorage.setItem('datawork-saved', JSON.stringify([...saved])); } catch {} }
function syncHash() {
  const p = new URLSearchParams();
  if (kind !== 'all') p.set('k', kind);
  if (platform !== ALL) p.set('p', platform);
  if (query) p.set('q', query);
  if (openId) p.set('open', openId);
  history.replaceState(null, '', p.size ? '#' + p : location.pathname);
}
const matches = (t, q) => q.split(/\s+/).every(w => norm([t.title, t.description, t.output, t.domain.name, t.tool.name, t.product.name, t.platform, t.format].join(' ')).includes(w));

const ofKind = t => kind === 'all' || t.kind === kind;
function visible() {
  const q = norm(query.trim());
  return ready.filter(t => ofKind(t) && (platform === ALL || (platform === SAVED ? saved.has(t.id) : t.platform === platform)) && (!q || matches(t, q)));
}

/* ---------- Kind switch: code blocks or agents ---------- */
function renderKinds() {
  const count = k => ready.filter(t => k === 'all' || t.kind === k).length;
  $('#kindSwitch').innerHTML = KINDS.map(([k, label]) => `<button type="button" data-kind="${k}" aria-pressed="${kind === k}">${label}<span>${count(k)}</span></button>`).join('');
  $$('[data-kind]').forEach(b => b.onclick = () => setKind(b.dataset.kind));
}
function setKind(k) {
  kind = k; openId = '';
  if (platform !== ALL && platform !== SAVED && !ready.some(t => ofKind(t) && t.platform === platform)) platform = ALL;
  renderKinds(); renderPlatforms(); renderBands(true);
}

/* ---------- Platform tabs ---------- */
function renderPlatforms() {
  const counts = new Map();
  const pool = ready.filter(ofKind);
  pool.forEach(t => counts.set(t.platform, (counts.get(t.platform) || 0) + 1));
  const names = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b, 'tr'));
  const tab = (name, count, cls = '') => `<button type="button" class="${cls}" data-platform="${esc(name)}" aria-pressed="${platform === name}">${esc(name)}<span>${count}</span></button>`;
  $('#platforms').innerHTML = tab(ALL, pool.length) + names.map(n => tab(n, counts.get(n))).join('') + tab(SAVED, saved.size, 'saved-tab');
  $$('[data-platform]').forEach(b => b.onclick = () => { platform = b.dataset.platform; openId = ''; renderPlatforms(); renderBands(true); });
}

/* ---------- Package bands ---------- */
const win = (title, body, ui = false) => `<figure class="window"${ui ? ' data-ui' : ''}><div class="window-bar"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>${esc(title)}</span></div><pre><code>${esc(body)}</code></pre></figure>`;
const commandsOf = t => t.engine !== 'local' ? 'python native.py --plan\npython native.py --execute --output output/run-001'
  : t.kind === 'agent' ? `copy .env.example .env\npython main.py --dry-run\npython main.py\n${t.command}` : `python main.py\n${t.command}`;
function actions(t) {
  const kb = (t.bytes / 1024).toLocaleString('tr', {maximumFractionDigits: 1});
  const isSaved = saved.has(t.id);
  return `<div class="actions">
        <a class="btn btn-primary" href="${esc(t.download)}" download>Paketi indir · ${kb} KB<span class="btn-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v12m-5-5 5 5 5-5M6 20h12"/></svg></span></a>
        <button type="button" class="btn btn-soft" data-copy="${t.id}">Komutları kopyala</button>
        <button type="button" class="btn btn-soft" data-save="${t.id}" aria-pressed="${isSaved}">${isSaved ? 'Kaydedildi' : 'Kaydet'}</button>
      </div>
      <p class="hash">SHA-256 · ${esc(t.sha256)}</p>`;
}
function localDetail(t) {
  const agent = t.kind === 'agent';
  const tree = ['main.py', 'README.md', ...(agent ? ['.env.example'] : []), ...t.files.map(f => 'ornek/' + f), 'output/   ← sonuçlar buraya'].join('\n');
  const tested = agent
    ? 'Paketlenmiş kopya örnek veriyle uçtan uca test edildi: OpenAI, Anthropic ve Ollama biçimlerinde sahte model sunucusu, ayrıca bilgisayarda çalışan bir Ollama modeli. Çıktılar taslaktır; sonuçları kendi verinle kontrol et.'
    : 'Paketlenmiş kopya örnek veriyle uçtan uca test edildi; hatalı girdide okunur hata verir. Sonuçları kendi verinle bilinen bir rakama karşı kontrol et.';
  return `<div class="row-detail" id="d-${t.id}">
    <dl>
      <div><dt>Ne yapar</dt><dd>${esc(t.description)}</dd></div>
      <div><dt>Girdi</dt><dd>${esc(t.input)}</dd></div>
      <div><dt>Çıktı</dt><dd>${esc(t.output)}</dd></div>
      <div><dt>${agent ? 'Model ve gizlilik' : 'Gizlilik'}</dt><dd>${esc(t.requirements)}</dd></div>
      <div><dt>Test durumu</dt><dd>${tested}</dd></div>
    </dl>
    <div class="run">
      ${win('Paket klasörü', tree, true)}
      ${win('Terminal', commandsOf(t).split('\n').map(c => '$ ' + c).join('\n'))}
      ${actions(t)}
    </div>
  </div>`;
}
function detail(t) {
  if (t.engine === 'local') return localDetail(t);
  return `<div class="row-detail" id="d-${t.id}">
    <dl>
      <div><dt>Ne yapar</dt><dd>${esc(t.description)}</dd></div>
      <div><dt>Teslim</dt><dd>${esc(t.output)}</dd></div>
      <div><dt>Bağlantı ve izinler</dt><dd>${esc(t.requirements)}</dd></div>
      <div><dt>Platformda ne değişir</dt><dd>${esc(t.effects)}</dd></div>
      <div><dt>Test durumu</dt><dd>Yerel HTTP sözleşme testi geçti. Canlı tenant testi ve dosyanın uygulamada açılış testi henüz yapılmadı.</dd></div>
    </dl>
    <div class="run">
      <figure class="window" data-ui><div class="window-bar"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>config.json · doldurulacak alanlar</span></div><pre><code>${esc(JSON.stringify({operation: t.operation, ...t.config}, null, 2))}</code></pre></figure>
      <figure class="window"><div class="window-bar"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Terminal</span></div><pre><code>$ python native.py --plan
$ python native.py --execute --output output/run-001</code></pre></figure>
      ${actions(t)}
      <div class="sources">${(t.sources || []).map(u => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(new URL(u).hostname)} belgeleri</a>`).join('')}</div>
    </div>
  </div>`;
}

function renderBands(animate = false) {
  const list = visible();
  const label = platform === SAVED ? 'kaydedilmiş paket' : kind === 'code' ? 'kod bloğu' : kind === 'agent' ? 'AI agent' : 'hazır paket';
  $('#listStatus').textContent = `${list.length.toLocaleString('tr')} ${label}${query ? ` · “${query.trim()}” araması` : ''}${platform !== ALL && platform !== SAVED ? ` · ${platform}` : ''}`;
  $('#bands').innerHTML = list.map((t, i) => {
    const open = t.id === openId;
    return `<article class="row${open ? ' open' : ''}${animate ? ' row-in' : ''}" style="--i:${i}">
      <button type="button" class="row-head" data-open="${t.id}" aria-expanded="${open}" aria-controls="d-${t.id}">
        <span class="row-title"><strong>${esc(t.title)}</strong><small>${esc(t.description)}</small></span>
        <span class="row-foot">
          <span class="row-meta">${esc(t.product.name)}<span>${t.engine === 'local' ? 'Yerel dosya' : 'Platform API'} · ${esc(t.domain.name)}${saved.has(t.id) ? ' · kayıtlı' : ''}</span></span>
          <span class="tag${t.kind === 'agent' ? ' agent' : ''}">${t.kind === 'agent' ? 'agent · ' : ''}${esc(t.format)}</span>
        </span>
        ${plus}
      </button>
      ${open ? detail(t) : ''}
    </article>`;
  }).join('');
  $$('[data-open]').forEach(b => b.onclick = () => {
    openId = openId === b.dataset.open ? '' : b.dataset.open;
    renderBands(); syncHash();
    const row = $(`[data-open="${openId}"]`); if (row) { row.focus({preventScroll: true}); row.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }
  });
  $$('[data-save]').forEach(b => b.onclick = () => {
    const id = b.dataset.save; saved.has(id) ? saved.delete(id) : saved.add(id); persist();
    toast(saved.has(id) ? 'Paket kaydedildi' : 'Kayıt kaldırıldı');
    renderPlatforms(); renderBands(); $(`[data-save="${id}"]`)?.focus();
  });
  $$('[data-copy]').forEach(b => b.onclick = async () => {
    try { await navigator.clipboard.writeText(commandsOf(ready.find(t => t.id === b.dataset.copy))); toast('Komutlar kopyalandı'); }
    catch { toast('Kopyalanamadı. Komutu seçip kopyalayabilirsin.'); }
  });
  renderPlannedHint(list);
  syncHash();
}

// When the search finds nothing ready, say honestly where it sits on the roadmap.
function renderPlannedHint(list) {
  const q = norm(query.trim()), hint = $('#plannedHint');
  if (!q && platform !== SAVED) { hint.hidden = true; return; }
  if (platform === SAVED && !list.length) {
    hint.hidden = false;
    hint.innerHTML = '<h3>Henüz kaydettiğin paket yok.</h3><p>Bir paketi açıp “Kaydet”e bastığında burada listelenir. Kayıtlar yalnızca bu tarayıcıda tutulur.</p>';
    return;
  }
  if (list.length) { hint.hidden = true; return; }
  const products = [];
  domains.forEach(d => d.tools.forEach(tool => tool.products.forEach(p => {
    if (q.split(/\s+/).every(w => norm([d.name, tool.name, p.name].join(' ')).includes(w))) products.push({d, tool, p});
  })));
  hint.hidden = false;
  hint.innerHTML = `<h3>“${esc(query.trim())}” için hazır paket yok.</h3>` + (products.length
    ? `<p>Kapsam haritasında ${products.length} planlı ürünle eşleşiyor. Bunlar için API yolu ve çıktı biçimi doğrulanmadan paket yayımlanmaz.</p><ul>${products.slice(0, 8).map(({d, tool, p}) => `<li><strong>${esc(p.name)}</strong><span>${esc(tool.name)} · ${esc(d.name)} · planlı</span></li>`).join('')}</ul>`
    : '<p>Ne hazır paketlerde ne de kapsam haritasında eşleşen bir şey var. Platform adıyla ya da teslim biçimiyle (QVF, PBIX, refresh) aramayı dene.</p>')
    + '<button type="button" class="text-btn" id="clearSearch">Aramayı temizle</button>';
  $('#clearSearch').onclick = () => { query = ''; $('#search').value = ''; renderBands(true); $('#search').focus(); };
}

/* ---------- Coverage map ---------- */
function renderCoverage() {
  $('#coverage').innerHTML = domains.map(d => {
    const products = d.tools.reduce((n, t) => n + t.products.length, 0);
    const count = ready.filter(t => t.domain.id === d.id).length;
    const open = d.id === openDomain;
    const toolList = d.tools.map(t => {
      const readyNames = new Set(ready.filter(r => r.tool.name === t.name && r.domain.id === d.id).map(r => r.product.name));
      return `<li><strong>${esc(t.name)}</strong><span>${t.products.map(p => esc(p.name) + (readyNames.has(p.name) ? ' <em>· hazır</em>' : '')).join(', ')}</span></li>`;
    }).join('');
    return `<div class="cov${open ? ' open' : ''}">
      <button type="button" class="cov-row" data-domain="${d.id}" aria-expanded="${open}" aria-controls="c-${d.id}">
        <span class="cov-name">${esc(d.name)}</span>
        <span class="cov-fact">${d.tools.length}</span>
        <span class="cov-fact">${products}</span>
        <span class="status ${count ? 'ready' : 'planned'}">${count ? `${count} hazır paket` : 'Planlı'}</span>
        ${plus}
      </button>
      ${open ? `<div class="cov-detail" id="c-${d.id}"><p>${esc(d.description)}. ${count ? `Bu alanda ${count} hazır paket var; diğer ürünler geliştirme planında.` : 'Bu alanda henüz hazır paket yok. Ürünler geliştirme planında; doğrulanmadan indirme verilmez.'}</p><ul class="cov-tools">${toolList}</ul>${count ? `<div><button type="button" class="btn btn-primary btn-small" data-show-domain="${d.id}">${count} hazır paketi göster</button></div>` : ''}</div>` : ''}
    </div>`;
  }).join('');
  $$('[data-domain]').forEach(b => b.onclick = () => {
    openDomain = openDomain === b.dataset.domain ? '' : b.dataset.domain;
    renderCoverage(); $(`[data-domain="${b.dataset.domain}"]`)?.focus({preventScroll: true});
  });
  $$('[data-show-domain]').forEach(b => b.onclick = () => {
    const d = domains.find(x => x.id === b.dataset.showDomain);
    platform = ALL; kind = 'all'; openId = ''; query = d.name; $('#search').value = d.name;
    renderKinds(); renderPlatforms(); renderBands(true); $('#paketler').scrollIntoView({behavior: 'smooth'});
  });
}

/* ---------- FAQ ---------- */
const FAQ = [
  ['Erişim anahtarlarım bu siteye gider mi?', 'Hayır. Site yalnızca paket indirir; form, hesap ya da sunucu çağrısı yok. Token’lar bilgisayarındaki .env dosyasında kalır ve yalnızca ilgili platformun kendi adresine gönderilir.',
    '.env.example', `QLIK_ACCESS_TOKEN=
# .env olarak kopyala, değeri yalnızca bu dosyaya yaz`],
  ['--plan ile --execute arasındaki fark ne?', '--plan ağ çağrısı yapmaz: config’i okur, yapılacak işlemi, gereken izinleri ve platform etkisini yazar. --execute gerçek API çağrılarını yapar ve sonuçları boş bir çıktı klasörüne yazar.',
    'PowerShell', `> python native.py --plan
{ "operation": "qlik-export", "network_calls": 0, … }`],
  ['Kod bloğu mu seçmeliyim, agent mı?', 'İşin kuralı belliyse (dışa aktar, çalıştır, profille, mutabık et, belgele) kod bloğu: model yok, sonuç her seferinde aynı. İş yorum ya da yazım istiyorsa (SQL, DAX, veri sözlüğü, KPI tanımı, geçiş planı) agent. Agent’lar da ayrıştırma ve denetimi kodla yapar; model yalnızca muhakeme adımına girer.',
    'Terminal', `> python main.py              # kod bloğu: örnek veriyle
> python main.py --dry-run    # agent: modeli çağırmadan isteği gör`],
  ['Agent’lar verimi nereye gönderiyor?', 'Yalnızca .env dosyasında seçtiğin sağlayıcıya. PROVIDER=ollama ise model bilgisayarında çalışır ve hiçbir veri dışarı çıkmaz. Göndermeden önce e-posta, telefon, TCKN, IBAN ve kart numaraları maskelenir; tablo satırları gönderilmez, yalnızca sütun istatistikleri gider. Giden metnin tamamı output/gonderilecek-istek.json dosyasındadır ve onayın istenir.',
    '.env', `PROVIDER=ollama
MODEL=qwen3:14b
# ya da PROVIDER=anthropic / openai ve API anahtarı`],
  ['Paketler canlı ortamda test edildi mi?', 'Platform paketleri yerel HTTP sözleşme testlerinden geçiyor; gerçek tenant’ta yapılan test şu an sıfır. Yerel kod blokları ve agent’lar paketlenmiş hâlleriyle örnek veride uçtan uca test ediliyor; agent’lar ayrıca sahte model sunucusuyla ve bilgisayarda çalışan bir Ollama modeliyle denendi.',
    'contract.json', `"verification": {
  "contract": "tested",
  "live": "not-tested",
  "native_open": "not-tested"
}`],
  ['Yeni rapor ya da dashboard kurabiliyor mu?', 'Evet, verinden başlangıç raporu kurar. Rapor şablonu blokları tek bir CSV ya da Excel dosyasından ölçü, boyut ve tarih sütunlarını seçer; KPI’lar, kırılım grafikleri, aylık trend, filtreler ve detay tablodan oluşan aynı tasarımı Qlik Sense uygulaması, Power BI projesi (PBIP), Tableau çalışma kitabı, Looker projesi, formüllü Excel raporu, çalışır dbt projesi ve HTML pano olarak üretir. Mevcut raporlarına dokunmaz, hiçbir platforma kendisi yüklemez; dosyaları sen açar ya da yüklersin.',
    'PowerShell · Qlik rapor şablonu', `> python main.py --input satislar.csv --title "Satış Raporu"
[OK] Satış Raporu: 4 ana ölçü, 5 ana boyut ve 9 nesneli rapor sayfası
output/qlik-uygulama/  script.qvs · measures.json · objects/ · build.cmd`],
  ['Rapor şablonları gerçekten açılıyor mu?', 'Biçimler resmî doğrulayıcılardan geçti: Power BI dosyaları Microsoft’un PBIR şemalarından ve TMDL ayrıştırıcısından, Tableau çalışma kitabı Tableau’nun resmî XSD’sinden, Excel dosyası Open XML SDK’dan; Excel formülleri bağımsız bir motorla yeniden hesaplandı, dbt projesi dbt build ile çalıştırıldı. Qlik, Power BI Desktop, Tableau ve Looker’ın kendisinde açılarak test edilmedi; ilk açılışta bir sorun görürsen GitHub’da bildir.',
    'Terminal · dbt şablonu', `> dbt build --profiles-dir .
Done. PASS=20 WARN=0 ERROR=0 SKIP=0 TOTAL=21`],
  ['İş yarıda kesilirse ne olur?', 'Çalışma kimlikleri run-state.json dosyasına yazılır. Job ve refresh paketleri --resume-from ile aynı işi yeni iş başlatmadan takip eder. Yazma çağrıları otomatik tekrarlanmaz.',
    'PowerShell · Databricks job', `> python native.py --execute --resume-from output/run-001/run-state.json --output output/continued-001
# config ile eşleşen job_id ve run_id okunur; yeni iş başlatılmaz`],
  ['Haritadaki planlı ürünler ne zaman gelir?', 'API yolu, ürün sürümü, yetkiler ve çıktı biçimi doğrulandığında. Doğrulanmamış bir uç nokta için paket üretilmez; bu yüzden planlı ürünlerde indirme bağlantısı yok.',
    'integration-plan.json', `"status": "partial",
"note": "API/SDK yolu ve format sürüme göre doğrulanacak."`],
  ['Neye ihtiyacım var?', 'Python 3.10 veya üzeri; ek kütüphane kurulmaz. Platform paketleri için platform erişimi, agent’lar için bir model (bilgisayarında Ollama ya da OpenAI/Anthropic anahtarı) gerekir. Ayrıntılar her paketin README dosyasında.',
    'run.bat', `@echo off
cd /d "%~dp0"
python native.py --plan
pause`]
];
const fragment = i => `<figure class="window" data-ui><div class="window-bar"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>${esc(FAQ[i][2])}</span></div><pre><code>${esc(FAQ[i][3])}</code></pre></figure>`;
let faqIndex = -1;
function renderFaq() {
  $('#faqList').innerHTML = FAQ.map(([q, a], i) => `<li class="faq-item"><button type="button" class="faq-q" data-faq="${i}" aria-expanded="${i === faqIndex}" aria-controls="faq-${i}">${esc(q)}${plus}</button>${i === faqIndex ? `<div class="faq-a" id="faq-${i}"><p>${esc(a)}</p>${fragment(i)}</div>` : ''}</li>`).join('');
  $$('[data-faq]').forEach(b => b.onclick = () => {
    const i = Number(b.dataset.faq); faqIndex = faqIndex === i ? -1 : i;
    renderFaq(); $(`[data-faq="${i}"]`).focus({preventScroll: true});
  });
}

/* ---------- Motion helpers ---------- */
$$('.hero .t-line').forEach((l, i) => l.style.setProperty('--i', i));
const io = 'IntersectionObserver' in window ? new IntersectionObserver(entries => entries.forEach(e => {
  if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
}), {rootMargin: '0px 0px -10% 0px'}) : null;
$$('.reveal').forEach((el, i) => { el.style.setProperty('--d', (i % 3) * 80 + 'ms'); io ? io.observe(el) : el.classList.add('in'); });

/* ---------- Wiring ---------- */
$('#search').addEventListener('input', e => { query = e.target.value; openId = ''; renderBands(); });
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#paketler').scrollIntoView({behavior: 'smooth'}); $('#search').focus({preventScroll: true}); }
});
const header = $('.site-header');
const onScroll = () => header.classList.toggle('scrolled', scrollY > 8);
addEventListener('scroll', onScroll, {passive: true}); onScroll();
$$('[data-kind-jump]').forEach(b => b.addEventListener('click', () => {
  if (!ready.length) return;
  platform = ALL; query = ''; $('#search').value = '';
  setKind(b.dataset.kindJump);
  $('#paketler').scrollIntoView({behavior: 'smooth'});
}));
$$('[data-jump]').forEach(a => a.addEventListener('click', () => {
  if (!ready.length) return;
  platform = a.dataset.jump; query = ''; openId = ''; $('#search').value = '';
  renderPlatforms(); renderBands(true);
}));
renderFaq();

try {
  const response = await fetch('/catalog.json');
  if (!response.ok) throw new Error(String(response.status));
  domains = await response.json();
  tasks = domains.flatMap(d => d.tools.flatMap(tool => tool.products.flatMap(product => product.tasks.map(t => ({...t, domain: d, tool, product})))));
  tasks.forEach(t => { t.platform = platformOf(t); t.format = formatOf(t); });
  ready = tasks.filter(t => t.download);

  const counts = {
    packages: ready.length,
    code: ready.filter(t => t.kind === 'code').length,
    agents: ready.filter(t => t.kind === 'agent').length,
    platforms: new Set(ready.map(t => t.platform)).size,
    domains: domains.length,
    products: domains.reduce((n, d) => n + d.tools.reduce((m, t) => m + t.products.length, 0), 0),
    readyDomains: new Set(ready.map(t => t.domain.id)).size
  };
  $$('[data-count]').forEach(el => { if (el.dataset.count in counts) el.textContent = counts[el.dataset.count].toLocaleString('tr'); });

  const initial = new URLSearchParams(location.hash.slice(1));
  const k = initial.get('k');
  if (KINDS.some(([id]) => id === k)) kind = k;
  const p = initial.get('p');
  if (p && (p === SAVED || ready.some(t => t.platform === p))) platform = p;
  query = initial.get('q') || ''; $('#search').value = query;
  const o = initial.get('open');
  if (o && ready.some(t => t.id === o)) openId = o;

  $('#bands').removeAttribute('aria-busy');
  renderKinds(); renderPlatforms(); renderBands(true); renderCoverage();
  if (openId) $(`[data-open="${openId}"]`)?.scrollIntoView({block: 'center'});
} catch (error) {
  $('#listStatus').textContent = 'Katalog yüklenemedi.';
  $('#bands').innerHTML = '';
  $('#plannedHint').hidden = false;
  $('#plannedHint').innerHTML = '<div><h3>Paket listesi açılmadı.</h3><p>catalog.json okunamadı. Yerelde çalıştırıyorsan önce <code>npm run build</code>, sonra <code>npm start</code> komutunu çalıştırıp sayfayı yenile.</p></div>';
  $('#coverage').innerHTML = '';
}
