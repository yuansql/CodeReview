from __future__ import annotations

from pathlib import Path


def load_markdown_standards(directory: Path) -> list[str]:
    """Load *.md under a directory. Empty → []."""
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.md"))
    contents: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            contents.append(f"# Source: {path.name}\n{text}")
    return contents


def load_standards_files(paths: list[Path]) -> list[str]:
    contents: list[str] = []
    for path in paths:
        p = path.expanduser()
        if not p.is_file():
            contents.append(f"# Missing: {p}\n(规范文件不存在)")
            continue
        text = p.read_text(encoding="utf-8").strip()
        if text:
            contents.append(f"# Source: {p}\n{text}")
    return contents


def standards_label(
    paths_loaded: list[str],
    lang: str,
    *,
    naming_enabled: bool | None = None,
) -> str:
    if naming_enabled is False or not paths_loaded:
        return f"{lang}：未配置 .md，跳过命名检查（语法仍检查）"
    sources = []
    for block in paths_loaded:
        first = block.splitlines()[0] if block else ""
        if first.startswith("# Source:"):
            sources.append(first.removeprefix("# Source:").strip())
    if sources:
        joined = "；".join(sources[:5])
        extra = f" 等 {len(sources)} 份" if len(sources) > 5 else ""
        return f"{lang}：{joined}{extra}"
    return f"{lang}：已加载 {len(paths_loaded)} 份规范"
