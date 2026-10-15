# Kesinti sonrası mevcut çalışmayı takip etme

## Güncel paketten tek komutla devam

Aşağıdaki sekiz platformun güncel başlatma veya takip paketinde, önceki config kaynak adreslerini koruyarak kayıt dosyasını gösterebilirsiniz. Eski ZIP’lerde bu seçenek olmayabilir; güncel paketi indirin.

```sh
python native.py --plan --resume-from output/run-001/run-state.json
python native.py --execute --resume-from output/run-001/run-state.json --output output/continued-001
```

Çalıştırıcı işi otomatik olarak ilgili monitor işlemine çevirir. Kaydın kaynak kimliklerini config ile karşılaştırır; yalnızca çalışma kimliklerini aktarır. Kayıttaki sunucu adresi, işlem adı veya anahtarlar kullanılmaz. Eksik/uyuşmayan kimlikte yeni iş başlatmadan durur. Workspace/server adresi orijinal config’te korunmalıdır; eski kayıtlar her platformun sunucu adresini içermediğinden adres değişikliğini kayıt üzerinden doğrulamak mümkün değildir. Bu komut Fabric, Qlik, SAP gibi tablodaki sekiz akışın dışındaki işlemlerde desteklenmez.

## Kimlikleri elle aktarma

Başlatma paketini yeniden çalıştırmak yeni bir platform işi oluşturabilir. Aşağıdaki ayrı takip paketleri yalnızca GET kullanır. Önceki `run-state.json` dosyasını koruyun; kimlikleri takip paketinin `config.json` dosyasına aktarın, geçerli erişimi yerel `.env` içine koyun ve yeni/boş bir çıktı klasörü seçin.

| Paket | Önceki kayıttan alınacak kimlikler | Ek yapılandırma |
|---|---|---|
| `databricks-monitor` | `job_id`, `run_id` | Aynı `workspace_url` |
| `airbyte-monitor` | `connection_id`, `job_id` | Airbyte Cloud |
| `adf-monitor` | `pipeline_name`, `run_id` | Aynı subscription, resource group, factory |
| `airflow-monitor` | `dag_id`, `dag_run_id` | Aynı `server`, `api_version` |
| `dbt-monitor` | `account_id`, `job_id`, `run_id` | Aynı bölgesel `server`; account/job metin, run pozitif tamsayı |
| `prefect-monitor` | `deployment_id`, `flow_run_id` | Aynı account/workspace içeren `api_url` |
| `dataform-monitor` | `compilation_result`, `workflow_invocation` | Tam kaynak adları |
| `powerbi-monitor` | `workspace_id`, `dataset_id`, `refresh_id` | UUID değerleri |

```sh
python native.py --plan
python native.py --execute --output output/continued-001
```

Yaklaşık dört dakika sonunda yerel takip zaman aşımına uğrayabilir; platform işi iptal edilmez. Aynı kimliklerle yeni bir çıktı klasöründe tekrar takip edilebilir. Başarısız veya iptal edilmiş işler yeniden başlatılmaz ve başarı dosyası oluşturulmaz. dbt başarılı olduğunda platformun `run_results.json` artifact’ını da indirir.

Başlatma yanıtı kaybolduğu için iş kimliği yoksa önce platform geçmişinden bulun. `request_pending` kaydı işlemin başarısız olduğunu kanıtlamaz. API anahtarını veya parametre dosyasını kurtarma kaydına eklemeyin. Bu akış otomatik rollback, kimlik bilgisi yenileme veya tüm ürünler için genel resume değildir.

Doğrulama: yerel HTTP sözleşme testleri; canlı tenant kabulü henüz yapılmadı.

Çalışma kaydı güncellemeleri geçici dosya, flush/fsync ve atomik replace kullanır. Yazma/replace başarısız olduğunda önceki kayıt korunur; bu mekanizma her dosya sistemi veya ani güç kaybında mutlak dayanıklılık garantisi değildir.
