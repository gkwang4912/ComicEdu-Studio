from __future__ import annotations

import json
import os
import re
import threading
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PROJECT_FILE_NAME = "project.json"
PROJECT_ID_PATTERN = re.compile(r"^prj_[0-9a-f]{12}$")
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
CURRENT_USER_ID: ContextVar[str] = ContextVar("gk_current_user_id", default="system")


def set_current_user(user_id: str) -> Token:
    return CURRENT_USER_ID.set(user_id)


def reset_current_user(token: Token) -> None:
    CURRENT_USER_ID.reset(token)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


class Repository:
    """Persist each project as an inspectable JSON document.

    Every project owns exactly one metadata file:
    data/projects/<project_id>/project.json. Images, prompt logs and other
    generated files stay beside that document in the same project directory.
    """

    def __init__(self, projects_dir: Path):
        self.projects_dir = Path(projects_dir).resolve()
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def ensure_user_session(self, requested_user_id: str | None = None) -> str:
        value = str(requested_user_id or "").strip()
        if value and USER_ID_PATTERN.fullmatch(value):
            return value
        return f"usr_{uuid.uuid4().hex}"

    def _project_path(self, project_id: str) -> Path:
        if not PROJECT_ID_PATTERN.fullmatch(str(project_id)):
            raise KeyError(project_id)
        return self.projects_dir / project_id / PROJECT_FILE_NAME

    def _read_document(self, project_id: str, *, enforce_owner: bool = True) -> dict[str, Any]:
        path = self._project_path(project_id)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise KeyError(project_id) from exc
        if not isinstance(document, dict) or not isinstance(document.get("project"), dict):
            raise KeyError(project_id)
        project = document["project"]
        if project.get("project_id") != project_id:
            raise KeyError(project_id)
        if enforce_owner and project.get("owner_id") != CURRENT_USER_ID.get():
            raise KeyError(project_id)
        document.setdefault("schema_version", SCHEMA_VERSION)
        document.setdefault("pages", [])
        document.setdefault("jobs", [])
        document.setdefault("assets", [])
        return document

    def _write_document(self, document: dict[str, Any]) -> None:
        project_id = str(document["project"]["project_id"])
        path = self._project_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        document["schema_version"] = SCHEMA_VERSION
        temporary = path.with_name(f".{PROJECT_FILE_NAME}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _iter_documents(self, *, enforce_owner: bool = True) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for path in sorted(self.projects_dir.glob(f"prj_*/{PROJECT_FILE_NAME}")):
            try:
                document = self._read_document(path.parent.name, enforce_owner=enforce_owner)
            except KeyError:
                continue
            documents.append(document)
        return documents

    @staticmethod
    def _project_view(document: dict[str, Any], include_pages: bool = True) -> dict[str, Any]:
        result = clone(document["project"])
        if include_pages:
            result["pages"] = clone(sorted(document.get("pages", []), key=lambda item: item["page_index"]))
            for page in result["pages"]:
                page["panels"] = sorted(page.get("panels", []), key=lambda item: item["panel_index"])
        return result

    @staticmethod
    def random_seed() -> int:
        return uuid.uuid4().int % 2_147_483_647

    @classmethod
    def _new_panel(cls, page_id: str, panel_index: int) -> dict[str, Any]:
        return {
            "panel_id": f"pan_{uuid.uuid4().hex[:12]}",
            "page_id": page_id,
            "panel_index": panel_index,
            "positive_prompt": "",
            "negative_prompt": "",
            "text_content": "",
            "seed": cls.random_seed(),
            "status": "draft",
            "revision": 0,
            "generated_image": None,
            "mask_image": None,
            "prompt_json": {},
        }

    @classmethod
    def _new_page(cls, project_id: str, page_index: int, story_arc_page: dict[str, Any]) -> dict[str, Any]:
        page_id = f"pag_{uuid.uuid4().hex[:12]}"
        return {
            "page_id": page_id,
            "project_id": project_id,
            "page_index": page_index,
            "status": "planned",
            "story_arc_page": clone(story_arc_page),
            "script": None,
            "character_recommendation": None,
            "selected_characters": None,
            "layout_recommendation": None,
            "selected_layout": None,
            "final_image": None,
            "revision": 0,
            "panels": [cls._new_panel(page_id, index) for index in range(1, 5)],
        }

    def create_project(
        self, title: str, subject: str, grade_level: str, topic: str,
        page_count: int, teaching_objective: str = "", story_style: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            project_id = f"prj_{uuid.uuid4().hex[:12]}"
            timestamp = now()
            document = {
                "schema_version": SCHEMA_VERSION,
                "project": {
                    "project_id": project_id,
                    "title": title,
                    "status": "initialized",
                    "owner_id": CURRENT_USER_ID.get(),
                    "subject": subject,
                    "grade_level": grade_level,
                    "topic": topic,
                    "teaching_objective": teaching_objective,
                    "story_style": story_style,
                    "page_count": page_count,
                    "material": None,
                    "material_analysis": None,
                    "plans": None,
                    "selected_plan": None,
                    "story_arc": None,
                    "story_arc_confirmed": False,
                    "error": None,
                    "current_generation_page": 0,
                    "orientation": "portrait",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
                "pages": [],
                "jobs": [],
                "assets": [],
            }
            self._write_document(document)
            return self._project_view(document)

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            projects = [self._project_view(item, include_pages=False) for item in self._iter_documents()]
        return sorted(projects, key=lambda item: item.get("updated_at", ""), reverse=True)

    def get_project(self, project_id: str, include_pages: bool = True) -> dict[str, Any]:
        with self._lock:
            return self._project_view(self._read_document(project_id), include_pages)

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            document = self._read_document(project_id)
            self._project_path(document["project"]["project_id"]).unlink()

    def update_project(self, project_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "title", "status", "subject", "grade_level", "topic", "teaching_objective",
            "story_style", "page_count", "material", "material_analysis", "plans",
            "selected_plan", "story_arc", "story_arc_confirmed", "error",
            "current_generation_page", "orientation",
        }
        with self._lock:
            document = self._read_document(project_id)
            project = document["project"]
            for key, value in fields.items():
                if key in allowed:
                    project[key] = clone(value)
            project["updated_at"] = now()
            self._write_document(document)
            return self._project_view(document)

    def replace_story_arc(self, project_id: str, story_arc: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
        with self._lock:
            document = self._read_document(project_id)
            pages_data = story_arc.get("pages") or []
            existing = {page["page_index"]: page for page in document["pages"]}
            pages: list[dict[str, Any]] = []
            for index, page_data in enumerate(pages_data, start=1):
                if index in existing:
                    page = existing[index]
                    page["story_arc_page"] = clone(page_data)
                else:
                    page = self._new_page(project_id, index, page_data)
                pages.append(page)
            pages.extend(
                page for index, page in sorted(existing.items())
                if index > len(pages_data) and page.get("final_image")
            )
            document["pages"] = pages
            project = document["project"]
            project.update({
                "story_arc": clone(story_arc),
                "page_count": len(pages_data),
                "story_arc_confirmed": bool(confirmed),
                "updated_at": now(),
            })
            self._write_document(document)
            return self._project_view(document)

    @staticmethod
    def _page(document: dict[str, Any], page_index: int) -> dict[str, Any]:
        page = next((item for item in document["pages"] if item["page_index"] == page_index), None)
        if page is None:
            raise KeyError((document["project"]["project_id"], page_index))
        return page

    def get_page(self, project_id: str, page_index: int) -> dict[str, Any]:
        with self._lock:
            return clone(self._page(self._read_document(project_id), page_index))

    def update_page(self, project_id: str, page_index: int, **fields: Any) -> dict[str, Any]:
        allowed = {
            "status", "story_arc_page", "script", "character_recommendation",
            "selected_characters", "layout_recommendation", "selected_layout",
            "final_image", "revision",
        }
        with self._lock:
            document = self._read_document(project_id)
            page = self._page(document, page_index)
            for key, value in fields.items():
                if key in allowed:
                    page[key] = clone(value)
            document["project"]["updated_at"] = now()
            self._write_document(document)
            return clone(page)

    def save_script(self, project_id: str, page_index: int, script: list[dict[str, Any]]) -> dict[str, Any]:
        if len(script) != 4:
            raise ValueError("每頁劇本必須剛好四格")
        with self._lock:
            document = self._read_document(project_id)
            page = self._page(document, page_index)
            page["script"] = clone(script)
            page["status"] = "script_ready"
            for index, item in enumerate(script, start=1):
                prompt = item.get("提示詞內容") or item.get("prompt") or {}
                if not isinstance(prompt, dict):
                    prompt = {}
                record = clone(item)
                record.update({"page_index": page_index, "panel_index": index, "序號": index})
                panel = page["panels"][index - 1]
                panel.update({
                    "positive_prompt": prompt.get("描述", ""),
                    "negative_prompt": prompt.get("負向提示詞", ""),
                    "text_content": item.get("文字內容", ""),
                    "prompt_json": record,
                    "status": "draft",
                })
            document["project"]["updated_at"] = now()
            self._write_document(document)
            return clone(page)

    def update_panel(self, project_id: str, page_index: int, panel_index: int, **fields: Any) -> dict[str, Any]:
        if panel_index not in range(1, 5):
            raise ValueError("panel_index 必須為 1-4")
        allowed = {
            "positive_prompt", "negative_prompt", "text_content", "prompt_json", "seed",
            "status", "revision", "generated_image", "mask_image",
        }
        with self._lock:
            document = self._read_document(project_id)
            page = self._page(document, page_index)
            panel = page["panels"][panel_index - 1]
            if any(key in fields for key in ("positive_prompt", "negative_prompt", "text_content")) and "prompt_json" not in fields:
                record = clone(panel.get("prompt_json") or {})
                prompt = record.get("提示詞內容")
                if not isinstance(prompt, dict):
                    prompt = {}
                prompt["描述"] = fields.get("positive_prompt", panel.get("positive_prompt", ""))
                prompt["負向提示詞"] = fields.get("negative_prompt", panel.get("negative_prompt", ""))
                record.update({
                    "page_index": page_index,
                    "panel_index": panel_index,
                    "序號": panel_index,
                    "提示詞內容": prompt,
                    "文字內容": fields.get("text_content", panel.get("text_content", "")),
                })
                fields["prompt_json"] = record
            for key, value in fields.items():
                if key in allowed:
                    panel[key] = clone(value)
            document["project"]["updated_at"] = now()
            self._write_document(document)
            return clone(panel)

    def create_job(
        self, project_id: str, job_type: str, page_index: int | None = None,
        panel_index: int | None = None, stage: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            document = self._read_document(project_id)
            job = {
                "job_id": f"job_{uuid.uuid4().hex[:12]}",
                "project_id": project_id,
                "page_index": page_index,
                "panel_index": panel_index,
                "job_type": job_type,
                "stage": stage,
                "status": "queued",
                "progress": None,
                "message": "等待執行",
                "comfy_prompt_id": None,
                "error": None,
                "created_at": now(),
                "started_at": None,
                "finished_at": None,
            }
            document["jobs"].append(job)
            document["project"]["updated_at"] = now()
            self._write_document(document)
            return clone(job)

    def _find_job(self, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for document in self._iter_documents():
            job = next((item for item in document["jobs"] if item["job_id"] == job_id), None)
            if job is not None:
                return document, job
        raise KeyError(job_id)

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "page_index", "panel_index", "stage", "status", "progress", "message",
            "comfy_prompt_id", "error", "started_at", "finished_at",
        }
        with self._lock:
            document, job = self._find_job(job_id)
            for key, value in fields.items():
                if key in allowed:
                    job[key] = clone(value)
            self._write_document(document)
            return clone(job)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            _, job = self._find_job(job_id)
            return clone(job)

    def list_jobs(self, project_id: str, after: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            jobs = clone(self._read_document(project_id)["jobs"])
        if after:
            return sorted((item for item in jobs if item["created_at"] >= after), key=lambda item: item["created_at"])
        return sorted(jobs, key=lambda item: item["created_at"], reverse=True)[:50]

    def recover_interrupted_generation_jobs(self) -> list[str]:
        generation_types = {"legacy_generate_all", "generate_page", "regenerate_panel"}
        recovered: list[str] = []
        with self._lock:
            for document in self._iter_documents(enforce_owner=False):
                changed = False
                for job in document["jobs"]:
                    if job.get("status") in {"queued", "running"} and job.get("job_type") in generation_types:
                        timestamp = now()
                        job.update({
                            "status": "interrupted",
                            "message": "後端重新啟動，工作已轉為接續模式",
                            "error": "背景工作因後端中斷而停止",
                            "finished_at": timestamp,
                        })
                        changed = True
                if changed:
                    for page in document["pages"]:
                        for panel in page.get("panels", []):
                            if panel.get("status") == "generating":
                                panel["status"] = "pending"
                    document["project"].update({
                        "status": "generation_interrupted", "error": None, "updated_at": now(),
                    })
                    self._write_document(document)
                    recovered.append(document["project"]["project_id"])
        return sorted(recovered)

    def mark_generation_transport_interrupted(self, job_id: str, project_id: str, error: str) -> None:
        with self._lock:
            document = self._read_document(project_id)
            job = next((item for item in document["jobs"] if item["job_id"] == job_id), None)
            if job is None:
                raise KeyError(job_id)
            timestamp = now()
            job.update({
                "status": "interrupted",
                "message": "後端或 ComfyUI 連線中斷，工作已轉為接續模式",
                "error": error,
                "finished_at": timestamp,
            })
            for page in document["pages"]:
                for panel in page.get("panels", []):
                    if panel.get("status") == "generating":
                        panel["status"] = "pending"
            document["project"].update({
                "status": "generation_interrupted", "error": None, "updated_at": timestamp,
            })
            self._write_document(document)

    def add_asset(
        self, project_id: str, kind: str, path: str, page_index: int | None = None,
        panel_index: int | None = None, revision: int = 0, metadata: Any = None,
    ) -> dict[str, Any]:
        with self._lock:
            document = self._read_document(project_id)
            asset = {
                "asset_id": f"ast_{uuid.uuid4().hex[:12]}",
                "project_id": project_id,
                "page_index": page_index,
                "panel_index": panel_index,
                "kind": kind,
                "path": path,
                "revision": revision,
                "metadata": clone(metadata or {}),
                "created_at": now(),
            }
            document["assets"].append(asset)
            document["project"]["updated_at"] = now()
            self._write_document(document)
            return clone(asset)

    def touch(self, project_id: str) -> None:
        with self._lock:
            document = self._read_document(project_id)
            document["project"]["updated_at"] = now()
            self._write_document(document)
