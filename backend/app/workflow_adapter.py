from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .comfyui_client import ComfyUIClient, ComfyUIError


class WorkflowAdapter:
    """Converts the provided UI workflow into small API prompts.

    The original graph relies on UI-only SetNode/GetNode aliases. Every public
    method below reconnects the real stage inputs explicitly and never submits
    those aliases.
    """

    TEXT_OUTPUT_BASE = 9000
    WIDGET_INPUTS = {
        "MaterialFileLoader": ["material_file_path"],
        "MaterialAnalysisPromptBuilder": ["subject", "grade_level", "max_material_characters", "system_prompt", "user_prompt_template"],
        "OpenAIResponsesAPI": ["api_key", "model", "temperature", "max_output_tokens", "output_mode", "output_schema", "request_nonce"],
        "StoryArcPromptBuilder": ["system_prompt", "user_prompt_template"],
        "PageScriptPromptBuilder": ["selected_plan_json", "story_arc_json", "page_json", "system_prompt", "user_prompt_template"],
        "CharacterRecommendationPromptBuilder": ["characters_json_path", "system_prompt", "user_prompt_template"],
        "LayoutRecommendationPromptBuilder": ["layouts_json_path", "expected_count", "system_prompt", "user_prompt_template"],
        "PanelLayoutLoader": ["layout_folder"],
        "CharacterReferenceMerger": ["characters_json_path", "character_root", "padding"],
        "CharacterReferenceSideRandomizer": ["mode", "swap_probability", "panel_count"],
    }

    @staticmethod
    def _api_key_override(api_key: str) -> dict[str, Any]:
        return {"api_key": api_key} if api_key else {}

    ASSET_PATH_NODE_IDS = {
        "characters_json": 173,
        "layouts_json": 175,
        "characters_dir": 186,
        "layouts_dir": 188,
    }

    def __init__(self, workflow_path: Path, client: ComfyUIClient, remote_workflow_name: str):
        self.workflow_path = Path(workflow_path)
        self.client = client
        self.remote_workflow_name = remote_workflow_name
        self._workflow: dict[str, Any] | None = None
        self._nodes: dict[str, dict[str, Any]] | None = None
        self._links: dict[int, list[Any]] | None = None
        self._asset_paths: dict[str, str] | None = None
        self._object_info: dict[str, Any] | None = None
        self._set_sources: dict[str, tuple[str, int]] | None = None
        self._virtual_node_ids: set[str] | None = None

    @property
    def workflow(self) -> dict[str, Any]:
        if self._workflow is None:
            # The project file is the deployment contract. A ComfyUI userdata
            # copy can contain stale/unsaved GUI widget state (for example a
            # StringFormat "{a}" with no values.a), so it must not silently
            # replace the original Comic8.json used by the Backend converter.
            if self.workflow_path.is_file():
                self._workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
            else:
                self._workflow = self.client.workflow(self.remote_workflow_name)
        return self._workflow

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        if self._nodes is None:
            if self.is_api_workflow:
                self._nodes = {str(node_id): node for node_id, node in self.workflow.items()}
            else:
                self._nodes = {str(node["id"]): node for node in self.workflow["nodes"]}
        return self._nodes

    @property
    def is_api_workflow(self) -> bool:
        return "nodes" not in self.workflow and all(
            isinstance(node, dict) and "class_type" in node for node in self.workflow.values()
        )

    @property
    def links(self) -> dict[int, list[Any]]:
        if self._links is None:
            self._links = {} if self.is_api_workflow else {int(link[0]): link for link in self.workflow["links"]}
        return self._links

    @property
    def asset_paths(self) -> dict[str, str]:
        if self._asset_paths is None:
            paths: dict[str, str] = {}
            for name, node_id in self.ASSET_PATH_NODE_IDS.items():
                node = self.nodes.get(str(node_id)) or {}
                if self.is_api_workflow:
                    value = str((node.get("inputs") or {}).get("f_string") or "").strip().strip("'\"").strip()
                else:
                    values = node.get("widgets_values") or []
                    value = str(values[0]).strip().strip("'\"").strip() if values else ""
                if not value:
                    raise ComfyUIError(f"本機工作流缺少 {name} 路徑（節點 {node_id}）")
                paths[name] = value
            self._asset_paths = paths
        return self._asset_paths

    @property
    def object_info(self) -> dict[str, Any]:
        if self._object_info is None:
            self._object_info = self.client.object_info()
        return self._object_info

    @staticmethod
    def _virtual_name(node: dict[str, Any]) -> str:
        named = node.get("widgets_values_named") or {}
        for key in ("Constant", "constant", "name"):
            value = named.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        values = node.get("widgets_values") or []
        if values and isinstance(values[0], str) and values[0].strip():
            return values[0].strip()
        raise ComfyUIError(
            f"Workflow Set/Get resolution failed: {node.get('type')} "
            f"node_id={node.get('id')} has no pair name"
        )

    def _build_set_get_index(self) -> None:
        if self._set_sources is not None:
            return
        set_sources: dict[str, tuple[str, int]] = {}
        virtual_ids: set[str] = set()
        for node_id, node in self.nodes.items():
            class_type = node.get("type")
            if class_type not in {"SetNode", "GetNode"}:
                continue
            virtual_ids.add(node_id)
            if class_type != "SetNode":
                continue
            name = self._virtual_name(node)
            if name in set_sources:
                raise ComfyUIError(
                    f"Workflow Set/Get resolution failed: ambiguous SetNode "
                    f"name={name} node_ids include {node_id}"
                )
            linked_inputs = [item for item in node.get("inputs") or [] if item.get("link") is not None]
            if len(linked_inputs) != 1:
                raise ComfyUIError(
                    f"Workflow Set/Get resolution failed: SetNode name={name} "
                    f"node_id={node_id} has {len(linked_inputs)} upstream links"
                )
            link = self.links.get(int(linked_inputs[0]["link"]))
            if not link:
                raise ComfyUIError(
                    f"Workflow Set/Get resolution failed: SetNode name={name} "
                    f"node_id={node_id} references a missing link"
                )
            set_sources[name] = (str(link[1]), int(link[2]))
        self._set_sources = set_sources
        self._virtual_node_ids = virtual_ids

    def _resolve_source(self, node_id: str, output_slot: int, visited: tuple[str, ...] = ()) -> tuple[str, int]:
        self._build_set_get_index()
        node_id = str(node_id)
        if node_id in visited:
            chain = " -> ".join((*visited, node_id))
            raise ComfyUIError(f"Workflow Set/Get resolution failed: Set/Get cycle detected: {chain}")
        node = self.nodes.get(node_id)
        if not node:
            return node_id, int(output_slot)
        class_type = node.get("type")
        if class_type == "GetNode":
            name = self._virtual_name(node)
            source = (self._set_sources or {}).get(name)
            if source is None:
                raise ComfyUIError(
                    f"Workflow Set/Get resolution failed: Unresolved GetNode "
                    f"name={name} node_id={node_id}"
                )
            return self._resolve_source(source[0], source[1], (*visited, node_id))
        if class_type == "SetNode":
            name = self._virtual_name(node)
            source = (self._set_sources or {}).get(name)
            if source is None:
                raise ComfyUIError(
                    f"Workflow Set/Get resolution failed: SetNode name={name} "
                    f"node_id={node_id} has no resolvable upstream source"
                )
            return self._resolve_source(source[0], source[1], (*visited, node_id))
        return node_id, int(output_slot)

    def inspect_set_get_nodes(self, log: bool = False) -> dict[str, Any]:
        """Statically inspect every UI-only Set/Get pair without submitting a prompt."""
        self._build_set_get_index()
        sets = []
        gets = []
        unresolved = []
        rewired = 0
        for node_id, node in self.nodes.items():
            class_type = node.get("type")
            if class_type == "SetNode":
                name = self._virtual_name(node)
                source = (self._set_sources or {})[name]
                resolved = self._resolve_source(*source)
                sets.append({"node_id": node_id, "name": name, "source": resolved})
            elif class_type == "GetNode":
                name = self._virtual_name(node)
                try:
                    resolved = self._resolve_source(node_id, 0)
                    targets = sum(len(output.get("links") or []) for output in node.get("outputs") or [])
                    rewired += targets
                    gets.append({"node_id": node_id, "name": name, "source": resolved, "targets": targets})
                except ComfyUIError as exc:
                    unresolved.append(str(exc))
        result = {"set_count": len(sets), "get_count": len(gets), "sets": sets, "gets": gets,
                  "rewired_count": rewired, "unresolved_count": len(unresolved), "unresolved": unresolved}
        if log:
            for item in sets:
                print(f"[WorkflowAdapter][SetGet] Set id={item['node_id']} name={item['name']} source={item['source'][0]}:{item['source'][1]}", flush=True)
            for item in gets:
                print(f"[WorkflowAdapter][SetGet] Get id={item['node_id']} name={item['name']} source={item['source'][0]}:{item['source'][1]} targets={item['targets']}", flush=True)
            print(f"[WorkflowAdapter][SetGet] Sets={len(sets)} Gets={len(gets)} Rewired={rewired} Unresolved={len(unresolved)}", flush=True)
        return result

    def _node(self, node_id: int, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        source = self.nodes[str(node_id)]
        class_type = source.get("class_type") if self.is_api_workflow else source["type"]
        schema = self.object_info.get(class_type)
        if not schema:
            raise ComfyUIError(f"ComfyUI 缺少 node class: {class_type}")
        if self.is_api_workflow:
            result = copy.deepcopy(source)
            if overrides:
                result.setdefault("inputs", {}).update(overrides)
            return result
        ui_inputs = {item["name"]: item for item in source.get("inputs") or []}
        raw_widgets = copy.deepcopy(source.get("widgets_values") or [])
        widget_values = iter(raw_widgets)
        inputs: dict[str, Any] = {}
        explicit_widget_names = self.WIDGET_INPUTS.get(class_type)
        if explicit_widget_names:
            for index, name in enumerate(explicit_widget_names):
                if index < len(raw_widgets):
                    inputs[name] = raw_widgets[index]
        if class_type == "KSampler" and len(raw_widgets) >= 7:
            for name, index in {"seed": 0, "steps": 2, "cfg": 3, "sampler_name": 4, "scheduler": 5, "denoise": 6}.items():
                inputs[name] = raw_widgets[index]
        order = schema.get("input_order") or {}
        definitions = schema.get("input") or {}
        for section in ("required", "optional"):
            for name in order.get(section, []):
                definition = (definitions.get(section) or {}).get(name) or []
                options = definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
                ui_input = ui_inputs.get(name)
                if ui_input and ui_input.get("link") is not None:
                    link = self.links[int(ui_input["link"])]
                    resolved_id, resolved_slot = self._resolve_source(str(link[1]), int(link[2]))
                    inputs[name] = [resolved_id, resolved_slot]
                elif name in inputs:
                    continue
                elif not options.get("forceInput"):
                    try:
                        inputs[name] = next(widget_values)
                    except StopIteration:
                        if "default" in options:
                            inputs[name] = copy.deepcopy(options["default"])
        if overrides:
            inputs.update(overrides)
        # Optional IMAGE inputs in the source UI workflow are represented by
        # an empty widget string. ComfyUI expects those inputs to be omitted.
        for name in ("image1", "image2", "image3"):
            if isinstance(inputs.get(name), str):
                inputs.pop(name, None)
        return {"class_type": class_type, "inputs": inputs, "_meta": {"title": source.get("title") or class_type}}

    @staticmethod
    def _synthetic(class_type: str, **inputs: Any) -> dict[str, Any]:
        return {"class_type": class_type, "inputs": inputs}

    def _prompt(self, node_ids: list[int], overrides: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
        overrides = overrides or {}
        prompt: dict[str, Any] = {}
        pending = [str(node_id) for node_id in node_ids]
        while pending:
            node_id = pending.pop(0)
            if node_id in prompt:
                continue
            source = self.nodes.get(node_id)
            if source and (source.get("type") or source.get("class_type")) in {"SetNode", "GetNode"}:
                continue
            prompt[node_id] = self._node(int(node_id), overrides.get(int(node_id)))
            for value in prompt[node_id]["inputs"].values():
                if isinstance(value, list) and len(value) == 2 and str(value[0]) in self.nodes:
                    dependency = str(value[0])
                    dependency_node = self.nodes[dependency]
                    if (dependency_node.get("type") or dependency_node.get("class_type")) in {"SetNode", "GetNode"}:
                        resolved_id, resolved_slot = self._resolve_source(dependency, int(value[1]))
                        value[:] = [resolved_id, resolved_slot]
                        dependency = resolved_id
                    if dependency not in prompt and dependency not in pending:
                        pending.append(dependency)
        self._validate_resolved_prompt(prompt)
        rewires = []
        for target_id, api_node in prompt.items():
            ui_node = self.nodes.get(target_id)
            if not ui_node or self.is_api_workflow:
                continue
            for ui_input in ui_node.get("inputs") or []:
                link_id = ui_input.get("link")
                if link_id is None or int(link_id) not in self.links:
                    continue
                link = self.links[int(link_id)]
                origin_id = str(link[1])
                origin = self.nodes.get(origin_id) or {}
                if origin.get("type") != "GetNode":
                    continue
                resolved = self._resolve_source(origin_id, int(link[2]))
                if api_node["inputs"].get(ui_input["name"]) == [resolved[0], resolved[1]]:
                    rewires.append((origin_id, self._virtual_name(origin), target_id, ui_input["name"], resolved))
        for get_id, name, target_id, input_name, resolved in rewires:
            print(
                f"[WorkflowAdapter][SetGet] Rewired Get {get_id} name={name} "
                f"-> target {target_id}.{input_name} -> source {resolved[0]}:{resolved[1]}",
                flush=True,
            )
        if rewires:
            print(f"[WorkflowAdapter][SetGet] Rewired={len(rewires)} Unresolved=0", flush=True)
        return prompt

    def _validate_resolved_prompt(self, prompt: dict[str, Any]) -> None:
        self._build_set_get_index()
        virtual_ids = self._virtual_node_ids or set()
        for node_id, node in prompt.items():
            if node.get("class_type") in {"SetNode", "GetNode"}:
                raise ComfyUIError(f"Workflow Set/Get resolution failed: virtual node remains in API prompt: {node_id}")
            for input_name, value in node.get("inputs", {}).items():
                if isinstance(value, list) and len(value) == 2 and str(value[0]) in virtual_ids:
                    raise ComfyUIError(
                        f"Workflow Set/Get resolution failed: unresolved virtual node dependency "
                        f"target={node_id}.{input_name} source={value[0]}"
                    )

    def _add_text_outputs(self, prompt: dict[str, Any], source_id: int, slots: list[int]) -> list[int]:
        output_ids = []
        for offset, slot in enumerate(slots, start=1):
            node_id = self.TEXT_OUTPUT_BASE + offset
            prompt[str(node_id)] = self._synthetic("PreviewAny", source=[str(source_id), slot])
            output_ids.append(node_id)
        return output_ids

    def stage1(self, material_path: str, subject: str, grade_level: str, api_key: str) -> tuple[dict[str, Any], list[int]]:
        prompt = self._prompt([227, 220, 221, 233], {
            227: {"material_file_path": material_path},
            220: {"subject": subject, "grade_level": grade_level},
            221: self._api_key_override(api_key),
        })
        # Enforce the workflow's supported range in the schema sent to the AI,
        # then clamp again in the service in case a provider ignores the schema.
        output_schema = prompt["221"]["inputs"].get("output_schema")
        if isinstance(output_schema, str):
            schema = json.loads(output_schema)
            page_count = schema["properties"]["plans"]["items"]["properties"]["recommended_page_count"]
            page_count.update({"minimum": 2, "maximum": 6})
            prompt["221"]["inputs"]["output_schema"] = json.dumps(schema, ensure_ascii=False)
        return prompt, self._add_text_outputs(prompt, 233, [0, 1, 8])

    def stage2(self, selected_plan: dict[str, Any], page_count: int, api_key: str, instruction: str = "") -> tuple[dict[str, Any], list[int]]:
        # The supplied Stage 2 node contract has min=2. For a one-page project,
        # request two AI-planned pages and let the service retain the first page.
        workflow_page_count = max(2, page_count)
        overrides: dict[str, Any] = {"selected_plan": json.dumps(selected_plan, ensure_ascii=False), "page_count": workflow_page_count}
        if instruction:
            original = self._node(241)["inputs"]["user_prompt_template"]
            overrides["user_prompt_template"] = original + "\n\n教師修改要求：\n" + instruction
        prompt = self._prompt([241, 239, 242], {241: overrides, 239: self._api_key_override(api_key)})
        output_schema = prompt["239"]["inputs"].get("output_schema")
        if isinstance(output_schema, str):
            schema = json.loads(output_schema)
            pages_schema = schema["properties"]["pages"]
            pages_schema.update({"minItems": workflow_page_count, "maxItems": workflow_page_count})
            prompt["239"]["inputs"]["output_schema"] = json.dumps(schema, ensure_ascii=False)
        return prompt, self._add_text_outputs(prompt, 242, [0, 1])

    def stage3(self, selected_plan: dict[str, Any], story_arc: dict[str, Any], page: dict[str, Any], api_key: str, instruction: str = "") -> tuple[dict[str, Any], list[int]]:
        overrides: dict[str, Any] = {
            "selected_plan_json": json.dumps(selected_plan, ensure_ascii=False),
            "story_arc_json": json.dumps(story_arc, ensure_ascii=False),
            "page_json": json.dumps(page, ensure_ascii=False),
        }
        text_policy = str(self.workflow.get("stage3_text_policy") or "").strip()
        if text_policy:
            original_system_prompt = self._node(263)["inputs"].get("system_prompt", "")
            overrides["system_prompt"] = original_system_prompt + "\n\n" + text_policy
        if instruction:
            original = self._node(263)["inputs"].get("user_prompt_template", "")
            overrides["user_prompt_template"] = original + "\n\n教師修改要求：\n" + instruction
        prompt = self._prompt([263, 201], {263: overrides, 201: self._api_key_override(api_key)})
        return prompt, self._add_text_outputs(prompt, 201, [0])

    def stage4(self, script: list[dict[str, Any]], characters_json: str, api_key: str) -> tuple[dict[str, Any], list[int]]:
        prompt = self._prompt([208, 210, 209], {
            208: {"script_json": json.dumps(script, ensure_ascii=False), "characters_json_path": characters_json},
            210: self._api_key_override(api_key),
        })
        return prompt, self._add_text_outputs(prompt, 209, [0, 1, 2, 3, 4, 5])

    def stage5(self, script: list[dict[str, Any]], characters: list[dict[str, Any]], layouts_json: str, api_key: str) -> tuple[dict[str, Any], list[int]]:
        prompt = self._prompt([212, 211, 213], {
            212: {
                "script_json": json.dumps(script, ensure_ascii=False),
                "recommended_characters_json": json.dumps(characters, ensure_ascii=False),
                "layouts_json_path": layouts_json,
                "expected_count": 3,
            },
            211: self._api_key_override(api_key),
        })
        return prompt, self._add_text_outputs(prompt, 213, [0, 1, 2, 3, 4])

    def generation(self, script: list[dict[str, Any]], character_ids: list[int], layout_name: str,
                   characters_json: str, characters_dir: str, layouts_dir: str, seed: int,
                   prefix: str) -> tuple[dict[str, Any], dict[str, int]]:
        ids = [214, 86, 91, 94, 93, 97, 80, 82, 81, 85, 215, 216, 266, 218,
               100, 101, 103, 104, 106, 105, 110, 115, 117, 118, 120, 128, 127, 129, 302, 290, 287]
        prompt = self._prompt(ids, {
            214: {"layout_name": layout_name, "layout_folder": layouts_dir},
            # EmptyImage stores width/height widgets even when those inputs are
            # linked. Generic widget decoding would otherwise shift 512 into
            # batch_size and create 512 images for a single panel.
            97: {"batch_size": 1, "color": 16777215},
            215: {"character_choices": json.dumps(character_ids), "characters_json_path": characters_json, "character_root": characters_dir},
            216: {"panel_count": 4},
            266: {
                "prompt_items": json.dumps(script, ensure_ascii=False), "panel_images": ["9060", 0],
                "panel_masks": ["9061", 0], "character_images": ["216", 0], "character_position_notes": ["216", 1],
            },
            218: {"text_contents": ["266", 5]},
            106: {"image1": ["266", 2], "image2": ["218", 0], "prompt": ["266", 0]},
            105: {"image1": ["266", 2], "image2": ["218", 0], "prompt": ["266", 1]},
            110: {"image": ["266", 3]}, 117: {"mask": ["266", 4]},
            115: {"pixels": ["110", 0], "vae": ["103", 0]},
            118: {"samples": ["115", 0], "mask": ["117", 0]},
            127: {"model": ["128", 0], "positive": ["106", 0], "negative": ["105", 0], "latent_image": ["118", 0], "seed": seed},
            129: {"samples": ["127", 0], "vae": ["103", 0]},
            302: {"image": ["129", 0], "panel_prompt_json": ["266", 8], "page_index": ["266", 6], "panel_index": ["266", 7]},
            290: {"generated_panel_images": ["302", 0], "base_images": ["97", 0], "panel_masks": ["81", 0], "panel_bboxes": ["80", 0], "page_indices": ["266", 6], "panel_indices": ["266", 7]},
        })
        prompt["9060"] = self._synthetic("ImpactMakeImageList", image1=["85", 0])
        prompt["9061"] = self._synthetic("ImpactMakeMaskList", mask1=["81", 0])
        prompt["9101"] = self._synthetic("SaveImage", images=["302", 0], filename_prefix=f"{prefix}/panels")
        prompt["9102"] = self._synthetic("MaskToImage", mask=["82", 0])
        prompt["9103"] = self._synthetic("SaveImage", images=["9102", 0], filename_prefix=f"{prefix}/masks")
        prompt["9104"] = self._synthetic("SaveImage", images=["97", 0], filename_prefix=f"{prefix}/base")
        prompt["9105"] = self._synthetic("SaveImage", images=["287", 0], filename_prefix=f"{prefix}/final")
        return prompt, {"panels": 9101, "masks": 9103, "base": 9104, "final": 9105}

    def regenerate_panel(self, panel: dict[str, Any], page_index: int, character_ids: list[int], layout_name: str,
                         characters_json: str, characters_dir: str, layouts_dir: str,
                         prefix: str) -> tuple[dict[str, Any], dict[str, int]]:
        ids = [214, 86, 91, 94, 93, 97, 80, 82, 81, 85, 215, 216, 218,
               100, 101, 103, 104, 106, 105, 110, 115, 117, 118, 120, 128, 127, 129, 302]
        selected = 9200
        panel_prompt = panel.get("prompt_json") or {
            "page_index": page_index,
            "panel_index": panel["panel_index"],
            "序號": panel["panel_index"],
            "提示詞內容": {
                "描述": panel["positive_prompt"],
                "負向提示詞": panel["negative_prompt"],
            },
            "文字內容": panel["text_content"],
        }
        prompt = self._prompt(ids, {
            214: {"layout_name": layout_name, "layout_folder": layouts_dir},
            # This prompt generates exactly one panel. Keep the backing canvas
            # at batch 1; the UI workflow's linked widgets are otherwise
            # decoded as batch_size=512 and color=512.
            97: {"batch_size": 1, "color": 0},
            80: {"polygon": [str(selected), 0]}, 82: {"polygon": [str(selected), 0], "image": ["97", 0]},
            81: {"mask": ["82", 0], "bbox": ["82", 1]}, 85: {"image": ["97", 0], "bbox": ["80", 0]},
            215: {"character_choices": json.dumps(character_ids), "characters_json_path": characters_json, "character_root": characters_dir},
            216: {"mode": "keep", "panel_count": 1},
            218: {"text_contents": panel["text_content"]},
            106: {"image1": ["216", 0], "image2": ["218", 0], "prompt": panel["positive_prompt"]},
            105: {"image1": ["216", 0], "image2": ["218", 0], "prompt": panel["negative_prompt"]},
            110: {"image": ["85", 0]}, 117: {"mask": ["81", 0]},
            115: {"pixels": ["110", 0], "vae": ["103", 0]}, 118: {"samples": ["115", 0], "mask": ["117", 0]},
            127: {"model": ["128", 0], "positive": ["106", 0], "negative": ["105", 0], "latent_image": ["118", 0], "seed": panel["seed"]},
            129: {"samples": ["127", 0], "vae": ["103", 0]},
            302: {
                "image": ["129", 0],
                "panel_prompt_json": json.dumps(panel_prompt, ensure_ascii=False),
                "page_index": page_index,
                "panel_index": panel["panel_index"],
            },
        })
        prompt[str(selected)] = self._synthetic("ImpactSelectNthItemOfAnyList", any_list=["86", 0], index=panel["panel_index"] - 1)
        prompt["9210"] = self._synthetic("SaveImage", images=["302", 0], filename_prefix=f"{prefix}/panel_{panel['panel_index']}")
        prompt["9211"] = self._synthetic("MaskToImage", mask=["82", 0])
        prompt["9212"] = self._synthetic("SaveImage", images=["9211", 0], filename_prefix=f"{prefix}/mask_{panel['panel_index']}")
        prompt["9213"] = self._synthetic("SaveImage", images=["216", 0], filename_prefix=f"{prefix}/debug_character_combination")
        prompt["9214"] = self._synthetic("SaveImage", images=["218", 0], filename_prefix=f"{prefix}/debug_text_reference")
        prompt["9215"] = self._synthetic("SaveImage", images=["129", 0], filename_prefix=f"{prefix}/debug_raw_panel")
        return prompt, {"panel": 9210, "mask": 9212, "character_combination": 9213, "text_reference": 9214, "raw_panel": 9215}

    def compose_page(self, panel_images: list[str | None], page_index: int, layout_name: str,
                     layouts_dir: str, prefix: str) -> tuple[dict[str, Any], int]:
        if len(panel_images) != 4:
            raise ValueError("Stage 8 整頁合成固定需要四格圖片")
        ids = [214, 86, 91, 94, 93, 97, 80, 82, 81, 290, 287]
        prompt = self._prompt(ids, {
            214: {"layout_name": layout_name, "layout_folder": layouts_dir},
            97: {"batch_size": 1, "color": 16777215},
        })
        image_links = {}
        prompt["9390"] = self._synthetic("EmptyImage", width=1024, height=1024, batch_size=1, color=16777215)
        for offset, image_name in enumerate(panel_images, start=1):
            node_id = str(9300 + offset)
            if image_name:
                prompt[node_id] = self._synthetic("LoadImage", image=image_name)
                image_links[f"image{offset}"] = [node_id, 0]
            else:
                image_links[f"image{offset}"] = ["9390", 0]
        prompt["9350"] = self._synthetic("ImpactMakeImageList", **image_links)

        page_links = {}
        panel_links = {}
        for offset in range(1, 5):
            page_node = str(9360 + offset)
            panel_node = str(9370 + offset)
            prompt[page_node] = self._synthetic("PrimitiveInt", value=page_index)
            prompt[panel_node] = self._synthetic("PrimitiveInt", value=offset)
            page_links[f"value{offset}"] = [page_node, 0]
            panel_links[f"value{offset}"] = [panel_node, 0]
        prompt["9380"] = self._synthetic("ImpactMakeAnyList", **page_links)
        prompt["9381"] = self._synthetic("ImpactMakeAnyList", **panel_links)
        prompt["290"]["inputs"].update({
            "generated_panel_images": ["9350", 0],
            "base_images": ["97", 0],
            # Match the original Stage 8 SetNode sources exactly: MASK comes
            # from the bbox-cropped node 81, while BBOX comes from node 80.
            # Feeding node 82's full-page mask here scales the whole canvas
            # into every bbox and creates the enlarged black-frame artifact.
            "panel_masks": ["81", 0],
            "panel_bboxes": ["80", 0],
            "page_indices": ["9380", 0],
            "panel_indices": ["9381", 0],
        })
        prompt["9399"] = self._synthetic("SaveImage", images=["287", 0], filename_prefix=prefix)
        return prompt, 9399

    def layout_geometry(self, layout_name: str, layouts_dir: str, prefix: str) -> tuple[dict[str, Any], dict[str, list[int]]]:
        """Export the exact masks and bboxes produced by Stage 8's panel nodes."""
        prompt = self._prompt(
            [214, 86, 91, 94, 93, 97],
            {
                214: {"layout_name": layout_name, "layout_folder": layouts_dir},
                97: {"batch_size": 1, "color": 16777215},
            },
        )
        mask_outputs: list[int] = []
        bbox_outputs: list[int] = []
        for panel_index in range(1, 5):
            selected = 9500 + panel_index
            bounds = 9510 + panel_index
            resized_mask = 9520 + panel_index
            cropped_mask = 9530 + panel_index
            mask_image = 9540 + panel_index
            saved_mask = 9550 + panel_index
            preview_bbox = 9560 + panel_index
            prompt[str(selected)] = self._synthetic(
                "ImpactSelectNthItemOfAnyList", any_list=["86", 0], index=panel_index - 1,
            )
            prompt[str(bounds)] = self._synthetic("bmad_PolygonBounds", polygon=[str(selected), 0])
            prompt[str(resized_mask)] = self._synthetic(
                "bmad_PolygonToResizedMask", polygon=[str(selected), 0], image=["97", 0],
                approx_res="1048576", pad="64",
            )
            prompt[str(cropped_mask)] = self._synthetic(
                "bmad_CropMaskByBBox", mask=[str(resized_mask), 0], bbox=[str(resized_mask), 1],
            )
            prompt[str(mask_image)] = self._synthetic("MaskToImage", mask=[str(cropped_mask), 0])
            prompt[str(saved_mask)] = self._synthetic(
                "SaveImage", images=[str(mask_image), 0], filename_prefix=f"{prefix}/panel_{panel_index}",
            )
            prompt[str(preview_bbox)] = self._synthetic("PreviewAny", source=[str(bounds), 0])
            mask_outputs.append(saved_mask)
            bbox_outputs.append(preview_bbox)
        return prompt, {"masks": mask_outputs, "bboxes": bbox_outputs}
