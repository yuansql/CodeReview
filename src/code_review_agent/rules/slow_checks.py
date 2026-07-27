from __future__ import annotations

import re
from pathlib import Path

from code_review_agent.models import Finding, Severity

# 仅数据库调用（禁止把 curl_exec / HTTP DELETE 当成 SQL）
_DB_CALL = re.compile(
    r"(?:"
    r"->query\s*\("
    r"|M\s*\([^)]*\)\s*->(?:query|execute|add|save|delete|select)\s*\("
    r"|->execute\s*\("
    r"|mysqli_query\s*\("
    r"|PDO::(?:query|exec|prepare)"
    r"|DB::(?:select|insert|update|delete|query|table)"
    r")",
    re.I,
)
# 兼容旧名：慢 SQL 行规则仍可能引用
_SQL_CALL = _DB_CALL
# SQL 关键字（用于片段判断；真正规则见下方更严的模式）
_SQL_KEYWORD = re.compile(r"\b(?:SELECT|UPDATE|DELETE|INSERT|REPLACE)\b", re.I)
_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
)
# 任意字符串字面量：用于把 "select..." . "where..." 拼回完整 SQL
_ANY_STRING = re.compile(
    r"""(?P<q>['"])(?P<body>(?:\\.|[^\\])*?)(?P=q)""",
    re.S,
)
_ORDER_RAND = re.compile(r"\bORDER\s+BY\s+RAND\s*\(", re.I)
# 必须像真 SQL：UPDATE ... SET / DELETE FROM，避免 $config['update'] 误报
_DML_NO_WHERE = re.compile(
    r"\b(?:DELETE\s+FROM\s+\S+|UPDATE\s+\S+\s+SET\b)(?![^;]*\bWHERE\b)",
    re.I | re.S,
)
_SELECT_STAR = re.compile(r"\bSELECT\s+\*\s+FROM\b", re.I)
_LIKE_LEADING = re.compile(r"\bLIKE\s+['\"]%", re.I)
_SELECT_FROM = re.compile(r"\bSELECT\b.+\bFROM\b", re.I | re.S)
_LOOP = re.compile(r"\b(foreach|for\s*\(|while\s*\()", re.I)
_LOOP_KEYWORD = re.compile(r"\b(?:foreach\s*\(|for\s*\(|while\s*\()", re.I)
_SLEEP = re.compile(r"\b(sleep|usleep|time_nanosleep)\s*\(", re.I)
_REMOTE_IO = re.compile(
    r"(?:file_get_contents\s*\(\s*['\"]https?://|curl_exec\s*\(|"
    r"file_put_contents\s*\(|fopen\s*\(\s*['\"]https?://|"
    r"->(?:request|get|post|put|delete)\s*\()",
    re.I,
)


def check_slow_on_diff(
    *,
    diff_text: str,
    added_entries: dict[str, list[tuple[int, str]]],
    hunks: list[dict],
) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_check_line_rules(added_entries))
    findings.extend(_check_hunk_rules(hunks))
    return _dedupe(findings)


def _check_line_rules(added_entries: dict[str, list[tuple[int, str]]]) -> list[Finding]:
    findings: list[Finding] = []
    for path, entries in added_entries.items():
        if not (path.endswith(".php") or path.endswith(".py") or path.endswith(".sql")):
            continue
        by_line = {ln: t for ln, t in entries}
        sql_covered_until = -1
        remote_io_lines: list[int] = []

        for lineno, text in sorted(entries, key=lambda x: x[0]):
            stripped = text.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("#") or stripped.startswith("*"):
                continue

            if path.endswith(".sql"):
                findings.extend(_sql_body_findings(path, lineno, text))
            elif lineno > sql_covered_until and _line_has_sqlish_string(text):
                # 跨行拼接：只拼 . "..." 链，避免把 ['update'] 等键名拼成假 SQL
                blob, end_ln = _expand_php_concat_blob(by_line, lineno)
                joined = _join_concat_string_bodies(blob)
                if joined.strip():
                    findings.extend(_sql_body_findings(path, lineno, joined))
                sql_covered_until = max(sql_covered_until, end_ln)

            if _SLEEP.search(text):
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        category="SLOW_CODE",
                        file=path,
                        line=lineno,
                        message="请求路径中使用 sleep/usleep，会造成阻塞等待",
                        suggestion="移出请求热路径，或改为异步/队列",
                        rule_source="builtin:slow_code",
                    )
                )
            if _REMOTE_IO.search(text):
                remote_io_lines.append(lineno)

        # 同一文件的远程 IO 合并成一条，避免整份报告被同类提醒淹没
        if remote_io_lines:
            extra = len(remote_io_lines) - 1
            message = "同步远程 IO / 大文件读写出现在业务代码中，可能拖慢接口"
            if extra:
                others = "、".join(str(ln) for ln in remote_io_lines[1:6])
                message += f"（本文件另有 {extra} 处：{others}）"
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    category="SLOW_CODE",
                    file=path,
                    line=remote_io_lines[0],
                    message=message,
                    suggestion="加超时、缓存，或放到异步任务；避免在循环内调用",
                    rule_source="builtin:slow_code",
                )
            )
    return findings


def _expand_php_concat_blob(
    by_line: dict[int, str],
    start_ln: int,
    *,
    max_span: int = 20,
) -> tuple[str, int]:
    """从 start_ln 起吞掉 PHP 字符串拼接续行，返回 (文本块, 覆盖到的行号)。"""
    parts: list[str] = [by_line[start_ln]]
    end = start_ln
    ln = start_ln + 1
    limit = start_ln + max_span
    while ln <= limit:
        if ln not in by_line:
            # 中间空行（未出现在新增行里）允许跳过
            if any(k > ln for k in by_line if k <= limit):
                ln += 1
                continue
            break
        raw = by_line[ln]
        stripped = raw.strip()
        if (
            stripped.startswith(".")
            or stripped.startswith('"')
            or stripped.startswith("'")
            or stripped.startswith(")")
            or stripped.startswith(";")
        ):
            parts.append(raw)
            end = ln
            if stripped.startswith(")") or stripped == ");" or stripped.endswith(");"):
                break
            ln += 1
            continue
        break
    return "\n".join(parts), end


def _looks_like_sql_fragment(body: str) -> bool:
    """排除 $config['update']、HTTP 'DELETE' 等，只认像样的 SQL 片段。"""
    compact = " ".join(body.split())
    if len(compact) < 10:
        return False
    if compact.upper() in _HTTP_METHODS:
        return False
    if not _SQL_KEYWORD.search(compact):
        return False
    # 纯标识符（数组键）不是 SQL
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", compact):
        return False
    return True


def _line_has_sqlish_string(text: str) -> bool:
    return any(_looks_like_sql_fragment(m.group("body")) for m in _ANY_STRING.finditer(text))


def _join_concat_string_bodies(blob: str) -> str:
    """
    拼接 SQL 相关字符串：
    - 本身像 SQL 片段的字面量
    - 或紧跟在 PHP `.` 后的续写字面量（where/limit 常在下一截）
    不把同一行里 $config['update'] 等无关键名拼进去。
    """
    parts: list[str] = []
    for m in _ANY_STRING.finditer(blob):
        body = m.group("body")
        before = blob[max(0, m.start() - 4) : m.start()].rstrip()
        if _looks_like_sql_fragment(body) or before.endswith("."):
            parts.append(body)
    return " ".join(parts)


def _sql_body_findings(path: str, lineno: int, body: str) -> list[Finding]:
    findings: list[Finding] = []
    compact = " ".join(body.split())
    # 整段仍不像 SQL 就直接跳过（防残余误报）
    if not _looks_like_sql_fragment(compact) and not re.search(
        r"\b(?:UPDATE\s+\S+\s+SET|DELETE\s+FROM|SELECT\b.+\bFROM)\b",
        compact,
        re.I,
    ):
        return findings

    if _DML_NO_WHERE.search(compact):
        findings.append(
            Finding(
                severity=Severity.WARNING,
                category="SLOW_SQL",
                file=path,
                line=lineno,
                message="DELETE/UPDATE 未见 WHERE，存在全表写入/删除风险（慢且危险）",
                suggestion="必须加 WHERE（及必要时 LIMIT）；禁止无条件批量改删",
                rule_source="builtin:slow_sql",
            )
        )

    if _ORDER_RAND.search(compact):
        findings.append(
            Finding(
                severity=Severity.WARNING,
                category="SLOW_SQL",
                file=path,
                line=lineno,
                message="使用 ORDER BY RAND()，大数据量下极易成为慢 SQL",
                suggestion="改为应用层随机、或用索引友好的随机主键策略",
                rule_source="builtin:slow_sql",
            )
        )

    if _LIKE_LEADING.search(compact):
        findings.append(
            Finding(
                severity=Severity.WARNING,
                category="SLOW_SQL",
                file=path,
                line=lineno,
                message="LIKE 以 % 开头，通常无法走索引，易成慢查询",
                suggestion="尽量改为前缀匹配、全文检索或搜索引擎",
                rule_source="builtin:slow_sql",
            )
        )

    if _SELECT_STAR.search(compact):
        findings.append(
            Finding(
                severity=Severity.WARNING,
                category="SLOW_SQL",
                file=path,
                line=lineno,
                message="SELECT * 会拉取多余列，增加 IO 与网络开销",
                suggestion="只选择业务需要的字段",
                rule_source="builtin:slow_sql",
            )
        )

    # SELECT ... FROM 且无 WHERE/LIMIT
    if _SELECT_FROM.search(compact) and not re.search(
        r"\b(WHERE|LIMIT|INTO|INFORMATION_SCHEMA)\b", compact, re.I
    ):
        findings.append(
            Finding(
                severity=Severity.WARNING,
                category="SLOW_SQL",
                file=path,
                line=lineno,
                message="SELECT 未见 WHERE/LIMIT，可能全表扫描",
                suggestion="加过滤条件、LIMIT，或确认表数据量极小",
                rule_source="builtin:slow_sql",
            )
        )

    return findings


def _check_hunk_rules(hunks: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    for hunk in hunks:
        path = hunk["file"]
        if not (path.endswith(".php") or path.endswith(".py")):
            continue
        lines: list[tuple[int, str]] = hunk["lines"]
        if not lines:
            continue

        # N+1 只认真实 DB API，且必须落在循环体内；
        # `$rows = M('')->query(...)` 后面紧跟 foreach 属于预取，不报。
        n1_line = _find_call_inside_loop(path, lines, _DB_CALL)

        if n1_line is not None:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    category="SLOW_SQL",
                    file=path,
                    line=n1_line,
                    message="同一改动块内出现循环 + 数据库查询，疑似 N+1 / 循环查库（慢 SQL）",
                    suggestion="改为批量查询、IN 查询，或先取出再映射；避免 foreach 内 query",
                    rule_source="builtin:slow_sql",
                )
            )

        remote_hit = _find_call_inside_loop(path, lines, _REMOTE_IO)
        if remote_hit is not None:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    category="SLOW_CODE",
                    file=path,
                    line=remote_hit,
                    message="循环内同步远程/文件 IO，极易把接口打成慢请求",
                    suggestion="移出循环、合并请求，或异步化",
                    rule_source="builtin:slow_code",
                )
            )

        # 嵌套循环：须真嵌套，且内层区间含 DB/远程等昂贵操作才报；
        # 纯内存 foreach(sources){ foreach(events) } 收集预取列表这类不报。
        costly_nested = _find_costly_nested_loop(lines)
        if costly_nested is not None:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    category="SLOW_CODE",
                    file=path,
                    line=costly_nested,
                    message="嵌套循环内含数据库/远程 IO，数据量大时易成慢代码",
                    suggestion="把查库/远程调用移出内层，改为批量或预取后再映射",
                    rule_source="builtin:slow_code",
                )
            )
    return findings


def _is_code_loop_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped.startswith(("//", "#", "*", "/*")):
        return False
    return bool(_LOOP_KEYWORD.search(stripped) or _LOOP.search(stripped))


def _find_costly_nested_loop(lines: list[tuple[int, str]]) -> int | None:
    """
    按花括号深度找真正的嵌套循环；仅当内层循环体（到该层结束）里
    出现 DB 调用或远程 IO 时才判定为慢代码。
    """
    depth = 0
    # 已打开、尚未闭合的循环：其「起始花括号深度」
    open_loops: list[int] = []
    # 内层循环候选：(inner_line, body_start_depth)
    nested_inners: list[tuple[int, int]] = []

    for ln, text in lines:
        is_loop = _is_code_loop_line(text)
        if is_loop and open_loops and depth > open_loops[-1]:
            nested_inners.append((ln, depth))
        if is_loop:
            open_loops.append(depth)

        for ch in text:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
                while open_loops and depth <= open_loops[-1]:
                    open_loops.pop()

    if not nested_inners:
        return None

    # 检查每个内层循环其后文，直到花括号回到进入前的深度
    by_idx = list(lines)
    for inner_ln, start_depth in nested_inners:
        body_parts: list[str] = []
        seen = False
        d = None
        # 从内层行起扫描；深度从该行前的 start_depth 开始，吃掉行内括号
        scan_depth = start_depth
        for ln, text in by_idx:
            if ln == inner_ln:
                seen = True
            if not seen:
                continue
            body_parts.append(text)
            for ch in text:
                if ch == "{":
                    scan_depth += 1
                elif ch == "}":
                    scan_depth = max(0, scan_depth - 1)
            # 内层循环体结束后（回到进入内层时的深度）
            if seen and ln != inner_ln and scan_depth <= start_depth:
                break
        body = "\n".join(body_parts)
        if _DB_CALL.search(body) or _REMOTE_IO.search(body) or _SLEEP.search(body):
            return inner_ln
    return None


def _find_call_inside_loop(
    path: str,
    lines: list[tuple[int, str]],
    pattern: re.Pattern[str],
) -> int | None:
    """
    返回首个「位于循环体内」的匹配行号；不在循环里就返回 None。
    PHP 按花括号深度判断，Python 按缩进判断。
    """
    if path.endswith(".py"):
        return _find_call_inside_loop_py(lines, pattern)
    return _find_call_inside_loop_braces(lines, pattern)


def _find_call_inside_loop_braces(
    lines: list[tuple[int, str]],
    pattern: re.Pattern[str],
) -> int | None:
    depth = 0
    body_depths: list[int] = []  # 每个未闭合循环体所在的花括号深度

    for ln, text in lines:
        is_loop = _is_code_loop_line(text)
        hit = bool(pattern.search(text))

        # 单行写法：foreach (...) $db->query(); 或 foreach (...) { query(); }
        if hit and (body_depths or is_loop):
            return ln

        pending_loop = is_loop
        for ch in text:
            if ch == "{":
                depth += 1
                if pending_loop:
                    body_depths.append(depth)
                    pending_loop = False
            elif ch == "}":
                while body_depths and body_depths[-1] >= depth:
                    body_depths.pop()
                depth = max(0, depth - 1)
    return None


def _find_call_inside_loop_py(
    lines: list[tuple[int, str]],
    pattern: re.Pattern[str],
) -> int | None:
    loop_indents: list[int] = []

    for ln, text in lines:
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(text) - len(text.lstrip())
        while loop_indents and indent <= loop_indents[-1]:
            loop_indents.pop()

        if pattern.search(text) and loop_indents:
            return ln
        if re.match(r"^\s*(?:for|while)\b", text):
            loop_indents.append(indent)
    return None


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.severity, f.category, f.file, f.line, f.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# silence unused Path if any
_ = Path
