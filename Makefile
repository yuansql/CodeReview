.PHONY: help install test self-check gate web hooks-install

help:
	@echo "make install       - editable install into .venv"
	@echo "make test          - run golden tests"
	@echo "make self-check    - same as test (CI alias)"
	@echo "make gate ARGS='...' - enterprise review gate (see docs/企业落地.md)"
	@echo "make web           - start config UI on :8765"
	@echo "make hooks-install - point git hooksPath to ./hooks"

install:
	python3 -m venv .venv || true
	.venv/bin/pip install -e .

test self-check:
	bash scripts/self_check.sh

gate:
	bash scripts/ci_gate.sh $(ARGS)

web:
	.venv/bin/python -m code_review_agent.web.app

hooks-install:
	git config core.hooksPath hooks
	chmod +x hooks/pre-commit scripts/self_check.sh scripts/ci_gate.sh
	@echo "hooksPath=hooks；提交前将跑黄金用例"
