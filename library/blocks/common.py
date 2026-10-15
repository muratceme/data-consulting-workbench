"""Small helpers shared by every code block and agent: console, output folder, PII masking, Markdown."""
import json, re, sys
from pathlib import Path


def console():
    # Windows terminals (cp1254) must not crash on Turkish text or symbols.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')


def here(*parts):
    """Path next to the running script, so `python main.py` works from any folder."""
    return Path(sys.argv[0]).resolve().parent.joinpath(*parts)


def output_dir(path):
    out = Path(path)
    if out.exists() and not out.is_dir():
        raise ValueError(f'Çıktı yolu bir dosya: {out}')
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def write_text(path, text):
    Path(path).write_text(text.rstrip() + '\n', encoding='utf-8')


def md_cell(value):
    return str('' if value is None else value).replace('|', '\\|').replace('\n', ' ').strip()


def md_table(headers, rows):
    if not rows:
        return '_Kayıt yok._\n'
    lines = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    lines += ['| ' + ' | '.join(md_cell(v) for v in row) + ' |' for row in rows]
    return '\n'.join(lines) + '\n'


# ---------- Personal data detection (Turkey-aware) ----------
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+')
IBAN = re.compile(r'\bTR\d{2}[ ]?(\d{4}[ ]?){5}\d{2}\b', re.I)
CARD = re.compile(r'\b(?:\d[ -]?){15,16}\b')
PHONE = re.compile(r'(?<!\d)(\+?90[ -]?)?0?5\d{2}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?!\d)')
TCKN = re.compile(r'(?<!\d)[1-9]\d{10}(?!\d)')


def valid_tckn(value):
    d = [int(c) for c in value]
    if len(d) != 11 or d[0] == 0:
        return False
    return (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 == d[9] and sum(d[:10]) % 10 == d[10]


def pii_kind(value):
    text = str(value)
    if EMAIL.search(text): return 'e-posta'
    if IBAN.search(text): return 'IBAN'
    m = TCKN.search(text)
    if m and valid_tckn(m.group(0)): return 'TCKN'
    if PHONE.search(text): return 'telefon'
    if CARD.search(text): return 'kart no'
    return None


def mask_text(text):
    """Replace personal data with typed placeholders. Returns (masked_text, counts)."""
    counts = {}

    def sub(pattern, label, value, check=None):
        def repl(m):
            if check and not check(m.group(0)):
                return m.group(0)
            counts[label] = counts.get(label, 0) + 1
            return f'[{label}]'
        return pattern.sub(repl, value)

    text = sub(EMAIL, 'E-POSTA', str(text))
    text = sub(IBAN, 'IBAN', text)
    text = sub(TCKN, 'TCKN', text, valid_tckn)
    text = sub(PHONE, 'TELEFON', text)
    text = sub(CARD, 'KART', text)
    return text, counts
