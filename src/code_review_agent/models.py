from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict

from pydantic import BaseModel, Field


class Severity(str, Enum):
    FATAL = "fatal"
    WARNING = "warning"


class Finding(BaseModel):
    severity: Severity
    category: str  # SYNTAX | NAMING | STYLE | LLM
    file: str
    line: int | None = None
    message: str
    suggestion: str | None = None
    rule_source: str = "builtin"


class ReviewState(TypedDict, total=False):
    project_id: str
    project_display_name: str
    repo_path: str
    base_ref: str
    head_ref: str
    diff_text: str
    commits: list[str]
    changed_files: list[str]
    php_files: list[str]
    python_files: list[str]
    skipped_files: list[str]
    standards_python: list[str]
    standards_php: list[str]
    standards_note: str
    naming_php_enabled: bool
    naming_python_enabled: bool
    php_method_style: str
    php_variable_style: str
    changed_lines: dict[str, list[int]]  # 文件 → 本次新增行号
    findings: list[dict[str, Any]]
    report_path: str
    summary: dict[str, Any]
    error: str


class LlmFindingItem(BaseModel):
    severity: Severity = Severity.WARNING
    category: str = "LLM"
    file: str
    line: int | None = None
    message: str
    suggestion: str | None = None


class LlmFindingsPayload(BaseModel):
    findings: list[LlmFindingItem] = Field(default_factory=list)
