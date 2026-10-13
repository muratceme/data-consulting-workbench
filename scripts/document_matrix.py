"""Regenerate the committed product matrix from the built catalog without secrets."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    rows = json.loads((ROOT/'dist/integration-plan.json').read_text(encoding='utf-8'))
    text = '# Tüm alan ve ürünlerin entegrasyon matrisi\n\n'
    text += f'{len(rows)} ürün bağlamı. `Kısmi` yalnızca listelenen işlemlerin kodlandığını ve yerel sözleşme testlerinden geçtiğini gösterir. '
    text += 'Hiçbirinde canlı hesap/uygulamada açılış doğrulaması yapılmadı. API/SDK yolu ve çıktı sütunları, uygulanmamış ürünlerde tasarım hedefidir; doğrulanmış vendor desteği iddiası değildir.\n\n'
    text += '| Alan | Yazılım | Ürün | Durum / uygulanmış işlemler | Planlanan bağlantı | Çıktı hedefi |\n|---|---|---|---|---|---|\n'
    for row in rows:
        status = ('Kısmi: '+', '.join(row['implemented_operations'])) if row['implemented_operations'] else 'Planlandı; paket yok'
        values = [row['domain'],row['tool'],row['product'],status,row['interface_target'],row['output_target']]
        text += '| '+' | '.join(value.replace('|',' / ').replace('\n',' ') for value in values)+' |\n'
    (ROOT/'docs/INTEGRATION-MATRIX.md').write_text(text,encoding='utf-8')


if __name__ == '__main__':
    main()
