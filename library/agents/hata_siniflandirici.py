"""İş hatası sınıflandırıcı (AI agent): zamanlanmış işlerin (Airflow, SSIS, cron, ADF) başarısız çalışmalarındaki hata mesajlarını
kategorilere ayıran, yeniden kullanılabilir regex kuralları yazar. Mesajlar önce kodla şablona indirgenir (sayılar, tarihler, UUID’ler,
IP ve dosya yolları <N>, <TARIH>, <IP>, <YOL> olur) ve yalnızca tekil şablonlar maskelenerek gönderilir. Model her kategori için bir desen, aksiyon ve
“yeniden denensin mi” kararı yazar. Kurallar sırayla (ilk eşleşen kazanır) TÜM mesajlarda yerelde çalıştırılır: derlenmeyen ya da iç
içe niceleyicili desen, hiçbir mesaja uymayan kategori, örnek olarak gösterdiği şablonu yakalamayan kural ve mesajların --kapsama
oranından azını sınıflayan kural seti eşleşmeyen örneklerle geri gönderilir.
Çıktı: siniflandirilmis.csv, kurallar.json, hata-siniflari.md."""
import argparse, re
from collections import Counter, defaultdict
import llm
import tabular
from common import here, output_dir, write_text, write_json, md_table, mask_text

NESTED = re.compile(r'\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]')
TEMPLATE = [(re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I), '<UUID>'),
            (re.compile(r'\b\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?\b'), '<TARIH>'),
            (re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b'), '<IP>'),
            (re.compile(r'(?<![\w)])(?:[A-Za-z]:\\|/)[^\s\'",;]+'), '<YOL>'),
            (re.compile(r'\b\d+(?:[.,]\d+)?\b'), '<N>')]


def template(msg):
    t = ' '.join(msg.split())
    for rx, token in TEMPLATE:
        t = rx.sub(token, t)
    return t


def classify(rules, msg):
    for r in rules:
        if r['rx'].search(msg):
            return r['kod']
    return ''


def validator(messages, templates, coverage):
    def validate(obj):
        rules, problems = [], []
        for i, c in enumerate(obj.get('kategoriler') or [], 1):
            if not isinstance(c, dict):
                continue
            code, rx = str(c.get('kod', '')).strip().upper(), str(c.get('desen', '')).strip()
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,30}', code) or code in {r['kod'] for r in rules}:
                problems.append(f'{i}. kategori kodu “{code}” geçersiz ya da tekrar (BÜYÜK_HARF_ALT_ÇİZGİ)')
                continue
            if NESTED.search(rx):
                problems.append(f'{code}: iç içe niceleyici ({NESTED.search(rx).group(0)})')
                continue
            try:
                compiled = re.compile(rx, re.I)
            except re.error as exc:
                problems.append(f'{code}: desen derlenmiyor ({exc})')
                continue
            if not str(c.get('aksiyon', '')).strip() or not isinstance(c.get('yeniden_dene'), bool):
                problems.append(f'{code}: aksiyon ve yeniden_dene (true/false) gerekli')
                continue
            rules.append({'kod': code, 'ad': str(c.get('ad', code)).strip(), 'desen': rx, 'rx': compiled, 'aksiyon': str(c['aksiyon']).strip(),
                          'yeniden_dene': c['yeniden_dene'], 'ornekler': [int(x) for x in c.get('ornekler') or [] if str(x).isdigit()]})
        if problems:
            raise ValueError('; '.join(problems))
        if not rules:
            raise ValueError('en az bir kategori gerekli')
        got = [classify(rules, m) for m in messages]
        used = Counter(got)
        idle = [r['kod'] for r in rules if not used[r['kod']]]
        if idle:
            problems.append(f'hiçbir mesajı yakalamayan (ya da önceki kurallarca gölgelenen) kategori: {", ".join(idle)}')
        for r in rules:
            wrong = [n for n in r['ornekler'] if 1 <= n <= len(templates) and classify(rules, templates[n - 1]) != r['kod']]
            if wrong:
                problems.append(f'{r["kod"]}: örnek gösterdiği şablon {", ".join(map(str, wrong))} bu kurala düşmüyor ({", ".join(classify(rules, templates[n - 1]) or "hiçbiri" for n in wrong)})')
        rate = sum(1 for g in got if g) / len(messages)
        if rate < coverage:
            left = list(dict.fromkeys(template(m) for m, g in zip(messages, got) if not g))
            problems.append(f'mesajların %{rate * 100:.1f}’i sınıflandı (gereken %{coverage * 100:.0f}); sınıflanmayan şablonlar: '
                            + ' | '.join(mask_text(t)[0][:160] for t in left[:6]))
        if problems:
            raise ValueError('; '.join(problems))
        return rules, got
    return validate


SYSTEM = (llm.SAFETY + ' Sen bir veri platformu ekibinde nöbetçi mühendissin. Başarısız iş çalışmalarının hata mesajı şablonlarını kök nedene göre kategorilere '
          'ayır (ör. bağlantı/zaman aşımı, kimlik doğrulama, kaynak dosya yok, şema uyumsuzluğu, disk/bellek, veri kalitesi, kilit/deadlock). Her kategori '
          'için Python re ile (büyük/küçük harf duyarsız, search) çalışan bir desen yaz; kurallar sırayla uygulanır, ilk eşleşen kazanır. Geçici hatalarda '
          'yeniden_dene true, kalıcı hatalarda false olsun. "ornekler" alanına o kategoriye düşen şablon numaralarından birkaçını yaz. İç içe niceleyici kullanma. '
          'Yanıt şeması: {"kategoriler": [{"kod": "BAGLANTI", "ad": "Bağlantı / zaman aşımı", "desen": "timeout|timed out|connection reset", '
          '"yeniden_dene": true, "aksiyon": "...", "ornekler": [1, 4]}]}')


def main():
    p = argparse.ArgumentParser(description='Başarısız iş çalışmalarının hata mesajlarını yeniden kullanılabilir regex kurallarıyla sınıflayan agent.')
    p.add_argument('--input', default=str(here('ornek', 'basarisiz_isler.csv')))
    p.add_argument('--message-column', default='hata_mesaji')
    p.add_argument('--job-column', default='is_adi')
    p.add_argument('--coverage', type=float, default=0.95)
    llm.add_arguments(p)
    args = p.parse_args()
    columns, rows = tabular.read(args.input)
    for c in (args.message_column, args.job_column):
        if c not in columns:
            raise ValueError(f'{c} sütunu yok. Sütunlar: {", ".join(columns)}')
    rows = [r for r in rows if r[args.message_column].strip()]
    if not rows:
        raise ValueError('Hata mesajı olan satır yok.')
    messages = [' '.join(r[args.message_column].split()) for r in rows]
    counts = Counter(template(m) for m in messages)
    templates = [t for t, _ in counts.most_common()]
    out = output_dir(args.output)
    user = (f'{len(rows)} başarısız çalışma, {len(templates)} tekil hata şablonu (sayılar <N>, tarihler <TARIH>, IP’ler <IP>, yollar <YOL>, UUID’ler <UUID> olarak indirgendi; '
            'desenlerin gerçek mesajlarda da çalışması için bu yer tutucuları desene yazma):\n'
            + '\n'.join(f'{i}. ({counts[t]} kez) {t[:400]}' for i, t in enumerate(templates, 1)))
    result, meta = llm.ask(args, out, SYSTEM, user, validator(messages, templates, args.coverage), max_tokens=12000)
    if result is None:
        return
    rules, got = result
    table = [{**r, 'kategori': g or 'SINIFLANMADI'} for r, g in zip(rows, got)]
    tabular.write_csv(out / 'siniflandirilmis.csv', columns + ['kategori'], table)
    write_json(out / 'kurallar.json', [{k: v for k, v in r.items() if k != 'rx'} for r in rules])
    by_cat, jobs = Counter(got), defaultdict(Counter)
    for r, g in zip(rows, got):
        jobs[g][r[args.job_column]] += 1
    md = ['# Hata sınıfları', '', f'{len(rows)} başarısız çalışma, {len(templates)} şablon; %{sum(1 for g in got if g) / len(rows) * 100:.1f}’i {len(rules)} kategoriye ayrıldı.', '',
          md_table(['Kategori', 'Çalışma', 'Yeniden dene', 'En çok etkilenen işler', 'Aksiyon'],
                   [[f'{r["kod"]} · {r["ad"]}', by_cat[r['kod']], 'evet' if r['yeniden_dene'] else 'hayır',
                     ', '.join(f'{j} ({n})' for j, n in jobs[r['kod']].most_common(3)), r['aksiyon']] for r in sorted(rules, key=lambda r: -by_cat[r['kod']])]), '']
    retry = sum(by_cat[r['kod']] for r in rules if r['yeniden_dene'])
    md += [f'Otomatik yeniden denemeyle çözülebilecek çalışma: {retry} (%{retry / len(rows) * 100:.0f}); kalanlar insan müdahalesi ister.', '',
           'kurallar.json sırayla uygulanır (ilk eşleşen kazanır); yeni hataları aynı kurallarla sınıflamak için kullanın.', '',
           f'_Model: {meta["model"]} · Kurallar tüm mesajlarda kodla çalıştırıldı; kapsama ve her kategorinin örnekleri denetlendi. Kök neden ataması anlam olarak elle kontrol edilmelidir._']
    write_text(out / 'hata-siniflari.md', '\n'.join(md))
    print(f'[OK] {len(rows)} çalışma, {len(rules)} kategori. Çıktı: {out / "hata-siniflari.md"}')


if __name__ == '__main__':
    llm.run(main)
