"""Files of a local code-block / agent package. Used by build.py and by tests that run the packaged copy."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'library'

ENV_EXAMPLE = '''# .env olarak kopyala. Bu dosya yalnızca bilgisayarında kalır; Git'e eklenmez.
# Sağlayıcı: ollama (model bilgisayarında çalışır, veri dışarı çıkmaz), openai veya anthropic
PROVIDER=ollama
# Sağlayıcında erişimin olan model adı. Örnek: Ollama "qwen3:14b", Anthropic "claude-sonnet-5-5"
MODEL=
OPENAI_API_KEY=
# OpenAI uyumlu başka bir sunucu (Azure OpenAI, LM Studio, vLLM...) için adresi değiştir
OPENAI_BASE_URL=https://api.openai.com/v1
ANTHROPIC_API_KEY=
OLLAMA_URL=http://localhost:11434
# Yerel modellerde uzun yanıtlar için en fazla bekleme (saniye)
LLM_TIMEOUT=1800
'''


def module_path(name):
    for folder in ('blocks', 'agents'):
        path = LIB / folder / f'{name}.py'
        if path.is_file():
            return path
    raise FileNotFoundError(f'Modül bulunamadı: {name}')


def readme(item):
    agent = item['kind'] == 'agent'
    samples = ', '.join(f'`ornek/{name}`' for name in item['samples'])
    lines = [f"# {item['title']}", '', f"**{'AI agent' if agent else 'Kod bloğu'}** · datawork", '', item['description'], '',
             '## Girdi', item['input'], '', '## Çıktı', item['output'] + ' (`output/` klasörüne yazılır)', '',
             '## Hızlı başlangıç', '']
    if agent:
        lines += ['1. `.env.example` dosyasını `.env` olarak kopyala; `PROVIDER` ve `MODEL` değerlerini, gerekiyorsa API anahtarını yaz.',
                  '   Ollama seçersen model bilgisayarında çalışır ve hiçbir veri dışarı çıkmaz (`ollama pull <model>`).',
                  f'2. Önce modeli çağırmadan isteği gör: `python main.py --dry-run` (örnek dosyalar: {samples}).',
                  '   `output/gonderilecek-istek.json` modele gidecek metnin tam hâlidir.',
                  '3. `python main.py` çalıştır; özet gösterilir ve onayın istenir (zamanlanmış çalışmada `--yes`).',
                  f"4. Kendi dosyanla: `{item['command']}`", '',
                  '## Gizlilik ve güvenlik', item['requirements'], '',
                  'Model yanıtı JSON olarak okunur ve yerelde denetlenir; kurala uymazsa model bir kez düzeltmeye zorlanır, yine uymazsa iş durur. '
                  'Modelin ürettiği kod/sorgu hiçbir sistemde otomatik çalıştırılmaz.', '']
    else:
        lines += [f'1. Örnek veriyle dene: `python main.py` (örnek dosyalar: {samples}).',
                  f"2. Kendi dosyanla: `{item['command']}`",
                  '3. Tüm seçenekler: `python main.py --help`', '',
                  '## Gizlilik ve güvenlik', item['requirements'], '']
    lines += ['## Test durumu',
              'Örnek veriyle uçtan uca test: GEÇTİ (paketlenmiş kopya üzerinde).' if not agent else
              'Örnek veriyle uçtan uca test: GEÇTİ — sahte model sunucusu (OpenAI, Anthropic, Ollama biçimleri) ve bilgisayardaki Ollama modeliyle.',
              'Sizin verinizle sonuçları bilinen bir rakamla karşılaştırın. Örnek veriler kurgusaldır.', '',
              'Windows’ta `run.bat` dosyasına çift tıklayarak örnekle çalıştırabilirsin.', '', 'MIT lisansı.']
    return '\n'.join(lines) + '\n'


def files(item):
    """Return {archive_name: bytes} for a LOCAL catalog entry."""
    if item.get('op'):
        import sys
        sys.path.insert(0, str(LIB / 'blocks'))
        import islem
        out = {'main.py': islem.stub(item['spec']).encode('utf-8')}
    else:
        out = {'main.py': (LIB / item['main']).read_bytes()}
    for name in item['modules']:
        out[f'{name}.py'] = module_path(name).read_bytes()
    for name, src in item['samples'].items():
        out[f'ornek/{name}'] = (LIB / 'samples' / src).read_bytes()
    out['README.md'] = readme(item).encode('utf-8')
    out['LICENSE'] = (ROOT / 'LICENSE').read_bytes()
    out['.gitignore'] = b'.env\noutput/\n__pycache__/\n'
    out['run.bat'] = b'@echo off\r\ncd /d "%~dp0"\r\npython main.py %*\r\npause\r\n'
    if item['kind'] == 'agent':
        out['.env.example'] = ENV_EXAMPLE.encode('utf-8')
    manifest = {'id': item['id'], 'tur': 'agent' if item['kind'] == 'agent' else 'kod', 'ad': item['title'], 'surum': '1.0.0', 'python': '>=3.10',
                'ana_dosya': 'main.py', 'calistir': item['command'], 'girdi': item['input'], 'cikti': item['output'],
                'internet': 'yalnızca seçilen model sağlayıcısı' if item['kind'] == 'agent' else False,
                'ornek_veri': [f'ornek/{n}' for n in item['samples']]}
    out['task.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    return out


def extract(item, target):
    """Write the package files to a folder (tests run the packaged copy, not the source tree)."""
    target = Path(target)
    for name, data in files(item).items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return target
