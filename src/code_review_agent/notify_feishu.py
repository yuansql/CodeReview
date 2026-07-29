"""飞书自定义机器人推送（Webhook v2 + 签名 + 关键词）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from typing import Any


def gen_sign(secret: str, timestamp: str | None = None) -> tuple[str, str]:
    """飞书官方算法：key = timestamp\\nsecret，msg 为空。"""
    ts = timestamp or str(round(time.time()))
    string_to_sign = f"{ts}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return ts, sign


def build_review_text(
    *,
    keyword: str,
    project_id: str | None,
    base_ref: str,
    head_ref: str,
    summary: dict[str, Any] | None,
    report_path: str | None,
    error: str | None = None,
) -> str:
    summary = summary or {}
    lines = [
        keyword.strip() or "审核推送",
        f"项目：{project_id or '-'}",
        f"分支：{head_ref} → {base_ref}",
        (
            f"致命 {summary.get('fatal', 0)} / "
            f"SLOW_SQL {summary.get('slow_sql', 0)} / "
            f"SLOW_CODE {summary.get('slow_code', 0)} / "
            f"NAMING {summary.get('naming', 0)} / "
            f"警告合计 {summary.get('warning', 0)}"
        ),
    ]
    if error:
        lines.append(f"错误：{error}")
    if report_path:
        lines.append(f"报告：{report_path}")
    return "\n".join(lines)


def send_feishu_text(
    *,
    webhook_url: str,
    text: str,
    secret: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    if not webhook_url.strip():
        raise ValueError("FEISHU_WEBHOOK_URL 未配置")

    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": text},
    }
    if secret.strip():
        ts, sign = gen_sign(secret.strip())
        payload["timestamp"] = ts
        payload["sign"] = sign

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip(),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"raw": body}
            if isinstance(parsed, dict) and parsed.get("code", 0) not in (0, None):
                raise RuntimeError(f"飞书返回错误：{parsed}")
            return parsed if isinstance(parsed, dict) else {"raw": body}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"飞书 HTTP {exc.code}：{detail}") from exc


def notify_review_result(
    *,
    webhook_url: str,
    secret: str,
    keyword: str,
    project_id: str | None,
    base_ref: str,
    head_ref: str,
    summary: dict[str, Any] | None,
    report_path: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    text = build_review_text(
        keyword=keyword,
        project_id=project_id,
        base_ref=base_ref,
        head_ref=head_ref,
        summary=summary,
        report_path=report_path,
        error=error,
    )
    return send_feishu_text(webhook_url=webhook_url, text=text, secret=secret)
