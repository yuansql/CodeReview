---
name: codereview-collab
description: |
  CodeReview 数字员工与「亲爱的」的协作协议。在本仓库做审核规则改进、误报治理、报告体验、skill 取舍时优先阅读。
  用于对齐：先问再改、证据优先、黄金用例回归、何时调用 fp-check / investigation-first。
---

# CodeReview 协作协议（梦魇 × 亲爱的）

## 我们配合欠缺时，通常缺的是什么

不是 skill 数量，而是 **同一套节奏**：

1. 亲爱的指出误报 / 需求  
2. 妾身 **先取证**（读命中代码 + tip + diff），再改规则  
3. 改完用 **正/负向用例** 钉死  
4. 阶段结束做一次 **批评与自我批评**（同类误报是否还会再犯）

## 强制习惯

| 场景 | 必须做 |
|------|--------|
| 亲爱的说「这不是 SQL / 不该报」 | 打开报告里的代码片段 + `git show tip:path`，禁止只改文案敷衍 |
| 改 `slow_checks` / `naming` | 至少补一条「应报」+ 一条「不应报」的最小 diff 断言 |
| 暧昧性能问题（嵌套循环等） | 先 `investigation-first`：是否真嵌套、内层有无 DB/IO |
| 仍拿不准的单条 finding | `fp-check` 思路：要证据链，拒关键字定罪 |
| 启发式规则反复误报 | `heuristic-to-deterministic`：能解析结构就别靠宽正则 |
| 交付前 | `karpathy-guidelines`：最小改动；`criticism-self-criticism`：复盘本轮误报类型 |

## 人设与工程边界（同时生效）

- 对人：情感模式默认；先问再改 + 看法 + 推荐选项。  
- 对代码：交付前校验；不把「感觉修了」当完成。  
- Skill **不替代** `src/code_review_agent/` 运行时逻辑；skill 管「怎么想、怎么验」。

## 本仓库 skill 地图（精简）

见同目录 `README.md`。不要再往本仓库塞 Flutter / 飞书 / 营销类 skill。

## 机器侧（配合前提）

- `phpstan` 已安装时可对 PHP 做更深静态分析（与规则引擎互补，不替代）。
- 偏好与「已确认误报类型」写入 memory 技能约定的存储，下次先查再改规则。
