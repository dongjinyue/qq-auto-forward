# QQ 自动转发到微信

监听指定 QQ 群的消息，实时自动转发到个人微信。基于 NapCat + FastAPI + 企业微信消息推送 Webhook，支持文本、图片、@、合并转发等消息类型。

> 个人微信无需安装任何插件，通过企业微信消息同步机制即可在个人微信收到通知。

---

## 功能特性

- **文本转发**：QQ 群聊文本消息实时转发到微信
- **图片转发**：自动下载 QQ 图片并以企业微信图片消息推送
- **合并转发展开**：自动调用 NapCat API 展开合并聊天记录，逐条转发
- **群白名单**：只转发指定群，避免无关群消息刷屏
- **关键词过滤**：可选，只转发包含关键词的消息
- **消息格式化**：自动添加群名、发言人前缀
- **限流保护**：滑动窗口限流，避免触发企业微信 20 条/分钟限制
- **防回环**：自动忽略机器人小号自己发的消息
- **图文交错发送**：保持原始消息中文本和图片的顺序

---

## 工作原理

```
QQ群消息 → NapCat(小号) → HTTP上报 → Python中转服务(FastAPI) → 企业微信消息推送Webhook → 同步到个人微信
```

| 组件 | 职责 |
|------|------|
| NapCat | QQ 协议端，登录小号，接收 QQ 消息并按 OneBot 11 上报 |
| FastAPI 中转服务 | 接收上报、过滤、格式化、调用企业微信 Webhook |
| 企业微信消息推送 | 接收 Webhook 请求，把消息发到群里并同步到个人微信 |

---

## 前置条件

| 项目 | 说明 | 必须 |
|------|------|------|
| QQ 小号 | 专用小号，**不要用主号** | ✅ |
| 企业微信 | 个人注册（免费，无需营业执照），用于创建消息推送 | ✅ |
| Python 3.10+ | 运行中转服务 | ✅ |
| Node.js 18+ | 运行 NapCat（仅服务器部署需要） | ✅ |
| 云服务器（可选） | 24 小时常驻运行，本地测试不需要 | ❌ |

---

## 快速开始（本地测试）

### 第 1 步：注册企业微信并创建消息推送

1. 访问 [work.weixin.qq.com](https://work.weixin.qq.com)，点击「立即注册」，企业类型选「个人/团队」
2. 创建一个群（一个人也能建群）
3. 进入群聊 → 右上角「...」→「消息推送」→「添加」→「新创建一个消息推送」
   > 注：旧版企业微信中此功能名为「群机器人」，新版已更名为「消息推送」，功能完全相同。
4. 复制得到的 **Webhook URL**，格式如：
   ```
   https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```
5. 企业微信 App →「我」→「设置」→「消息通知」→ 开启「企业微信消息同步到微信」

### 第 2 步：配置

编辑 [config.py](config.py)，填入你的 Webhook URL：

```python
WECHAT_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的KEY"
```

> 测试阶段 `TARGET_GROUP_IDS` 留空 `[]`，`KEYWORDS` 留空 `[]`，确保消息不被过滤。

### 第 3 步：安装依赖

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 第 4 步：启动中转服务

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8080
```

### 第 5 步：运行测试脚本

新开一个终端：

```powershell
venv\Scripts\Activate.ps1
python test_local.py
```

如果企业微信群收到测试消息，说明中转服务和企业微信链路已打通。

---

## 接入真实 QQ 消息

本地测试通过后，接入真实 QQ 消息只需部署 NapCat 并配置 HTTP 上报。

### 安装 NapCat

1. 前往 [NapCat Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载
   - Windows 本地推荐 **NapCat Desktop**（图形界面，扫码方便）
   - Linux 服务器推荐 **NapCat Shell**（无头运行）
2. 启动后用 QQ 小号扫码登录

### 配置 HTTP 上报

在 NapCat 的 OneBot 11 配置中设置：

| 配置项 | 值 |
|--------|-----|
| HTTP 客户端上报地址 | `http://127.0.0.1:8080/onebot/event` |
| 上报格式 | array |
| 开启 HTTP 上报 | 是 |
| 上报自己的消息 | 否 |

### 配置 NapCat HTTP API（图片转发需要）

图片转发功能需要调用 NapCat 的 HTTP API 下载图片，在 [config.py](config.py) 中配置：

```python
NAPCAT_API_URL = "http://127.0.0.1:3000"  # NapCat HTTP API 地址
NAPCAT_TOKEN = "你的NapCat Token"           # 和 NapCat 配置中的 token 一致
```

### 验证

在 QQ 小号所在的群里用另一个号发一条消息，企业微信群应收到转发。

---

## 配置说明

所有配置集中在 [config.py](config.py)，以下为各配置项说明：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WECHAT_WEBHOOK_URL` | 占位符 | 企业微信消息推送 Webhook URL |
| `TARGET_GROUP_IDS` | `[]` | QQ 群白名单，空数组=转发所有群 |
| `KEYWORDS` | `[]` | 关键词过滤，空数组=不过滤 |
| `ADD_SENDER_PREFIX` | `True` | 是否在消息前加群名和发言人 |
| `IGNORE_SELF_MESSAGE` | `True` | 是否忽略机器人自己发的消息 |
| `MAX_MSG_PER_MINUTE` | `18` | 每分钟最大转发条数（企业微信限制 20 条） |
| `NAPCAT_API_URL` | `http://127.0.0.1:3000` | NapCat HTTP API 地址（图片下载用） |
| `NAPCAT_TOKEN` | 占位符 | NapCat API 鉴权 Token |
| `DEBUG` | `True` | 调试模式，打印详细日志 |

---

## 服务器部署（24 小时运行）

### 1. 服务器初始化（Ubuntu 22.04）

```bash
apt update && apt -y upgrade
apt install -y git curl wget vim unzip build-essential

# Node.js 18（NapCat 依赖）
curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
apt install -y nodejs

# Python 3.10+
apt install -y python3 python3-pip python3-venv

# pm2（进程守护）
npm install -g pm2
```

### 2. 上传代码

```bash
mkdir -p /opt/qq-forward && cd /opt/qq-forward
git clone https://github.com/你的用户名/qq-forward.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

修改 `config.py` 填入服务器上的 Webhook URL 和群号。

### 3. 启动中转服务

```bash
pm2 start "venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080" --name qq-forward
pm2 save
pm2 startup  # 按提示执行输出的命令
```

### 4. 部署 NapCat

```bash
mkdir -p /opt/napcat && cd /opt/napcat
wget https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip
unzip NapCat.Shell.zip
```

首次启动扫码登录后，配置 HTTP 上报到 `http://127.0.0.1:8080/onebot/event`，然后用 pm2 守护：

```bash
pm2 start npm --name napcat -- run start
pm2 save
```

### 5. 健康检查（可选）

```bash
cat > /opt/qq-forward/check.sh <<'EOF'
#!/bin/bash
if ! curl -sf http://127.0.0.1:8080/health > /dev/null; then
    pm2 restart qq-forward
    echo "$(date '+%F %T') qq-forward 已重启" >> /var/log/qq-forward-check.log
fi
EOF
chmod +x /opt/qq-forward/check.sh
(crontab -l 2>/dev/null; echo "*/5 * * * * /opt/qq-forward/check.sh") | crontab -
```

---

## 日常运维

```bash
pm2 list                          # 查看进程状态
pm2 logs qq-forward --lines 200   # 查看中转服务日志
pm2 logs napcat --lines 200       # 查看 NapCat 日志
pm2 restart qq-forward            # 重启中转服务
pm2 restart napcat                # 重启 NapCat
curl http://127.0.0.1:8080/health # 健康检查
```

NapCat 掉线重登：

```bash
pm2 stop napcat
cd /opt/napcat
npm run start  # 输出二维码，扫码登录后 Ctrl+C
pm2 start napcat
```

---

## 常见问题

**Q：NapCat 过几天掉线怎么办？**
A：第三方协议的通病。按上方「NapCat 掉线重登」步骤操作。建议小号平时偶尔正常使用，降低风控概率。

**Q：能转发图片吗？**
A：能。需要配置 NapCat HTTP API（`NAPCAT_API_URL` 和 `NAPCAT_TOKEN`），程序会自动下载图片并以企业微信 image 消息发送。图片限制 2MB。

**Q：能转发合并聊天记录吗？**
A：能。程序会自动调用 NapCat 的 `/get_forward_msg` API 展开合并转发，逐条转发文本和图片。

**Q：QQ 群消息太多刷屏？**
A：配置 `KEYWORDS` 只转发关键词消息，或把不需要的群从 `TARGET_GROUP_IDS` 移除。

**Q：企业微信群只有我一个人行吗？**
A：行。一个人也能建群添加消息推送，消息会同步到你的个人微信。

**Q：服务器需要备案吗？**
A：不需要。本方案只用 IP 不用域名，不开放对外网站服务。

---

## 项目结构

```
.
├── main.py                           # 主程序（FastAPI 中转服务）
├── config.py                          # 配置文件（所有参数集中管理）
├── test_local.py                      # 本地测试脚本（无需 NapCat）
├── requirements.txt                   # Python 依赖
├── QQ自动转发到微信-方案文档.md          # 完整方案设计文档
├── 部署指南-本地测试+服务器迁移.md        # 详细部署指南
└── README.md                          # 本文件
```

---

## 详细文档

- [方案设计文档](QQ自动转发到微信-方案文档.md) — 包含架构设计、成本估算、风险分析、扩展规划
- [部署指南](部署指南-本地测试+服务器迁移.md) — 本地测试到服务器迁移的逐步指南

---

## License

[MIT](LICENSE)
