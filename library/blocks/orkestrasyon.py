"""Orkestrasyon blokları: cron ifadesi açıklama, Airflow DAG denetimi, GitHub Actions güvenlik denetimi, iş bağımlılığı ve kritik yol.
Dosyalar yalnızca okunur; DAG kodu çalıştırılmaz (ast ile çözümlenir), iş akışı tetiklenmez."""
import ast, re
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from islem import op, arg, Result, need, numbers

GENEL = 'Zamanlama ve iş akışı dosyaları'
GUNLER = ['Pazar', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi']
AYLAR = ['', 'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık']
FIELDS = [('dakika', 0, 59), ('saat', 0, 23), ('ayın günü', 1, 31), ('ay', 1, 12), ('haftanın günü', 0, 7)]
ALIASES = {'@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *', '@monthly': '0 0 1 * *', '@weekly': '0 0 * * 0', '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@hourly': '0 * * * *'}
NAMES = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
         'sun': 0, 'mon': 1, 'tue': 2, 'wed': 3, 'thu': 4, 'fri': 5, 'sat': 6}


def parse_field(text, lo, hi, label):
    values = set()
    for part in text.lower().split(','):
        m = re.fullmatch(r'(\*|[a-z]{3}|\d+)(?:-([a-z]{3}|\d+))?(?:/(\d+))?', part)
        if not m:
            raise ValueError(f'{label}: "{part}" okunamadı' + (' (L, W, # Quartz/Jenkins sözdizimidir, standart cron değildir)' if re.search('[lw#?]', part) else ''))
        a, b, step = m.groups()
        conv = lambda x: NAMES[x] if x in NAMES else int(x)
        start, end = (lo, hi) if a == '*' else (conv(a), conv(b) if b else (hi if step else conv(a)))
        if not (lo <= start <= hi and lo <= end <= hi) or start > end:
            raise ValueError(f'{label}: {part} geçerli aralık ({lo}-{hi}) dışında')
        values.update(range(start, end + 1, int(step) if step else 1))
    if label == 'haftanın günü' and 7 in values:
        values.discard(7)
        values.add(0)
    return values


def parse_cron(expr):
    expr = ALIASES.get(expr.strip().lower(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f'5 alan beklenir (dakika saat gün ay haftagünü), {len(parts)} alan var')
    return parts, [parse_field(p, lo, hi, label) for p, (label, lo, hi) in zip(parts, FIELDS)]


def describe(parts, sets):
    minute, hour, dom, month, dow = parts
    if minute.startswith('*/') and hour == '*':
        when = f'her {minute[2:]} dakikada bir'
    elif hour == '*' and minute.isdigit():
        when = f'her saatin {int(minute):02d}. dakikasında'
    elif hour.startswith('*/') and minute.isdigit():
        when = f'{hour[2:]} saatte bir, :{int(minute):02d}’de'
    elif minute.isdigit() and re.fullmatch(r'[\d,]+', hour):
        when = ', '.join(f'{int(h):02d}:{int(minute):02d}' for h in hour.split(',')) + '’de'
    else:
        when = f'dakika {minute}, saat {hour} alanlarına uyan zamanlarda'
    days = []
    if dom != '*':
        days.append(f"ayın {', '.join(str(d) for d in sorted(sets[2]))}. günü")
    if dow != '*':
        names = [GUNLER[d] for d in sorted(sets[4])]
        days.append('hafta içi her gün' if sorted(sets[4]) == [1, 2, 3, 4, 5] else ', '.join(names))
    months = '' if month == '*' else ', '.join(AYLAR[m] for m in sorted(sets[3])) + ' aylarında, '
    when_days = (' veya '.join(days) if dom != '*' and dow != '*' else days[0]) if days else 'her gün'
    return f'{months}{when_days} {when}'.strip()


def next_runs(sets, start, n=3):
    minutes, hours, dom, months, dow = sets
    out, t = [], start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = start + timedelta(days=800)
    dom_star, dow_star = len(dom) == 31, len(dow) == 7
    while len(out) < n and t < limit:
        day_ok = (t.day in dom or (t.weekday() + 1) % 7 in dow) if not dom_star and not dow_star else (t.day in dom and (t.weekday() + 1) % 7 in dow)
        if t.month not in months or not day_ok:
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if t.hour not in hours:
            t = (t + timedelta(hours=1)).replace(minute=0)
            continue
        if t.minute in minutes:
            out.append(t)
        t += timedelta(minutes=1)
    return out


@op('cron-aciklama', title='Cron ifadelerini Türkçe açıkla ve doğrula', domain='orchestration', product=GENEL, tag='cron', samples={'zamanlamalar.csv': 'zamanlamalar.csv'},
    args=[arg('--column', default='cron'), arg('--from', dest='start', default='2026-10-16 12:00', help='Sonraki çalışmaların hesaplanacağı an')],
    description='Airflow, cron, Kubernetes CronJob ve GitHub Actions zamanlamalarındaki 5 alanlı cron ifadelerini doğrular, “hafta içi her gün 08:30’da” gibi Türkçe açıklamaya çevirir ve sonraki 3 çalışma zamanını hesaplar. Standart olmayan (L, W, #) ve aralık dışı alanları işaretler.',
    input='Cron ifadesi sütunu olan CSV/XLSX', output='sonuc.csv', command='python main.py --column cron --from "2026-10-16 12:00"',
    expect={'gecerli': 5, 'hatali': 2})
def cron(columns, rows, args):
    from tabular import parse_date
    start = parse_date(args.start)[0] if parse_date(args.start) else datetime.now()
    out = []
    for r in rows:
        expr = r[need(args.column, columns, '--column')].strip()
        try:
            parts, sets = parse_cron(expr)
            runs = next_runs(sets, start)
            out.append({**r, 'aciklama': describe(parts, sets), 'sonraki_calismalar': ' | '.join(t.strftime('%Y-%m-%d %H:%M') for t in runs), 'hata': ''})
        except ValueError as exc:
            out.append({**r, 'aciklama': '', 'sonraki_calismalar': '', 'hata': str(exc)})
    bad = sum(bool(o['hata']) for o in out)
    return Result(f'{len(out) - bad} geçerli, {bad} hatalı cron ifadesi', columns + ['aciklama', 'sonraki_calismalar', 'hata'], out, {'gecerli': len(out) - bad, 'hatali': bad},
                  notes=['Zaman dilimi uygulanmaz; zamanlayıcınızın saat dilimini (çoğunlukla UTC) dikkate alın. Gün ve haftagünü birlikte verilirse cron “veya” mantığı uygular.'])


# ---------------------------------------------------------------- Airflow
SECRET_NAME = re.compile(r'(parola|sifre|şifre|password|passwd|secret|token|api_?key|access_?key)', re.I)


def call_name(node):
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ''


def kw(node, name):
    return next((k.value for k in node.keywords if k.arg == name), None)


def airflow_findings(path, text):
    tree = ast.parse(text, filename=str(path))
    findings, dags, tasks = [], [], []
    defaults = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defaults[t.id] = {k.value: v for k, v in zip(node.value.keys, node.value.values) if isinstance(k, ast.Constant)}
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and len(node.value.value) >= 4:
            for t in node.targets:
                if isinstance(t, ast.Name) and SECRET_NAME.search(t.id):
                    findings.append(('yüksek', node.lineno, f'Koda gömülü gizli bilgi: {t.id} (Airflow Connection/Variable veya secrets backend kullanın)'))
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name == 'DAG':
            dag_id = kw(node, 'dag_id') or (node.args[0] if node.args else None)
            dag_id = dag_id.value if isinstance(dag_id, ast.Constant) else '?'
            dags.append(dag_id)
            sd = kw(node, 'start_date')
            if sd is not None and any(isinstance(n, ast.Call) and call_name(n) in ('now', 'today', 'utcnow', 'days_ago') for n in ast.walk(sd)):
                findings.append(('yüksek', node.lineno, f'{dag_id}: start_date dinamik (datetime.now/days_ago) — zamanlama kayar, çalışmalar atlanabilir'))
            if kw(node, 'schedule_interval') is not None:
                findings.append(('orta', node.lineno, f'{dag_id}: schedule_interval kullanımdan kaldırıldı; “schedule” kullanın (Airflow 3’te yok)'))
            if kw(node, 'catchup') is None:
                findings.append(('orta', node.lineno, f'{dag_id}: catchup açıkça belirtilmemiş (sürüme göre varsayılan değişir)'))
            da = kw(node, 'default_args')
            dflt = defaults.get(da.id, {}) if isinstance(da, ast.Name) else {k.value: v for k, v in zip(da.keys, da.values)} if isinstance(da, ast.Dict) else {}
            if 'retries' not in dflt:
                findings.append(('orta', node.lineno, f'{dag_id}: default_args içinde retries yok — geçici hatalarda iş hemen başarısız olur'))
            if 'owner' not in dflt:
                findings.append(('düşük', node.lineno, f'{dag_id}: owner tanımsız'))
            if kw(node, 'tags') is None:
                findings.append(('düşük', node.lineno, f'{dag_id}: tags yok (arayüzde filtrelenemez)'))
        tid = kw(node, 'task_id')
        if isinstance(tid, ast.Constant) and name.endswith(('Operator', 'Sensor')):
            tasks.append((tid.value, name, node.lineno))
    for t, n in Counter(t for t, _, _ in tasks).items():
        if n > 1:
            line = next(l for x, _, l in tasks if x == t)
            findings.append(('yüksek', line, f'task_id "{t}" {n} kez tanımlı — DAG yüklenemez'))
    if re.search(r'^\s*\w+\s*=\s*Variable\.get\(', text, re.M):
        findings.append(('orta', 0, 'Modül düzeyinde Variable.get — her DAG ayrıştırmasında veritabanına gider; görev içinde veya Jinja ile okuyun'))
    return dags, tasks, findings


@op('airflow-dag-denetim', title='Airflow DAG dosyalarını denetle', domain='orchestration', product='Airflow', tag='DAG', kind='file',
    samples={'dags/satis_yukleme.py': 'dags/satis_yukleme.py', 'dags/eski_rapor.py': 'dags/eski_rapor.py'}, sample_input='dags',
    args=[arg('--strict', action='store_true', help='Yüksek önemli bulguda 2 koduyla çık (CI)')],
    description='DAG dosyalarını çalıştırmadan (Python ast ile) çözümler: DAG ve görev listesi, dinamik start_date, kaldırılan schedule_interval, catchup, retries/owner/tags eksikliği, tekrar eden task_id, koda gömülü parola ve modül düzeyinde Variable.get kullanımını raporlar.',
    input='DAG .py dosyası veya dags/ klasörü', output='sonuc.csv (bulgular) + gorevler.csv', command='python main.py --input dags/ --strict',
    expect={'dag': 2, 'gorev': 5, 'yuksek': 3})
def airflow(args):
    p = Path(args.input)
    files = sorted(p.rglob('*.py')) if p.is_dir() else [p]
    out, tasks_all, dag_count = [], [], 0
    for f in files:
        text = f.read_text(encoding='utf-8-sig', errors='replace')
        if 'DAG' not in text:
            continue
        try:
            dags, tasks, findings = airflow_findings(f, text)
        except SyntaxError as exc:
            out.append({'dosya': f.name, 'satir': exc.lineno or 0, 'onem': 'yüksek', 'bulgu': f'Sözdizimi hatası: {exc.msg}'})
            continue
        dag_count += len(dags)
        tasks_all += [{'dosya': f.name, 'task_id': t, 'operator': o, 'satir': l} for t, o, l in tasks]
        out += [{'dosya': f.name, 'satir': l, 'onem': s, 'bulgu': m} for s, l, m in findings]
    sev = Counter(o['onem'] for o in out)
    return Result(f"{dag_count} DAG, {len(tasks_all)} görev; {len(out)} bulgu ({sev['yüksek']} yüksek)", ['dosya', 'satir', 'onem', 'bulgu'], out,
                  {'dag': dag_count, 'gorev': len(tasks_all), 'bulgu': len(out), 'yuksek': sev['yüksek']},
                  files={'gorevler.csv': (['dosya', 'task_id', 'operator', 'satir'], tasks_all)}, code=2 if args.strict and sev['yüksek'] else 0)


# ---------------------------------------------------------------- GitHub Actions
UNTRUSTED = re.compile(r'\$\{\{\s*github\.(event\.(pull_request|issue|comment|review|head_commit|discussion)\.(title|body|head\.ref|message|label)|head_ref)[^}]*\}\}')


@op('github-actions-denetim', title='GitHub Actions iş akışlarını güvenlik açısından denetle', domain='orchestration', product='Actions', tag='CI güvenliği', kind='file',
    samples={'workflows/ci.yml': 'workflows/ci.yml', 'workflows/nightly.yml': 'workflows/nightly.yml'}, sample_input='workflows',
    args=[arg('--strict', action='store_true')],
    description='.github/workflows dosyalarında pull_request_target ile PR kodunun çekilmesini, SHA’ya sabitlenmemiş üçüncü taraf action’ları (@main/@master), run adımlarına doğrudan giren güvenilmeyen başlık/gövde ifadelerini (script injection), gizli anahtarın loga yazdırılmasını, eksik permissions ve timeout-minutes tanımlarını satır numarasıyla bulur.',
    input='.yml dosyası veya .github/workflows klasörü', output='sonuc.csv (bulgular)', command='python main.py --input .github/workflows --strict',
    expect={'dosya': 2, 'yuksek': 4})
def gha(args):
    p = Path(args.input)
    files = sorted(list(p.rglob('*.yml')) + list(p.rglob('*.yaml'))) if p.is_dir() else [p]
    out = []
    for f in files:
        lines = f.read_text(encoding='utf-8-sig', errors='replace').splitlines()
        text = '\n'.join(lines)
        add = lambda n, s, m: out.append({'dosya': f.name, 'satir': n, 'onem': s, 'bulgu': m})
        prt = re.search(r'^\s*pull_request_target\s*:', text, re.M)
        if prt and re.search(r'ref:\s*\$\{\{\s*github\.event\.pull_request\.head\.(sha|ref)', text):
            add(text[:prt.start()].count('\n') + 1, 'yüksek', 'pull_request_target ile PR kodu çekiliyor — fork’tan gelen kod gizli anahtarlarla çalışır')
        if not re.search(r'^permissions\s*:', text, re.M):
            add(1, 'orta', 'Üst düzeyde permissions tanımlı değil — GITHUB_TOKEN geniş yetkiyle çalışabilir')
        in_run = False
        for n, line in enumerate(lines, start=1):
            m = re.match(r'\s*-?\s*uses:\s*([^\s#]+)', line)
            if m:
                ref = m.group(1)
                if '@' not in ref:
                    add(n, 'orta', f'{ref}: sürüm belirtilmemiş')
                else:
                    name, ver = ref.rsplit('@', 1)
                    if ver in ('main', 'master', 'latest', 'HEAD'):
                        add(n, 'yüksek', f'{ref}: dal adına bağlı — action sahibi kodu her an değiştirebilir')
                    elif not re.fullmatch(r'[0-9a-f]{40}', ver) and not name.startswith(('actions/', 'github/', './')):
                        add(n, 'orta', f'{ref}: üçüncü taraf action commit SHA’sına sabitlenmemiş')
            if re.match(r'\s*-?\s*run\s*:', line):
                in_run = True
            elif re.match(r'\s*-?\s*(uses|name|with|env|if|id)\s*:', line):
                in_run = False
            if in_run and UNTRUSTED.search(line):
                add(n, 'yüksek', 'Güvenilmeyen ifade run adımında doğrudan kullanılıyor (script injection); önce env değişkenine atayın')
            if in_run and re.search(r'(echo|printf|cat).*\$\{\{\s*secrets\.', line):
                add(n, 'yüksek', 'Gizli anahtar komutta yazdırılıyor; maskeleme her durumda korumaz')
        jobs = re.findall(r'^  ([\w-]+):\s*$', text.split('jobs:', 1)[1], re.M) if 'jobs:' in text else []
        if jobs and 'timeout-minutes' not in text:
            add(1, 'düşük', 'timeout-minutes yok — takılan iş varsayılan 6 saat dakika tüketir')
    sev = Counter(o['onem'] for o in out)
    return Result(f"{len(files)} iş akışı, {len(out)} bulgu ({sev['yüksek']} yüksek)", ['dosya', 'satir', 'onem', 'bulgu'], out,
                  {'dosya': len(files), 'bulgu': len(out), 'yuksek': sev['yüksek']}, code=2 if args.strict and sev['yüksek'] else 0,
                  notes=['YAML tam ayrıştırılmaz; satır tabanlı kontrol yapılır. Bulguları dosyada doğrulayın.'])


@op('is-bagimlilik-analizi', title='İş bağımlılıkları: döngü, eksik iş ve kritik yol', domain='orchestration', product=GENEL, tag='kritik yol', samples={'is_bagimliliklari.csv': 'is_bagimliliklari.csv'},
    args=[arg('--job', default='is'), arg('--deps', default='bagli_oldugu'), arg('--duration', default='sure_dk')],
    description='İş (job/görev) listesindeki bağımlılıklardan döngüleri, tanımsız işe bağımlılıkları ve kökleri bulur; süreler verilirse her işin en erken başlangıç/bitişini, toplam süreyi belirleyen kritik yolu ve paralel çalışabilecek seviyeleri hesaplar. Gece yükleme penceresi planlaması için.',
    input='İş listesi (iş, bağlı olduğu işler, süre)', output='sonuc.csv (iş bazında zamanlama) + rapor', command='python main.py --job task --deps upstream --duration avg_minutes',
    expect={'is': 10, 'dongu': 2, 'eksik_bagimlilik': 1, 'kritik_yol_dk': 107})
def bagimlilik(columns, rows, args):
    job, dep = need(args.job, columns, '--job'), need(args.deps, columns, '--deps')
    dur = dict(zip((r[job].strip() for r in rows), numbers(rows, args.duration))) if args.duration in columns else {}
    deps = {r[job].strip(): [d.strip() for d in r[dep].split(',') if d.strip()] for r in rows}
    missing = [(j, d) for j, ds in deps.items() for d in ds if d not in deps]
    indeg = {j: sum(1 for d in ds if d in deps) for j, ds in deps.items()}
    children = defaultdict(list)
    for j, ds in deps.items():
        for d in ds:
            if d in deps:
                children[d].append(j)
    queue, order = deque(sorted(j for j, n in indeg.items() if n == 0)), []
    while queue:
        j = queue.popleft()
        order.append(j)
        for c in children[j]:
            indeg[c] -= 1
            if not indeg[c]:
                queue.append(c)
    cyclic = sorted(set(deps) - set(order))
    start, finish, level, prev = {}, {}, {}, {}
    for j in order:
        ups = [d for d in deps[j] if d in finish]
        start[j] = max((finish[d] for d in ups), default=0.0)
        prev[j] = max(ups, key=lambda d: finish[d]) if ups else None
        finish[j] = start[j] + (dur.get(j) or 0.0)
        level[j] = max((level[d] + 1 for d in ups), default=0)
    end = max(finish, key=finish.get) if finish else None
    path, cur = [], end
    while cur:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    out = [{'is': j, 'seviye': level.get(j, ''), 'en_erken_baslangic': start.get(j, ''), 'en_erken_bitis': finish.get(j, ''),
            'kritik_yolda': 'evet' if j in path else '', 'durum': 'döngüde' if j in cyclic else ('eksik bağımlılık: ' + ', '.join(d for jj, d in missing if jj == j)) if any(jj == j for jj, _ in missing) else ''}
           for j in deps]
    total = finish[end] if end else 0
    return Result(f"{len(deps)} iş; kritik yol {' → '.join(path)} ({total:g} dk); {len(cyclic)} iş döngüde, {len(missing)} eksik bağımlılık", list(out[0]), out,
                  {'is': len(deps), 'dongu': len(cyclic), 'eksik_bagimlilik': len(missing), 'kritik_yol_dk': total, 'paralel_seviye': max(level.values(), default=0) + 1},
                  notes=['Döngüdeki işler zamanlanamaz ve kritik yol hesabına girmez. Eksik bağımlılıklar yok sayılarak hesaplanır.'])
