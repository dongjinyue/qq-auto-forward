"""QQ 合并转发消息的回归测试。"""

import unittest
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


if __name__ == "__main__":
    unittest.main()
