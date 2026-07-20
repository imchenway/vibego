"""Telegram 专用代理与受保护 DNS 配置。"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from types import MethodType
from typing import Awaitable, Callable, Mapping, Optional
from urllib.parse import urlparse

import aiohttp
from aiohttp_socks import ProxyConnector


TELEGRAM_API_HOST = "api.telegram.org"
_SUPPORTED_PROXY_SCHEMES = {"http", "socks4", "socks5"}
_DOH_ENDPOINTS = (
    "https://dns.google/resolve",
    "https://cloudflare-dns.com/dns-query",
)


class TelegramProxyConfigError(ValueError):
    """Telegram 代理配置缺失或格式不合法。"""


class TelegramProxyDNSError(RuntimeError):
    """无法通过受保护 DNS 解析 Telegram Bot API。"""


@dataclass(frozen=True)
class ResolvedTelegramProxy:
    """一次 Telegram 代理解析结果。"""

    url: Optional[str]
    source: Optional[str]
    is_socks5: bool


def _read_macos_system_proxy() -> str:
    """读取 macOS 当前系统代理，不读取终端代理环境变量。"""

    if sys.platform != "darwin":
        return ""
    executable = shutil.which("scutil") or "/usr/sbin/scutil"
    try:
        completed = subprocess.run(
            [executable, "--proxy"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _system_socks5_url(raw: str) -> str:
    """从 scutil 输出中提取启用的 SOCKS5 主机和用户端口。"""

    values: dict[str, str] = {}
    for key in ("SOCKSEnable", "SOCKSProxy", "SOCKSPort"):
        match = re.search(rf"^\s*{key}\s*:\s*(.*?)\s*$", raw, flags=re.MULTILINE)
        if match:
            values[key] = match.group(1)

    if values.get("SOCKSEnable") != "1":
        raise TelegramProxyConfigError("macOS 系统 SOCKS5 代理未启用")

    host = values.get("SOCKSProxy", "").strip()
    port_raw = values.get("SOCKSPort", "").strip()
    if not host or not port_raw.isdigit() or not 1 <= int(port_raw) <= 65535:
        raise TelegramProxyConfigError("macOS 系统 SOCKS5 主机或端口无效")

    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"socks5://{url_host}:{port_raw}"


def _validate_proxy_url(url: str) -> str:
    """校验完整代理 URL；主机和端口必须由用户显式提供。"""

    parsed = urlparse(url)
    if parsed.scheme.lower() not in _SUPPORTED_PROXY_SCHEMES:
        raise TelegramProxyConfigError(
            "TELEGRAM_PROXY 协议无效，仅支持 http、socks4、socks5"
        )
    if not parsed.hostname:
        raise TelegramProxyConfigError("TELEGRAM_PROXY 缺少代理主机")
    try:
        port = parsed.port
    except ValueError as exc:
        raise TelegramProxyConfigError("TELEGRAM_PROXY 端口无效") from exc
    if port is None:
        raise TelegramProxyConfigError("TELEGRAM_PROXY 必须包含用户指定的端口")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise TelegramProxyConfigError("TELEGRAM_PROXY 不能包含路径、查询参数或片段")
    return url


def resolve_telegram_proxy(
    environ: Optional[Mapping[str, str]] = None,
    *,
    system_proxy_reader: Optional[Callable[[], str]] = None,
) -> ResolvedTelegramProxy:
    """解析 Telegram 专用代理；空值直连，且不继承终端代理。"""

    source_env = os.environ if environ is None else environ
    configured = source_env.get("TELEGRAM_PROXY", "").strip()
    if not configured:
        return ResolvedTelegramProxy(url=None, source=None, is_socks5=False)

    if configured.lower() == "system":
        reader = system_proxy_reader or _read_macos_system_proxy
        url = _system_socks5_url(reader())
        return ResolvedTelegramProxy(url=url, source="macOS SOCKS5", is_socks5=True)

    url = _validate_proxy_url(configured)
    return ResolvedTelegramProxy(
        url=url,
        source="TELEGRAM_PROXY",
        is_socks5=urlparse(url).scheme.lower() == "socks5",
    )


async def resolve_telegram_api_ipv4_via_doh(proxy_url: str) -> str:
    """通过同一代理访问 DoH，避免本机污染 DNS 影响 Bot API。"""

    errors: list[str] = []
    for endpoint in _DOH_ENDPOINTS:
        connector = ProxyConnector.from_url(proxy_url)
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(
                    endpoint,
                    params={"name": TELEGRAM_API_HOST, "type": "A"},
                    headers={"accept": "application/dns-json"},
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
            for answer in payload.get("Answer", []):
                if answer.get("type") != 1:
                    continue
                candidate = str(answer.get("data", "")).strip()
                address = ipaddress.ip_address(candidate)
                if address.version == 4:
                    return candidate
            errors.append(f"{urlparse(endpoint).hostname}: 无 A 记录")
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            AttributeError,
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            errors.append(f"{urlparse(endpoint).hostname}: {type(exc).__name__}")

    detail = "; ".join(errors) or "无可用 DoH 服务"
    raise TelegramProxyDNSError(f"无法解析 {TELEGRAM_API_HOST}: {detail}")


async def install_telegram_api_dns_override(
    proxy_url: Optional[str],
    *,
    resolver: Callable[[str], Awaitable[str]] = resolve_telegram_api_ipv4_via_doh,
) -> Optional[str]:
    """为 SOCKS5 会话安装仅作用于 Telegram Bot API 的 IPv4 解析。"""

    if not proxy_url or urlparse(proxy_url).scheme.lower() != "socks5":
        return None

    resolved_ip = await resolver(proxy_url)
    try:
        address = ipaddress.ip_address(resolved_ip)
    except ValueError as exc:
        raise TelegramProxyDNSError("Telegram Bot API 受保护 DNS 返回了无效地址") from exc
    if address.version != 4:
        raise TelegramProxyDNSError("Telegram Bot API 受保护 DNS 未返回 IPv4 地址")

    loop = asyncio.get_running_loop()
    original = getattr(loop, "_vibego_original_getaddrinfo", loop.getaddrinfo)
    setattr(loop, "_vibego_original_getaddrinfo", original)

    async def guarded_getaddrinfo(
        self,
        host,
        port,
        *,
        family=0,
        type=0,
        proto=0,
        flags=0,
    ):
        normalized_host = str(host).rstrip(".").lower()
        if normalized_host == TELEGRAM_API_HOST:
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (resolved_ip, port),
                )
            ]
        return await original(
            host,
            port,
            family=family,
            type=type,
            proto=proto,
            flags=flags,
        )

    loop.getaddrinfo = MethodType(guarded_getaddrinfo, loop)
    return resolved_ip


def configure_aiogram_session(session, proxy_url: Optional[str]) -> None:
    """为 aiogram 连接器配置 IPv4；SOCKS5 使用已保护的本地解析。"""

    if not proxy_url:
        return
    connector_init = session._connector_init  # type: ignore[attr-defined]
    connector_init.update({"family": socket.AF_INET, "ttl_dns_cache": 60})
    if urlparse(proxy_url).scheme.lower() == "socks5":
        connector_init["rdns"] = False
