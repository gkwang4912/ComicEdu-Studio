from __future__ import annotations

import json
import sys
from pathlib import Path


CORRECTOR_NODE_ID = 302
SOURCE_NODE_ID = 129
ALIGNER_NODE_ID = 266
SET_NODE_ID = 275


def update_workflow(path: Path) -> None:
    workflow = json.loads(path.read_text(encoding="utf-8"))
    nodes = {int(node["id"]): node for node in workflow.get("nodes", [])}
    expected = {
        SOURCE_NODE_ID: "VAEDecode",
        ALIGNER_NODE_ID: "PanelGenerationInputAligner",
        SET_NODE_ID: "SetNode",
    }
    for node_id, node_type in expected.items():
        actual = nodes.get(node_id, {}).get("type")
        if actual != node_type:
            raise RuntimeError(f"{path}: Node {node_id} 預期 {node_type}，實際 {actual!r}；停止修改以保護新版工作流")

    aligner = nodes[ALIGNER_NODE_ID]
    if not any(output.get("name") == "panel_prompt_jsons" for output in aligner.get("outputs", [])):
        aligner["outputs"].append({
            "localized_name": "panel_prompt_jsons",
            "name": "panel_prompt_jsons",
            "shape": 6,
            "type": "STRING",
            "links": [442],
        })
    else:
        output = next(item for item in aligner["outputs"] if item.get("name") == "panel_prompt_jsons")
        output["links"] = [442]

    if CORRECTOR_NODE_ID not in nodes:
        corrector = {
            "id": CORRECTOR_NODE_ID,
            "type": "DialogueTextCorrector",
            "pos": [3508.0, 3962.0],
            "size": [330.0, 154.0],
            "flags": {},
            "order": max(int(node.get("order", 0)) for node in workflow["nodes"]) + 1,
            "mode": 0,
            "inputs": [
                {"localized_name": "image", "name": "image", "type": "IMAGE", "link": 389},
                {"localized_name": "panel_prompt_json", "name": "panel_prompt_json", "type": "STRING", "link": 442},
                {"localized_name": "page_index", "name": "page_index", "type": "INT", "link": 443},
                {"localized_name": "panel_index", "name": "panel_index", "type": "INT", "link": 444},
                {"localized_name": "confidence", "name": "confidence", "type": "FLOAT", "widget": {"name": "confidence"}, "link": None},
                {"localized_name": "minimum_confidence", "name": "minimum_confidence", "type": "FLOAT", "widget": {"name": "minimum_confidence"}, "link": None},
                {"localized_name": "confidence_step", "name": "confidence_step", "type": "FLOAT", "widget": {"name": "confidence_step"}, "link": None},
            ],
            "outputs": [
                {"localized_name": "corrected_panel_image", "name": "corrected_panel_image", "type": "IMAGE", "links": [445]},
                {"localized_name": "debug_info", "name": "debug_info", "type": "STRING", "links": None},
            ],
            "properties": {"Node name for S&R": "DialogueTextCorrector"},
            "widgets_values": [0.50, 0.05, 0.05],
            "widgets_values_named": {"confidence": 0.80, "minimum_confidence": 0.10, "confidence_step": 0.10},
            "title": "Stage 7.5 - Dialogue Text Correction",
        }
        workflow["nodes"].append(corrector)
        nodes[CORRECTOR_NODE_ID] = corrector

    corrector = nodes[CORRECTOR_NODE_ID]
    optional_inputs = [
        ("confidence", 0.80),
        ("minimum_confidence", 0.10),
        ("confidence_step", 0.10),
    ]
    existing_inputs = {item.get("name") for item in corrector.get("inputs", [])}
    for name, _ in optional_inputs:
        if name not in existing_inputs:
            corrector["inputs"].append({
                "localized_name": name, "name": name, "type": "FLOAT",
                "widget": {"name": name}, "link": None,
            })
    corrector["widgets_values"] = [value for _, value in optional_inputs]
    corrector["widgets_values_named"] = {name: value for name, value in optional_inputs}

    link_by_id = {int(link[0]): link for link in workflow.get("links", [])}
    source_link = link_by_id.get(389)
    if not source_link or int(source_link[1]) != SOURCE_NODE_ID:
        raise RuntimeError(f"{path}: 找不到 VAEDecode 的 Link 389；停止修改")
    source_link[3] = CORRECTOR_NODE_ID
    source_link[4] = 0

    desired_links = {
        442: [442, ALIGNER_NODE_ID, 8, CORRECTOR_NODE_ID, 1, "STRING"],
        443: [443, ALIGNER_NODE_ID, 6, CORRECTOR_NODE_ID, 2, "INT"],
        444: [444, ALIGNER_NODE_ID, 7, CORRECTOR_NODE_ID, 3, "INT"],
        445: [445, CORRECTOR_NODE_ID, 0, SET_NODE_ID, 0, "IMAGE"],
    }
    workflow["links"] = [link for link in workflow["links"] if int(link[0]) not in desired_links]
    workflow["links"].extend(desired_links.values())

    nodes[SET_NODE_ID]["inputs"][0]["link"] = 445
    nodes[SOURCE_NODE_ID]["outputs"][0]["links"] = [389]
    nodes[CORRECTOR_NODE_ID]["inputs"][0]["link"] = 389
    nodes[CORRECTOR_NODE_ID]["inputs"][1]["link"] = 442
    nodes[CORRECTOR_NODE_ID]["inputs"][2]["link"] = 443
    nodes[CORRECTOR_NODE_ID]["inputs"][3]["link"] = 444
    nodes[CORRECTOR_NODE_ID]["outputs"][0]["links"] = [445]

    workflow["last_node_id"] = max(int(workflow.get("last_node_id", 0)), CORRECTOR_NODE_ID)
    workflow["last_link_id"] = max(int(workflow.get("last_link_id", 0)), max(desired_links))
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated: {path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: update_dialogue_workflow.py WORKFLOW [WORKFLOW ...]")
    for argument in sys.argv[1:]:
        update_workflow(Path(argument).resolve())
