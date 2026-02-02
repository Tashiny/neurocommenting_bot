# modules/security.py
import asyncio
import random
import re
import hashlib
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


class SecurityManager:
    """
    Менеджер безопасности для одного Telegram-аккаунта.

    Отвечает за:
    - "человеческие" задержки (anti-spam)
    - обработку FloodWait
    - базовую валидацию постов (фильтры)
    - логирование активности и истории комментариев
    - метрики/статистика для админки
    """

    def __init__(self, account_name: str, settings):
        """
        Args:
            account_name: имя аккаунта (используется в именах файлов логов/истории)
            settings: configparser.ConfigParser (обычно settings.ini)
        """
        self.account_name = account_name
        self.settings = settings

        self.activity_log: List[Dict] = []
        self.flood_wait_history: List[Dict] = []
        self.comment_history: List[Dict] = []
        self.start_time = datetime.now()

        # Гарантируем директории
        os.makedirs("logs", exist_ok=True)
        os.makedirs("data", exist_ok=True)

        # Загружаем историю из файла (если была)
        self._load_history()

    # -----------------------------
    # Persistence
    # -----------------------------
    def _history_path(self) -> str:
        return os.path.join("data", f"security_{self.account_name}.json")

    def _load_history(self) -> None:
        """Загрузка истории активности/комментариев из data/security_<account>.json"""
        history_file = self._history_path()
        if not os.path.exists(history_file):
            return

        try:
            with open(history_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.activity_log = data.get("activity_log", []) or []
            self.flood_wait_history = data.get("flood_wait_history", []) or []
            self.comment_history = data.get("comment_history", []) or []

        except Exception as e:
            print(f"⚠️ Ошибка загрузки истории для {self.account_name}: {e}")

    def _save_history(self) -> None:
        """Сохранение истории активности/комментариев в data/security_<account>.json"""
        history_file = self._history_path()

        try:
            data = {
                "account_name": self.account_name,
                "last_update": datetime.now().isoformat(),
                "activity_log": self.activity_log[-1000:],
                "flood_wait_history": self.flood_wait_history[-100:],
                "comment_history": self.comment_history[-500:],
                "total_comments": len(self.comment_history),
                "total_activity": len(self.activity_log),
            }

            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        except Exception as e:
            print(f"⚠️ Ошибка сохранения истории для {self.account_name}: {e}")

    # -----------------------------
    # Delays / anti-spam
    # -----------------------------
    async def human_delay(
        self,
        min_seconds: Optional[int] = None,
        max_seconds: Optional[int] = None,
        action_type: str = "default",
    ) -> None:
        """
        Имитация человеческих задержек.

        min_seconds/max_seconds можно передать явно, либо они берутся из профиля и settings.ini.
        Для комментариев учитываются Behavior/min_comment_delay и Behavior/max_comment_delay.
        """
        delay_profiles = {
            "subscription": (15, 45),
            "comment": (30, 180),
            "scrolling": (3, 10),
            "typing": (1, 5),
            "reading": (10, 60),
            "like": (1, 3),
            "default": (10, 60),
        }

        # Профиль по умолчанию
        profile_min, profile_max = delay_profiles.get(action_type, delay_profiles["default"])

        if min_seconds is None:
            min_seconds = profile_min
        if max_seconds is None:
            max_seconds = profile_max

        # Переопределение из settings.ini для комментариев
        if action_type == "comment":
            cfg_min = self.settings.getint("Behavior", "min_comment_delay", fallback=min_seconds)
            cfg_max = self.settings.getint("Behavior", "max_comment_delay", fallback=max_seconds)
            if cfg_min is not None and cfg_max is not None:
                min_seconds, max_seconds = int(cfg_min), int(cfg_max)

        # Защита от некорректных значений
        if min_seconds < 0:
            min_seconds = 0
        if max_seconds < min_seconds:
            max_seconds = min_seconds

        delay = random.randint(int(min_seconds), int(max_seconds))

        print(f"   ⏳ [{self.account_name}] Задержка {action_type}: {delay} секунд")
        self.log_activity(f"Начало задержки {action_type}: {delay}с")
        await asyncio.sleep(delay)
        self.log_activity(f"Конец задержки {action_type}")

    async def random_delay(
        self,
        min_seconds: Optional[int] = None,
        max_seconds: Optional[int] = None,
        action_type: str = "default",
    ) -> None:
        """
        Алиас для совместимости: в части модулей используется random_delay() вместо human_delay().
        """
        await self.human_delay(min_seconds=min_seconds, max_seconds=max_seconds, action_type=action_type)

    # -----------------------------
    # FloodWait
    # -----------------------------
    async def handle_flood_wait(self, error: Exception) -> bool:
        """
        Обработка FloodWait от Telegram (Telethon FloodWaitError имеет поле .seconds).
        Возвращает True, если ожидание выполнено и можно продолжать.
        """
        try:
            wait_time = int(getattr(error, "seconds", 60) or 60)

            flood_record = {
                "timestamp": datetime.now().isoformat(),
                "wait_time": wait_time,
                "account": self.account_name,
                "reason": str(error),
            }
            self.flood_wait_history.append(flood_record)

            print(f"\n   🚫 FLOODWAIT ДЛЯ {self.account_name}")
            print(f"   ⚠️ Причина: {error}")
            print(f"   ⏳ Ожидание: {wait_time} секунд ({wait_time/60:.1f} минут)")
            print(f"   🕒 Время: {datetime.now().strftime('%H:%M:%S')}")

            # Показываем прогресс (не спамим)
            last_print = 0
            for passed in range(wait_time):
                remaining = wait_time - passed
                if remaining <= 10 or remaining % 60 == 0:
                    if remaining != last_print:
                        print(f"   ⏳ Осталось: {remaining} секунд")
                        last_print = remaining
                await asyncio.sleep(1)

            print("   ✅ FloodWait завершен, продолжаем работу")
            self.log_activity(f"FloodWait завершен: {wait_time} секунд")
            self._save_history()
            return True

        except Exception as e:
            print(f"   ❌ Ошибка обработки FloodWait: {e}")
            await asyncio.sleep(60)
            return True

    # -----------------------------
    # Content filters
    # -----------------------------
    def validate_post(self, post_text: str) -> Tuple[bool, str]:
        """
        Валидация поста перед комментированием.

        Возвращает (is_valid, reason). Настройки берутся из settings.ini.
        """
        # Проверяем настройку отключения всех фильтров
        disable_all_filters = self.settings.get("Content", "disable_all_filters", fallback="no").lower() in ("yes", "true", "1", "on", "да", "вкл")
        if disable_all_filters:
            return True, "Все фильтры отключены"
        
        if not post_text:
            return False, "Пустой пост"

        text = post_text.strip()
        post_lower = text.lower()

        # Минимальная длина поста
        min_length = self.settings.getint("Content", "min_post_length", fallback=20)
        if len(text) < int(min_length):
            return False, f"Пост слишком короткий ({len(text)} < {min_length})"

        # Максимальная длина поста
        max_length = self.settings.getint("Content", "max_post_length", fallback=5000)
        if max_length and len(text) > int(max_length):
            return False, f"Пост слишком длинный ({len(text)} > {max_length})"

        # Черный список слов
        blacklist_str = self.settings.get("Content", "blacklist_words", fallback="")
        blacklist = [w.strip().lower() for w in blacklist_str.split(",") if w.strip()]
        if blacklist:
            for w in blacklist:
                if w in post_lower:
                    return False, f"Содержит запрещенное слово: {w}"

        # Пропуск "рекламных/спонсорских" постов
        skip_sponsored_raw = self.settings.get("Behavior", "skip_sponsored_posts", fallback="yes").strip().lower()
        skip_sponsored = skip_sponsored_raw in ("1", "true", "yes", "y", "on")
        if skip_sponsored:
            spam_indicators = [
                "реклама", "спонсор", "партнер", "промокод", "скидка",
                "акция", "распродажа", "купить", "продать",
                "заказать", "оформить", "доставка",
                "sponsored", "ad", "#ad"
            ]
            for indicator in spam_indicators:
                if indicator in post_lower:
                    return False, "Рекламный пост"

        # Пропуск постов со ссылками
        skip_links_raw = self.settings.get("Behavior", "skip_posts_with_links", fallback="yes").strip().lower()
        skip_links = skip_links_raw in ("1", "true", "yes", "y", "on")
        if skip_links:
            url_patterns = [
                r"https?://\S+",
                r"t\.me/\S+",
                r"bit\.ly/\S+",
                r"tinyurl\.com/\S+",
                r"@\w+"
            ]
            for pattern in url_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return False, "Пост содержит ссылки"

        return True, "Пост валиден"


    def log_activity(self, action: str, details: Optional[Dict] = None) -> None:
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "account": self.account_name,
            "action": action,
            "details": details or {},
        }
        self.activity_log.append(log_entry)

        # файл-лог активности
        log_file = os.path.join("logs", f"activity_{self.account_name}.log")
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            details_str = f" ({json.dumps(details, ensure_ascii=False)})" if details else ""
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {action}{details_str}\n")
        except Exception as e:
            print(f"⚠️ Ошибка записи лога: {e}")

        # периодически сохраняем историю
        if len(self.activity_log) % 50 == 0:
            self._save_history()

    def log_comment(self, post_id: str, channel: str, comment: str) -> None:
        comment_record = {
            "timestamp": datetime.now().isoformat(),
            "account": self.account_name,
            "post_id": str(post_id),
            "channel": str(channel),
            "comment": (comment or "")[:100],
            "full_comment_hash": hashlib.md5((comment or "").encode("utf-8")).hexdigest(),
        }
        self.comment_history.append(comment_record)

        self.log_activity(f"Отправлен комментарий в {channel}", {"post_id": str(post_id), "comment_length": len(comment or "")})
        self._save_history()

    def check_daily_limit(self) -> int:
        """
        Возвращает количество комментариев, отправленных сегодня.
        Лимит берётся из settings.ini: Behavior/daily_comment_limit
        """
        today = datetime.now().date()
        today_comments = [
            c for c in self.comment_history
            if datetime.fromisoformat(c["timestamp"]).date() == today
        ]
        return len(today_comments)

    def get_stats(self) -> Dict:
        now = datetime.now()
        today = now.date()

        today_comments = [
            c for c in self.comment_history
            if datetime.fromisoformat(c["timestamp"]).date() == today
        ]

        week_ago = now - timedelta(days=7)
        week_activity = [
            a for a in self.activity_log
            if datetime.fromisoformat(a["timestamp"]) > week_ago
        ]

        day_ago = now - timedelta(hours=24)
        recent_floods = [
            f for f in self.flood_wait_history
            if datetime.fromisoformat(f["timestamp"]) > day_ago
        ]

        return {
            "account_name": self.account_name,
            "uptime_hours": (now - self.start_time).total_seconds() / 3600,
            "total_comments": len(self.comment_history),
            "comments_today": len(today_comments),
            "total_activity": len(self.activity_log),
            "activity_last_7_days": len(week_activity),
            "flood_wait_last_24h": len(recent_floods),
            "last_flood_wait": self.flood_wait_history[-1]["timestamp"] if self.flood_wait_history else None,
            "last_comment": self.comment_history[-1]["timestamp"] if self.comment_history else None,
        }