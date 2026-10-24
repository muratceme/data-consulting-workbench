# Native integration development plan

**English** · [Türkçe](NATIVE-ROADMAP.tr.md)

## Product rule

Code block: a deterministic operation whose input/output, API operation and acceptance criterion are known. Agent: work that needs selection/design/interpretation based on the schema and the business goal. Agent output is checked with the vendor parser, a dry run, a compiler or a sandbox where possible; if it was not checked, it does not get the "working artifact" label.

"We produced an HTML/JSON report" is not a platform integration on its own. JSON is an acceptable delivery only if it is a real platform contract/job definition or a receipt. Closed formats such as QVF/PBIX are never produced by changing a file extension; the vendor engine/API must produce them. Local products that work through an SDK/driver do not get an invented REST API or an unnecessary API key requirement.

## Completion gates for every product

1. **Version and operation contract:** cloud/on-prem distinction, official endpoint/SDK, licence, authentication, scope, native output.
2. **Implementation:** input validation, errors/pagination/polling, retry and side-effect behaviour, access to secrets, native artifact validation.
3. **Local contract test:** a real HTTP test server or SDK fixture; the success flow and failure cases. This is not a real vendor test.
4. **Live sandbox test:** credentials in environment variables; vendor job/request ID, permission errors, minimal scope and real output.
5. **Acceptance in the application:** the artifact is opened/imported; schema, pages, data and metric correctness are checked. For agents, reference business scenarios and human evaluation are added.

Only when the last two stages are complete can a task be marked `live-tested` / `native-open-tested`. At present no task carries these labels.

## First slice implemented

- Qlik Cloud: export of the source app; template copy, reload with the existing script, native QVF export. The source is kept; the copy is not deleted automatically.
- Power BI Service: PBIX export; Tableau Cloud/Server: real TWB/TWBX download. These are not new report designs.
- SAP B1/S4 and Oracle Fusion: discovering the real service schema. The SAP B1 sales quotation draft is now created with the Drafts API and read back by DocEntry; no final document is produced.
- BigQuery: SELECT validation in the API, a budgeted WRITE_EMPTY destination table job; for the agent that writes SQL from the schema, only a dry run and a `.sql` delivery.

- Databricks: the source code of a table profiling notebook is generated, opened as a new SOURCE/PYTHON workspace object, and read back with get-status and export. The notebook is not run automatically.
- ADF: an approved JSON definition is published as a pipeline with a new UUID name. A separate run operation starts an existing pipeline and tracks it to completion.
- Airflow 3.2: a run is created for an existing DAG through /api/v2; logical_date=null and conf are sent; only success is delivered. The Airflow 2 API is not supported.
- dbt Cloud: an existing job is triggered; account/job/run IDs and the terminal state are checked; after success the native run_results.json is downloaded. Build/test commands must already be defined in the job. A missing artifact is a delivery error.
- Prefect 3 Cloud: a parameterised SCHEDULED flow run is created for an existing deployment; the idempotency_key is recorded before the call. Terminal states other than COMPLETED raise an error; a timeout does not cancel the run. No flow code or new deployment is produced.

- GitHub Actions: tracking by the exact workflow_run_id in the REST API 2026-03-10 dispatch response; repo/workflow/run/event validation. Only completed/success is delivered. The old 204 response is not treated as success and is not re-triggered. Creating workflows and downloading artifacts are outside this package's scope.

- Fivetran: force=false sync on an existing manual connection; the v2 STARTED result and the advance of succeeded_at are tracked. Automatic schedules, active/historical syncs and paused connections are not triggered. If there is a concurrent external trigger, run matching is not guaranteed; the endpoint gives no exact run ID. Config/credential responses are not stored.

- Airbyte Cloud: Public API sync job, jobId/connectionId/jobType validation and terminal state tracking. Incomplete/failed/cancelled does not count as success. Token renewal and the self-managed Config API are out of scope; a timeout does not cancel the job. The native delivery is the platform job record, not a new connection definition.

- Dataform: v1 API creation/read-back of a new compilationResult with a full Git SHA, checking an existing compilationResult, and starting/tracking a workflow invocation. Compilation errors block the run; the invocation/compilation resource IDs must match. Generating new SQLX/models and live BigQuery acceptance are not there yet.

- Power BI enhanced refresh: full/transactional semantic model refresh, refresh ID and Location checks; HTTP 202 is acceptance, HTTP 200 + Completed is success. Cancellation/failure and timeout are distinguished; platform error details are not stored. Shared capacity and new report generation are out of scope.

- Databricks Jobs API 2.2: run-now on an existing job, job parameters, an idempotency token before the call, and run ID tracking. Only TERMINATED/SUCCESS is accepted. No job definition, notebook result download or token renewal; live compute acceptance is pending.

- Databricks existing run tracking: a separate read-only package continues with GET using the earlier job_id/run_id. It does not start a new run; the old output is kept.

- Airbyte existing job tracking: GET only with connection_id/job_id; after an interruption the same sync is tracked. No new transfer/reset/cancel is started; the old output files are kept.

- Fabric Notebook: creating a new item from the user's ipynb source, 201/202 LRO tracking, workspace/item checks and download with getDefinition. Cell sources are compared; input containing outputs is rejected. There is also a separate export package for an existing notebook that creates no new item, and separate RunNotebook run and GET tracking packages for an existing notebook; the exact job ID/Completed is checked. Lakehouse/runtime binding, parameter/session overrides and live Fabric acceptance are not there yet.

Later rounds added from-scratch setup packages (Qlik Cloud, Fabric Lakehouse and semantic model, Metabase, Looker, Snowflake, Unity Catalog, BigQuery, ADF, Airflow), new platforms (Confluent, dbt Cloud jobs, Databricks jobs, Prefect, Dagster, Airbyte connections, Fivetran, Purview/OpenMetadata/DataHub, Monte Carlo, Soda Cloud, GA4, Pinecone, Qdrant), live audits and post-setup maintenance; see the [consultant work research](DANISMAN-ARASTIRMASI.md) and the README.

## Remaining priorities

Snowflake first slice: OAuth SQL API, a persistent profiling view with a unique name, and a query of that view. HTTP 202 polling, HTTP 422 errors, statement ID checks and validation of the single-row aggregate result are implemented. Source rows are not downloaded; the account needs CREATE VIEW/SELECT permissions and a warehouse. Native SQL and query IDs are recorded. This is not a quality approval against business rules. Live SQL compilation and tenant acceptance are pending.

- **BI:** schema discovery with Qlik QIX and building an app from scratch from approved sheet/chart definitions; a Qlik Windows QRS certificate/virtual proxy adapter; Power BI/Fabric report definitions; Tableau template/data-source binding and publishing. A separate real opening test for each product.
- **ERP:** document types other than the SAP quotation and mappings, S/4 implementation by communication scenario; Logo Tiger/GO Objects and Netsis NetOpenX version/licence contracts; Oracle ERP document/reporting services. No generic POST endpoint will be invented. Access to Logo's official documentation failed during this work; old third-party samples were not used as a source for product code.
- **Data platforms:** official job/query APIs and notebook/model artifacts for Redshift and others.
- **ETL/DataOps:** live acceptance of the ADF pipeline schema, Fabric pipelines; deploying the definition the product accepts and its run record.
- **Quality/governance/MDM/security:** metadata discovery → rule design → vendor definition → sandbox validation.
- **ML/GenAI/analytics/GIS/planning/migration/FinOps/file tools:** validating the SDK/API path named in the matrix per product/version; output contract, implementation and tests.

All 207 product contexts are in [`INTEGRATION-MATRIX.md`](INTEGRATION-MATRIX.md) and `dist/integration-plan.json`. Having a shared transport layer does not mean these products' integrations are complete.

## Current limits

- No automatic OAuth consent/token renewal, streaming large downloads or general checkpoint resume yet; there are separate tracking packages that use the existing job ID for Databricks, Airbyte, ADF, Airflow, dbt Cloud, Prefect, Dataform, Power BI and Fabric notebook jobs.
- Download redirects are rejected for security; some real tenant flows may need extra work because of this.
- QVF compatibility is not proven outside Qlik Cloud. The PBIX and Tableau test files are local fixtures, not real application files.
- Failed write requests are not retried automatically; the user should inspect the recorded job/app ID.
- Local tests do not replace real account, licence and installation access.
