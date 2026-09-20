# Garmin Import Job — Cloud Scheduled Run: Implementation Handoff

Audience: a coding agent/engineer picking this up next. This document describes
(1) the **current, verified behavior** of `jobs/import-garmin` as it exists in
this repository today, (2) the **Azure prior art** already proven for the
sibling Polar job (`jobs/import/infra`), and (3) a concrete, cloud-neutral plan
to run the Garmin job as a **scheduled job on both AWS and Azure**, treating
PostgreSQL and DuckDB as equally first-class destinations on each cloud.

No cloud infrastructure exists for this job yet (neither AWS nor Azure). No
AWS references exist anywhere in this repository today — this is a greenfield
AWS design. Everything AWS-specific below is a recommendation to implement,
not a description of existing code.

Every code snippet, resource name, and identifier below is a **placeholder**.
Do not copy secrets, tokens, account numbers, or personal data into IaC code,
CI/CD logs, or this document.

---

## 1. Current behavior (verified from source, 2026-08-28)

### 1.1 Source data: GarminDB SQLite

- Upstream: [GarminDB](https://github.com/tcgoetz/GarminDB) produces two SQLite
  databases: `garmin_activities.db` (tables `activities`, `activity_records`)
  and `garmin.db` (table `sleep`). Default location:
  `GARMIN_DB_DIR=local_data/garmin_sqlite/DBs` (`garmin_etl/config.py`).
- `garmin_etl/transform.py` opens both **read-only** (`sqlite3` URI `mode=ro`),
  builds three DataFrames with explicit, stable dtypes:
  - `workouts` → `garmin_workout_metadata` (PK `activity_id`; derived
    `workoutId` = `DD-MM-YYYY_HHMMSS` from `start_time`; first valid GPS fix
    with `gps_source` provenance).
  - `timeseries` → `garmin_timeseries` (PK `activity_id, record`; per-second
    HR/GPS/speed/cadence/altitude).
  - `sleep` → `garmin_sleep` (PK `day`; durations converted `HH:MM:SS` → integer
    seconds; GarminDB's zero-duration placeholder rows are dropped).
- `transform_all()` performs preflight integrity checks: source files exist,
  required tables present, no null/duplicate `workoutId`, no duplicate
  `(activity_id, record)`, and disambiguates rare same-second `workoutId`
  collisions with a numeric suffix. **This validation already exists and
  requires no new code.**

### 1.2 Optional live download from Garmin (`GARMIN_DOWNLOAD=true`)

- `garmin_etl/download.py` shells out to `garmindb_cli.py --all --download
  --import [--latest]` (the `garmindb` package, pinned to **`3.7.0`** in
  `pyproject.toml`'s `download` extra — deliberately pinned because it is the
  last version using the single-file `garth_session` token format this job
  depends on; `3.8.x` changed the token store).
- It writes a job-controlled `GarminConnectConfig.json` into
  `GARMIN_CONFIG_DIR` (paths pinned to `GARMIN_BASE_DIR`, `metric=true`, only
  `activities` + `sleep` stats enabled).
- **Auth is a pre-existing `garth_session` token file**, not a live
  username/password login inside the scheduled run. `_ensure_session_token()`
  looks for `<GARMIN_CONFIG_DIR>/garth_session`, falling back once to the
  legacy `~/.GarminDb/garth_session` if present. If neither exists, it raises
  `FileNotFoundError` and the job aborts (step 2 in `main.py`).
- `_warn_on_expired_oauth2_metadata()` decodes only the token-expiry metadata.
  If `refresh_token_expires_at` is in the past, it warns but still lets garth
  attempt an OAuth1-to-OAuth2 exchange. This is required because garth 0.6.3
  does not treat that OAuth2 metadata as definitive session expiry.
  `garmindb_cli.py` exits 0 even on some auth failures, so the module scans
  captured stdout for failure markers such as `"401 Client Error"` and
  `"session expired"` and returns `False` if found.
- `GARMIN_DOWNLOAD_REQUIRED` (default `true`) controls whether a download
  failure aborts the job (`sys.exit(1)`) or falls back to whatever SQLite data
  is already on disk.
- Default behavior (`GARMIN_DOWNLOAD=false`) is **transform-only**: it assumes
  the SQLite DBs are already present at `GARMIN_DB_DIR` (e.g. bind-mounted).

### 1.3 Auth / token generation (interactive, Docker-contained)

- `garmin_etl/auth.py` (`docker compose run --rm garmin-auth`, a dedicated
  `stdin_open`/`tty` compose service): prompts for email/password (and garth
  handles MFA on stdin), calls `garth.Client().login(...)`, writes
  `client.dumps()` to `<GARMIN_CONFIG_DIR>/garth_session` (`chmod 600`).
- OAuth2 expiry metadata is embedded in the session and produces a warning,
  but the authoritative validity check is the live Garmin API call because the
  longer-lived OAuth1 token may still renew the session.

### 1.4 Destinations: DuckDB or PostgreSQL (`DATABASE_TYPE`)

- `garmin_etl/storage/duckdb.py`: single-file DuckDB at `GARMIN_DUCKDB_PATH`
  (default `local_data/garmin.duckdb`, **kept separate from the Polar job's**
  `database_v2.duckdb`). Explicit DDL, idempotent delete-by-`activity_id`/`day`
  upsert inside a `BEGIN TRANSACTION` block.
- `garmin_etl/storage/postgres.py`: same three tables via `psycopg` v3, using
  either `POSTGRES_CONNECTION_STRING` or discrete
  `POSTGRES_HOST/PORT/DATABASE/USER/PASSWORD/SSLMODE`. Idempotent
  delete-by-id upsert wrapped in an explicit commit/rollback.
- `garmin_etl/query.py` and `garmin_etl/validate.py` are read-only query and
  validation CLI tools
  (also invoked via compose `tools` profile) that already implement: read-only
  SQL against either backend, and a full **DuckDB vs. PostgreSQL vs.
  transformed-SQLite-source** equality + relational-integrity check
  (duplicate PKs, orphan `timeseries` rows, `workoutId` mismatches). **Reuse
  these as-is** for post-deploy smoke tests — no new validation code is
  needed.

### 1.5 Docker runtime

- `Dockerfile`: `python:3.14-slim` + `uv`. **Build context is the job
  directory itself** (`jobs/import-garmin/`), *not* the repo root — unlike
  `jobs/import/Dockerfile`, which builds from the repo root so it can `import
  polar.*`. The Garmin job is deliberately self-contained; it cannot import
  anything under `polar/`. Any shared logic needed for cloud persistence must
  be vendored into `garmin_etl/` (see §3), not imported from `polar/`.
- `CMD ["uv", "run", "python", "/app/main.py"]`. `PYTHONUNBUFFERED=1`,
  `IN_CONTAINER=true`.
- `docker-compose.yml` profiles: `auth` (interactive token mint),
  `source` (`refresh-garmin-source`, download-only), `duckdb`
  (`import-garmin-duckdb`), `postgres` (local Postgres container +
  `import-garmin-postgres`), `tools` (`query-duckdb`, `query-postgres`,
  `validate-garmin`).
- `.env.example` already declares `AZURE_STORAGE_ENABLED` /
  `AZURE_STORAGE_ACCOUNT_NAME` / `AZURE_STORAGE_CONTAINER_NAME` and is
  annotated **"reserved for future DuckDB upload"**. `garmin_etl/config.py`
  loads these three values into the config dict — **and nothing else in the
  job reads them.** This is the gap this document exists to close (§3).

### 1.6 What does NOT exist today (confirmed by inspection, not assumption)

- No cloud storage upload/download code anywhere in `garmin_etl/` (`grep` for
  `azure|s3|blob|upload_database|download_database` under
  `jobs/import-garmin` only matches the three unused config keys above).
- No `azure-storage-blob`, `azure-identity`, or `boto3` dependency in
  `pyproject.toml`.
- No overlap/locking mechanism of any kind (also true of the sibling Polar
  job).
- No AWS references anywhere in the repository.

---

## 2. Azure prior art (`jobs/import/infra`, Polar job) — what to reuse

`jobs/import/infra/__main__.py` is a working Pulumi (`pulumi_azure_native`)
program that deploys the Polar job as an Azure Container Apps Job. Reuse this
pattern for Garmin rather than reinventing it:

- **ACR** (`Basic` SKU, `admin_user_enabled=False`) — images pulled via a
  User-Assigned Managed Identity (UAMI), pushed by GitHub Actions OIDC.
- **UAMI** + `RoleAssignment`s: `AcrPull` on the ACR, `Storage Blob Data
  Contributor` on the storage account (DuckDB variant only).
- **Log Analytics Workspace** (`PerGB2018`, 30-day retention) feeding the
  Container Apps Environment's `app_logs_configuration`.
- **VNet + delegated subnet** (`Microsoft.App/environments` delegation +
  `Microsoft.Storage` service endpoint) so a Consumption-only, VNet-integrated
  Container Apps Environment can reach a storage account whose firewall
  default action is `Deny`. The existing account's network rule is patched
  with a `pulumi_command.local.Command` running `az storage account
  network-rule add` (the account itself is *not* owned by the stack).
- **Container Apps `Job`** with `trigger_type=SCHEDULE`,
  `schedule_trigger_config.cron_expression`, `replica_timeout` (a real
  platform-enforced execution timeout — 1800s in the Polar job),
  `replica_retry_limit`, `registries` (ACR via UAMI identity), `secrets`
  (currently **inline** `SecretArgs(name=..., value=...)` sourced from Pulumi
  config secrets — see §9 for the recommended upgrade to native Key Vault
  references), and `env` entries either plain or `secret_ref`.
- **Image tag resolution**: CI (`import-job-build.yml`) computes an immutable
  `1.0.YYMMDD.N` tag and pushes both that and `latest`; CD
  (`import-job-deploy.yml`, triggered on git tag push) discovers the most
  recent deployable tag via `az acr repository show-tags --orderby
  time_desc` (filtered by regex to exclude `latest` and the `buildcache`
  manifest) and calls `az containerapp job update --image ...`. Both use
  `azure/login@v2` OIDC federated credentials (`environment: production`).
  `deploy.sh` / `config_from_env.sh` are the local/manual equivalents.

Everything above is **directly reusable** for the Azure side of the Garmin
job (new ACR repository `garmin-import-job`, new UAMI, new/shared Log
Analytics workspace, new Container Apps Job, same CI/CD shape). The
PostgreSQL variant is new for Azure (the Polar job has no PostgreSQL Azure
deployment to copy) — see §5.

---

## 3. Required code changes (do these BEFORE writing cloud IaC)

These are gaps, not present anywhere in `jobs/import-garmin` today. They are
cloud-neutral (same code serves both AWS and Azure) so implement them first.

### 3.1 DuckDB snapshot restore/upload (the core ask)

Add a persistence layer mirroring `polar/storage/duckdb.py`'s
`upload_database_to_azure` / `download_database_from_azure` /
`get_latest_duckdb_blob_name`, generalized to two providers:

- New self-contained modules (vendored, not imported from `polar/`, because
  the Docker build context is `jobs/import-garmin/` only — see §1.5):
  - `garmin_etl/cloud/azure_blob.py` — port of `polar/cloud/azure.py`
    (`DefaultAzureCredential` + `azure-storage-blob`): `upload_file`,
    `download_file`, `list_blobs`.
  - `garmin_etl/cloud/s3.py` — new, using `boto3`: `upload_file`,
    `download_file`, `list_objects` against an S3 bucket/prefix.
- `garmin_etl/storage/duckdb.py` additions:
  - `upload_database_to_cloud(config)` / `download_database_from_cloud(config)`
    / `get_latest_duckdb_snapshot_name(config)`, dispatching on a new
    `CLOUD_STORAGE_PROVIDER` config key (`none` | `azure` | `aws`). Keep the
    existing blob-naming convention `duckdb/DD-MM-YYYY_HHMMSS.duckdb` (UTC) so
    the "parse the newest timestamp from the name" selection logic in
    `get_latest_duckdb_blob_name` can be reused for S3 keys unchanged. Use a
    distinct prefix (e.g. `garmin-duckdb/`) if the same bucket/account is
    shared with the Polar job, to avoid key collisions with `duckdb/`.
- `garmin_etl/config.py`: add `CLOUD_STORAGE_PROVIDER` (default `none`),
  `AWS_S3_BUCKET_NAME`, `AWS_REGION` (Azure vars already exist).
- `main.py`: wire it in around the existing transform/load workflow —
  restore **before** transform+load (only for `DATABASE_TYPE=duckdb`), upload
  **after** load. Fail closed on restore/upload errors: an ephemeral container
  must not publish or accept an incomplete database merely because object
  storage was unavailable. A missing snapshot may create a fresh database only
  during an explicit bootstrap that is configured to download the full Garmin
  history.
- `pyproject.toml`: add optional-dependency groups `cloud-azure =
  ["azure-storage-blob", "azure-identity"]` and `cloud-aws = ["boto3"]`; the
  `Dockerfile` should `uv sync` with whichever extra(s) match the target
  deployment (mirroring how `download` is conditionally synced today).

### 3.2 Related required change: the GarminDB SQLite *source* is also ephemeral

Not explicitly part of "DuckDB snapshot" but tightly coupled and easy to miss:
on both AWS Fargate and Azure Container Apps Jobs the container filesystem
does not persist between scheduled invocations. Today `GARMIN_DB_DIR` /
`GARMIN_BASE_DIR` are expected to hold state across runs (e.g. so
`GARMIN_DOWNLOAD_LATEST=true` incremental pulls are cheap, and so the default
`GARMIN_DOWNLOAD=false` transform-only path even has input files to read).
Without persistence, every scheduled run starts from an empty
`local_data/garmin_sqlite/` directory. Two options, pick one before cloud
cutover:

1. **Simplest (no new code):** always run with `GARMIN_DOWNLOAD=true` in the
   cloud and accept that `garmindb_cli.py` must do a fresh full pull each run
   (verify empirically whether `--latest` against an empty cache still limits
   history — GarminDB's behavior here should be confirmed by a test run before
   relying on it; if it silently no-ops or errors on a missing DB, use
   `GARMIN_DOWNLOAD_LATEST=false` instead). Simpler ops, higher Garmin Connect
   API load and longer run time on every invocation.
2. **Recommended:** persist the `GARMIN_BASE_DIR` tree (or at minimum the
   `DBs/` subfolder) using the *same* upload/download primitives built in
   §3.1 (e.g. tar it, upload as `garmin-sqlite/latest.tar.gz`, restore before
   the download phase, re-upload after). This keeps incremental downloads fast
   and keeps the transform-only path meaningful. This is new code, not yet
   started; treat it as an explicit follow-up task, distinguished from the
   DuckDB-snapshot requirement.

The `garth_session` token itself is **not** part of this cache — it is a
credential, not derived data, and must always come from a secret store (§6),
never from the persisted state blob/bucket.

### 3.3 Non-overlap / locking (new, required for both clouds — see §7)

No locking exists today. Required before enabling a recurring schedule,
because two overlapping runs racing on the same DuckDB snapshot key (or the
same GarminDB SQLite cache, or the same `garth_session` refresh) can corrupt
state. Design in §7; implement as a small `garmin_etl/lock.py` used by
`main.py` around the whole run.

---

## 4. Cloud-neutral contract

Both cloud deployments must satisfy the same behavioral contract so the same
container image and (mostly) the same env var names work on either cloud:

| Concern | Env var | Notes |
|---|---|---|
| Database backend | `DATABASE_TYPE` | `duckdb` \| `postgres` (existing) |
| Cloud storage backend | `CLOUD_STORAGE_PROVIDER` *(new)* | `none` \| `azure` \| `aws` — selects which persistence module runs the DuckDB snapshot restore/upload (§3.1) |
| Garmin download toggle | `GARMIN_DOWNLOAD`, `GARMIN_DOWNLOAD_LATEST`, `GARMIN_DOWNLOAD_REQUIRED` | existing |
| Garmin source paths | `GARMIN_DB_DIR`, `GARMIN_BASE_DIR`, `GARMIN_CONFIG_DIR` | existing; must resolve to a scratch/ephemeral path inside the container (e.g. `/app/local_data/...`), not a host bind mount, in the cloud |
| garth token | file at `<GARMIN_CONFIG_DIR>/garth_session` | existing consumer; in the cloud this file must be materialized at container start from a secret (§6), never baked into the image |
| DuckDB path | `GARMIN_DUCKDB_PATH` | existing; ephemeral local path, restored/uploaded around it |
| PostgreSQL | `POSTGRES_CONNECTION_STRING` or `POSTGRES_HOST/PORT/DATABASE/USER/PASSWORD/SSLMODE` | existing |
| Azure Storage | `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_CONTAINER_NAME` | existing keys, newly consumed |
| AWS S3 *(new)* | `AWS_S3_BUCKET_NAME`, `AWS_REGION` (or rely on the ECS task's default region/credential chain) | |
| Container marker | `IN_CONTAINER=true` | existing |

Reliability principle: **cloud persistence and auth/token failures are fatal**.
The only non-error missing-snapshot case is an explicitly configured first
bootstrap that performs a full-history source download. Do not silently import
or upload partial state from an ephemeral filesystem.

---

## 5. AWS design

### 5.1 Shared resources (both DB variants)

- **ECR** repository `garmin-import-job` (image scanning on push enabled).
  Docs: [Amazon ECR](https://docs.aws.amazon.com/AmazonECR/latest/userguide/what-is-ecr.html).
- **VPC**: reuse an existing VPC or create one with private subnets across 2+
  AZs. No public IPs on tasks. Prefer **Interface VPC Endpoints** for
  `ecr.api`, `ecr.dkr`, `secretsmanager`, `logs`, and a **Gateway Endpoint**
  for `s3` (DuckDB variant) instead of a NAT Gateway, to avoid NAT egress cost
  and keep traffic off the public internet. Docs: [Amazon ECS interface VPC
  endpoints](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/vpc-endpoints.html).
- **Security groups**: `garmin-job-sg` (egress 443 to VPC endpoints, plus 5432
  to the RDS SG in the Postgres variant); `garmin-rds-sg` (ingress 5432 from
  `garmin-job-sg` only).
- **ECS Cluster** (Fargate capacity provider), **Task Definition**:
  - 1 container, `FARGATE` launch type, e.g. `0.5 vCPU / 1 GB` (tune after a
    real run; the Azure prior art uses the same sizing).
  - **Execution role**: ECR pull, CloudWatch Logs write, Secrets Manager read
    (for the specific secret ARNs only).
  - **Task role**: application-level AWS permissions — none for the
    PostgreSQL-only variant; `s3:GetObject`/`PutObject`/`ListBucket` scoped to
    the specific bucket+prefix for the DuckDB variant.
  - Secrets injected via the task definition's `secrets` block (`valueFrom`
    pointing at Secrets Manager ARNs), **not** `environment` plaintext. Docs:
    [Specifying sensitive data using Secrets
    Manager](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data-secrets.html).
  - Log driver `awslogs` → CloudWatch Logs group `/ecs/garmin-import-job`
    (30-day retention to start). Docs: [Sending ECS logs to CloudWatch
    Logs](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_awslogs.html).
- **EventBridge Scheduler**: a schedule (cron or rate expression, explicit
  time zone) whose target is the ECS `RunTask` API, with a dedicated IAM
  execution role scoped to `ecs:RunTask` on the specific task definition ARN
  plus `iam:PassRole` for the execution/task roles. Configure a **DLQ** (SQS)
  on the target so failed `RunTask` invocations (e.g. throttling) are not
  silently dropped; configure `maximumRetryAttempts` /
  `maximumEventAgeInSeconds` for delivery retries — **this only retries the
  API call to start the task, not task execution failures** (see §10). Docs:
  [Schedule Amazon ECS tasks with EventBridge
  Scheduler](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/tasks-scheduled-eventbridge-scheduler.html),
  [EventBridge Scheduler user
  guide](https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html),
  [Scheduler DLQ](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule-dlq.html).
- **CloudWatch**: an EventBridge rule on `ECS Task State Change` (state
  `STOPPED`, non-zero `exitCode` or `stoppedReason` containing
  `Essential container in task exited`) → SNS topic → email/Slack. Optionally
  a CloudWatch Logs metric filter on `"❌ ERROR"` (the job's own error prefix)
  for faster signal than waiting on task state.

### 5.2 AWS + PostgreSQL variant

- **Amazon RDS for PostgreSQL** (or Aurora PostgreSQL-Compatible), private
  subnets, subnet group spanning the same AZs as the task subnets, storage
  encryption enabled (KMS), `rds.force_ssl=1` parameter, and (recommended)
  **RDS-managed master password in Secrets Manager** so the DB password never
  needs manual rotation logic. Docs: [Password management for RDS with
  Secrets
  Manager](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html),
  [SSL/TLS with
  RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html).
- Env: `DATABASE_TYPE=postgres`, `POSTGRES_HOST=<rds endpoint>`,
  `POSTGRES_PORT=5432`, `POSTGRES_DATABASE=garmin`, `POSTGRES_USER=...`,
  `POSTGRES_PASSWORD` from Secrets Manager (RDS-managed secret ARN),
  `POSTGRES_SSLMODE=verify-full` (with the RDS CA bundle) or `require`.
- Locking: PostgreSQL session-level advisory lock (§7.1) — no extra AWS
  resource needed.

### 5.3 AWS + DuckDB (S3-persisted) variant

- **S3 bucket** (e.g. `<org>-garmin-workoutdata`), versioning **on**
  (rollback safety for snapshot corruption), default encryption (SSE-S3 or
  SSE-KMS), Block Public Access on, bucket policy least-privilege to the task
  role only. Prefix `garmin-duckdb/` for snapshots.
- Env: `DATABASE_TYPE=duckdb`, `CLOUD_STORAGE_PROVIDER=aws`,
  `AWS_S3_BUCKET_NAME=<bucket>`, `AWS_REGION=<region>`.
- Locking: S3 conditional-write lock object, or a DynamoDB lock table (§7.2).
- Docs: [S3 conditional
  writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html),
  [S3 bucket
  policies](https://docs.aws.amazon.com/AmazonS3/latest/userguide/example-bucket-policies.html).

---

## 6. Azure design

### 6.1 Shared resources (both DB variants) — extends §2's prior art

- **ACR**: new repository `garmin-import-job` (same registry as the Polar job
  is a reasonable default, or a dedicated registry — either works with the
  existing Pulumi pattern).
- **UAMI** `garmin-import-job-identity` + role assignments: `AcrPull` on the
  ACR; `Storage Blob Data Contributor` scoped to the storage account/container
  (DuckDB variant); `Key Vault Secrets User` (RBAC) on the Key Vault.
- **Log Analytics Workspace**: reuse the existing workspace or create
  `garmin-import-logs`, feeding the Container Apps Environment. Docs: [Log
  monitoring in Azure Container
  Apps](https://learn.microsoft.com/en-us/azure/container-apps/log-monitoring).
- **VNet + delegated subnet**, same shape as `jobs/import/infra` (subnet
  delegated to `Microsoft.App/environments`; add a second delegated subnet for
  `Microsoft.DBforPostgreSQL/flexibleServers` in the Postgres variant — these
  must be separate subnets).
- **Container Apps Environment** (Consumption, VNet-integrated).
- **Key Vault**: `garmin-garth-session` (secret containing the token file
  contents), `garmin-postgres-password` (Postgres variant only; prefer Entra
  ID auth instead — see §6.2). Docs: [Key Vault
  overview](https://learn.microsoft.com/en-us/azure/key-vault/general/overview).
- **Container Apps `Job`**: `trigger_type=Schedule`, `cron_expression`,
  `replica_timeout` (this *is* a real platform-enforced execution timeout,
  unlike AWS Fargate — see §10), `replica_retry_limit`. **Recommended
  upgrade over the current Polar job pattern**: reference secrets natively
  from Key Vault (`secretRef` + `keyVaultUrl` + `identity`) instead of
  inlining `SecretArgs(value=...)` sourced from Pulumi config secrets. Docs:
  [Manage secrets in Azure Container
  Apps](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets#reference-secret-from-key-vault),
  [Jobs in Azure Container
  Apps](https://learn.microsoft.com/en-us/azure/container-apps/jobs).
- **Alerting**: Azure Monitor alert rule on the Container Apps Job's
  `JobExecutionFailed` (or similar) metric / Log Analytics query over
  `ContainerAppConsoleLogs_CL` for the job's own `"❌ ERROR"` lines → Action
  Group (email/Slack/webhook).

### 6.2 Azure + PostgreSQL variant

- **Azure Database for PostgreSQL — Flexible Server**, VNet-integrated
  (private access) into the dedicated delegated subnet so the job reaches it
  without public exposure; TLS is enforced by default. Docs: [Networking
  concepts for Azure Database for PostgreSQL flexible
  server](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-networking).
- Env: `DATABASE_TYPE=postgres`, `POSTGRES_HOST=<flexible server FQDN>`,
  `POSTGRES_PORT=5432`, `POSTGRES_DATABASE=garmin`, `POSTGRES_SSLMODE=require`.
- **Recommended enhancement (optional, needs code change):** use Entra ID
  (Azure AD) authentication instead of a password — the UAMI becomes a
  Postgres AAD admin and the job authenticates with an access token instead of
  `POSTGRES_PASSWORD`. `garmin_etl/storage/postgres.py` currently only
  supports password-based `psycopg.connect(...)`; this would need a new
  connection path. Flag as optional, not required for initial cutover. Docs:
  [Microsoft Entra authentication with Azure Database for PostgreSQL flexible
  server](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-azure-ad-authentication).
- Locking: PostgreSQL advisory lock (§7.1), same as AWS.

### 6.3 Azure + DuckDB (Blob-persisted) variant

- Reuse the storage-account pattern from `jobs/import/infra` (VNet service
  endpoint + firewall network-rule, or upgrade to a **Private Endpoint**,
  which is Microsoft's current recommended pattern over service endpoints).
  Docs: [Private Endpoint
  overview](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-overview).
- Container/prefix: dedicated container or a `garmin-duckdb/` prefix distinct
  from the Polar job's `duckdb/` prefix if sharing a storage account.
- Env: `DATABASE_TYPE=duckdb`, `CLOUD_STORAGE_PROVIDER=azure`,
  `AZURE_STORAGE_ACCOUNT_NAME=...`, `AZURE_STORAGE_CONTAINER_NAME=...`,
  `AZURE_CLIENT_ID=<UAMI client id>` (for `DefaultAzureCredential`, matching
  the existing Polar job pattern in `jobs/import/infra/__main__.py`).
- Locking: Azure Blob **lease** on a sentinel blob (§7.2).

---

## 7. Non-overlap / locking design (new — required for both clouds)

Neither EventBridge Scheduler nor Container Apps Jobs' schedule trigger
prevents a new invocation from starting while a prior one is still running if
it overran the interval. A lock is needed to protect: the shared DuckDB
snapshot (or S3/Blob GarminDB SQLite cache, if §3.2's recommended option is
implemented), and the `garth_session` token file (concurrent refreshes could
race). Use whichever mechanism matches the deployed variant:

### 7.1 PostgreSQL variant (both clouds) — PostgreSQL advisory lock

Acquire a session-level `pg_try_advisory_lock(hashtext('garmin-import-job'))`
on a dedicated long-lived connection immediately after configuration loads;
release it (`pg_advisory_unlock`, or simply close the connection — session
locks are released automatically on disconnect) in a `finally` block around
the whole run. If the lock is **not** acquired, log a clear
`"SKIPPED_DUE_TO_LOCK"` marker and **exit 0** (a legitimate skip, not a
failure — avoids false-positive alerts on every genuinely-overlapping tick).

### 7.2 DuckDB variant (both clouds) — cloud-native object lock

No shared database exists to hold an advisory lock, so use the object store
itself:

- **AWS**: create a lock object (e.g. `garmin-duckdb/lock.json` containing a
  start timestamp) with a **conditional PUT** (`If-None-Match: *`) so only one
  concurrent writer can create it; delete it in a `finally` block. If a lock
  object already exists and is *younger* than `2 × expected max runtime`,
  skip (exit 0, log `SKIPPED_DUE_TO_LOCK`). If it is *older*, treat the prior
  run as crashed, log a loud warning, and take over (still delete/recreate the
  lock). Docs: [S3 conditional
  writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).
  A DynamoDB table with a conditional `PutItem` is a more battle-tested
  alternative if stronger guarantees are wanted (see the [Amazon DynamoDB Lock
  Client](https://github.com/awslabs/amazon-dynamodb-lock-client) pattern).
- **Azure**: acquire a **blob lease** on a sentinel blob (e.g.
  `garmin-duckdb/lock.blob`), 60s duration, renewed by a background
  thread/heartbeat for the run's duration, released in a `finally` block. If
  the lease is already held, skip (exit 0). Docs: [Lease Blob (REST
  API)](https://learn.microsoft.com/en-us/rest/api/storageservices/lease-blob),
  [Managing concurrency in Blob
  Storage](https://learn.microsoft.com/en-us/azure/storage/blobs/concurrency-manage).

### 7.3 Schedule-level mitigation (both clouds, cheap, do this too)

Set the schedule interval comfortably above the observed p99 run duration,
and set `replica_timeout` (Azure) / an in-container timeout wrapper (AWS, see
§10) below the schedule interval, so a hung run is killed before the next tick
starts — locking is the correctness backstop, not the primary defense.

---

## 8. Persistence workflow (restore → run → upload)

Applies to the DuckDB variant on either cloud, once §3.1 is implemented:

1. Acquire the lock (§7.2).
2. Download the newest `garmin-duckdb/*.duckdb` snapshot (by parsed
   timestamp, mirroring `get_latest_duckdb_blob_name`) to `GARMIN_DUCKDB_PATH`.
   A missing snapshot is allowed only for an explicit first bootstrap that
   performs a full-history source download; other missing/corrupt/failed
   restores are fatal.
3. (Optional, §3.2) restore the GarminDB SQLite cache similarly.
4. Materialize `garth_session` from the secret store to
   `<GARMIN_CONFIG_DIR>/garth_session` (§9) before the download phase runs.
5. Run the existing pipeline unchanged: download (optional) → transform →
   load.
6. (Optional, §3.2) re-upload the GarminDB SQLite cache.
7. Upload the local DuckDB file as a new timestamped snapshot
   (`garmin-duckdb/DD-MM-YYYY_HHMMSS.duckdb`, UTC).
8. Release the lock.

Failures at steps 2 through 7 are fatal, except the explicit first-bootstrap
missing-snapshot case described above. This prevents a transient storage error
from replacing durable history with partial ephemeral state.

---

## 9. Garth token generation & rotation in the cloud

The interactive login (`garmin_etl/auth.py`, MFA-capable) **cannot run inside
a headless scheduled job** — it needs a TTY and a human for MFA. Token
lifecycle in the cloud must therefore be:

1. **Generate/renew out-of-band**, on a workstation, using the Dockerized
   interactive command: `docker compose run --rm garmin-auth`.
2. **Store the resulting `garth_session` file contents** as a secret:
   - AWS: a Secrets Manager secret (string type, holding the file's raw
     contents).
   - Azure: a Key Vault secret (same).
3. **At container start**, an init step (small shell/Python snippet run before
   `main.py`, or a change to the entrypoint) writes the secret's value to
   `<GARMIN_CONFIG_DIR>/garth_session` with `chmod 600` — the token is **never
   baked into the image** and never lives in a task definition/Container App
   `environment` block as plaintext.
4. **Rotation is a secret-value update only** — no image rebuild, no
   redeploy. The next scheduled run reads the new secret value. Because OAuth2
   metadata does not prove OAuth1 expiry, alert on repeated authentication
   failure markers and rotate proactively based on the token's age and an
   operator-defined interval rather than hard-failing solely on
   `refresh_token_expires_at`.
5. Never store the raw Garmin email/password in the cloud at all — only the
   derived token, exactly as the current local workflow already does.

---

## 10. Retries & timeouts

- **AWS Fargate has no built-in task execution timeout.** If one is needed,
  wrap the entrypoint with a shell `timeout <seconds>` (or an application-level
  watchdog) so a hung run self-terminates; there is no equivalent to Azure's
  `replica_timeout` today.
- **EventBridge Scheduler retries only cover the `RunTask` API call**
  (`maximumRetryAttempts`, `maximumEventAgeInSeconds`) — a transient
  throttling error starting the task, not a failed task execution. For true
  execution-level retry on AWS, wrap `RunTask` in an **AWS Step Functions**
  state machine with `Retry`/`Catch` (optional enhancement, not required for
  initial cutover). Docs: [Manage a target queue with a dead-letter
  queue](https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule-dlq.html),
  [Call Amazon ECS with Step
  Functions](https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html).
- **Azure Container Apps Jobs natively support both** `replica_timeout`
  (execution timeout, platform-enforced) and `replica_retry_limit`
  (execution-level retry within the same trigger) — no extra plumbing needed,
  as already configured in `jobs/import/infra/__main__.py`.
- **Application-level retry for transient upstream errors**: the Polar job's
  `main.py` already retries transient `503`/service errors from its API
  (`MAX_RETRIES = 3`, `RETRY_DELAY = 30`). Mirror this inside
  `garmin_etl/download.py`'s `run_download()` for transient Garmin
  Connect/garth errors (distinct from the hard-fail-on-expired-token path,
  which must stay non-retryable).

---

## 11. Logging & alerts

- Keep the existing print-based step logging (`PYTHONUNBUFFERED=1` already
  ensures it streams) — it already carries clear `✅`/`❌`/`⚠️` markers.
- AWS: `awslogs` driver → CloudWatch Logs group `/ecs/garmin-import-job`;
  alert via an EventBridge rule on ECS `Task State Change` (`STOPPED` +
  non-zero exit) → SNS.
- Azure: Container Apps → Log Analytics
  (`ContainerAppConsoleLogs_CL`/`ContainerAppSystemLogs_CL`); alert via an
  Azure Monitor alert rule (log query on `"❌ ERROR"` or on job execution
  failure) → Action Group.
- Emit the `SKIPPED_DUE_TO_LOCK` marker (§7) as its own recognizable log line
  so a separate, lower-severity alert can page only if skips repeat (which
  would indicate a stuck prior run, not routine schedule jitter).

---

## 12. Integrity checks

- **Pre-existing, reuse as-is**: `transform_all()`'s preflight checks (§1.1)
  run on every invocation automatically.
- **Pre-existing, reuse as-is**: `garmin_etl/validate.py` (`--backend
  duckdb|postgres|both`) compares the destination(s) row-for-row against a
  fresh transform of the source and checks relational integrity (no orphan
  `timeseries`, no `workoutId` mismatches). Run this as a post-deploy smoke
  test (a one-off manual ECS `RunTask` / Container Apps Job execution) after
  first cutover and after any schema change.
- **New, recommended**: after implementing §3.1, add a round-trip check to
  the acceptance test plan (§13) — upload a snapshot, then in a second,
  independent run confirm it is downloaded and its row counts match.

---

## 13. Failure recovery

| Failure | Detection | Recovery |
|---|---|---|
| `garth_session` rejected/invalid | GarminDB returns non-zero or emits an authentication-failure marker; the wrapper exits non-zero when `GARMIN_DOWNLOAD_REQUIRED=true` | Operator renews the token out-of-band (§9) and updates the secret value — **no redeploy** |
| DuckDB snapshot missing/corrupt | Restore step exits non-zero, except an explicitly enabled first bootstrap | Restore a prior good S3/Blob **version**; bootstrap a fresh DB only with a verified full-history Garmin source download |
| Overlapping run detected | Lock not acquired (§7) | Current run logs `SKIPPED_DUE_TO_LOCK` and exits 0; investigate only if skips recur (indicates a stuck prior run — locate and terminate it) |
| `garmin_etl.validate` mismatch post-deploy | Manual/scheduled smoke test fails | Treat as a data-integrity incident — do not auto-remediate; page a human; roll back to the last-known-good DuckDB snapshot version if needed |
| Transient Garmin/API error | Non-auth exception during download | Application-level retry with backoff (§10); if exhausted, job exits non-zero and next scheduled tick retries naturally |
| Persistent connectivity failure (DB or storage unreachable) | Non-zero exit, CloudWatch/Log Analytics alert | Standard incident response — check network path (VPC endpoints/NAT, Private Endpoint/service endpoint), security groups/firewall rules, credentials |

---

## 14. Security

- No secrets baked into the image or present in task definitions/Container
  App YAML as plaintext — always secret-store references
  (`valueFrom`/Secrets Manager, `secretRef`/Key Vault).
- Least privilege IAM/RBAC: task role scoped to only the specific secret ARNs
  and S3 bucket/prefix it needs; UAMI scoped to the specific Key Vault and
  storage container, not the whole subscription/account.
- Private networking: no public IPs on Fargate tasks; Container Apps
  Environment VNet-integrated; database reachable only from the job's
  security group/subnet; TLS enforced everywhere (`sslmode=require`/
  `verify-full`, Flexible Server's default-on TLS).
- Encryption at rest: RDS storage encryption (KMS) and S3 default encryption
  + Block Public Access + versioning; Azure Storage and Azure Database for
  PostgreSQL encryption at rest (on by default).
- Audit trail: CloudTrail for Secrets Manager/S3 access; Azure Activity Log +
  Key Vault diagnostic logs for secret-access auditing.
- Treat `garth_session` as a high-value credential — it grants full Garmin
  Connect account access. Store only in Secrets Manager/Key Vault, restrict
  read access to the task/job identity only, never commit it to git (already
  covered by `.gitignore`/`.dockerignore`), and never store the raw Garmin
  username/password in the cloud.
- Immutable image tags (mirror the Polar job's `1.0.YYMMDD.N` scheme) so a
  scheduled run's image cannot silently change underneath it between builds.

---

## 15. Acceptance criteria

1. CI builds and pushes an immutably-tagged image to ECR/ACR on merge, using
   OIDC (no long-lived cloud credentials in GitHub Actions secrets) — mirror
   `import-job-build.yml`/`import-job-deploy.yml` for AWS (e.g.
   `aws-actions/configure-aws-credentials` with an OIDC role) and Azure
   (`azure/login@v2`, already proven).
2. The scheduled job runs unattended on its defined cadence on both clouds
   and both DB variants, producing a `✅ GARMIN IMPORT JOB COMPLETE` log line
   each time.
3. Idempotency: running the job twice in immediate succession produces
   identical row counts in the destination tables (no duplication),
   verifiable via `garmin_etl.validate`.
4. DuckDB variant only: a snapshot uploaded by one run is successfully
   restored and used by the next run (round-trip verified, §12).
5. PostgreSQL variant only: data lands only in the private RDS/Flexible
   Server instance, unreachable from outside the job's network path.
6. No secret value ever appears in plaintext in logs, task definitions,
   Container App configuration, or CI output.
7. Locking: manually starting a second execution while one is in progress
   results in the second exiting cleanly (`SKIPPED_DUE_TO_LOCK`) with no data
   corruption in either the DuckDB snapshot or the PostgreSQL destination.
8. An induced failure (e.g. a deliberately wrong secret value) produces an
   alert (CloudWatch alarm / Azure Monitor alert) within a defined SLA
   (e.g. 15 minutes).
9. Rotating the `garth_session` secret value (no redeploy) is picked up by
   the next scheduled run.
10. A prior DuckDB snapshot can be restored from S3/Blob version history
    within a defined RTO if the latest snapshot is found to be bad.
11. `garmin_etl.validate --backend both` passes during initial cutover
    (confirms DuckDB and PostgreSQL destinations agree, if both are stood up
    for comparison) or `--backend duckdb`/`--backend postgres` passes for
    whichever single backend is the production target.
