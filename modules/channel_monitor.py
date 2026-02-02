# modules/channel_monitor.py
from __future__ import annotations

import asyncio
import inspect
import random
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from telethon import TelegramClient, events, functions, types, utils
from telethon.errors import (
    ChatAdminRequiredError, 
    FloodWaitError, 
    RPCError, 
    UserBannedInChannelError, 
    ChatWriteForbiddenError,
    MsgIdInvalidError
)

from modules.comment_generator import CommentGenerator

ChannelRef = Union[str, int]

class ChannelMonitor:
    """
    Мониторинг каналов v6.2 (Debug Version).
    
    Исправления:
    1. Логирование ошибок при отправке (почему не уходит коммент).
    2. Попытка вступить в группу комментариев, если нет прав писать.
    3. Улучшенная обработка comment_to.
    """

    def __init__(
        self,
        account_manager,
        on_comment_sent: Optional[Callable[[str, str, str, str], Awaitable[None]]] = None,
    ):
        self.account_manager = account_manager
        self.comment_generator = CommentGenerator()
        self.on_comment_sent = on_comment_sent

        self.processed_posts: Dict[Tuple[str, int, int], float] = {}
        self._processed_counter: int = 0
        self.channel_info_cache: Dict[Tuple[str, int], Dict[str, Any]] = {}
        self._send_locks: Dict[int, asyncio.Lock] = {}
        self._last_send_ts: Dict[int, float] = {}

    def reload_settings(self):
        """ПРОБРОС ПЕРЕЗАГРУЗКИ: Монитор -> Генератор"""
        if hasattr(self, 'comment_generator'):
            self.comment_generator.reload_settings()

    # --- HELPERS ---
    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()

    def _cleanup_processed(self) -> None:
        self._processed_counter += 1
        if self._processed_counter % 1000 != 0: return
        now = self._now()
        cutoff = now - (24 * 60 * 60)
        for k, ts in list(self.processed_posts.items()):
            if ts < cutoff: self.processed_posts.pop(k, None)
        while len(self.processed_posts) > 50000:
            try: self.processed_posts.pop(next(iter(self.processed_posts)), None)
            except Exception: break

    @staticmethod
    def _get_channel_peer_id(msg: Any, event: events.NewMessage.Event) -> int:
        try:
            if msg is not None and getattr(msg, "peer_id", None) is not None:
                return int(utils.get_peer_id(msg.peer_id))
        except Exception: pass
        return int(getattr(event, "chat_id", 0) or 0)

    # --- SECURITY FILTERS ---
    def _passes_content_filters(self, post_text: str, security) -> bool:
        # Проверяем отключение
        disable_all = security.settings.get("Content", "disable_all_filters", fallback="no").lower() in ("yes", "true", "1", "on")
        if disable_all: return True
        
        text = (post_text or "").strip()
        if not text: return False

        min_len = security.settings.getint("Content", "min_post_length", fallback=50)
        max_len = security.settings.getint("Content", "max_post_length", fallback=5000)

        if len(text) < min_len: return False
        if len(text) > max_len: text = text[:max_len]

        blacklist_raw = security.settings.get("Content", "blacklist_words", fallback="")
        blacklist = [w.strip().lower() for w in blacklist_raw.split(",") if w.strip()]
        lower = text.lower()
        if blacklist and any(w in lower for w in blacklist): return False

        skip_sponsored = security.settings.get("Behavior", "skip_sponsored_posts", fallback="yes").strip().lower() in ("yes", "true", "1", "on")
        if skip_sponsored:
            spam_indicators = ["реклама", "спонсор", "партнер", "промокод", "скидка", "акция", "распродажа", "купить", "продать", "заказать", "#ad"]
            if any(ind in lower for ind in spam_indicators): return False

        skip_links = security.settings.get("Behavior", "skip_posts_with_links", fallback="no").strip().lower() in ("yes", "true", "1", "on")
        if skip_links:
            url_patterns = [r"http://", r"https://", r"t\.me/", r"@\w+"]
            if any(re.search(p, text, re.IGNORECASE) for p in url_patterns): return False

        return True

    # --- QUEUE ---
    async def _send_with_inter_account_queue(self, channel_peer_id: int, security, send_coro: Callable[[], Awaitable[bool]]) -> bool:
        lock = self._send_locks.setdefault(channel_peer_id, asyncio.Lock())
        min_gap = security.settings.getint("Behavior", "inter_account_delay_min", fallback=10)
        max_gap = security.settings.getint("Behavior", "inter_account_delay_max", fallback=15)
        
        async with lock:
            now = self._now()
            last = self._last_send_ts.get(channel_peer_id, 0.0)
            if last > 0:
                wait = random.randint(min_gap, max_gap) - (now - last)
                if wait > 0: await asyncio.sleep(wait)
            
            ok = await send_coro()
            if ok:
                self._last_send_ts[channel_peer_id] = self._now()
            return ok

    # --- TELETHON HELPERS ---
    async def get_channel_info(self, client: TelegramClient, channel: ChannelRef, account_name: str) -> Dict[str, Any]:
        entity = await client.get_entity(channel)
        peer_id = int(utils.get_peer_id(entity))
        cache_key = (account_name, peer_id)
        
        # Обновляем кэш реже, но если нет linked_chat, пробуем получить снова
        if cache_key in self.channel_info_cache: 
            info = self.channel_info_cache[cache_key]
            if info.get("linked_chat_id"):
                return info

        linked_chat_id = None
        linked_chat = None
        try:
            full = await client(functions.channels.GetFullChannelRequest(channel=entity))
            linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
            if linked_chat_id: linked_chat = await client.get_entity(linked_chat_id)
        except Exception: pass

        info = {"entity": entity, "peer_id": peer_id, "linked_chat_id": linked_chat_id, "linked_chat": linked_chat, "source_url": str(channel)}
        self.channel_info_cache[cache_key] = info
        return info

    async def _try_join(self, client, entity):
        try: await client(functions.channels.JoinChannelRequest(channel=entity))
        except Exception: pass

    # --- SEND ---
    async def send_comment(self, client: TelegramClient, channel_entity: Any, post_id: int, linked_chat: Optional[Any], comment_text: str, security) -> bool:
        """
        Попытка отправить комментарий с подробным логированием ошибок.
        """
        err_prefix = f"[{security.account_name}] Отправка не удалась:"

        # 1. Основной способ: comment_to (Telethon сам находит тред)
        try:
            await client.send_message(channel_entity, comment_text, comment_to=post_id)
            return True
        except (UserBannedInChannelError, ChatWriteForbiddenError) as e:
            # Если нас забанили или запретили писать, пропуск
            print(f"   ❌ {err_prefix} Нет прав писать (бан/ограничение): {e}")
            security.log_activity(f"Ошибка отправки: нет прав/бан: {e}")
            return False
        except Exception as e:
            print(f"   ⚠️ {err_prefix} Ошибка метода comment_to: {type(e).__name__}: {e}")
            # Продолжаем пробовать другие методы

        # 2. Если есть привязанная группа, пробуем через неё
        if linked_chat:
            # 2.1. Сначала попробуем вступить в группу (частая причина ошибок - "не участник")
            try:
                await self._try_join(client, linked_chat)
            except Exception: pass

            try:
                # Получаем сообщение обсуждения
                discussion = await client(functions.messages.GetDiscussionMessageRequest(peer=channel_entity, msg_id=post_id))
                reply_to_msg_id = discussion.messages[0].id if discussion.messages else None
                
                # Отправляем ответ в группу
                await client.send_message(linked_chat, comment_text, reply_to=reply_to_msg_id)
                return True
            except Exception as e:
                print(f"   ⚠️ {err_prefix} Ошибка отправки через GetDiscussion: {type(e).__name__}: {e}")
                
                # 3. Последняя надежда: просто сообщение в чат
                try:
                    await client.send_message(linked_chat, comment_text)
                    print(f"   ⚠️ {err_prefix} Отправлено просто в чат (без реплая)")
                    return True
                except Exception as e2:
                    print(f"   ❌ {err_prefix} Полный провал отправки: {e2}")
                    security.log_activity(f"FATAL: Не удалось отправить комментарий ни одним способом. Err: {e2}")
                    return False
        
        print(f"   ❌ {err_prefix} Не найден linked_chat или все методы не сработали.")
        return False

    # --- RUN ---
    async def run(self, account_name: str, channels: List[ChannelRef], stop_event: Optional[asyncio.Event] = None) -> None:
        client = await self.account_manager.connect(account_name)
        security = self.account_manager.get_security_manager(account_name)
        
        resolved_peers = []
        for ch in channels:
            try:
                info = await self.get_channel_info(client, ch, account_name)
                await self._try_join(client, info["entity"])
                resolved_peers.append(info["entity"])
            except Exception: pass

        if not resolved_peers: 
            print(f"❌ [{account_name}] Не удалось разрешить ни один канал из списка.")
            return

        @client.on(events.NewMessage(chats=resolved_peers))
        async def _on_new_post(event):
            msg = event.message
            if not msg or getattr(msg, "out", False): return
            msg_id = int(getattr(msg, "id", 0))
            peer_id = self._get_channel_peer_id(msg, event)
            
            key = (account_name, peer_id, msg_id)
            if key in self.processed_posts: return
            self.processed_posts[key] = self._now()
            self._cleanup_processed()

            text = (msg.message or "").strip()
            if not self._passes_content_filters(text, security): return

            # Получаем инфо и проверяем наличие комментариев
            info = await self.get_channel_info(client, peer_id, account_name)
            if not info.get("linked_chat_id"): 
                # Можно логировать, что комментарии отключены
                # security.log_activity("Пропуск: у канала нет привязанного чата")
                return

            # Задержка после поста
            delay = random.randint(
                security.settings.getint("Behavior", "post_comment_delay_min", fallback=0),
                security.settings.getint("Behavior", "post_comment_delay_max", fallback=60)
            )
            if delay > 0: 
                # print(f"   ⏳ [{account_name}] Ждем {delay}с после выхода поста...")
                await asyncio.sleep(delay)

            # Генерация (уже с новыми настройками)
            comment = await self.comment_generator.generate_comment(
                post_text=text,
                account_name=account_name,
                post_key=f"{peer_id}:{msg_id}"
            )
            
            if not comment: return

            # Человеческая пауза перед набором
            await security.random_delay(
                security.settings.getint("Behavior", "min_comment_delay", fallback=10),
                security.settings.getint("Behavior", "max_comment_delay", fallback=60),
                "comment"
            )

            async def _do_send():
                return await self.send_comment(client, info["entity"], msg_id, info["linked_chat"], comment, security)

            ok = await self._send_with_inter_account_queue(peer_id, security, _do_send)
            
            if ok:
                security.log_comment(str(msg_id), str(peer_id), comment)
                if self.on_comment_sent:
                    try: await self.on_comment_sent(account_name, str(info["source_url"]), str(msg_id), comment)
                    except Exception: pass

        if stop_event:
            await asyncio.wait([client.disconnected, asyncio.create_task(stop_event.wait())], return_when=asyncio.FIRST_COMPLETED)
            if stop_event.is_set(): await client.disconnect()
        else:
            await client.run_until_disconnected()