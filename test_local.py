# test_local.py
"""本地端到端测试脚本
无需启动 NapCat，直接模拟 NapCat 的 OneBot11 HTTP 上报格式，
验证：1) 中转服务能接收  2) 正确过滤/格式化  3) 成功推送到企业微信

使用步骤：
  1) 先填好 config.py 的 WECHAT_WEBHOOK_URL
  2) 终端1启动中转服务： uvicorn main:app --reload --host 127.0.0.1 --port 8080
  3) 终端2运行本脚本：   python test_local.py
  4) 观察企业微信群里有没有收到测试消息，以及终端1的日志
"""
from __future__ import annotations

import json
import sys
import time

import requests

ENDPOINT = "http://127.0.0.1:8080/onebot/event"
HEALTH_URL = "http://127.0.0.1:8080/health"

# 模拟的 QQ 小号 ID
SELF_QQ = 10001
# 模拟的群号（如果 config.TARGET_GROUP_IDS 填了，要把这个群号也加进去）
TEST_GROUP_ID = 123456789
TEST_GROUP_NAME = "测试QQ群"
# 模拟发送消息的QQ用户
TEST_USER_ID = 20001
TEST_USER_NICK = "张三"


def check_health() -> bool:
    print("=" * 60)
    print("① 检查中转服务是否启动")
    try:
        r = requests.get(HEALTH_URL, timeout=3)
        r.raise_for_status()
        data = r.json()
        print(f"   ✅ 服务在线：{data}")
        if not data.get("webhook_configured"):
            print("   ⚠️  config.py 的 WECHAT_WEBHOOK_URL 还没填好，先去填！")
        return True
    except requests.ConnectionError:
        print("   ❌ 连接失败！请先运行：")
        print("        uvicorn main:app --reload --host 127.0.0.1 --port 8080")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ 异常：{e}")
        return False


def build_group_text_event(text: str, group_id: int = TEST_GROUP_ID, user_id: int = TEST_USER_ID) -> dict:
    """构造一个 OneBot11 群文本消息事件（NapCat上报格式）"""
    return {
        "post_type": "message",
        "message_type": "group",
        "time": int(time.time()),
        "self_id": SELF_QQ,
        "sub_type": "normal",
        "group_id": group_id,
        "group_name": TEST_GROUP_NAME,
        "user_id": user_id,
        "sender": {
            "user_id": user_id,
            "nickname": TEST_USER_NICK,
            "card": TEST_USER_NICK,  # 群名片
            "role": "member",
        },
        "message_id": int(time.time() * 1000),
        "message": [
            {"type": "text", "data": {"text": text}},
        ],
        "raw_message": text,
    }


def build_mixed_event() -> dict:
    """含 @某人 + 文本 + 图片的复合消息"""
    return {
        "post_type": "message",
        "message_type": "group",
        "time": int(time.time()),
        "self_id": SELF_QQ,
        "sub_type": "normal",
        "group_id": TEST_GROUP_ID,
        "group_name": TEST_GROUP_NAME,
        "user_id": 20002,
        "sender": {
            "user_id": 20002,
            "nickname": "李四",
            "card": "李四",
            "role": "member",
        },
        "message": [
            {"type": "at", "data": {"qq": "20001", "name": "张三"}},
            {"type": "text", "data": {"text": " 看下这张截图 "}},
            {"type": "image", "data": {"url": "https://example.com/a.jpg", "file": "a.jpg"}},
        ],
    }


def build_other_group_event() -> dict:
    """来自非白名单群的消息（应被过滤）"""
    return {
        "post_type": "message",
        "message_type": "group",
        "time": int(time.time()),
        "self_id": SELF_QQ,
        "sub_type": "normal",
        "group_id": 999999999,
        "group_name": "无关群",
        "user_id": 30000,
        "sender": {"user_id": 30000, "nickname": "路人甲", "card": "路人甲", "role": "member"},
        "message": [{"type": "text", "data": {"text": "这条消息应该被过滤，因为群不在白名单"}}],
    }


def build_self_message_event() -> dict:
    """机器人小号自己发的消息（应被忽略）"""
    return {
        "post_type": "message",
        "message_type": "group",
        "time": int(time.time()),
        "self_id": SELF_QQ,
        "sub_type": "normal",
        "group_id": TEST_GROUP_ID,
        "group_name": TEST_GROUP_NAME,
        "user_id": SELF_QQ,  # 和 self_id 相同
        "sender": {"user_id": SELF_QQ, "nickname": "小号自己", "card": "小号", "role": "admin"},
        "message": [{"type": "text", "data": {"text": "我自己发的不应该被转发"}}],
    }


def send_event(name: str, event: dict) -> None:
    print("-" * 60)
    print(f"② 发送测试：{name}")
    try:
        r = requests.post(ENDPOINT, json=event, timeout=5)
        r.raise_for_status()
        data = r.json()
        print(f"   响应：{json.dumps(data, ensure_ascii=False)}")
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ 请求失败：{e}")


def main() -> int:
    if not check_health():
        return 1

    send_event(
        "普通文本消息（核心功能）",
        build_group_text_event(f"这是本地测试消息 {time.strftime('%H:%M:%S')}，如果收到就说明转发链路通啦~"),
    )
    time.sleep(1)

    send_event(
        "含@和图片的复合消息",
        build_mixed_event(),
    )
    time.sleep(1)

    send_event(
        "非白名单群（应该被过滤，响应 filtered_group 或 ignored）",
        build_other_group_event(),
    )
    time.sleep(1)

    send_event(
        "机器人小号自己发的消息（应该被忽略，响应 ignore_self）",
        build_self_message_event(),
    )

    print("=" * 60)
    print("③ 测试消息已全部发送")
    print("   👉 请查看企业微信群，确认至少收到了前两条消息")
    print("   👉 同时查看 uvicorn 终端，应有清晰的日志")
    print("   👉 如消息未收到：")
    print("      - 检查 WECHAT_WEBHOOK_URL 是否填对")
    print("      - 确认 TARGET_GROUP_IDS 为空，或包含 123456789")
    print("      - 确认 KEYWORDS 为空（全部放行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
