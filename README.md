# 小智Pro (XiaoZhi Pro) 平台插件

本插件将 Hermes Agent 连接到小智Pro——一个面向 IoT 设备的实时语音交互平台。通过此适配器，Hermes 可以通过 WebSocket 接收来自小智硬件设备的消息并发送回复。

## 架构

小智Pro 是**持久连接**通道。适配器与小智Pro服务端保持长连接 WebSocket，双向转发消息：

```
小智设备 ────> 小智Pro服务端 <──(WebSocket)──> Hermes (xiaozhi-pro 适配器)
                       │                      │
小智设备 <──── 小智Pro服务端 <──(WS 实时推送)────┘
```

- **入站**：服务端通过已建立的 WebSocket 推送消息（type `message`），适配器按 `message_id` 去重后派发 `MessageEvent` 到网关。
- **出站**：`send` 通过 WebSocket 发回 `response` 消息。当 `device_id` 非空时，服务端投递到指定设备；为空时广播该用户的所有设备。

## 配置

只需要一个凭证：[小智Pro平台](https://mkwyqeoebedx.sealosbja.site/)的 **API 密钥**。

### 第一步：获取 API 密钥

1. 登录小智Pro控制台
2. 进入「用户中心 → API Key」页面
3. 点击「创建密钥」，复制生成的密钥

⚠️ API 密钥是连接小智Pro的唯一凭证，请勿泄露。

### 第二步：安装插件

将插件下载到 Hermes 的平台插件目录（**目录名必须为 `xiaozhi_pro`，下划线，不能用连字符**）：

**方式 A：git clone**

```bash
git clone https://github.com/hougeai/xiaozhi-pro-hermes-plugin.git ~/.hermes/hermes-agent/plugins/platforms/xiaozhi_pro
```

**方式 B：下载压缩包**

1. 从 [GitHub Releases](https://github.com/hougeai/xiaozhi-pro-hermes-plugin/releases) 下载最新版压缩包
2. 解压到 `~/.hermes/hermes-agent/plugins/platforms/xiaozhi_pro/`

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/platforms/xiaozhi_pro
unzip xiaozhi-pro-hermes-plugin-*.zip -d ~/.hermes/hermes-agent/plugins/platforms/xiaozhi_pro
```

> ⚠️ 目录名必须是 `xiaozhi_pro`（Python 模块要求），否则 Hermes 无法加载插件。

### 第三步：配置

在 `~/.hermes/config.yaml` 中添加：

```yaml
platforms:
  xiaozhi_pro:
    extra:
      token: "你的API密钥"
```

> 本插件属于 bundled platform 插件，Hermes 会自动加载，**无需**在 `plugins.enabled` 中声明。
> 只要配置了 `token`（或设置了 `XIAOZHI_PRO_TOKEN` 环境变量），网关启动时就会自动连接。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `XIAOZHI_PRO_TOKEN` | — | 小智Pro服务端认证的 API 密钥 |

token 可以通过 config.yaml 的 `extra.token` 或环境变量 `XIAOZHI_PRO_TOKEN` 提供，任一有值即可。

### 第四步：重启网关

```bash
hermes gateway restart
```

启动后日志中应出现 `[XiaoZhiPro] Connecting to ...` 和 `[XiaoZhiPro] Auth OK`。

### 第五步：首次连接需要配对

服务端首次通过 xiaozhi_pro 通道连接 Hermes 会生成一个配对码，需在终端执行配对命令。配对成功后适配器即可接收来自已配对设备的消息。例如：

```bash
Hi~ I don't recognize you yet!
Here's your pairing code: B8XWG5HE
Ask the bot owner to run: hermes pairing approve xiaozhi_pro B8XWG5HE
```

## 禁用插件

本插件属于 bundled platform 插件，Hermes 启动时会自动加载并连接。**`plugins.enabled` 和 `enabled: false` 均无法禁用它**（这是 Hermes gateway 的设计：bundled platform 有凭证就自动启用）。

要禁用连接，必须**移除 token**：

```yaml
platforms:
  xiaozhi_pro:
    extra:
      # token: "你的API密钥"   ← 注释掉或删除即可禁用
```

同时确保环境变量 `XIAOZHI_PRO_TOKEN` 也未设置：

```bash
unset XIAOZHI_PRO_TOKEN
```

移除 token 后重启网关，插件将不会连接。

## 会话路由

每个 `user_id` 对应一个 session，`chat_id` 格式为 `xiaozhi:<user_id>`。

`device_id` 仅用于回复时的投递路由：有 `device_id` 则精确投递到该设备，无则广播该用户的所有设备。
