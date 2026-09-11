from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "comfyui_nodes" / "GK_comic"
DEFAULT_TARGET = Path(r"D:\ComfyUI\ComfyUI\ComfyUI\custom_nodes\GK_comic")


def sync(target: Path) -> None:
    if not (target / "__init__.py").is_file():
        raise FileNotFoundError(f"找不到 GK_comic custom node：{target}")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    active = target / "dialogue_text_corrector.py"
    if active.is_file():
        backup = target / "backup" / f"dialogue_text_corrector_{timestamp}.py"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(active, backup)
        print(f"backup: {backup}")

    shutil.copy2(SOURCE / "dialogue_text_corrector.py", active)
    shutil.copy2(SOURCE / "requirements.txt", target / "requirements.txt")
    for folder in ("fonts", "models"):
        source_folder = SOURCE / folder
        target_folder = target / folder
        target_folder.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source_folder,
            target_folder,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
    print(f"synced: {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="同步新版 DialogueTextCorrector 至 ComfyUI custom_nodes")
    parser.add_argument("target", nargs="?", type=Path, default=DEFAULT_TARGET)
    sync(parser.parse_args().target.resolve())
