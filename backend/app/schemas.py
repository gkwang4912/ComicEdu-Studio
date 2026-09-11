from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ProjectCreate(BaseModel):
    title: str = Field(default="", max_length=120)
    subject: str = Field(default="自然科學", max_length=80)
    grade_level: str = Field(default="國小高年級", max_length=80)
    topic: str = Field(default="", max_length=500)
    page_count: int = Field(default=3, ge=2, le=6)
    gradeLevel: str = Field(default="國小高年級", max_length=80)
    teachingObjective: str = Field(default="", max_length=500)
    storyStyle: str = Field(default="", max_length=120)


class MaterialText(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    name: str = Field(default="教材主題.txt", max_length=180)


class PlanSelection(BaseModel):
    plan_index: int = Field(ge=1, le=3)
    page_count: int = Field(ge=2, le=6)


class StoryArcUpdate(BaseModel):
    story_arc: dict[str, Any]

    @field_validator("story_arc")
    @classmethod
    def validate_pages(cls, value: dict[str, Any]) -> dict[str, Any]:
        pages = value.get("pages")
        if not isinstance(pages, list) or not 2 <= len(pages) <= 6:
            raise ValueError("story_arc.pages 必須包含 2-6 頁")
        for page in pages:
            knowledge = page.get("panel_knowledge", [])
            if not isinstance(knowledge, list) or len(knowledge) != 4:
                raise ValueError("每頁 panel_knowledge 必須剛好四項")
        return value


class RevisionRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


class ScriptUpdate(BaseModel):
    script: list[dict[str, Any]]

    @field_validator("script")
    @classmethod
    def four_panels(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) != 4:
            raise ValueError("每頁劇本必須剛好四格")
        return value


class CharacterSelection(BaseModel):
    character_ids: list[int] = Field(min_length=2, max_length=2)


class LayoutSelection(BaseModel):
    layout_name: str = Field(pattern=r"^layout_\d{2}\.png$")


class PanelUpdate(BaseModel):
    positive_prompt: str | None = Field(default=None, max_length=20_000)
    negative_prompt: str | None = Field(default=None, max_length=10_000)
    text_content: str | None = Field(default=None, max_length=1000)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)


class RegenerateRequest(BaseModel):
    seed_mode: Literal["original", "random"] = "original"
