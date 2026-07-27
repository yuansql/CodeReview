# 代码审核数字员工 — 设计摘要

> 2026-07-24 与主人对齐后落地。

**Goal:** 分支合入 main 前，对 PHP/Python diff 做语法与命名审核，输出带致命/警告的 Markdown 报告。

**Architecture:** CLI 触发 LangGraph；规则引擎（ast / php -l / 命名正则）+ Kimi LLM 补充；`standards/{python,php}/*.md` 可覆盖默认规范。

**Trigger:** 先做 CLI；`hooks/` 预留 webhook / git hook。

**Model:** Moonshot OpenAI-compatible（`.env`）。
