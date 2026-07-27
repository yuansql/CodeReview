from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _run(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def resolve_ref(repo: Path, ref: str) -> str:
    return _run(repo, "rev-parse", "--verify", ref).strip()


def ref_exists(repo: Path, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def fetch_remote(repo: Path, remote: str = "origin") -> str | None:
    """拉取远程；失败返回错误信息，不抛（列表仍可用本地缓存的 remote-tracking）。"""
    proc = subprocess.run(
        ["git", "fetch", "--prune", remote],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "git fetch failed").strip()
    return None


def list_review_branches(repo: Path, remote: str = "origin") -> tuple[list[dict], str | None]:
    """
    以远程分支为主列表。
    返回 ([{name, has_local, source}], fetch_warning)。
    source: local_and_remote | remote_only | local_only
    """
    fetch_warning = fetch_remote(repo, remote)

    remote_names: set[str] = set()
    out = subprocess.run(
        ["git", "branch", "-r", "--format=%(refname:short)"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if out.returncode == 0:
        prefix = f"{remote}/"
        for line in out.stdout.splitlines():
            name = line.strip()
            if not name or "->" in name:
                continue
            if name.startswith(prefix):
                short = name[len(prefix) :]
                if short == "HEAD":
                    continue
                remote_names.add(short)

    local_names: set[str] = set()
    out_l = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if out_l.returncode == 0:
        local_names = {b.strip() for b in out_l.stdout.splitlines() if b.strip()}

    names = sorted(remote_names | local_names)
    items: list[dict] = []
    for name in names:
        has_local = name in local_names
        on_remote = name in remote_names
        if on_remote and has_local:
            source = "local_and_remote"
        elif on_remote:
            source = "remote_only"
        else:
            source = "local_only"
        items.append({"name": name, "has_local": has_local, "source": source})
    return items, fetch_warning


def resolve_branch_ref(repo: Path, branch: str, remote: str = "origin") -> tuple[str, str]:
    """
    本地有该分支 → 用本地名；
    否则 fetch 后用 remote/branch。
    返回 (git_ref, note)。
    """
    if ref_exists(repo, f"refs/heads/{branch}"):
        return branch, "local"
    fetch_remote(repo, remote)
    remote_ref = f"{remote}/{branch}"
    if ref_exists(repo, f"refs/remotes/{remote}/{branch}") or ref_exists(repo, remote_ref):
        return remote_ref, "remote"
    raise GitError(
        f"分支 `{branch}` 本地不存在，远程 `{remote}` 也找不到；请确认分支名或先推送到远程"
    )


def three_dot_diff(repo: Path, base: str, head: str) -> str:
    return _run(repo, "diff", f"{base}...{head}")


def commit_list(repo: Path, base: str, head: str) -> list[str]:
    out = _run(repo, "log", f"{base}..{head}", "--oneline")
    return [line for line in out.splitlines() if line.strip()]


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    out = _run(repo, "diff", "--name-only", f"{base}...{head}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def show_file_at(repo: Path, ref: str, rel_path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def parse_added_lines(diff_text: str) -> dict[str, list[int]]:
    """从 unified diff 解析新增行号。返回 {相对路径: [行号, ...]}。"""
    return {
        path: [ln for ln, _ in entries]
        for path, entries in parse_added_entries(diff_text).items()
    }


def parse_added_entries(diff_text: str) -> dict[str, list[tuple[int, str]]]:
    """
    解析新增行：{相对路径: [(行号, 行文本), ...]}
    行文本不含前导 '+'。
    """
    result: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    new_line = 0

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current = None
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path == "/dev/null":
                current = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            current = path
            result.setdefault(current, [])
            continue
        if raw.startswith("@@"):
            try:
                plus = raw.split("+", 1)[1].split("@@", 1)[0].strip()
                start = plus.split(",", 1)[0]
                new_line = int(start)
            except (IndexError, ValueError):
                new_line = 0
            continue
        if current is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            result[current].append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue
        elif raw.startswith("\\"):
            continue
        else:
            new_line += 1

    return {path: entries for path, entries in result.items() if entries}


def parse_added_hunks(diff_text: str) -> list[dict]:
    """
    按 hunk 聚合新增行，便于检测「循环内查库」。
    每项: {file, lines: [(lineno, text), ...], texts: joined}
    """
    hunks: list[dict] = []
    current: str | None = None
    hunk_entries: list[tuple[int, str]] = []
    new_line = 0

    def _flush() -> None:
        nonlocal hunk_entries
        if current and hunk_entries:
            hunks.append(
                {
                    "file": current,
                    "lines": list(hunk_entries),
                    "texts": "\n".join(t for _, t in hunk_entries),
                }
            )
        hunk_entries = []

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            _flush()
            current = None
            continue
        if raw.startswith("+++ "):
            _flush()
            path = raw[4:].strip()
            if path == "/dev/null":
                current = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            current = path
            continue
        if raw.startswith("@@"):
            _flush()
            try:
                plus = raw.split("+", 1)[1].split("@@", 1)[0].strip()
                start = plus.split(",", 1)[0]
                new_line = int(start)
            except (IndexError, ValueError):
                new_line = 0
            continue
        if current is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            hunk_entries.append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue
        elif raw.startswith("\\"):
            continue
        else:
            new_line += 1
    _flush()
    return hunks
