# hooks 预留

本目录留给服务器触发适配，当前 CLI 已可独立运行。

计划接口（尚未实现）：

1. `post-merge` / `pre-receive` git hook：合并进 main 后调用
   `python -m code_review_agent review --base main --head HEAD`
2. GitLab / GitHub webhook：收到 merge 事件后同样调用 `run_review()`

接入时复用 `code_review_agent.graph.run_review`，不要另写一套审核逻辑。
