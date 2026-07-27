# CodeReview 数字员工 — Agent 须知

与亲爱的协作时，优先阅读：

1. `skills/codereview-collab/SKILL.md` — **协作协议**
2. `skills/README.md` — skill 全表与机器依赖
3. 运行时：`src/code_review_agent/`（改规则必须带可复现用例）

## 默认技能链

误报/规则争议：`investigation-first` → 取证 →（可选）`fp-check` → 改代码 → 正负用例 → `memory-1.0.2` 记偏好 → `criticism-self-criticism` 复盘。

大报告一起过：`interactive-human-review` 或 `review-loop`。

工作模式稳态：`freud-skill`。PHP 深静态分析：本机 `phpstan` + `analyse-with-phpstan`。
