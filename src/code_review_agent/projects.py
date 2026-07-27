from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from code_review_agent.config import PROJECT_ROOT

PhpMethodStyle = Literal["snake_case", "camelCase"]
PhpVariableStyle = Literal["snake_case", "camelCase", "either"]


class StandardsByLang(BaseModel):
    php: list[Path] = Field(default_factory=list)
    python: list[Path] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    id: str
    display_name: str
    repo: Path
    standards: StandardsByLang = Field(default_factory=StandardsByLang)
    php_method_style: PhpMethodStyle = "camelCase"
    php_variable_style: PhpVariableStyle = "camelCase"
    default_branch: str | None = None

    @field_validator("standards", mode="before")
    @classmethod
    def _coerce_standards(cls, value: Any) -> Any:
        """兼容旧写法 standards: [path] → 视为 php。"""
        if value is None:
            return {"php": [], "python": []}
        if isinstance(value, list):
            return {"php": value, "python": []}
        return value

    def resolve_lang_texts(self, lang: Literal["php", "python"]) -> list[str]:
        paths = self.standards.php if lang == "php" else self.standards.python
        texts: list[str] = []
        for path in paths:
            p = path.expanduser()
            if not p.is_file():
                texts.append(f"# Missing standard: {p}\n(文件不存在，请检查 projects.yaml)")
                continue
            body = p.read_text(encoding="utf-8").strip()
            texts.append(f"# Source: {p}\n{body}")
        return texts

    @property
    def has_php_standards(self) -> bool:
        return bool(self.standards.php)

    @property
    def has_python_standards(self) -> bool:
        return bool(self.standards.python)


@lru_cache
def _raw_projects() -> dict[str, ProjectConfig]:
    path = PROJECT_ROOT / "projects.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result: dict[str, ProjectConfig] = {}
    for pid, cfg in (data.get("projects") or {}).items():
        result[pid] = ProjectConfig(id=pid, **cfg)
    return result


def list_projects() -> list[ProjectConfig]:
    return list(_raw_projects().values())


def get_project(project_id: str) -> ProjectConfig:
    projects = _raw_projects()
    if project_id not in projects:
        known = ", ".join(sorted(projects)) or "(无)"
        raise KeyError(f"未知项目 id: {project_id}；已登记: {known}")
    return projects[project_id]


def reload_projects() -> None:
    _raw_projects.cache_clear()
