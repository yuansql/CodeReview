"""慢 SQL / 慢代码的黄金用例：每条都对应一次真实误报或真实命中。

可直接跑：python3 tests/test_slow_checks.py
装了 pytest 也能跑：pytest tests
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_review_agent.rules.slow_checks import (  # noqa: E402
    _check_hunk_rules,
    _check_line_rules,
    _find_call_inside_loop,
)


def _line_cats(path: str, entries: list[tuple[int, str]]) -> list[str]:
    return [f.category for f in _check_line_rules({path: entries})]


def _hunk(path: str, lines: list[tuple[int, str]]) -> list[tuple[str, int]]:
    findings = _check_hunk_rules([{"file": path, "lines": lines}])
    return [(f.category, f.line) for f in findings]


# ---------- 不应报：数组键 / HTTP 动词 ----------

def test_config_array_key_update_is_not_sql() -> None:
    entries = [(664, "$update = intval($config['update'] ?? 0);")]
    assert _line_cats("A.php", entries) == []


def test_http_delete_is_not_sql() -> None:
    cats = _line_cats("A.php", [(133, "$url = $this->buildSignedUrl('DELETE', $path);")])
    assert "SLOW_SQL" not in cats


def test_redis_get_is_not_remote_io() -> None:
    cats = _line_cats("A.php", [(56, "$verify = $redis->get('app_aichat_bind' . $phone);")])
    assert "SLOW_CODE" not in cats
    assert "SLOW_SQL" not in cats


def test_model_delete_is_not_remote_io() -> None:
    cats = _line_cats(
        "A.php",
        [(20, "M('app_ai_chat_bind')->delete(['id'=>$res['id']]);")],
    )
    assert "SLOW_CODE" not in cats


def test_http_client_request_with_method_is_remote_io() -> None:
    cats = _line_cats(
        "A.php",
        [(91, "return $this->request('POST', $url, $body, ['Content-Type: application/json']);")],
    )
    assert "SLOW_CODE" in cats


def test_curl_exec_is_remote_io_not_sql() -> None:
    cats = _line_cats("A.php", [(222, "$output = curl_exec($ch);")])
    assert "SLOW_SQL" not in cats
    assert "SLOW_CODE" in cats


# ---------- 不应报：多行拼接后 WHERE/LIMIT 齐全 ----------

def test_multiline_concat_select_with_where_limit_is_clean() -> None:
    entries = [
        (312, '$rows = M(\'\')->query('),
        (313, '"select b.user_id from ai_device_last_user a "'),
        (314, '. "left join app_user b on a.user_id=b.user_id "'),
        (315, '. "where a.code=\'{$imei}\' limit 1"'),
    ]
    assert _line_cats("A.php", entries) == []


# ---------- 应报：真 SQL 风险 ----------

def test_delete_without_where_is_reported() -> None:
    cats = _line_cats("A.php", [(10, '$db->query("DELETE FROM app_user");')])
    assert "SLOW_SQL" in cats


def test_select_star_is_reported() -> None:
    cats = _line_cats("A.php", [(10, '$db->query("SELECT * FROM app_user WHERE id=1");')])
    assert "SLOW_SQL" in cats


# ---------- N+1：只认循环体内的查库 ----------

def test_prefetch_query_then_foreach_is_not_n_plus_one() -> None:
    """真实误报：先一次 IN 查询，再 foreach 组装映射。"""
    lines = [
        (151, "$ids_str = implode(',', $role_ids);"),
        (152, "$rows = M('')->query(\"select role_id from t where role_id in ({$ids_str})\");"),
        (153, "foreach($rows as $row){"),
        (154, "    $map[intval($row['role_id'])] = 1;"),
        (155, "}"),
    ]
    assert _hunk("A.php", lines) == []


def test_single_save_outside_loop_is_not_n_plus_one() -> None:
    """真实误报：普通一次 save，附近没有循环。"""
    lines = [
        (132, "public function email_unbind() {"),
        (133, "    $res = M('app_user')->save(['user_id' => $this->userId], ["),
        (134, "        'email' => '',"),
        (135, "    ]);"),
        (136, "}"),
    ]
    assert _hunk("A.php", lines) == []


def test_query_inside_foreach_is_n_plus_one() -> None:
    lines = [
        (10, "foreach($ids as $id){"),
        (11, "    $row = M('')->query(\"select * from t where id={$id}\");"),
        (12, "}"),
    ]
    hits = _hunk("A.php", lines)
    assert ("SLOW_SQL", 11) in hits


def test_python_cursor_execute_inside_for_is_n_plus_one() -> None:
    lines = [
        (10, "for uid in uids:"),
        (11, "    row = cursor.execute('select 1 from t where id=%s', (uid,))"),
    ]
    hits = _hunk("a.py", lines)
    assert ("SLOW_SQL", 11) in hits


def test_python_execute_outside_loop_is_clean() -> None:
    lines = [
        (10, "rows = cursor.execute('select id from t')"),
        (11, "for row in rows:"),
        (12, "    use(row)"),
    ]
    assert _hunk("a.py", lines) == []


def test_python_indent_detects_call_inside_for() -> None:
    """Python 走缩进判断：循环体内命中，循环外不命中。

    注：`_DB_CALL` 目前只覆盖 PHP 写法，Python 的 cursor.execute() 尚未纳入识别。
    """
    pattern = re.compile(r"\.execute\s*\(")
    inside = [(10, "for uid in uids:"), (11, "    cur.execute(sql)")]
    outside = [(10, "cur.execute(sql)"), (11, "for row in rows:"), (12, "    use(row)")]
    assert _find_call_inside_loop("a.py", inside, pattern) == 11
    assert _find_call_inside_loop("a.py", outside, pattern) is None


# ---------- 嵌套循环：内存收集不报，内层查库才报 ----------

def test_in_memory_nested_loop_is_clean() -> None:
    lines = [
        (1, "foreach ($sources as $s) {"),
        (2, "    foreach ($s['events'] as $e) {"),
        (3, "        $pending[] = $e['id'];"),
        (4, "    }"),
        (5, "}"),
    ]
    assert _hunk("A.php", lines) == []


def test_nested_loop_with_db_inside_is_reported() -> None:
    lines = [
        (1, "foreach ($sources as $s) {"),
        (2, "    foreach ($s['events'] as $e) {"),
        (3, "        M('t')->select();"),
        (4, "    }"),
        (5, "}"),
    ]
    cats = [c for c, _ in _hunk("A.php", lines)]
    assert "SLOW_CODE" in cats


# ---------- 远程 IO 噪声压缩 ----------

def test_remote_io_collapsed_per_file() -> None:
    entries = [
        (56, "$res = curl_exec($ch1);"),
        (95, "$res = curl_exec($ch2);"),
        (391, "$res = curl_exec($ch3);"),
    ]
    findings = _check_line_rules({"A.php": entries})
    remote = [f for f in findings if f.category == "SLOW_CODE"]
    assert len(remote) == 1
    assert remote[0].line == 56
    assert "另有 2 处" in remote[0].message


def _run_all() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name}")
    print("FAILED" if failures else "ALL PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
