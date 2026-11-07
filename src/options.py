"""The options the admin page can change. Each can still start from its environment variable; a saved value wins."""

from settings import Setting

SETTINGS = [
    Setting("update_check_hours", "Check for updates every (hours)", "float", 6.0, "SERVICE_UPDATE_CHECK_HOURS", 0, 720,
            help="0 = only when you press Check now"),
    Setting("max_inflight", "Calls allowed to wait for the models", "int", 0, "SERVICE_MAX_INFLIGHT", 0, 10000,
            help="Per replica; more /v1/ask calls get 429. 0 = 4"),
    Setting("sync_seconds", "Look for new weights every (seconds)", "float", 10.0, "SERVICE_SYNC_SECONDS", 0, 3600,
            help="Weights another replica downloaded. 0 = off"),
    Setting("cache_size", "Answers kept per replica", "int", 256, "SERVICE_CACHE_SIZE", 0, 100000,
            help="For identical questions. 0 = off"),
    Setting("max_project_models", "Project models loaded per replica", "int", 2, "SERVICE_MAX_PROJECT_MODELS", 1, 64),
    Setting("project_wait_seconds", "Wait for a free project slot (seconds)", "float", 30.0, "SERVICE_PROJECT_WAIT_SECONDS", 0, 600),
    Setting("project_models_dir", "Project models folder", "text", "", "SERVICE_PROJECT_MODELS_DIR",
            help="A folder inside the container. Empty = project models off"),
    Setting("warm", "Load the models at start", "bool", True, "SERVICE_WARM", restart=True),
]
