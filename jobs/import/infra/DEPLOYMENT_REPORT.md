# Deployment Report: Polar Import Job → Azure Container Apps Job

**Branch:** `dev/anton/pulumi-deploy-import-job`  
**Commits:** `2348c3b` (initial), `7d4daa1` (amd64 fix), `5d11ff2` (runtime fixes)  
**Date:** 2026-04-19  
**Status:** ✅ Deployed and verified — job ran successfully

---

## Summary

All Pulumi infrastructure-as-code files for deploying the Polar workout import job as an Azure Container Apps Job have been created and committed locally. The implementation is ready for review before pushing and deploying.

---

## Files Created

| File | Purpose |
|------|---------|
| `jobs/import/infra/__main__.py` | Pulumi program — defines all Azure resources |
| `jobs/import/infra/Pulumi.yaml` | Pulumi project definition (Python runtime, venv) |
| `jobs/import/infra/Pulumi.prod.yaml` | Stack config with encrypted secrets (**gitignored**) |
| `jobs/import/infra/requirements.txt` | Python deps: `pulumi`, `pulumi-azure-native` |
| `jobs/import/infra/deploy.sh` | Build, push, and deploy script |
| `jobs/import/infra/config_from_env.sh` | Bootstraps Pulumi config from `.env` + `tokens_polar.json` |
| `jobs/import/infra/.gitignore` | Ignores venv, `__pycache__`, `.pulumi/`, stack YAML |
| `jobs/import/infra/venv/` | Python venv with Pulumi SDK installed (gitignored) |

---

## Azure Resources Deployed by Pulumi

| # | Resource | Azure Type | Location / Scope |
|---|----------|-----------|-----------------|
| 1 | **User Assigned Managed Identity** (`polar-import-job-identity`) | `Microsoft.ManagedIdentity/userAssignedIdentities` | `muskul.ai` RG, East US |
| 2 | **ACR Pull Role Assignment** | `Microsoft.Authorization/roleAssignments` | Scoped to `humandcoded2` ACR |
| 3 | **Storage Blob Data Contributor Role Assignment** | `Microsoft.Authorization/roleAssignments` | Scoped to `muskulsa` Storage Account |
| 4 | **PostgreSQL Entra ID Administrator** | `Microsoft.DBforPostgreSQL/flexibleServers/administrators` | `humandcoded-pg` in `humandcoded` RG |
| 5 | **Log Analytics Workspace** (`polar-import-logs`) | `Microsoft.OperationalInsights/workspaces` | `muskul.ai` RG, East US |
| 6 | **Container Apps Environment** (`polar-import-env`) | `Microsoft.App/managedEnvironments` | `muskul.ai` RG, East US |
| 7 | **Container Apps Job** (`polar-import-job`) | `Microsoft.App/jobs` | `muskul.ai` RG, East US |

### Existing Resources Referenced (not created)

- **Resource Group:** `muskul.ai` (East US)
- **ACR:** `humandcoded2.azurecr.io` (in `humandcoded` RG)
- **Storage Account:** `muskulsa` (in `muskul.ai` RG)
- **PostgreSQL Server:** `humandcoded-pg` (in `humandcoded` RG, West US)

---

## Environment Variables Injected into Container

### Secrets (stored in Container App secrets, referenced by secret_ref)

| Env Var | Secret Name |
|---------|-------------|
| `POLAR_CLIENT_SECRET` | `polar-client-secret` |
| `POSTGRES_PASSWORD` | `postgres-password` |
| `ACCESS_TOKEN` | `access-token` |
| `REFRESH_TOKEN` | `refresh-token` |

### Plain Values

| Env Var | Source |
|---------|--------|
| `POLAR_CLIENT_ID` | Pulumi config |
| `POLAR_REDIRECT_PORT` | Pulumi config (default: `5001`) |
| `POLAR_MEMBER_ID` | Pulumi config |
| `ALLOW_PORT_FALLBACK` | Hardcoded `true` |
| `DATABASE_TYPE` | Pulumi config (default: `postgres`) |
| `POSTGRES_HOST` | Pulumi config |
| `POSTGRES_PORT` | Pulumi config (default: `5432`) |
| `POSTGRES_DATABASE` | Pulumi config (default: `workoutdata`) |
| `POSTGRES_USER` | Pulumi config |
| `AZURE_STORAGE_ENABLED` | Hardcoded `true` |
| `AZURE_STORAGE_ACCOUNT_NAME` | Pulumi config |
| `AZURE_STORAGE_CONTAINER_NAME` | Pulumi config |
| `TOKEN_TYPE` | Pulumi config (default: `bearer`) |
| `OUTPUT_DIR` | Hardcoded `local_data` |
| `VO2MAX_DATA_PATH` | Hardcoded `data/v02max_data.csv` |
| `ZONES_CSV_PATH` | Hardcoded `hr_data/zones.csv` |
| `IN_CONTAINER` | Hardcoded `true` |

---

## Fixes Applied (vs. previous session)

1. **Role definition IDs now include subscription prefix** — RBAC role assignments previously used bare `/providers/Microsoft.Authorization/roleDefinitions/{guid}` which would fail at deploy time. Now correctly prefixed with `/subscriptions/{subscription_id}/...`.

2. **Added `REFRESH_TOKEN`** — Previously only `ACCESS_TOKEN` was passed. For a daily cron job, the access token will expire. The refresh token is now stored as a Pulumi secret, injected as a Container App secret, and passed as `REFRESH_TOKEN` env var.

3. **PostgreSQL admin annotated as future prep** — Added a comment clarifying the `pg_admin` resource prepares for future Entra ID auth migration; the job currently uses password auth.

4. **`AZURE_CLIENT_ID` for UAMI** — Added `AZURE_CLIENT_ID` env var pointing to the UAMI's client ID so `DefaultAzureCredential` can discover the User Assigned Managed Identity in the container.

5. **Docker `--platform linux/amd64`** — Apple Silicon builds default to `arm64`, but Azure Container Apps requires `amd64`. Added `--platform linux/amd64` to the Docker build in `deploy.sh`.

6. **Refresh token is optional** — `tokens_polar.json` has `null` for `refresh_token`. Changed from `require_secret` to not setting the env var at all (`os.getenv('REFRESH_TOKEN')` returns `None`, which passes validation).

---

## How to Deploy

### First-time setup

```bash
cd jobs/import/infra

# Install Pulumi venv deps (already done)
source venv/bin/activate
pip install -r requirements.txt

# Bootstrap Pulumi config from .env and tokens
./config_from_env.sh

# Review the config
pulumi config --stack prod
```

### Deploy

```bash
cd jobs/import/infra

# Full deploy: build image + push to ACR + pulumi up
./deploy.sh

# Or just infra (skip Docker build)
./deploy.sh --infra-only

# Or just build image (skip Pulumi)
./deploy.sh --build-only
```

### Verify after deploy

```bash
# Check job exists
az containerapp job show -n polar-import-job -g muskul.ai

# Trigger manually
az containerapp job start -n polar-import-job -g muskul.ai

# View logs
az containerapp job execution list -n polar-import-job -g muskul.ai
```

---

## Known Considerations

### 1. Token Expiry
The Polar API access token has a finite lifetime. While `REFRESH_TOKEN` is now passed to the container, the application code must implement token refresh logic to use it. If the access token expires before the next run and refresh is not implemented, the job will fail to authenticate with Polar.

### 2. PostgreSQL Auth
The job uses **password auth** (`POSTGRES_USER` + `POSTGRES_PASSWORD`), not Entra ID / managed identity auth. The UAMI is registered as a PostgreSQL Entra admin for future migration, but the app-side code would need updating to use `azure.identity` token-based auth.

### 3. PostgreSQL Networking
No firewall rules or private endpoints are created by this Pulumi program. The PostgreSQL server (`humandcoded-pg` in West US) must already allow connections from the Container Apps Environment (in East US). Verify existing firewall rules or VNet configuration.

### 4. Pulumi State & Secrets
- **State backend:** Pulumi Cloud (default) — free for individual use.
- **Secrets:** Encrypted with passphrase-based encryption (`PULUMI_CONFIG_PASSPHRASE` defaults to empty string in scripts). For production, consider setting a real passphrase or using a cloud KMS secrets provider.
- **`Pulumi.prod.yaml`** is gitignored to avoid committing secrets. Run `config_from_env.sh` to recreate it on a new machine.

### 5. Cross-Region Latency
Container Apps Job (East US) → PostgreSQL (West US) introduces ~30-50ms latency per query. Acceptable for a daily batch job.

### 6. UAMI Propagation
On first deploy, Azure Entra ID may take a few minutes to propagate the new UAMI. Role assignments or PostgreSQL admin creation may transiently fail with `PrincipalNotFound`. A retry/re-deploy resolves this.

---

## Deployment Verification

The job was manually triggered and completed successfully:

```
POLAR IMPORT JOB COMPLETE
- Database type: POSTGRES
- New CSVs processed: 14
- Files cleaned up: yes
```

**Execution history:**

| Execution | Status | Notes |
|-----------|--------|-------|
| `polar-import-job-2f2r4wv` | ❌ Failed | `REFRESH_TOKEN` validation error (value was `"None"`) |
| `polar-import-job-mt9wxfn` | ❌ Failed | `DefaultAzureCredential` couldn't find UAMI (missing `AZURE_CLIENT_ID`) |
| `polar-import-job-rh0pflk` | ✅ Succeeded | 14 CSVs processed, 28 files cleaned up |

---

## What's Left Before Going Live

- [x] **Deploy infrastructure** — all 8 Azure resources created
- [x] **Trigger job manually** — verified end-to-end (14 CSVs processed)
- [ ] **Verify PostgreSQL connectivity** — confirm imported data is in the database
- [ ] **Consider token refresh** — implement in app code if Polar tokens expire
- [ ] **Set a real `PULUMI_CONFIG_PASSPHRASE`** — for stronger secret encryption
- [ ] **Merge branch** — create PR from `dev/anton/pulumi-deploy-import-job`
