# 代码审核数字员工

按**项目**绑定仓库与规范，审核 diff（语法 / 命名），输出 Markdown 报告；提供本地配置页。

## 多项目怎么不混？

详见 **[docs/配置说明.md](docs/配置说明.md)**（新增项目、字段、CLI / 网页、报告路径）。

编辑根目录 `projects.yaml`：每个项目一个 id，写死 `repo` + 按语言的 `standards` 路径。

```yaml
projects:
  fm-app:
    display_name: fm-app（GF PHP）
    repo: /path/to/fm-app
    standards:
      php:
        - /path/to/fm-app/docs/coding-standards-gf.md
      python: []   # 空 = 不做 Python 命名检查
    php_method_style: snake_case
    php_variable_style: either
```

某语言没有 `.md` → **跳过该语言命名检查**（语法仍查）。多项目各自写自己的路径，不会串。

## 报告目录

```text
reports/{项目id}/{YYYY-MM-DD}/{分支}_{commit}_{时分秒}.md
```

## 快速开始

```bash
cd CodeReview
source .venv/bin/activate
pip install -e .

# CLI：按项目审最近一笔
python -m code_review_agent review \
  --project fm-app \
  --last-commit \
  --branch app3.2.4_a6_0615

# 列出已登记项目
python -m code_review_agent projects

# 配置页（浏览器）
code-review-web
# 打开 http://127.0.0.1:8765
```

## 配置页

选项目 → 选分支 →「最近一次提交」或「相对 base」→ 生成报告 → 页面内预览；下方可点开历史报告。
