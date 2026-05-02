"""Constants for the Missions layer."""

# Default organization UUID for single-business local servers.
# All mission tables reference this org by default in v1.
# Multi-tenant deployments override this per request.
DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000001"

# Mission Manager service port (within Cascadia 6200-6207 range)
MISSION_MANAGER_PORT = 6207

# Mission run statuses
MISSION_RUN_STATUS_PENDING = "pending"
MISSION_RUN_STATUS_RUNNING = "running"
MISSION_RUN_STATUS_PAUSED = "paused_for_approval"
MISSION_RUN_STATUS_WAITING_RETRY = "waiting_retry"
MISSION_RUN_STATUS_COMPLETED = "completed"
MISSION_RUN_STATUS_FAILED = "failed"

VALID_MISSION_RUN_STATUSES = {
    MISSION_RUN_STATUS_PENDING,
    MISSION_RUN_STATUS_RUNNING,
    MISSION_RUN_STATUS_PAUSED,
    MISSION_RUN_STATUS_WAITING_RETRY,
    MISSION_RUN_STATUS_COMPLETED,
    MISSION_RUN_STATUS_FAILED,
}

# Retry policy
MAX_AUTO_RETRY_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = [30, 120]
