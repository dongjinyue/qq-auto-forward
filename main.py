# main.py
"""QQ消息 -> 企业微信群机器人 中转服务

NapCat(OneBot11) 通过 HTTP POST 上报到 /onebot/event
本服务过滤、格式化后调用企业微信 Webhook 推送。

本地启动:  uvicorn main:app --reload --host 127.0.0.1 --port 8080
服务器启动: uvicorn main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
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


def _parse_cq_code(cq_str: str) -> list[dict]:
    """把 CQ 码字符串解析成 OneBot11 消息段数组。
    例如：[CQ:image,file=abc.jpg,url=https://x]你好 -> [image段, text段]
    """
    segments: list[dict] = []
    # 找出所有 [CQ:type,k=v,k2=v2]
    pattern = re.compile(r"\[CQ:([a-zA-Z0-9_]+)((?:,[a-zA-Z0-9_]+=[^\]]*)*)\]")
    last = 0
    for m in pattern.finditer(cq_str):
        start, end = m.span()
        if start > last:
            text = cq_str[last:start]
            if text:
                segments.append({"type": "text", "data": {"text": text}})
        seg_type = m.group(1)
        data: dict[str, str] = {}
        param_str = m.group(2)
        if param_str:
            # 去掉开头逗号，按逗号分割
            for kv in param_str[1:].split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    data[k.strip()] = v
        segments.append({"type": seg_type, "data": data})
        last = end
    if last < len(cq_str):
        tail = cq_str[last:]
        if tail:
            segments.append({"type": "text", "data": {"text": tail}})
    return segments


def _normalize_message(msg: list[dict] | str | Any) -> list[dict]:
    """把任意格式（message数组 / CQ码字符串 / 其他）统一成 OneBot11 消息段数组"""
    if isinstance(msg, list):
        # 列表里可能混合了字符串段？规范化
        out: list[dict] = []
        for item in msg:
            if isinstance(item, dict):
                out.append(item)
            elif isinstance(item, str):
                out.extend(_parse_cq_code(item))
        return out
    if isinstance(msg, str):
        return _parse_cq_code(msg)
    return []


def _extract_text(message: list[dict] | str | Any) -> str:
    """从 OneBot11 message 数组 / 字符串 / CQ码 提取纯文本
    注：forward 合并转发由调用方单独调用 _extract_forward_ids + _fetch_forward_content 展开
    """
    if isinstance(message, str):
        # 如果是 CQ 码字符串，先解析
        if "[CQ:" in message:
            message = _parse_cq_code(message)
        else:
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
            pass
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
        elif t == "forward":
            fid = d.get("id", "") or d.get("forward_id", "") or ""
            if not fid:
                # NapCat forward 段可能还有其他字段存 id
                for key in list(d.keys()):
                    val = d.get(key)
                    if isinstance(val, str) and val and len(val) > 5:
                        fid = val
                        _log(f"🔍 forward 段：id 从字段 {key} 提取 = {fid[:30]}")
                        break
            if not fid:
                _log(f"🔍 forward 段 data 全量：{d}")
            parts.append(f"\n[聊天记录({fid[:8] if fid else 'id未知'})]\n")
        else:
            parts.append(f"[{t}]")
    return "".join(parts).strip()


def _extract_forward_ids(message: list[dict] | str | Any) -> list[str]:
    """提取消息数组中所有 forward 段的 id"""
    if not isinstance(message, list):
        return []
    ids: list[str] = []
    for seg in message:
        if not isinstance(seg, dict):
            continue
        t = seg.get("type", "")
        if t != "forward":
            continue
        d = seg.get("data", {})
        if not isinstance(d, dict):
            d = {}
        _log(f"🔍 forward 段 type={t} data={d}")
        fid = d.get("id", "") or d.get("forward_id", "") or ""
        if not fid:
            # 尝试从其他字段找 id
            for key in list(d.keys()):
                val = d.get(key)
                if isinstance(val, str) and val and len(val) > 5:
                    fid = val
                    break
        if fid:
            ids.append(fid)
            _log(f"🔍 forward id 提取成功：{fid[:30]}")
        else:
            _log(f"⚠️ forward 段未能提取到 id，data 全量：{d}")
    return ids


async def _fetch_forward_content(forward_id: str) -> list[dict[str, Any]]:
    """调用 NapCat /get_forward_msg 获取合并转发的聊天记录内容。
    返回有序段列表：[{"type":"text","text":"昵称: xxx"}, {"type":"image","url":"...","file":"..."}, ...]
    保持聊天记录内部「文字→图片」交错顺序
    """
    result_segments: list[dict[str, Any]] = []
    try:
        api_url = f"{config.NAPCAT_API_URL}/get_forward_msg"
        headers = {}
        if config.NAPCAT_TOKEN:
            headers["Authorization"] = f"Bearer {config.NAPCAT_TOKEN}"
        async with httpx.AsyncClient(timeout=20) as client:
            # 同时尝试 id 和 message_id 两个常见字段名
            for payload in [
                {"id": forward_id},
                {"message_id": forward_id},
            ]:
                resp = await client.post(api_url, json=payload, headers=headers)
                if resp.status_code != 200:
                    continue
                j = resp.json()
                _log(f"📋 /get_forward_msg 响应：status={j.get('status')} keys={list(j.keys())}")
                if j.get("status") != "ok":
                    continue
                result = j.get("data")
                if not result:
                    continue
                # 常见格式: {"messages": [...]} 或直接是数组
                msgs = result if isinstance(result, list) else result.get("messages", [])
                if not isinstance(msgs, list) or not msgs:
                    # 尝试其他字段名，NapCat 可能用 content/items 等
                    for alt_key in ("content", "items", "list", "msgs"):
                        if isinstance(result, dict) and isinstance(result.get(alt_key), list) and result.get(alt_key):
                            msgs = result[alt_key]
                            _log(f"📋 使用备用字段 {alt_key} 获取聊天记录 {len(msgs)} 条")
                            break
                if not isinstance(msgs, list) or not msgs:
                    _log(f"📋 result 详情（前500字符）：{str(result)[:500]}")
                    continue
                if config.DEBUG:
                    # 打印前 2 条的完整结构
                    for idx in range(min(2, len(msgs))):
                        _log(f"📋 聊天记录[{idx}] 结构：{str(msgs[idx])[:400]}")

                # 聊天记录开头分隔符
                result_segments.append({"type": "text", "text": "---聊天记录---\n"})

                for idx, m in enumerate(msgs):
                    if idx >= 30:
                        result_segments.append({"type": "text", "text": f"\n……（剩余 {len(msgs) - 30} 条已省略）"})
                        break
                    if not isinstance(m, dict):
                        if isinstance(m, str):
                            result_segments.append({"type": "text", "text": m + "\n"})
                        continue
                    sender = m.get("sender", {}) or {}
                    nickname = sender.get("nickname") or sender.get("card") or str(m.get("user_id") or sender.get("user_id") or "?")
                    content_raw = m.get("content", m.get("message", m.get("raw_message", "")))
                    # content 可能是 OneBot11 数组、字符串 CQ 码、或嵌套的 message 数组 → 统一规范化
                    content_segs = _normalize_message(content_raw)

                    # 本条消息：先攒前缀 + 文本，遇到图片就先把文本段发出去，再发图片段
                    text_buf: list[str] = [f"{nickname}: "]
                    has_content = False

                    def flush_text_buf():
                        nonlocal text_buf, has_content
                        if text_buf:
                            joined = "".join(text_buf)
                            if joined.strip():
                                result_segments.append({"type": "text", "text": joined})
                            text_buf = []
                            has_content = True

                    for seg in content_segs:
                        if not isinstance(seg, dict):
                            continue
                        st = seg.get("type", "")
                        sd = seg.get("data", {})
                        if not isinstance(sd, dict):
                            sd = {}
                        if st == "text":
                            text_buf.append(sd.get("text", "") or "")
                        elif st == "at":
                            qq = sd.get("qq", "")
                            name = sd.get("name", "")
                            text_buf.append(f"@{name or qq}" if (qq or name) else "@某人")
                        elif st == "reply":
                            text_buf.append("[引用]")
                        elif st == "face":
                            text_buf.append(f"[表情{sd.get('id', '')}]")
                        elif st == "image":
                            # 有图片：先把已攒的文本段 flush，再插入图片段
                            flush_text_buf()
                            img_url = sd.get("url", "") or ""
                            img_file = sd.get("file", "") or ""
                            if img_url or img_file:
                                result_segments.append({"type": "image", "url": img_url, "file": img_file})
                        elif st == "file":
                            text_buf.append(f" [文件:{sd.get('name', '')}]")
                        elif st == "record":
                            text_buf.append(" [语音]")
                        elif st == "video":
                            text_buf.append(" [视频]")
                        elif st == "forward":
                            text_buf.append(" [聊天记录]")

                    # 本条消息结束，flush 剩余文本 + 换行
                    if text_buf and "".join(text_buf).strip():
                        result_segments.append({"type": "text", "text": "".join(text_buf)})
                    # 每条消息后加一个换行（合并到下一条 text 的开头或单独加）
                    if result_segments and result_segments[-1]["type"] == "text":
                        result_segments[-1]["text"] = result_segments[-1]["text"].rstrip() + "\n"
                    elif result_segments:
                        result_segments.append({"type": "text", "text": "\n"})

                # 聊天记录结尾分隔符
                if result_segments and result_segments[-1]["type"] == "text":
                    result_segments[-1]["text"] = result_segments[-1]["text"].rstrip("\n") + "\n----------------"
                else:
                    result_segments.append({"type": "text", "text": "----------------"})

                return result_segments
    except Exception as e:  # noqa: BLE001
        _log(f"⚠️ 获取聊天记录失败：{e}")
    return result_segments


def _extract_image_info(message: list[dict] | str | Any) -> list[dict[str, str]]:
    """从 OneBot11 message 数组提取图片信息列表
    返回 [{"url": "...", "file": "..."}, ...]
    url: QQ CDN 链接（通常需要鉴权）
    file: NapCat 本地缓存标识，用于调用 /get_image API
    """
    if not isinstance(message, list):
        return []
    images: list[dict[str, str]] = []
    for seg in message:
        if not isinstance(seg, dict):
            continue
        if seg.get("type", "") != "image":
            continue
        d = seg.get("data", {})
        if not isinstance(d, dict):
            d = {}
        _log(f"🔍 image 段 data：{d}")
        url = str(d.get("url", "") or "")
        file_val = str(d.get("file", "") or "")
        if url or file_val:
            _log(f"🖼️ 图片 url={url[:80] if url else 'N/A'}... file={file_val[:50] if file_val else 'N/A'}...")
            images.append({"url": url, "file": file_val})
    return images


def _extract_segments(message: list[dict] | str | Any) -> list[dict[str, Any]]:
    """从 OneBot11 message 数组提取有序消息段列表
    返回格式: [{"type": "text", "text": "hello"}, {"type": "image", "url": "...", "file": "..."}, ...]
    保留原始 text/image 交错顺序
    """
    if isinstance(message, str):
        message = _parse_cq_code(message)
    if not isinstance(message, list):
        return []

    segments: list[dict[str, Any]] = []
    for seg in message:
        if not isinstance(seg, dict):
            continue
        t = seg.get("type", "")
        d = seg.get("data", {})
        if not isinstance(d, dict):
            d = {}

        if t == "text":
            txt = d.get("text", "") or ""
            if txt:
                segments.append({"type": "text", "text": txt})
        elif t == "image":
            url = str(d.get("url", "") or "")
            file_val = str(d.get("file", "") or "")
            if url or file_val:
                segments.append({"type": "image", "url": url, "file": file_val})
        elif t == "at":
            qq = d.get("qq", "")
            name = d.get("name", "")
            segments.append({"type": "text", "text": f"@{name or qq}" if (qq or name) else "@某人"})
        elif t == "reply":
            segments.append({"type": "text", "text": "[引用]"})
        elif t == "face":
            segments.append({"type": "text", "text": f"[表情{d.get('id', '')}]"})
        elif t == "file":
            segments.append({"type": "text", "text": f"[文件:{d.get('name', '')}]"})
        elif t == "record":
            segments.append({"type": "text", "text": "[语音]"})
        elif t == "video":
            segments.append({"type": "text", "text": "[视频]"})
        elif t == "forward":
            fid = d.get("id", "") or d.get("forward_id", "") or ""
            if not fid:
                for key in list(d.keys()):
                    val = d.get(key)
                    if isinstance(val, str) and val and len(val) > 5:
                        fid = val
                        break
            segments.append({"type": "forward", "id": fid})
    return segments


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


async def _fetch_image_data(image_info: dict[str, str]) -> bytes | None:
    """获取图片二进制数据
    优先级：NapCat /get_image API → base64:// → 本地文件 → HTTP 下载（带 UA）
    """
    url = image_info.get("url", "")
    file_val = image_info.get("file", "")
    _log(f"🔍 开始获取图片：url={url[:60] if url else 'N/A'} file={file_val[:60] if file_val else 'N/A'}")

    # 1) NapCat /get_image API（POST JSON + Bearer token）
    # NapCat 返回格式: {"status":"ok", "data": {"file":"本地路径", "url":"CDN链接"}}
    if file_val:
        try:
            api_url = f"{config.NAPCAT_API_URL}/get_image"
            headers = {}
            if config.NAPCAT_TOKEN:
                headers["Authorization"] = f"Bearer {config.NAPCAT_TOKEN}"
            # 缩短超时：NapCat 内部 downloadRichMedia 容易超时，快速失败回退到 HTTP
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(api_url, json={"file": file_val}, headers=headers)
                content_type = resp.headers.get("content-type", "")
                _log(f"🐱 NapCat /get_image：status={resp.status_code} ct={content_type}")

                if resp.status_code == 200 and "json" in content_type:
                    j = resp.json()
                    if j.get("status") == "ok" and j.get("data"):
                        napcat_data = j["data"]
                        # data 是 dict: {"file": "C:/path/xxx.jpg", "url": "https://..."}
                        if isinstance(napcat_data, dict):
                            local_file = napcat_data.get("file", "")
                            if local_file:
                                try:
                                    with open(local_file, "rb") as f:
                                        data = f.read()
                                    _log(f"📂 NapCat /get_image 本地文件，大小 {len(data)} 字节")
                                    return data
                                except Exception:
                                    pass
                            cdn_url = napcat_data.get("url", "")
                            if cdn_url:
                                try:
                                    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                                        r2 = await c.get(cdn_url)
                                        if "json" not in r2.headers.get("content-type", ""):
                                            _log(f"🌐 NapCat CDN 下载，大小 {len(r2.content)} 字节")
                                            return r2.content
                                except Exception:
                                    pass
                        # data 是 string: base64
                        elif isinstance(napcat_data, str):
                            b64_str = napcat_data
                            if ";base64," in b64_str:
                                b64_str = b64_str.split(";base64,", 1)[1]
                            data = base64.b64decode(b64_str)
                            _log(f"📦 NapCat /get_image base64，大小 {len(data)} 字节")
                            return data
        except httpx.TimeoutException:
            _log(f"⚠️ NapCat /get_image 超时（8s），快速回退到其他方式")
        except Exception as e:  # noqa: BLE001
            _log(f"⚠️ NapCat /get_image 异常：{e}")

    # 2) base64:// 前缀 → 直接解码
    if file_val and file_val.startswith("base64://"):
        try:
            data = base64.b64decode(file_val[len("base64://"):])
            _log(f"📦 base64:// 解码成功，大小 {len(data)} 字节")
            return data
        except Exception as e:  # noqa: BLE001
            _log(f"⚠️ base64:// 解码失败：{e}")

    # 3) file:/// 前缀 → 读取本地文件
    if file_val and file_val.startswith("file:///"):
        try:
            with open(file_val[len("file:///"):], "rb") as f:
                data = f.read()
            _log(f"📂 读取本地文件，大小 {len(data)} 字节")
            return data
        except Exception as e:  # noqa: BLE001
            _log(f"⚠️ 读取本地文件失败：{e}")

    # 4) HTTP URL 下载（带浏览器 UA 和 Referer，QQ CDN 可能需要）
    if url and (url.startswith("http://") or url.startswith("https://")):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://qq.com/",
            }
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                ct = resp.headers.get("content-type", "")
                _log(f"🌐 HTTP 下载：status={resp.status_code} ct={ct} size={len(resp.content)}")
                if "json" not in ct and len(resp.content) > 100:
                    return resp.content
        except Exception as e:  # noqa: BLE001
            _log(f"⚠️ HTTP 下载失败：{e}")

    # 5) 裸路径当本地文件
    if file_val and not file_val.startswith(("http://", "https://", "base64://", "file:///")):
        try:
            with open(file_val, "rb") as f:
                data = f.read()
            _log(f"📂 读取本地路径，大小 {len(data)} 字节")
            return data
        except Exception as e:  # noqa: BLE001
            _log(f"⚠️ 读取本地路径失败：{e}")

    _log("❌ 所有方式均无法获取图片")
    return None


async def _send_image_to_wechat(image_info: dict[str, str]) -> tuple[bool, str]:
    """获取图片并以企业微信 image 消息类型发送"""
    if "请粘贴你的KEY" in config.WECHAT_WEBHOOK_URL:
        _log("❌ Webhook URL 还没填好，请编辑 config.py 的 WECHAT_WEBHOOK_URL")
        return False, "webhook_not_configured"

    image_data = await _fetch_image_data(image_info)
    if not image_data:
        _log("❌ 所有方式均无法获取图片")
        return False, "fetch_failed"

    # 企业微信图片限制 2MB
    if len(image_data) > 2 * 1024 * 1024:
        _log(f"❌ 图片超过 2MB 限制（{len(image_data)} 字节），跳过")
        return False, "image_too_large"

    b64 = base64.b64encode(image_data).decode("utf-8")
    md5 = hashlib.md5(image_data).hexdigest()

    payload = {
        "msgtype": "image",
        "image": {
            "base64": b64,
            "md5": md5,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(config.WECHAT_WEBHOOK_URL, json=payload)
            data = resp.json()
            _log(f"📸 企业微信图片响应：errcode={data.get('errcode')} errmsg={data.get('errmsg')}")
            if data.get("errcode", 0) != 0:
                err = f"企业微信错误：errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
                _log(f"❌ {err}")
                return False, err
            return True, "ok"
    except httpx.TimeoutException:
        _log("❌ 发送图片到企业微信超时")
        return False, "timeout"
    except Exception as e:  # noqa: BLE001
        _log(f"❌ 发送图片异常：{e}")
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

    # 同时处理接收的消息（message）和自己发送的消息（message_sent）
    if post_type not in ("message", "message_sent"):
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

    # 提取有序消息段（保留 text/image 交错顺序）
    raw_message = data.get("message", "")
    if config.DEBUG:
        _log(f"📝 原始 message（前500字符）：{str(raw_message)[:500]}")
    segments = _extract_segments(raw_message)

    # 处理合并转发：展开为有序段，保持text/image交错顺序
    final_segments: list[dict[str, Any]] = []
    all_images: list[dict[str, str]] = []

    for seg in segments:
        if seg["type"] == "forward":
            fid = seg.get("id", "")
            if not fid:
                _log("⚠️ forward 段无有效 id，跳过")
                continue
            _log(f"📋 检测到合并转发，id={fid[:16]}...，正在获取内容...")
            forward_segments = await _fetch_forward_content(fid)
            if forward_segments:
                _log(f"📋 聊天记录展开为 {len(forward_segments)} 个有序段")
                # 直接展开到 final_segments，并收集图片
                for fseg in forward_segments:
                    final_segments.append(fseg)
                    if fseg["type"] == "image":
                        all_images.append({"url": fseg.get("url", ""), "file": fseg.get("file", "")})
        else:
            final_segments.append(seg)
            if seg["type"] == "image":
                all_images.append({"url": seg.get("url", ""), "file": seg.get("file", "")})

    # 检查是否有可转发内容
    text_only = "".join(s.get("text", "") for s in final_segments if s["type"] == "text")
    if not text_only and not all_images:
        _log("ℹ️ 消息无可转发内容，丢弃")
        return JSONResponse({"status": "empty_text"})

    # 关键词过滤
    if not _match_keywords(text_only):
        return JSONResponse({"status": "filtered_keyword"})

    # 限流
    if _rate_limited():
        return JSONResponse({"status": "rate_limited"})

    # 构建发言人/群名前缀
    group_name = data.get("group_name") or (f"群{group_id}" if group_id else "群")
    nickname = (
        sender.get("card")
        or sender.get("nickname")
        or (f"用户{user_id}" if user_id else "某人")
    )
    prefix = f"【{group_name}】{nickname}：\n" if config.ADD_SENDER_PREFIX else ""

    # 并行下载所有图片
    if all_images:
        _log(f"🔄 开始并行下载 {len(all_images)} 张图片...")
        fetch_tasks = [_fetch_image_data(img) for img in all_images]
        image_data_list = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        # 记录下载结果
        success_count = sum(1 for d in image_data_list if isinstance(d, bytes) and len(d) > 0)
        _log(f"✅ 图片下载完成：成功 {success_count}/{len(all_images)}")
    else:
        image_data_list = []

    # 按顺序交错发送 text 和 image
    send_ok = True
    img_idx = 0
    first_text = True

    # 如果只有图片没有文本，先发一条带前缀的文本
    if not text_only and all_images and prefix:
        ok, reason = await _send_to_wechat(prefix.rstrip())
        if ok:
            _log(f"✅ 前缀发送成功")
        else:
            send_ok = False
        first_text = False

    for seg in final_segments:
        if seg["type"] == "text":
            txt = seg["text"]
            if not txt:
                continue
            # 第一段文本加前缀
            if first_text and prefix:
                txt = prefix + txt
                first_text = False
            ok, reason = await _send_to_wechat(txt)
            if ok:
                _log(f"✅ 文本转发成功 [{txt[:30].replace(chr(10), ' ')}...]")
            else:
                send_ok = False

        elif seg["type"] == "image":
            if img_idx >= len(image_data_list):
                break
            img_data = image_data_list[img_idx]
            img_idx += 1

            if not isinstance(img_data, bytes) or len(img_data) == 0:
                _log(f"❌ 图片 {img_idx} 下载失败，跳过")
                continue

            # 企业微信图片限制 2MB
            if len(img_data) > 2 * 1024 * 1024:
                _log(f"❌ 图片超过 2MB 限制（{len(img_data)} 字节），跳过")
                continue

            # 构建并发送图片
            b64 = base64.b64encode(img_data).decode("utf-8")
            md5 = hashlib.md5(img_data).hexdigest()
            payload = {
                "msgtype": "image",
                "image": {"base64": b64, "md5": md5},
            }
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(config.WECHAT_WEBHOOK_URL, json=payload)
                    data_resp = resp.json()
                    _log(f"📸 企业微信图片响应：errcode={data_resp.get('errcode')} errmsg={data_resp.get('errmsg')}")
                    if data_resp.get("errcode", 0) == 0:
                        _log(f"✅ 图片 {img_idx} 转发成功")
                    else:
                        _log(f"❌ 图片 {img_idx} 转发失败：errcode={data_resp.get('errcode')}")
                        send_ok = False
            except Exception as e:
                _log(f"❌ 发送图片异常：{e}")
                send_ok = False

    return JSONResponse({"status": "ok" if send_ok else "send_failed"})


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
