# GitHub Actions ↔ Azure Access Setup

Reference for reproducing the OIDC + RBAC setup that lets the workflows in
[.github/workflows/](./) deploy the Polar import job.

## Identity

We **reuse** the existing app registration `github-actions-intellicv` (already
used by the `intellicv` repo). Do **not** create a new app.

| Value | |
|---|---|
| App display name | `github-actions-intellicv` |
| `AZURE_CLIENT_ID` | `d806dbf8-1574-4792-8139-111009131b14` |
| `AZURE_TENANT_ID` | `7297cdd3-6bcc-4985-915d-6099494801ec` |
| `AZURE_SUBSCRIPTION_ID` | `6a1174a1-2fb7-4a1e-a7d9-6d98ace5ee79` |

## RBAC required

Grant on the **app's service principal** (`appId` above):

| Role | Scope | Why |
|---|---|---|
| `Contributor` | subscription `6a1174a1-...` | Pre-existing. Covers `az acr push`, `az containerapp job update`, Pulumi resource CRUD. |
| `User Access Administrator` | RG `muskul.ai` | Pulumi creates `RoleAssignment`s (storage-blob-data-contributor on `muskulsa`, acr-pull on `muskulacr`). |

Idempotent grant commands:

```bash
APP_ID=d806dbf8-1574-4792-8139-111009131b14
SUB=6a1174a1-2fb7-4a1e-a7d9-6d98ace5ee79
az role assignment create --assignee "$APP_ID" \
  --role "User Access Administrator" \
  --scope "/subscriptions/$SUB/resourceGroups/muskul.ai"
```

## Federated credentials

Add to the same app so GitHub OIDC tokens from this repo are accepted:

```bash
APP_ID=d806dbf8-1574-4792-8139-111009131b14
REPO=antoniantonov/workoutdata

for cred in \
  "github-workoutdata-master:repo:${REPO}:ref:refs/heads/master" \
  "github-workoutdata-tags:repo:${REPO}:ref:refs/tags/*" \
  "github-workoutdata-env-production:repo:${REPO}:environment:production"
do
  NAME="${cred%%:*}"
  SUBJECT="${cred#*:}"
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"$NAME\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }"
done
```

Verify: `az ad app federated-credential list --id "$APP_ID" -o table`

## Azure resources

| Resource | Name | Resource group |
|---|---|---|
| Container Registry | `muskulacr` (`muskulacr.azurecr.io`) | `muskul.ai` |
| Storage account | `muskulsa` | `muskul.ai` |
| Container Apps Job | `polar-import-job` | `muskul.ai` |
| Log Analytics | `polar-import-logs` | `muskul.ai` |

> The registry `humandcoded2` and the PostgreSQL server `humandcoded-pg` were
> deleted along with the `humandcoded` resource group. The ACR was recreated as
> `muskulacr` inside `muskul.ai`, and the job was moved to DuckDB-on-Blob so it
> no longer needs a PostgreSQL server.

## GitHub repository secrets

```bash
REPO=antoniantonov/workoutdata
gh secret set AZURE_CLIENT_ID       --repo "$REPO" --body "d806dbf8-1574-4792-8139-111009131b14"
gh secret set AZURE_TENANT_ID       --repo "$REPO" --body "7297cdd3-6bcc-4985-915d-6099494801ec"
gh secret set AZURE_SUBSCRIPTION_ID --repo "$REPO" --body "6a1174a1-2fb7-4a1e-a7d9-6d98ace5ee79"
# Pulumi stack secrets in Pulumi.prod.yaml are encrypted with an empty
# passphrase locally; mirror that in CI:
printf '' | gh secret set PULUMI_CONFIG_PASSPHRASE --repo "$REPO"
```

## Pulumi: run it locally, not in CI

[import-job-environment-setup.yml](import-job-environment-setup.yml) is **not
usable as-is**. Two prerequisites are missing:

1. `pulumi login` in that workflow requires `PULUMI_ACCESS_TOKEN` (Pulumi Cloud),
   which is not set as a repo secret.
2. `jobs/import/infra/Pulumi.prod.yaml` — which holds the stack config and the
   encrypted secrets — is gitignored, so a CI checkout has no stack config and
   `config.require(...)` fails.

Until both are addressed, apply infrastructure changes from a workstation:

```bash
cd jobs/import/infra
pulumi login file://~          # state lives in ~/.pulumi/stacks/workoutdata-import-job
pulumi stack select prod
pulumi preview
pulumi up
```

Making it work in CI would require creating a Pulumi Cloud token, setting
`PULUMI_ACCESS_TOKEN`, migrating the stack state to Pulumi Cloud, and committing
`Pulumi.prod.yaml` (its secrets are already passphrase-encrypted, and
`PULUMI_CONFIG_PASSPHRASE` is already a repo secret).

The **build** and **deploy** workflows do not use Pulumi and work with the
Azure OIDC secrets alone.

## GitHub environment

Create environment `production` in **GitHub → Settings → Environments**.
Required because [import-job-build.yml](import-job-build.yml),
[import-job-deploy.yml](import-job-deploy.yml)
and [import-job-environment-setup.yml](import-job-environment-setup.yml)
all declare `environment: production` (matches the `github-workoutdata-env-production`
federated credential subject).

The build workflow declares it specifically so it can be dispatched from a
feature branch — the only other federated credentials are for `refs/heads/master`
and `refs/tags/*`. Keep the `production` environment free of deployment branch
policies, or dev-branch dispatches will be rejected.

No environment-scoped secrets needed — the four repo secrets above are
inherited.

## Verification

```bash
# RBAC
az role assignment list --assignee d806dbf8-1574-4792-8139-111009131b14 --all -o table

# Federated creds
az ad app federated-credential list --id d806dbf8-1574-4792-8139-111009131b14 -o table

# GitHub secrets
gh secret list --repo antoniantonov/workoutdata

# End-to-end: trigger build workflow manually and confirm Azure Login step passes
gh workflow run import-job-build.yml --repo antoniantonov/workoutdata
```
