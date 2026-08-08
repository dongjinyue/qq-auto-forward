# main.py
"""QQ消息 -> 企业微信群机器人 中转服务

NapCat(OneBot11) 通过 HTTP POST 上报到 /onebot/event
本服务过滤、格式化后调用企业微信 Webhook 推送。

本地启动:  uvicorn main:app --reload --host 127.0.0.1 --port 8080
服务器启动: uvicorn main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import config

app = FastAPI(title="QQ Forward to WeChat")

# 滑动窗口限流（60秒内最多 MAX_MSG_PER_MINUTE 条）
_msg_timestamps: deque[float] = deque()


def _log(msg: str) -> None:
    if config.DEBUG:
        ts = time.strftime("%H:%M:%S", time.localtime())
        print(f"[{ts}] {msg}")


def _rate_limited() -> bool:
    """返回 True 表示被限流，丢弃本条消息"""
    now = time.time()
    while _msg_timestamps and now - _msg_timestamps[0] > 60:
        _msg_timestamps.popleft()
    if len(_msg_timestamps) >= config.MAX_MSG_PER_MINUTE:
        _log("⚠️ 限流触发，丢弃本条")
        return True
    _msg_timestamps.append(now)
    return False


def _extract_text(message: list[dict] | str | Any) -> str:
    """从 OneBot11 message 数组 / 字符串 提取纯文本"""
    if isinstance(message, str):
        return message.strip()
    if not isinstance(message, list):
        return ""
    parts: list[str] = []
    for seg in message:
        if not isinstance(seg, dict):
            continue
        t = seg.get("type", "")
        d = seg.get("data", {})
        if not isinstance(d, dict):
            d = {}
        if t == "text":
            parts.append(d.get("text", ""))
        elif t == "at":
            qq = d.get("qq", "")
            name = d.get("name", "")
            parts.append(f"@{name or qq}" if (qq or name) else "@某人")
        elif t == "image":
            url = d.get("url", "") or d.get("file", "")
            parts.append(f"[图片:{url[:40]}...]" if url else "[图片]")
        elif t == "reply":
            parts.append("[引用]")
        elif t == "face":
            parts.append(f"[表情{d.get('id', '')}]")
        elif t == "file":
            parts.append(f"[文件:{d.get('name', '')}]")
        elif t == "record":
            parts.append("[语音]")
        elif t == "video":
            parts.append("[视频]")
        else:
            parts.append(f"[{t}]")
    return "".join(parts).strip()


def _match_keywords(text: str) -> bool:
    """关键词匹配：配置为空时全部放行；否则任意命中即放行"""
    if not config.KEYWORDS:
        return True
    hit = any(kw and kw in text for kw in config.KEYWORDS)
    if not hit:
        _log(f"🔍 关键词未命中，丢弃：{text[:30]}...")
    return hit


async def _send_to_wechat(content: str) -> tuple[bool, str]:
    """调用企业微信 Webhook 发送 text 消息
    返回 (是否成功, 说明信息)
    """
    if not content:
        return False, "empty"

    # 企业微信文本消息最长 4096 字节，按 UTF-8 截断
    encoded = content.encode("utf-8")
    if len(encoded) > 4000:
        content = encoded[:4000].decode("utf-8", errors="ignore") + "\n...[内容过长已截断]"

    payload = {"msgtype": "text", "text": {"content": content}}

    if "请粘贴你的KEY" in config.WECHAT_WEBHOOK_URL:
        _log("❌ Webhook URL 还没填好，请编辑 config.py 的 WECHAT_WEBHOOK_URL")
        return False, "webhook_not_configured"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(config.WECHAT_WEBHOOK_URL, json=payload)
            data = resp.json()
            if data.get("errcode", 0) != 0:
                err = f"企业微信错误：errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
                _log(f"❌ {err}")
                return False, err
            return True, "ok"
    except httpx.TimeoutException:
        _log("❌ 调用企业微信超时")
        return False, "timeout"
    except Exception as e:  # noqa: BLE001
        _log(f"❌ 调用企业微信异常：{e}")
        return False, f"exception:{e}"


@app.post("/onebot/event")
async def handle_onebot_event(request: Request) -> JSONResponse:
    """接收 NapCat 的 OneBot11 HTTP 上报"""
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"status": "bad_request"})

    post_type = data.get("post_type")

    # 元事件（心跳、生命周期）直接ok，不要打太多日志
    if post_type == "meta_event":
        return JSONResponse({"status": "ok"})

    if config.DEBUG:
        _log(f"📥 收到事件 post_type={post_type} type={data.get('message_type')}")

    if post_type != "message":
        return JSONResponse({"status": "ignore_not_message"})

    # 只处理群消息
    if data.get("message_type") != "group":
        return JSONResponse({"status": "ignore_not_group"})

    group_id = data.get("group_id")
    sender = data.get("sender") or {}
    user_id = data.get("user_id")
    self_id = data.get("self_id")

    # 忽略自己发的消息，防止回环
    if config.IGNORE_SELF_MESSAGE and self_id and self_id == user_id:
        return JSONResponse({"status": "ignore_self"})

    # 群白名单
    if config.TARGET_GROUP_IDS and group_id not in config.TARGET_GROUP_IDS:
        _log(f"🚫 不在白名单的群 {group_id}，丢弃")
        return JSONResponse({"status": "filtered_group"})

    # 提取文本
    raw_message = data.get("message", "")
    text = _extract_text(raw_message)
    if not text:
        _log("ℹ️ 消息无可转发内容，丢弃")
        return JSONResponse({"status": "empty_text"})

    # 关键词过滤
    if not _match_keywords(text):
        return JSONResponse({"status": "filtered_keyword"})

    # 限流
    if _rate_limited():
        return JSONResponse({"status": "rate_limited"})

    # 格式化
    if config.ADD_SENDER_PREFIX:
        group_name = data.get("group_name") or (f"群{group_id}" if group_id else "群")
        nickname = (
            sender.get("card")
            or sender.get("nickname")
            or (f"用户{user_id}" if user_id else "某人")
        )
        content = f"【{group_name}】{nickname}：\n{text}"
    else:
        content = text

    # 发送
    ok, reason = await _send_to_wechat(content)
    if ok:
        _log(f"✅ 转发成功 [{(content[:30]).replace(chr(10), ' ')}...]")
    return JSONResponse({"status": "ok" if ok else f"send_failed:{reason}"})


@app.get("/health")
async def health() -> dict[str, Any]:
    """健康检查"""
    return {
        "status": "ok",
        "service": "qq-forward",
        "webhook_configured": "请粘贴你的KEY" not in config.WECHAT_WEBHOOK_URL,
        "target_groups": config.TARGET_GROUP_IDS,
        "keywords": config.KEYWORDS,
    }
