from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from openai import OpenAI

from code_review_agent.config import Settings, get_settings
from code_review_agent.git_ops import (
    GitError,
    changed_files,
    commit_list,
    parse_added_entries,
    parse_added_hunks,
    parse_added_lines,
    resolve_ref,
    show_file_at,
    three_dot_diff,
)
from code_review_agent.models import Finding, LlmFindingsPayload, ReviewState, Severity
from code_review_agent.report import render_report, write_report
from code_review_agent.rules.naming import (
    check_bracket_balance,
    check_php_naming_on_added_lines,
    check_php_syntax,
    check_python_naming_on_added_lines,
    check_python_syntax,
)
from code_review_agent.rules.slow_checks import check_slow_on_diff
from code_review_agent.rules.standards_loader import load_markdown_standards

_IDENT = re.compile(r"[`'\"]([A-Za-z_][A-Za-z0-9_]*)[`'\"]")
_RENAME_TO = re.compile(
    r"(?:重命名为|改为|改成|rename(?:d)?\s+to)\s*[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?",
    re.I,
)
_SNAKE_OK = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_CAMEL_OK = re.compile(r"^[a-z][a-zA-Z0-9]*$")


def _findings_from_state(state: ReviewState) -> list[Finding]:
    return [Finding.model_validate(item) for item in state.get("findings", [])]


def _append_findings(state: ReviewState, new_items: list[Finding]) -> list[dict]:
    merged = _findings_from_state(state) + new_items
    return [f.model_dump() for f in merged]


def _filter_findings_to_changed_lines(
    findings: list[Finding],
    changed_lines: dict[str, list[int]],
) -> list[Finding]:
    """命名/风格类问题只保留落在本次新增行上的。"""
    if not changed_lines:
        return []
    allowed = {path.replace("\\", "/"): set(lines) for path, lines in changed_lines.items()}

    def _lines_for(file_path: str) -> set[int] | None:
        key = file_path.replace("\\", "/")
        if key in allowed:
            return allowed[key]
        for path, lines in allowed.items():
            if key.endswith(path) or path.endswith(key):
                return lines
        return None

    kept: list[Finding] = []
    for finding in findings:
        if finding.line is None:
            continue
        lines = _lines_for(finding.file)
        if lines is not None and finding.line in lines:
            kept.append(finding)
    return kept


_PASCAL_OK = re.compile(r"^[A-Z][A-Za-z0-9]*$")


def _is_noise_llm_naming(
    finding: Finding,
    *,
    method_style: str,
) -> bool:
    """丢掉 LLM 碎碎念：变量、说得过去的标识符、只加下划线等。"""
    if finding.category not in {"NAMING", "STYLE", "LLM"}:
        return False

    text = f"{finding.message} {finding.suggestion or ''}"
    # 变量 / 短名 / 语义挑剔 → 一律静音
    if any(
        k in text
        for k in (
            "变量",
            "variable",
            "过短",
            "语义不明",
            "局部变量",
            "语义改进",
            "更有意义",
            "可读性更好",
        )
    ):
        return True

    idents = _IDENT.findall(text)
    rename = _RENAME_TO.search(text)
    new_name = rename.group(1) if rename else None
    old_name = idents[0] if idents else None

    if old_name and new_name:
        # map_sound_name → map_sound_name_ / _map_sound_name
        if old_name.strip("_").lower() == new_name.strip("_").lower():
            return True
        if new_name in {old_name + "_", "_" + old_name, old_name + "__"}:
            return True
        # 新旧都是合法 snake/camel/Pascal → 说得过去，不报
        def _ok(n: str) -> bool:
            return bool(_SNAKE_OK.match(n) or _CAMEL_OK.match(n) or _PASCAL_OK.match(n))

        if _ok(old_name) and _ok(new_name):
            # 仅风格偏好（snake↔camel）且非「明显违规」项目硬规则时静音
            if method_style == "snake_case" and _SNAKE_OK.match(old_name):
                return True
            if method_style == "camelCase" and _CAMEL_OK.match(old_name):
                return True
            if _PASCAL_OK.match(old_name):
                return True

    # 已符合当前风格，却说「不符合 xxx」
    style_hit = None
    if "snake_case" in text.lower():
        style_hit = "snake_case"
    elif "camelcase" in text.lower() or "camelCase" in text:
        style_hit = "camelCase"

    if style_hit and "不符合" in text and old_name:
        if style_hit == "snake_case" and _SNAKE_OK.match(old_name):
            return True
        if style_hit == "camelCase" and _CAMEL_OK.match(old_name):
            return True
        if method_style == "snake_case" and _SNAKE_OK.match(old_name):
            return True
        if method_style == "camelCase" and _CAMEL_OK.match(old_name):
            return True

    # 标识符本身已合法 → 说得过去，丢掉纯品味建议
    if old_name and (
        _SNAKE_OK.match(old_name) or _CAMEL_OK.match(old_name) or _PASCAL_OK.match(old_name)
    ):
        if method_style == "snake_case" and _SNAKE_OK.match(old_name):
            return True
        if method_style == "camelCase" and (
            _CAMEL_OK.match(old_name) or _PASCAL_OK.match(old_name)
        ):
            return True
        if method_style == "snake_case" and _PASCAL_OK.match(old_name):
            return True  # 类名 Pascal 正常

    return False


def _filter_noise_llm_findings(
    findings: list[Finding],
    *,
    method_style: str,
) -> list[Finding]:
    return [
        f
        for f in findings
        if not _is_noise_llm_naming(f, method_style=method_style)
    ]


def _estimate_tokens(text: str) -> int:
    """偏保守估算（中英混排约 2 字符 ≈ 1 token）。"""
    return max(1, (len(text) + 1) // 2)


def _model_context_tokens(model: str) -> int:
    name = (model or "").lower()
    if "128k" in name:
        return 128_000
    if "32k" in name:
        return 32_000
    if "8k" in name:
        return 8_192
    return 8_192


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max(0, max_tokens * 2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n... [truncated] ..."


def _format_added_block(path: str, entries: list[tuple[int, str]]) -> str:
    lines = [f"### `{path}`"]
    for ln, text in entries:
        lines.append(f"+{ln}|{text}")
    return "\n".join(lines)


def _split_entries_to_blocks(
    path: str,
    entries: list[tuple[int, str]],
    *,
    budget_tokens: int,
) -> list[str]:
    """单文件过大时按行切开，保证每块估算不超过预算。"""
    if not entries:
        return []
    budget = max(200, budget_tokens)
    blocks: list[str] = []
    start = 0
    while start < len(entries):
        end = start + 1
        while end < len(entries):
            candidate = _format_added_block(path, entries[start : end + 1])
            if _estimate_tokens(candidate) > budget:
                break
            end += 1
        block = _format_added_block(path, entries[start:end])
        if _estimate_tokens(block) > budget:
            block = _truncate_to_tokens(block, budget)
        blocks.append(block)
        start = end
    return blocks


def _chunk_added_for_llm(
    added: dict[str, list[tuple[int, str]]],
    *,
    budget_tokens: int,
) -> list[str]:
    """先预算再分段：能一包就一包，超了按文件/行切。"""
    budget = max(200, budget_tokens)
    file_blocks: list[str] = []
    for path, entries in sorted(added.items()):
        whole = _format_added_block(path, entries)
        if _estimate_tokens(whole) <= budget:
            file_blocks.append(whole)
        else:
            file_blocks.extend(
                _split_entries_to_blocks(path, entries, budget_tokens=budget)
            )

    chunks: list[str] = []
    current: list[str] = []
    current_tok = 0
    for block in file_blocks:
        tok = _estimate_tokens(block)
        if current and current_tok + tok > budget:
            chunks.append("\n\n".join(current))
            current = []
            current_tok = 0
        current.append(block)
        current_tok += tok
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.severity, f.category, f.file, f.line, f.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def load_config(state: ReviewState) -> ReviewState:
    settings = get_settings()
    project_id = state.get("project_id")
    standards_php: list[str] = []
    standards_python: list[str] = []
    standards_note = ""
    php_method_style = "camelCase"
    php_variable_style = "camelCase"
    display_name = ""
    naming_php_enabled = False
    naming_python_enabled = False

    if project_id:
        from code_review_agent.projects import get_project

        try:
            project = get_project(project_id)
        except KeyError as exc:
            return {**state, "error": str(exc), "findings": state.get("findings") or []}

        repo = project.repo.expanduser().resolve()
        standards_php = project.resolve_lang_texts("php")
        standards_python = project.resolve_lang_texts("python")
        naming_php_enabled = project.has_php_standards
        naming_python_enabled = project.has_python_standards
        php_method_style = project.php_method_style
        php_variable_style = project.php_variable_style
        display_name = project.display_name
        parts = []
        if naming_php_enabled:
            parts.append("PHP 命名按绑定 .md")
        else:
            parts.append("PHP 未配 .md→跳过命名")
        if naming_python_enabled:
            parts.append("Python 命名按绑定 .md")
        else:
            parts.append("Python 未配 .md→跳过命名")
        standards_note = (
            f"项目 `{project_id}`：" + "；".join(parts)
            + "。不会混用其他项目规范。命名/风格仅审本次 diff 新增行。"
        )
    else:
        repo = Path(state.get("repo_path") or settings.target_repo or Path.cwd()).resolve()
        standards_python = load_markdown_standards(settings.standards_python_dir)
        standards_php = load_markdown_standards(settings.standards_php_dir)
        naming_python_enabled = bool(standards_python)
        naming_php_enabled = bool(standards_php)
        standards_note = (
            "未指定 --project：仅使用全局 standards/python|php；"
            "某语言目录为空则跳过该语言命名检查。"
            "命名/风格仅审本次 diff 新增行。"
        )

    return {
        **state,
        "project_id": project_id or "",
        "project_display_name": display_name,
        "repo_path": str(repo),
        "standards_python": standards_python,
        "standards_php": standards_php,
        "standards_note": standards_note,
        "naming_php_enabled": naming_php_enabled,
        "naming_python_enabled": naming_python_enabled,
        "php_method_style": php_method_style,
        "php_variable_style": php_variable_style,
        "findings": state.get("findings") or [],
    }


def get_diff(state: ReviewState) -> ReviewState:
    repo = Path(state["repo_path"])
    base = state.get("base_ref") or "main"
    head = state.get("head_ref") or "HEAD"
    try:
        resolve_ref(repo, base)
        resolve_ref(repo, head)
        diff = three_dot_diff(repo, base, head)
        files = changed_files(repo, base, head)
        commits = commit_list(repo, base, head)
    except GitError as exc:
        return {**state, "error": str(exc)}

    php_files = [f for f in files if f.endswith(".php")]
    python_files = [f for f in files if f.endswith(".py")]
    reviewed = set(php_files) | set(python_files)
    skipped = [f for f in files if f not in reviewed]
    added_lines = parse_added_lines(diff)

    return {
        **state,
        "base_ref": base,
        "head_ref": head,
        "diff_text": diff,
        "changed_files": files,
        "changed_lines": added_lines,
        "php_files": php_files,
        "python_files": python_files,
        "skipped_files": skipped,
        "commits": commits,
    }


def check_syntax(state: ReviewState) -> ReviewState:
    if state.get("error"):
        return state
    repo = Path(state["repo_path"])
    head = state["head_ref"]
    php_bin = shutil.which("php")
    new_findings: list[Finding] = []

    for rel in state.get("python_files", []):
        source = show_file_at(repo, head, rel)
        if source is None:
            continue
        new_findings.extend(check_python_syntax(Path(rel), source))
        new_findings.extend(check_bracket_balance(Path(rel), source))

    for rel in state.get("php_files", []):
        source = show_file_at(repo, head, rel)
        if source is None:
            continue
        # 每个文件只提示一次「无 php」：首次带 warning，后续若无 php_bin 仍跑 fallback 会重复
        # 简化：全部调用；报告里可能重复无 php 警告。改为只对第一个文件发 fallback。
        findings = check_php_syntax(Path(rel), source, php_bin)
        if not php_bin:
            # 去重：无 php 时只保留真正内容相关；统一追加一条全局警告在命名阶段前
            findings = [f for f in findings if "未找到 `php`" not in f.message and "未检测到 PHP" not in f.message]
        new_findings.extend(findings)
        new_findings.extend(check_bracket_balance(Path(rel), source))

    if state.get("php_files") and not php_bin:
        new_findings.append(
            Finding(
                severity=Severity.WARNING,
                category="SYNTAX",
                file="(php toolchain)",
                line=None,
                message="本机未找到 `php`，已跳过 `php -l` 语法检查",
                suggestion="安装 PHP CLI 后重跑以启用致命级语法检测",
                rule_source="builtin:fallback",
            )
        )

    return {**state, "findings": _append_findings(state, new_findings)}


def check_naming(state: ReviewState) -> ReviewState:
    if state.get("error"):
        return state
    new_findings: list[Finding] = []
    method_style = state.get("php_method_style") or "camelCase"
    variable_style = state.get("php_variable_style") or "camelCase"
    rule_source = (
        f"project:{state.get('project_id')}"
        if state.get("project_id")
        else "builtin"
    )
    diff = state.get("diff_text") or ""
    if not diff.strip():
        return state

    # 关键命名：只看新增行文本，避免报出分支未改的旧方法
    added = parse_added_entries(diff)

    if state.get("naming_python_enabled"):
        for rel, entries in added.items():
            if not rel.endswith(".py"):
                continue
            new_findings.extend(check_python_naming_on_added_lines(Path(rel), entries))

    if state.get("naming_php_enabled"):
        for rel, entries in added.items():
            if not rel.endswith(".php"):
                continue
            new_findings.extend(
                check_php_naming_on_added_lines(
                    Path(rel),
                    entries,
                    method_style=method_style,
                    variable_style=variable_style,
                    rule_source=rule_source,
                )
            )

    return {**state, "findings": _append_findings(state, new_findings)}


def check_perf(state: ReviewState) -> ReviewState:
    """慢 SQL / 慢代码：只扫本次 diff 新增行。"""
    if state.get("error"):
        return state
    diff = state.get("diff_text") or ""
    if not diff.strip():
        return state

    entries = parse_added_entries(diff)
    hunks = parse_added_hunks(diff)
    new_findings = check_slow_on_diff(
        diff_text=diff,
        added_entries=entries,
        hunks=hunks,
    )
    # 已基于新增行生成，再按行号过滤一次防跨文件误挂
    new_findings = _filter_findings_to_changed_lines(
        new_findings, state.get("changed_lines") or {}
    )
    return {**state, "findings": _append_findings(state, new_findings)}


def llm_enrich(state: ReviewState) -> ReviewState:
    if state.get("error"):
        return state

    settings = get_settings()
    if not settings.moonshot_api_key:
        return state

    diff = state.get("diff_text") or ""
    if not diff.strip():
        return {
            **state,
            "findings": _append_findings(
                state,
                [
                    Finding(
                        severity=Severity.WARNING,
                        category="STYLE",
                        file="(diff)",
                        message="相对 base 的 diff 为空，无可审改动",
                        suggestion="确认 --base / --head 是否正确",
                        rule_source="builtin",
                    )
                ],
            ),
        }

    naming_php = bool(state.get("naming_php_enabled"))
    naming_py = bool(state.get("naming_python_enabled"))
    if not naming_php and not naming_py:
        # 两边都没规范：跳过 LLM 命名补充，语法已在前面节点处理
        return state

    parts: list[str] = []
    if naming_php:
        parts.extend(state.get("standards_php") or [])
    if naming_py:
        parts.extend(state.get("standards_python") or [])
    standards_blob = "\n\n".join(parts)

    scope_bits = []
    if naming_php:
        scope_bits.append("PHP 命名按规范审")
    else:
        scope_bits.append("PHP 未配规范→不要报 PHP 命名问题")
    if naming_py:
        scope_bits.append("Python 命名按规范审")
    else:
        scope_bits.append("Python 未配规范→不要报 Python 命名问题")

    existing = _findings_from_state(state)
    existing_summary = "\n".join(
        f"- [{f.severity.value}] {f.category} {f.file}:{f.line} {f.message}"
        for f in existing[:30]
    ) or "(尚无规则引擎发现)"

    system = (
        "你是代码审核数字员工。只做命名规范与风格一致性补充；语法硬错误已由规则引擎处理。"
        "必须严格遵守下方「自定义规范」。"
        f"范围：{'；'.join(scope_bits)}。"
        "只报告本段给出的新增行（+行号|内容）上的问题；不要报告未出现的旧代码。"
        "finding.line 必须是新文件中的行号，且落在本段新增行上。"
        "命名硬规则："
        "1) 只报类名/方法名的明显违规；变量名、短名、说得过去的标识符一律不要报。"
        "2) 若标识符已符合当前方法风格，禁止报命名问题。"
        f"   当前 PHP 方法风格是 {state.get('php_method_style', 'camelCase')}："
        "snake_case 例：map_sound_name、get_list；camelCase 例：mapSoundName、getList。"
        "3) 禁止建议只增加前后下划线（如 map_sound_name → map_sound_name_ / _map_sound_name）。"
        "4) 不要做纯品味/语义润色建议；拿不准就返回空 findings。"
        "5) suggestion 必须给出基于原名转换后的真实新名（如 getAiInfo→get_ai_info），禁止写无关示例 get_list。"
        "6) severity=fatal 仅用于会影响运行的问题（语法错误、括号缺失等）；"
        "SLOW_SQL/命名问题一律用 warning，不要标 fatal。"
        "不要发散到安全/性能/业务需求。输出必须是 JSON："
        '{"findings":[{"severity":"warning|fatal","category":"NAMING|STYLE|SYNTAX|LLM",'
        '"file":"path","line":1,"message":"...","suggestion":"..."}]}'
        "若无新问题，返回 {\"findings\":[]}。severity 优先 warning。"
    )

    context_limit = _model_context_tokens(settings.moonshot_model)
    max_out_tokens = 1_200
    safety = 200
    # 规范单独封顶，避免把整份 md 塞爆
    standards_cap = min(2_500, max(400, (context_limit - max_out_tokens) // 5))
    standards_blob = _truncate_to_tokens(standards_blob, standards_cap)
    existing_summary = _truncate_to_tokens(existing_summary, 800)

    prefix = (
        f"## 自定义规范\n{standards_blob}\n\n"
        f"## 规则引擎已有发现（已限定新增行）\n{existing_summary}\n\n"
        f"## 本段新增代码（格式 +行号|内容；仅这些行可报）\n"
    )
    fixed_tokens = (
        _estimate_tokens(system)
        + _estimate_tokens(prefix)
        + max_out_tokens
        + safety
    )
    code_budget = context_limit - fixed_tokens
    if code_budget < 400:
        # 规范过大时再砍一刀，给代码留位
        standards_blob = _truncate_to_tokens(standards_blob, 600)
        prefix = (
            f"## 自定义规范\n{standards_blob}\n\n"
            f"## 规则引擎已有发现（已限定新增行）\n{existing_summary}\n\n"
            f"## 本段新增代码（格式 +行号|内容；仅这些行可报）\n"
        )
        fixed_tokens = (
            _estimate_tokens(system)
            + _estimate_tokens(prefix)
            + max_out_tokens
            + safety
        )
        code_budget = max(400, context_limit - fixed_tokens)

    added = parse_added_entries(diff)
    # 只审 php/py 新增（与命名开关对齐）
    filtered: dict[str, list[tuple[int, str]]] = {}
    for path, entries in added.items():
        lower = path.lower()
        if naming_php and lower.endswith(".php"):
            filtered[path] = entries
        elif naming_py and lower.endswith((".py", ".pyi")):
            filtered[path] = entries
    if not filtered:
        return state

    chunks = _chunk_added_for_llm(filtered, budget_tokens=code_budget)
    client = OpenAI(
        api_key=settings.moonshot_api_key,
        base_url=settings.moonshot_base_url,
    )
    all_new: list[Finding] = []
    errors: list[str] = []

    for idx, chunk in enumerate(chunks, start=1):
        user = (
            f"{prefix}{chunk}\n\n"
            f"（分段 {idx}/{len(chunks)}；"
            f"refs {state.get('base_ref')}...{state.get('head_ref')}）\n"
        )
        # 二次保险：整包仍超预算则再截代码段
        total_est = _estimate_tokens(system) + _estimate_tokens(user) + max_out_tokens
        if total_est > context_limit:
            overflow = total_est - context_limit + 50
            chunk = _truncate_to_tokens(chunk, max(200, _estimate_tokens(chunk) - overflow))
            user = (
                f"{prefix}{chunk}\n\n"
                f"（分段 {idx}/{len(chunks)}，已再截断；"
                f"refs {state.get('base_ref')}...{state.get('head_ref')}）\n"
            )
        try:
            completion = client.chat.completions.create(
                model=settings.moonshot_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=max_out_tokens,
            )
            raw = completion.choices[0].message.content or "{}"
            payload = _parse_llm_json(raw)
            batch = [
                Finding(
                    severity=item.severity,
                    category=item.category,
                    file=item.file,
                    line=item.line,
                    message=item.message,
                    suggestion=item.suggestion,
                    rule_source=f"llm:{settings.moonshot_model}",
                )
                for item in payload.findings
            ]
            all_new.extend(batch)
        except Exception as exc:  # noqa: BLE001 — 单段失败不阻断其它段
            errors.append(f"段{idx}/{len(chunks)}: {exc}")

    all_new = _filter_findings_to_changed_lines(
        all_new, state.get("changed_lines") or {}
    )
    all_new = _filter_noise_llm_findings(
        all_new,
        method_style=state.get("php_method_style") or "camelCase",
    )
    all_new = _dedupe_findings(all_new)

    if errors:
        hint = (
            "已按模型窗口预算分段；若仍失败可换 moonshot-v1-32k/128k，"
            "或检查 MOONSHOT_API_KEY / BASE_URL / MODEL"
        )
        if len(errors) == len(chunks):
            all_new.append(
                Finding(
                    severity=Severity.WARNING,
                    category="LLM",
                    file="(llm)",
                    message=f"Kimi 调用失败，已跳过 LLM 补充：{errors[0]}",
                    suggestion=hint,
                    rule_source="builtin",
                )
            )
        else:
            all_new.append(
                Finding(
                    severity=Severity.WARNING,
                    category="LLM",
                    file="(llm)",
                    message=(
                        f"Kimi 有 {len(errors)}/{len(chunks)} 段失败，"
                        f"其余段已合并：{errors[0]}"
                    ),
                    suggestion=hint,
                    rule_source="builtin",
                )
            )

    return {**state, "findings": _append_findings(state, all_new)}


def write_report_node(state: ReviewState) -> ReviewState:
    settings = get_settings()
    findings = _findings_from_state(state)

    if state.get("error"):
        findings = findings + [
            Finding(
                severity=Severity.FATAL,
                category="SYNTAX",
                file="(git)",
                message=state["error"],
                suggestion="确认仓库路径、项目 id 与 base/head 引用存在",
                rule_source="builtin",
            )
        ]

    commits = state.get("commits") or []
    commit_short = commits[0].split()[0] if commits else None

    content = render_report(
        project_id=state.get("project_id") or None,
        repo_path=state.get("repo_path", ""),
        base_ref=state.get("base_ref", "main"),
        head_ref=state.get("head_ref", "HEAD"),
        findings=findings,
        skipped_files=state.get("skipped_files") or [],
        standards_python=state.get("standards_python") or [],
        standards_php=state.get("standards_php") or [],
        commits=commits,
        standards_note=state.get("standards_note"),
    )
    path = write_report(
        settings.reports_dir,
        content,
        project_id=state.get("project_id") or None,
        head_ref=state.get("head_ref", "HEAD"),
        commit_short=commit_short,
    )
    fatals = sum(1 for f in findings if f.severity == Severity.FATAL)
    warnings = sum(1 for f in findings if f.severity == Severity.WARNING)
    by_cat: dict[str, int] = {}
    for f in findings:
        by_cat[f.category] = by_cat.get(f.category, 0) + 1
    return {
        **state,
        "findings": [f.model_dump() for f in findings],
        "report_path": str(path),
        "summary": {
            "fatal": fatals,
            "warning": warnings,
            "slow_sql": by_cat.get("SLOW_SQL", 0),
            "slow_code": by_cat.get("SLOW_CODE", 0),
            "naming": by_cat.get("NAMING", 0),
            "categories": by_cat,
        },
    }


def _parse_llm_json(raw: str) -> LlmFindingsPayload:
    text = raw.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            return LlmFindingsPayload(findings=[])
    return LlmFindingsPayload.model_validate(data)


# silence unused import warning for Settings in type hints usage
_ = Settings
