# modules/account_manager.py
"""
AccountManager — единая точка управления Telegram-аккаунтами (Telethon).

Ключевые принципы (под вашу текущую архитектуру):
1) Источник истины по аккаунтам — база данных / Telegram-бот (а accounts.txt — зеркало).
2) Аккаунт считается "валидным для работы", только если есть сохранённая сессия:
   sessions/<account_name>.session
3) AccountManager НЕ запрашивает коды/пароли в консоли. Если сессии нет или она не авторизована,
   нужно выполнить вход через Telegram-бот (FSM) и сохранить сессию.

Формат accounts.txt (зеркало):
    account_name:+79001234567
Пустые строки и комментарии (# ...) игнорируются.
"""

from __future__ import annotations

import asyncio
import configparser
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from modules.security import SecurityManager


@dataclass(frozen=True)
class AccountRecord:
    name: str
    phone: str


class AccountManager:
    def __init__(
        self,
        sessions_dir: str = "sessions",
        settings_path: str = "settings.ini",
        api_keys_path: str = "api_keys.txt",
        accounts_path: str = "accounts.txt",
    ):
        self.sessions_dir = sessions_dir
        self.settings_path = settings_path
        self.api_keys_path = api_keys_path
        self.accounts_path = accounts_path

        os.makedirs(self.sessions_dir, exist_ok=True)
        os.makedirs("logs", exist_ok=True)
        os.makedirs("data", exist_ok=True)

        self.settings = self._load_settings(self.settings_path)
        self.api_id, self.api_hash = self._load_telegram_api(self.api_keys_path, self.settings)

        # runtime caches
        self.clients: Dict[str, TelegramClient] = {}
        self.security_managers: Dict[str, SecurityManager] = {}

    # -----------------------------
    # Loading helpers
    # -----------------------------
    def _load_settings(self, path: str) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        if os.path.exists(path):
            cfg.read(path, encoding="utf-8")
        return cfg

    def _load_telegram_api(
        self,
        api_keys_path: str,
        settings: configparser.ConfigParser,
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Поддерживаем несколько источников:
        1) env: TELEGRAM_API_ID / TELEGRAM_API_HASH
        2) api_keys.txt: telegram_api_id / telegram_api_hash (с любыми пробелами вокруг '=')
        3) settings.ini: [Mistral] telegram_api_id / telegram_api_hash (как у вас сейчас)
        """
        env_id = os.getenv("TELEGRAM_API_ID")
        env_hash = os.getenv("TELEGRAM_API_HASH")
        if env_id and env_hash:
            try:
                return int(str(env_id).strip()), str(env_hash).strip()
            except Exception:
                pass

        # api_keys.txt
        api_id = None
        api_hash = None
        if os.path.exists(api_keys_path):
            try:
                with open(api_keys_path, "r", encoding="utf-8") as f:
                    for raw in f:
                        line = raw.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k == "telegram_api_id":
                            try:
                                api_id = int(v)
                            except Exception:
                                api_id = None
                        elif k == "telegram_api_hash":
                            api_hash = v
            except Exception:
                # silently fallback to settings.ini
                pass

        # settings.ini fallback
        if (api_id is None or not api_hash) and settings.has_section("Mistral"):
            try:
                if api_id is None:
                    v = settings.get("Mistral", "telegram_api_id", fallback="").strip()
                    if v:
                        api_id = int(v)
                if not api_hash:
                    api_hash = settings.get("Mistral", "telegram_api_hash", fallback="").strip() or None
            except Exception:
                pass

        return api_id, api_hash

    # -----------------------------
    # accounts.txt mirror (optional)
    # -----------------------------
    @staticmethod
    def normalize_phone(phone: str) -> Tuple[bool, str]:
        """Нормализация телефона. Возвращает (ok, phone_or_error)."""
        if not phone or not isinstance(phone, str):
            return False, "Номер телефона пустой"

        phone = phone.strip()
        cleaned = re.sub(r"[^\d\+]", "", phone)

        if not cleaned:
            return False, "Номер телефона не содержит цифр"

        # добавляем '+', если нет
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned

        if not re.match(r"^\+\d{11,15}$", cleaned):
            return False, f"Неверный формат номера: {cleaned}"

        return True, cleaned

    def load_accounts_from_txt(self) -> List[AccountRecord]:
        """Чтение accounts.txt. НЕ является источником истины, только зеркало."""
        if not os.path.exists(self.accounts_path):
            return []

        result: List[AccountRecord] = []
        try:
            with open(self.accounts_path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" not in line:
                        continue
                    name, phone = line.split(":", 1)
                    name = name.strip()
                    phone = phone.strip()
                    if "#" in phone:
                        phone = phone.split("#", 1)[0].strip()

                    if not name:
                        continue
                    ok, normalized = self.normalize_phone(phone)
                    if not ok:
                        # пропускаем мусорные строки, но не падаем
                        continue

                    result.append(AccountRecord(name=name, phone=normalized))
        except Exception:
            return []

        return result

    def write_accounts_to_txt(self, accounts: List[AccountRecord]) -> None:
        """Перезаписывает accounts.txt (зеркало)."""
        os.makedirs(os.path.dirname(self.accounts_path) or ".", exist_ok=True)
        with open(self.accounts_path, "w", encoding="utf-8") as f:
            for a in accounts:
                f.write(f"{a.name}:{a.phone}\n")

    # -----------------------------
    # sessions + clients
    # -----------------------------
    def session_path(self, account_name: str) -> str:
        return os.path.join(self.sessions_dir, f"{account_name}.session")

    def session_exists(self, account_name: str) -> bool:
        return os.path.exists(self.session_path(account_name))

    def get_security_manager(self, account_name: str) -> SecurityManager:
        sm = self.security_managers.get(account_name)
        if sm is None:
            sm = SecurityManager(account_name=account_name, settings=self.settings)
            self.security_managers[account_name] = sm
        return sm

    async def connect(self, account_name: str) -> TelegramClient:
        """
        Подключение к аккаунту по СУЩЕСТВУЮЩЕЙ сессии.

        Если сессии нет или она не авторизована — возвращает исключение, чтобы верхний уровень
        (бот) мог сообщить пользователю "нужно залогиниться/перелогиниться".
        """
        if account_name in self.clients:
            client = self.clients[account_name]
            if client.is_connected():
                return client

        if not self.api_id or not self.api_hash:
            raise RuntimeError("Не настроены telegram_api_id / telegram_api_hash (api_keys.txt или settings.ini)")

        spath = self.session_path(account_name)
        if not os.path.exists(spath):
            raise FileNotFoundError(
                f"Сессия не найдена: {spath}. Сначала выполните вход через Telegram-бот и сохраните сессию."
            )

        client = TelegramClient(spath, self.api_id, self.api_hash)
        security = self.get_security_manager(account_name)

        try:
            await client.connect()

            # Важно: AccountManager не запрашивает код/пароль. Только проверка.
            is_auth = await client.is_user_authorized()
            if not is_auth:
                await client.disconnect()
                raise PermissionError(
                    f"Сессия {account_name} не авторизована. Перелогиньте аккаунт через Telegram-бот."
                )

            self.clients[account_name] = client
            security.log_activity("Telethon client подключён по сохранённой сессии")
            return client

        except FloodWaitError as e:
            # централизованно учитываем floodwait
            try:
                await security.handle_flood_wait(e)
            except Exception:
                pass
            raise

        except RPCError as e:
            # типовые RPC ошибки Telethon
            security.log_activity(f"RPCError: {type(e).__name__}: {e}")
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                pass
            raise

        except Exception as e:
            security.log_activity(f"Ошибка connect(): {type(e).__name__}: {e}")
            try:
                if client.is_connected():
                    await client.disconnect()
            except Exception:
                pass
            raise

    async def disconnect(self, account_name: str) -> None:
        client = self.clients.get(account_name)
        if not client:
            return
        try:
            if client.is_connected():
                await client.disconnect()
        finally:
            self.clients.pop(account_name, None)

    async def disconnect_all(self) -> None:
        tasks = [self.disconnect(name) for name in list(self.clients.keys())]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.clients.clear()

    async def connect_many(self, account_names: List[str]) -> Dict[str, TelegramClient]:
        """
        Подключает несколько аккаунтов. Возвращает dict успешных подключений.
        Ошибки по отдельным аккаунтам не прерывают остальные.
        """
        result: Dict[str, TelegramClient] = {}
        for name in account_names:
            try:
                result[name] = await self.connect(name)
            except Exception:
                # намеренно проглатываем — верхний уровень может логировать отдельно
                continue
        return result

    def available_accounts(self) -> List[str]:
        """
        Аккаунты, которые "доступны" для работы (есть session-файл).
        Это полезно для UI: показывать только реально залогиненные аккаунты.
        """
        accounts = self.load_accounts_from_txt()
        return [a.name for a in accounts if self.session_exists(a.name)]

    def get_account_stats(self) -> List[Dict[str, Any]]:
        stats: List[Dict[str, Any]] = []
        for account_name in self.security_managers.keys():
            sm = self.security_managers.get(account_name)
            if sm:
                stats.append(sm.get_stats())
        return stats