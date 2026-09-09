"""QQ 合并转发消息的回归测试。"""

import unittest
from collections import deque
from unittest.mock import patch

import main


class _FakeResponse:
    """模拟 NapCat 的 HTTP 响应。"""

    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return {"status": "ok", "data": {"messages": self._data}}


class _FakeAsyncClient:
    """根据合并转发 ID 返回外层或内层聊天记录。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, json, headers):
        forward_id = json.get("id") or json.get("message_id")
        messages = {
            "outer-forward": [
                {
                    "sender": {"nickname": "外层用户"},
                    "content": [
                        {
                            "type": "forward",
                            "data": {"id": "inner-forward"},
                        }
                    ],
                }
            ],
            "inner-forward": [
                {
                    "sender": {"nickname": "内层用户"},
                    "content": [
                        {"type": "text", "data": {"text": "内层真实内容"}}
                    ],
                }
            ],
        }
        return _FakeResponse(messages[forward_id])


class FetchForwardContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_expands_nested_forward_content(self):
        """外层聊天记录中的内层聊天记录也应展开真实内容。"""

        # 关闭调试日志，避免 Windows GBK 终端干扰测试结果。
        with (
            patch("main.httpx.AsyncClient", _FakeAsyncClient),
            patch("main.config.DEBUG", False),
        ):
            segments = await main._fetch_forward_content("outer-forward")

        forwarded_text = "".join(
            segment.get("text", "")
            for segment in segments
            if segment.get("type") == "text"
        )

        self.assertIn("内层用户: 内层真实内容", forwarded_text)
        self.assertNotIn("外层用户:  [聊天记录]", forwarded_text)

    async def test_does_not_drop_messages_after_the_thirtieth_item(self):
        """长聊天记录应完整展开，不应在第 30 条后截断。"""

        messages = [
            {
                "sender": {"nickname": f"用户{index}"},
                "content": [
                    {"type": "text", "data": {"text": f"消息{index}"}}
                ],
            }
            for index in range(35)
        ]

        class _LongForwardClient(_FakeAsyncClient):
            async def post(self, url, json, headers):
                return _FakeResponse(messages)

        with (
            patch("main.httpx.AsyncClient", _LongForwardClient),
            patch("main.config.DEBUG", False),
        ):
            segments = await main._fetch_forward_content("long-forward")

        forwarded_text = "".join(
            segment.get("text", "")
            for segment in segments
            if segment.get("type") == "text"
        )
        self.assertIn("用户34: 消息34", forwarded_text)
        self.assertNotIn("已省略", forwarded_text)


class MessageBatchingTests(unittest.TestCase):
    def test_merges_adjacent_text_without_losing_content(self):
        """相邻文本应合并发送，图片仍保持原有位置。"""

        source = [
            {"type": "text", "text": "第一条\n"},
            {"type": "text", "text": "第二条\n"},
            {"type": "image", "url": "https://example.com/a.jpg", "file": ""},
            {"type": "text", "text": "第三条\n"},
        ]

        result = main._batch_text_segments(source)

        self.assertEqual(
            result,
            [
                {"type": "text", "text": "第一条\n第二条\n"},
                {"type": "image", "url": "https://example.com/a.jpg", "file": ""},
                {"type": "text", "text": "第三条\n"},
            ],
        )

    def test_splits_text_by_utf8_bytes_without_truncation(self):
        """超长中文文本应分块，而不是截断后丢失剩余内容。"""

        content = "消息" * 1000
        result = main._batch_text_segments(
            [{"type": "text", "text": content}],
            max_bytes=100,
        )

        chunks = [segment["text"] for segment in result]
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.encode("utf-8")) <= 100 for chunk in chunks))
        self.assertEqual("".join(chunks), content)


class RateLimitTests(unittest.TestCase):
    def test_counts_each_webhook_request_and_returns_required_wait(self):
        """频率限制应针对每次 Webhook 请求，额度用完后给出等待时间。"""

        timestamps = deque([100.0, 110.0])

        with patch("main.config.MAX_MSG_PER_MINUTE", 2):
            wait_seconds = main._reserve_rate_limit_slot(timestamps, now=120.0)

        self.assertEqual(wait_seconds, 40.0)
        self.assertEqual(list(timestamps), [100.0, 110.0])

    def test_reserves_slot_after_old_request_expires(self):
        """滑动窗口中的旧请求过期后，新请求应占用一个新额度。"""

        timestamps = deque([100.0, 110.0])

        with patch("main.config.MAX_MSG_PER_MINUTE", 2):
            wait_seconds = main._reserve_rate_limit_slot(timestamps, now=160.1)

        self.assertEqual(wait_seconds, 0.0)
        self.assertEqual(list(timestamps), [110.0, 160.1])


if __name__ == "__main__":
    unittest.main()
