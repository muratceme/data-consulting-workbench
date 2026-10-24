# What do consultants actually do on these platforms?

**English** · [Türkçe](DANISMAN-ARASTIRMASI.tr.md)

Review date: 23 October 2026. Goal: for the site's 23 domains / 207 products, find the work that data consultants on each platform actually sell and deliver, and add what the site was missing. No package was removed.

Method: consulting firms' marketplace listings (Microsoft Marketplace, AWS Marketplace, G-Cloud), job ads, vendor documentation and open-source audit tools were reviewed. Most sources are vendor marketing; they show the scope, not a standard.

## Finding: consulting work fits the same four patterns on every platform

1. **Health check / audit** (2–3 weeks, scored findings report + prioritised roadmap). The platform's own exports are read: VertiPaq + Best Practice Analyzer in Power BI, configuration + waits + indexes in SQL Server, AWR in Oracle, pg_settings + pg_stat_statements + bloat in PostgreSQL, query history + RBAC in Snowflake.
2. **Migration assessment** (inventory + complexity score + effort estimate + wave plan): Tableau→Power BI, QlikView→Qlik Sense, PowerCenter→IDMC, SSIS→ADF/Fabric, Teradata→Snowflake, SAS→Python, Alteryx→dbt, Hive metastore→Unity Catalog, legacy ERP→S/4HANA.
3. **Cost / capacity optimisation**: Databricks system.billing, Fabric Capacity Metrics, BigQuery JOBS, Snowflake credits, Fivetran MAR.
4. **Setup / rule writing**: GX and Soda checks, Purview/Collibra glossaries, MDM matching and survivorship rules, GA4 / tracking plan audits.

## Already covered on the site

Power BI model documentation (.pbit), Tableau inventory, Qlik lineage, LookML inventory, dbt audit and DAG analysis, Airflow DAG audit, ADF and SSIS inventories, Snowflake query history and storage, BigQuery job cost and table design, Redshift table design, PostgreSQL pg_stat_statements and bloat, SQL Server waits / indexes / deadlocks / backups, MySQL slow log, MongoDB profiler, Kafka topic audit and consumer lag, report usage inventory, access review, duplicate records and golden record, KVKK inventory. These match the work in the research; no package needs to be removed.

## Missing work that was added

32 jobs in total, added in four batches (86430ac, d9e93c0, 01dd3f5 and the last batch). All run in tests on their sample data; none were tried on a live platform.

| Domain | Product | Consultant's job | Added package |
|---|---|---|---|
| BI | Power BI Desktop | Model size and cardinality review with VertiPaq Analyzer | `pbi-vertipaq` |
| BI | Power BI Desktop | Tabular Editor Best Practice Analyzer rules | `pbi-bpa` |
| BI | Tableau Desktop | Workbook complexity score for a Tableau→Power BI migration | `tableau-gecis-karmasikligi` |
| BI | QlikView / Qlik Sense | Synthetic key, circular reference and island table audit | `qlik-veri-modeli` |
| BI | Looker | Cleaning up unused fields/explores with System Activity | `looker-kullanilmayan-alanlar` |
| Warehouse | Snowflake | RBAC audit (ACCOUNTADMIN, role hierarchy, PUBLIC grants) | `snowflake-rbac-denetimi` |
| Warehouse | Teradata Vantage | DBQL workload analysis and inventory of syntax to convert | `teradata-is-yuku` |
| Lakehouse | Unity Catalog | Hive metastore → Unity Catalog migration readiness (similar to the UCX assessment) | `uc-gecis-hazirligi` |
| FinOps | Databricks System Tables | system.billing.usage: jobs running on all-purpose compute, untagged usage | `databricks-faturalama` |
| FinOps | Fabric Capacity Metrics | CU overage, throttling windows, top consuming items | `fabric-kapasite` |
| Integration | Fivetran | MAR analysis by connection and table | `fivetran-mar` |
| Integration | PowerCenter | Repository XML inventory and IDMC migration complexity | `powercenter-envanter` |
| Database | Oracle Database | AWR report: load profile, top wait events, most expensive SQL | `oracle-awr` |
| Database | PostgreSQL | pg_settings configuration audit and wraparound risk | `pg-yapilandirma` |
| Database | SQL Server | Server and database configuration audit (similar to sp_Blitz) | `sqlserver-yapilandirma` |
| Database | MongoDB Atlas | Unused and duplicate indexes with $indexStats | `mongodb-indeks-kullanimi` |
| Quality | GX / Soda | Generating a GX expectation suite and a SodaCL checks file from a profile | `gx-soda-kurallari` |
| Governance | Purview / Collibra | Converting a business glossary into an import file and validating it | `sozluk-ice-aktarim` |
| MDM | MDM tools | Match threshold calibration (over/under-merging) | `eslestirme-kalibrasyonu` |
| Product analytics | GA4 | BigQuery export audit (empty days, missing parameters) | `ga4-export-denetimi` |
| Product analytics | Segment / Amplitude | Tracking plan compliance | `tracking-plan-uyumu` |
| Migration | SAP Migration Cockpit | Pre-load validation of a filled template | `sap-sablon-dogrulama` |
| Data science | SAS Viya | SAS code inventory and Python migration complexity | `sas-kod-envanteri` |
| Data science | Alteryx Designer | .yxmd workflow inventory and dbt/Python migration complexity | `alteryx-envanter` |
| ML | MLflow | Experiment and model registry audit | `mlflow-denetimi` |
| Streaming | Kinesis / Event Hubs | Shard / throughput unit sizing from throughput | `akis-kapasite` |
| Orchestration | Airflow | Pool saturation, queue wait and zombie tasks | `airflow-havuz-analizi` |
| Analytics engineering | dbt Core | dbt_project_evaluator modelling rules (source fan-out, rejoining, etc.) | `dbt-modelleme-kurallari` |
| BI | Power BI Service | Tenant inventory and governance with the Scanner API | `pbi-tarayici-envanteri` |
| Database | Redis | Memory, eviction and persistence checks from INFO output | `redis-saglik` |
| Warehouse | ClickHouse | Part and partitioning analysis with system.parts | `clickhouse-parca-analizi` |
| Time series | InfluxDB | Series cardinality analysis | `influxdb-kardinalite` |

## Second round: from-scratch setup work

The first round mostly added work that audits and improves an existing setup. Consultants also build the same platforms from scratch for clients: a model and dashboard from given tables, a role/warehouse/budget layout in an empty account, the first load pipeline, the first DAG. The site already had single-file report templates (`qlik-rapor-sablonu`, `pbi-rapor-sablonu`, `tableau-rapor-sablonu`, `lookml-rapor-sablonu`, `dbt-proje-sablonu`, `excel-rapor-sablonu`, `pptx-rapor-sablonu`); what was missing was building a related model from several tables and the first setup of platforms outside BI. This round added 14 code blocks and 3 agents. No new web research was done in this round; the jobs were chosen from the setup services described in the first round's sources and the steps in the platforms' own setup documentation.

| Domain | Product | From-scratch setup job | Added package |
|---|---|---|---|
| BI | Qlik Sense | App with a related data model, master items and sheets from a folder of tables (no synthetic keys/loops) | `qlik-cok-tablolu-uygulama` |
| BI | Power BI Desktop | Star model, many-to-one relationships, calendar, DAX measures and a two-page report (PBIP) | `pbi-yildiz-model-projesi` |
| BI | Looker | One view per table, an explore with many_to_one joins, a LookML dashboard | `lookml-cok-tablolu-proje` |
| BI | Tableau Desktop | Data source with left joins, KPI/trend/breakdown sheets and a dashboard | `tableau-cok-tablolu-calisma-kitabi` |
| BI | Superset | Import bundle with a virtual dataset, charts and filters | `superset-pano-paketi` |
| BI | Metabase | SQL cards and a script that sets up a collection + dashboard through the API | `metabase-pano-kurulumu` |
| BI | Report requirements | Agent that designs a multi-table dashboard on request and builds the projects above | `pano-kurucu` |
| Database | PostgreSQL / SQL Server | 3NF tables, keys, DDL and load from a flat export | `veritabani-normalizasyon` |
| Transformation | dbt Core | Multi-table source → staging → dim/fct, relationship tests | `dbt-yildiz-projesi` |
| Warehouse | Snowflake | Databases, access/functional roles, warehouses, resource monitors, service users | `snowflake-hesap-kurulumu` |
| Lakehouse | Unity Catalog | Catalogs, schemas, group and service principal grants | `unity-catalog-kurulumu` |
| Warehouse | BigQuery | Datasets, IAM and service accounts in environment projects (Terraform) | `bigquery-terraform-kurulumu` |
| Warehouse | Snowflake / UC / BigQuery | Agent that turns a free-text setup request into an access plan and builds it | `ambar-kurucu` |
| Integration | Azure Data Factory | Metadata-driven pipeline with a control table, full/incremental copy and watermark | `adf-metadata-pipeline` |
| Orchestration | Airflow | DAG from a task list, pools, critical path and deadline check | `airflow-dag-kurulumu` |
| Orchestration | Airflow | Agent that turns a free-text data pipeline into a validated DAG | `pipeline-kurucu` |
| ML | scikit-learn | Project with leakage removal, feature selection, training and prediction scripts | `sklearn-proje-kurulumu` |

All run in tests on their sample data. The generated projects (QVF, PBIP, LookML, TWB, Superset bundle, Metabase script, Snowflake/UC SQL, Terraform, ADF JSON, DAG, ML scripts) were not tried by opening or running them in the target applications.

## Third round: sending the setup to the account through the API

Each program's official API documentation was checked to see whether the files produced by the setup blocks can be installed on the platform through the API. The ten packages implemented and the limits the API allows:

| Program | Possible with the API | Not possible with the API (what the package does) | Package |
|---|---|---|---|
| Qlik Cloud | Uploading Data Files, creating an app, writing the load script (`/apps/{id}/scripts`), reload, data model metadata | Master items and sheets need the Engine API (websocket); left to qlik-cli through build.cmd | `qlik-cloud-build` |
| Fabric Lakehouse | Creating a lakehouse, writing files to OneLake, Delta tables with Load Table, table list | Load Table is not supported on schema-enabled lakehouses | `fabric-lakehouse-build` |
| Power BI Service | Creating and reading back a semantic model with a TMDL definition | Sources disagree on the `definition.pbir` format in the report (PBIR) creation API; the report is published from Desktop. Credentials for data refresh are bound on the platform | `fabric-semantic-model` |
| Metabase | Collection, SQL cards, dashboard and card layout (`X-API-Key`) | The card layout endpoint changed between versions; the package uses the 0.47+ format | `metabase-dashboard` |
| Looker | Reading explore fields, queries, creating a user-defined dashboard and elements | LookML files are not written through the API; they are deployed with Git | `looker-dashboard` |
| Snowflake | Every DDL/GRANT statement through the SQL API, verification with SHOW GRANTS | Switching roles depends on the token's scope | `snowflake-setup` |
| Unity Catalog | Creating catalogs/schemas, adding and reading grants, ownership | Creating groups and service principals is in the account API; they must already exist | `unity-catalog-setup` |
| BigQuery | Service accounts, datasets with access lists | The project IAM policy (jobUser) needs read-modify-write; the package does not touch it | `bigquery-setup` |
| Azure Data Factory | Creating linked services, datasets, pipelines and triggers | Triggers are not started, no data is moved | `adf-solution` |
| Airflow | Import errors, DAG and task list, unpause | The REST API does not upload DAG files; the file is placed with Git/CI | `airflow-activate` |

Not implemented: Superset's import endpoint needs a session cookie and a CSRF token; a workbook published to Tableau Cloud cannot connect to a local CSV (an extract or a published data source is needed). For both, the local blocks keep producing files. The packages were tested against a local contract server; not tried on a live account.

Sources: [Qlik data files](https://qlik.dev/manage/data-files/create-datafiles/), [Qlik load script REST](https://community.qlik.com/t5/Integration-Extension-APIs/Qlik-Cloud-Change-Load-script-through-REST-API/m-p/2423987),
[Fabric lakehouse API](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-api), [Fabric create semantic model](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/create-semantic-model),
[Fabric report creation discussion](https://community.fabric.microsoft.com/t5/Developer/Fabric-Create-Reports-model-via-Microsoft-Fabric-API/m-p/4719497),
[Metabase API keys](https://www.metabase.com/docs/latest/people-and-groups/api-keys), [Metabase card layout](https://discourse.metabase.com/t/how-to-add-dashboard-tabs-programmatically-in-metabase-0-49-which-endpoints-to-use/255084),
[Looker create_dashboard_element](https://cloud.google.com/looker/docs/reference/looker-api/4.0/25.14/methods/Dashboard/create_dashboard_element),
[Unity Catalog update permissions](https://docs.databricks.com/api/uc-grants/v1/update-permissions), [Airflow API client](https://apache.googlesource.com/airflow-client-python).

## Fourth round: API work beyond setup

The third round's packages only sent the local setup output to the account. This round added three more groups of work the APIs allow (33 packages):

| Group | Program and package | API limit / how the package behaves |
|---|---|---|
| New platform setup | Confluent Cloud `confluent-topics`, dbt Cloud `dbt-cloud-job`, Databricks `databricks-job-create`, Prefect `prefect-deployment`, Dagster `dagster-schedule`, Airbyte `airbyte-connection`, Fivetran `fivetran-schema` | Dagster definitions live in code, so the API only verifies and starts schedules; Prefect updates a deployment with the same name, so the name is checked to be free first; Airbyte/Fivetran source credentials never go into the package |
| Catalog and quality | Purview `purview-glossary`, OpenMetadata `openmetadata-glossary`, DataHub `datahub-glossary`, Monte Carlo `montecarlo-rules`, Soda Cloud `soda-scan` | The Purview hierarchy cannot be set with a documented API field, so the parent term is linked with an `isA` relation; stewards are added only with an Entra object ID. The Soda Cloud API does not write check definitions; it only runs a defined scan |
| Product analytics and vectors | GA4 `ga4-admin`, Pinecone `pinecone-index`, Qdrant `qdrant-collection` | GA4 custom definitions cannot be deleted, only archived; quotas are checked before writing. The BigQuery link is on the v1alpha endpoint |
| Live audits | Power BI `powerbi-scan`, Snowflake `snowflake-usage`, Databricks `databricks-cost`, Qlik `qlik-reload-history`, Tableau `tableau-usage`, Airflow `airflow-history`, ADF `adf-history` | Read only; the Scanner output is the input of the local `pbi-tarayici-envanteri` block. Databricks amounts are list prices |
| Maintenance and publishing | Power BI `powerbi-deploy`, `powerbi-refresh-schedule`, `powerbi-rls-members`, `powerbi-workspace-access`; Qlik `qlik-publish`, `qlik-reload-schedule`, `qlik-space-access`; Tableau `tableau-permissions`, `tableau-extract-schedule`; Snowflake `snowflake-grant-sync`; Unity Catalog `uc-grant-sync` | The Power BI REST API does not grant RLS membership; the package adds members to the TMDL role file through the Fabric definition API (try it in a test workspace first on a live model). Access packages only add; different permissions and extra rights are reported, never revoked |

All packages were tested against a local contract server; not tried on a live account.

Sources: [Confluent Topic v3](https://docs.confluent.io/cloud/current/api.html#tag/Topic-(v3)), [Schema Registry API](https://docs.confluent.io/platform/current/schema-registry/develop/api.html),
[dbt Cloud API v2](https://docs.getdbt.com/dbt-cloud/api-v2), [Databricks jobs/create](https://docs.databricks.com/api/workspace/jobs/create), [Prefect deployments](https://docs.prefect.io/v3/api-ref/rest-api/server/deployments/create-deployment),
[Dagster GraphQL](https://docs.dagster.io/guides/operate/graphql), [Airbyte createConnection](https://reference.airbyte.com/reference/createconnection), [Fivetran schema config](https://fivetran.com/docs/rest-api/api-reference/connection-schema/connection-schema-config),
[Purview Create Term](https://learn.microsoft.com/en-us/rest/api/purview/datamapdataplane/glossary/create-term), [Soda Cloud scans](https://docs.soda.io/soda-cloud-api-v1/scans),
[GA4 Admin API](https://developers.google.com/analytics/devguides/config/admin/v1/rest), [Pinecone create_index](https://docs.pinecone.io/reference/api/2025-04/control-plane/create_index), [Qdrant collections](https://api.qdrant.tech/api-reference/collections/create-collection),
[Power BI Scanner](https://learn.microsoft.com/en-us/rest/api/power-bi/admin/workspace-info-post-workspace-info), [Power BI deployAll](https://learn.microsoft.com/en-us/rest/api/power-bi/pipelines/deploy-all),
[Fabric updateDefinition](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/update-semantic-model-definition), [TMDL](https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview),
[Qlik reload-tasks](https://qlik.dev/apis/rest/reload-tasks/), [Tableau permissions](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_permissions.htm), [Databricks billing tables](https://docs.databricks.com/aws/en/admin/system-tables/billing).

## Sources

- Power BI health check listings: [Protiviti](https://appsource.microsoft.com/en/marketplace/consulting-services/roberthalf_protiviti.powerbiperformance), [Sword](https://marketplace.microsoft.com/ja-jp/marketplace/consulting-services/swordgroup1676561891488.powerbi_healthcheck_3wk), [Coeo](https://coeo.com/wp-content/uploads/2024/09/Power-BI-Health-Check-.pdf), [endjin tool overview](https://endjin.com/blog/performance-optimisation-tools-for-power-bi)
- Power BI governance: [Unused artifacts API](https://powerbi.microsoft.com/ar-sa/blog/power-bi-unused-artifacts-api-preview)
- Tableau: [Atrium](https://atrium.ai/services/salesforce-consulting/tableau/server-health-check/), [InterWorks Platform 360](https://community.curator.interworks.com/platform360), [Resource Monitoring Tool](https://help.tableau.com/current/server/en-us/rmt-content-pages.htm), [MAQ Tableau→Power BI assessment](https://marketplace.microsoft.com/en-us/product/maqsoftware.tableau_to_microsoft_powerbi_migration_assessment)
- Qlik: [app performance and synthetic keys](https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/Apps/app-performance.htm)
- Looker: [finding unused fields](https://developers.looker.com/api/tutorials/finding-unused-fields), [Henry](https://pypi.org/project/henry), [Looker health tools](https://discuss.google.dev/t/introducing-looker-health-tools-in-mcp-toolbox/283239)
- Snowflake: [Intricity health check](https://intricity.com/services/snowflake-health-check), [Crayon](https://aws.amazon.com/marketplace/pp/prodview-udqpmpxjxyha4), [Operational excellence](https://www.snowflake.com/en/developers/guides/operational-excellence/)
- Teradata: [Datafold guide](https://datafold.com/resources/teradata-to-snowflake-migration), [SnowConvert](https://mobilize.net/products/database-migrations/snowconvert/teradata-get-started), [Aegis](https://www.aegissofttech.com/snowflake-services/migration/teradata-to-snowflake)
- Databricks: [UCX](https://docs.databricks.com/aws/data-governance/unity-catalog/ucx), [UCX blog](https://databricks.com/blog/automating-unity-catalog-upgrade-workflows-ucx), [cost best practices](https://docs.databricks.com/aws/lakehouse-architecture/cost-optimization/best-practices)
- dbt: [dbt_project_evaluator](https://github.com/dbt-labs/dbt-project-evaluator), [Datatonic dbt health check](https://datatonic.com/partners/dbt/dbt-health-check-americas/)
- Fabric: [throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling), [Metrics app compute](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app-compute-page)
- Fivetran: [tracking and reducing usage](https://fivetran.com/docs/core-concepts/usage-based-pricing/tracking-and-optimizing-usage)
- Informatica: [CTI PowerCenter→IDMC](https://ctidata.com/wp-content/uploads/2023/04/Informatica-PowerCenter-to-IDMC-Migration.pdf), [Informatica upgrade readiness](https://success.informatica.com/success-accelerators/cdipc--platform-compatibility---upgrade-readiness.html)
- ADF/SSIS: [SSIS migration](https://learn.microsoft.com/en-us/azure/data-factory/scenario-ssis-migration-overview), [ADF→Fabric assessment](https://community.fabric.microsoft.com/t5/Fabric-Updates-Blog/Assessing-Your-Azure-Data-Factory-for-Migration-to-Fabric-Data/ba-p/5172414)
- SQL Server: [Denny Cherry 3-day health check](https://azuremarketplace.microsoft.com/en-us/marketplace/consulting-services/dennycherry_associates_consulting.sql_health_check_nq)
- Oracle: [Oracle Healthcheck example](https://www.oracle.com/oce/dc/assets/CONT652A3C4564DA4F888292CAEB5A523BA0/native/oracle-healtcheck-service-example.pdf), [IBM AWR guide](https://www.ibm.com/downloads/cas/WNK5D2QZ)
- PostgreSQL: [postgres.ai checkup](https://v2.postgres.ai/docs/monitoring/checkup), [pg-healthcheck](https://www.cybrosys.com/research-and-development/postgres/how-to-use-the-pg-healthcheck-tool-in-postgresql)
- MongoDB: [slow queries](https://www.mongodb.com/docs/v8.2/troubleshooting/slow-queries-in-production/), [performance checklist](https://delbridge.solutions/mongodb-performance-checklist)
- Data quality: [Soda vs GX comparison](https://www.dataexpert.io/blog/soda-vs-great-expectations-data-quality-tools)
- Governance: [Purview and Collibra lessons](https://dzone.com/articles/data-catalog-azure-purview-collibra-lessons)
- MDM: [Informatica→Reltio migration plan](https://digihub.mastechdigital.com/assets/Reltio/ReltiLabs/Platform%20Migration/RELTIO_Informatica_MDM_to_Reltio_Migration.docx)
- MLOps: [Blackthorn framework](https://blackthorn-vision.com/blog/mlops-consulting-services/)
- GA4: [BigQuery export audit](https://www.optizent.com/?p=7863), [GA4 audit checklist](https://piotrlitwa.com/articles/en/ga4-audit-checklist.html)
- SAP: [data migration job ad](https://builtin.com/job/sap-data-migration-consultant/4747735)
- SAS: [RiskSpan](https://riskspan.com/sas-to-python-migration/), [Hexaware assessment](https://marketplace.microsoft.com/de-lu/product/hexawaretechnologiesltd-nj1734355056208.modernize-sas-to-pyspark-assessment)
- Alteryx: [rewriting with dbt](https://docs.getdbt.com/blog/framework-refactor-alteryx-dbt), [Lakebridge Alteryx analysis](https://community.databricks.com/t5/community-articles/stop-translating-alteryx-boxes-a-lakebridge-assisted-test-driven/td-p/163424)
- Kafka: [consumer lag](https://redpanda.com/guides/kafka-performance-kafka-consumer-lag)
- Airflow: [airflow-diagnostics](https://pypi.org/project/airflow-diagnostics-mcp/)
- Business Central: [configuration packages](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/administration/apply-company-configuration-packages)
