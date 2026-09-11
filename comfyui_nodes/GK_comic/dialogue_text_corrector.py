from __future__ import annotations

import json
import importlib
import importlib.util
import math
import sys
import threading
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont


MODULE_DIR = Path(__file__).resolve().parent
CTD_SOURCE_DIR = MODULE_DIR / "models" / "comic-text-detector"
CTD_MODEL_PATH = MODULE_DIR / "models" / "comictextdetector.pt"
FONT_PATH = MODULE_DIR / "fonts" / "jf-openhuninn-2.0.ttf"

TEXT_DETECTION_THRESHOLD = 0.80
VARIABLE_CONFIDENCE_LEVELS = tuple(round(value / 10, 1) for value in range(8, 0, -1))
TEXT_MASK_THRESHOLD = 96
MIN_TEXT_AREA = 50
MAX_TEXT_GAP_X = 80
MAX_TEXT_GAP_Y = 60
MAX_CENTER_DISTANCE_FACTOR = 5.0
MIN_X_OVERLAP_RATIO = 0.20
MAX_TEXT_HEIGHT_RATIO = 2.8

BUBBLE_EXPAND_X = 40
BUBBLE_EXPAND_Y = 30
BUBBLE_SEARCH_MARGIN = 150
WHITE_THRESHOLD = 210
BUBBLE_MIN_AREA_RATIO = 0.25
BUBBLE_MAX_IMAGE_AREA_RATIO = 0.70
BUBBLE_INTERIOR_ERODE = 8

CLEAR_MASK_DILATION_MIN = 3
CLEAR_MASK_DILATION_MAX = 8
FONT_SIZE_MAX = 72
FONT_SIZE_MIN = 10
TEXT_LINE_SPACING = 1.15
TEXT_REGION_PADDING = 10

_DETECTORS: dict[float, Any] = {}
_READER = None
_LOAD_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()


@dataclass
class TextDetection:
    index: int
    bbox: list[int]
    confidence: float
    area: int
    line_count: int
    font_size: float
    vertical: bool


@dataclass
class TextGroup:
    index: int
    detection_indices: list[int]
    bbox: list[int]
    total_text_area: int
    score: float = 0.0


def _numpy_upstream_compatibility() -> None:
    aliases = {
        "bool8": np.bool_,
        "float_": np.float64,
        "int_": np.int64,
        "uint": np.uint64,
        "ScalarType": np.generic,
    }
    for name, value in aliases.items():
        if not hasattr(np, name):
            setattr(np, name, value)


def _load_text_detector(confidence: float = TEXT_DETECTION_THRESHOLD):
    threshold = round(float(np.clip(confidence, 0.05, 0.95)), 2)
    with _LOAD_LOCK:
        # Confidence is changed on the loaded detector for each attempt. Keep
        # only one GPU model instead of loading eight identical model copies.
        if _DETECTORS:
            return next(iter(_DETECTORS.values()))
        if not (CTD_SOURCE_DIR / "inference.py").is_file():
            raise FileNotFoundError(f"comic-text-detector 原始碼不存在：{CTD_SOURCE_DIR}")
        if not CTD_MODEL_PATH.is_file():
            raise FileNotFoundError(f"comic-text-detector 模型不存在：{CTD_MODEL_PATH}")

        try:
            import torchsummary  # noqa: F401
        except ImportError:
            shim = types.ModuleType("torchsummary")
            shim.summary = lambda *args, **kwargs: None
            sys.modules["torchsummary"] = shim
        try:
            import wandb  # noqa: F401
        except ImportError:
            # Upstream imports wandb from training utilities, but inference
            # never calls it. Keep the active ComfyUI environment inference-only.
            wandb_shim = types.ModuleType("wandb")
            wandb_shim.init = lambda *args, **kwargs: None
            sys.modules["wandb"] = wandb_shim
        _numpy_upstream_compatibility()
        package_name = "_gk_comic_text_detector"
        if package_name not in sys.modules:
            package_spec = importlib.util.spec_from_file_location(
                package_name,
                CTD_SOURCE_DIR / "__init__.py",
                submodule_search_locations=[str(CTD_SOURCE_DIR)],
            )
            if package_spec is None or package_spec.loader is None:
                raise ImportError(f"無法建立 comic-text-detector package：{CTD_SOURCE_DIR}")
            package = importlib.util.module_from_spec(package_spec)
            sys.modules[package_name] = package
            package_spec.loader.exec_module(package)
        TextDetector = importlib.import_module(f"{package_name}.inference").TextDetector

        device = "cuda" if torch.cuda.is_available() else "cpu"
        detector = TextDetector(
            model_path=str(CTD_MODEL_PATH),
            input_size=1024,
            device=device,
            conf_thresh=threshold,
            act="leaky",
        )
        _DETECTORS[threshold] = detector
        print(f"[TextCorrection] comic-text-detector loaded: device={device}, threshold={threshold}")
        return detector


def _load_reader():
    global _READER
    with _LOAD_LOCK:
        if _READER is None:
            import easyocr
            _READER = easyocr.Reader(["ch_tra", "en"], gpu=False, verbose=False)
    return _READER


def _parse_prompt(panel_prompt_json: Any) -> dict[str, Any]:
    value = panel_prompt_json if isinstance(panel_prompt_json, dict) else json.loads(str(panel_prompt_json or "{}"))
    if not isinstance(value, dict):
        raise ValueError("panel_prompt_json 必須是 JSON object")
    return value


def _extract_expected_text(prompt: dict[str, Any]) -> str:
    if "文字內容" not in prompt:
        raise ValueError("Stage 3 Panel Prompt 缺少『文字內容』")
    return str(prompt.get("文字內容") or "").strip()


def _validate_mapping(prompt: dict[str, Any], page_index: int, panel_index: int) -> None:
    stored_page = prompt.get("page_index")
    stored_panel = prompt.get("panel_index", prompt.get("序號"))
    if stored_page is not None and int(stored_page) != int(page_index):
        raise ValueError(f"page_index mapping 不一致：Prompt={stored_page}, Input={page_index}")
    if stored_panel is not None and int(stored_panel) != int(panel_index):
        raise ValueError(f"panel_index mapping 不一致：Prompt={stored_panel}, Input={panel_index}")


def _clip_box(box: Sequence[int], width: int, height: int) -> list[int]:
    x1, y1, x2, y2 = (int(round(value)) for value in box)
    return [max(0, x1), max(0, y1), min(width, x2), min(height, y2)]


def _box_area(box: Sequence[int]) -> int:
    return max(0, int(box[2]) - int(box[0])) * max(0, int(box[3]) - int(box[1]))


def _union_box(boxes: Iterable[Sequence[int]]) -> list[int]:
    boxes = list(boxes)
    return [
        min(int(box[0]) for box in boxes),
        min(int(box[1]) for box in boxes),
        max(int(box[2]) for box in boxes),
        max(int(box[3]) for box in boxes),
    ]


def _expand_box(box: Sequence[int], dx: int, dy: int, width: int, height: int) -> list[int]:
    return _clip_box([box[0] - dx, box[1] - dy, box[2] + dx, box[3] + dy], width, height)


def _text_detections_from_blocks(
    blocks, text_mask: np.ndarray, shape: tuple[int, ...], threshold: float,
) -> list[TextDetection]:
    height, width = shape[:2]
    detections: list[TextDetection] = []
    for block in blocks:
        bbox = _clip_box(block.xyxy, width, height)
        if _box_area(bbox) < MIN_TEXT_AREA:
            continue
        x1, y1, x2, y2 = bbox
        region = text_mask[y1:y2, x1:x2]
        mask_support = float(np.count_nonzero(region)) / max(1, region.size)
        detections.append(TextDetection(
            index=len(detections),
            bbox=bbox,
            confidence=round(float(np.clip(max(threshold, mask_support), 0, 1)), 4),
            area=_box_area(bbox),
            line_count=max(1, len(getattr(block, "lines", []))),
            font_size=float(max(1, getattr(block, "font_size", y2 - y1))),
            vertical=bool(getattr(block, "vertical", False)),
        ))
    return detections


def _synthetic_text_input(
    shape: tuple[int, ...], supplied: list[dict[str, Any]], threshold: float,
) -> tuple[list[TextDetection], np.ndarray]:
    height, width = shape[:2]
    mask = np.zeros((height, width), np.uint8)
    detections: list[TextDetection] = []
    for item in supplied:
        score = float(item.get("confidence", 1.0))
        if score < threshold:
            continue
        bbox = _clip_box(item.get("bbox") or [0, 0, 0, 0], width, height)
        if _box_area(bbox) < MIN_TEXT_AREA:
            continue
        x1, y1, x2, y2 = bbox
        cv2.rectangle(mask, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), 255, -1)
        detections.append(TextDetection(
            index=len(detections), bbox=bbox, confidence=score, area=_box_area(bbox),
            line_count=int(item.get("line_count", 1)), font_size=float(item.get("font_size", y2 - y1)),
            vertical=bool(item.get("vertical", False)),
        ))
    return detections, mask


def _detect_text(
    image_rgb: np.ndarray, confidence: float,
    supplied: list[dict[str, Any]] | None = None,
) -> tuple[list[TextDetection], np.ndarray]:
    if supplied is not None:
        return _synthetic_text_input(image_rgb.shape, supplied, confidence)
    image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    detector = _load_text_detector(TEXT_DETECTION_THRESHOLD)
    threshold = round(float(np.clip(confidence, 0.1, 0.8)), 1)
    # ComfyUI may execute multiple panels concurrently. Serialize the mutable
    # threshold and inference call so one panel cannot change another's value.
    with _INFERENCE_LOCK:
        detector.conf_thresh = threshold
        _, refined_mask, blocks = detector(image_bgr, keep_undetected_mask=True)
    refined_mask = np.asarray(refined_mask, dtype=np.uint8)
    return _text_detections_from_blocks(blocks, refined_mask, image_rgb.shape, threshold), refined_mask


def _axis_overlap(a1: int, a2: int, b1: int, b2: int) -> int:
    return max(0, min(a2, b2) - max(a1, b1))


def _same_text_group(a: TextDetection, b: TextDetection) -> bool:
    ax1, ay1, ax2, ay2 = a.bbox
    bx1, by1, bx2, by2 = b.bbox
    aw, ah = ax2 - ax1, ay2 - ay1
    bw, bh = bx2 - bx1, by2 - by1
    gap_x = max(0, max(ax1, bx1) - min(ax2, bx2))
    gap_y = max(0, max(ay1, by1) - min(ay2, by2))
    overlap_x = _axis_overlap(ax1, ax2, bx1, bx2) / max(1, min(aw, bw))
    overlap_y = _axis_overlap(ay1, ay2, by1, by2) / max(1, min(ah, bh))
    center_distance = math.hypot((ax1 + ax2 - bx1 - bx2) / 2, (ay1 + ay2 - by1 - by2) / 2)
    average_height = max(1.0, (ah + bh) / 2)
    height_ratio = max(ah, bh) / max(1, min(ah, bh))
    return bool(
        (overlap_x >= MIN_X_OVERLAP_RATIO or overlap_y >= 0.35)
        and gap_x <= MAX_TEXT_GAP_X
        and gap_y <= MAX_TEXT_GAP_Y
        and center_distance <= MAX_CENTER_DISTANCE_FACTOR * average_height
        and height_ratio <= MAX_TEXT_HEIGHT_RATIO
    )


def _group_text(detections: list[TextDetection]) -> list[TextGroup]:
    parent = list(range(len(detections)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(detections)):
        for right in range(left + 1, len(detections)):
            if _same_text_group(detections[left], detections[right]):
                union(left, right)

    members: dict[int, list[int]] = {}
    for index in range(len(detections)):
        members.setdefault(find(index), []).append(index)
    groups = []
    for indices in members.values():
        groups.append(TextGroup(
            index=len(groups),
            detection_indices=indices,
            bbox=_union_box(detections[index].bbox for index in indices),
            total_text_area=sum(detections[index].area for index in indices),
        ))
    return groups


def _ring_statistics(image_rgb: np.ndarray, box: Sequence[int]) -> tuple[float, float, float]:
    height, width = image_rgb.shape[:2]
    outer = _expand_box(box, BUBBLE_EXPAND_X, BUBBLE_EXPAND_Y, width, height)
    ox1, oy1, ox2, oy2 = outer
    x1, y1, x2, y2 = box
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    patch = gray[oy1:oy2, ox1:ox2]
    if not patch.size:
        return 0.0, 0.0, 0.0
    light_ratio = float(np.mean(patch >= WHITE_THRESHOLD))
    ring = np.ones(patch.shape, np.uint8)
    ring[max(0, y1 - oy1):max(0, y2 - oy1), max(0, x1 - ox1):max(0, x2 - ox1)] = 0
    ring_pixels = patch[ring > 0]
    dark_ratio = float(np.mean(ring_pixels < 100)) if ring_pixels.size else 0.0
    return light_ratio, dark_ratio, float(np.std(patch))


def _select_primary_text_group(
    groups: list[TextGroup], detections: list[TextDetection], image_rgb: np.ndarray,
) -> TextGroup:
    image_area = image_rgb.shape[0] * image_rgb.shape[1]
    for group in groups:
        light_ratio, dark_ratio, contrast = _ring_statistics(image_rgb, group.bbox)
        heights = [detections[index].bbox[3] - detections[index].bbox[1] for index in group.detection_indices]
        consistency = 1 / (1 + float(np.std(heights)) / max(1.0, float(np.mean(heights))))
        huge_penalty = 5.0 if _box_area(group.bbox) > image_area * 0.45 else 0.0
        group.score = round(
            math.log1p(group.total_text_area)
            + min(3.0, len(group.detection_indices) * 0.8)
            + 3.0 * light_ratio + dark_ratio + consistency + contrast / 255 - huge_penalty,
            4,
        )
    return max(groups, key=lambda item: item.score)


def _text_seed_mask(shape: tuple[int, int], group: TextGroup, detections: list[TextDetection]) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    for index in group.detection_indices:
        x1, y1, x2, y2 = detections[index].bbox
        cv2.rectangle(mask, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), 255, -1)
    return mask


def _infer_bubble(
    image_rgb: np.ndarray, group: TextGroup, detections: list[TextDetection],
) -> tuple[np.ndarray, list[int], str, float, dict[str, Any]]:
    height, width = image_rgb.shape[:2]
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    search_margin = max(BUBBLE_SEARCH_MARGIN, round(max(width, height) * 0.12))
    search = _expand_box(group.bbox, search_margin, search_margin, width, height)
    sx1, sy1, sx2, sy2 = search
    roi = gray[sy1:sy2, sx1:sx2]
    light = np.where(roi >= WHITE_THRESHOLD, 255, 0).astype(np.uint8)
    scale = max(1, round(min(width, height) / 700))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * scale + 1, 2 * scale + 1))
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE, kernel, iterations=2)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(light, 8)

    local_text = np.zeros(light.shape, np.uint8)
    for index in group.detection_indices:
        x1, y1, x2, y2 = detections[index].bbox
        cv2.rectangle(
            local_text,
            (max(0, x1 - sx1), max(0, y1 - sy1)),
            (min(sx2 - sx1 - 1, x2 - sx1), min(sy2 - sy1 - 1, y2 - sy1)),
            255, -1,
        )
    text_pixels = max(1, np.count_nonzero(local_text))
    candidates: list[dict[str, Any]] = []
    for label in range(1, count):
        component = (labels == label).astype(np.uint8) * 255
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(component)
        cv2.drawContours(filled, contours, -1, 255, cv2.FILLED)
        cover = cv2.dilate(filled, kernel, iterations=max(1, scale)) > 0
        coverage = float(np.count_nonzero(cover & (local_text > 0))) / text_pixels
        x, y, candidate_width, candidate_height, area = [int(value) for value in stats[label]]
        area_ratio = area / max(1, _box_area(group.bbox))
        image_ratio = area / max(1, width * height)
        if coverage < 0.20 or area_ratio < BUBBLE_MIN_AREA_RATIO or image_ratio > BUBBLE_MAX_IMAGE_AREA_RATIO:
            continue
        band = cv2.dilate(component, kernel, iterations=2) - cv2.erode(component, kernel, iterations=1)
        boundary_values = roi[band > 0]
        boundary_dark = float(np.mean(boundary_values < 150)) if boundary_values.size else 0.0
        interior_brightness = float(np.mean(roi[component > 0])) / 255
        score = 5 * coverage + 2 * interior_brightness + boundary_dark + min(1.0, area_ratio / 4) - max(0.0, image_ratio - 0.35) * 8
        candidates.append({
            "label": label,
            "score": round(score, 4),
            "coverage": round(coverage, 4),
            "bbox": [x + sx1, y + sy1, x + candidate_width + sx1, y + candidate_height + sy1],
            "area": area,
        })

    bubble_mask = np.zeros((height, width), np.uint8)
    if candidates:
        best = max(candidates, key=lambda item: item["score"])
        component = (labels == best["label"]).astype(np.uint8) * 255
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(component, contours, -1, 255, cv2.FILLED)
        bubble_mask[sy1:sy2, sx1:sx2] = component
        bubble_box = best["bbox"]
        method = "opencv"
    else:
        average_height = np.mean([detections[index].bbox[3] - detections[index].bbox[1] for index in group.detection_indices])
        line_count = sum(detections[index].line_count for index in group.detection_indices)
        dx = max(BUBBLE_EXPAND_X, round(average_height * max(1.2, line_count * 0.5)))
        dy = max(BUBBLE_EXPAND_Y, round(average_height * 0.9))
        bubble_box = _expand_box(group.bbox, dx, dy, width, height)
        x1, y1, x2, y2 = bubble_box
        cv2.rectangle(bubble_mask, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), 255, -1)
        method = "fallback"

    seed = _text_seed_mask((height, width), group, detections)
    coverage = float(np.count_nonzero((bubble_mask > 0) & (seed > 0))) / max(1, np.count_nonzero(seed))
    return bubble_mask, bubble_box, method, coverage, {"search_bbox": search, "candidates": candidates}


def _build_clear_mask(
    text_mask: np.ndarray, detection_indices: list[int], detections: list[TextDetection],
    bubble_box: Sequence[int], bubble_mask: np.ndarray,
) -> np.ndarray:
    height, width = text_mask.shape[:2]
    selected = np.zeros((height, width), np.uint8)
    for index in detection_indices:
        x1, y1, x2, y2 = _expand_box(detections[index].bbox, 2, 2, width, height)
        selected[y1:y2, x1:x2] = 255
    detector_text = np.where(text_mask >= TEXT_MASK_THRESHOLD, 255, 0).astype(np.uint8)
    clear = cv2.bitwise_and(detector_text, selected)
    # keep_undetected_mask=True can retain isolated punctuation even when CTD
    # does not promote it to a TextBlock. Include that mask around the bubble.
    bx1, by1, bx2, by2 = bubble_box
    zone_dx = max(10, round((bx2 - bx1) * 0.10))
    zone_dy = max(10, round((by2 - by1) * 0.14))
    zx1, zy1, zx2, zy2 = _expand_box(bubble_box, zone_dx, zone_dy, width, height)
    zone = np.zeros((height, width), np.uint8)
    zone[zy1:zy2, zx1:zx2] = 255
    border_guard = max(5, round(min(width, height) / 140))
    guard_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * border_guard + 1, 2 * border_guard + 1),
    )
    safe_inside = cv2.erode(bubble_mask, guard_kernel, iterations=1)
    outside_distance = cv2.distanceTransform(cv2.bitwise_not(bubble_mask), cv2.DIST_L2, 5)
    far_outside = np.where(outside_distance >= border_guard * 1.5, 255, 0).astype(np.uint8)
    punctuation_zone = cv2.bitwise_or(safe_inside, far_outside)
    extra_text = cv2.bitwise_and(detector_text, zone)
    extra_text = cv2.bitwise_and(extra_text, punctuation_zone)
    clear = cv2.bitwise_or(clear, extra_text)
    expected_area = sum(detections[index].area for index in detection_indices)
    if np.count_nonzero(clear) < max(8, expected_area * 0.005):
        clear = selected
    # Keep the detector's exact glyph pixels even when they touch or cross the
    # inferred bubble border. Only the dilation halo is guarded, so border
    # strokes and tails remain protected without leaving old glyph fragments.
    glyph_core = clear.copy()
    dilation = int(np.clip(round(min(width, height) / 180), CLEAR_MASK_DILATION_MIN, CLEAR_MASK_DILATION_MAX))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilation + 1, 2 * dilation + 1))
    clear = cv2.dilate(clear, kernel, iterations=1)
    guarded_halo = cv2.bitwise_and(clear, punctuation_zone)
    return cv2.bitwise_or(glyph_core, guarded_halo)


def _clear_text(
    image_rgb: np.ndarray, clear_mask: np.ndarray, bubble_mask: np.ndarray,
) -> tuple[np.ndarray, list[int], int, int]:
    inside_mask = cv2.bitwise_and(clear_mask, bubble_mask)
    outside_mask = cv2.bitwise_and(clear_mask, cv2.bitwise_not(bubble_mask))
    # Generated text can overflow the speech bubble. Restore those pixels from
    # their local surroundings instead of painting the exterior bubble-white.
    cleaned = image_rgb.copy()
    if np.any(outside_mask):
        # Inpainting directly across the bubble edge pulls white bubble pixels
        # into dark panel gutters. Repair each exterior component from a ring
        # that is also outside the bubble, preferring samples farther from the
        # border when the inferred bubble is slightly undersized.
        count, labels = cv2.connectedComponents((outside_mask > 0).astype(np.uint8), connectivity=8)
        outside_distance = cv2.distanceTransform(cv2.bitwise_not(bubble_mask), cv2.DIST_L2, 5)
        sample_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
        all_exterior = bubble_mask == 0
        for label in range(1, count):
            component = np.where(labels == label, 255, 0).astype(np.uint8)
            ring = cv2.dilate(component, sample_kernel, iterations=2)
            ring = cv2.bitwise_and(ring, cv2.bitwise_not(component))
            ring_pixels = (ring > 0) & all_exterior & (outside_mask == 0)
            far_pixels = ring_pixels & (outside_distance >= 5)
            samples = image_rgb[far_pixels]
            if len(samples) < 8:
                samples = image_rgb[ring_pixels]
            if len(samples):
                exterior_fill = np.median(samples, axis=0).astype(np.uint8)
                alpha = cv2.GaussianBlur(component, (3, 3), 0).astype(np.float32)[..., None] / 255
                solid = np.empty_like(cleaned)
                solid[:] = exterior_fill
                cleaned = np.clip(cleaned * (1 - alpha) + solid * alpha, 0, 255).astype(np.uint8)
            else:
                cleaned = cv2.inpaint(cleaned, component, 4, cv2.INPAINT_TELEA)
    radius = max(5, CLEAR_MASK_DILATION_MAX + 2)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    ring = cv2.dilate(inside_mask, kernel, iterations=2)
    ring = cv2.bitwise_and(ring, cv2.bitwise_not(inside_mask))
    ring = cv2.bitwise_and(ring, bubble_mask)
    samples = image_rgb[ring > 0]
    if len(samples) < 10:
        samples = image_rgb[(bubble_mask > 0) & (inside_mask == 0)]
    fill = np.median(samples, axis=0).astype(np.uint8) if len(samples) else np.array([255, 255, 255], np.uint8)
    cleaned[inside_mask > 0] = fill
    alpha = cv2.GaussianBlur(inside_mask, (3, 3), 0).astype(np.float32)[..., None] / 255
    solid = np.empty_like(cleaned)
    solid[:] = fill
    cleaned = np.clip(cleaned * (1 - alpha) + solid * alpha, 0, 255).astype(np.uint8)
    return (
        cleaned,
        [int(value) for value in fill],
        int(np.count_nonzero(inside_mask)),
        int(np.count_nonzero(outside_mask)),
    )


def _safe_text_box(bubble_mask: np.ndarray, bubble_box: Sequence[int], text_box: Sequence[int]) -> list[int]:
    height, width = bubble_mask.shape
    erode = max(1, BUBBLE_INTERIOR_ERODE)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode + 1, 2 * erode + 1))
    inner = cv2.erode(bubble_mask, kernel, iterations=1)
    x1, y1, x2, y2 = bubble_box
    roi = inner[y1:y2, x1:x2]
    if roi.size:
        row_good = np.mean(roi > 0, axis=1) >= 0.68
        column_good = np.mean(roi > 0, axis=0) >= 0.55

        def containing_run(values: np.ndarray, center: int) -> tuple[int, int] | None:
            indices = np.where(values)[0]
            if not indices.size:
                return None
            runs: list[tuple[int, int]] = []
            start = previous = int(indices[0])
            for value in indices[1:]:
                value = int(value)
                if value != previous + 1:
                    runs.append((start, previous + 1))
                    start = value
                previous = value
            runs.append((start, previous + 1))
            containing = [run for run in runs if run[0] <= center < run[1]]
            return max(containing or runs, key=lambda run: run[1] - run[0])

        text_center_x = int((text_box[0] + text_box[2]) / 2) - x1
        text_center_y = int((text_box[1] + text_box[3]) / 2) - y1
        row_run = containing_run(row_good, text_center_y)
        column_run = containing_run(column_good, text_center_x)
        if row_run and column_run:
            candidate = [x1 + column_run[0], y1 + row_run[0], x1 + column_run[1], y1 + row_run[1]]
            if _box_area(candidate) >= max(400, _box_area(text_box) * 0.20):
                return _clip_box(candidate, width, height)
    fallback = list(_clip_box(bubble_box, width, height))
    bubble_height = fallback[3] - fallback[1]
    text_center_y = (text_box[1] + text_box[3]) / 2
    bubble_center_y = (fallback[1] + fallback[3]) / 2
    if text_center_y < bubble_center_y - bubble_height * 0.07:
        fallback[3] -= round(bubble_height * 0.22)
    elif text_center_y > bubble_center_y + bubble_height * 0.07:
        fallback[1] += round(bubble_height * 0.22)
    # A speech bubble can touch the image edge or have a steep rounded side.
    # Its bounding box then contains exterior pixels that would clip the first
    # or last glyph. Use conservative row spans from the actual eroded interior
    # to inset the fallback horizontally.
    fx1, fy1, fx2, fy2 = fallback
    fallback_roi = inner[fy1:fy2, fx1:fx2]
    left_edges: list[int] = []
    right_edges: list[int] = []
    minimum_span = max(8, round((fx2 - fx1) * 0.45))
    for row in fallback_roi > 0:
        indices = np.where(row)[0]
        if indices.size and int(indices[-1] - indices[0] + 1) >= minimum_span:
            left_edges.append(int(indices[0]))
            right_edges.append(int(indices[-1] + 1))
    if left_edges and right_edges:
        fallback[0] = max(fallback[0], fx1 + round(float(np.percentile(left_edges, 70))))
        fallback[2] = min(fallback[2], fx1 + round(float(np.percentile(right_edges, 30))))
    return _clip_box(fallback, width, height)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.is_file():
        raise FileNotFoundError(f"繁體中文字型不存在：{path}")
    return ImageFont.truetype(str(path), size=size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap_by_pixels(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and _text_width(draw, candidate, font) > max_width:
                lines.append(current.rstrip())
                current = character.lstrip()
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
    return lines


def _fit_text(
    draw: ImageDraw.ImageDraw, text: str, region: Sequence[int], font_path: Path,
) -> tuple[ImageFont.FreeTypeFont, list[str], int, int]:
    width = max(1, region[2] - region[0] - 2 * TEXT_REGION_PADDING)
    height = max(1, region[3] - region[1] - 2 * TEXT_REGION_PADDING)
    maximum = min(FONT_SIZE_MAX, max(FONT_SIZE_MIN, round(height * 0.42)))
    for size in range(maximum, FONT_SIZE_MIN - 1, -1):
        font = _font(font_path, size)
        lines = _wrap_by_pixels(draw, text, font, width)
        ascent, descent = font.getmetrics()
        line_height = max(1, round((ascent + descent) * TEXT_LINE_SPACING))
        total_height = line_height * len(lines)
        if max((_text_width(draw, line, font) for line in lines), default=0) <= width and total_height <= height:
            return font, lines, line_height, total_height
    raise ValueError(f"正確文字即使縮小至 {FONT_SIZE_MIN}px 仍無法完整放入對話框")


def _draw_text(
    image_rgb: np.ndarray, text: str, region: Sequence[int], font_path: Path,
) -> tuple[np.ndarray, dict[str, Any]]:
    pil_image = Image.fromarray(image_rgb)
    draw = ImageDraw.Draw(pil_image)
    font, lines, line_height, total_height = _fit_text(draw, text, region, font_path)
    x1, y1, x2, y2 = region
    cursor_y = y1 + (y2 - y1 - total_height) / 2
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        line_width = box[2] - box[0]
        x = x1 + (x2 - x1 - line_width) / 2
        draw.text((round(x), round(cursor_y - box[1])), line, font=font, fill=(0, 0, 0))
        cursor_y += line_height
    return np.asarray(pil_image), {
        "font_path": str(font_path), "font_size": font.size, "line_count": len(lines),
        "lines": lines, "safe_text_region": list(region),
    }


def _run_ocr(image_rgb: np.ndarray, box: Sequence[int]) -> list[dict[str, Any]]:
    height, width = image_rgb.shape[:2]
    x1, y1, x2, y2 = _clip_box(box, width, height)
    roi = image_rgb[y1:y2, x1:x2]
    if not roi.size:
        return []
    output = []
    for polygon, text, confidence in _load_reader().readtext(roi, detail=1, paragraph=False):
        output.append({
            "polygon": [[float(point[0]) + x1, float(point[1]) + y1] for point in polygon],
            "text": str(text), "confidence": float(confidence),
        })
    return output


def correct_dialogue_rgb(
    image_rgb: np.ndarray,
    panel_prompt_json: Any,
    page_index: int,
    panel_index: int,
    *,
    confidence: float = TEXT_DETECTION_THRESHOLD,
    bubble_detections: list[dict[str, Any]] | None = None,
    bubble_detector=None,
    minimum_confidence: float = 0.05,
    confidence_step: float = 0.05,
    ocr_results: list[dict[str, Any]] | None = None,
    font_path: Path | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    del bubble_detector, confidence, minimum_confidence, confidence_step  # Legacy workflow inputs retained for compatibility.
    prompt = _parse_prompt(panel_prompt_json)
    _validate_mapping(prompt, page_index, panel_index)
    expected_text = _extract_expected_text(prompt)
    debug: dict[str, Any] = {
        "page_index": int(page_index), "panel_index": int(panel_index),
        "expected_text": expected_text, "replacement_text": expected_text,
        "detector": "dmMaze/comic-text-detector",
        "confidence_mode": "variable_0.8_to_0.1",
        "confidence_levels": list(VARIABLE_CONFIDENCE_LEVELS),
        "replaced": False, "replacement_status": "NO",
    }
    if not expected_text:
        debug.update({"status": "skipped", "warning": "Stage 3 文字內容為空"})
        return image_rgb.copy(), debug

    attempts: list[dict[str, Any]] = []
    accepted = None
    for threshold in VARIABLE_CONFIDENCE_LEVELS:
        detections, text_mask = _detect_text(image_rgb, threshold, bubble_detections)
        attempt: dict[str, Any] = {
            "threshold": threshold,
            "text_detection_count": len(detections),
            "bubble_found": False,
        }
        if not detections:
            attempt["result"] = "no_text"
            attempts.append(attempt)
            continue

        groups = _group_text(detections)
        selected = _select_primary_text_group(groups, detections, image_rgb)
        bubble_mask, bubble_box, method, coverage, bubble_diagnostics = _infer_bubble(
            image_rgb, selected, detections,
        )
        attempt.update({
            "group_count": len(groups),
            "selected_group": selected.index,
            "bubble_method": method,
            "text_coverage": round(coverage, 4),
            "bubble_found": method == "opencv",
            "result": "accepted" if method == "opencv" else "fallback_only",
        })
        attempts.append(attempt)
        if method == "opencv":
            accepted = (
                threshold, detections, text_mask, groups, selected,
                bubble_mask, bubble_box, method, coverage, bubble_diagnostics,
            )
            break

    debug["confidence_attempts"] = attempts
    if accepted is None:
        debug.update({
            "status": "preserved", "detection_threshold": None,
            "text_detection_count": attempts[-1].get("text_detection_count", 0) if attempts else 0,
            "group_count": 0, "selected_group": {}, "bubble_method": "none",
            "text_coverage": 0.0,
            "warning": "信心值由 0.8 降至 0.1 後仍未找到 OpenCV 對話框，保留原圖",
        })
        return image_rgb.copy(), debug

    (
        accepted_threshold, detections, text_mask, groups, selected,
        bubble_mask, bubble_box, method, coverage, bubble_diagnostics,
    ) = accepted
    debug["detection_threshold"] = accepted_threshold
    debug["text_detection_count"] = len(detections)
    debug["detections"] = [asdict(item) for item in detections]
    debug["group_count"] = len(groups)
    debug["groups"] = [asdict(item) for item in groups]
    debug["selected_group"] = asdict(selected)
    debug.update({
        "bubble_method": method, "bubble_bbox": bubble_box,
        "bubble_area": int(np.count_nonzero(bubble_mask)),
        "text_coverage": round(coverage, 4), "bubble_diagnostics": bubble_diagnostics,
    })

    detected_ocr = ocr_results if ocr_results is not None else _run_ocr(image_rgb, selected.bbox)
    debug["ocr_text"] = "".join(str(item.get("text", "")) for item in detected_ocr)
    debug["ocr_region_count"] = len(detected_ocr)

    clear_indices = list(selected.detection_indices)
    bx1, by1, bx2, by2 = bubble_box
    margin_x = max(8, round((bx2 - bx1) * 0.05))
    margin_y = max(8, round((by2 - by1) * 0.08))
    nearby_bubble = _expand_box(bubble_box, margin_x, margin_y, image_rgb.shape[1], image_rgb.shape[0])
    for detection in detections:
        if detection.index in clear_indices:
            continue
        dx1, dy1, dx2, dy2 = detection.bbox
        center_x = (dx1 + dx2) / 2
        center_y = (dy1 + dy2) / 2
        if nearby_bubble[0] <= center_x <= nearby_bubble[2] and nearby_bubble[1] <= center_y <= nearby_bubble[3]:
            clear_indices.append(detection.index)
    clear_mask = _build_clear_mask(text_mask, clear_indices, detections, bubble_box, bubble_mask)
    if not np.any(clear_mask):
        raise ValueError("無法建立文字清除遮罩")
    cleaned, fill_rgb, inside_clear_pixels, outside_clear_pixels = _clear_text(image_rgb, clear_mask, bubble_mask)
    safe_region = _safe_text_box(bubble_mask, bubble_box, selected.bbox)
    corrected, text_layout = _draw_text(cleaned, expected_text, safe_region, font_path or FONT_PATH)
    debug.update({
        "status": "corrected", "replaced": True, "replacement_status": "YES",
        "clear_mask_pixels": int(np.count_nonzero(clear_mask)), "fill_rgb": fill_rgb,
        "clear_detection_indices": clear_indices,
        "inside_clear_pixels": inside_clear_pixels,
        "outside_clear_pixels": outside_clear_pixels,
        **text_layout,
    })
    return corrected, debug


class DialogueTextCorrector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "panel_prompt_json": ("STRING", {"forceInput": True}),
                "page_index": ("INT", {"forceInput": True}),
                "panel_index": ("INT", {"forceInput": True}),
            },
            "optional": {
                "confidence": ("FLOAT", {"default": 0.80, "min": 0.80, "max": 0.80, "step": 0.10}),
                "minimum_confidence": ("FLOAT", {"default": 0.10, "min": 0.10, "max": 0.10, "step": 0.10}),
                "confidence_step": ("FLOAT", {"default": 0.10, "min": 0.10, "max": 0.10, "step": 0.10}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("corrected_panel_image", "debug_info")
    FUNCTION = "correct"
    CATEGORY = "GK_comic/Post Generation"

    def correct(
        self, image, panel_prompt_json, page_index, panel_index,
        confidence=0.80, minimum_confidence=0.10, confidence_step=0.10,
    ):
        original = image
        try:
            if not torch.is_tensor(image) or image.ndim != 4:
                raise ValueError(f"IMAGE 必須是 BHWC tensor，實際為 {type(image)} {getattr(image, 'shape', '')}")
            outputs = []
            debug_items = []
            for batch_index in range(int(image.shape[0])):
                rgb = np.clip(image[batch_index].detach().cpu().numpy() * 255, 0, 255).astype(np.uint8)
                corrected, debug = correct_dialogue_rgb(
                    rgb, panel_prompt_json, int(page_index), int(panel_index),
                    confidence=float(confidence), minimum_confidence=float(minimum_confidence),
                    confidence_step=float(confidence_step),
                )
                outputs.append(torch.from_numpy(corrected.astype(np.float32) / 255))
                debug_items.append(debug)
                prefix = f"[TextCorrection] Page {page_index} Panel {panel_index}"
                print(f"{prefix} Detector: {debug.get('detector')}")
                print(f"{prefix} Confidence attempts: {debug.get('confidence_attempts', [])}")
                print(f"{prefix} Accepted confidence: {debug.get('detection_threshold')}")
                print(f"{prefix} Text detections: {debug.get('text_detection_count', 0)}")
                print(f"{prefix} Text groups: {debug.get('group_count', 0)}")
                print(f"{prefix} Selected group: {debug.get('selected_group', {}).get('index')}")
                print(f"{prefix} Bubble method: {debug.get('bubble_method', 'none')}")
                print(f"{prefix} Bubble bbox: {debug.get('bubble_bbox', [])}")
                print(f"{prefix} Text coverage: {debug.get('text_coverage', 0)}")
                print(f"{prefix} OCR text: {json.dumps(debug.get('ocr_text', ''), ensure_ascii=False)}")
                print(f"{prefix} Clear mask: {debug.get('clear_mask_pixels', 0)} pixels")
                print(f"{prefix} Replacement: {json.dumps(debug.get('replacement_text', ''), ensure_ascii=False)}")
                print(f"{prefix} Font size: {debug.get('font_size', 0)}, lines: {debug.get('line_count', 0)}")
                print(f"{prefix} Replacement status: {debug.get('replacement_status', 'NO')}")
                if debug.get("warning"):
                    print(f"[TextCorrection][WARNING] {debug['warning']}")
            payload = debug_items if len(debug_items) > 1 else debug_items[0]
            return torch.stack(outputs, dim=0), json.dumps(payload, ensure_ascii=False)
        except Exception as exc:
            warning = f"{type(exc).__name__}: {exc}"
            print(f"[TextCorrection][WARNING] Page {page_index} Panel {panel_index}: {warning}; original image preserved")
            return original, json.dumps({
                "page_index": int(page_index), "panel_index": int(panel_index),
                "status": "preserved", "replaced": False,
                "replacement_status": "NO", "warning": warning,
            }, ensure_ascii=False)


NODE_CLASS_MAPPINGS = {"DialogueTextCorrector": DialogueTextCorrector}
NODE_DISPLAY_NAME_MAPPINGS = {"DialogueTextCorrector": "GK Post - Dialogue Text Corrector (CTD)"}
