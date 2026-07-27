from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from code_review_agent.graph import run_review
from code_review_agent.projects import get_project, list_projects

app = typer.Typer(add_completion=False, no_args_is_help=True, help="代码审核数字员工 CLI")
console = Console()


@app.command("review")
def review(
    project: Optional[str] = typer.Option(
        None,
        "--project",
        "-p",
        help="projects.yaml 中的项目 id（推荐；自动带上仓库与规范）",
    ),
    base: str = typer.Option("main", "--base", help="对比基准（非 --last-commit 时）"),
    head: str = typer.Option("HEAD", "--head", help="待审端"),
    last_commit: bool = typer.Option(
        False,
        "--last-commit",
        help="只审指定分支最近一次提交（需同时传 --branch）",
    ),
    branch: Optional[str] = typer.Option(
        None,
        "--branch",
        "-b",
        help="分支名；与 --last-commit 联用",
    ),
    repo: Optional[Path] = typer.Option(
        None,
        "--repo",
        help="被审仓库路径；有 --project 时可省略",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
) -> None:
    """审核 base...head 的 diff，写出 Markdown 报告。"""
    if last_commit:
        if not branch:
            console.print("[red]使用 --last-commit 时必须指定 --branch 分支名[/red]")
            raise typer.Exit(code=2)
        base, head = f"{branch}~1", branch
    elif branch:
        head = branch

    repo_path: str | None = str(repo) if repo else None
    if project:
        try:
            cfg = get_project(project)
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        repo_path = str(cfg.repo.expanduser())
        if not branch and cfg.default_branch and last_commit:
            pass

    console.print(
        f"[bold]审核[/bold] project={project or '-'} | {head} → {base}"
        + (f" @ {repo_path}" if repo_path else "")
    )
    result = run_review(
        project_id=project,
        repo_path=repo_path,
        base_ref=base,
        head_ref=head,
    )
    report = result.get("report_path")
    summary = result.get("summary") or {}
    if report:
        console.print(f"报告已写入：[green]{report}[/green]")
    console.print(
        f"致命 {summary.get('fatal', 0)} / 警告 {summary.get('warning', 0)}"
    )
    if summary.get("fatal", 0) > 0:
        raise typer.Exit(code=1)


@app.command("projects")
def projects_cmd() -> None:
    """列出 projects.yaml 已登记项目。"""
    items = list_projects()
    if not items:
        console.print("尚未登记项目，请编辑 projects.yaml")
        return
    for p in items:
        console.print(f"[bold]{p.id}[/bold]  {p.display_name}")
        console.print(f"  repo: {p.repo}")
        for s in p.standards.php:
            console.print(f"  php: {s}")
        if not p.standards.php:
            console.print("  php: （未配置，跳过 PHP 命名）")
        for s in p.standards.python:
            console.print(f"  python: {s}")
        if not p.standards.python:
            console.print("  python: （未配置，跳过 Python 命名）")


@app.callback()
def main() -> None:
    """数字员工：语法 + 命名规范审核（PHP / Python）。"""


if __name__ == "__main__":
    app()
