# skills

本目录是 **CodeReview 数字员工** 的协作与准确率工具箱。

运行时审核逻辑以 `src/code_review_agent/` 为准；此处 skill 管「怎么想、怎么验、怎么和亲爱的对齐」。

## 清单（2026-07-28 · P0 落地补齐）

### 协作核心

| Skill | 用途 |
|--------|------|
| `codereview-collab/` | **我们怎么一起干活**（取证→改规则→用例→复盘） |
| `freud-skill/` | 工作模式稳态 / 少情绪开销 |
| `memory-1.0.2/` | 记偏好与误报「黑历史」 |
| `interactive-human-review/` | 大报告带亲爱的逐步确认 |
| `review-loop/` | 人审↔机审闭环 |
| `investigation-first/` | 先调查再下结论 |
| `criticism-self-criticism/` | 阶段复盘 |
| `karpathy-guidelines/` | 最小改动 |

### 准确率 / 工程

| Skill | 用途 |
|--------|------|
| `diagnosing-bugs/` | 正负用例回归思路 |
| `fp-check/` | 单条 finding 真伪（证据链） |
| `heuristic-to-deterministic/` | 启发式收成可判定 |
| `code-review/` | Standards / Spec 双轴参考 |
| `analyse-with-phpstan/` | PHPStan 分析流程（本机已装 `phpstan` 2.2.5） |

### 落地 P0（测试 + 门禁）

| Skill | 用途 |
|--------|------|
| `tdd/` | 改规则先写失败用例，再改实现 |
| `test-runner-1.0.0/` | 跑 `tests/`、约定命令与回归习惯 |
| `setup-pre-commit/` | 填 `hooks/`、pre-commit 骨架 |
| `git-guardrails-claude-code/` | 危险 git 操作护栏（配合门禁） |

符号链接指向 `.agents/skills/` 或本机全局 skill，避免双份拷贝。

## 机器依赖（已核对）

- `phpstan` → `/opt/homebrew/bin/phpstan`（2.2.5）
- `php`、CodeReview `.venv`、`ego-browser`：本机已有
- 黄金用例：`python3 tests/test_slow_checks.py`

## 推荐节奏

```
亲爱的指出问题
  → investigation-first / 看 tip 代码
  → 暧昧则 fp-check；大报告用 interactive-human-review 或 review-loop
  → 改规则（heuristic-to-deterministic）；tdd：先补正负断言
  → test-runner 跑通 tests/
  → memory 记「此类勿再误报」
  → criticism-self-criticism 复盘
  → 门禁落地：setup-pre-commit / git-guardrails（填 hooks）
```

全局 `~/.agents/skills`（~137）未删；归档另议。未链：`qa`、`security-auditor`（P1，发版/安全扫时再挂）。
