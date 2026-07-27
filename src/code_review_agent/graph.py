from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from code_review_agent.models import ReviewState
from code_review_agent.nodes.review_nodes import (
    check_naming,
    check_perf,
    check_syntax,
    get_diff,
    llm_enrich,
    load_config,
    write_report_node,
)


def build_graph():
    graph = StateGraph(ReviewState)
    graph.add_node("load_config", load_config)
    graph.add_node("get_diff", get_diff)
    graph.add_node("check_syntax", check_syntax)
    graph.add_node("check_naming", check_naming)
    graph.add_node("check_perf", check_perf)
    graph.add_node("llm_enrich", llm_enrich)
    graph.add_node("write_report", write_report_node)

    graph.add_edge(START, "load_config")
    graph.add_edge("load_config", "get_diff")
    graph.add_edge("get_diff", "check_syntax")
    graph.add_edge("check_syntax", "check_naming")
    graph.add_edge("check_naming", "check_perf")
    graph.add_edge("check_perf", "llm_enrich")
    graph.add_edge("llm_enrich", "write_report")
    graph.add_edge("write_report", END)
    return graph.compile()


def run_review(
    *,
    base_ref: str,
    head_ref: str,
    project_id: str | None = None,
    repo_path: str | None = None,
) -> ReviewState:
    app = build_graph()
    payload: ReviewState = {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "findings": [],
    }
    if project_id:
        payload["project_id"] = project_id
    if repo_path:
        payload["repo_path"] = repo_path
    return app.invoke(payload)
