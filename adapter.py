"""
XiaoZhi Pro Platform Adapter for Hermes Agent.

WS Client that connects to the XiaoZhi Pro hermes-server.py,
relaying messages between XiaoZhi hardware devices and the Hermes agent.

Configuration (token only, via environment or config.yaml)::

    platforms:
      xiaozhi_pro:
        enabled: true
        extra:
          token: "your_api_key"

Or via environment variable:
    XIAOZHI_PRO_TOKEN
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None  # type: ignore[assignment]

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.config import Platform

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

_WS_URL = "wss://upldapuxqzmh.sealosbja.site/api/v1/hermes_server/ws"
_AUTH_TIMEOUT = 10
_WS_HEARTBEAT = 30
_WS_TIMEOUT = 15
_RECONNECT_DELAY = 3
_MAX_RECONNECT_DELAY = 60
_DEDUP_TTL = 300


class XiaoZhiAdapter(BasePlatformAdapter):

    def __init__(self, config):
        super().__init__(config, Platform("xiaozhi_pro"))
        self._token = config.extra.get("token", os.getenv("XIAOZHI_PRO_TOKEN", ""))
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws = None
        self._receive_task: Optional[asyncio.Task] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._user_id: str = ""
        self._seen_messages: Dict[str, float] = {}
        self._last_device_id: Dict[str, str] = {}  # user_id → device_id

    async def connect(self) -> bool:
        if not AIOHTTP_AVAILABLE:
            logger.warning("[XiaoZhiPro] aiohttp not installed. Run: pip install aiohttp")
            return False
        logger.info(f"[XiaoZhiPro] Connecting to {_WS_URL} ...")
        self._running = True
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                _WS_URL, heartbeat=_WS_HEARTBEAT, timeout=_WS_TIMEOUT,
            )
            auth_ok = await self._do_auth()
            if not auth_ok:
                await self._session.close()
                self._running = False
                return False
            logger.info(f"[XiaoZhiPro] Auth OK, user_id={self._user_id}")
            self._loop_task = asyncio.create_task(self._reconnect_loop())
            return True
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
            logger.warning(f"[XiaoZhiPro] Connect failed: {exc}")
            await self._session.close()
            self._running = False
            return False

    async def disconnect(self) -> None:
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()

    async def _reconnect_loop(self) -> None:
        """后台重连循环：WS 断开后自动重连。"""
        delay = _RECONNECT_DELAY
        while self._running:
            # 先启动接收循环（首次由 connect() 已建立连接）
            try:
                self._receive_task = asyncio.create_task(self._receive_loop())
                await self._receive_task
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error(f"[XiaoZhiPro] Receive loop error: {exc}", exc_info=True)

            if not self._running:
                break

            # 断开后重连
            logger.info(f"[XiaoZhiPro] WS disconnected, reconnecting in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, _MAX_RECONNECT_DELAY)

            try:
                self._ws = await self._session.ws_connect(
                    _WS_URL, heartbeat=_WS_HEARTBEAT, timeout=_WS_TIMEOUT,
                )
                auth_ok = await self._do_auth()
                if not auth_ok:
                    continue
                logger.info(f"[XiaoZhiPro] Reconnected OK, user_id={self._user_id}")
                delay = _RECONNECT_DELAY  # 重连成功，重置延迟
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                logger.warning(f"[XiaoZhiPro] Reconnect failed: {exc}")

    async def _do_auth(self) -> bool:
        try:
            await self._ws.send_json({"type": "auth", "token": self._token})
            async with asyncio.timeout(_AUTH_TIMEOUT):
                msg = await self._ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "auth_ok":
                        self._user_id = data.get("user_id", "")
                        return True
                    logger.error(f"[XiaoZhiPro] Auth failed: {data}")
                else:
                    logger.error(f"[XiaoZhiPro] Expected auth_ok, got: {msg.type}")
                return False
        except asyncio.TimeoutError:
            logger.error("[XiaoZhiPro] Auth timeout")
            return False
        except Exception as exc:
            logger.error(f"[XiaoZhiPro] Auth error: {exc}")
            return False

    async def _receive_loop(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_message(json.loads(msg.data))
                else:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[XiaoZhiPro] Receive loop error: {exc}", exc_info=True)

    async def _handle_ws_message(self, data: Dict[str, Any]) -> None:
        msg_type = data.get("type")
        if msg_type == "message":
            await self._handle_inbound(data)
        elif msg_type == "response":
            logger.debug(f"[XiaoZhiPro] Got response: {data.get('text', '')[:50]}")
        elif msg_type == "ping":
            await self._ws.send_json({"type": "pong", "timestamp": int(time.time() * 1000)})
        else:
            logger.warning(f"[XiaoZhiPro] Unknown message type: {msg_type}")

    async def _handle_inbound(self, data: Dict[str, Any]) -> None:
        message_id = data.get("message_id", "")
        user_id = data.get("user_id", "")
        device_id = data.get("device_id", "")
        text = data.get("text", "")

        if message_id and self._is_duplicate(message_id):
            return

        # 每个 user_id 只有一个 session，device_id 仅记录用于回复时路由投递
        session_key = f"xiaozhi:{user_id}"
        if device_id:
            self._last_device_id[user_id] = device_id

        source = self.build_source(
            chat_id=session_key,
            chat_name=user_id,
            chat_type="dm",
            user_id=user_id,
            user_name=user_id,
            message_id=message_id,
        )

        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=data,
            message_id=message_id,
            timestamp=datetime.now(),
        )

        await self.handle_message(event)

    def _is_duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False
        now = time.time()
        expired = [k for k, v in self._seen_messages.items() if now - v > _DEDUP_TTL]
        for k in expired:
            del self._seen_messages[k]
        if message_id in self._seen_messages:
            return True
        self._seen_messages[message_id] = now
        return False

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if not self._ws or self._ws.closed:
            return SendResult(success=False, error="[XiaoZhiPro] WS not connected")
        # chat_id = "xiaozhi:<user_id>"
        # device_id: 优先从 metadata 取（由 Hermes 传入），否则查 _last_device_id，最后广播
        device_id = ""
        if metadata and metadata.get("device_id"):
            device_id = metadata["device_id"]
        else:
            user_id = chat_id[8:] if chat_id.startswith("xiaozhi:") else ""
            device_id = self._last_device_id.get(user_id, "")
        msg_id = int(time.time() * 1000)
        try:
            await self._ws.send_json({
                "type": "response",
                "text": content,
                "device_id": device_id,
                "message_id": msg_id,
            })
            return SendResult(success=True, message_id=str(msg_id))
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        name = chat_id[8:] if chat_id.startswith("xiaozhi:") else chat_id
        return {"name": name, "type": "dm"}

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if not self._ws or self._ws.closed:
            return
        user_id = chat_id[8:] if chat_id.startswith("xiaozhi:") else ""
        device_id = self._last_device_id.get(user_id, "")
        try:
            await self._ws.send_json({"type": "typing", "device_id": device_id})
        except Exception:
            pass


def _check_requirements() -> bool:
    """只检查依赖库是否可用；token 验证留给 connect() 阶段。

    旧版只看环境变量，导致 config.yaml 中配置了 token 也不生效。
    现在不再强制要求环境变量，config.yaml 的 extra.token 同样有效。
    """
    return AIOHTTP_AVAILABLE


def _env_enablement() -> dict | None:
    """从环境变量读取配置，注入 PlatformConfig.extra。

    在 adapter 构建之前被 gateway 配置加载阶段调用，
    使得 is_connected 探针和 gateway status 能正确反映配置状态。
    """
    token = os.getenv("XIAOZHI_PRO_TOKEN", "").strip()
    if not token:
        return None
    seed: dict = {"token": token}
    return seed


def register(ctx):
    ctx.register_platform(
        name="xiaozhi_pro",
        label="小智Pro",
        adapter_factory=lambda cfg: XiaoZhiAdapter(cfg),
        check_fn=_check_requirements,
        is_connected=lambda cfg: bool(
            # 用户显式写了 enabled: false 时，shared-key bridge 会在 extra
            # 中设置 _enabled_explicit=True。此时应返回 False，防止
            # _apply_env_overrides 把 enabled 强制覆盖为 True。
            cfg.extra.get("_enabled_explicit") is not True
            and (cfg.extra.get("token") or os.getenv("XIAOZHI_PRO_TOKEN"))
        ),
        env_enablement_fn=_env_enablement,
        required_env=[],
        install_hint="Requires aiohttp: pip install aiohttp",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via XiaoZhi Pro hardware device. "
            "Keep messages concise. The device displays text and may read it aloud. "
            "Avoid markdown formatting."
        ),
    )
