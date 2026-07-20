# Vibego 运行上下文

Vibego 将 Telegram 控制面与本机项目 worker 连接起来。本词汇表用于固定网络配置和运行身份的业务含义。

## Language

**Telegram 专用代理**：
仅供 Vibego 的 Telegram Master 与 worker 访问 Bot API 的代理配置，其选择不受终端通用代理影响。
_Avoid_：终端代理、Mono 端口

**系统 SOCKS5 模式**：
由用户操作系统当前启用的 SOCKS5 主机和端口共同定义的 Telegram 专用代理模式。
_Avoid_：默认 SOCKS 端口、固定端口模式

**直连模式**：
Telegram 专用代理为空时，不经过代理访问 Bot API 的运行模式。
_Avoid_：自动回退模式
