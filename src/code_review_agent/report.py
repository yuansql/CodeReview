from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from code_review_agent.git_ops import show_file_at
from code_review_agent.models import Finding, Severity
from code_review_agent.rules.standards_loader import standards_label


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", text).strip("_") or "unknown"


def render_report(
    *,
    project_id: str | None,
    repo_path: str,
    base_ref: str,
    head_ref: str,
    findings: list[Finding],
    skipped_files: list[str],
    standards_python: list[str],
    standards_php: list[str],
    commits: list[str],
    standards_note: str | None = None,
    context_before: int = 2,
    context_after: int = 2,
) -> str:
    fatals = [f for f in findings if f.severity == Severity.FATAL]
    warnings = [f for f in findings if f.severity == Severity.WARNING]
    slow_sql = [f for f in warnings if f.category == "SLOW_SQL"]
    slow_code = [f for f in warnings if f.category == "SLOW_CODE"]
    naming = [f for f in warnings if f.category == "NAMING"]
    other_warn = [
        f
        for f in warnings
        if f.category not in {"SLOW_SQL", "SLOW_CODE", "NAMING"}
    ]

    if fatals:
        verdict = '<span class="sev-fatal">**存在致命错误（影响运行）**</span>'
    elif warnings:
        verdict = '<span class="sev-warn">**仅警告（可通过，建议修复）**</span>'
    else:
        verdict = "**通过**"

    snippet_cache = _SnippetCache(
        repo_path=repo_path,
        head_ref=head_ref,
        before=context_before,
        after=context_after,
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        "# 代码审核报告",
        "",
        f"- 项目：`{project_id or '(未登记)'}`",
        f"- 仓库：`{repo_path}`",
        f"- 分支：`{head_ref}` → `{base_ref}`",
        f"- 时间：{now}",
        f"- 结论：{verdict}",
        "",
        "## 摘要",
        f'- <span class="sev-fatal">致命（深红，影响运行）：{len(fatals)}</span>',
        f'- <span class="sev-slow">SLOW_SQL（淡红）：{len(slow_sql)}</span>',
        f'- <span class="sev-warn">SLOW_CODE：{len(slow_code)}</span>',
        f'- <span class="sev-warn">NAMING：{len(naming)}</span>',
        f'- <span class="sev-warn">其他警告：{len(other_warn)}</span>',
        f'- <span class="sev-warn">警告合计：{len(warnings)}</span>',
        f"- 未审文件：{len(skipped_files)}（非 php/py）",
        "- 致命：语法错误、括号不匹配等会影响运行的问题",
        "- 命名仅类/方法明显违规；说得过去的不报。慢SQL/慢代码与命名只看 **新增行**；语法/括号查整文件",
        "- 问题代码取自待合并分支 tip，命中行前有 `>` 标记",
        "",
    ]

    if commits:
        lines.append("## 提交")
        for c in commits[:50]:
            lines.append(f"- `{c}`")
        lines.append("")

    lines.append('## <span class="sev-fatal">致命错误</span>')
    if not fatals:
        lines.append("_无_")
    else:
        for f in fatals:
            lines.extend(_finding_block(f, snippet_cache.for_finding(f)))
    lines.append("")

    other_warnings = [f for f in warnings if f.category != "SLOW_SQL"]
    lines.append('## <span class="sev-slow">SLOW_SQL</span>')
    if not slow_sql:
        lines.append("_无_")
    else:
        for f in slow_sql:
            lines.extend(_finding_block(f, snippet_cache.for_finding(f)))
    lines.append("")

    lines.append('## <span class="sev-warn">警告</span>')
    if not other_warnings:
        lines.append("_无_")
    else:
        for f in other_warnings:
            lines.extend(_finding_block(f, snippet_cache.for_finding(f)))
    lines.append("")

    if skipped_files:
        lines.append("## 未审文件")
        for p in skipped_files:
            lines.append(f"- `{p}`")
        lines.append("")

    lines.extend(
        [
            "## 规范来源",
            f"- {standards_label(standards_python, 'Python', naming_enabled=bool(standards_python))}",
            f"- {standards_label(standards_php, 'PHP', naming_enabled=bool(standards_php))}",
        ]
    )
    if standards_note:
        lines.append(f"- 说明：{standards_note}")
    lines.append("")
    return "\n".join(lines)


class _SnippetCache:
    def __init__(
        self,
        *,
        repo_path: str,
        head_ref: str,
        before: int = 2,
        after: int = 2,
    ) -> None:
        self.repo = Path(repo_path) if repo_path else None
        self.head_ref = head_ref
        self.before = before
        self.after = after
        self._files: dict[str, list[str] | None] = {}

    def _lines_for(self, rel_path: str) -> list[str] | None:
        if rel_path in self._files:
            return self._files[rel_path]
        if not self.repo or not self.repo.is_dir():
            self._files[rel_path] = None
            return None
        candidates = [rel_path, rel_path.lstrip("./")]
        source = None
        for cand in candidates:
            source = show_file_at(self.repo, self.head_ref, cand)
            if source is not None:
                break
        self._files[rel_path] = source.splitlines() if source is not None else None
        return self._files[rel_path]

    def for_finding(self, f: Finding) -> str | None:
        if f.line is None or not f.file or f.file.startswith("("):
            return None
        source_lines = self._lines_for(f.file)
        if not source_lines:
            return None
        return _format_snippet(
            source_lines,
            f.line,
            before=self.before,
            after=self.after,
        )


def _format_snippet(
    source_lines: list[str],
    hit_line: int,
    *,
    before: int = 2,
    after: int = 2,
) -> str | None:
    if hit_line < 1 or hit_line > len(source_lines):
        return None
    start = max(1, hit_line - before)
    end = min(len(source_lines), hit_line + after)
    rows: list[str] = []
    width = len(str(end))
    for i in range(start, end + 1):
        mark = ">" if i == hit_line else " "
        rows.append(f"{mark} {i:>{width}} | {source_lines[i - 1].rstrip()}")
    return "\n".join(rows)


def _fence_lang(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".php"):
        return "php"
    if lower.endswith((".py", ".pyi")):
        return "python"
    if lower.endswith(".sql"):
        return "sql"
    return ""


def _finding_block(f: Finding, snippet: str | None = None) -> list[str]:
    loc = f"{f.file}:{f.line}" if f.line else f.file
    if f.severity == Severity.FATAL:
        badge = '<span class="sev-fatal">致命</span>'
        title = f'### <span class="sev-fatal">[{f.category}]</span> `{loc}`'
    elif f.category == "SLOW_SQL":
        badge = '<span class="sev-slow">警告 · SLOW_SQL</span>'
        title = f'### <span class="sev-slow">[{f.category}]</span> `{loc}`'
    else:
        badge = '<span class="sev-warn">警告</span>'
        title = f'### <span class="sev-warn">[{f.category}]</span> `{loc}`'
    block = [
        title,
        f"- 级别：{badge}",
        f"- 问题：{f.message}",
        f"- 依据：{f.rule_source}",
    ]
    if f.suggestion:
        block.append(f"- 建议：{f.suggestion}")
    if snippet:
        lang = _fence_lang(f.file)
        block.append("- 代码：")
        block.append("")
        block.append(f"```{lang}".rstrip())
        block.append(snippet)
        block.append("```")
    block.append("")
    return block


def write_report(
    reports_dir: Path,
    content: str,
    *,
    project_id: str | None,
    head_ref: str,
    commit_short: str | None,
) -> Path:
    """reports/{project}/{YYYY-MM-DD}/{branch}_{commit}_{HHMMSS}.md"""
    pid = _safe_name(project_id or "unregistered")
    day = datetime.now().strftime("%Y-%m-%d")
    time_part = datetime.now().strftime("%H%M%S")
    branch = _safe_name(head_ref)
    commit = _safe_name(commit_short or "nocommit")
    folder = reports_dir / pid / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{branch}_{commit}_{time_part}.md"
    path.write_text(content, encoding="utf-8")
    return path
