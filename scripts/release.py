"""Bundle only tracked source and generated dist; exclude all local runtime and credentials."""
import subprocess,zipfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
files=[ROOT/p for p in subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines()]
files.extend(p for p in (ROOT/'dist').rglob('*') if p.is_file())
output=ROOT/'.runtime';output.mkdir(exist_ok=True)
archive=output/'datawork-v0.1.0.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in sorted(files):z.write(p,Path('datawork')/p.relative_to(ROOT))
checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
(output/'datawork-v0.1.0.sha256').write_text(checksum+'  '+archive.name+'\n',encoding='utf-8')
print(str(archive));print('SHA256 '+checksum)
