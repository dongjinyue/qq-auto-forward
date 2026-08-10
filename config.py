# config.py
"""QQ转发到企业微信 - 配置文件
本地测试和云服务器部署都用这个文件，只改里面的值即可
"""

# ============ NapCat HTTP API 地址 ============
# NapCat 自身的 HTTP API 端口（不是事件上报端口），用于获取图片等资源
# 通常在 NapCat 配置中设置，默认为 http://127.0.0.1:3000
NAPCAT_API_URL = "http://127.0.0.1:3000"
# NapCat HTTP API 鉴权 token（和 NapCat 配置里的 token 一致）
NAPCAT_TOKEN = "请填入你的NapCat Token"

# ============ 企业微信 Webhook URL ============
# 粘贴你从企业微信消息推送复制的完整URL
# 格式示例：https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# TODO: 本地测试前必须先填好这个！
WECHAT_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=请粘贴你的KEY"

# ============ QQ群白名单 ============
# 只转发这些群的消息（填群号，可多个）；留空 [] 表示转发所有群
# 本地测试时 test_local.py 里的模拟群号要能匹配上
TARGET_GROUP_IDS: list[int] = [
    # 123456789,   # 示例：替换为你要转发的QQ群号
    # 987654321,
]

# ============ 关键词过滤（可选） ============
# 只转发包含以下任一关键词的消息；留空 [] 表示不过滤，全部转发
KEYWORDS: list[str] = [
    # "报名", "通知", "重要",
]

# ============ 消息类型白名单 ============
SUPPORTED_MSG_TYPES = ["text", "image", "at", "reply"]

# ============ 行为开关 ============
# 是否在转发消息前加上群名和发言人前缀
ADD_SENDER_PREFIX = True
# 忽略机器人自己发的消息
IGNORE_SELF_MESSAGE = True

# ============ 频率限制 ============
# 企业微信消息推送每分钟最多20条，留点余量
MAX_MSG_PER_MINUTE = 18

# ============ 本地调试 ============
# True 时会打印更详细的日志，部署到服务器后可以改 False
DEBUG = True
