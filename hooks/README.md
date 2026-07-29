# hooks

## 本仓库（Agent 自身）

| 文件 | 作用 |
|------|------|
| `pre-commit` | 提交前跑 `scripts/self_check.sh`（黄金用例） |

启用：

```bash
make hooks-install
# 等价于：git config core.hooksPath hooks && chmod +x hooks/pre-commit scripts/*.sh
```

## 业务仓门禁

**不要**把业务审核逻辑写进业务仓 hook 再实现一遍。  
请用 CI 调用：

```bash
bash /path/to/CodeReview/scripts/ci_gate.sh \
  --project <id> --base <merge目标> --head <待合并> --fail-on fatal
```

详见 [docs/企业落地.md](../docs/企业落地.md)。

历史预留的 post-merge / webhook 方案仍可包一层 HTTP，内部只调 `run_review()` / CLI。
