from __future__ import annotations

import io
import json
import random
import shutil
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import cv2
import httpx
import numpy as np
from PIL import Image, PngImagePlugin

from .comfyui_client import ComfyUIClient, ComfyUIError
from .config import Settings
from .repository import Repository
from .workflow_adapter import WorkflowAdapter


Progress = Callable[..., None]


def parse_json(text: str) -> Any:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(value)


class ComicService:
    PAGE_SIZE = (768, 1086)
    PANEL_COLORS = ((120, 173, 210), (255, 178, 110), (128, 198, 128), (230, 125, 126))
    LAYOUT_MASK_VERSION = "3-stage8"
    SCANNED_PDF_MESSAGE = (
        "無法讀取這份 PDF 的文字。這份檔案可能是掃描影像，"
        "請先使用 OCR 轉成可搜尋文字的 PDF，或改上傳 DOCX、TXT、MD 檔案。"
    )

    def __init__(self, settings: Settings, repository: Repository, client: ComfyUIClient, adapter: WorkflowAdapter):
        self.settings = settings
        self.repository = repository
        self.client = client
        self.adapter = adapter
        self._layout_geometry_lock = threading.Lock()

    def require_ai(self) -> str:
        health = self.client.health()
        if not health["connected"]:
            raise ComfyUIError(f"ComfyUI 無法連線: {health.get('error', '')}")
        # Workflow OpenAI nodes own their credentials. An empty value preserves
        # the workflow's linked api_key node instead of replacing it.
        return self.settings.openai_api_key

    def chat_assistant(self, messages: list[dict[str, Any]], context: dict[str, Any], project: dict[str, Any] | None = None) -> str:
        api_key = self.settings.openai_api_key
        if not api_key:
            raise ValueError("AI 助手尚未設定，請確認 OPENAI_API_KEY")
        project_context = project or {}
        system_prompt = (
            "你是漫教工坊的繁體中文劇本設計助手。協助教師把教材轉成清楚、可視覺化、適合四格漫畫的劇本。"
            "回答要具體、精簡並可直接採用；可提出場景、對白、知識呈現與故事節奏建議。"
            "不要假裝已修改資料，若教師要求修改，請清楚說明建議修改內容。"
        )
        context_text = json.dumps({
            "目前表單": context,
            "故事弧線": project_context.get("story_arc"),
            "目前劇本": [page.get("script") for page in project_context.get("pages", []) if page.get("script")],
        }, ensure_ascii=False)
        input_messages = [{"role": "system", "content": system_prompt + "\n\n目前專案資料：\n" + context_text}]
        for item in messages[-12:]:
            role = item.get("role")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                input_messages.append({"role": role, "content": content[:4000]})
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o", "input": input_messages, "temperature": 0.5, "max_output_tokens": 800},
            timeout=90,
        )
        if response.status_code >= 400:
            raise ValueError(f"AI 助手暫時無法回覆（HTTP {response.status_code}）")
        payload = response.json()
        text = str(payload.get("output_text") or "").strip()
        if not text:
            parts = []
            for output in payload.get("output") or []:
                for content in output.get("content") or []:
                    if content.get("type") in {"output_text", "text"} and content.get("text"):
                        parts.append(str(content["text"]))
            text = "\n".join(parts).strip()
        if not text:
            raise ValueError("AI 助手沒有回傳文字內容")
        return text

    @classmethod
    def redact_prompt_secrets(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[REDACTED]" if key.lower() in {"api_key", "authorization", "password", "token"} else cls.redact_prompt_secrets(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls.redact_prompt_secrets(item) for item in value]
        return value

    def write_prompt_log(self, project_id: str, stage: int, message: str, prompt: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        created_at = datetime.now(timezone.utc)
        directory = self.settings.projects_dir / project_id / "prompt_logs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{created_at.strftime('%Y%m%dT%H%M%S_%fZ')}_stage_{stage}.json"
        payload = {
            "created_at": created_at.isoformat(),
            "stage": stage,
            "message": message,
            "prompt_id": None,
            "prompt": self.redact_prompt_secrets(prompt),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path, payload

    @staticmethod
    def update_prompt_log(path: Path, payload: dict[str, Any], **fields: Any) -> None:
        payload.update(fields)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_prompt(self, project_id: str, prompt: dict[str, Any], progress: Progress, stage: int, message: str, timeout: int = 900) -> dict[str, Any]:
        progress(stage=stage, progress=None, message=message)
        log_path, log_payload = self.write_prompt_log(project_id, stage, message, prompt)
        try:
            prompt_id = self.client.submit(prompt)
        except Exception as exc:
            self.update_prompt_log(log_path, log_payload, submit_error=str(exc))
            raise
        self.update_prompt_log(log_path, log_payload, prompt_id=prompt_id)
        progress(comfy_prompt_id=prompt_id, message=f"{message}，ComfyUI 執行中")
        try:
            history = self.client.wait(prompt_id, timeout=timeout, callback=lambda: progress(message=f"{message}，等待輸出"))
        except Exception as exc:
            self.update_prompt_log(log_path, log_payload, execution_error=str(exc))
            raise
        self.update_prompt_log(log_path, log_payload, completed_at=datetime.now(timezone.utc).isoformat())
        return history

    def analyze_material(self, project_id: str, progress: Progress) -> None:
        project = self.repository.get_project(project_id)
        material = project.get("material") or {}
        path = material.get("path")
        if not path or not Path(path).exists():
            raise ValueError("尚未提供可分析的教材")
        api_key = self.require_ai()
        uploaded_material = material.get("source") == "upload" or material.get("name") not in {None, "topic.txt"}
        subject = "請根據教材內容自動判斷最適合的學科，不要沿用專案預設值" if uploaded_material else (project.get("subject") or "自然科學").strip()
        grade_level = (project.get("grade_level") or "國小高年級").strip()
        if not project.get("subject") or not project.get("grade_level"):
            project = self.repository.update_project(project_id, subject=subject, grade_level=grade_level)
        uploaded_material_name = self.client.upload_file(
            Path(path), f"GKComic/{project_id}/materials"
        )
        remote_material_path = self.client.input_file_path(uploaded_material_name)
        prompt, outputs = self.adapter.stage1(remote_material_path, subject, grade_level, api_key)
        try:
            history = self.run_prompt(project_id, prompt, progress, 1, "分析教材並產生三個企劃")
        except ComfyUIError as exc:
            raw_error = str(exc)
            if any(marker in raw_error for marker in ("PDF 幾乎無法擷取文字", "掃描型 PDF", "目前 Stage 1 不執行 OCR")):
                raise ValueError(self.SCANNED_PDF_MESSAGE) from exc
            raise
        analysis = parse_json(self.client.text_output(history, str(outputs[0])))
        plans_raw = parse_json(self.client.text_output(history, str(outputs[1])))
        plans = plans_raw.get("plans", plans_raw) if isinstance(plans_raw, dict) else plans_raw
        if not isinstance(plans, list) or len(plans) != 3:
            raise ValueError(f"Stage 1 必須回傳三個企劃，實際為 {len(plans) if isinstance(plans, list) else '非陣列'}")
        for plan in plans:
            if isinstance(plan, dict):
                raw_count = plan.get("recommended_page_count", 3)
                try:
                    plan["recommended_page_count"] = max(2, min(6, int(raw_count)))
                except (TypeError, ValueError):
                    plan["recommended_page_count"] = 3
        detected_subject = next((str(plan.get("subject", "")).strip() for plan in plans if str(plan.get("subject", "")).strip()), "")
        updates = {"material_analysis": analysis, "plans": plans, "status": "plans_ready", "error": None}
        if uploaded_material and detected_subject:
            updates["subject"] = detected_subject
        self.repository.update_project(project_id, **updates)

    def generate_story_arc(self, project_id: str, progress: Progress, instruction: str = "") -> None:
        project = self.repository.get_project(project_id)
        if not project.get("selected_plan"):
            raise ValueError("請先選擇企劃")
        api_key = self.require_ai()
        selected_plan = dict(project["selected_plan"])
        selected_plan["recommended_page_count"] = int(project["page_count"])
        prompt, outputs = self.adapter.stage2(selected_plan, project["page_count"], api_key, instruction)
        history = self.run_prompt(project_id, prompt, progress, 2, "規劃多頁 Story Arc")
        story_arc = parse_json(self.client.text_output(history, str(outputs[0])))
        if "pages" not in story_arc:
            pages = parse_json(self.client.text_output(history, str(outputs[1])))
            story_arc["pages"] = pages
        target_page_count = max(2, min(6, int(project.get("page_count") or 3)))
        generated_pages = story_arc.get("pages") or []
        if len(generated_pages) < target_page_count:
            raise ValueError(f"Stage 2 頁數不足：需要 {target_page_count} 頁，實際為 {len(generated_pages)} 頁")
        story_arc["pages"] = generated_pages[:target_page_count]
        for index, page in enumerate(story_arc["pages"], start=1):
            page["page_number"] = index
        self.repository.replace_story_arc(project_id, story_arc, confirmed=False)
        self.repository.update_project(project_id, status="story_arc_ready", error=None)

    def generate_script(self, project_id: str, page_index: int, progress: Progress, instruction: str = "", panel_only: int | None = None) -> None:
        project = self.repository.get_project(project_id)
        page = self.repository.get_page(project_id, page_index)
        api_key = self.require_ai()
        prompt, outputs = self.adapter.stage3(project["selected_plan"], project["story_arc"], page["story_arc_page"], api_key, instruction)
        history = self.run_prompt(project_id, prompt, progress, 3, f"產生第 {page_index} 頁四格劇本")
        script = parse_json(self.client.text_output(history, str(outputs[0])))
        if isinstance(script, dict):
            script = script.get("prompts") or script.get("panels") or []
        if len(script) != 4:
            raise ValueError(f"Stage 3 必須回傳四格，實際為 {len(script)}")
        if panel_only:
            existing = page.get("script") or [self.panel_to_script(item) for item in page["panels"]]
            existing[panel_only - 1] = script[panel_only - 1]
            script = existing
        # Keep the prompt schema stable for the preview and image workflow.
        for item in script:
            prompt = item.get("提示詞內容") or item.get("prompt") or item.get("narration") or {}
            if isinstance(prompt, dict):
                negative = prompt.get("負向提示詞", prompt.get("負面提示詞", prompt.get("negative_prompt", item.get("負向提示詞", item.get("負面提示詞", "")))))
                prompt.setdefault("描述", prompt.get("description", prompt.get("positive_prompt", "")))
                prompt["負向提示詞"] = negative or ""
                item["提示詞內容"] = prompt
            else:
                item["提示詞內容"] = {"描述": str(prompt), "負向提示詞": item.get("負向提示詞", "") or ""}
        self.repository.save_script(project_id, page_index, script)
        self.repository.update_project(project_id, status="scripts_ready", error=None)

    @staticmethod
    def panel_to_script(panel: dict[str, Any]) -> dict[str, Any]:
        if isinstance(panel.get("prompt_json"), dict) and panel["prompt_json"]:
            return json.loads(json.dumps(panel["prompt_json"], ensure_ascii=False))
        return {"序號": panel["panel_index"], "提示詞內容": {"描述": panel["positive_prompt"], "負向提示詞": panel["negative_prompt"]}, "文字內容": panel["text_content"], "人物數量": 2, "人物陣列": ["人物1", "人物2"]}

    def persist_panel_prompt_snapshot(self, project_id: str, page_index: int, panel: dict[str, Any], revision: int, root: Path) -> Path:
        """Persist the exact Stage 3 panel record used by one generation revision."""
        record = self.panel_to_script(panel)
        record["page_index"] = page_index
        record["panel_index"] = int(panel["panel_index"])
        record["序號"] = int(panel["panel_index"])
        path = root / "prompts" / f"panel_{panel['panel_index']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self.repository.add_asset(
            project_id, "panel_prompt", str(path), page_index,
            int(panel["panel_index"]), revision, metadata=record,
        )
        return path

    def recommend_characters(self, project_id: str, page_index: int, progress: Progress) -> None:
        page = self.repository.get_page(project_id, page_index)
        if not page.get("script"):
            raise ValueError("請先產生本頁劇本")
        api_key = self.require_ai()
        prompt, outputs = self.adapter.stage4(
            page["script"], self.adapter.asset_paths["characters_json"], api_key
        )
        history = self.run_prompt(project_id, prompt, progress, 4, f"推薦第 {page_index} 頁角色")
        recommended_ids = parse_json(self.client.text_output(history, str(outputs[0])))
        reasons_raw = parse_json(self.client.text_output(history, str(outputs[3])))
        if isinstance(reasons_raw, list):
            reasons = {
                str(item.get("character_id")): str(item.get("reason") or "")
                for item in reasons_raw if isinstance(item, dict) and item.get("character_id") is not None
            }
        else:
            reasons = reasons_raw if isinstance(reasons_raw, dict) else {}
        normalized_ids = [int(value) for value in recommended_ids] if isinstance(recommended_ids, list) else []
        if len(set(normalized_ids)) != 2 or len(self.character_records(normalized_ids)) != 2:
            raise ValueError("AI 角色推薦必須回傳兩名有效且不同的角色")
        recommendation = {
            "recommended_ids": normalized_ids,
            "recommended_characters": parse_json(self.client.text_output(history, str(outputs[1]))),
            "summary": self.client.text_output(history, str(outputs[2])),
            "reasons": reasons,
            "pairing_tip": self.client.text_output(history, str(outputs[4])),
        }
        self.repository.update_page(project_id, page_index, character_recommendation=recommendation, status="characters_recommended")

    def recommend_layouts(self, project_id: str, page_index: int, progress: Progress) -> None:
        page = self.repository.get_page(project_id, page_index)
        if len(page.get("selected_characters") or []) != 2:
            raise ValueError("請先人工選擇兩名角色")
        characters = self.character_records(page["selected_characters"])
        api_key = self.require_ai()
        prompt, outputs = self.adapter.stage5(
            page["script"], characters, self.adapter.asset_paths["layouts_json"], api_key
        )
        history = self.run_prompt(project_id, prompt, progress, 5, f"推薦第 {page_index} 頁版面")
        selected = parse_json(self.client.text_output(history, str(outputs[0])))
        names = parse_json(self.client.text_output(history, str(outputs[1])))
        reasons = parse_json(self.client.text_output(history, str(outputs[2])))
        candidates = []
        for index, name in enumerate(names):
            if name in self.four_panel_layouts():
                candidates.append({"layout_name": name, "reason": reasons[index] if index < len(reasons) else "", "rank": index + 1})
        if len(candidates) != 3:
            raise ValueError(f"Stage 5 沒有回傳三個可用四格 Layout: {selected}")
        self.repository.update_page(project_id, page_index, layout_recommendation={"candidates": candidates}, status="layouts_recommended")

    def compose_stage8(self, project_id: str, page: dict[str, Any], progress: Progress,
                       destination: Path, label: str) -> Path:
        upload_root = f"GKComic/{project_id}/page_{page['page_index']}/stage8/{label}/inputs"
        uploaded_images: list[str | None] = []
        for panel_number, panel in enumerate(page.get("panels") or [], start=1):
            image_path = panel.get("generated_image")
            path = Path(image_path) if image_path else None
            panel_upload_root = f"{upload_root}/panel_{panel_number}"
            uploaded_images.append(self.client.upload_image(path, panel_upload_root) if path and path.is_file() else None)
        prompt, output_node = self.adapter.compose_page(
            uploaded_images,
            page["page_index"],
            page["selected_layout"],
            self.adapter.asset_paths["layouts_dir"],
            f"GKComic/{project_id}/page_{page['page_index']}/stage8/{label}/composed",
        )
        history = self.run_prompt(project_id, prompt, progress, 8, f"Stage 8 合成第 {page['page_index']} 頁（{label}）", timeout=300)
        outputs = self.save_outputs(history, output_node, destination)
        if not outputs:
            raise ValueError("Stage 8 PageComposer 沒有輸出整頁漫畫")
        return outputs[0]

    def generate_page(self, project_id: str, page_index: int, progress: Progress) -> None:
        page = self.repository.get_page(project_id, page_index)
        if not page.get("selected_layout") or len(page.get("selected_characters") or []) != 2:
            raise ValueError("請先人工確認角色與版面")
        project_dir = self.page_asset_dir(project_id, page_index, page["revision"] + 1)
        base_path = self.settings.projects_dir / project_id / f"page_{page_index}" / "base.png"
        base_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path = self.settings.projects_dir / project_id / f"page_{page_index}" / "preview_partial.png"
        staged_panels: list[dict[str, Any]] = []
        completed_count = 0
        for index, panel in enumerate(page["panels"], start=1):
            image_path = Path(panel["generated_image"]) if panel.get("generated_image") else None
            mask_path = Path(panel["mask_image"]) if panel.get("mask_image") else None
            reusable = bool(image_path and image_path.is_file() and mask_path and mask_path.is_file())
            if reusable:
                staged_panels.append({**panel, "status": "completed"})
                self.repository.update_panel(project_id, page_index, index, status="completed")
                completed_count += 1
            else:
                staged_panels.append({**panel, "generated_image": None, "mask_image": None, "status": "pending"})
                self.repository.update_panel(
                    project_id, page_index, index,
                    generated_image=None, mask_image=None, status="pending",
                )

        initial_page = {**page, "panels": [dict(panel) for panel in staged_panels]}
        initial_label = "resume" if completed_count else "blank"
        initial_output = self.compose_stage8(
            project_id, initial_page, progress,
            project_dir / "stage8" / initial_label, initial_label,
        )
        shutil.copyfile(initial_output, base_path)
        shutil.copyfile(initial_output, preview_path)
        latest_composition = initial_output
        for index, panel in enumerate(page["panels"], start=1):
            if staged_panels[index - 1].get("generated_image"):
                progress(progress=index / 4, panel_index=index, message=f"第 {index} 格已存在，接續下一格")
                continue
            self.repository.update_panel(project_id, page_index, index, status="generating")
            prefix = f"GKComic/{project_id}/page_{page_index}/rev_{page['revision'] + 1}/panel_{index}"
            self.persist_panel_prompt_snapshot(
                project_id, page_index, panel, panel["revision"] + 1, project_dir,
            )
            prompt, outputs = self.adapter.regenerate_panel(
                panel, page_index, page["selected_characters"], page["selected_layout"], self.adapter.asset_paths["characters_json"],
                self.adapter.asset_paths["characters_dir"], self.adapter.asset_paths["layouts_dir"], prefix,
            )
            history = self.run_prompt(project_id, prompt, progress, 8, f"生成第 {page_index} 頁第 {index} 格", timeout=3600)
            images = self.save_outputs(history, outputs["panel"], project_dir / "panels" / f"panel_{index}")
            masks = self.save_outputs(history, outputs["mask"], project_dir / "masks" / f"panel_{index}")
            self.save_debug_inputs(project_id, page_index, index, page["revision"] + 1, history, outputs)
            if not images or not masks:
                raise ValueError(f"第 {index} 格沒有產生真實圖片或 mask")
            staged_panels[index - 1].update({
                "generated_image": str(images[0]), "mask_image": str(masks[0]),
                "status": "completed", "revision": panel["revision"] + 1,
            })
            staged_page = {**page, "panels": [dict(item) for item in staged_panels]}
            latest_composition = self.compose_stage8(
                project_id, staged_page, progress, project_dir / "stage8" / f"panel_{index}", f"panel_{index}"
            )
            shutil.copyfile(latest_composition, preview_path)
            self.repository.update_panel(project_id, page_index, index, generated_image=str(images[0]), mask_image=str(masks[0]), status="completed", revision=panel["revision"] + 1)
            progress(progress=index / 4, panel_index=index, message=f"第 {index} 格完成")
        final_path = project_dir / "final" / f"page_{page_index}.png"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(latest_composition, final_path)
        self.repository.update_page(project_id, page_index, final_image=str(final_path), revision=page["revision"] + 1, status="completed")
        self.repository.update_project(project_id, status="generation_ready", error=None)

    def regenerate_panel(self, project_id: str, page_index: int, panel_index: int, seed_mode: str, progress: Progress) -> None:
        page = self.repository.get_page(project_id, page_index)
        panel = dict(page["panels"][panel_index - 1])
        if seed_mode == "random":
            panel["seed"] = random.randint(1, 2_147_483_647)
        prefix = f"GKComic/{project_id}/page_{page_index}/panel_{panel_index}_rev_{panel['revision'] + 1}"
        target = self.page_asset_dir(project_id, page_index, page["revision"] + 1)
        self.persist_panel_prompt_snapshot(
            project_id, page_index, panel, panel["revision"] + 1, target,
        )
        prompt, outputs = self.adapter.regenerate_panel(
            panel, page_index, page["selected_characters"], page["selected_layout"], self.adapter.asset_paths["characters_json"],
            self.adapter.asset_paths["characters_dir"], self.adapter.asset_paths["layouts_dir"], prefix,
        )
        history = self.run_prompt(project_id, prompt, progress, 8, f"重生第 {page_index} 頁第 {panel_index} 格", timeout=3600)
        images = self.save_outputs(history, outputs["panel"], target / "panels")
        masks = self.save_outputs(history, outputs["mask"], target / "masks")
        self.save_debug_inputs(project_id, page_index, panel_index, page["revision"] + 1, history, outputs)
        if not images or not masks:
            raise ValueError("單格生成沒有產生真實圖片與 mask")
        staged_panels = [dict(item) for item in page["panels"]]
        staged_panels[panel_index - 1].update({"generated_image": str(images[0]), "mask_image": str(masks[0])})
        staged_page = {**page, "panels": staged_panels}
        composed = self.compose_stage8(
            project_id, staged_page, progress, target / "stage8" / f"panel_{panel_index}", f"regenerate_{panel_index}"
        )
        final_path = target / "final" / f"page_{page_index}_rev_{page['revision'] + 1}.png"
        final_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(composed, final_path)
        preview_path = self.settings.projects_dir / project_id / f"page_{page_index}" / "preview_partial.png"
        shutil.copyfile(composed, preview_path)
        self.repository.update_panel(project_id, page_index, panel_index, generated_image=str(images[0]), mask_image=str(masks[0]), seed=panel["seed"], revision=panel["revision"] + 1, status="completed")
        self.repository.update_page(project_id, page_index, final_image=str(final_path), revision=page["revision"] + 1, status="completed")

    def layout_panel_masks(self, page: dict[str, Any]) -> list[Image.Image]:
        layout_name = page.get("selected_layout")
        layout_path = self.settings.layouts_dir / (layout_name or "")
        if not layout_name or not layout_path.is_file():
            raise ValueError("找不到本頁選用的版面")
        with Image.open(layout_path).convert("RGB") as layout:
            masks: list[Image.Image] = []
            pixels = list(layout.getdata())
            supersample = 4
            high_size = (self.PAGE_SIZE[0] * supersample, self.PAGE_SIZE[1] * supersample)
            for color in self.PANEL_COLORS:
                source = Image.new("L", layout.size)
                source.putdata([255 if pixel == color else 0 for pixel in pixels])
                if not source.getbbox():
                    source.close()
                    raise ValueError(f"版面 {layout_name} 缺少面板色塊 {color}")
                filled = self.fill_mask_holes(source)
                source.close()
                binary = np.asarray(filled, dtype=np.uint8)
                filled.close()
                contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if not contours:
                    raise ValueError(f"版面 {layout_name} 的面板色塊 {color} 無有效輪廓")
                smooth = np.zeros((high_size[1], high_size[0]), dtype=np.uint8)
                scale_x = high_size[0] / layout.width
                scale_y = high_size[1] / layout.height
                for contour in contours:
                    epsilon = max(1.0, cv2.arcLength(contour, True) * 0.015)
                    polygon = cv2.approxPolyDP(contour, epsilon, True).astype(np.float64)
                    polygon[:, 0, 0] *= scale_x
                    polygon[:, 0, 1] *= scale_y
                    cv2.fillPoly(smooth, [np.rint(polygon).astype(np.int32)], 255, cv2.LINE_AA)
                high_mask = Image.fromarray(smooth, mode="L")
                masks.append(high_mask.resize(self.PAGE_SIZE, Image.Resampling.LANCZOS))
                high_mask.close()
        return masks

    @staticmethod
    def fill_mask_holes(mask: Image.Image) -> Image.Image:
        """Fill zero-valued regions enclosed by a panel while preserving its outer polygon."""
        image = mask.convert("L")
        width, height = image.size
        pixels = image.load()
        queue: deque[tuple[int, int]] = deque()
        outside: set[tuple[int, int]] = set()
        for x in range(width):
            if pixels[x, 0] == 0: queue.append((x, 0))
            if pixels[x, height - 1] == 0: queue.append((x, height - 1))
        for y in range(height):
            if pixels[0, y] == 0: queue.append((0, y))
            if pixels[width - 1, y] == 0: queue.append((width - 1, y))
        while queue:
            point = queue.popleft()
            if point in outside:
                continue
            outside.add(point)
            x, y = point
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height and pixels[nx, ny] == 0 and (nx, ny) not in outside:
                    queue.append((nx, ny))
        visited = set(outside)
        max_hole_area = int(width * height * 0.03)
        for y in range(height):
            for x in range(width):
                if pixels[x, y] != 0 or (x, y) in visited:
                    continue
                component: list[tuple[int, int]] = []
                component_queue = deque([(x, y)])
                visited.add((x, y))
                while component_queue:
                    cx, cy = component_queue.popleft()
                    component.append((cx, cy))
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                        if 0 <= nx < width and 0 <= ny < height and pixels[nx, ny] == 0 and (nx, ny) not in visited:
                            visited.add((nx, ny))
                            component_queue.append((nx, ny))
                if len(component) <= max_hole_area:
                    for hx, hy in component:
                        pixels[hx, hy] = 255
        return image

    def ensure_layout_geometry(self, project_id: str, page_index: int) -> dict[str, Any]:
        page = self.repository.get_page(project_id, page_index)
        layout_name = page.get("selected_layout")
        if not layout_name:
            raise ValueError("找不到本頁選用的版面")
        root = self.settings.projects_dir / project_id / f"page_{page_index}" / "layout_masks" / Path(layout_name).stem
        metadata_path = root / "geometry.json"

        def cached() -> dict[str, Any] | None:
            if not metadata_path.is_file():
                return None
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            valid = (
                metadata.get("version") == self.LAYOUT_MASK_VERSION
                and metadata.get("layout_name") == layout_name
                and metadata.get("canvas") == {"w": self.PAGE_SIZE[0], "h": self.PAGE_SIZE[1]}
                and len(metadata.get("positions") or []) == 4
                and all((root / f"panel_{index}.png").is_file() for index in range(1, 5))
            )
            return metadata if valid else None

        existing = cached()
        if existing:
            return existing
        with self._layout_geometry_lock:
            existing = cached()
            if existing:
                return existing
            root.mkdir(parents=True, exist_ok=True)
            prompt, outputs = self.adapter.layout_geometry(
                layout_name, self.adapter.asset_paths["layouts_dir"],
                f"GKComic/{project_id}/page_{page_index}/layout_geometry/{Path(layout_name).stem}",
            )
            history = self.run_prompt(project_id, prompt, lambda **_: None, 8, f"建立第 {page_index} 頁 Stage 8 動畫幾何", timeout=300)
            positions: list[dict[str, int]] = []
            for panel_index, (mask_node, bbox_node) in enumerate(zip(outputs["masks"], outputs["bboxes"]), start=1):
                mask_outputs = self.client.image_outputs(history, str(mask_node))
                if not mask_outputs:
                    raise ValueError(f"Stage 8 沒有輸出第 {panel_index} 格 mask")
                bbox_values = json.loads(self.client.text_output(history, str(bbox_node)))
                if not isinstance(bbox_values, list) or len(bbox_values) != 4:
                    raise ValueError(f"Stage 8 第 {panel_index} 格 bbox 格式錯誤")
                x0, y0, x1, y1 = (int(round(float(value))) for value in bbox_values)
                width, height = x1 - x0, y1 - y0
                if width <= 0 or height <= 0:
                    raise ValueError(f"Stage 8 第 {panel_index} 格 bbox 無效")
                content = self.client.download_output(mask_outputs[0])
                with Image.open(io.BytesIO(content)).convert("L") as mask:
                    moments = cv2.moments(np.asarray(mask, dtype=np.uint8), binaryImage=True)
                    local_x = moments["m10"] / moments["m00"] if moments["m00"] else mask.width / 2
                    local_y = moments["m01"] / moments["m00"] if moments["m00"] else mask.height / 2
                    center_x = int(round(x0 + local_x / mask.width * width))
                    center_y = int(round(y0 + local_y / mask.height * height))
                    rgba = Image.new("RGBA", mask.size, (255, 255, 255, 0))
                    rgba.putalpha(mask)
                    png_info = PngImagePlugin.PngInfo()
                    png_info.add_text("layout_mask_version", self.LAYOUT_MASK_VERSION)
                    rgba.save(root / f"panel_{panel_index}.png", "PNG", pnginfo=png_info)
                    rgba.close()
                positions.append({"x": x0, "y": y0, "w": width, "h": height, "cx": center_x, "cy": center_y})
            metadata = {
                "version": self.LAYOUT_MASK_VERSION,
                "layout_name": layout_name,
                "canvas": {"w": self.PAGE_SIZE[0], "h": self.PAGE_SIZE[1]},
                "positions": positions,
            }
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            return metadata

    def panel_positions(self, page: dict[str, Any]) -> tuple[dict[str, int], list[dict[str, int]]]:
        metadata = self.ensure_layout_geometry(page["project_id"], page["page_index"])
        return metadata["canvas"], metadata["positions"]

    def layout_mask_path(self, project_id: str, page_index: int, panel_index: int) -> Path:
        page = self.repository.get_page(project_id, page_index)
        layout_key = Path(page.get("selected_layout") or "layout").stem
        destination = self.settings.projects_dir / project_id / f"page_{page_index}" / "layout_masks" / layout_key / f"panel_{panel_index}.png"
        if panel_index not in range(1, 5):
            raise ValueError("Panel 不存在")
        self.ensure_layout_geometry(project_id, page_index)
        if not destination.is_file():
            raise ValueError("Stage 8 panel mask 尚未建立")
        return destination

    def save_outputs(self, history: dict[str, Any], node_id: int, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        saved = []
        for index, image in enumerate(self.client.image_outputs(history, str(node_id)), start=1):
            suffix = Path(image["filename"]).suffix or ".png"
            path = directory / f"{index:02d}{suffix}"
            path.write_bytes(self.client.download_output(image))
            saved.append(path)
        return saved

    def save_debug_inputs(self, project_id: str, page_index: int, panel_index: int, revision: int,
                          history: dict[str, Any], outputs: dict[str, int]) -> None:
        root = self.settings.projects_dir / project_id / f"page_{page_index}" / "debug_inputs" / f"panel_{panel_index}" / f"rev_{revision}"
        for key, kind in (
            ("character_combination", "character_combination"),
            ("text_reference", "text_reference"),
            ("raw_panel", "raw_generated_panel"),
        ):
            node_id = outputs.get(key)
            if not node_id:
                continue
            for path in self.save_outputs(history, node_id, root / key):
                self.repository.add_asset(project_id, kind, str(path), page_index, panel_index, revision)

    def page_asset_dir(self, project_id: str, page_index: int, revision: int) -> Path:
        path = self.settings.projects_dir / project_id / f"page_{page_index}" / f"rev_{revision}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def character_records(self, ids: list[int]) -> list[dict[str, Any]]:
        data = json.loads(self.settings.characters_json.read_text(encoding="utf-8"))["characters"]
        wanted = {int(item) for item in ids}
        return [item for item in data if int(item["id"]) in wanted]

    def four_panel_layouts(self) -> dict[str, Any]:
        layouts = json.loads(self.settings.layouts_json.read_text(encoding="utf-8"))
        return {name: value for name, value in layouts.items() if value.get("panel_count") == 4}
