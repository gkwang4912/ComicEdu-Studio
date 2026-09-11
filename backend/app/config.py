from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMFY_INPUT_DIR_TEXT = os.getenv("COMFYUI_INPUT_DIR", "").strip()
WORKFLOW_PATH_TEXT = os.getenv("COMFYUI_WORKFLOW_PATH", "Comic8_api.json").strip()
WORKFLOW_PATH = Path(WORKFLOW_PATH_TEXT)
if not WORKFLOW_PATH.is_absolute():
    WORKFLOW_PATH = ROOT / WORKFLOW_PATH


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = Path(os.getenv("GK_COMIC_DATA_DIR", ROOT / "data"))
    frontend_dir: Path = ROOT / "ComicEdu-Web"
    workflow_path: Path = WORKFLOW_PATH
    characters_json: Path = ROOT / "file" / "characters" / "characters.json"
    characters_dir: Path = ROOT / "file" / "characters" / "characters"
    layouts_json: Path = ROOT / "file" / "layouts" / "layouts.json"
    layouts_dir: Path = ROOT / "file" / "layouts" / "layouts"
    comfy_url: str = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    comfy_workflow_name: str = os.getenv("COMFYUI_WORKFLOW_NAME", "Comic8.json")
    comfy_input_dir: Path | None = Path(COMFY_INPUT_DIR_TEXT) if COMFY_INPUT_DIR_TEXT else None
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    upload_limit_mb: int = int(os.getenv("GK_UPLOAD_LIMIT_MB", "25"))
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "5000"))
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "").strip()
    cors_allowed_origins: tuple[str, ...] = tuple(filter(None, os.getenv("CORS_ALLOWED_ORIGINS", os.getenv("FRONTEND_ORIGIN", "")).split(",")))
    session_cookie_secure: bool = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    session_cookie_samesite: str = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.projects_dir.mkdir(parents=True, exist_ok=True)
