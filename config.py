"""Local runtime configuration for GK Comic.

Put machine-specific secrets and runtime values in this file.
Do not commit real API keys to source control.
"""

import os


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_WORKFLOW_NAME = "Comic8.json"
COMFYUI_WORKFLOW_PATH = os.getenv("COMFYUI_WORKFLOW_PATH", "Comic8_api.json")
COMFYUI_INPUT_DIR = os.getenv("COMFYUI_INPUT_DIR", "")
GK_COMIC_DATA_DIR = ""
GK_UPLOAD_LIMIT_MB = 25
HOST = "0.0.0.0"
PORT = 5000
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", FRONTEND_ORIGIN)
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()
AUTO_OPEN_BROWSER = False
FRONTEND_PATH = "/Portal.html"


def apply() -> None:
    """Expose configured values to backend settings before the app is imported."""
    values = {
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "COMFYUI_URL": COMFYUI_URL,
        "COMFYUI_WORKFLOW_NAME": COMFYUI_WORKFLOW_NAME,
        "COMFYUI_WORKFLOW_PATH": COMFYUI_WORKFLOW_PATH,
        "COMFYUI_INPUT_DIR": COMFYUI_INPUT_DIR,
        "GK_COMIC_DATA_DIR": GK_COMIC_DATA_DIR,
        "GK_UPLOAD_LIMIT_MB": str(GK_UPLOAD_LIMIT_MB),
        "HOST": HOST,
        "PORT": str(PORT),
        "FRONTEND_ORIGIN": FRONTEND_ORIGIN,
        "CORS_ALLOWED_ORIGINS": CORS_ALLOWED_ORIGINS,
        "SESSION_COOKIE_SECURE": str(SESSION_COOKIE_SECURE),
        "SESSION_COOKIE_SAMESITE": SESSION_COOKIE_SAMESITE,
    }
    for key, value in values.items():
        if value:
            os.environ.setdefault(key, value)
