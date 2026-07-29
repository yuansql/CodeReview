from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from code_review_agent.config import get_settings
from code_review_agent.graph import run_review
from code_review_agent.projects import get_project, list_projects, reload_projects

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="代码审核数字员工", version="0.2.0")


class ReviewRequest(BaseModel):
    project_id: str
    branch: str  # 待合并分支（功能分支）
    merge_branch: str  # 合并目标（主分支）


def create_app() -> FastAPI:
    return app


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    reload_projects()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"projects": [p.model_dump(mode="json") for p in list_projects()]},
    )


@app.get("/api/projects")
def api_projects():
    reload_projects()
    return [
        {
            "id": p.id,
            "display_name": p.display_name,
            "repo": str(p.repo),
            "default_branch": p.default_branch,
            "standards": {
                "php": [str(s) for s in p.standards.php],
                "python": [str(s) for s in p.standards.python],
            },
            "php_method_style": p.php_method_style,
        }
        for p in list_projects()
    ]


@app.get("/api/projects/{project_id}/branches")
def api_branches(project_id: str):
    try:
        project = get_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repo = project.repo.expanduser()
    if not repo.is_dir():
        raise HTTPException(status_code=400, detail=f"仓库不存在: {repo}")
    from code_review_agent.git_ops import list_review_branches

    items, fetch_warning = list_review_branches(repo)
    return {
        "branches": items,
        "default_branch": project.default_branch,
        "fetch_warning": fetch_warning,
    }


@app.post("/api/review")
def api_review(body: ReviewRequest):
    try:
        project = get_project(body.project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not body.branch.strip() or not body.merge_branch.strip():
        raise HTTPException(status_code=400, detail="请同时选择「分支」和「合并分支」")
    if body.branch.strip() == body.merge_branch.strip():
        raise HTTPException(status_code=400, detail="分支与合并分支不能相同")

    from code_review_agent.git_ops import GitError, resolve_branch_ref

    repo = project.repo.expanduser()
    try:
        # 审：合并分支(base)...待合并分支(head) 整段差距
        head_ref, head_src = resolve_branch_ref(repo, body.branch.strip())
        base_ref, base_src = resolve_branch_ref(repo, body.merge_branch.strip())
    except GitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = run_review(
        project_id=body.project_id,
        repo_path=str(repo),
        base_ref=base_ref,
        head_ref=head_ref,
    )
    report_path = result.get("report_path")
    rel = None
    if report_path:
        try:
            rel = str(Path(report_path).relative_to(get_settings().reports_dir))
        except ValueError:
            rel = report_path

    feishu_ok = None
    feishu_error = None
    settings = get_settings()
    if settings.feishu_webhook_url and (settings.feishu_notify or "auto").lower() not in {
        "off",
        "0",
        "false",
        "no",
    }:
        try:
            from code_review_agent.notify_feishu import notify_review_result

            notify_review_result(
                webhook_url=settings.feishu_webhook_url,
                secret=settings.feishu_webhook_secret,
                keyword=settings.feishu_webhook_keyword or "审核推送",
                project_id=body.project_id,
                base_ref=body.merge_branch,
                head_ref=body.branch,
                summary=result.get("summary") or {},
                report_path=report_path,
                error=result.get("error"),
            )
            feishu_ok = True
        except Exception as exc:  # noqa: BLE001
            feishu_ok = False
            feishu_error = str(exc)

    return {
        "ok": not bool(result.get("error")),
        "error": result.get("error"),
        "summary": result.get("summary") or {},
        "report_path": report_path,
        "report_rel": rel,
        "commits": result.get("commits") or [],
        "project_id": body.project_id,
        "branch": body.branch,
        "merge_branch": body.merge_branch,
        "resolved_head": head_ref,
        "resolved_base": base_ref,
        "head_source": head_src,
        "base_source": base_src,
        "feishu_ok": feishu_ok,
        "feishu_error": feishu_error,
    }


@app.get("/api/reports")
def api_reports(project_id: str | None = None, limit: int = 50):
    root = get_settings().reports_dir
    if not root.is_dir():
        return []
    files = sorted(root.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    items = []
    for path in files:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        pid = parts[0] if parts else ""
        if project_id and pid != project_id:
            continue
        items.append(
            {
                "project_id": pid,
                "rel": str(rel),
                "name": path.name,
                "day": parts[1] if len(parts) > 1 else "",
                "mtime": path.stat().st_mtime,
            }
        )
        if len(items) >= limit:
            break
    return items


@app.get("/api/reports/content")
def api_report_content(rel: str):
    root = get_settings().reports_dir.resolve()
    path = (root / rel).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"rel": rel, "content": path.read_text(encoding="utf-8")}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "code_review_agent.web.app:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
    )


if __name__ == "__main__":
    main()
