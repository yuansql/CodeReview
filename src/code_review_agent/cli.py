from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from code_review_agent.config import get_settings
from code_review_agent.graph import run_review
from code_review_agent.notify_feishu import notify_review_result
from code_review_agent.projects import get_project, list_projects

app = typer.Typer(add_completion=False, no_args_is_help=True, help="代码审核数字员工 CLI")
console = Console()

# 企业门禁：默认只拦致命；CI 可升到 slow / any
FailOn = typer.Option(
    "fatal",
    "--fail-on",
    help="门禁级别：off | fatal | slow | any（fatal=语法等；slow=再含 SLOW_*；any=含命名/LLM）",
)


def _should_notify_feishu(flag: str | None) -> bool:
    settings = get_settings()
    mode = (flag if flag is not None else settings.feishu_notify or "auto").strip().lower()
    if mode in {"0", "off", "false", "no"}:
        return False
    if mode in {"1", "on", "true", "yes", "always"}:
        return bool(settings.feishu_webhook_url)
    # auto
    return bool(settings.feishu_webhook_url)


def _try_notify_feishu(result: dict, *, base: str, head: str) -> None:
    settings = get_settings()
    try:
        notify_review_result(
            webhook_url=settings.feishu_webhook_url,
            secret=settings.feishu_webhook_secret,
            keyword=settings.feishu_webhook_keyword or "审核推送",
            project_id=result.get("project_id"),
            base_ref=base,
            head_ref=head,
            summary=result.get("summary") or {},
            report_path=result.get("report_path"),
            error=result.get("error"),
        )
        console.print("[green]已推送飞书[/green]")
    except Exception as exc:  # noqa: BLE001 — 通知失败不改变门禁结果
        console.print(f"[yellow]飞书推送失败：{exc}[/yellow]")


def _exit_for_gate(summary: dict, fail_on: str, has_error: bool) -> None:
    if has_error:
        raise typer.Exit(code=2)
    level = (fail_on or "fatal").strip().lower()
    if level in {"off", "none", "0"}:
        return
    fatal = int(summary.get("fatal") or 0)
    slow = int(summary.get("slow_sql") or 0) + int(summary.get("slow_code") or 0)
    warning = int(summary.get("warning") or 0)
    if level == "fatal" and fatal > 0:
        raise typer.Exit(code=1)
    if level == "slow" and (fatal > 0 or slow > 0):
        raise typer.Exit(code=1)
    if level == "any" and (fatal > 0 or warning > 0):
        raise typer.Exit(code=1)


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
        help="分支名；与 --last-commit 联用，或作为 head",
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
    fail_on: str = FailOn,
    notify_feishu: Optional[str] = typer.Option(
        None,
        "--notify-feishu",
        help="飞书推送：auto|on|off（默认读 FEISHU_NOTIFY；配了 webhook 则 auto=推）",
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
        if last_commit and not branch and cfg.default_branch:
            branch = cfg.default_branch
            base, head = f"{branch}~1", branch

    console.print(
        f"[bold]审核[/bold] project={project or '-'} | {head} → {base}"
        + (f" @ {repo_path}" if repo_path else "")
        + f" | fail-on={fail_on}"
    )
    result = run_review(
        project_id=project,
        repo_path=repo_path,
        base_ref=base,
        head_ref=head,
    )
    report = result.get("report_path")
    summary = result.get("summary") or {}
    if result.get("error"):
        console.print(f"[red]审核失败：{result['error']}[/red]")
    if report:
        console.print(f"报告已写入：[green]{report}[/green]")
    console.print(
        f"致命 {summary.get('fatal', 0)} / "
        f"SLOW_SQL {summary.get('slow_sql', 0)} / "
        f"SLOW_CODE {summary.get('slow_code', 0)} / "
        f"警告合计 {summary.get('warning', 0)}"
    )
    if _should_notify_feishu(notify_feishu):
        _try_notify_feishu(result, base=base, head=head)
    _exit_for_gate(summary, fail_on, bool(result.get("error")))


@app.command("feishu-test")
def feishu_test() -> None:
    """向已配置的飞书机器人发一条测试「审核推送」。"""
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.feishu_webhook_url:
        console.print("[red]未配置 FEISHU_WEBHOOK_URL[/red]")
        raise typer.Exit(code=2)
    try:
        notify_review_result(
            webhook_url=settings.feishu_webhook_url,
            secret=settings.feishu_webhook_secret,
            keyword=settings.feishu_webhook_keyword or "审核推送",
            project_id="(test)",
            base_ref="main",
            head_ref="feature",
            summary={
                "fatal": 0,
                "slow_sql": 0,
                "slow_code": 0,
                "naming": 0,
                "warning": 0,
            },
            report_path="(feishu-test)",
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]推送失败：{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print("[green]测试消息已发送，请看飞书群[/green]")


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


@app.command("self-check")
def self_check() -> None:
    """跑本仓库黄金用例（企业交付前自检）。"""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "self_check.sh"
    proc = subprocess.run(["bash", str(script)], cwd=root)
    raise typer.Exit(code=proc.returncode)


@app.callback()
def main() -> None:
    """数字员工：语法 + 命名规范审核（PHP / Python）。"""


if __name__ == "__main__":
    app()
