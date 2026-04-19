"""Pulumi program to deploy the Polar import job as an Azure Container Apps Job.

Resources deployed:
- User Assigned Managed Identity (UAMI)
- Role assignments (ACR Pull, Storage Blob Data Contributor)
- PostgreSQL Entra ID administrator for UAMI
- Log Analytics Workspace
- Container Apps Environment
- Container Apps Job (daily cron schedule)

Existing resources referenced:
- Resource Group: muskul.ai (East US)
- ACR: humandcoded2 (in humandcoded RG)
- Storage Account: muskulsa (in muskul.ai RG)
- PostgreSQL Server: humandcoded-pg (in humandcoded RG)
"""
import pulumi
import pulumi_azure_native as azure_native
from pulumi_azure_native import (
    managedidentity,
    authorization,
    operationalinsights,
    app,
    dbforpostgresql,
)

config = pulumi.Config()

# =============================================================================
# Constants for existing resources
# =============================================================================
RESOURCE_GROUP_NAME = "muskul.ai"
LOCATION = "eastus"

ACR_NAME = "humandcoded2"
ACR_RESOURCE_GROUP = "humandcoded"
ACR_LOGIN_SERVER = "humandcoded2.azurecr.io"

STORAGE_ACCOUNT_NAME = "muskulsa"

PG_SERVER_NAME = "humandcoded-pg"
PG_RESOURCE_GROUP = "humandcoded"

# =============================================================================
# Look up existing resources
# =============================================================================
current = azure_native.authorization.get_client_config()
subscription_id = current.subscription_id

# Well-known Azure role definition IDs (must include subscription prefix)
ROLE_ACR_PULL = f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
ROLE_STORAGE_BLOB_DATA_CONTRIBUTOR = f"/subscriptions/{subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"

acr = azure_native.containerregistry.get_registry(
    registry_name=ACR_NAME,
    resource_group_name=ACR_RESOURCE_GROUP,
)

storage_account = azure_native.storage.get_storage_account(
    account_name=STORAGE_ACCOUNT_NAME,
    resource_group_name=RESOURCE_GROUP_NAME,
)

pg_server = azure_native.dbforpostgresql.get_server(
    server_name=PG_SERVER_NAME,
    resource_group_name=PG_RESOURCE_GROUP,
)

# =============================================================================
# User Assigned Managed Identity
# =============================================================================
uami = managedidentity.UserAssignedIdentity(
    "import-job-identity",
    resource_group_name=RESOURCE_GROUP_NAME,
    resource_name_="polar-import-job-identity",
    location=LOCATION,
    tags={"purpose": "polar-import-job"},
)

# =============================================================================
# Role Assignments
# =============================================================================

# ACR Pull — allows the UAMI to pull images from the existing ACR
acr_pull_role = authorization.RoleAssignment(
    "acr-pull-role",
    principal_id=uami.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=ROLE_ACR_PULL,
    scope=acr.id,
)

# Storage Blob Data Contributor — allows the UAMI to read/write blobs
storage_role = authorization.RoleAssignment(
    "storage-blob-contributor-role",
    principal_id=uami.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=ROLE_STORAGE_BLOB_DATA_CONTRIBUTOR,
    scope=storage_account.id,
)

# =============================================================================
# PostgreSQL Entra ID Administrator for UAMI
# Note: The job currently uses password auth. This prepares for future
# migration to Entra ID (managed identity) auth for PostgreSQL.
# =============================================================================
pg_admin = dbforpostgresql.Administrator(
    "pg-uami-admin",
    server_name=PG_SERVER_NAME,
    resource_group_name=PG_RESOURCE_GROUP,
    principal_type=dbforpostgresql.PrincipalType.SERVICE_PRINCIPAL,
    principal_name=uami.name,
    object_id=uami.principal_id,
    tenant_id=current.tenant_id,
)

# =============================================================================
# Log Analytics Workspace (required for Container Apps Environment)
# =============================================================================
log_workspace = operationalinsights.Workspace(
    "import-job-logs",
    resource_group_name=RESOURCE_GROUP_NAME,
    workspace_name="polar-import-logs",
    location=LOCATION,
    sku=operationalinsights.WorkspaceSkuArgs(
        name="PerGB2018",
    ),
    retention_in_days=30,
)

# Get the shared key for the workspace
log_workspace_keys = operationalinsights.get_shared_keys_output(
    resource_group_name=RESOURCE_GROUP_NAME,
    workspace_name=log_workspace.name,
)

# =============================================================================
# Container Apps Environment
# =============================================================================
container_env = app.ManagedEnvironment(
    "import-job-env",
    resource_group_name=RESOURCE_GROUP_NAME,
    environment_name="polar-import-env",
    location=LOCATION,
    app_logs_configuration=app.AppLogsConfigurationArgs(
        destination="log-analytics",
        log_analytics_configuration=app.LogAnalyticsConfigurationArgs(
            customer_id=log_workspace.customer_id,
            shared_key=log_workspace_keys.primary_shared_key,
        ),
    ),
)

# =============================================================================
# Container Apps Job — Environment Variables
# =============================================================================

# Read configuration values (set via `pulumi config set`)
polar_client_id = config.require("polar-client-id")
polar_client_secret = config.require_secret("polar-client-secret")
polar_redirect_port = config.get("polar-redirect-port") or "5001"
polar_member_id = config.require("polar-member-id")

database_type = config.get("database-type") or "postgres"
postgres_host = config.require("postgres-host")
postgres_port = config.get("postgres-port") or "5432"
postgres_database = config.get("postgres-database") or "workoutdata"
postgres_user = config.require("postgres-user")
postgres_password = config.require_secret("postgres-password")

azure_storage_account = config.get("azure-storage-account") or STORAGE_ACCOUNT_NAME
azure_storage_container = config.get("azure-storage-container") or "workoutdata"

access_token = config.require_secret("access-token")
token_type = config.get("token-type") or "bearer"

image_tag = config.require("image-tag")
cron_schedule = config.get("cron-schedule") or "0 6 * * *"

image_name = f"{ACR_LOGIN_SERVER}/polar-import-job:{image_tag}"

# =============================================================================
# Container Apps Job
# =============================================================================
container_job = app.Job(
    "polar-import-job",
    resource_group_name=RESOURCE_GROUP_NAME,
    job_name="polar-import-job",
    location=LOCATION,
    environment_id=container_env.id,
    configuration=app.JobConfigurationArgs(
        trigger_type=app.TriggerType.SCHEDULE,
        replica_timeout=1800,  # 30 minutes max
        replica_retry_limit=1,
        schedule_trigger_config=app.JobConfigurationScheduleTriggerConfigArgs(
            cron_expression=cron_schedule,
            parallelism=1,
            replica_completion_count=1,
        ),
        registries=[
            app.RegistryCredentialsArgs(
                server=ACR_LOGIN_SERVER,
                identity=uami.id,
            ),
        ],
        secrets=[
            app.SecretArgs(name="polar-client-secret", value=polar_client_secret),
            app.SecretArgs(name="postgres-password", value=postgres_password),
            app.SecretArgs(name="access-token", value=access_token),
        ],
    ),
    template=app.JobTemplateArgs(
        containers=[
            app.ContainerArgs(
                name="polar-import",
                image=image_name,
                resources=app.ContainerResourcesArgs(
                    cpu=0.5,
                    memory="1Gi",
                ),
                env=[
                    # Polar API config
                    app.EnvironmentVarArgs(name="POLAR_CLIENT_ID", value=polar_client_id),
                    app.EnvironmentVarArgs(name="POLAR_CLIENT_SECRET", secret_ref="polar-client-secret"),
                    app.EnvironmentVarArgs(name="POLAR_REDIRECT_PORT", value=polar_redirect_port),
                    app.EnvironmentVarArgs(name="POLAR_MEMBER_ID", value=polar_member_id),
                    app.EnvironmentVarArgs(name="ALLOW_PORT_FALLBACK", value="true"),

                    # Database config
                    app.EnvironmentVarArgs(name="DATABASE_TYPE", value=database_type),
                    app.EnvironmentVarArgs(name="POSTGRES_HOST", value=postgres_host),
                    app.EnvironmentVarArgs(name="POSTGRES_PORT", value=postgres_port),
                    app.EnvironmentVarArgs(name="POSTGRES_DATABASE", value=postgres_database),
                    app.EnvironmentVarArgs(name="POSTGRES_USER", value=postgres_user),
                    app.EnvironmentVarArgs(name="POSTGRES_PASSWORD", secret_ref="postgres-password"),

                    # Azure Storage config
                    app.EnvironmentVarArgs(name="AZURE_STORAGE_ENABLED", value="true"),
                    app.EnvironmentVarArgs(name="AZURE_STORAGE_ACCOUNT_NAME", value=azure_storage_account),
                    app.EnvironmentVarArgs(name="AZURE_STORAGE_CONTAINER_NAME", value=azure_storage_container),
                    # UAMI client ID for DefaultAzureCredential
                    app.EnvironmentVarArgs(name="AZURE_CLIENT_ID", value=uami.client_id),

                    # OAuth tokens (loaded from env instead of tokens_polar.json)
                    app.EnvironmentVarArgs(name="ACCESS_TOKEN", secret_ref="access-token"),
                    app.EnvironmentVarArgs(name="TOKEN_TYPE", value=token_type),

                    # File paths (relative to /app inside container)
                    app.EnvironmentVarArgs(name="OUTPUT_DIR", value="local_data"),
                    app.EnvironmentVarArgs(name="VO2MAX_DATA_PATH", value="data/v02max_data.csv"),
                    app.EnvironmentVarArgs(name="ZONES_CSV_PATH", value="hr_data/zones.csv"),

                    # Container flag
                    app.EnvironmentVarArgs(name="IN_CONTAINER", value="true"),
                ],
            ),
        ],
    ),
    identity=app.ManagedServiceIdentityArgs(
        type=app.ManagedServiceIdentityType.USER_ASSIGNED,
        user_assigned_identities=[uami.id],
    ),
    opts=pulumi.ResourceOptions(depends_on=[acr_pull_role]),
)

# =============================================================================
# Exports
# =============================================================================
pulumi.export("uami_principal_id", uami.principal_id)
pulumi.export("uami_client_id", uami.client_id)
pulumi.export("container_job_name", container_job.name)
pulumi.export("container_env_name", container_env.name)
pulumi.export("image", image_name)
pulumi.export("acr_login_server", ACR_LOGIN_SERVER)
pulumi.export("cron_schedule", cron_schedule)
