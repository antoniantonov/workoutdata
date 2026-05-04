# TODO

## Storage account management (`muskulsa`)

Today the `muskulsa` storage account in resource group `muskul.ai` is referenced
from the import-job Pulumi stack via `azure_native.storage.get_storage_account(...)`
(see [jobs/import/infra/__main__.py](../jobs/import/infra/__main__.py)).
The storage account itself is **not** managed by IaC, so the firewall
`virtualNetworkRule` for the ACA subnet is patched in via a `pulumi_command`
local CLI step (`muskulsa-aca-vnet-rule`). This is brittle: drift between the
portal and the stack is invisible, the network rule can be wiped by anyone
editing the storage account directly, and we cannot enforce settings like
`publicNetworkAccess`, `allowSharedKeyAccess`, blob versioning, lifecycle
policies, etc.

### Task 1 — Bring `muskulsa` under Pulumi management (preferred)

Goal: have the storage account, its containers, and its `networkAcls` fully
defined in [jobs/import/infra/__main__.py](../jobs/import/infra/__main__.py).

Steps:
1. Replace the `get_storage_account(...)` lookup with a real
   `azure_native.storage.StorageAccount(...)` resource that mirrors the current
   muskulsa configuration (SKU, kind, `minimumTlsVersion`, `allowBlobPublicAccess`,
   `allowSharedKeyAccess`, network rules incl. existing IP allowlist, etc.).
2. Define the existing blob containers (e.g. `workoutdata`) as
   `azure_native.storage.BlobContainer` resources.
3. Add the ACA subnet as a `network_acls.virtual_network_rules` entry directly
   on the resource — no more `pulumi_command` workaround.
4. Run `pulumi import azure-native:storage:StorageAccount muskulsa <id>` and
   `pulumi import azure-native:storage:BlobContainer ...` for each container so
   Pulumi adopts existing state without recreating data.
5. Delete the `storage_vnet_rule` `local.Command` block and remove
   `pulumi-command` from [requirements.txt](../jobs/import/infra/requirements.txt)
   if no other Command resource remains.
6. Update [docs/POSTGRES_SETUP.md](POSTGRES_SETUP.md) / README to reflect that
   the storage account is now stack-owned.

Risk / unknowns:
- `pulumi import` on a storage account that holds production data is safe in
  principle (no data loss), but property drift between Pulumi state and the
  actual resource will surface as a diff on the next `pulumi up`. The first
  `pulumi up` after import must be reviewed line-by-line.
- The IP allowlist on muskulsa contains personal/home IPs. Keep them as
  Pulumi config values, not hard-coded literals.

### Task 2 — Fallback: new storage account + one-time migration

Only if Task 1 is blocked (e.g. cross-stack ownership conflicts, governance,
Pulumi-managed lifecycle would break unrelated consumers of muskulsa).

Goal: create a fresh storage account managed by the import-job Pulumi stack,
move the workout-data container to it, and retire the old account from the
import job's perspective.

Steps:
1. In [jobs/import/infra/__main__.py](../jobs/import/infra/__main__.py), define
   a new `azure_native.storage.StorageAccount` (e.g. `muskulimportsa`) plus the
   `workoutdata` container, with VNet rule for the ACA subnet baked in.
2. Add a one-time migration script
   `scripts/migrate_workoutdata_storage.sh` that:
   - Authenticates with `az login` (or accepts a SAS).
   - Uses `azcopy sync` (preferred) or `az storage blob copy start-batch`
     to copy every blob from `muskulsa/workoutdata` to
     `muskulimportsa/workoutdata`.
   - Verifies counts and checksums (`az storage blob list ... --query 'length(@)'`
     on both sides; spot-check `Content-MD5`).
   - Prints a final summary and exits non-zero on any mismatch.
   - Idempotent: safe to re-run; should detect already-copied blobs and skip.
3. Update [polar/cloud/azure.py](../polar/cloud/azure.py) and the import-job
   Pulumi env vars (`AZURE_STORAGE_ACCOUNT_NAME`) to point to the new account.
4. After a few successful job runs against the new account, decommission the
   `workoutdata` container on `muskulsa` (manual; do **not** delete the
   storage account itself since other workloads may still use it).
5. Document the migration outcome in
   `docs/reports/<DATE>_storage_migration.md`.

Acceptance criteria:
- New account created via `pulumi up` with no manual portal steps.
- Migration script runs cleanly end-to-end against a small test container
  before being run against `workoutdata`.
- Import job successfully writes new blobs to the new account and reads them
  back via the existing query/audit notebooks.
