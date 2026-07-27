from __future__ import annotations

import ast
import re
from pathlib import Path

from code_review_agent.models import Finding, Severity

# Python: snake_case names; PascalCase classes
_PY_SNAKE = re.compile(r"^[a-z_][a-z0-9_]*$")
_PY_PASCAL = re.compile(r"^[A-Z][a-zA-Z0-9]*$")

# PHP: camelCase methods; PascalCase classes
_PHP_CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
_PHP_PASCAL = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
_PHP_FUNC = re.compile(
    r"\b(?:public|protected|private|static|final|abstract|\s)*function\s+&?([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
)
_PHP_CLASS = re.compile(r"\b(?:class|interface|trait|enum)\s+([a-zA-Z_][a-zA-Z0-9_]*)")


def _to_snake(name: str) -> str:
    """camelCase / PascalCase / 混用 → snake_case（用于建议里的真实例子）。"""
    s = name.strip("_")
    if not s:
        return name
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = s.replace("-", "_")
    s = re.sub(r"_+", "_", s)
    return s.lower().strip("_") or name.lower()


def _to_camel(name: str) -> str:
    """snake_case / PascalCase → camelCase。"""
    s = name.strip("_")
    if not s:
        return name
    if "_" in s or "-" in s:
        parts = re.split(r"[_-]+", s)
        head, *rest = parts
        return head[:1].lower() + head[1:] + "".join(p[:1].upper() + p[1:] for p in rest if p)
    if s[0].isupper():
        return s[:1].lower() + s[1:]
    return s


def _to_pascal(name: str) -> str:
    """snake_case / camelCase → PascalCase。"""
    camel = _to_camel(name)
    return camel[:1].upper() + camel[1:] if camel else name


def _suggest_rename(name: str, style: str) -> str:
    if style == "snake_case":
        example = _to_snake(name)
    elif style == "camelCase":
        example = _to_camel(name)
    elif style == "PascalCase":
        example = _to_pascal(name)
    else:
        example = name
    if example == name:
        return f"改为 {style}"
    return f"建议重命名为 `{example}`（{style}）"


def check_bracket_balance(path: Path, source: str) -> list[Finding]:
    """括号/花括号/方括号不成对 → 致命（影响运行）。"""
    # 粗略去掉字符串与注释，降低误报
    cleaned = _strip_strings_and_comments(source, path.suffix)
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = {v: k for k, v in pairs.items()}
    stack: list[tuple[str, int]] = []
    line = 1
    for ch in cleaned:
        if ch == "\n":
            line += 1
            continue
        if ch in pairs:
            stack.append((ch, line))
        elif ch in closing:
            if not stack or stack[-1][0] != closing[ch]:
                return [
                    Finding(
                        severity=Severity.FATAL,
                        category="SYNTAX",
                        file=str(path),
                        line=line,
                        message=f"括号不匹配：遇到多余的 `{ch}`（影响运行）",
                        suggestion="检查该行附近是否少写/多写了括号、花括号或方括号",
                        rule_source="builtin:brackets",
                    )
                ]
            stack.pop()
    if stack:
        ch, ln = stack[-1]
        return [
            Finding(
                severity=Severity.FATAL,
                category="SYNTAX",
                file=str(path),
                line=ln,
                message=f"括号未闭合：`{ch}` 缺少对应闭合符（影响运行）",
                suggestion="补全括号/花括号/方括号后再合并",
                rule_source="builtin:brackets",
            )
        ]
    return []


def _strip_strings_and_comments(source: str, suffix: str) -> str:
    out: list[str] = []
    i = 0
    n = len(source)
    in_sq = in_dq = in_bq = False
    in_line = in_block = False
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        if in_line:
            out.append("\n" if ch == "\n" else " ")
            if ch == "\n":
                in_line = False
            i += 1
            continue
        if in_block:
            out.append("\n" if ch == "\n" else " ")
            if ch == "*" and nxt == "/":
                out.append("  ")
                in_block = False
                i += 2
                continue
            i += 1
            continue

        if not (in_sq or in_dq or in_bq):
            if ch == "/" and nxt == "/" and suffix in {".php", ".js", ".ts"}:
                in_line = True
                out.append("  ")
                i += 2
                continue
            if ch == "#" and suffix == ".py":
                in_line = True
                out.append(" ")
                i += 1
                continue
            if ch == "/" and nxt == "*":
                in_block = True
                out.append("  ")
                i += 2
                continue

        if ch == "'" and not in_dq and not in_bq:
            # escape?
            if i > 0 and source[i - 1] == "\\":
                out.append(" ")
            else:
                in_sq = not in_sq
                out.append(" ")
            i += 1
            continue
        if ch == '"' and not in_sq and not in_bq:
            if i > 0 and source[i - 1] == "\\":
                out.append(" ")
            else:
                in_dq = not in_dq
                out.append(" ")
            i += 1
            continue
        if ch == "`" and suffix == ".py" and not in_sq and not in_dq:
            in_bq = not in_bq
            out.append(" ")
            i += 1
            continue

        if in_sq or in_dq or in_bq:
            out.append("\n" if ch == "\n" else " ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def check_python_syntax(path: Path, source: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        findings.append(
            Finding(
                severity=Severity.FATAL,
                category="SYNTAX",
                file=str(path),
                line=exc.lineno,
                message=f"Python 语法错误：{exc.msg}",
                suggestion="修复语法后再合并",
                rule_source="builtin:ast.parse",
            )
        )
    return findings


def check_python_naming(path: Path, source: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if not _PY_PASCAL.match(node.name):
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        category="NAMING",
                        file=str(path),
                        line=node.lineno,
                        message=f"类名 `{node.name}` 不符合 PascalCase（PEP 8）",
                        suggestion=_suggest_rename(node.name, "PascalCase"),
                        rule_source="builtin:PEP8",
                    )
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            if not _PY_SNAKE.match(node.name):
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        category="NAMING",
                        file=str(path),
                        line=node.lineno,
                        message=f"函数名 `{node.name}` 不符合 snake_case（PEP 8）",
                        suggestion=_suggest_rename(node.name, "snake_case"),
                        rule_source="builtin:PEP8",
                    )
                )
    return findings


def check_php_syntax(path: Path, source: str, php_bin: str | None) -> list[Finding]:
    findings: list[Finding] = []
    if php_bin:
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".php", encoding="utf-8", delete=False) as tmp:
            tmp.write(source)
            tmp_path = tmp.name
        try:
            proc = subprocess.run(
                [php_bin, "-l", tmp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout or "php -l failed").strip()
                findings.append(
                    Finding(
                        severity=Severity.FATAL,
                        category="SYNTAX",
                        file=str(path),
                        line=None,
                        message=f"PHP 语法错误：{msg}",
                        suggestion="修复语法后再合并",
                        rule_source="builtin:php -l",
                    )
                )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    else:
        # 无 php 时做极简括号/标签检查，并给出警告
        if "<?" not in source and path.suffix == ".php":
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    category="SYNTAX",
                    file=str(path),
                    line=1,
                    message="未检测到 PHP 起始标签，且本机无 php 可执行文件，跳过严格语法检查",
                    suggestion="安装 PHP CLI，或确认文件内容",
                    rule_source="builtin:fallback",
                )
            )
        else:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    category="SYNTAX",
                    file=str(path),
                    line=None,
                    message="本机未找到 `php`，已跳过 `php -l` 语法检查",
                    suggestion="安装 PHP CLI 后重跑以启用致命级语法检测",
                    rule_source="builtin:fallback",
                )
            )
    return findings


def check_php_naming_on_added_lines(
    path: Path,
    added_lines: list[tuple[int, str]],
    *,
    method_style: str = "camelCase",
    variable_style: str = "camelCase",
    rule_source: str = "builtin:PSR-12",
) -> list[Finding]:
    """只检查 diff 新增行上的类/方法明显违规；变量说得过去不报。"""
    _ = variable_style  # 保留参数兼容，变量命名已静音
    findings: list[Finding] = []
    for lineno, text in added_lines:
        stripped = text.strip()
        if not stripped or stripped.startswith(("//", "#", "*", "/*")):
            continue

        for match in _PHP_CLASS.finditer(text):
            name = match.group(1)
            if not _PHP_PASCAL.match(name):
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        category="NAMING",
                        file=str(path),
                        line=lineno,
                        message=f"类/接口/Trait 名 `{name}` 不符合 PascalCase",
                        suggestion=_suggest_rename(name, "PascalCase"),
                        rule_source=rule_source,
                    )
                )

        for match in _PHP_FUNC.finditer(text):
            name = match.group(1)
            if name.startswith("__"):
                continue
            if method_style == "snake_case":
                if not _PY_SNAKE.match(name):
                    findings.append(
                        Finding(
                            severity=Severity.WARNING,
                            category="NAMING",
                            file=str(path),
                            line=lineno,
                            message=f"方法名 `{name}` 不符合 snake_case（项目规范）",
                            suggestion=_suggest_rename(name, "snake_case"),
                            rule_source=rule_source,
                        )
                    )
            elif not _PHP_CAMEL.match(name):
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        category="NAMING",
                        file=str(path),
                        line=lineno,
                        message=f"方法名 `{name}` 不符合 camelCase",
                        suggestion=_suggest_rename(name, "camelCase"),
                        rule_source=rule_source,
                    )
                )
        # 变量命名故意不查：短名 / snake·camel 说得过去一律不黄警告
    return findings


_PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_PY_CLASS_LINE = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:(]")


def check_python_naming_on_added_lines(
    path: Path,
    added_lines: list[tuple[int, str]],
) -> list[Finding]:
    """只检查新增行上的 def/class，不扫整文件。"""
    findings: list[Finding] = []
    for lineno, text in added_lines:
        m_cls = _PY_CLASS_LINE.match(text)
        if m_cls:
            name = m_cls.group(1)
            if not _PY_PASCAL.match(name):
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        category="NAMING",
                        file=str(path),
                        line=lineno,
                        message=f"类名 `{name}` 不符合 PascalCase（PEP 8）",
                        suggestion=_suggest_rename(name, "PascalCase"),
                        rule_source="builtin:PEP8",
                    )
                )
        m_def = _PY_DEF.match(text)
        if m_def:
            name = m_def.group(1)
            if name.startswith("__") and name.endswith("__"):
                continue
            if not _PY_SNAKE.match(name):
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        category="NAMING",
                        file=str(path),
                        line=lineno,
                        message=f"函数名 `{name}` 不符合 snake_case（PEP 8）",
                        suggestion=_suggest_rename(name, "snake_case"),
                        rule_source="builtin:PEP8",
                    )
                )
    return findings
