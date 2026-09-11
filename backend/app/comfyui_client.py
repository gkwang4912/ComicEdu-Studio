from __future__ import annotations

import json
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx


class ComfyUIError(RuntimeError):
    pass


class ComfyUIClient:
    def __init__(self, base_url: str, input_dir: Path | None = None):
        self.base_url = base_url.rstrip("/")
        self.input_dir = Path(input_dir).resolve() if input_dir else None
        self.client_id = f"gk-comic-{uuid.uuid4().hex}"
        self.client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), limits=httpx.Limits(max_keepalive_connections=10, max_connections=20))

    def health(self) -> dict[str, Any]:
        try:
            response = self.client.get(f"{self.base_url}/system_stats", timeout=10)
            response.raise_for_status()
            return {
                "connected": True,
                "url": self.base_url,
                "input_dir": str(self.input_dir) if self.input_dir else None,
                "input_dir_configured": bool(self.input_dir and self.input_dir.is_dir()),
                "stats": response.json(),
            }
        except Exception as exc:
            return {"connected": False, "url": self.base_url, "error": str(exc)}

    def object_info(self) -> dict[str, Any]:
        response = self.client.get(f"{self.base_url}/object_info", timeout=30)
        response.raise_for_status()
        return response.json()

    def workflow(self, name: str) -> dict[str, Any]:
        encoded_path = quote(f"workflows/{name}", safe="")
        response = self.client.get(
            f"{self.base_url}/api/userdata/{encoded_path}",
            timeout=30,
        )
        if response.status_code >= 400:
            raise ComfyUIError(f"無法讀取本機工作流 {name}: {self._error_message(response)}")
        return response.json()

    def submit(self, prompt: dict[str, Any]) -> str:
        response = self.client.post(
            f"{self.base_url}/prompt",
            json={"prompt": prompt, "client_id": self.client_id},
            timeout=30,
        )
        if response.status_code >= 400:
            raise ComfyUIError(self._error_message(response))
        payload = response.json()
        if payload.get("node_errors"):
            raise ComfyUIError(json.dumps(payload["node_errors"], ensure_ascii=False))
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError("ComfyUI 未回傳 prompt_id")
        return prompt_id

    def wait(self, prompt_id: str, timeout: int = 900, callback=None) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = self.client.get(f"{self.base_url}/history/{prompt_id}", timeout=15)
            response.raise_for_status()
            history = response.json()
            if prompt_id in history:
                item = history[prompt_id]
                status = item.get("status", {})
                if status.get("status_str") == "error" or status.get("completed") is False:
                    messages = status.get("messages") or []
                    raise ComfyUIError(json.dumps(messages, ensure_ascii=False))
                return item
            if callback:
                callback()
            time.sleep(1)
        raise ComfyUIError(f"等待 ComfyUI prompt {prompt_id} 逾時")

    def download_output(self, image: dict[str, Any]) -> bytes:
        params = urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")})
        response = self.client.get(f"{self.base_url}/view?{params}", timeout=120)
        response.raise_for_status()
        return response.content

    def upload_image(self, path: Path, subfolder: str) -> str:
        return self.upload_file(path, subfolder)

    def upload_file(self, path: Path, subfolder: str) -> str:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            response = self.client.post(
                f"{self.base_url}/upload/image",
                files={"image": (path.name, handle, content_type)},
                data={"type": "input", "subfolder": subfolder, "overwrite": "true"},
                timeout=120,
            )
        response.raise_for_status()
        payload = response.json()
        name = payload.get("name")
        if not name:
            raise ComfyUIError("ComfyUI 上傳圖片後未回傳檔名")
        uploaded_subfolder = str(payload.get("subfolder") or "").strip("/\\")
        return f"{uploaded_subfolder}/{name}" if uploaded_subfolder else str(name)

    def input_file_path(self, uploaded_name: str) -> str:
        relative = Path(uploaded_name.strip("/\\"))
        if self.input_dir:
            return str(self.input_dir / relative)
        return str(Path("input") / relative)

    @staticmethod
    def text_output(history: dict[str, Any], node_id: str) -> str:
        output = history.get("outputs", {}).get(str(node_id), {})
        for key in ("text", "string", "value"):
            value = output.get(key)
            if isinstance(value, list) and value:
                return str(value[0])
            if isinstance(value, str):
                return value
        raise ComfyUIError(f"節點 {node_id} 沒有可解析文字輸出: {output}")

    @staticmethod
    def image_outputs(history: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
        return list(history.get("outputs", {}).get(str(node_id), {}).get("images") or [])

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return response.text[:2000]
