# Datawork — application-specific data automations

**English** · [Türkçe](README.tr.md)

Two kinds of packages for data work: **code blocks** (the same input always gives the same result; platform API runners and local tools that never go online) and **AI agents** (the reasoning step runs in a model, while parsing and checking run in code). Users pick the kind on the site. The site is only a browse-and-download interface; it collects no keys and no data.

## Current status

24 October 2026: 23 domains / 207 product contexts. **580 downloadable packages**: 82 platform API packages (81 code blocks + the BigQuery agent; two Tableau operations are listed separately under Cloud and Server) and 498 local packages (380 code blocks + 118 agents). In total **461 code blocks and 119 agents**; every one of the 23 domains has at least one downloadable package. The other 372 tasks are **planned integrations** and have no download links.

Package contents (READMEs inside the ZIPs, CLI messages, output column names) are in Turkish, because the blocks target Turkish data work (e-Fatura, KVKK, TDHP chart of accounts, Turkish calendars). Commands and file formats are the same in any language.

### Local code blocks (no internet, Python standard library)

| Package | Input | Output |
|---|---|---|
| Data profile | CSV/XLSX | Types, blanks, ranges, duplicates, traces of personal data |
| Quality check | CSV/XLSX + rules.json | Violation list (Excel row numbers), `--strict` exit code |
| Table reconciliation | Two CSV/XLSX files + key | Rows only in source/target, field differences, totals |
| SQL lineage | Folder of .sql files | Source/intermediate/final tables, risky statements, Mermaid |
| Power BI model documentation | .pbit / model.bim | Tables, measures, relationships, model risks |
| Tableau inventory | .twb/.twbx | Data sources, calculated fields, unused fields |
| Qlik lineage | .qvs | Tables, sources, STORE/DROP, warnings, Mermaid |
| dbt audit | manifest.json | Test/documentation coverage, layer rules, freshness |
| KVKK masking | CSV/XLSX | Pseudonym (salted HMAC) / partial / delete; personal data embedded in note fields; re-scan |
| Schema diff | Two DDL files/folders | Added/removed tables and columns, type narrowing, NOT NULL, likely renames, ALTER draft |
| Date table | Date range + holiday file | Turkish calendar dimension, public holidays, working-day sequence, fiscal year; CSV + SQL |
| File merge | Folder of CSV/XLSX | One table + source file; schema drift, decimal separator differences, duplicate rows |

### Single-purpose local code blocks (368)

Each block does one job and is registered with `islem.op(...)` in `library/blocks/<category>.py`. The registration carries the catalog text, command-line options, sample data and the **hand-verified expected result** on that sample. The `main.py` in a package is a two-line launcher; the shared runner (`islem.py`) reads the input, changes no files, never goes online, and writes `sonuc.csv`, `rapor.md` and `ozet.json` under `output/`.

| Category | Blocks | Examples |
|---|---|---|
| Data cleaning and transformation (`temizlik.py`) | 27 | whitespace/invisible characters, snake_case, Turkish upper/lower case, repairing broken characters (Ã¼), date and number formats, filters and formulas (safe expressions), JOIN, VLOOKUP, pivot/unpivot, grouping, file splitting, encoding |
| Data validation (`dogrulama.py`) | 16 | Great Expectations suite and SodaCL checks from a data profile (tried on a new file), TCKN, VKN, IBAN (+bank), phone (E.164), e-mail typo suggestions, licence plates, 81 provinces, postal code–province, card Luhn + masking, referential integrity, key candidates, A→B consistency, pattern profile, checksums, row rules |
| Accounting and finance (`finans.py`) | 18 | TDHP trial balance check, summary balance sheet/income statement, journal audit, receivables ageing, FIFO payment matching, bank reconciliation, MT940, reading and generating e-Fatura (UBL-TR) (sales, withholding, return, exemption), VAT, currency conversion, Benford, duplicate payments, depreciation, loan schedule, inventory cost (FIFO/average), expense classification, cash flow, UBL-TR e-Fatura/e-Arşiv XML from invoice lines |
| Master data (`anaveri.py`) | 13 | MDM match threshold calibration (auto-merge / review / reject zones), similar company clustering, legal name standardisation, address parsing, golden record + source trail, first/last name split, hierarchy (cycles/orphans), mapping to reference code lists, record completeness/validity score, shared tax ID/IBAN/phone, hierarchy flattening, master data change audit (IBAN and contact changed together), product attributes against category templates |
| Statistics (`analiz.py`) | 17 | descriptive statistics, correlation, outliers, histogram, chi-square, A/B test, sample size, regression, seasonality, forecast with backtest, series anomalies, quantiles, scaling, Pareto, bootstrap confidence interval, normality (Jarque-Bera), Mann-Whitney U |
| Sales and customers (`musteri.py`) | 16 | GA4 BigQuery export audit, Segment/Amplitude tracking plan compliance, RFM, cohorts, ABC, XYZ, basket analysis, churn risk, CLV, funnel, sessions, product performance, period comparison, reorder point, feature adoption, NPS with margin of error |
| SQL (`sqlgen.py`) | 10 | CSV→CREATE TABLE/INSERT (6 dialects), MERGE/UPSERT, SCD2, safe formatter, lint, DDL→data dictionary/ER (Mermaid), dialect translation, data tests |
| File formats (`bicim.py`) | 9 | nested JSON, JSON Lines, XML, fixed width, Excel sheets, CSV→XLSX, access logs, HTML report |
| BI generators (`bigen.py`) | 7 | DAX time intelligence, Power Query M, BigQuery schema, Qlik LOAD, Power BI RLS, KPI traffic light, dbt skeleton |
| e-Documents (`ebelge.py`) | 1 | UBL-TR e-İrsaliye XML from shipment lines (plate, driver TCKN, postal code, GİB unit code checks); shared unit code mapping for e-Fatura and e-İrsaliye |
| Dimensional model (`yildiz.py`) | 1 | star schema from a flat file: dimensions with surrogate keys and an “unknown” row, date dimension, fact table; DDL + load SQL (SQLite, Postgres, SQL Server, Snowflake), verified by running it in SQLite |
| Qlik and Power BI model (`bimodel.py`) | 5 | time intelligence: Qlik set analysis master measures and a Power BI calculation group (TMDL; YTD, QTD, MTD, previous month, last year, last 12 months, fiscal year); row-level security: Qlik Section Access and a Power BI TMDL role (with the expected view per user); Qlik incremental QVD load (insert, update, delete) |
| Ready-made report templates (`rapor.py`) | 9 | from one CSV/Excel file: Qlik Sense app (script, master items, report sheet), Power BI project (PBIP: TMDL model + PBIR report), Tableau workbook (.twb), Looker project (view, explore, dashboard), Excel report with formulas and charts, PowerPoint deck, working dbt project, single-file HTML dashboard, Excel report with a real PivotTable |
| Location and sensors (`zaman.py`) | 7 | InfluxDB series cardinality (tags written like IDs), coordinate checks, nearest point, threshold breaches, data gaps, resampling, business-hours SLA |
| Location (`konum.py`) | 8 | assigning points to GeoJSON regions (with holes/multipolygons), vehicle GPS route, stop and jump summary, distance matrix and nearest neighbour, geofence entry/exit, GPS stop detection, geohash grid density, store catchment, DBSCAN location clustering |
| Budgeting and planning (`planlama.py`) | 13 | budget vs actual, budget phasing, year-end forecast, scenarios, consolidation + intercompany elimination, call-centre staffing (Erlang C), price–volume–mix variance, multi-product break-even, formula-based sensitivity (tornado), headcount cost plan, forecast accuracy (WAPE, bias, FVA), capacity planning (utilisation, extra capacity), month-to-date sales target tracking |
| Orchestration (`orkestrasyon.py`) | 8 | Airflow pool saturation and queue wait, cron explanation + next runs, Airflow DAG static audit (ast), GitHub Actions security audit, job dependencies and critical path, schedule concurrency and self-overlapping jobs, maintenance window clashes, retries and flaky tasks |
| Integration (`entegrasyon.py`) | 4 | ADF/Synapse pipeline inventory (Mermaid), SSIS .dtsx package inventory, PowerCenter repository inventory and IDMC migration complexity, Fivetran MAR analysis (expensive and unread tables) |
| Lakehouse (`lakehouse.py`) | 8 | Delta _delta_log summary, Iceberg metadata summary, small-file analysis in object storage, Delta VACUUM estimate, partition path consistency and missing dates, medallion layer rules, OPTIMIZE/VACUUM maintenance plan, Hive metastore → Unity Catalog migration readiness (SYNC / DEEP CLONE / CTAS, wave plan and SQL) |
| Databases (`veritabani.py`) | 19 | Redis INFO health check, Oracle AWR report (AAS, wait events, expensive SQL), PostgreSQL configuration and wraparound, SQL Server configuration audit (sp_Blitz-like), MongoDB $indexStats unused/redundant indexes, SQL Server plans (.sqlplan), wait statistics, index usage, deadlock XML; PostgreSQL EXPLAIN JSON, pg_stat_statements, table bloat/vacuum; MySQL slow query log, SQL Server missing index suggestions, PostgreSQL lock wait tree, backup history audit, MongoDB profiler query shapes, long transactions and idle in transaction, duplicate and covered indexes |
| Warehouse and FinOps (`maliyet.py`) | 16 | Databricks system.billing (jobs running on all-purpose compute, weekend clusters, untagged usage), Fabric capacity throttling windows and SKU suggestion, Snowflake query history and credits, BigQuery job cost, cloud bill summary, month-end cost forecast, mandatory tag compliance, commitment (reserved/savings plan) suggestion, rightsizing, shared cost allocation (showback), unit cost trend, storage tier suggestion, idle resources, unused licences, egress cost |
| Warehouse design (`ambar.py`) | 9 | ClickHouse system.parts (too many parts, small inserts, over-fine partitioning), Snowflake RBAC audit (ACCOUNTADMIN, role hierarchy, PUBLIC grants, MANAGE GRANTS), Teradata DBQL workload and inventory of syntax to convert, Redshift distribution/sort/compression audit, Snowflake Time Travel/Fail-safe/clone storage, BigQuery partitioning/clustering audit, unqueried tables, SCD2 integrity (overlaps, gaps, multiple current rows), Snowflake clustering depth |
| Migration (`gecis.py`) | 14 | SAP Migration Cockpit template pre-validation, field mapping validation, table row count reconciliation, target length/encoding risk, keyless column profile comparison, T-SQL/Oracle → PostgreSQL query translation, migration waves by dependency and volume, row reconciliation by key, identity/sequence collision check, target type compatibility (overflow, rounding, invalid dates), duration estimate per wave, time-zone shift, blank/NULL conversion, broken Turkish character (mojibake) detection and repair |
| dbt (`donusum.py`) | 6 | dbt_project_evaluator modelling rules (direct joins to sources, source fanout, rejoining, root models), manifest.json test and documentation coverage, source freshness summary, incremental model risks (unique_key, filter, lookback), incremental model template (unique_key, lookback), snapshot (SCD2, closing deleted rows) |
| ML (`ml.py`) | 13 | MLflow experiment and model registry audit, classification and regression metrics, PSI data drift, leak-free splitting, leakage check, threshold choice by F1/cost, calibration (Brier, ECE), WoE/IV, multi-class metrics, lift/gain and KS, group fairness (4/5 rule, TPR/FPR gap), prediction interval coverage (Winkler) |
| GenAI / RAG (`rag.py`) | 14 | heading-aware chunking (JSONL), near-duplicate documents, tokens/cost, prompt injection scan, hit@k/MRR, answer evaluation (EM, token F1), LLM log masking, LLM usage/cost/latency analysis, prompt template variable check, source citation verification, user feedback (Wilson lower bound), embedding vector check, retrieval source coverage, prompt version comparison (sign test) |
| Streaming (`akis.py`) | 14 | Kinesis shard / Event Hubs TU sizing, JSON Schema from events, Avro compatibility, event latency/watermark, Kafka consumer lag, Kafka topic replication audit, duplicates/conflicting copies/sequence gaps, Debezium CDC summary, event-time window totals and late events, message size distribution, partition key skew, DLQ error groups, outbox ↔ publish reconciliation, Schema Registry compatibility audit |
| Privacy and governance (`yonetisim.py`) | 5 | personal data inventory of a folder, retention periods, KVKK 30-day request tracking, access review, ownership coverage |
| Privacy (`gizlilik.py`) | 9 | k-anonymity and l-diversity, anonymisation by generalisation, İYS consent check, cross-border transfer inventory, 72-hour breach notification tracking, cookie inventory audit, folder search for a data subject request, realistic-looking personal data in test environments, masking verification (leaks, consistency, collisions) |
| Governance (`katalog.py`) | 12 | converting a business glossary to a Purview import CSV (hierarchy and cycle checks), business glossary quality, lineage and impact analysis from SQL scripts, segregation of duties (SoD), classification consistency, data contracts, access request audit (self-approval, expired access), BI report usage inventory, propagation of classifications along lineage, diff of two permission lists, policy exception register, unused permissions (least privilege) |
| Observability (`gozlem.py`) | 12 | freshness SLA, volume anomalies, schema changes, NULL rate jumps, job run history (success, p95, slowdown), log error signatures, quality test scorecard (new/flaky/chronic), delivery SLA compliance, row loss between layers, alert noise (MTTA/MTTR), end-to-end data latency (p50/p95, SLA), persistent level shifts (change points) |
| BI and dbt translation/analysis (`bi_ceviri.py`) | 6 | dbt run_results and DAG, LookML inventory, Power BI measure dependencies, Tableau→DAX, Qlik→DAX |
| Analytics code inventory (`kod_envanteri.py`) | 2 | migration complexity and effort for SAS programs (PROC → Python equivalent, macros, LIBNAME, dynamic code) and Alteryx workflows (tool → dbt/SQL equivalent, password masking) |
| BI health check (`bi_saglik.py`) | 6 | Power BI Scanner API tenant inventory, Power BI VertiPaq column size and cardinality, Best Practice Analyzer rules, Tableau→Power BI migration complexity score and effort, Qlik data model (synthetic keys, loops, island tables), unused fields/explores/views in Looker |
| BI from scratch (`kurulum.py`) | 6 | multi-table model from a folder of tables (fact + dimensions) whose keys and relationships are found and verified in the data; Qlik Sense app (with synthetic key and loop checks), Power BI star model project (PBIP), Looker project (explore with joins), Tableau workbook, Superset import bundle, Metabase SQL cards and API setup script |
| Platforms from scratch (`platform_kurulum.py`) | 8 | normalised database from a flat file (3NF, verified by re-joining in SQLite), multi-table dbt star project, Snowflake account from a list of teams/layers, Unity Catalog and BigQuery (Terraform) setup, Azure Data Factory metadata-driven load pipeline, Airflow DAG, scikit-learn project skeleton |

**From-scratch setup blocks.** Local blocks are offline. For ten platforms (Qlik Cloud, Fabric Lakehouse, Power BI Service, Metabase, Looker, Snowflake, Unity Catalog, BigQuery, ADF, Airflow) there are also platform packages that set up the same output in the account through the API: the `kurulum/` folder in the package is the block's sample output, which you replace with your own; if a resource with the same name exists in the target the package creates nothing and stops, and at the end it reads the result back from the platform and compares. Besides maintenance and audit blocks, these jobs build a platform from nothing: a related data model and dashboard from a folder of tables (Qlik, Power BI, Looker, Tableau, Superset, Metabase), a database from a flat file, a dbt project, Snowflake / Unity Catalog / BigQuery account setup, an ADF load pipeline, an Airflow DAG and an ML project. Model blocks verify relationships in the data (is the key unique in the parent table, are there unmatched values in the child table); a relationship that would create a second path is cut by renaming the field in Qlik and by an inactive relationship in Power BI. Superset and Metabase SQL, dbt models and the normalised database are run in SQLite and the numbers compared with a Python calculation; for access setups, the effect of the generated GRANTs is computed and compared with the requested access. The generated projects were not tested by opening them in the target applications.

**Maintenance and live audits through APIs.** Beyond setup there are three more groups of platform packages: programs that have an API but no setup package yet (Confluent, dbt Cloud, Databricks jobs, Prefect, Dagster, Airbyte, Fivetran, Purview/OpenMetadata/DataHub glossaries, Monte Carlo, Soda Cloud, GA4, Pinecone, Qdrant); live audits that read the platform directly instead of an exported file (Power BI Scanner, Snowflake Account Usage, Databricks System Tables, Qlik/Tableau/Airflow/ADF history); and day-to-day work after setup (Power BI deployment, scheduling, RLS and access; Qlik publishing, reload schedules and space access; Tableau permissions and extract schedules; Snowflake and Unity Catalog grant sync). Packages that write read the target first: they never overwrite an existing resource, never change a different permission, and never revoke access; schedules are created switched off unless you ask otherwise.

**Consultant work research.** What consultants actually sell on each platform, and which jobs the site was missing, with sources, is in [docs/DANISMAN-ARASTIRMASI.md](docs/DANISMAN-ARASTIRMASI.md) (Turkish). Missing jobs are being added as packages.

**e-Fatura generation.** During development the sample outputs of the efatura-uret block passed the OASIS UBL 2.1 XSD, GİB's UBL-TR 1.2.1 XSD and GİB's e-Fatura/e-Arşiv Schematron business rules (XSLT 2 with Saxon) without errors; the rules were confirmed active by trying a broken invoice number and tax code. Withholding, return and exemption invoices passed the same rules; every case the block rejects (withholding rate not matching the code, exemption code or withholding on a SATIS invoice, return in a commercial scenario, return without a reference, 0% VAT without a reason) produced the same error from GİB's Schematron when entered into the XML by hand. The generated XML is read back through the existing efatura-csv reader and the amounts compared. The e-İrsaliye generator's sample outputs also passed GİB's UBL-TR 1.2.1 XSD and Schematron rules (GİB's own older dispatch sample fails the newer delivery address rule). The XML files are unsigned; signing and submission to GİB were not tested. The Excel pivot report passed the Open XML SDK and was read with openpyxl; it was not tested by opening it in Excel.

**Templates verified by running them.** The star schema block runs the SQL it generates in SQLite every time; it fails if the fact row count, measure totals or key matches do not agree with the source. The Postgres output was passed through Postgres's own parser (libpg_query), the SQL Server and Snowflake outputs through sqlglot. The dbt incremental model and snapshot templates were run twice with dbt-core 1.12 + DuckDB: on the second run the incremental model processed only rows changed after the last load (with the lookback margin) and caught a late-arriving change; the snapshot closed the old version of changed records and marked the deleted record invalid.

**Qlik and Power BI model blocks.** The time intelligence and row-level security blocks separately compute in Python the value the generated expression should return on the sample data (the measure result, or the rows and total each user sees); after installing in the application you compare against these numbers. The expected values were checked with an independent calculation (including an April fiscal year start). The Power BI calculation group and RLS role were added to the PBIP model produced by the report template and opened with Microsoft's TMDL parser (9 items, role filter, hidden security table). Section Access identities are upper-cased the way Qlik compares them (ali → ALI); Turkish values are matched through ASCII codes and every spelling in the data is linked. Qlik expressions and scripts were not tested by running them in Qlik itself.

**Ready-made report templates.** Eight templates share the same report design: measures (sum/average), a distinct-count KPI, up to three breakdown dimensions, a calendar and monthly trend, filters and a detail table. Columns are chosen from the data and can be changed with `--measures`, `--dimensions` and `--date`. Every template also writes an HTML preview showing the same numbers. During development the formats were passed through official validators: the Power BI report JSON files through Microsoft's PBIR JSON schemas, the TMDL model through Microsoft's TMDL parser (Analysis Services library), the Tableau workbook through Tableau's official TWB XSD (2026.1), and the Excel and PowerPoint files through the Open XML SDK validator (the deck was also read with python-pptx and its chart values compared); Excel formulas were recalculated with an independent formula engine and compared with the cached values. The dbt project was tried by running `dbt build` with dbt-core 1.12 + DuckDB (all models and tests passed, result tables matched the Python calculation). Qlik objects were checked against corectl's open-source build format and LookML against the lkml parser. **None of them were tested by opening them in Qlik, Power BI Desktop, Tableau, Looker, Excel or PowerPoint themselves.** On dirty data (the same value written with different case, blank dimensions) the templates write warnings; Excel and Power BI count such values as one, Qlik and Tableau as separate.

The distribution functions in the statistics blocks (normal, t, chi-square) were written with the standard library and compared with table values. The finance blocks are for checking; they do not replace accounting entries or tax filings.

### Local AI agents (Ollama, OpenAI or Anthropic)

119 agents (118 local + the BigQuery platform agent). In each, the model makes the judgement call and code checks the answer before anything is written. Some examples:

- **Builders:** dashboard builder (multi-table dashboard from a folder of tables on request; the choice is checked against the data model and the projects are produced by the setup blocks), warehouse builder (free text → access plan → Snowflake / Unity Catalog / BigQuery setup; grants compared with the plan), pipeline builder (free text → Airflow DAG; plans with cycles, destructive commands or missed deadlines are rejected).
- **Writers:** SQL writer, DAX measure writer, dbt model writer (rejected if a raw table name remains in the query; model SQL and yml are written by code), Qlik expression writer (fields not in the script, functions Qlik lacks, undefined variables and unbalanced parentheses are rejected), Tableau calculated field writer, regex writer (the pattern is tried locally on every example; nested quantifiers are rejected), log parser (named-group regex tried on the whole file; patterns parsing under 98% of lines are rejected), migration mapping writer, JSON mapping writer, data contract writer.
- **Explainers with checked numbers:** period commentary (totals computed by code; every number in the text matched to these facts), SQL explainer (table names not in the SQL are rejected), budget variance commentator (an adverse variance cannot be skipped or presented as favourable), FinOps savings plan, model card, regression and cohort interpreters, funnel analyst, segment interpreter, experiment analyst (t-test on non-normal data, decisions contradicting the p-value and the wrong direction are rejected), A/B test decision note.
- **Designers and advisers:** dimensional model designer (dependencies measured in the data; load SQL run in SQLite via the star schema block), Avro schema designer, Kafka topic designer, report designer, chart picker (produces Vega-Lite), SLO designer, partitioning, Z-order, warehouse size, connection pool, retry policy, autovacuum, timeout, watermark, incremental strategy and chunk size advisers — each proposal is replayed or measured on your data.
- **Matchers and classifiers:** master data match referee, address resolver, TDHP chart-of-accounts mapper, withholding code and unit code suggesters (codes not on GİB's lists are rejected), product category mapper, glossary → column mapper, job error classifier, open-ended survey coder, KVKK processing inventory, masking plan, deletion request planner.

Shared rules (`library/agents/llm.py`): personal data is masked, the outgoing text is written to `output/gonderilecek-istek.json` and approval is asked (`--yes`), `--dry-run` does not call the model, the model's JSON answer is checked locally and one correction is requested if it breaks a rule. Model output is never executed automatically on any system. The full list with each agent's checks is in the [Turkish README](README.tr.md#yerel-ai-agentlar-ollama-openai-veya-anthropic).

### Platform API packages

| Application | Implemented operation | Delivery |
|---|---|---|
| Qlik Cloud | Export an existing app; copy a template → reload → export | `.qvf`, a new tenant app in the template flow |
| Power BI Service | Export an existing downloadable report | `.pbix` (does not design a new report) |
| Power BI enhanced refresh | Full/transactional refresh of an existing semantic model and tracking by exact refresh ID | Refreshed model + refresh record; shared capacity not supported |
| Tableau Cloud / Server | PAT sign-in → workbook download → sign-out | `.twb` / `.twbx` depending on the response |
| SAP Business One | Service Layer metadata; creating and reading back a new sales quotation draft | `.edmx` or a Drafts record/DocEntry in the ERP |
| SAP S/4HANA | Metadata from the user's active Bearer OData service | `.edmx` service contract |
| Oracle Fusion ERP | REST describe | Resource contract JSON; not an ERP document |
| BigQuery | SELECT dry run → budget check → destination table job → result check | Table in BigQuery, job receipt locally |
| BigQuery agent | Live API schema → model → BigQuery SELECT dry run | `.sql`; the query is not run |
| Fabric Lakehouse | Publish a reviewed ipynb → follow the async result → download and compare source cells; separate export for an existing notebook | Workspace notebook + `.ipynb`; not run automatically |
| Fabric Notebook Jobs | RunNotebook on an existing notebook; separate GET tracking package | Platform job instance + result record; no parameter/session override |
| Databricks Lakehouse | Generate a profiling notebook → SOURCE import → get-status → export | Workspace notebook + Python source file; not run automatically |
| Databricks Jobs | Run an existing job with parameters, verify job/run IDs and the terminal result | Platform run + result record; task outputs are not downloaded |
| Databricks run tracking | After an interruption, track by job_id/run_id with GET only | Result record without starting a new run |
| Azure Data Factory | Publish a pipeline under a new name; also run/track an existing pipeline | Native pipeline or pipeline run |
| Airflow 3.2 | Trigger and track an existing DAG with public API v2 | DAG run; no success output on failure |
| dbt Cloud | Start, track an existing build/test job and download its artifact | dbt run + the platform's own `run_results.json` |
| Prefect 3 Cloud | Create and track a parameterised flow run from a deployment | Flow run on the platform + receipt with only IDs/state |
| Snowflake | Create a new profiling view with the OAuth SQL API, query it and check aggregate counts | Persistent view + `quality-view.sql` + query ID/counts; no quality verdict |
| GitHub Actions | Start an existing workflow_dispatch job, track by exact run ID | Platform workflow run + result record; no artifact download |
| Fivetran | Sync a manual connection with force=false, watch for a new success time | Platform sync + timestamp record; no exact run ID, concurrent triggers not supported |
| Airbyte Cloud | Create a sync job for an existing connection, track by job/connection ID | Platform job + row/byte counters if available; no reset call |
| Airbyte job tracking | After an interruption, track by connection_id/job_id with GET only | Job result and counters without starting a new transfer |
| Google Cloud Dataform | Create/read back a compilationResult from a fixed Git SHA; also run and track an existing compilation | Platform compilationResult or workflow invocation; does not design new SQLX/models |
| Qlik Cloud setup | Upload CSVs to Data Files → new app → load script → reload → field and synthetic key check from data model metadata | New app with a loaded data model; sheets via qlik-cli |
| Fabric Lakehouse setup | New lakehouse → upload CSVs to OneLake → Load Table → operation tracking → table list | New lakehouse with Delta tables |
| Power BI semantic model setup | Publish the TMDL definition as a new semantic model → read back with getDefinition | New model in the workspace (data refresh separate) |
| Metabase setup | Verify the database → collection, SQL cards, dashboard → read back the layout | Collection + dashboard |
| Looker setup | Sign in → check explore fields → dashboard, queries, elements → read back the element list | User-defined dashboard (LookML is deployed via Git) |
| Snowflake account setup | Run the setup SQL statement by statement with its role (only CREATE IF NOT EXISTS/GRANT) → check the permission matrix with SHOW GRANTS | Databases, roles, warehouses, monitors |
| Unity Catalog setup | Create catalogs if absent → schemas → grants → read back → ownership handover | Catalogs, schemas, grants |
| BigQuery setup | Service accounts → datasets with access lists → read back | Datasets + service accounts |
| ADF solution deployment | Placeholder check → if none exist, linked services, datasets, pipelines and triggers under prefixed names | Solution with a stopped trigger |
| Airflow DAG activation | No import errors? → DAG and task list match the file? → unpause | DAG ready to run |
| Confluent Cloud setup | No existing topic or schema subject? → topics (partitions, replication, allowed configs) → read back configs → compatibility mode + Avro schema registration | Topics and schemas |
| dbt Cloud job setup | Environment is a deployment environment, no job with the same name? → jobs (dbt commands only, schedule off) → read back | Scheduled jobs |
| Databricks job setup | No dependency cycle, no job with the same name? → multi-task job (PAUSED) → read back the task list | Lakeflow job |
| Prefect deployment setup | Work pool exists, deployment name free? → flow + paused deployments → read back | Deployments |
| Dagster schedule activation | Schedule loaded in code with the same cron? → start the stopped ones | Running schedules |
| Airbyte connection setup | Source and destination in the same workspace, stream and sync mode offered by the source? → inactive connection → read back | Connection |
| Fivetran table selection | Read the schema config → enable/disable only differing tables → frequency → read back | Updated connection |
| Purview / OpenMetadata / DataHub glossary | No term with the same name? → parent terms first → hierarchy (isA / parent) → read back | Glossary terms |
| Monte Carlo rules | Find the warehouse by name, no rule with the same description? → scheduled custom SQL rules (SELECT only) → read back | Monitors |
| Soda Cloud scan | Trigger the scan definition → track with X-Soda-Scan-Id → check results | `soda_kontrolleri.csv` |
| GA4 admin | Existing definitions and quota → custom dimensions/metrics, key events, BigQuery link → read back | GA4 property configuration |
| Pinecone / Qdrant | No index/collection yet? → create (+ payload indexes) → ready and configured as planned? | Vector index |
| Power BI Scanner (live) | List workspaces → scan in batches of 100 → merge | `scan_result.json` (input for the pbi-tarayici-envanteri block) |
| Snowflake / Databricks cost (live) | Fixed SELECTs on Account Usage / System Tables | Credits, idle warehouses, admin roles, inactive users; cost by product/job/day CSVs |
| Qlik / Tableau / Airflow / ADF history (live) | Page through reload, view, DAG run and pipeline run history | Failures and durations per app/pipeline CSV |
| Power BI deployment | Workspaces assigned to the stages? → deployAll → track the operation | Test/Production stage |
| Power BI schedule, RLS, access | Save the previous schedule → new schedule; missing members into the TMDL role file; missing identities into the workspace → read back | Updated model/workspace |
| Qlik publish, reload schedule, space access | Publish to a managed space (update only with republish=true); reload task if none is active; missing space roles | Published app, task, assignments |
| Tableau permissions and extract schedules | Only undefined capabilities in project/default permissions; extract refresh for workbooks without a task | Permissions, tasks |
| Snowflake / Unity Catalog grant sync | Read current grants against the plan → grant only what is missing → report extras (no revoke) | `yetki_farki.csv` |

**Live vendor/tenant tests: 0. File-opening tests in the applications: 0.** The tests use a local HTTP contract server; they do not prove that a real QVF/PBIX is produced. Agent model answers are faked in tests. These packages are not production-approved yet.

The SAP/Oracle discovery packages are not labelled as report automation. The SAP sales quotation draft is a separate write operation; it does not create a final document. The Qlik template flow (platform package) uses the existing charts and load script; to build a report from scratch use the local `qlik-rapor-sablonu` block. Logo integration is not implemented yet; no unverified endpoint is generated.

## Running locally

Python 3.10+ and Node.js 22+:

```sh
npm run build
npm test
npm start
```

The server runs on `http://localhost:4322` by default and moves to a free port if that one is taken. The interface is under `site/`: `index.html`, `style.css` (Aurora, dark only) and `app.js`. The list has an “All / Code blocks / AI agents” switch (`#k=code`, `#k=agent`). Planned products appear on the coverage map without download links. The site interface is in Turkish.

## Using a package

Local code block: unzip, `python main.py` runs on the sample data; for your own file run `python main.py --help`. Local agent: `.env.example` → `.env` (PROVIDER, MODEL), first `python main.py --dry-run`, then `python main.py`.

Platform package:

1. Unzip; read the supported product, permissions and platform effects in the package README.
2. `.env.example` → `.env`; enter the platform's own token/access details. Getting and refreshing OAuth tokens happens in your identity provider.
3. Edit the source/target IDs in `config.json`, `context.md` for the agent, and `query.sql` for the SQL job.
4. `python native.py --plan` previews without network calls.
5. `python native.py --execute --output output/run-001` makes the real platform calls.

A new, empty output folder is required. Operations that write or may incur LLM costs are stated in the package README. Schema and project context are sent to the chosen model; the agent does not pull row data. IDs from partial runs are in `run-state.json`, the file summary in `manifest.json`. There is no automatic rollback and no automatic execution of model-written code in production.

In total **378 tests** pass (the 368 single-purpose blocks run one by one as subtests of one test); each single-purpose block's packaged copy is run on its own sample data and compared with the expected result. Local package tests (`tests/test_local.py`) run the packaged copy on sample data; agents are tested against a fake model server speaking the OpenAI/Anthropic/Ollama formats (masking, approval, dry run, correction attempt, HTTP errors). The first eight agents were also tried by hand with a local Ollama model (qwen3 14B); the later 110 agents were tested only against the fake model server. The test count is not live tenant validation.

## Test layers

- Regression tests of the old local engines (these engines are no longer distributed as product packages).
- Real localhost HTTP calls specific to each platform: path, payload, auth headers, polling, errors and output type.
- Missing credentials, redirect/origin, GET retry / no POST repeat, budget limits, failed reloads, protecting existing output.
- Integrity/SHA-256 for every ZIP; a network-free `--plan` run for every package; no downloads on planned tasks.
- End-to-end tests of local code blocks and agents using the packaged copy.

Live end-to-end testing is a separate stage: it needs a licensed sandbox, the right version, valid access, and opening and inspecting the artifact in the target application. It does not replace the local contract tests.

## Layout

- `catalog/catalog.py`: the full domain and product taxonomy; earlier CSV drafts remain as historical helpers.
- `catalog/native_catalog.py`: implemented operations, access, effects and official sources; the open plan status of other products.
- `library/native.py`: API transport, platform flows and output checks.
- `library/agent.py`: model provider; in the new flow used only for the model call.
- `catalog/local_catalog.py`: definitions of local code blocks and agents; `CATEGORY_MODULES` loads the single-purpose block modules.
- `library/blocks/islem.py`: shared runner and registration mechanism for single-purpose blocks.
- `library/blocks/`, `library/agents/`, `library/samples/`: local package code and fictional sample data (`scripts/make_samples.py` generates the binary samples).
- `scripts/build.py` + `scripts/local_package.py`: build the native and local packages; remove old generated ZIPs.
- `dist/integration-plan.json`: machine-readable integration plan for the 207 product contexts.
- `docs/INTEGRATION-MATRIX.md`: all products and the remaining scope (Turkish).
- `docs/NATIVE-ROADMAP.md`: completion criteria and next development work (Turkish).

[GitHub](https://github.com/muratceme/data-consulting-workbench) · MIT.

## Resuming Databricks tracking after an interruption

Download the **Mevcut Databricks çalışmasını yeniden başlatmadan takip et** (track an existing Databricks run without restarting it) package. Enter the `job_id` and `run_id` from the previous `run-state.json` into the new package's `config.json`, with the same workspace URL. The valid token stays only in the local `.env`. `python native.py --execute --output output/continued-001` only reads the existing run's state. Use a new empty output folder; the earlier record is kept. If the run ID was never obtained, find it in the Databricks history first; running the old start package again can create a new job run.

## Resuming Airbyte tracking after an interruption

In the **Mevcut Airbyte aktarımını yeniden başlatmadan takip et** (track an existing Airbyte sync without restarting it) package, enter the `connection_id` and `job_id` from the previous `run-state.json` into `config.json`. The valid token stays in the local `.env`. `python native.py --execute --output output/continued-001` only reads the existing job; use a new empty output folder. Running the start package again may create a new sync; use the separate monitor package for tracking.

## Tracking after an interruption on other platforms

ADF, Airflow, dbt Cloud, Prefect, Dataform and Power BI also have separate tracking packages. They query the existing ID without starting a new run; dbt downloads the successful run's artifact. Platform and configuration mappings are in the [recovery guide](docs/RECOVERY.md) (Turkish).

The current nine tracking flows accept `--resume-from previous/run-state.json`: the run ID is carried over automatically, the config's source IDs are compared, and no new job is started. Command examples are in the [recovery guide](docs/RECOVERY.md).
