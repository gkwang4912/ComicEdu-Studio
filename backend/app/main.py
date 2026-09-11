from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from xml.etree import ElementTree

import fitz
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from .comfyui_client import ComfyUIClient
from .config import settings
from .jobs import JobManager
from .repository import Repository, reset_current_user, set_current_user
from .schemas import (CharacterSelection, LayoutSelection, MaterialText, PanelUpdate,
                      PlanSelection, ProjectCreate, RegenerateRequest, RevisionRequest,
                      ScriptUpdate, StoryArcUpdate)
from .service import ComicService
from .workflow_adapter import WorkflowAdapter


repository = Repository(settings.projects_dir)
comfy_client = ComfyUIClient(settings.comfy_url, settings.comfy_input_dir)
adapter = WorkflowAdapter(settings.workflow_path, comfy_client, settings.comfy_workflow_name)
service = ComicService(settings, repository, comfy_client, adapter)
jobs = JobManager(repository)

app = FastAPI(title="GK Comic API", version="1.0.0", description="Local-first educational comic production API")
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_allowed_origins), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

FLOW_PAGES = ["1_劇本構思.html", "2_角色設定.html", "3_分鏡配置.html", "4_AI生圖.html", "5_匯出分享.html"]


@app.on_event("startup")
def recover_jobs_after_restart() -> None:
    repository.recover_interrupted_generation_jobs()


def project_completed_step(project: dict[str, Any]) -> int:
    pages = project.get("pages") or []
    if (
        not project.get("story_arc_confirmed")
        or not pages
        or not all(item.get("script") for item in pages)
    ):
        return 1
    if not all(item.get("selected_characters") for item in pages):
        return 2
    if not all(item.get("selected_layout") for item in pages):
        return 3
    if not all(item.get("final_image") for item in pages):
        return 4
    return 5


@app.middleware("http")
async def user_session(request: Request, call_next: Callable) -> Response:
    user_id = repository.ensure_user_session(request.cookies.get("gk_user_session"))
    token = set_current_user(user_id)
    try:
        response: Response | None = None
        stage_match = re.search(r"/([1-5])_", request.url.path)
        project_id = request.query_params.get("projectId")
        if request.method == "GET" and stage_match and int(stage_match.group(1)) > 1 and not project_id:
            response = RedirectResponse(f"/{FLOW_PAGES[0]}", status_code=303)
        elif request.method == "GET" and stage_match and project_id:
            try:
                completed_step = project_completed_step(repository.get_project(project_id))
                requested_step = int(stage_match.group(1))
                if requested_step > completed_step:
                    target = FLOW_PAGES[completed_step - 1]
                    response = RedirectResponse(f"/{target}?projectId={quote(project_id)}", status_code=303)
            except KeyError:
                pass
        if response is None:
            response = await call_next(request)
    finally:
        reset_current_user(token)
    if request.cookies.get("gk_user_session") != user_id:
        response.set_cookie(
            "gk_user_session",
            user_id,
            httponly=True,
            samesite=settings.session_cookie_samesite,
            secure=settings.session_cookie_secure,
            max_age=31_536_000,
        )
    return response


class LegacyCharacterSelection(BaseModel):
    selectedCharacterIds: list[str]


class LegacyStoryboardSelection(BaseModel):
    selectedLayoutName: str | None = None
    pages: list[dict[str, Any]] | None = None
    orientation: str | None = None


class LegacyScriptUpdate(BaseModel):
    script: list[dict[str, Any]]


class LegacySceneRevision(BaseModel):
    instruction: str


class LegacyPanelEdit(BaseModel):
    prompt: str | None = None
    dialogueText: str | None = None
    seed: int | None = None


class LegacyMaterialPlanSelection(BaseModel):
    planIndex: int


class LegacyLongScriptRequest(BaseModel):
    pageCount: int = 3


class LegacyConfirmStoryArc(BaseModel):
    storyArc: dict[str, Any] | None = None


class LegacyMultiPageStoryboard(BaseModel):
    pages: list[dict[str, Any]]
    orientation: str | None = None


def latest_job(project_id: str, *types: str) -> dict[str, Any] | None:
    return next((job for job in repository.list_jobs(project_id) if job["job_type"] in types), None)


def legacy_status(project: dict[str, Any]) -> str:
    if project.get("error") or project.get("status") == "failed":
        return "failed"
    page = (project.get("pages") or [{}])[0]
    if project.get("status") == "analyzing_material":
        return "analyzing_material"
    if project.get("status") == "generation_interrupted":
        return "generation_interrupted"
    if project.get("plans") and not project.get("selected_plan"):
        return "material_analyzed"
    generation = latest_job(project["project_id"], "legacy_generate_all", "generate_page", "regenerate_panel")
    if generation and generation["status"] in {"queued", "running"}:
        return "generating_panels"
    script_job = latest_job(project["project_id"], "legacy_confirm_story_arc", "legacy_generate_long")
    if script_job and script_job["status"] in {"queued", "running"}:
        if project.get("story_arc") and not all(item.get("script") for item in project.get("pages") or []):
            return "generating_page_scripts"
        return "generating_script"
    if project.get("story_arc") and not all(item.get("script") for item in project.get("pages") or []):
        return "story_arc_generated"
    if any(item.get("final_image") for item in project.get("pages") or []):
        if all(item.get("final_image") for item in project.get("pages") or []):
            return "panels_completed"
    if len(project.get("pages") or []) == 1 and page.get("final_image"):
        return "panels_completed"
    layout_job = latest_job(project["project_id"], "legacy_recommend_layouts", "recommend_layouts")
    if layout_job and layout_job["status"] in {"queued", "running"}:
        return "selecting_layouts"
    if page.get("layout_recommendation"):
        return "layouts_selected"
    script_job = latest_job(project["project_id"], "legacy_generate_script")
    if script_job and script_job["status"] in {"queued", "running"}:
        return "generating_script"
    if page.get("script"):
        return "script_generated"
    return "initialized"


def legacy_project(project: dict[str, Any]) -> dict[str, Any]:
    page = (project.get("pages") or [{}])[0]
    recommendation = page.get("layout_recommendation") or {}
    selected_characters = page.get("selected_characters") or []
    pages = project.get("pages") or []
    story_arc = project.get("story_arc") or {}
    page_summaries = []
    for index, item in enumerate(story_arc.get("pages") or [], start=1):
        page_summaries.append({
            "pageNumber": item.get("page_number", index),
            "title": item.get("page_title", item.get("title", f"第 {index} 頁")),
            "summary": item.get("summary", ""),
            "keyKnowledgePoints": item.get("key_knowledge_points", []),
            "storyRole": item.get("story_role", ""),
            "storyEvent": item.get("story_event", ""),
            "visualFocus": item.get("visual_focus", ""),
            "endingHook": item.get("ending_hook", ""),
        })
    page_scripts = [item.get("script") or [] for item in pages]
    selected_layout_pages = [
        {"pageIndex": index, "selectedLayoutName": item.get("selected_layout")}
        for index, item in enumerate(pages)
        if item.get("selected_layout")
    ]
    recommendations_by_page = [
        (item.get("layout_recommendation") or {}).get("candidates", [])
        for item in pages
    ]
    completed_step = project_completed_step(project)
    return {
        "id": project["project_id"],
        "createdAt": project.get("created_at"),
        "status": legacy_status(project),
        "pageCount": project.get("page_count", len(pages) or 1),
        "currentGeneratingPage": project.get("current_generation_page", 0),
        "completedStep": completed_step,
        "settings": {
            "topic": project.get("topic", ""),
            "subject": project.get("subject", ""),
            "gradeLevel": project.get("grade_level", ""),
            "teachingObjective": project.get("teaching_objective", ""),
            "storyStyle": project.get("story_style", ""),
        },
        "script": page.get("script"),
        "multiPageScript": page_scripts if any(page_scripts) else None,
        "storyArc": {**story_arc, "pageSummaries": page_summaries} if story_arc else None,
        "storyArcConfirmed": bool(project.get("story_arc_confirmed")),
        "materialPlans": project.get("plans") or [],
        "characters": {"selectedCharacterIds": [str(value) for value in selected_characters]},
        "layout": {
            "selectedLayoutName": page.get("selected_layout"),
            "orientation": project.get("orientation", "portrait"),
            "pages": selected_layout_pages,
        },
        "recommended_layouts": recommendation.get("candidates", []),
        "perPageRecommendedLayouts": recommendations_by_page,
        "panels": {str(index): value for index, value in enumerate(page.get("panels") or [])},
        "export": page.get("final_image"),
        "exportPages": [item.get("final_image") for item in pages if item.get("final_image")],
        "error": project.get("error"),
    }


def panel_positions(page: dict[str, Any]) -> tuple[dict[str, int], list[dict[str, int]]]:
    return service.panel_positions(page)


def legacy_panels(project_id: str, page_index: int | None = None) -> dict[str, Any]:
    project = repository.get_project(project_id)
    pages = project.get("pages") or [{}]
    requested_index = page_index if page_index is not None and page_index >= 0 else project.get("current_generation_page", 0)
    requested_index = max(0, min(requested_index, len(pages) - 1))
    page = dict(pages[requested_index])
    base_path = settings.projects_dir / project_id / f"page_{requested_index + 1}" / "base.png"
    preview_path = settings.projects_dir / project_id / f"page_{requested_index + 1}" / "preview_partial.png"
    page["base_image"] = str(base_path)
    page["layout_image"] = str(settings.layouts_dir / (page.get("selected_layout") or ""))
    canvas, positions = panel_positions(page)
    panels = []
    for index, panel in enumerate(page.get("panels") or []):
        panels.append({
            "id": index,
            "status": "completed" if panel.get("generated_image") else panel.get("status", "pending"),
            "imagePath": f"/api/v1/projects/{project_id}/pages/{requested_index + 1}/panels/{index + 1}/image" if panel.get("generated_image") else None,
            "prompt": panel.get("positive_prompt", ""),
            "dialogueText": panel.get("text_content", ""),
            "panelPrompt": panel.get("prompt_json") or {},
            "seed": panel.get("seed"),
        })
    completed = sum(1 for panel in panels if panel["status"] == "completed")
    return {
        "status": legacy_status(project),
        "totalProgress": int(completed / 4 * 100),
        "completedPanels": completed,
        "totalPanels": 4,
        "canvasSize": canvas,
        "panelPositions": positions,
        "panels": panels,
        "previewAvailable": bool(page.get("final_image") or preview_path.is_file() or base_path.is_file()),
        "previewVersion": preview_path.stat().st_mtime_ns if preview_path.is_file() else (base_path.stat().st_mtime_ns if base_path.is_file() else 0),
        "currentGeneratingPage": project.get("current_generation_page", 0),
        "pageIndex": requested_index,
        "orientation": project.get("orientation", "portrait"),
        "error": project.get("error"),
    }


def fail_not_found(exc: KeyError) -> None:
    raise HTTPException(status_code=404, detail="找不到指定資料") from exc


def queue(project_id: str, job_type: str, stage: int, operation: Callable, page_index: int | None = None, panel_index: int | None = None) -> dict[str, Any]:
    try:
        repository.get_project(project_id, include_pages=False)
    except KeyError as exc:
        fail_not_found(exc)
    job = repository.create_job(project_id, job_type, page_index, panel_index, stage)
    return jobs.submit(job, operation)


def require_generation_nodes() -> None:
    if "DialogueTextCorrector" not in comfy_client.object_info():
        raise HTTPException(
            status_code=503,
            detail="ComfyUI 尚未載入 DialogueTextCorrector；請重新啟動 ComfyUI 後再開始生圖。",
        )


def startup_path_diagnostics(log: bool = True) -> dict[str, int]:
    """Report deployment path issues without making them fatal."""
    warnings = 0
    errors = 0
    print("[ComicEdu][Startup] ========================================", flush=True) if log else None
    print("[ComicEdu][Startup] Deployment diagnostics", flush=True) if log else None
    checks = [("Backend root", settings.root), ("Data dir", settings.data_dir), ("Frontend dir", settings.frontend_dir),
              ("Workflow", settings.workflow_path), ("Characters metadata", settings.characters_json), ("Layouts metadata", settings.layouts_json)]
    for name, path in checks:
        ok = path.exists()
        if not ok: warnings += 1
        if log: print(f"[{'OK' if ok else 'WARN'}] {name}: {path}", flush=True)
    if log: print(f"[OK] Backend host: {settings.host}", flush=True); print(f"[OK] Backend port: {settings.port}", flush=True)
    if log: print(f"[OK] ComfyUI URL: {settings.comfy_url}", flush=True)
    if settings.comfy_input_dir is None:
        warnings += 1
        if log: print("[WARN] COMFYUI_INPUT_DIR is not configured", flush=True)
    elif log: print(f"[OK] ComfyUI input dir: {settings.comfy_input_dir}", flush=True)
    try:
        workflow = json.loads(settings.workflow_path.read_text(encoding="utf-8"))
        path_pattern = re.compile(r"^(?:[A-Za-z]:[\\/]|/).+")
        for node_id, node in workflow.items():
            values = node.get("inputs", {}) if isinstance(node, dict) else {}
            for name, value in values.items():
                if isinstance(value, str) and path_pattern.match(value):
                    is_windows = bool(re.match(r"^[A-Za-z]:[\\/]", value))
                    exists = Path(value).exists() if not is_windows else False
                    if is_windows or not exists: warnings += 1
                    if log: print(f"[PATH][{'WARN' if (is_windows or not exists) else 'OK'}] node {node_id} {name}: {value} ({'Windows absolute path' if is_windows else 'path not found'})", flush=True)
    except Exception as exc:
        warnings += 1
        if log: print(f"[WARN] Workflow diagnostics unavailable: {exc}", flush=True)
    if log: print(f"[ComicEdu][Startup] Path warnings: {warnings}; errors: {errors}", flush=True); print("[ComicEdu][Startup] ========================================", flush=True)
    return {"warning_count": warnings, "error_count": errors}


@app.on_event("startup")
def print_startup_diagnostics() -> None:
    startup_path_diagnostics()


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    comfyui = comfy_client.health()
    if comfyui["connected"]:
        try:
            adapter.asset_paths
            comfyui["workflow"] = settings.comfy_workflow_name
            comfyui["workflow_configured"] = True
            comfyui["dialogue_text_corrector_loaded"] = (
                "DialogueTextCorrector" in comfy_client.object_info()
            )
        except Exception as exc:
            comfyui["workflow_configured"] = False
            comfyui["workflow_error"] = str(exc)
    diagnostics = startup_path_diagnostics(log=False)
    return {"backend": "ok", "comfyui": comfyui, "openai_configured": bool(settings.openai_api_key),
            "backend_runtime": {"host": settings.host, "port": settings.port},
            "frontend_origin_configured": bool(settings.frontend_origin),
            "path_diagnostics": diagnostics}


@app.get("/api/v1/showcase")
def showcase() -> dict[str, Any]:
    """Portal enhancement endpoint; an empty list keeps bundled examples visible."""
    return {"success": True, "data": {"items": [], "total": 0}}


@app.post("/api/v1/projects", status_code=201)
def create_project(payload: ProjectCreate) -> dict[str, Any]:
    grade_level = (
        payload.grade_level
        if "grade_level" in payload.model_fields_set
        else payload.gradeLevel
    ) or "國小高年級"
    title = payload.title.strip() or payload.topic.strip()[:120] or "未命名漫畫教材"
    material_text = payload.topic.strip()
    if payload.teachingObjective.strip():
        material_text += f"\n\n教學目標：{payload.teachingObjective.strip()}"
    if payload.storyStyle.strip():
        material_text += f"\n故事風格：{payload.storyStyle.strip()}"
    project = repository.create_project(
        title,
        payload.subject,
        grade_level,
        payload.topic,
        payload.page_count,
        payload.teachingObjective,
        payload.storyStyle,
    )
    if material_text:
        project_dir = settings.projects_dir / project["project_id"] / "material"
        project_dir.mkdir(parents=True, exist_ok=True)
        material_path = project_dir / "topic.txt"
        material_path.write_text(material_text, encoding="utf-8")
        project = repository.update_project(project["project_id"], material={"name": "topic.txt", "path": str(material_path), "type": "text/plain", "preview": material_text[:4000], "source": "project_settings"})
    return {**project, "success": True, "data": {"projectId": project["project_id"]}}


@app.get("/api/v1/projects")
def list_projects() -> list[dict[str, Any]]:
    projects = repository.list_projects()
    result = []
    for summary in projects:
        project = repository.get_project(summary["project_id"])
        legacy = legacy_project(project)
        completed = legacy["completedStep"] == 5
        result.append({
            "id": project["project_id"],
            "title": project.get("title") or project.get("topic") or "未命名教材",
            "topic": project.get("topic", ""),
            "subject": project.get("subject", ""),
            "gradeLevel": project.get("grade_level", ""),
            "pageCount": project.get("page_count", 1),
            "status": legacy["status"],
            "completed": completed,
            "completedStep": legacy["completedStep"],
            "updatedAt": project.get("updated_at"),
            "coverUrl": f"/api/v1/projects/{project['project_id']}/export/image?page=0" if completed else None,
        })
    return result


@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    try:
        project = repository.get_project(project_id)
        return {**project, "success": True, "data": legacy_project(project)}
    except KeyError as exc:
        fail_not_found(exc)


@app.delete("/api/v1/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> Response:
    try:
        repository.delete_project(project_id)
    except KeyError as exc:
        fail_not_found(exc)
    target = (settings.projects_dir / project_id).resolve()
    if target.parent == settings.projects_dir.resolve() and target.exists():
        shutil.rmtree(target)
    return Response(status_code=204)


@app.post("/api/v1/projects/{project_id}/material")
def save_material_text(project_id: str, payload: MaterialText) -> dict[str, Any]:
    try:
        repository.get_project(project_id, include_pages=False)
    except KeyError as exc:
        fail_not_found(exc)
    directory = settings.projects_dir / project_id / "material"
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = Path(payload.name).name
    path = directory / safe_name
    path.write_text(payload.text, encoding="utf-8")
    return repository.update_project(project_id, material={"name": safe_name, "path": str(path), "type": "text/plain", "preview": payload.text[:4000], "source": "upload"}, status="material_ready")


@app.post("/api/v1/projects/{project_id}/material/upload")
async def upload_material(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        repository.get_project(project_id, include_pages=False)
    except KeyError as exc:
        fail_not_found(exc)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".txt", ".md", ".docx", ".doc"}:
        raise HTTPException(status_code=415, detail="目前只支援 PDF、TXT、MD、DOC、DOCX")
    content = await file.read(settings.upload_limit_mb * 1024 * 1024 + 1)
    if len(content) > settings.upload_limit_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"檔案不可超過 {settings.upload_limit_mb} MB")
    directory = settings.projects_dir / project_id / "material"
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or f"material{suffix}").name
    path = directory / safe_name
    path.write_bytes(content)
    try:
        if suffix == ".pdf":
            document = fitz.open(stream=content, filetype="pdf")
            preview = "\n".join(page.get_text() for page in document)[:4000]
        elif suffix == ".docx":
            with zipfile.ZipFile(BytesIO(content)) as archive:
                xml = archive.read("word/document.xml")
            root = ElementTree.fromstring(xml)
            preview = "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))[:4000]
        else:
            preview = content.decode("utf-8")[:4000]
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"教材解析失敗: {exc}") from exc
    repository.update_project(project_id, material={"name": safe_name, "path": str(path), "type": file.content_type or suffix, "preview": preview, "source": "upload"}, status="analyzing_material", error=None)
    job = queue(project_id, "analyze_material", 1, lambda progress: service.analyze_material(project_id, progress))
    return {"success": True, "message": "教材已上傳，開始分析", "data": job}


@app.post("/api/v1/projects/{project_id}/material/analyze", status_code=202)
def analyze_material(project_id: str) -> dict[str, Any]:
    repository.update_project(project_id, status="analyzing_material", error=None)
    return queue(project_id, "analyze_material", 1, lambda progress: service.analyze_material(project_id, progress))


@app.post("/api/v1/projects/{project_id}/script/generate", status_code=202)
def legacy_generate_script(project_id: str) -> dict[str, Any]:
    """Run the current Stage 1-4 workflow behind ComicEdu's original first step."""
    repository.update_project(project_id, status="generating_script", error=None, page_count=1)

    def operation(progress: Callable[..., None]) -> None:
        service.analyze_material(project_id, progress)
        analyzed = repository.get_project(project_id)
        plans = analyzed.get("plans") or []
        if len(plans) != 3:
            raise ValueError("Stage 1 未產生三個可用企劃")
        repository.update_project(project_id, selected_plan=plans[0], page_count=1, status="plan_selected")
        service.generate_story_arc(project_id, progress)
        arc = repository.get_project(project_id).get("story_arc")
        repository.replace_story_arc(project_id, arc, confirmed=True)
        service.generate_script(project_id, 1, progress)
        service.recommend_characters(project_id, 1, progress)
        repository.update_project(project_id, status="scripts_ready", error=None)

    job = queue(project_id, "legacy_generate_script", 1, operation)
    return {"success": True, "data": job}


@app.post("/api/v1/projects/{project_id}/material/select-plan")
def legacy_select_material_plan(project_id: str, payload: LegacyMaterialPlanSelection) -> dict[str, Any]:
    project = repository.get_project(project_id)
    plans = project.get("plans") or []
    if payload.planIndex < 0 or payload.planIndex >= len(plans):
        raise HTTPException(status_code=422, detail="企劃索引不存在")
    selected = plans[payload.planIndex]
    pick = lambda *keys: next((selected[key] for key in keys if selected.get(key) not in (None, "")), "")
    subject = pick("subject", "subject_name", "discipline") or project.get("subject") or "自然科學"
    grade_level = pick("gradeLevel", "grade_level", "grade", "target_grade") or project.get("grade_level") or "國小高年級"
    topic = pick("topic", "title", "plan_title") or project.get("topic") or ""
    teaching_objective = pick("teachingObjective", "teaching_objective", "objective", "learning_objective")
    story_style = pick("storyStyle", "story_style", "style")
    page_count = int(pick("recommended_page_count", "page_count", "pageCount", "recommendedPageCount") or project.get("page_count") or 3)
    page_count = max(2, min(page_count, 6))
    updated = repository.update_project(
        project_id,
        selected_plan=selected,
        page_count=page_count,
        subject=subject,
        grade_level=grade_level,
        topic=topic,
        teaching_objective=teaching_objective,
        story_style=story_style,
        status="plan_selected",
        error=None,
    )
    return {"success": True, "data": selected, "project": legacy_project(updated)}


@app.post("/api/v1/projects/{project_id}/script/generate-long", status_code=202)
def legacy_generate_long_script(project_id: str, payload: LegacyLongScriptRequest) -> dict[str, Any]:
    page_count = max(2, min(int(payload.pageCount), 6))
    repository.update_project(project_id, page_count=page_count, status="generating_script", error=None)

    def operation(progress: Callable[..., None]) -> None:
        project = repository.get_project(project_id)
        if not project.get("selected_plan"):
            service.analyze_material(project_id, progress)
            project = repository.get_project(project_id)
            plans = project.get("plans") or []
            if not plans:
                raise ValueError("Stage 1 未產生可用企劃")
            repository.update_project(project_id, selected_plan=plans[0])
        service.generate_story_arc(project_id, progress)
        repository.update_project(project_id, status="story_arc_ready", error=None)

    job = queue(project_id, "legacy_generate_long", 2, operation)
    return {"success": True, "message": "Story Arc 生成任務已啟動", "data": job}


@app.post("/api/v1/projects/{project_id}/confirm-story-arc", status_code=202)
def legacy_confirm_story_arc(project_id: str, payload: LegacyConfirmStoryArc) -> dict[str, Any]:
    project = repository.get_project(project_id)
    if payload.storyArc:
        submitted_pages = payload.storyArc.get("pages") or payload.storyArc.get("pageSummaries") or []
        current_pages = (project.get("story_arc") or {}).get("pages") or []
        if submitted_pages and not payload.storyArc.get("pages"):
            normalized_pages = []
            for index, summary in enumerate(submitted_pages):
                base = dict(current_pages[index]) if index < len(current_pages) else {}
                base.update({
                    "page_number": summary.get("pageNumber", index + 1),
                    "page_title": summary.get("title", base.get("page_title", f"第 {index + 1} 頁")),
                    "summary": summary.get("summary", base.get("summary", "")),
                    "key_knowledge_points": summary.get("keyKnowledgePoints", base.get("key_knowledge_points", [])),
                    "story_role": summary.get("storyRole", base.get("story_role", "")),
                    "story_event": summary.get("storyEvent", base.get("story_event", "")),
                    "visual_focus": summary.get("visualFocus", base.get("visual_focus", "")),
                    "ending_hook": summary.get("endingHook", base.get("ending_hook", "")),
                })
                normalized_pages.append(base)
            payload.storyArc = {**(project.get("story_arc") or {}), "pages": normalized_pages}
        if payload.storyArc.get("pages"):
            repository.replace_story_arc(project_id, payload.storyArc, confirmed=True)
    elif project.get("story_arc"):
        repository.replace_story_arc(project_id, project["story_arc"], confirmed=True)

    def operation(progress: Callable[..., None]) -> None:
        current = repository.get_project(project_id)
        for page in current.get("pages") or []:
            service.generate_script(project_id, page["page_index"], progress)
        repository.update_project(project_id, status="scripts_ready", error=None)

    job = queue(project_id, "legacy_confirm_story_arc", 3, operation)
    return {"success": True, "message": "逐頁四格劇本生成任務已啟動", "data": job}


@app.put("/api/v1/projects/{project_id}/script")
def legacy_update_script(project_id: str, payload: LegacyScriptUpdate) -> dict[str, Any]:
    try:
        page = repository.save_script(project_id, 1, payload.script)
        return {"success": True, "data": page}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/projects/{project_id}/script/scene/{scene_index}/modify")
def legacy_revise_scene(project_id: str, scene_index: int, payload: LegacySceneRevision) -> dict[str, Any]:
    if scene_index not in range(4):
        raise HTTPException(status_code=422, detail="scene_index 必須為 0-3")
    service.generate_script(project_id, 1, lambda **_: None, payload.instruction, scene_index + 1)
    return {"success": True, "data": repository.get_page(project_id, 1)["script"][scene_index]}


@app.post("/api/v1/chat")
def legacy_chat(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages") or []
    if not any(str(item.get("content") or "").strip() for item in messages if item.get("role") == "user"):
        raise HTTPException(status_code=422, detail="請先輸入問題")
    project = None
    project_id = str(payload.get("projectId") or "").strip()
    if project_id:
        try:
            project = repository.get_project(project_id)
        except KeyError as exc:
            fail_not_found(exc)
    try:
        reply = service.chat_assistant(messages, payload.get("context") or {}, project)
        return {"success": True, "reply": reply}
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/v1/projects/{project_id}/characters")
def legacy_select_characters(project_id: str, payload: LegacyCharacterSelection) -> dict[str, Any]:
    try:
        ids = [int(value) for value in payload.selectedCharacterIds]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="角色 ID 格式錯誤") from exc
    valid = {item["id"] for item in characters()}
    if len(set(ids)) != 2 or not set(ids).issubset(valid):
        raise HTTPException(status_code=422, detail="請選擇兩名不同的有效角色")
    pages = repository.get_project(project_id).get("pages") or []
    for item in pages:
        repository.update_page(project_id, item["page_index"], selected_characters=ids, status="characters_selected")
    return {"success": True, "data": repository.get_project(project_id)}


@app.get("/api/v1/projects/{project_id}/character-recommendation")
def legacy_character_recommendation(project_id: str) -> dict[str, Any]:
    try:
        recommendation = repository.get_page(project_id, 1).get("character_recommendation")
    except KeyError:
        recommendation = None
    if not recommendation:
        return {"success": False, "error": "角色推薦尚未完成"}
    return {"success": True, "data": recommendation}


@app.post("/api/v1/projects/{project_id}/layouts/generate", status_code=202)
def legacy_generate_layouts(project_id: str) -> dict[str, Any]:
    existing = latest_job(project_id, "legacy_recommend_layouts")
    if existing and existing.get("status") in {"queued", "running"}:
        return {"success": True, "data": existing, "deduplicated": True}
    repository.update_project(project_id, status="selecting_layouts", error=None)

    def operation(progress: Callable[..., None]) -> None:
        pages = repository.get_project(project_id).get("pages") or []
        for page in pages:
            service.recommend_layouts(project_id, page["page_index"], progress)
        repository.update_project(project_id, status="layouts_ready", error=None)

    job = queue(project_id, "legacy_recommend_layouts", 5, operation)
    return {"success": True, "data": job}


@app.put("/api/v1/projects/{project_id}/storyboard")
def legacy_select_storyboard(project_id: str, payload: LegacyStoryboardSelection) -> dict[str, Any]:
    if payload.orientation:
        repository.update_project(project_id, orientation=payload.orientation)
    if payload.pages:
        for item in payload.pages:
            page_index = int(item.get("pageIndex", 0)) + 1
            layout_name = item.get("selectedLayoutName")
            if layout_name not in service.four_panel_layouts():
                raise HTTPException(status_code=422, detail=f"第 {page_index} 頁版面不存在")
            repository.update_page(project_id, page_index, selected_layout=layout_name, status="layout_selected")
        return {"success": True, "data": repository.get_project(project_id)}
    if payload.selectedLayoutName not in service.four_panel_layouts():
        raise HTTPException(status_code=422, detail="只允許目前資產中的四格 Layout")
    page = repository.update_page(project_id, 1, selected_layout=payload.selectedLayoutName, status="layout_selected")
    return {"success": True, "data": page}


@app.put("/api/v1/projects/{project_id}/storyboard/multi-page")
def legacy_select_multi_page_storyboard(project_id: str, payload: LegacyMultiPageStoryboard) -> dict[str, Any]:
    if payload.orientation:
        repository.update_project(project_id, orientation=payload.orientation)
    return legacy_select_storyboard(project_id, LegacyStoryboardSelection(pages=payload.pages, orientation=payload.orientation))


@app.get("/api/v1/layouts/images/{layout_name}")
def legacy_layout_image(layout_name: str) -> FileResponse:
    return layout_image(layout_name)


@app.get("/api/v1/characters/images/{character_id}")
def legacy_character_image(character_id: int) -> FileResponse:
    return character_image(character_id)


@app.post("/api/v1/projects/{project_id}/panels/generate-all", status_code=202)
def legacy_generate_all(project_id: str) -> dict[str, Any]:
    require_generation_nodes()
    existing = latest_job(project_id, "legacy_generate_all")
    if existing and existing.get("status") in {"queued", "running"}:
        return {"success": True, "data": existing, "resumed": False}

    project = repository.get_project(project_id)
    pages = project.get("pages") or []
    first_incomplete = next(
        (page["page_index"] - 1 for page in pages if not page.get("final_image") or not Path(page["final_image"]).is_file()),
        max(0, len(pages) - 1),
    )
    repository.update_project(project_id, status="generating_panels", current_generation_page=first_incomplete, error=None)

    def operation(progress: Callable[..., None]) -> None:
        pages = repository.get_project(project_id).get("pages") or []
        if not pages:
            raise ValueError("尚未建立任何頁面")
        for page in pages:
            final_image = page.get("final_image")
            if final_image and Path(final_image).is_file():
                continue
            repository.update_project(project_id, current_generation_page=page["page_index"] - 1, status="generating_panels")
            service.generate_page(project_id, page["page_index"], progress)
        repository.update_project(project_id, status="generation_ready", current_generation_page=len(pages) - 1, error=None)

    job = queue(project_id, "legacy_generate_all", 8, operation)
    return {"success": True, "data": job}


@app.get("/api/v1/projects/{project_id}/panels")
def legacy_get_panels(project_id: str, page: int = -1) -> dict[str, Any]:
    return {"success": True, "data": legacy_panels(project_id, page), "mtime": time.time()}


@app.get("/api/v1/projects/{project_id}/panels/wait")
async def legacy_wait_panels(project_id: str, last_mtime: float = 0, page: int = -1) -> dict[str, Any]:
    project = repository.get_project(project_id)
    if project.get("status") == "generation_interrupted":
        # Also recover already-open tabs that still run an older frontend
        # bundle: their next long-poll is enough to restart the missing work.
        legacy_generate_all(project_id)
    await asyncio.sleep(1)
    return {"success": True, "data": legacy_panels(project_id, page), "mtime": time.time()}


@app.put("/api/v1/projects/{project_id}/panels/{panel_id}/prompt")
def legacy_update_panel(project_id: str, panel_id: int, payload: LegacyPanelEdit, page: int = -1) -> dict[str, Any]:
    page_index = (page + 1) if page >= 0 else repository.get_project(project_id).get("current_generation_page", 0) + 1
    fields: dict[str, Any] = {}
    if payload.prompt is not None:
        fields["positive_prompt"] = payload.prompt
    if payload.dialogueText is not None:
        fields["text_content"] = payload.dialogueText
    if payload.seed is not None:
        fields["seed"] = payload.seed
    panel = repository.update_panel(project_id, page_index, panel_id + 1, **fields)
    return {"success": True, "data": panel}


@app.post("/api/v1/projects/{project_id}/panels/{panel_id}/regenerate", status_code=202)
def legacy_regenerate_panel(project_id: str, panel_id: int, payload: LegacyPanelEdit, page: int = -1) -> dict[str, Any]:
    require_generation_nodes()
    page_index = (page + 1) if page >= 0 else repository.get_project(project_id).get("current_generation_page", 0) + 1
    legacy_update_panel(project_id, panel_id, payload, page)
    mode = "random" if payload.seed is not None else "original"
    job = queue(project_id, "regenerate_panel", 8, lambda progress: service.regenerate_panel(project_id, page_index, panel_id + 1, mode, progress), page_index, panel_id + 1)
    return {"success": True, "data": job}


@app.get("/api/v1/projects/{project_id}/masks/{panel_id}")
def legacy_mask(project_id: str, panel_id: int, page: int = -1) -> FileResponse:
    page_index = (page + 1) if page >= 0 else repository.get_project(project_id).get("current_generation_page", 0) + 1
    page = repository.get_page(project_id, page_index)
    if panel_id not in range(4):
        raise HTTPException(status_code=404, detail="Panel 不存在")
    try:
        path = service.layout_mask_path(project_id, page_index, panel_id + 1)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/v1/projects/{project_id}/preview")
@app.get("/api/v1/projects/{project_id}/export/image")
def legacy_final_image(project_id: str, page: int = -1) -> FileResponse:
    page_index = (page + 1) if page >= 0 else repository.get_project(project_id).get("current_generation_page", 0) + 1
    page = repository.get_page(project_id, page_index)
    if page.get("final_image"):
        return FileResponse(
            checked_asset(page.get("final_image"), project_id),
            media_type="image/png",
            filename=f"{project_id}.png",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    # Both files are direct Stage 8 PageComposer outputs. Never redraw panel
    # borders in Pillow here: doing so caused the stepped/jagged divider lines.
    base_path = settings.projects_dir / project_id / f"page_{page_index}" / "base.png"
    preview_path = settings.projects_dir / project_id / f"page_{page_index}" / "preview_partial.png"
    output_path = preview_path if preview_path.is_file() else base_path
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="圖片尚未產生")
    return FileResponse(
        output_path,
        media_type="image/png",
        filename=f"{project_id}.png",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/api/v1/projects/{project_id}/plans")
def get_plans(project_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    return {"material_analysis": project.get("material_analysis"), "plans": project.get("plans") or [], "selected_plan": project.get("selected_plan")}


@app.put("/api/v1/projects/{project_id}/selected-plan")
def select_plan(project_id: str, payload: PlanSelection) -> dict[str, Any]:
    project = get_project(project_id)
    plans = project.get("plans") or []
    if len(plans) != 3:
        raise HTTPException(status_code=409, detail="尚未取得三個企劃")
    return repository.update_project(project_id, selected_plan=plans[payload.plan_index - 1], page_count=payload.page_count, status="plan_selected")


@app.post("/api/v1/projects/{project_id}/story-arc/generate", status_code=202)
def generate_story_arc(project_id: str) -> dict[str, Any]:
    return queue(project_id, "generate_story_arc", 2, lambda progress: service.generate_story_arc(project_id, progress))


@app.put("/api/v1/projects/{project_id}/story-arc")
def update_story_arc(project_id: str, payload: StoryArcUpdate) -> dict[str, Any]:
    return repository.replace_story_arc(project_id, payload.story_arc, confirmed=False)


@app.post("/api/v1/projects/{project_id}/story-arc/revise", status_code=202)
def revise_story_arc(project_id: str, payload: RevisionRequest) -> dict[str, Any]:
    return queue(project_id, "revise_story_arc", 2, lambda progress: service.generate_story_arc(project_id, progress, payload.instruction))


@app.post("/api/v1/projects/{project_id}/story-arc/confirm")
def confirm_story_arc(project_id: str) -> dict[str, Any]:
    project = get_project(project_id)
    if not project.get("story_arc"):
        raise HTTPException(status_code=409, detail="尚未建立 Story Arc")
    repository.replace_story_arc(project_id, project["story_arc"], confirmed=True)
    return repository.update_project(project_id, status="story_arc_confirmed")


@app.get("/api/v1/projects/{project_id}/pages/{page_index}")
def get_page(project_id: str, page_index: int) -> dict[str, Any]:
    try:
        return repository.get_page(project_id, page_index)
    except KeyError as exc:
        fail_not_found(exc)


@app.post("/api/v1/projects/{project_id}/pages/{page_index}/script/generate", status_code=202)
def generate_script(project_id: str, page_index: int) -> dict[str, Any]:
    return queue(project_id, "generate_page_script", 3, lambda progress: service.generate_script(project_id, page_index, progress), page_index)


@app.put("/api/v1/projects/{project_id}/pages/{page_index}/script")
def update_script(project_id: str, page_index: int, payload: ScriptUpdate) -> dict[str, Any]:
    return repository.save_script(project_id, page_index, payload.script)


@app.post("/api/v1/projects/{project_id}/pages/{page_index}/script/revise", status_code=202)
def revise_script(project_id: str, page_index: int, payload: RevisionRequest) -> dict[str, Any]:
    return queue(project_id, "revise_page_script", 3, lambda progress: service.generate_script(project_id, page_index, progress, payload.instruction), page_index)


@app.post("/api/v1/projects/{project_id}/pages/{page_index}/panels/{panel_index}/revise", status_code=202)
def revise_panel(project_id: str, page_index: int, panel_index: int, payload: RevisionRequest) -> dict[str, Any]:
    if panel_index not in range(1, 5):
        raise HTTPException(status_code=422, detail="panel_index 必須為 1-4")
    instruction = f"只修改第 {panel_index} 格，其餘三格保持一致。{payload.instruction}"
    return queue(project_id, "revise_panel_script", 3, lambda progress: service.generate_script(project_id, page_index, progress, instruction, panel_index), page_index, panel_index)


@app.get("/api/v1/characters")
def characters() -> list[dict[str, Any]]:
    data = json.loads(settings.characters_json.read_text(encoding="utf-8"))["characters"]
    for item in data:
        item["image_url"] = f"/api/v1/characters/{item['id']}/image"
    return data


@app.get("/api/v1/characters/{character_id}/image")
def character_image(character_id: int) -> FileResponse:
    path = settings.characters_dir / f"{character_id}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="角色圖片不存在")
    return FileResponse(path)


@app.post("/api/v1/projects/{project_id}/pages/{page_index}/characters/recommend", status_code=202)
def recommend_characters(project_id: str, page_index: int) -> dict[str, Any]:
    existing = latest_job(project_id, "recommend_characters")
    if existing and existing.get("page_index") == page_index and existing.get("status") in {"queued", "running"}:
        return existing
    return queue(project_id, "recommend_characters", 4, lambda progress: service.recommend_characters(project_id, page_index, progress), page_index)


@app.put("/api/v1/projects/{project_id}/pages/{page_index}/characters")
def select_characters(project_id: str, page_index: int, payload: CharacterSelection) -> dict[str, Any]:
    valid = {item["id"] for item in characters()}
    if len(set(payload.character_ids)) != 2 or not set(payload.character_ids).issubset(valid):
        raise HTTPException(status_code=422, detail="請選擇兩名不同的有效角色")
    return repository.update_page(project_id, page_index, selected_characters=payload.character_ids, status="characters_selected")


@app.get("/api/v1/layouts")
def layouts() -> list[dict[str, Any]]:
    return [{"layout_name": name, **value, "image_url": f"/api/v1/layouts/{name}/image"} for name, value in service.four_panel_layouts().items()]


@app.get("/api/v1/layouts/{layout_name}/image")
def layout_image(layout_name: str) -> FileResponse:
    if layout_name not in service.four_panel_layouts():
        raise HTTPException(status_code=404, detail="四格 Layout 不存在")
    return FileResponse(settings.layouts_dir / layout_name)


@app.post("/api/v1/projects/{project_id}/pages/{page_index}/layouts/recommend", status_code=202)
def recommend_layouts(project_id: str, page_index: int) -> dict[str, Any]:
    return queue(project_id, "recommend_layouts", 5, lambda progress: service.recommend_layouts(project_id, page_index, progress), page_index)


@app.put("/api/v1/projects/{project_id}/pages/{page_index}/layout")
def select_layout(project_id: str, page_index: int, payload: LayoutSelection) -> dict[str, Any]:
    if payload.layout_name not in service.four_panel_layouts():
        raise HTTPException(status_code=422, detail="只允許目前資產中的四格 Layout")
    return repository.update_page(project_id, page_index, selected_layout=payload.layout_name, status="layout_selected")


@app.post("/api/v1/projects/{project_id}/pages/{page_index}/generate", status_code=202)
def generate_page(project_id: str, page_index: int) -> dict[str, Any]:
    require_generation_nodes()
    return queue(project_id, "generate_page", 8, lambda progress: service.generate_page(project_id, page_index, progress), page_index)


@app.put("/api/v1/projects/{project_id}/pages/{page_index}/panels/{panel_index}")
def update_panel(project_id: str, page_index: int, panel_index: int, payload: PanelUpdate) -> dict[str, Any]:
    fields = payload.model_dump(exclude_none=True)
    try:
        return repository.update_panel(project_id, page_index, panel_index, **fields)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/projects/{project_id}/pages/{page_index}/panels/{panel_index}/regenerate", status_code=202)
def regenerate_panel(project_id: str, page_index: int, panel_index: int, payload: RegenerateRequest) -> dict[str, Any]:
    require_generation_nodes()
    return queue(project_id, "regenerate_panel", 8, lambda progress: service.regenerate_panel(project_id, page_index, panel_index, payload.seed_mode, progress), page_index, panel_index)


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    try:
        return repository.get_job(job_id)
    except KeyError as exc:
        fail_not_found(exc)


@app.get("/api/v1/projects/{project_id}/events")
async def project_events(project_id: str, request: Request) -> StreamingResponse:
    async def stream():
        previous = ""
        while not await request.is_disconnected():
            snapshot = json.dumps(repository.list_jobs(project_id), ensure_ascii=False, sort_keys=True)
            if snapshot != previous:
                previous = snapshot
                yield f"event: jobs\ndata: {snapshot}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def checked_asset(path_value: str | None, project_id: str) -> Path:
    if not path_value:
        raise HTTPException(status_code=404, detail="圖片尚未產生")
    path = Path(path_value).resolve()
    allowed = (settings.projects_dir / project_id).resolve()
    if allowed not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="圖片不存在")
    return path


@app.get("/api/v1/projects/{project_id}/pages/{page_index}/final.png")
def final_image(project_id: str, page_index: int) -> FileResponse:
    page = get_page(project_id, page_index)
    return FileResponse(checked_asset(page.get("final_image"), project_id), media_type="image/png", filename=f"page_{page_index}.png")


@app.get("/api/v1/projects/{project_id}/pages/{page_index}/panels/{panel_index}/image")
def panel_image(project_id: str, page_index: int, panel_index: int) -> FileResponse:
    page = get_page(project_id, page_index)
    if panel_index not in range(1, 5):
        raise HTTPException(status_code=404, detail="Panel 不存在")
    return FileResponse(checked_asset(page["panels"][panel_index - 1].get("generated_image"), project_id), media_type="image/png")


@app.get("/api/v1/projects/{project_id}/export")
def export_project(project_id: str, format: str = "png") -> Response:
    project = get_project(project_id)
    completed = [page for page in project["pages"] if page.get("final_image")]
    if not completed:
        raise HTTPException(status_code=409, detail="尚無可匯出的完成頁面")
    output_format = format.lower()
    if output_format not in {"png", "jpg", "jpeg", "pdf"}:
        raise HTTPException(status_code=422, detail="匯出格式只支援 png、jpg、jpeg、pdf")
    subject_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(project.get("subject") or "漫畫教材")).strip(" ._") or "漫畫教材"
    if output_format == "pdf":
        document = fitz.open()
        try:
            for page_data in completed:
                path = checked_asset(page_data["final_image"], project_id)
                with Image.open(path) as source:
                    width, height = source.size
                pdf_page = document.new_page(width=width, height=height)
                pdf_page.insert_image(fitz.Rect(0, 0, width, height), filename=str(path))
            content = document.tobytes(deflate=True)
        finally:
            document.close()
        encoded_name = quote(f"{subject_name}.pdf")
        return Response(content, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=export.pdf; filename*=UTF-8''{encoded_name}"})
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for page in completed:
            path = checked_asset(page["final_image"], project_id)
            extension = "png" if output_format == "png" else output_format
            if output_format == "png":
                archive.write(path, f"{subject_name}_第{page['page_index']:02d}頁.png")
            else:
                converted = BytesIO()
                with Image.open(path).convert("RGB") as image:
                    image.save(converted, "JPEG", quality=95, optimize=True)
                archive.writestr(f"{subject_name}_第{page['page_index']:02d}頁.{extension}", converted.getvalue())
    encoded_name = quote(f"{subject_name}_{output_format}.zip")
    return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f"attachment; filename=export.zip; filename*=UTF-8''{encoded_name}"})


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/Portal.html")


app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
