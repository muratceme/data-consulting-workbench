# Tracking an existing run after an interruption

**English** · [Türkçe](RECOVERY.tr.md)

## Continue with one command from the current package

In the current start or tracking package of the nine platforms below, you can point to the run record while keeping the source addresses of the earlier config. Older ZIPs may not have this option; download the current package.

```sh
python native.py --plan --resume-from output/run-001/run-state.json
python native.py --execute --resume-from output/run-001/run-state.json --output output/continued-001
```

The runner automatically turns the job into the matching monitor operation. It compares the record's source IDs with the config and carries over only the run IDs. The server address, operation name or keys in the record are never used. If an ID is missing or does not match, it stops without starting a new job. The workspace/server address must be kept in the original config; older records do not contain every platform's server address, so a change of address cannot be checked through the record. This command is not supported for operations outside the nine flows in the table, such as Fabric notebook publish/export, Qlik or SAP.

## Carrying IDs over by hand

Running the start package again can create a new job on the platform. The separate tracking packages below use GET only. Keep the earlier `run-state.json`; copy the IDs into the tracking package's `config.json`, put valid access in the local `.env`, and choose a new, empty output folder.

| Package | IDs to take from the earlier record | Extra configuration |
|---|---|---|
| `fabric-notebook-monitor` | `workspace_id`, `notebook_id`, `job_instance_id` | UUID values; only the job state is read with GET |
| `databricks-monitor` | `job_id`, `run_id` | Same `workspace_url` |
| `airbyte-monitor` | `connection_id`, `job_id` | Airbyte Cloud |
| `adf-monitor` | `pipeline_name`, `run_id` | Same subscription, resource group, factory |
| `airflow-monitor` | `dag_id`, `dag_run_id` | Same `server`, `api_version` |
| `dbt-monitor` | `account_id`, `job_id`, `run_id` | Same regional `server`; account/job as text, run as a positive integer |
| `prefect-monitor` | `deployment_id`, `flow_run_id` | `api_url` with the same account/workspace |
| `dataform-monitor` | `compilation_result`, `workflow_invocation` | Full resource names |
| `powerbi-monitor` | `workspace_id`, `dataset_id`, `refresh_id` | UUID values |

```sh
python native.py --plan
python native.py --execute --output output/continued-001
```

In most packages local tracking may time out after about four minutes; the platform job is not cancelled. Fabric job tracking follows the API's Retry-After interval, so it can take longer. You can track again with the same IDs in a new output folder. Failed or cancelled jobs are not restarted and no success file is written. When dbt succeeds, the platform's `run_results.json` artifact is also downloaded.

If there is no job ID because the start response was lost, find it in the platform history first. A `request_pending` record does not prove the operation failed. Do not add the API key or the parameter file to the recovery record. This flow is not automatic rollback, credential renewal, or a general resume for every product.

Verification: local HTTP contract tests; no live tenant acceptance yet.

Run record updates use a temporary file, flush/fsync and an atomic replace. If the write/replace fails, the earlier record is kept; this is not an absolute durability guarantee on every file system or on sudden power loss.

## Retrying a Fabric notebook download

If `notebook_id` was recorded during publishing, enter the same workspace_id/notebook_id into the **Mevcut Fabric notebook kaynağını indir** (download an existing Fabric notebook source) package. The package does not create a new notebook; it uses POST for the getDefinition read. The source notebook must have its outputs/attachments cleared. If you have the create operation ID but no notebook_id, check the Fabric operation result on the platform first; starting the publish package again can create a new item. `--resume-from` is not yet supported for Fabric publish/export; it is supported for running notebooks.
