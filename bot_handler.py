# bot_handler.py
import asyncio
import logging
import inspect
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, 
    CallbackQuery, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile
)
from aiogram.fsm.storage.memory import MemoryStorage
import sqlite3
import json
import os
import re
import random
import configparser
from typing import List, Dict, Any, Optional
from collections import Counter

from dataclasses import dataclass
from telethon import TelegramClient, utils
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
)

# Импорты модулей
try:
    from modules.account_manager import AccountManager
    from modules.channel_monitor import ChannelMonitor
except ImportError:
    from account_manager import AccountManager
    from channel_monitor import ChannelMonitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния для FSM
class Form(StatesGroup):
    waiting_for_phone = State()
    waiting_for_tg_code = State()
    waiting_for_2fa_password = State()
    waiting_for_api_id = State()
    waiting_for_api_hash = State()
    waiting_for_mistral_key = State()
    waiting_for_channel = State()
    waiting_for_account_name = State()
    editing_settings = State()
    waiting_for_setting_value = State()
    waiting_for_post_delay_min = State()
    waiting_for_post_delay_max = State()

# База данных
class SimpleDatabase:
    def __init__(self, db_path: str = 'neurocommenting.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS accounts (
                name TEXT PRIMARY KEY,
                phone TEXT,
                user_id INTEGER,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name TEXT,
                url TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name TEXT,
                channel_url TEXT,
                post_id TEXT,
                comment_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channel_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name TEXT NOT NULL,
                channel_url TEXT NOT NULL,
                channel_peer_id TEXT,
                analysis_data TEXT,
                posts_count INTEGER DEFAULT 0,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_name, channel_url)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                section TEXT,
                key TEXT,
                value TEXT,
                PRIMARY KEY (section, key)
            )
        ''')
        
        # Миграция
        try:
            cursor.execute("PRAGMA table_info(accounts)")
            cols = [row[1] for row in cursor.fetchall()]
            if "is_active" not in cols:
                cursor.execute("ALTER TABLE accounts ADD COLUMN is_active INTEGER DEFAULT 1")
                cursor.execute("UPDATE accounts SET is_active=1 WHERE is_active IS NULL")
        except Exception:
            pass

        conn.commit()
        conn.close()
    
    def add_user(self, user_id: int, username: str, first_name: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)', (user_id, username, first_name))
        conn.commit()
        conn.close()
    
    def user_exists(self, user_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def add_account(self, name: str, phone: str, user_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO accounts (name, phone, user_id) VALUES (?, ?, ?)', (name, phone, user_id))
        conn.commit()
        conn.close()
    
    def account_exists(self, name: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT name FROM accounts WHERE name = ?', (name,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def get_user_accounts(self, user_id: int) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM accounts WHERE user_id = ?', (user_id,))
        accounts = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return accounts
    
    def get_all_accounts(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM accounts')
        accounts = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return accounts
    
    def delete_account(self, name: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM accounts WHERE name = ?', (name,))
        cursor.execute('DELETE FROM channels WHERE account_name = ?', (name,))
        cursor.execute('DELETE FROM comments WHERE account_name = ?', (name,))
        conn.commit()
        conn.close()
    
    def add_channel(self, account_name: str, url: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO channels (account_name, url) VALUES (?, ?)', (account_name, url))
        conn.commit()
        conn.close()
    
    def get_account_channels(self, account_name: str) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM channels WHERE account_name = ?', (account_name,))
        channels = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return channels
    
    def delete_channel(self, channel_id: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM channels WHERE id = ?', (channel_id,))
        conn.commit()
        conn.close()
    
    def add_comment(self, account_name: str, channel_url: str, post_id: str, comment_text: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO comments (account_name, channel_url, post_id, comment_text) VALUES (?, ?, ?, ?)', (account_name, channel_url, post_id, comment_text))
        conn.commit()
        conn.close()
    
    def save_channel_analysis(self, account_name: str, channel_url: str, channel_peer_id: str, analysis_data: str, posts_count: int):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO channel_analysis 
            (account_name, channel_url, channel_peer_id, analysis_data, posts_count, analyzed_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (account_name, channel_url, channel_peer_id, analysis_data, posts_count))
        conn.commit()
        conn.close()
    
    def get_channel_analysis(self, account_name: str, channel_url: str) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM channel_analysis 
            WHERE account_name = ? AND channel_url = ?
            ORDER BY analyzed_at DESC LIMIT 1
        ''', (account_name, channel_url))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_channel_analysis_by_url(self, channel_url: str) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM channel_analysis 
            WHERE channel_url = ?
            ORDER BY analyzed_at DESC LIMIT 1
        ''', (channel_url,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_accounts_for_channel(self, channel_url: str) -> List[str]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT account_name FROM channels WHERE url = ? AND is_active = 1', (channel_url,))
        accounts = [row[0] for row in cursor.fetchall()]
        conn.close()
        return accounts
    
    def get_account_comments(self, account_name: str) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM comments WHERE account_name = ? ORDER BY created_at DESC', (account_name,))
        comments = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return comments

    def set_setting(self, section: str, key: str, value: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO settings (section, key, value) VALUES (?, ?, ?)', (section, key, str(value)))
        conn.commit()
        conn.close()

    def get_all_settings(self) -> Dict[str, Dict[str, str]]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT section, key, value FROM settings')
        rows = cursor.fetchall()
        conn.close()
        settings = {}
        for section, key, value in rows:
            if section not in settings:
                settings[section] = {}
            settings[section][key] = value
        return settings

class ConfigManager:
    def __init__(self, db: SimpleDatabase = None):
        self.settings_file = 'settings.ini'
        self.api_keys_file = 'api_keys.txt'
        self.db = db
        
    def sync_db_to_file(self):
        """ПРИНУДИТЕЛЬНАЯ СИНХРОНИЗАЦИЯ: БД -> settings.ini при старте"""
        if not self.db:
            return

        db_settings = self.db.get_all_settings()
        if not db_settings:
            return 

        config = configparser.ConfigParser()
        if os.path.exists(self.settings_file):
            config.read(self.settings_file, encoding='utf-8')
        
        updated = False
        for section, items in db_settings.items():
            if not config.has_section(section):
                config.add_section(section)
            for key, value in items.items():
                current_val = config.get(section, key, fallback=None)
                if current_val != str(value):
                    config.set(section, key, str(value))
                    updated = True
        
        if updated or not os.path.exists(self.settings_file):
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                config.write(f)
            print("✅ Настройки восстановлены из БД в settings.ini")

    def get_settings(self) -> Dict[str, Any]:
        file_settings = {}
        config = configparser.ConfigParser()
        if os.path.exists(self.settings_file):
            config.read(self.settings_file, encoding='utf-8')
            file_settings = {section: dict(config.items(section)) for section in config.sections()}
        
        if self.db:
            db_settings = self.db.get_all_settings()
            
            # Миграция: если в БД пусто, а файл есть -> сохраняем в БД
            if not db_settings and file_settings:
                for sec, items in file_settings.items():
                    for k, v in items.items():
                        self.db.set_setting(sec, k, v)
                return file_settings
            
            # Если в БД есть данные -> они главные
            final_settings = file_settings.copy()
            for sec, items in db_settings.items():
                if sec not in final_settings:
                    final_settings[sec] = {}
                for k, v in items.items():
                    final_settings[sec][k] = v
            return final_settings
            
        return file_settings
    
    def get_api_keys(self) -> Dict[str, str]:
        keys = {}
        if os.path.exists(self.api_keys_file):
            with open(self.api_keys_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        key, value = line.split('=', 1)
                        keys[key.strip()] = value.strip()
        return keys
    
    def update_api_key(self, key: str, value: str):
        lines = []
        if os.path.exists(self.api_keys_file):
            with open(self.api_keys_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(key) and '=' in line:
                lines[i] = f"{key} = {value}\n"
                found = True
                break
        
        if not found:
            lines.append(f"{key} = {value}\n")
        
        with open(self.api_keys_file, 'w', encoding='utf-8') as f:
            f.writelines(lines)

    def update_setting(self, section: str, key: str, value: str):
        # 1. Обновляем файл
        config = configparser.ConfigParser()
        if os.path.exists(self.settings_file):
            config.read(self.settings_file, encoding='utf-8')
        if not config.has_section(section):
            config.add_section(section)
        config.set(section, key, str(value))
        with open(self.settings_file, 'w', encoding='utf-8') as f:
            config.write(f)
        # 2. Обновляем БД
        if self.db:
            self.db.set_setting(section, key, str(value))


@dataclass
class PendingLogin:
    account_name: str
    phone: str
    client: TelegramClient
    started_at: datetime

class NeuroCommentingBot:
    def __init__(self, token: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.db = SimpleDatabase()
        
        # 1. Инициализируем конфиг
        self.config = ConfigManager(db=self.db)
        
        # 2. ВАЖНО: Восстанавливаем settings.ini из БД ПЕРЕД инициализацией остальных модулей
        self.config.sync_db_to_file()
        
        self.running_monitors = {}
        self._scan_performed = set()
        self._pending_logins: Dict[int, PendingLogin] = {}

        # 3. Теперь безопасно запускаем менеджеры (они прочитают правильный settings.ini)
        self.account_manager = AccountManager(
            sessions_dir="sessions",
            settings_path="settings.ini",
            api_keys_path="api_keys.txt",
            accounts_path="accounts.txt",
        )
        
        try:
            sig = inspect.signature(ChannelMonitor.__init__)
            if "on_comment_sent" in sig.parameters:
                self.channel_monitor = ChannelMonitor(self.account_manager, on_comment_sent=self._on_comment_sent)
            else:
                self.channel_monitor = ChannelMonitor(self.account_manager)
        except Exception:
            self.channel_monitor = ChannelMonitor(self.account_manager)

        os.makedirs('sessions', exist_ok=True)
        self.register_handlers()
    
    async def _on_comment_sent(self, account_name: str, channel_url: str, post_id: str, comment_text: str) -> None:
        try:
            await asyncio.to_thread(self.db.add_comment, str(account_name), str(channel_url), str(post_id), str(comment_text))
        except Exception as e:
            logger.warning(f"Не удалось записать комментарий в статистику: {e}")

    def register_handlers(self):
        """Регистрация всех обработчиков команд"""
        # Основные команды
        self.dp.message.register(self.start_command, Command("start"))
        self.dp.message.register(self.help_command, Command("help"))
        
        # Команды аккаунтов
        self.dp.message.register(self.add_account_command, Command("add_account"))
        self.dp.message.register(self.list_accounts_command, Command("accounts"))
        self.dp.message.register(self.delete_account_command, Command("delete_account"))
        
        # Команды каналов
        self.dp.message.register(self.add_channel_command, Command("add_channel"))
        self.dp.message.register(self.list_channels_command, Command("channels"))
        self.dp.message.register(self.delete_channel_command, Command("delete_channel"))
        
        # Команды управления
        self.dp.message.register(self.settings_command, Command("settings"))
        self.dp.message.register(self.start_monitoring_command, Command("start_monitoring"))
        self.dp.message.register(self.stop_monitoring_command, Command("stop"))
        self.dp.message.register(self.stats_command, Command("stats"))
        
        # Обработчики текстовых сообщений от кнопок меню
        # ИСПОЛЬЗУЕМ endswith, чтобы избежать проблем с вариациями эмодзи (VS16)
        self.dp.message.register(self.list_accounts_command, F.text.endswith("Аккаунты"))
        self.dp.message.register(self.list_channels_command, F.text.endswith("Каналы"))
        self.dp.message.register(self.settings_command, F.text.endswith("Настройки"))
        self.dp.message.register(self.start_monitoring_command, F.text.endswith("Запустить"))
        self.dp.message.register(self.stop_monitoring_command, F.text.endswith("Остановить"))
        self.dp.message.register(self.stats_command, F.text.endswith("Статистика"))
        
        # Обработчики состояний FSM
        self.dp.message.register(self.process_account_name, Form.waiting_for_account_name)
        self.dp.message.register(self.process_phone, Form.waiting_for_phone)
        self.dp.message.register(self.process_tg_code, Form.waiting_for_tg_code)
        self.dp.message.register(self.process_2fa_password, Form.waiting_for_2fa_password)
        self.dp.message.register(self.process_channel, Form.waiting_for_channel)
        self.dp.message.register(self.process_api_id, Form.waiting_for_api_id)
        self.dp.message.register(self.process_api_hash, Form.waiting_for_api_hash)
        self.dp.message.register(self.process_mistral_key, Form.waiting_for_mistral_key)
        self.dp.message.register(self.process_setting_value, Form.waiting_for_setting_value)
        self.dp.message.register(self.process_post_delay_min, Form.waiting_for_post_delay_min)
        self.dp.message.register(self.process_post_delay_max, Form.waiting_for_post_delay_max)
        
        # Обработчики callback
        self.dp.callback_query.register(self.callback_handler)
        
        # Обработчик для любых текстовых сообщений (для отладки)
        self.dp.message.register(self.unknown_message)
    
    async def start_command(self, message: Message):
        """Обработчик команды /start"""
        user_id = message.from_user.id
        
        # Добавляем пользователя в базу
        self.db.add_user(user_id, message.from_user.username, message.from_user.first_name)
        
        keyboard = self.get_main_keyboard()
        
        await message.answer(
            f"👋 Привет, {message.from_user.first_name}!\n\n"
            f"🤖 Я бот для управления NeuroCommenting системой.\n\n"
            f"📈 <b>Основные возможности:</b>\n"
            f"• Добавление/удаление аккаунтов\n"
            f"• Привязка каналов к аккаунтам\n"
            f"• Настройка параметров комментирования\n"
            f"• Запуск/остановка мониторинга\n"
            f"• Просмотр статистики\n\n"
            f"• <b>DEV. by Tashinny1</b> (@dx1one)\n\n"
            f"Используйте кнопки из меню ниже:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def unknown_message(self, message: Message):
        """Обработчик неизвестных сообщений"""
        await message.answer(
            "❌ Неизвестная команда.\n"
            "Используйте меню вниз или команду /help для справки."
        )
    
    async def help_command(self, message: Message):
        """Обработчик команды /help"""
        help_text = """
ℹ️ <b>Список доступных команд:</b>

<b>Аккаунты:</b>
/accounts - Список аккаунтов
/add_account - Добавить аккаунт
/delete_account - Удалить аккаунт

<b>Каналы:</b>
/channels - Список каналов
/add_channel - Добавить канал
/delete_channel - Удалить канал

<b>Управление:</b>
/start - Начать работу с ботом
/start_monitoring - Запустить мониторинг
/stop - Остановить мониторинг
/settings - Настройки

<b>Информация:</b>
/stats - Статистика
/help - Эта справка

<b>Быстрые действия:</b>
Используйте кнопки меню для быстрого доступа к функциям.
        """
        await message.answer(help_text, parse_mode="HTML")

    # --------------------
    # Внутренние утилиты (ID пользователя, API ключи, авторизация Telethon)
    # --------------------
    def _get_user_id(self, message: Message, user_id: Optional[int] = None) -> int:
        """Единообразно получает user_id (важно для callback, где message.from_user = бот)."""
        return int(user_id) if user_id is not None else int(message.from_user.id)

    def _load_telegram_api_credentials(self) -> Optional[Dict[str, Any]]:
        """Берет telegram_api_id / telegram_api_hash из api_keys.txt. Возвращает None, если невалидно."""
        api_keys = self.config.get_api_keys()
        api_id_raw = api_keys.get('telegram_api_id', '').strip()
        api_hash = api_keys.get('telegram_api_hash', '').strip()

        # Обрабатываем дефолтные плейсхолдеры из шаблона
        if not api_id_raw or api_id_raw.upper() in {'ВАШ_API_ID', 'YOUR_API_ID'}:
            return None
        if not api_hash or api_hash.upper() in {'ВАШ_API_HASH', 'YOUR_API_HASH'}:
            return None

        if not api_id_raw.isdigit():
            return None

        api_id = int(api_id_raw)
        if len(api_hash) < 20:
            return None

        return {'api_id': api_id, 'api_hash': api_hash}

    async def _cancel_pending_login(self, user_id: int):
        pending = self._pending_logins.pop(user_id, None)
        if pending:
            try:
                await pending.client.disconnect()
            except Exception:
                pass

    def _post_login_keyboard(self, account_name: str) -> InlineKeyboardMarkup:
        """Клавиатура после успешного входа/нахождения сессии."""
        keyboard_buttons = []
        
        # Проверяем лимит каналов перед показом кнопки
        existing_channels = self.db.get_account_channels(account_name)
        if len(existing_channels) < 5:
            keyboard_buttons.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data=f"add_channel_{account_name}")])
        
        keyboard_buttons.append([InlineKeyboardButton(text="🔑 Настроить API ключи", callback_data=f"setup_api_{account_name}")])
        keyboard_buttons.append([InlineKeyboardButton(text="📝 К списку аккаунтов", callback_data="list_accounts")])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    async def _ensure_telegram_api_keys_and_resume(self, message: Message, state: FSMContext, account_name: str, phone: str) -> bool:
        """Если нет API ID/Hash — запрашивает их через бота и ставит флаг resume_login."""
        creds = self._load_telegram_api_credentials()
        if creds:
            return True

        # Пытаемся понять, чего именно не хватает
        api_keys = self.config.get_api_keys()
        api_id_raw = api_keys.get('telegram_api_id', '').strip()
        api_hash = api_keys.get('telegram_api_hash', '').strip()

        await state.update_data(
            account_name=account_name,
            phone=phone,
            resume_login=True,
        )

        if not api_id_raw or api_id_raw.upper() in {'ВАШ_API_ID', 'YOUR_API_ID'} or not api_id_raw.isdigit():
            await state.set_state(Form.waiting_for_api_id)
            await message.answer(
                "⚠️ Для входа в аккаунт нужен Telegram API ID.\n\n"
                "Введите <b>telegram_api_id</b> (только цифры). Получить можно на <code>my.telegram.org</code>.",
                parse_mode="HTML",
            )
            return False

        # API ID есть, значит просим Hash
        await state.set_state(Form.waiting_for_api_hash)
        await message.answer(
            "⚠️ Для входа в аккаунт нужен Telegram API Hash.\n\n"
            "Введите <b>telegram_api_hash</b>. Получить можно на <code>my.telegram.org</code>.",
            parse_mode="HTML",
        )
        return False

    async def _start_telethon_login(self, message: Message, state: FSMContext, account_name: str, phone: str):
        """Запускает авторизацию Telethon: если сессии нет — отправляет код и переводит FSM в ожидание кода."""
        creds = self._load_telegram_api_credentials()
        if not creds:
            # Должно быть перехвачено _ensure_telegram_api_keys_and_resume(), но на всякий случай.
            await message.answer("❌ Не настроены Telegram API ключи (telegram_api_id / telegram_api_hash).")
            return

        # Если у пользователя уже была незавершенная авторизация — закрываем её
        await self._cancel_pending_login(message.from_user.id)

        session_path = os.path.join('sessions', account_name)
        client = TelegramClient(session_path, creds['api_id'], creds['api_hash'])

        try:
            await client.connect()

            if await client.is_user_authorized():
                # Сессия уже есть — просто фиксируем аккаунт
                try:
                    _me = await client.get_me()
                except Exception:
                    _me = None

                await client.disconnect()
                self.db.add_account(account_name, phone, message.from_user.id)
                self.update_accounts_file()
                await state.clear()

                await message.answer(
                    f"✅ Аккаунт <b>{account_name}</b> уже был авторизован — сессия найдена и будет использоваться.\n\n"
                    f"📞 Телефон: <code>{phone}</code>\n"
                    + (f"👤 Telegram: <code>@{_me.username}</code>\n" if getattr(_me, 'username', None) else "")
                    + "\nТеперь можно привязывать каналы.",
                    reply_markup=self._post_login_keyboard(account_name),
                    parse_mode="HTML",
                )
                return

            # Нет сессии — отправляем код
            await client.send_code_request(phone)
            self._pending_logins[message.from_user.id] = PendingLogin(
                account_name=account_name,
                phone=phone,
                client=client,
                started_at=datetime.now(),
            )

            await state.set_state(Form.waiting_for_tg_code)
            await state.update_data(account_name=account_name, phone=phone)

            await message.answer(
                "📩 Код подтверждения отправлен в Telegram/SMS на указанный номер.\n\n"
                "Введите код (только цифры).",
            )

        except FloodWaitError as e:
            await client.disconnect()
            await state.clear()
            await message.answer(
                f"❌ Telegram временно ограничил попытки входа (FloodWait). Попробуйте позже.\n"
                f"⏳ Ожидание: {getattr(e, 'seconds', 'N/A')} сек."
            )
        except PhoneNumberInvalidError:
            await client.disconnect()
            await state.clear()
            await message.answer("❌ Telegram отклонил номер телефона. Проверьте формат и попробуйте снова.")
        except Exception as e:
            logger.exception("Ошибка при старте авторизации Telethon")
            try:
                await client.disconnect()
            except Exception:
                pass
            await state.clear()
            await message.answer(f"❌ Не удалось начать авторизацию: {e}")
    
    async def add_account_command(self, message: Message, state: FSMContext):
        """Добавление нового аккаунта"""
        await state.set_state(Form.waiting_for_account_name)
        await message.answer(
            "📝 <b>Добавление нового аккаунта</b>\n\n"
            "Введите имя для аккаунта (только латинские буквы и цифры):",
            parse_mode="HTML"
        )
    
    async def process_account_name(self, message: Message, state: FSMContext):
        """Обработка имени аккаунта"""
        account_name = message.text.strip()
        
        # Проверка имени
        if not re.match(r'^[a-zA-Z0-9_]+$', account_name):
            await message.answer(
                "❌ Неверное имя аккаунта.\n"
                "Используйте только латинские буквы, цифры и подчеркивание.\n"
                "Попробуйте снова:"
            )
            return
        
        # Проверка существования аккаунта
        if self.db.account_exists(account_name):
            await message.answer(
                f"❌ Аккаунт '{account_name}' уже существует.\n"
                "Придумайте другое имя:"
            )
            return
        
        await state.update_data(account_name=account_name)
        await state.set_state(Form.waiting_for_phone)
        
        await message.answer(
            f"✅ Имя аккаунта: <b>{account_name}</b>\n\n"
            "Теперь введите номер телефона в формате:\n"
            "<code>+79001234567</code>",
            parse_mode="HTML"
        )
    
    async def process_phone(self, message: Message, state: FSMContext):
        """Обработка номера телефона"""
        phone = message.text.strip()
        
        # Проверка номера
        if not re.match(r'^\+7\d{10}$', phone) and not re.match(r'^\+\d{11,15}$', phone):
            await message.answer(
                "❌ Неверный формат номера.\n"
                "Введите номер в международном формате:\n"
                "<code>+79001234567</code>",
                parse_mode="HTML"
            )
            return
        
        user_data = await state.get_data()
        account_name = user_data['account_name']

        # 1) Проверяем Telegram API ключи. Если их нет — запрашиваем и продолжим после ввода.
        ready = await self._ensure_telegram_api_keys_and_resume(message, state, account_name, phone)
        if not ready:
            return

        # 2) Стартуем вход и сохраняем сессию (если сессии не было — попросим код).
        await self._start_telethon_login(message, state, account_name, phone)

    async def process_tg_code(self, message: Message, state: FSMContext):
        """Обработка кода из Telegram/SMS и завершение входа"""
        code = re.sub(r"\s+", "", (message.text or "")).strip()
        if not re.match(r"^\d{4,8}$", code):
            await message.answer("❌ Код должен состоять из 4-8 цифр. Введите код ещё раз:")
            return

        pending = self._pending_logins.get(message.from_user.id)
        if not pending:
            await state.clear()
            await message.answer("❌ Нет активной авторизации. Запустите /add_account заново.")
            return

        client = pending.client
        try:
            await client.sign_in(phone=pending.phone, code=code)

            if not await client.is_user_authorized():
                raise RuntimeError("Авторизация не подтверждена")

            me = None
            try:
                me = await client.get_me()
            except Exception:
                pass

            await client.disconnect()
            self._pending_logins.pop(message.from_user.id, None)

            # Фиксируем аккаунт в БД и в accounts.txt
            self.db.add_account(pending.account_name, pending.phone, message.from_user.id)
            self.update_accounts_file()
            await state.clear()

            await message.answer(
                f"✅ Вход выполнен, сессия сохранена.\n\n"
                f"👤 Аккаунт: <b>{pending.account_name}</b>\n"
                f"📞 Телефон: <code>{pending.phone}</code>\n"
                + (f"👤 Telegram: <code>@{me.username}</code>\n" if getattr(me, 'username', None) else "")
                + "\nТеперь можно привязывать каналы.",
                reply_markup=self._post_login_keyboard(pending.account_name),
                parse_mode="HTML",
            )

        except SessionPasswordNeededError:
            # 2FA включена
            await state.set_state(Form.waiting_for_2fa_password)
            await message.answer(
                "🔒 На аккаунте включена двухфакторная защита (пароль).\n\n"
                "Введите пароль 2FA:",
            )
        except PhoneCodeInvalidError:
            await message.answer("❌ Неверный код. Попробуйте снова:")
        except PhoneCodeExpiredError:
            await self._cancel_pending_login(message.from_user.id)
            await state.clear()
            await message.answer("❌ Код истёк. Запустите /add_account и повторите ввод заново.")
        except FloodWaitError as e:
            await self._cancel_pending_login(message.from_user.id)
            await state.clear()
            await message.answer(
                f"❌ Telegram временно ограничил попытки входа (FloodWait). Попробуйте позже.\n"
                f"⏳ Ожидание: {getattr(e, 'seconds', 'N/A')} сек."
            )
        except Exception as e:
            logger.exception("Ошибка при подтверждении кода")
            await self._cancel_pending_login(message.from_user.id)
            await state.clear()
            await message.answer(f"❌ Ошибка при входе: {e}")

    async def process_2fa_password(self, message: Message, state: FSMContext):
        """Обработка пароля 2FA и завершение входа"""
        password = (message.text or "").strip()
        if not password:
            await message.answer("❌ Пароль не может быть пустым. Введите пароль 2FA:")
            return

        pending = self._pending_logins.get(message.from_user.id)
        if not pending:
            await state.clear()
            await message.answer("❌ Нет активной авторизации. Запустите /add_account заново.")
            return

        client = pending.client
        try:
            await client.sign_in(password=password)

            if not await client.is_user_authorized():
                raise RuntimeError("Авторизация не подтверждена")

            me = None
            try:
                me = await client.get_me()
            except Exception:
                pass

            await client.disconnect()
            self._pending_logins.pop(message.from_user.id, None)

            self.db.add_account(pending.account_name, pending.phone, message.from_user.id)
            self.update_accounts_file()
            await state.clear()

            await message.answer(
                f"✅ Вход выполнен, сессия сохранена.\n\n"
                f"👤 Аккаунт: <b>{pending.account_name}</b>\n"
                f"📞 Телефон: <code>{pending.phone}</code>\n"
                + (f"👤 Telegram: <code>@{me.username}</code>\n" if getattr(me, 'username', None) else "")
                + "\nТеперь можно привязывать каналы.",
                reply_markup=self._post_login_keyboard(pending.account_name),
                parse_mode="HTML",
            )

        except Exception as e:
            logger.exception("Ошибка при вводе 2FA пароля")
            await self._cancel_pending_login(message.from_user.id)
            await state.clear()
            await message.answer(f"❌ Не удалось войти с 2FA паролем: {e}")
    
    async def list_accounts_command(self, message: Message, user_id: Optional[int] = None):
        """Показать список аккаунтов"""
        uid = self._get_user_id(message, user_id)
        accounts = self.db.get_user_accounts(uid)
        
        if not accounts:
            await message.answer(
                "😕 У вас пока нет добавленных аккаунтов.\n"
                "Используйте /add_account чтобы добавить первый аккаунт."
            )
            return
        
        text = "📝 <b>Ваши аккаунты:</b>\n\n"
        for i, account in enumerate(accounts, 1):
            status = "🟢 Активен" if account.get('is_active', 1) else "🔴 Неактивен"
            channels_count = len(self.db.get_account_channels(account['name']))
            
            text += (
                f"{i}. <b>{account['name']}</b> {status}\n"
                f"   📞: <code>{account['phone']}</code>\n"
                f"   📺 Каналов: {channels_count}\n"
                f"   📅 Добавлен: {account['created_at'][:10]}\n\n"
            )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="add_account")],
            [InlineKeyboardButton(text="🗑️ Удалить аккаунт", callback_data="delete_account_menu")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")]
        ])
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    def _format_dt(self, dt_str: str) -> str:
        if not dt_str:
            return "—"
        try:
            return str(dt_str).replace("T", " ").split(".")[0]
        except Exception:
            return str(dt_str)

    def _count_recent(self, comments: List[Dict], hours: int) -> int:
        if not comments:
            return 0
        now = datetime.now()
        cutoff = now.timestamp() - hours * 3600
        cnt = 0
        for c in comments:
            dt_str = c.get("created_at") or ""
            try:
                iso = str(dt_str).replace(" ", "T")
                ts = datetime.fromisoformat(iso).timestamp()
                if ts >= cutoff:
                    cnt += 1
            except Exception:
                continue
        return cnt

    async def detailed_stats_view(self, message: Message, user_id: Optional[int] = None, edit: bool = False):
        """Экран детальной статистики."""
        uid = self._get_user_id(message, user_id)
        accounts = self.db.get_user_accounts(uid)

        text = "📈 <b>Детальная статистика</b>\n\n"
        if not accounts:
            text += "Аккаунтов пока нет\n"
        else:
            grand_total = 0
            grand_24h = 0
            for account in accounts:
                name = account["name"]
                channels = self.db.get_account_channels(name)
                comments = self.db.get_account_comments(name)

                total = len(comments)
                c24 = self._count_recent(comments, 24)
                c7d = self._count_recent(comments, 24 * 7)
                last_dt = self._format_dt(comments[0].get("created_at")) if comments else "—"

                grand_total += total
                grand_24h += c24

                per_channel: Dict[str, int] = {}
                for c in comments:
                    ch = str(c.get("channel_url") or "unknown")
                    per_channel[ch] = per_channel.get(ch, 0) + 1
                top = sorted(per_channel.items(), key=lambda x: x[1], reverse=True)[:3]

                text += f"👤 <b>{name}</b>\n"
                text += f"   📺 Каналов: {len(channels)}\n"
                text += f"   💬 Комментов: {total} (24ч: {c24}, 7д: {c7d})\n"
                text += f"   🕒 Последний: {last_dt}\n"
                if top:
                    text += "   🏆 Топ каналов:\n"
                    for ch, n in top:
                        short = ch
                        if len(short) > 48:
                            short = short[:45] + "..."
                        text += f"      • {short} — {n}\n"
                text += "\n"

            text += f"📊 <b>Всего комментов:</b> {grand_total}\n"
            text += f"🔥 <b>За 24 часа:</b> {grand_24h}\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_detailed_stats")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="stats_back")],
        ])

        try:
            if edit:
                await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            else:
                await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    
    async def delete_account_command(self, message: Message, user_id: Optional[int] = None):
        """Удаление аккаунта"""
        uid = self._get_user_id(message, user_id)
        accounts = self.db.get_user_accounts(uid)
        
        if not accounts:
            await message.answer("У вас нет аккаунтов для удаления.")
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for account in accounts:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑️ {account['name']} ({account['phone']})",
                    callback_data=f"delete_account_{account['name']}"
                )
            ])
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ])
        
        await message.answer(
            "Выберите аккаунт для удаления:",
            reply_markup=keyboard
        )
    
    async def add_channel_command(self, message: Message, state: FSMContext, user_id: Optional[int] = None):
        """Добавление канала к аккаунту"""
        uid = self._get_user_id(message, user_id)
        accounts = self.db.get_user_accounts(uid)
        
        if not accounts:
            await message.answer(
                "❌ У вас нет аккаунтов.\n"
                "Сначала добавьте аккаунт командой /add_account"
            )
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for account in accounts:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"📺 {account['name']} ({account['phone']})",
                    callback_data=f"select_account_{account['name']}"
                )
            ])
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ])
        
        await message.answer(
            "Выберите аккаунт для привязки канала:",
            reply_markup=keyboard
        )
    
    async def process_channel(self, message: Message, state: FSMContext):
        """Обработка URL канала"""
        channel_url = message.text.strip()
        user_data = await state.get_data()
        account_name = user_data['account_name']
        
        # Валидация URL
        if not (channel_url.startswith('https://t.me/') or channel_url.startswith('@')):
            await message.answer(
                "❌ Неверный формат канала.\n"
                "Используйте:\n"
                "• <code>https://t.me/username</code>\n"
                "• <code>@username</code>\n\n"
                "Введите правильный URL:",
                parse_mode="HTML"
            )
            return
        
        # Проверка лимита каналов (максимум 5 на аккаунт)
        existing_channels = self.db.get_account_channels(account_name)
        # Проверяем, не добавляется ли уже существующий канал
        channel_exists = any(ch.get('url', '').strip() == channel_url.strip() for ch in existing_channels)
        
        if channel_exists:
            await message.answer(
                f"ℹ️ Канал <code>{channel_url}</code> уже привязан к аккаунту <b>{account_name}</b>.",
                parse_mode="HTML"
            )
            await state.clear()
            return
        
        if len(existing_channels) >= 5:
            await message.answer(
                f"❌ <b>Достигнут лимит каналов</b>\n\n"
                f"К аккаунту <b>{account_name}</b> уже привязано <b>5 каналов</b>.\n"
                f"Максимальное количество каналов на один аккаунт: <b>5</b>.\n\n"
                f"Чтобы добавить новый канал, сначала удалите один из существующих.",
                parse_mode="HTML"
            )
            await state.clear()
            return
        
        # Сохраняем канал
        self.db.add_channel(account_name, channel_url)
        
        # Обновляем файл channels.txt
        self.update_channels_file()
        
        await state.clear()
        
        # Проверяем, можно ли добавить еще каналы
        updated_channels = self.db.get_account_channels(account_name)
        can_add_more = len(updated_channels) < 5
        
        keyboard_buttons = []
        if can_add_more:
            keyboard_buttons.append([InlineKeyboardButton(text="➕ Добавить еще канал", callback_data=f"add_channel_{account_name}")])
        keyboard_buttons.append([InlineKeyboardButton(text="📝 К списку каналов", callback_data="list_channels")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        status_text = f"✅ Канал успешно добавлен к аккаунту <b>{account_name}</b>!\n\n"
        status_text += f"🔗 Канал: <code>{channel_url}</code>\n\n"
        status_text += f"📈 Каналов у аккаунта: <b>{len(updated_channels)}/5</b>"
        
        await message.answer(
            status_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def list_channels_command(self, message: Message, user_id: Optional[int] = None):
        """Показать список каналов"""
        uid = self._get_user_id(message, user_id)
        accounts = self.db.get_user_accounts(uid)
        
        if not accounts:
            await message.answer("У вас нет аккаунтов.")
            return
        
        text = "📺 <b>Привязанные каналы:</b>\n\n"
        
        for account in accounts:
            channels = self.db.get_account_channels(account['name'])
            
            if channels:
                text += f"👤 <b>{account['name']}</b>:\n"
                for i, channel in enumerate(channels, 1):
                    status = "🟢" if channel['is_active'] else "🔴"
                    text += f"  {status} {channel['url']}\n"
                text += "\n"
        
        if len(text) == len("📺 <b>Привязанные каналы:</b>\n\n"):
            text = "😕 Каналы не добавлены.\nИспользуйте /add_channel"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton(text="🗑️ Удалить канал", callback_data="delete_channel_menu")]
        ])
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    
    async def delete_channel_command(self, message: Message, user_id: Optional[int] = None):
        """Удаление канала"""
        uid = self._get_user_id(message, user_id)
        accounts = self.db.get_user_accounts(uid)
        
        all_channels = []
        for account in accounts:
            channels = self.db.get_account_channels(account['name'])
            for channel in channels:
                all_channels.append({
                    'account': account['name'],
                    'url': channel['url'],
                    'id': channel['id']
                })
        
        if not all_channels:
            await message.answer("Нет каналов для удаления.")
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for channel in all_channels:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑️ {channel['url']} ({channel['account']})",
                    callback_data=f"delete_channel_{channel['id']}"
                )
            ])
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
        ])
        
        await message.answer(
            "Выберите канал для удаления:",
            reply_markup=keyboard
        )
    
    async def settings_command(self, message: Message):
        """Настройки системы (понятное меню)"""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🤵 Формальный", callback_data="preset|formal"),
                InlineKeyboardButton(text="😄 Неформальный", callback_data="preset|informal")
            ],
            [
                InlineKeyboardButton(text="🤝 Дружелюбный", callback_data="preset|friendly")
            ],
            [InlineKeyboardButton(text="🤖 Генерация комментариев", callback_data="mistral_settings")],
            [InlineKeyboardButton(text="⏸ Паузы и очередь", callback_data="behavior_settings")],
            [InlineKeyboardButton(text="📝 Текст и стиль", callback_data="content_settings")],
            [InlineKeyboardButton(text="🔒 Безопасность (расшир.)", callback_data="security_settings")],
            [InlineKeyboardButton(text="📈 Логи (расширенные)", callback_data="logging_settings")],
            [InlineKeyboardButton(text="🔑 Ключи API", callback_data="update_api_keys")],
        ])

        await message.answer(
            "⚙️ <b>Настройки</b>\n\n"
            "Здесь вы настраиваете стиль комментариев, паузы/очередь между аккаунтами и параметры генерации.\n"
            "Выберите рабочий профиль или настройте параметры вручную.\n\n"
            "Выберите раздел:",
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    async def start_monitoring_command(self, message: Message, user_id: Optional[int] = None):
        """Запуск мониторинга"""
        uid = self._get_user_id(message, user_id)
        accounts = self.db.get_user_accounts(uid)
        
        active_accounts = [acc for acc in accounts if acc.get('is_active', 1)]
        
        if not active_accounts:
            await message.answer(
                "❌ Нет активных аккаунтов.\n"
                "Активируйте аккаунты в настройках."
            )
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for account in active_accounts:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"▶️ Запустить {account['name']}",
                    callback_data=f"start_monitor_{account['name']}"
                )
            ])
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="▶️ Запустить все", callback_data="start_all_monitors")
        ])
        
        await message.answer(
            "▶️ <b>Запуск мониторинга</b>\n\n"
            "Выберите аккаунт для запуска:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def stop_monitoring_command(self, message: Message):
        """Остановка мониторинга"""
        if not self.running_monitors:
            await message.answer("❌ Нет запущенных мониторов.")
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for account_name in self.running_monitors.keys():
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"⏹️ Остановить {account_name}",
                    callback_data=f"stop_monitor_{account_name}"
                )
            ])
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="⏹️ Остановить все", callback_data="stop_all_monitors")
        ])
        
        await message.answer(
            "⏹️ <b>Остановка мониторинга</b>\n\n"
            "Выберите аккаунт для остановки:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def stats_command(self, message: Message, user_id: Optional[int] = None):
        """Показать статистику"""
        uid = self._get_user_id(message, user_id)
        accounts = self.db.get_user_accounts(uid)
        
        total_comments = 0
        active_monitors = len(self.running_monitors)
        
        text = "📈 <b>Статистика системы</b>\n\n"
        text += f"👤 Аккаунтов: {len(accounts)}\n"
        text += f"▶️ Активных мониторов: {active_monitors}\n\n"
        
        for account in accounts:
            channels = self.db.get_account_channels(account['name'])
            comments = self.db.get_account_comments(account['name'])
            total_comments += len(comments)
            
            text += f"👤 <b>{account['name']}</b>\n"
            text += f"   📞: {account['phone']}\n"
            text += f"   📺 Каналов: {len(channels)}\n"
            text += f"   💬 Комментариев: {len(comments)}\n"
            text += f"   🟢 Активен: {'Да' if account.get('is_active', 1) else 'Нет'}\n"
            text += f"   🏃 Монитор: {'Запущен' if account['name'] in self.running_monitors else 'Остановлен'}\n\n"
        
        text += f"💬 <b>Всего комментариев:</b> {total_comments}\n"
        text += f"⏱ <b>Время работы:</b> {self.get_uptime()}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_stats")],
            [InlineKeyboardButton(text="📊 Детальная статистика", callback_data="detailed_stats")]
        ])
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    
    # Новые методы для обработки составной API ID
    async def process_api_id(self, message: Message, state: FSMContext):
        """Обработка ввода API ID"""
        api_id = message.text.strip()
        user_data = await state.get_data()
        account_name = user_data.get('account_name', 'default')
        
        if not api_id.isdigit():
            await message.answer("❌ API ID должен содержать только цифры. Попробуйте снова:")
            return
        
        self.config.update_api_key('telegram_api_id', api_id)

        # Если пользователь пришел сюда из флоу добавления аккаунта — продолжаем (просим hash)
        if user_data.get('resume_login'):
            await state.set_state(Form.waiting_for_api_hash)
            await message.answer(
                "Теперь введите <b>telegram_api_hash</b>. Получить можно на <code>my.telegram.org</code>.",
                parse_mode="HTML",
            )
            return

        display_name = "Глобальные настройки" if account_name == "global" else account_name

        await state.clear()
        await message.answer(
            f"✅ <b>Telegram API ID обновлен!</b>\n"
            f"Для: <b>{display_name}</b>\n"
            f"🆔 ID: <code>{api_id}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К ключам", callback_data="update_api_keys")],
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")]
            ])
        )
    
    async def process_api_hash(self, message: Message, state: FSMContext):
        """Обработка ввода API Hash"""
        api_hash = message.text.strip()
        user_data = await state.get_data()
        account_name = user_data.get('account_name', 'default')
        
        if len(api_hash) < 20:
            await message.answer("❌ API Hash слишком короткий. Попробуйте снова:")
            return
        
        self.config.update_api_key('telegram_api_hash', api_hash)

        # Если пользователь пришел сюда из флоу добавления аккаунта — продолжаем вход
        if user_data.get('resume_login'):
            phone = user_data.get('phone')
            if not phone:
                await state.clear()
                await message.answer("❌ Не удалось продолжить авторизацию: не найден номер телефона.")
                return
            await self._start_telethon_login(message, state, account_name, phone)
            return

        display_name = "Глобальные настройки" if account_name == "global" else account_name

        await state.clear()
        await message.answer(
            f"✅ <b>Telegram API Hash обновлен!</b>\n"
            f"Для: <b>{display_name}</b>\n"
            f"🔑 Hash: <code>{api_hash[:10]}...</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К ключам", callback_data="update_api_keys")],
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")]
            ])
        )
    
    async def process_mistral_key(self, message: Message, state: FSMContext):
        """Обработка ввода Mistral API Key"""
        mistral_key = message.text.strip()
        user_data = await state.get_data()
        account_name = user_data.get('account_name', 'default')
        
        if len(mistral_key) < 20:
            await message.answer("❌ Mistral API Key слишком короткий. Попробуйте снова:")
            return
        
        self.config.update_api_key('mistral_api_key', mistral_key)
        
        display_name = "Глобальные настройки" if account_name == "global" else account_name

        await state.clear()
        await message.answer(
            f"✅ <b>Mistral API Key обновлен!</b>\n"
            f"Для: <b>{display_name}</b>\n"
            f"🔑 Key: <code>{mistral_key[:10]}...</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К ключам", callback_data="update_api_keys")],
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")]
            ])
        )
    
    async def process_post_delay_min(self, message: Message, state: FSMContext):
        """Обработка ввода минимальной задержки после поста"""
        try:
            min_val = int(message.text.strip())
            if min_val < 0:
                await message.answer("❌ Значение не может быть отрицательным. Введите число от 0:")
                return
            if min_val > 3600:
                await message.answer("❌ Значение слишком большое (максимум 3600 секунд). Введите другое значение:")
                return
            
            await state.update_data(post_delay_min=min_val)
            await state.set_state(Form.waiting_for_post_delay_max)
            await message.answer(
                f"✅ Минимальная задержка: <code>{min_val}</code> сек\n\n"
                "Теперь введите максимальную задержку в секундах (должна быть больше минимальной):",
                parse_mode="HTML"
            )
        except ValueError:
            await message.answer("❌ Введите число (например: 10):")
    
    async def process_post_delay_max(self, message: Message, state: FSMContext):
        """Обработка ввода максимальной задержки после поста"""
        try:
            max_val = int(message.text.strip())
            user_data = await state.get_data()
            min_val = user_data.get('post_delay_min', 0)
            
            if max_val < min_val:
                await message.answer(
                    f"❌ Максимальная задержка должна быть больше или равна минимальной ({min_val} сек).\n"
                    "Введите другое значение:"
                )
                return
            if max_val > 7200:
                await message.answer("❌ Значение слишком большое (максимум 7200 секунд). Введите другое значение:")
                return
            
            self.update_setting_in_file("Behavior", "post_comment_delay_min", str(min_val))
            self.update_setting_in_file("Behavior", "post_comment_delay_max", str(max_val))
            
            await state.clear()
            
            await message.answer(
                f"✅ <b>Задержка после поста установлена</b>\n\n"
                f"• Минимум: <code>{min_val}</code> сек\n"
                f"• Максимум: <code>{max_val}</code> сек\n\n"
                f"Эта настройка применяется ко всем аккаунтам.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад к настройкам", callback_data="behavior_settings")]
                ]),
                parse_mode="HTML"
            )
        except ValueError:
            await message.answer("❌ Введите число (например: 60):")

    async def process_setting_value(self, message: Message, state: FSMContext):
        """Обработка ввода значения настройки (с подсказками и валидацией)"""
        raw = (message.text or "").strip()
        user_data = await state.get_data()
        section = user_data.get('section')
        key = user_data.get('key')

        if not section or not key:
            await message.answer("❌ Ошибка: не найдены данные настройки.")
            await state.clear()
            return

        ok, normalized, err, adjustments = self._normalize_and_validate_setting(section, key, raw)
        if not ok:
            await message.answer(
                f"❌ Не удалось сохранить значение.\n\n"
                f"Причина: <b>{err}</b>\n\n"
                "Отправьте другое значение или нажмите «🔙 Назад».",
                reply_markup=self._back_to_section_kb(section),
                parse_mode="HTML"
            )
            return

        # Сохраняем основную настройку
        self.update_setting_in_file(section, key, normalized)

        # Если потребовались корректировки связанных параметров (min/max) — сохраняем их тоже
        for adj_key, adj_val in adjustments.items():
            self.update_setting_in_file(section, adj_key, adj_val)

        await state.clear()

        label = self._settings_schema().get(section, {}).get(key, {}).get("label", key)
        pretty = self._format_setting_value(section, key, normalized)

        extra_note = ""
        if adjustments:
            parts = []
            for ak, av in adjustments.items():
                alabel = self._settings_schema().get(section, {}).get(ak, {}).get("label", ak)
                parts.append(f"{alabel} → <code>{self._format_setting_value(section, ak, av)}</code>")
            extra_note = "\n\n<i>Я также поправил связанные параметры, чтобы значения были согласованы:</i>\n" + "\n".join([f"• {p}" for p in parts])

        await message.answer(
            f"✅ Готово!\n\n"
            f"<b>{label}</b> обновлено: <code>{pretty}</code>"
            f"{extra_note}",
            reply_markup=self._back_to_section_kb(section),
            parse_mode="HTML"
        )

    async def callback_handler(self, callback: CallbackQuery, state: FSMContext):
        """Обработчик callback запросов"""
        data = callback.data
        
        if data == "add_account":
            await self.add_account_command(callback.message, state)
        
        elif data.startswith("setup_api_"):
            account_name = data.replace("setup_api_", "")
            await self.setup_api_keys(callback.message, account_name)
        
        elif data == "list_accounts":
            await self.list_accounts_command(callback.message, user_id=callback.from_user.id)
        
        elif data == "delete_account_menu":
            await self.delete_account_command(callback.message, user_id=callback.from_user.id)
        
        elif data.startswith("delete_account_"):
            account_name = data.replace("delete_account_", "")
            # Останавливаем монитор, если он запущен
            if account_name in self.running_monitors:
                try:
                    await self.stop_monitoring(callback.message, account_name)
                except Exception:
                    pass
            self.db.delete_account(account_name)
            self.update_accounts_file()
            self.update_channels_file()

            # Удаляем Telethon-сессию, чтобы при повторном добавлении запросил код снова
            for suffix in [".session", ".session-journal"]:
                sp = os.path.join('sessions', f"{account_name}{suffix}")
                try:
                    if os.path.exists(sp):
                        os.remove(sp)
                except Exception:
                    pass

            await callback.message.edit_text(
                f"✅ Аккаунт <b>{account_name}</b> удален.",
                parse_mode="HTML"
            )
        
        elif data == "add_channel":
            await self.add_channel_command(callback.message, state, user_id=callback.from_user.id)
        
        elif data.startswith("add_channel_"):
            account_name = data.replace("add_channel_", "")
            
            # Проверка лимита каналов перед началом добавления
            existing_channels = self.db.get_account_channels(account_name)
            if len(existing_channels) >= 5:
                await callback.message.edit_text(
                    f"❌ <b>Достигнут лимит каналов</b>\n\n"
                    f"К аккаунту <b>{account_name}</b> уже привязано <b>5 каналов</b>.\n"
                    f"Максимальное количество каналов на один аккаунт: <b>5</b>.\n\n"
                    f"Чтобы добавить новый канал, сначала удалите один из существующих через /channels",
                    parse_mode="HTML"
                )
                return
            
            await state.set_state(Form.waiting_for_channel)
            await state.update_data(account_name=account_name)
            await callback.message.edit_text(
                f"Введите URL канала для аккаунта <b>{account_name}</b>:\n\n"
                f"📈 Текущее количество каналов: <b>{len(existing_channels)}/5</b>\n\n"
                "Формат:\n"
                "• https://t.me/username\n"
                "• @username",
                parse_mode="HTML"
            )
        
        elif data == "list_channels":
            await self.list_channels_command(callback.message, user_id=callback.from_user.id)
        
        elif data == "delete_channel_menu":
            await self.delete_channel_command(callback.message, user_id=callback.from_user.id)
        
        elif data.startswith("delete_channel_"):
            channel_id = int(data.replace("delete_channel_", ""))
            self.db.delete_channel(channel_id)
            self.update_channels_file()
            await callback.message.edit_text("✅ Канал удален.")
        
        elif data.startswith("select_account_"):
            account_name = data.replace("select_account_", "")
            await state.set_state(Form.waiting_for_channel)
            await state.update_data(account_name=account_name)
            await callback.message.edit_text(
                f"Введите URL канала для аккаунта <b>{account_name}</b>:\n\n"
                "Формат:\n"
                "• https://t.me/username\n"
                "• @username",
                parse_mode="HTML"
            )
        
        elif data == "cancel":
            await self._cancel_pending_login(callback.from_user.id)
            await state.clear()
            await callback.message.edit_text("❌ Операция отменена.")
        
        elif data == "settings_menu":
            await self.settings_command(callback.message)
        
        elif data == "update_api_keys":
            # Сброс состояния, если пользователь возвращается из режима ввода
            await state.clear()
            await self.update_api_keys_menu(callback.message)
        
        elif data.startswith("start_monitor_"):
            account_name = data.replace("start_monitor_", "")
            await self.start_monitoring(callback.message, account_name)
        
        elif data == "start_all_monitors":
            accounts = self.db.get_user_accounts(callback.from_user.id)
            for account in accounts:
                if account.get('is_active', 1):
                    await self.start_monitoring(callback.message, account['name'])
            await callback.message.edit_text("✅ Все мониторы запущены.")
        
        elif data.startswith("stop_monitor_"):
            account_name = data.replace("stop_monitor_", "")
            await self.stop_monitoring(callback.message, account_name)
        
        elif data == "stop_all_monitors":
            for account_name in list(self.running_monitors.keys()):
                await self.stop_monitoring(callback.message, account_name)
            await callback.message.edit_text("✅ Все мониторы остановлены.")
        
        elif data == "refresh_stats":
            await self.stats_command(callback.message, user_id=callback.from_user.id)
            try:
                await callback.answer("Статистика обновлена!")
            except Exception:
                pass

        elif data == "detailed_stats":
            await self.detailed_stats_view(callback.message, user_id=callback.from_user.id, edit=False)
            try:
                await callback.answer()
            except Exception:
                pass

        elif data == "refresh_detailed_stats":
            await self.detailed_stats_view(callback.message, user_id=callback.from_user.id, edit=True)
            try:
                await callback.answer("Обновлено")
            except Exception:
                pass

        elif data == "stats_back":
            await self.stats_command(callback.message, user_id=callback.from_user.id)
            try:
                await callback.answer()
            except Exception:
                pass

        
        elif data.startswith("set_api_id_"):
            account_name = data.replace("set_api_id_", "")
            await state.set_state(Form.waiting_for_api_id)
            await state.update_data(account_name=account_name)
            
            target_text = "Глобальные настройки" if account_name == "global" else f"Аккаунт {account_name}"
            
            await callback.message.edit_text(
                f"📝 <b>Ввод Telegram API ID</b>\n"
                f"Цель: <b>{target_text}</b>\n\n"
                f"Введите новый <code>telegram_api_id</code> (только цифры).\n"
                f"Получить можно на https://my.telegram.org",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="update_api_keys" if account_name == "global" else f"setup_api_{account_name}")]
                ])
            )
        
        elif data.startswith("set_api_hash_"):
            account_name = data.replace("set_api_hash_", "")
            await state.set_state(Form.waiting_for_api_hash)
            await state.update_data(account_name=account_name)
            
            target_text = "Глобальные настройки" if account_name == "global" else f"Аккаунт {account_name}"
            
            await callback.message.edit_text(
                f"📝 <b>Ввод Telegram API Hash</b>\n"
                f"Цель: <b>{target_text}</b>\n\n"
                f"Введите новый <code>telegram_api_hash</code>.\n"
                f"Получить можно на https://my.telegram.org",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="update_api_keys" if account_name == "global" else f"setup_api_{account_name}")]
                ])
            )
        
        elif data.startswith("set_mistral_key_"):
            account_name = data.replace("set_mistral_key_", "")
            await state.set_state(Form.waiting_for_mistral_key)
            await state.update_data(account_name=account_name)
            
            target_text = "Глобальные настройки" if account_name == "global" else f"Аккаунт {account_name}"
            
            await callback.message.edit_text(
                f"📝 <b>Ввод Mistral API Key</b>\n"
                f"Цель: <b>{target_text}</b>\n\n"
                f"Введите новый <code>mistral_api_key</code>.\n"
                f"Получить можно на https://console.mistral.ai",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="update_api_keys" if account_name == "global" else f"setup_api_{account_name}")]
                ])
            )
        
        elif data.startswith("preset|"):
            preset = data.split("|", 1)[1].strip().lower()
            await self.apply_settings_preset(callback.message, preset)

        elif data.startswith("help|"):
            # Формат: help|Section|key
            try:
                _, section, key = data.split("|", 2)
            except Exception:
                try:
                    await callback.answer()
                except Exception:
                    pass
                return
            await self.show_setting_help(callback.message, section, key)

        elif data.startswith("setv|"):
            # Формат: setv|Section|key|value
            try:
                _, section, key, value = data.split("|", 3)
            except Exception:
                try:
                    await callback.answer()
                except Exception:
                    pass
                return
            await self.apply_setting_value_from_button(callback.message, section, key, value)
            await state.clear()

        elif data in ["mistral_settings", "behavior_settings", "security_settings", 
                      "content_settings", "logging_settings"]:
            await self.show_settings_category(callback.message, data)

        elif data.startswith("edit_setting_"):
            # Формат: edit_setting_{section}_{key}
            parts = data.replace("edit_setting_", "").split("_", 1)
            if len(parts) == 2:
                section, key = parts
                await self.open_setting_editor(callback.message, state, section, key)
        
        elif data == "set_post_comment_delay":
            await self.set_post_comment_delay_menu(callback.message, state)
        
        elif data == "toggle_channel_scan":
            # Переключение настройки анализа каналов
            all_settings = self.config.get_settings()
            behavior_settings = all_settings.get("Behavior", {})
            current_value = behavior_settings.get("enable_channel_scan", "yes").lower()
            
            # Переключаем значение
            new_value = "no" if current_value in ("yes", "true", "1", "on", "да", "вкл") else "yes"
            self.update_setting_in_file("Behavior", "enable_channel_scan", new_value)
            
            status_text = "включен" if new_value == "yes" else "выключен"
            await callback.message.edit_text(
                f"✅ Анализ каналов <b>{status_text}</b>.\n\n"
                f"ℹ️ Изменения вступили в силу.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="behavior_settings")]
                ])
            )
        
        elif data == "toggle_all_filters":
            # Переключение настройки отключения всех фильтров
            all_settings = self.config.get_settings()
            content_settings = all_settings.get("Content", {})
            filters_disabled = content_settings.get("disable_all_filters", "no").lower() in ("yes", "true", "1", "on", "да", "вкл")
            
            # Переключаем значение
            new_value = "no" if filters_disabled else "yes"
            self.update_setting_in_file("Content", "disable_all_filters", new_value)
            
            status_text = "отключены" if new_value == "yes" else "включены"
            await callback.message.edit_text(
                f"✅ Все фильтры постов <b>{status_text}</b>.\n\n"
                f"ℹ️ Бот будет комментировать {'любые посты без проверок' if new_value == 'yes' else 'только посты, прошедшие фильтры'}.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="content_settings")]
                ])
            )
        
        elif data.startswith("post_delay_preset|"):
            # Формат: post_delay_preset|min|max
            parts = data.replace("post_delay_preset|", "").split("|")
            if len(parts) == 2:
                min_val, max_val = parts
                self.update_setting_in_file("Behavior", "post_comment_delay_min", min_val)
                self.update_setting_in_file("Behavior", "post_comment_delay_max", max_val)
                await callback.message.edit_text(
                    f"✅ Задержка после поста установлена:\n"
                    f"• Минимум: <code>{min_val}</code> сек\n"
                    f"• Максимум: <code>{max_val}</code> сек\n\n"
                    f"Эта настройка применяется ко всем аккаунтам.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 Назад", callback_data="behavior_settings")]
                    ])
                )
        
        elif data == "post_delay_manual":
            await state.set_state(Form.waiting_for_post_delay_min)
            await callback.message.edit_text(
                "✏️ <b>Настройка задержки вручную</b>\n\n"
                "Введите минимальную задержку в секундах (например: 10):",
                parse_mode="HTML"
            )

        try:
            await callback.answer()
        except Exception:
            # Игнорируем ошибки с истекшими callback'ами
            pass
    
    async def setup_api_keys(self, message: Message, account_name: str):
        """Настройка API ключей для аккаунта"""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Telegram API ID", callback_data=f"set_api_id_{account_name}")],
            [InlineKeyboardButton(text="Telegram API Hash", callback_data=f"set_api_hash_{account_name}")],
            [InlineKeyboardButton(text="Mistral API Key", callback_data=f"set_mistral_key_{account_name}")],
            [InlineKeyboardButton(text="✅ Готово", callback_data="list_accounts")]
        ])
        
        api_keys = self.config.get_api_keys()
        
        status_text = ""
        if 'telegram_api_id' in api_keys and api_keys['telegram_api_id'] != 'ВАШ_API_ID':
            status_text += "✅ Telegram API ID: настроен\n"
        else:
            status_text += "❌ Telegram API ID: не настроен\n"
            
        if 'telegram_api_hash' in api_keys and api_keys['telegram_api_hash'] != 'ВАШ_API_HASH':
            status_text += "✅ Telegram API Hash: настроен\n"
        else:
            status_text += "❌ Telegram API Hash: не настроен\n"
            
        if 'mistral_api_key' in api_keys and api_keys['mistral_api_key'] != 'ВАШ_MISTRAL_API_KEY':
            status_text += "✅ Mistral API Key: настроен\n"
        else:
            status_text += "❌ Mistral API Key: не настроен\n"
        
        await message.answer(
            f"🔑 <b>Настройка API ключей для {account_name}</b>\n\n"
            f"📊 <b>Статус:</b>\n{status_text}\n"
            "Выберите какой ключ настроить:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def set_post_comment_delay_menu(self, message: Message, state: FSMContext):
        """Меню установки задержки после публикации поста"""
        all_settings = self.config.get_settings()
        behavior_settings = all_settings.get("Behavior", {})
        
        current_min = behavior_settings.get("post_comment_delay_min", "0")
        current_max = behavior_settings.get("post_comment_delay_max", "60")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Мгновенно (0-30 сек)", callback_data="post_delay_preset|0|30")],
            [InlineKeyboardButton(text="⏩ Быстро (5-60 сек)", callback_data="post_delay_preset|5|60")],
            [InlineKeyboardButton(text="⏱ Средне (30-180 сек)", callback_data="post_delay_preset|30|180")],
            [InlineKeyboardButton(text="🐢 Медленно (60-300 сек)", callback_data="post_delay_preset|60|300")],
            [InlineKeyboardButton(text="✏️ Настроить вручную", callback_data="post_delay_manual")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="behavior_settings")]
        ])
        
        await message.answer(
            f"⏱ <b>Задержка после публикации поста</b>\n\n"
            f"Эта настройка определяет, через сколько времени после появления поста в канале будет отправлен комментарий.\n\n"
            f"📈 <b>Текущие значения:</b>\n"
            f"• Минимум: <code>{current_min}</code> сек\n"
            f"• Максимум: <code>{current_max}</code> сек\n\n"
            f"Выберите готовый вариант или настройте вручную:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    async def update_api_keys_menu(self, message: Message):
        """Меню управления API ключами"""
        api_keys = self.config.get_api_keys()
        
        telegram_api_id = api_keys.get('telegram_api_id', 'не установлен')
        telegram_api_hash = api_keys.get('telegram_api_hash', 'не установлен')
        mistral_api_key = api_keys.get('mistral_api_key', 'не установлен')
        
        # Маскировка для безопасности
        def mask(s):
            s = str(s).strip()
            if not s or s in ['не установлен', 'ВАШ_API_ID', 'ВАШ_API_HASH', 'ВАШ_MISTRAL_API_KEY']:
                return "❌ Не установлен"
            if len(s) < 10:
                return "***"
            return s[:4] + "..." + s[-4:]

        text = (
            "🔐 <b>Управление API ключами</b>\n\n"
            "Здесь вы можете изменить глобальные API ключи бота.\n"
            "⚠️ Ключи используются для всех аккаунтов по умолчанию.\n\n"
            "📤 <b>Telegram API</b>\n"
            f"• ID: <code>{mask(telegram_api_id)}</code>\n"
            f"• Hash: <code>{mask(telegram_api_hash)}</code>\n\n"
            "🧠 <b>Mistral API</b>\n"
            f"• Key: <code>{mask(mistral_api_key)}</code>"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изм. Telegram API ID", callback_data="set_api_id_global")],
            [InlineKeyboardButton(text="✏️ Изм. Telegram API Hash", callback_data="set_api_hash_global")],
            [InlineKeyboardButton(text="✏️ Изм. Mistral API Key", callback_data="set_mistral_key_global")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="settings_menu")]
        ])
        
        try:
            await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    
    async def show_settings_category(self, message: Message, category: str):
        """Показать настройки категории (понятный вид)"""
        settings_map = {
            "mistral_settings": ("Mistral", "🧠 Генерация комментариев"),
            "behavior_settings": ("Behavior", "⏩ Паузы и очередь"),
            "security_settings": ("Security", "🔒 Безопасность (расширенные)"),
            "content_settings": ("Content", "📝 Текст и стиль"),
            "logging_settings": ("Logging", "📈 Логи (расширенные)")
        }

        section, title = settings_map[category]
        all_settings = self.config.get_settings()
        settings = all_settings.get(section, {})

        schema = self._settings_schema().get(section, {})
        order = self._settings_order().get(section, [])

        # Какие ключи показываем в обычном режиме
        # Показываем даже те, которых еще нет в settings.ini — тогда будет «—» и их можно быстро задать
        visible_keys = [k for k in order if schema.get(k, {}).get("visible", True)]

        # Для Mistral скрываем ключи/служебные параметры из списка (их меняют через API ключи)
        if section == "Mistral":
            hidden = {"telegram_api_id", "telegram_api_hash", "mistral_api_key"}
            visible_keys = [k for k in visible_keys if k not in hidden]

        intro = self._section_intro(section)

        lines = [f"<b>{title}</b>", "", intro, "", "<b>Текущие значения:</b>"]
        for key in visible_keys:
            meta = schema.get(key, {})
            label = meta.get("label", key)
            short = meta.get("short", "")
            value = settings.get(key, "")
            pretty = self._format_setting_value(section, key, value)
            if short:
                lines.append(f"• <b>{label}</b>: <code>{pretty}</code>\n  <i>{short}</i>")
            else:
                lines.append(f"• <b>{label}</b>: <code>{pretty}</code>")

        # Дополнительные/неописанные настройки (чтобы ничего не терялось)
        extra_keys = [k for k in settings.keys() if k not in visible_keys]
        # для Mistral скрытые ключи показываем отдельным блоком и объясняем, что лучше менять через меню ключей
        if section == "Mistral":
            extra_keys = [k for k in extra_keys if k in {"telegram_api_id", "telegram_api_hash", "mistral_api_key"}]

        if extra_keys and section != "Content":
            lines.append("")
            lines.append("<b>Дополнительно:</b>")
            for k in extra_keys:
                v = settings.get(k, "")
                pretty = self._format_setting_value(section, k, v, for_list=True)
                if section == "Mistral" and k in {"telegram_api_id", "telegram_api_hash", "mistral_api_key"}:
                    lines.append(f"• <b>{k}</b>: <code>{pretty}</code>\n  <i>Обычно меняют через «🔐 API ключи»</i>")
                else:
                    lines.append(f"• <b>{k}</b>: <code>{pretty}</code>")

        text = "\n".join([x for x in lines if x is not None])

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])

        # Кнопки редактирования
        for key in visible_keys:
            meta = schema.get(key, {})
            label = meta.get("label", key)
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"edit_setting_{section}_{key}")
            ])

        # Быстрые действия
        if section == "Mistral":
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔑 Ключи API", callback_data="update_api_keys")])
        
        if section == "Behavior":
            keyboard.inline_keyboard.append([InlineKeyboardButton(text="⏱ Задержка после поста", callback_data="set_post_comment_delay")])
            # Кнопка для включения/отключения скана
            behavior_settings = all_settings.get("Behavior", {})
            scan_enabled = behavior_settings.get("enable_channel_scan", "yes").lower() in ("yes", "true", "1", "on", "да", "вкл")
            scan_button_text = "👁 Анализ каналов: ВКЛ" if scan_enabled else "👁 Анализ каналов: ВЫКЛ"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=scan_button_text, callback_data="toggle_channel_scan")])
        
        if section == "Content":
            # Кнопка для отключения всех фильтров
            content_settings = all_settings.get("Content", {})
            filters_disabled = content_settings.get("disable_all_filters", "no").lower() in ("yes", "true", "1", "on", "да", "вкл")
            filters_button_text = "🔞 Фильтры: ОТКЛ" if filters_disabled else "✅ Фильтры: ВКЛ"
            keyboard.inline_keyboard.append([InlineKeyboardButton(text=filters_button_text, callback_data="toggle_all_filters")])

        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data="settings_menu")
        ])

        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    async def analyze_channels(self, account_name: str, channel_refs: List[str]) -> Dict[str, Any]:
        """Анализ контента каналов перед запуском комментирования с детектированием дубликатов"""
        try:
            client = await self.account_manager.connect(account_name)
            security = self.account_manager.get_security_manager(account_name)
            
            analysis_results = {}
            channels_to_analyze = []  # Каналы, которые нужно проанализировать
            channels_reused = []  # Каналы, для которых используем существующий анализ
            
            # Проверяем каждый канал на наличие существующего анализа
            for channel_url in channel_refs:
                existing_analysis = self.db.get_channel_analysis_by_url(channel_url)
                if existing_analysis:
                    # Используем существующий анализ
                    try:
                        analysis_data = json.loads(existing_analysis['analysis_data'])
                        channels_reused.append((channel_url, analysis_data))
                        
                        # Разбрасываем данные на все аккаунты, использующие этот канал
                        accounts_using_channel = self.db.get_accounts_for_channel(channel_url)
                        for acc_name in accounts_using_channel:
                            # Используем существующий peer_id или получаем новый
                            try:
                                info = await self.channel_monitor.get_channel_info(client, channel_url, account_name=acc_name)
                                entity = info.get("entity")
                                if entity:
                                    channel_peer_id = str(int(utils.get_peer_id(entity)))
                                else:
                                    channel_peer_id = existing_analysis.get('channel_peer_id', '')
                            except Exception:
                                # Если не удалось получить peer_id, используем старый
                                channel_peer_id = existing_analysis.get('channel_peer_id', '')
                            
                            self.db.save_channel_analysis(
                                account_name=acc_name,
                                channel_url=channel_url,
                                channel_peer_id=channel_peer_id,
                                analysis_data=existing_analysis['analysis_data'],
                                posts_count=existing_analysis['posts_count']
                            )
                    except Exception as e:
                        security.log_activity(f"Ошибка при использовании существующего анализа {channel_url}: {type(e).__name__}")
                        channels_to_analyze.append(channel_url)
                else:
                    # Нужно проанализировать
                    channels_to_analyze.append(channel_url)
            
            # Анализируем только новые каналы
            for idx, channel_url in enumerate(channels_to_analyze, 1):
                try:
                    # Получаем информацию о канале
                    info = await self.channel_monitor.get_channel_info(client, channel_url, account_name=account_name)
                    entity = info.get("entity")
                    if not entity:
                        continue
                    
                    channel_peer_id = str(int(utils.get_peer_id(entity)))
                    channel_title = getattr(entity, "title", channel_url)
                    
                    # Читаем последние посты (безопасно, с задержками)
                    posts_texts = []
                    try:
                        # Читаем последние 15 постов с безопасными задержками
                        messages = await client.get_messages(entity, limit=15)
                        
                        for msg in messages:
                            if hasattr(msg, "message") and msg.message:
                                text = msg.message.strip()
                                if text and len(text) > 20:  # Игнорируем очень короткие посты
                                    posts_texts.append(text)
                            
                            # Безопасная задержка между запросами
                            if len(posts_texts) % 5 == 0:
                                await asyncio.sleep(random.uniform(2, 4))
                    
                    except Exception as e:
                        security.log_activity(f"Ошибка чтения постов из {channel_url}: {type(e).__name__}")
                        continue
                    
                    if not posts_texts:
                        continue
                    
                    # Анализ контента
                    all_text = " ".join(posts_texts).lower()
                    
                    # Извлекаем ключевые слова (простые слова длиной > 4 символов)
                    words = [w for w in all_text.split() if len(w) > 4 and w.isalpha()]
                    word_freq = Counter(words).most_common(20)
                    
                    # Определяем тематику (по ключевым словам)
                    topics = []
                    tech_keywords = ["программирование", "разработка", "код", "алгоритм", "технология", "it", "python", "javascript"]
                    edu_keywords = ["обучение", "курс", "урок", "образование", "студент", "университет"]
                    news_keywords = ["новость", "событие", "происшествие", "новости", "репортаж"]
                    
                    if any(kw in all_text for kw in tech_keywords):
                        topics.append("технологии")
                    if any(kw in all_text for kw in edu_keywords):
                        topics.append("образование")
                    if any(kw in all_text for kw in news_keywords):
                        topics.append("новости")
                    
                    if not topics:
                        topics = ["общее"]
                    
                    # Средняя длина поста
                    avg_length = sum(len(t) for t in posts_texts) / len(posts_texts) if posts_texts else 0
                    
                    # Стиль (формальный/неформальный по наличию эмодзи и сленга)
                    has_emoji = any(any(ord(c) > 127 for c in text) for text in posts_texts[:5])
                    style = "неформальный" if has_emoji else "формальный"
                    
                    analysis_data = {
                        "topics": topics,
                        "keywords": [w[0] for w in word_freq[:10]],
                        "avg_post_length": int(avg_length),
                        "style": style,
                        "posts_analyzed": len(posts_texts),
                        "channel_title": channel_title
                    }
                    
                    # Сохраняем анализ для всех аккаунтов, использующих этот канал
                    accounts_using_channel = self.db.get_accounts_for_channel(channel_url)
                    for acc_name in accounts_using_channel:
                        try:
                            # Получаем peer_id для каждого аккаунта
                            acc_info = await self.channel_monitor.get_channel_info(client, channel_url, account_name=acc_name)
                            acc_entity = acc_info.get("entity")
                            if acc_entity:
                                acc_peer_id = str(int(utils.get_peer_id(acc_entity)))
                            else:
                                acc_peer_id = channel_peer_id
                        except Exception:
                            acc_peer_id = channel_peer_id
                        
                        self.db.save_channel_analysis(
                            account_name=acc_name,
                            channel_url=channel_url,
                            channel_peer_id=acc_peer_id,
                            analysis_data=json.dumps(analysis_data, ensure_ascii=False),
                            posts_count=len(posts_texts)
                        )
                    
                    analysis_results[channel_url] = analysis_data
                    
                    # Задержка между каналами для безопасности
                    await asyncio.sleep(random.uniform(3, 6))
                    
                except Exception as e:
                    security.log_activity(f"Ошибка анализа канала {channel_url}: {type(e).__name__}: {e}")
                    continue
            
            # Добавляем результаты для каналов с переиспользованным анализом
            for channel_url, analysis_data in channels_reused:
                analysis_results[channel_url] = analysis_data
                analysis_results[channel_url]["_reused"] = True  # Флаг переиспользования
            
            await client.disconnect()
            return {
                "results": analysis_results,
                "analyzed_count": len(channels_to_analyze),
                "reused_count": len(channels_reused),
                "total_count": len(channel_refs)
            }
            
        except Exception as e:
            return {"error": str(e)}

    async def start_monitoring(self, message: Message, account_name: str):
        """Запуск реального мониторинга (Telethon) для аккаунта."""
        if account_name in self.running_monitors:
            await message.answer(f"❌ Монитор для {account_name} уже запущен.")
            return

        # Каналы, привязанные к аккаунту
        channels = self.db.get_account_channels(account_name)
        channel_refs = [c.get("url") for c in channels if c.get("url")]
        channel_refs = list(dict.fromkeys(channel_refs))  # дедупликация

        if not channel_refs:
            await message.answer(
                f"❌ Для <b>{account_name}</b> не привязано ни одного канала.\n"
                f"Откройте «📺 Каналы» → «➕ Добавить канал» и привяжите хотя бы один канал.",
                parse_mode="HTML",
            )
            return

        # Проверяем настройку анализа и флаг перезапуска
        all_settings = self.config.get_settings()
        behavior_settings = all_settings.get("Behavior", {})
        scan_enabled = behavior_settings.get("enable_channel_scan", "yes").lower() in ("yes", "true", "1", "on", "да", "вкл")
        scan_already_performed = account_name in self._scan_performed
        
        status_msg = None
        
        # Запускаем анализ только если:
        # 1. Настройка включена
        # 2. Скан еще не выполнялся для этого аккаунта при текущем запуске бота
        if scan_enabled and not scan_already_performed:
            # Уведомление о начале анализа
            status_msg = await message.answer(
                f"🔎 <b>Анализ каналов для {account_name}</b>\n\n"
                f"📈 Анализирую контент {len(channel_refs)} каналов...\n"
                f"⏳ Это займет некоторое время.",
                parse_mode="HTML",
            )

            # Запускаем анализ
            analysis_response = await self.analyze_channels(account_name, channel_refs)
            
            # Обрабатываем ответ (может быть старый формат или новый)
            if isinstance(analysis_response, dict) and "results" in analysis_response:
                analysis_results = analysis_response["results"]
                analyzed_count = analysis_response.get("analyzed_count", 0)
                reused_count = analysis_response.get("reused_count", 0)
            else:
                # Старый формат для обратной совместимости
                analysis_results = analysis_response
                analyzed_count = len([v for v in analysis_results.values() if "error" not in v and not v.get("_reused", False)])
                reused_count = len([v for v in analysis_results.values() if v.get("_reused", False)])
            
            # Формируем отчет об анализе
            analysis_report = []
            reused_report = []
            successful = 0
            for channel_url, data in analysis_results.items():
                if "error" not in data:
                    successful += 1
                    topics = ", ".join(data.get("topics", []))
                    channel_title = data.get('channel_title', channel_url)
                    if data.get("_reused", False):
                        reused_report.append(f"• {channel_title}: {topics} (переиспользован)")
                    else:
                        analysis_report.append(f"• {channel_title}: {topics}")
            
            # Обновляем сообщение с результатами анализа
            report_text = f"✅ <b>Анализ завершен</b>\n\n"
            report_text += f"📈 Новых проанализировано: {analyzed_count}\n"
            if reused_count > 0:
                report_text += f"♻️ Переиспользовано: {reused_count}\n"
            report_text += f"📺 Всего каналов: {len(channel_refs)}\n\n"
            
            if analysis_report:
                report_text += "<b>Новые результаты:</b>\n" + "\n".join(analysis_report[:3])
                if len(analysis_report) > 3:
                    report_text += f"\n... и еще {len(analysis_report) - 3} каналов"
            
            if reused_report:
                report_text += "\n\n<b>Переиспользованы:</b>\n" + "\n".join(reused_report[:2])
                if len(reused_report) > 2:
                    report_text += f"\n... и еще {len(reused_report) - 2} каналов"
            
            report_text += "\n\n🚀 Запуск комментирования через 10 секунд..."
            
            await status_msg.edit_text(report_text, parse_mode="HTML")
            
            # Ждем 10 секунд перед запуском комментирования
            await asyncio.sleep(10)
            
            # Отмечаем, что скан выполнен для этого аккаунта
            self._scan_performed.add(account_name)
        else:
            # Скан пропущен - сразу запускаем комментирование
            if not status_msg:
                status_msg = await message.answer(
                    f"▶️ <b>Запуск мониторинга для {account_name}</b>\n\n"
                    f"📺 Каналов: {len(channel_refs)}\n"
                    f"💨 Анализ каналов пропущен (отключен или уже выполнен).",
                    parse_mode="HTML",
                )

        stop_event = asyncio.Event()

        async def _runner():
            await self.channel_monitor.run(
                account_name=account_name,
                channels=channel_refs,
                stop_event=stop_event,
            )

        task = asyncio.create_task(_runner(), name=f"monitor:{account_name}")

        self.running_monitors[account_name] = {
            "start_time": datetime.now(),
            "task": task,
            "stop_event": stop_event,
            "channels": channel_refs,
        }

        # Если задача упала сразу — покажем причину
        await asyncio.sleep(0.2)
        if task.done():
            exc = task.exception()
            self.running_monitors.pop(account_name, None)
            if exc:
                await status_msg.edit_text(
                    f"❌ Не удалось запустить мониторинг для <b>{account_name}</b>.\n\n"
                    f"<code>{type(exc).__name__}: {exc}</code>\n\n"
                    f"Чаще всего причина одна из:\n"
                    f"• нет Telegram API ключей (telegram_api_id / telegram_api_hash)\n"
                    f"• нет сессии sessions/{account_name}.session (нужно заново подключить аккаунт)\n"
                    f"• сессия не авторизована / разлогинилась\n",
                    parse_mode="HTML",
                )
                return

        if status_msg:
            await status_msg.edit_text(
                f"✅ <b>Мониторинг запущен</b>\n\n"
                f"📺 Каналов: {len(channel_refs)}\n"
                f"⏱ Время запуска: {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"🧠 Бот начал комментировать новые посты.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"✅ <b>Мониторинг запущен</b>\n\n"
                f"📺 Каналов: {len(channel_refs)}\n"
                f"⏱ Время запуска: {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"🧠 Бот начал комментировать новые посты.",
                parse_mode="HTML",
            )

        def _on_done(t: asyncio.Task):
            try:
                err = t.exception()
            except asyncio.CancelledError:
                err = None
            except Exception:
                err = None

            self.running_monitors.pop(account_name, None)
            if err:
                logger.error("Monitor %s stopped with error: %s: %s", account_name, type(err).__name__, err)

        task.add_done_callback(_on_done)

        await message.answer(
            f"✅ Мониторинг для <b>{account_name}</b> запущен.\n"
            f"📺 Каналов: <b>{len(channel_refs)}</b>\n\n"
            f"ℹ️ Комментарии публикуются не мгновенно: применяется задержка из settings.ini.",
            parse_mode="HTML",
        )


    async def stop_monitoring(self, message: Message, account_name: str):
        """Остановка мониторинга для аккаунта"""
        info = self.running_monitors.get(account_name)
        if not info:
            await message.answer(f"❌ Монитор для {account_name} не запущен.")
            return

        stop_event = info.get("stop_event")
        task = info.get("task")

        try:
            if stop_event and not stop_event.is_set():
                stop_event.set()
        except Exception:
            pass

        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=10)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
            except Exception:
                pass

        self.running_monitors.pop(account_name, None)
        await message.answer(f"✅ Мониторинг для <b>{account_name}</b> остановлен.", parse_mode="HTML")


    def get_uptime(self):
        """Получить время работы"""
        if not self.running_monitors:
            return "Не запущено"
        
        first_start = min([monitor['start_time'] for monitor in self.running_monitors.values()])
        delta = datetime.now() - first_start
        
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        seconds = delta.seconds % 60
        
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def get_main_keyboard(self):
        """Создание основной клавиатуры"""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📝 Аккаунты"), KeyboardButton(text="📺 Каналы")],
                [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="▶️ Запустить")],
                [KeyboardButton(text="⏹️ Остановить"), KeyboardButton(text="📈 Статистика")]
            ],
            resize_keyboard=True,
            input_field_placeholder="Выберите действие..."
        )
        return keyboard
    
    def update_accounts_file(self):
        """Обновление файла accounts.txt"""
        accounts = self.db.get_all_accounts()
        
        with open('accounts.txt', 'w', encoding='utf-8') as f:
            for account in accounts:
                f.write(f"{account['name']}:{account['phone']}\n")
    
    def update_channels_file(self):
        """Зеркалим каналы в channels.txt (для отладки/совместимости).

        Формат строки: account_name|channel_url
        ВАЖНО: таблица channels не содержит is_active, поэтому фильтрацию делаем по аккаунту.
        """
        try:
            # Получаем всех пользователей (или только текущего - тут упрощение)
            # В идеале нужно пройтись по всем пользователям
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM users')
            users = cursor.fetchall()
            conn.close()

            lines_out = []
            
            # Собираем каналы всех пользователей
            for user_row in users:
                user_id = user_row[0]
                accounts = self.db.get_user_accounts(user_id)
                for a in accounts:
                    # если в БД нет поля is_active (старая схема) — считаем аккаунт активным
                    if not int(a.get('is_active', 1) or 0):
                        continue

                    chs = self.db.get_account_channels(a['name'])
                    for c in chs:
                        url = (c.get('url') or '').strip()
                        if url:
                            lines_out.append(f"{a['name']}|{url}")

            with open('channels.txt', 'w', encoding='utf-8') as f:
                for line in lines_out:
                    f.write(line + '\n')
        except Exception:
            return

    # ====================
    # Понятные настройки (UI + валидация)
    # ====================

    def _settings_order(self) -> Dict[str, List[str]]:
        return {
            "Mistral": ["model", "max_tokens", "temperature", "top_p", "telegram_api_id", "telegram_api_hash", "mistral_api_key"],
            "Behavior": [
                "enable_channel_scan",
                "post_comment_delay_min", "post_comment_delay_max",
                "min_comment_delay", "max_comment_delay",
                "inter_account_delay_min", "inter_account_delay_max",
                "daily_comment_limit",
                "mistral_request_gap_seconds",
                "min_delay_between_actions", "max_delay_between_actions",
                "skip_posts_with_links", "skip_sponsored_posts",
                "use_fillers",
            ],
            "Content": [
                "disable_all_filters",
                "min_post_length", "max_post_length",
                "blacklist_words",
                "comment_tone",
                "comment_sentences_min", "comment_sentences_max",
                "allow_emoji",
                "language",
            ],
            "Security": ["flood_wait_handling", "max_retries", "use_2fa", "session_backup"],
            "Logging": ["log_level", "log_to_file", "max_log_size_mb", "keep_logs_days"],
        }

    def _settings_schema(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        # visible=False — скрываем из обычного списка, но не запрещаем
        return {
            "Mistral": {
                "model": {
                    "label": "Модель",
                    "short": "Какая модель будет писать комментарии.",
                    "desc": "Можно оставить как есть. Если смените — убедитесь, что модель доступна в вашем API Mistral.",
                    "type": "str",
                    "examples": ["mistral-small-latest", "mistral-medium-latest"],
                },
                "max_tokens": {
                    "label": "Длина ответа (max_tokens)",
                    "short": "Чем больше — тем длиннее комментарии.",
                    "desc": "Обычно 80–200 хватает для коротких комментариев.",
                    "type": "int",
                    "min": 20,
                    "max": 800,
                    "unit": "токенов",
                    "quick": ["80", "120", "160", "200"],
                },
                "temperature": {
                    "label": "Креативность (temperature)",
                    "short": "0.2 — строго и ровно, 0.7 — нормально, 1.0 — более свободно.",
                    "desc": "Если комментарии слишком однотипные — чуть увеличьте. Если слишком «уносит» — уменьшите.",
                    "type": "float",
                    "min": 0.0,
                    "max": 1.5,
                    "quick": ["0.3", "0.5", "0.7", "0.9"],
                },
                "top_p": {
                    "label": "Разнообразие (top_p)",
                    "short": "Доп. настройка разнообразия текста. Обычно 0.9–1.0.",
                    "desc": "Если не уверены — оставьте 0.9.",
                    "type": "float",
                    "min": 0.1,
                    "max": 1.0,
                    "quick": ["0.8", "0.9", "1.0"],
                },
                # ключи/служебные — лучше менять через меню API ключей
                "telegram_api_id": {"label": "Telegram API ID", "type": "int", "visible": False},
                "telegram_api_hash": {"label": "Telegram API HASH", "type": "str", "visible": False},
                "mistral_api_key": {"label": "Mistral API key", "type": "str", "visible": False},
            },
            "Behavior": {
                "enable_channel_scan": {
                    "label": "Анализ каналов при запуске",
                    "short": "Включить автоматический анализ каналов перед комментированием.",
                    "desc": "Если включено — бот будет анализировать контент каналов при запуске мониторинга.\n\n",
                    "type": "bool",
                },
                "post_comment_delay_min": {
                    "label": "Задержка после поста (мин.)",
                    "short": "Минимальная задержка после публикации поста перед отправкой комментария.",
                    "desc": "Через сколько секунд после появления поста начать отправку комментария.",
                    "type": "int",
                    "min": 0,
                    "max": 3600,
                    "unit": "сек",
                    "quick": ["0", "5", "10", "30", "60"],
                },
                "post_comment_delay_max": {
                    "label": "Задержка после поста (макс.)",
                    "short": "Максимальная задержка после публикации поста перед отправкой комментария.",
                    "desc": "Ставьте больше min. Если поставить меньше — я сам поправлю.",
                    "type": "int",
                    "min": 0,
                    "max": 7200,
                    "unit": "сек",
                    "quick": ["30", "60", "120", "300", "600"],
                },
                "min_comment_delay": {
                    "label": "Пауза перед комментом (мин.)",
                    "short": "Минимальная задержка перед отправкой комментария.",
                    "desc": "Чем больше паузы — тем естественнее выглядит активность.",
                    "type": "int",
                    "min": 0,
                    "max": 600,
                    "unit": "сек",
                    "quick": ["10", "15", "30", "45"],
                },
                "max_comment_delay": {
                    "label": "Пауза перед комментом (макс.)",
                    "short": "Максимальная задержка перед отправкой комментария.",
                    "desc": "Ставьте больше min. Если поставить меньше — я сам поправлю.",
                    "type": "int",
                    "min": 0,
                    "max": 900,
                    "unit": "сек",
                    "quick": ["10", "20", "30", "60", "90", "120"],
                },
                "inter_account_delay_min": {
                    "label": "Очередь между аккаунтами (мин.)",
                    "short": "Если один канал комментируют несколько аккаунтов — выдерживаем паузу между ними.",
                    "desc": "Рекомендую 10–20 секунд, чтобы не было «залпа» в один момент.",
                    "type": "int",
                    "min": 0,
                    "max": 120,
                    "unit": "сек",
                    "quick": ["10", "12", "15", "20"],
                },
                "inter_account_delay_max": {
                    "label": "Очередь между аккаунтами (макс.)",
                    "short": "Верхняя граница паузы между аккаунтами.",
                    "desc": "Ставьте больше min. Если поставить меньше — я сам поправлю.",
                    "type": "int",
                    "min": 0,
                    "max": 300,
                    "unit": "сек",
                    "quick": ["15", "20", "25", "30"],
                },
                "daily_comment_limit": {
                    "label": "Лимит комментариев в сутки",
                    "short": "Ограничение на аккаунт. 0 = без лимита.",
                    "desc": "Для обычной безопасной работы часто ставят 20–80.",
                    "type": "int",
                    "min": 0,
                    "max": 2000,
                    "quick": ["20", "50", "80", "120", "0"],
                },
                "mistral_request_gap_seconds": {
                    "label": "Пауза между запросами к Mistral",
                    "short": "Помогает не упираться в лимиты при большой активности.",
                    "desc": "Если бывают ошибки по лимитам — увеличьте до 1.5–2.0.",
                    "type": "float",
                    "min": 0.0,
                    "max": 10.0,
                    "unit": "сек",
                    "quick": ["0.8", "1.0", "1.2", "1.5"],
                },
                "min_delay_between_actions": {
                    "label": "Пауза между действиями (мин.)",
                    "short": "Общая пауза для фоновых действий (расширенная настройка).",
                    "desc": "Если не понимаете — можно не трогать.",
                    "type": "int",
                    "min": 0,
                    "max": 600,
                    "unit": "сек",
                    "quick": ["5", "10", "20"],
                },
                "max_delay_between_actions": {
                    "label": "Пауза между действиями (макс.)",
                    "short": "Общая максимальная пауза для фоновых действий (расширенная настройка).",
                    "desc": "Ставьте больше min. Если поставить меньше — я сам поправлю.",
                    "type": "int",
                    "min": 0,
                    "max": 1200,
                    "unit": "сек",
                    "quick": ["30", "60", "90"],
                },
                "skip_posts_with_links": {
                    "label": "Пропускать посты со ссылками",
                    "short": "Если включено — бот не будет комментировать посты с ссылками.",
                    "desc": "Полезно, если вы не хотите активность под рекламными/переходными постами.",
                    "type": "bool",
                },
                "skip_sponsored_posts": {
                    "label": "Пропускать рекламные посты",
                    "short": "Если включено — бот старается пропускать посты, похожие на рекламу.",
                    "desc": "Работает по ключевым словам (пример: «реклама», «promo», «акция»).",
                    "type": "bool",
                },
                "use_fillers": {
                    "label": "Добавлять разговорные обороты",
                    "short": "Экспериментальная настройка.",
                    "desc": "Если включено — иногда будут добавляться короткие «разговорные» обороты.",
                    "type": "bool",
                },
            },
            "Content": {
                "disable_all_filters": {
                    "label": "Отключить все фильтры постов",
                    "short": "Комментировать любой пост без проверок.",
                    "desc": "Если включено — бот будет комментировать все посты, игнорируя фильтры по длине, черный список, рекламу и ссылки.",
                    "type": "bool",
                },
                "min_post_length": {
                    "label": "Минимальная длина поста",
                    "short": "Если пост очень короткий — бот его пропустит.",
                    "desc": "Защищает от комментирования под короткими анонсами.",
                    "type": "int",
                    "min": 0,
                    "max": 20000,
                    "unit": "символов",
                    "quick": ["30", "50", "80", "120"],
                },
                "max_post_length": {
                    "label": "Максимальная длина поста",
                    "short": "Если пост слишком длинный — бот пропустит (или возьмёт только начало).",
                    "desc": "Обычно 3000–8000 достаточно.",
                    "type": "int",
                    "min": 100,
                    "max": 50000,
                    "unit": "символов",
                    "quick": ["3000", "5000", "8000", "12000"],
                },
                "blacklist_words": {
                    "label": "Чёрный список слов",
                    "short": "Если в посте есть эти слова — пост пропускается.",
                    "desc": "Пишите через запятую. Пример: реклама, продам, скидка, spam",
                    "type": "csv",
                },
                "comment_tone": {
                    "label": "Тон комментариев",
                    "short": "Нейтрально, дружелюбно, профессионально или с юмором.",
                    "desc": "«auto» — подбирается автоматически.",
                    "type": "enum",
                    "choices": ["auto", "neutral", "friendly", "professional", "humorous"],
                    "choice_labels": {
                        "auto": "Авто",
                        "neutral": "Нейтрально",
                        "friendly": "Дружелюбно",
                        "professional": "Профессионально",
                        "humorous": "С юмором",
                    },
                },
                "comment_sentences_min": {
                    "label": "Длина (предложений) — мин.",
                    "short": "Минимум предложений в комментарии.",
                    "desc": "Обычно 1–2 предложения.",
                    "type": "int",
                    "min": 1,
                    "max": 6,
                    "unit": "предл.",
                    "quick": ["1", "2", "3"],
                },
                "comment_sentences_max": {
                    "label": "Длина (предложений) — макс.",
                    "short": "Максимум предложений в комментарии.",
                    "desc": "Ставьте больше или равно min.",
                    "type": "int",
                    "min": 1,
                    "max": 8,
                    "unit": "предл.",
                    "quick": ["2", "3", "4"],
                },
                "allow_emoji": {
                    "label": "Эмодзи",
                    "short": "Добавлять ли эмодзи в комментарии.",
                    "desc": "auto — по ситуации, yes — можно, no — никогда.",
                    "type": "enum",
                    "choices": ["auto", "yes", "no"],
                    "choice_labels": {"auto": "Авто", "yes": "Да", "no": "Нет"},
                },
                "language": {
                    "label": "Язык комментариев",
                    "short": "Экспериментальная настройка (может не влиять).",
                    "desc": "auto/ru/en",
                    "type": "enum",
                    "choices": ["auto", "ru", "en"],
                    "choice_labels": {"auto": "Авто", "ru": "Русский", "en": "English"},
                    "visible": False,
                },
            },
            "Security": {
                "use_2fa": {
                    "label": "Использовать 2FA",
                    "short": "Если у аккаунта включен пароль Telegram — он понадобится при входе.",
                    "desc": "Обычно можно оставить как есть.",
                    "type": "bool",
                },
                "max_retries": {
                    "label": "Повторы при ошибках",
                    "short": "Сколько раз повторять при временной ошибке.",
                    "desc": "Рекомендую 2–5.",
                    "type": "int",
                    "min": 0,
                    "max": 10,
                    "quick": ["1", "3", "5"],
                },
                "flood_wait_handling": {
                    "label": "Если Telegram просит подождать (FloodWait)",
                    "short": "adaptive — ждём сколько нужно, strict — с запасом, ignore — не рекомендую.",
                    "desc": "Если хотите максимально безопасно — strict.",
                    "type": "enum",
                    "choices": ["adaptive", "strict", "ignore"],
                    "choice_labels": {"adaptive": "Адаптивно", "strict": "Строго", "ignore": "Игнорировать"},
                },
                "session_backup": {
                    "label": "Резервные копии сессий",
                    "short": "Хранить бэкапы файлов сессий (рекомендуется).",
                    "desc": "Помогает восстановиться после сбоев.",
                    "type": "bool",
                },
            },
            "Logging": {
                "log_level": {
                    "label": "Уровень логов",
                    "short": "INFO — обычно достаточно. DEBUG — для диагностики.",
                    "desc": "Чем выше детализация — тем больше файл логов.",
                    "type": "enum",
                    "choices": ["DEBUG", "INFO", "WARNING", "ERROR"],
                    "choice_labels": {"DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARNING", "ERROR": "ERROR"},
                },
                "log_to_file": {
                    "label": "Записывать логи в файл",
                    "short": "Если выключить — логи будут только в консоли.",
                    "desc": "Для сервера обычно лучше включить.",
                    "type": "bool",
                },
                "max_log_size_mb": {
                    "label": "Макс. размер лога",
                    "short": "Когда лог достигает лимита — ротируется (если включено).",
                    "desc": "Обычно 5–20 МБ.",
                    "type": "int",
                    "min": 1,
                    "max": 200,
                    "unit": "МБ",
                    "quick": ["5", "10", "20"],
                },
                "keep_logs_days": {
                    "label": "Хранить логи дней",
                    "short": "Сколько дней хранить старые логи.",
                    "desc": "Обычно 3–14 дней.",
                    "type": "int",
                    "min": 0,
                    "max": 365,
                    "unit": "дней",
                    "quick": ["3", "7", "14", "30"],
                },
            },
        }

    def _section_intro(self, section: str) -> str:
        intro_map = {
            "Mistral": "Параметры генерации текста (как «пишет» ИИ).",
            "Behavior": "Паузы и очередь — чтобы комментарии выглядели естественно и не были одинаково «мгновенными».",
            "Content": "Фильтры и стиль комментариев.",
            "Security": "Расширенные параметры. Если не уверены — оставьте по умолчанию.",
            "Logging": "Расширенные параметры логов. Нужны в основном для диагностики.",
        }
        return intro_map.get(section, "")

    def _category_by_section(self, section: str) -> str:
        return {
            "Mistral": "mistral_settings",
            "Behavior": "behavior_settings",
            "Content": "content_settings",
            "Security": "security_settings",
            "Logging": "logging_settings",
        }.get(section, "settings_menu")

    def _back_to_section_kb(self, section: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к разделу", callback_data=self._category_by_section(section))],
            [InlineKeyboardButton(text="⚙️ Все настройки", callback_data="settings_menu")],
        ])


    def _format_setting_value(self, section: str, key: str, value: str, for_list: bool = False) -> str:
        v = (value or "").strip()
        schema = self._settings_schema().get(section, {})
        meta = schema.get(key, {})
        t = meta.get("type", "str")

        if t == "bool":
            vl = v.lower()
            if vl in ("yes", "true", "1", "on", "да", "вкл", "enabled"):
                return "вкл"
            if vl in ("no", "false", "0", "off", "нет", "выкл", "disabled"):
                return "выкл"
            return v or "—"

        if t == "enum":
            if not v:
                return "—"
            labels = meta.get("choice_labels") or {}
            choices = meta.get("choices") or []
            # Для DEBUG/INFO/... оставляем верхний регистр
            if choices and str(choices[0]).isupper():
                return v.upper()
            vl = v.lower()
            return str(labels.get(vl, labels.get(v, v)))

        if t == "csv":
            if not v:
                return "—"
            if for_list and len(v) > 60:
                return v[:57] + "..."
            return v

        return v or "—"

    def _normalize_and_validate_setting(self, section: str, key: str, raw: str):
        """Returns: (ok, normalized, error, adjustments_dict)"""
        schema = self._settings_schema().get(section, {})
        meta = schema.get(key, {})
        t = meta.get("type", "str")
        raw0 = (raw or "").strip()

        def norm_bool(s: str) -> Optional[str]:
            sl = s.strip().lower()
            if sl in ("yes", "y", "true", "1", "on", "да", "вкл", "включить"):
                return "yes"
            if sl in ("no", "n", "false", "0", "off", "нет", "выкл", "выключить"):
                return "no"
            return None

        adjustments: Dict[str, str] = {}

        try:
            if t == "int":
                if not re.fullmatch(r"-?\d+", raw0):
                    return False, "", "нужно целое число", {}
                val = int(raw0)
                mn = meta.get("min")
                mx = meta.get("max")
                if mn is not None and val < int(mn):
                    return False, "", f"минимум {mn}", {}
                if mx is not None and val > int(mx):
                    return False, "", f"максимум {mx}", {}
                normalized = str(val)

            elif t == "float":
                s = raw0.replace(",", ".")
                if not re.fullmatch(r"-?\d+(\.\d+)?", s):
                    return False, "", "нужно число (например 1.2)", {}
                val = float(s)
                mn = meta.get("min")
                mx = meta.get("max")
                if mn is not None and val < float(mn):
                    return False, "", f"минимум {mn}", {}
                if mx is not None and val > float(mx):
                    return False, "", f"максимум {mx}", {}
                normalized = str(val).rstrip("0").rstrip(".") if "." in str(val) else str(val)

            elif t == "bool":
                b = norm_bool(raw0)
                if b is None:
                    return False, "", "ожидается да/нет (yes/no)", {}
                normalized = b

            elif t == "enum":
                choices = [str(x) for x in (meta.get("choices") or [])]
                if choices and choices[0].isupper():
                    up = raw0.strip().upper()
                    if up not in choices:
                        return False, "", f"разрешено: {', '.join(choices)}", {}
                    normalized = up
                else:
                    sl = raw0.strip().lower()
                    if choices and sl not in choices:
                        return False, "", f"разрешено: {', '.join(choices)}", {}
                    normalized = sl if choices else raw0

            elif t == "csv":
                if raw0.lower() in ("clear", "пусто", "очистить", "-"):
                    normalized = ""
                else:
                    items = [x.strip() for x in raw0.split(",")]
                    items = [x for x in items if x]
                    normalized = ", ".join(items)

            else:
                normalized = raw0

        except Exception:
            return False, "", "не удалось распознать значение", {}

        # Автокоррекция для пар min/max
        def adjust_pair(min_key: str, max_key: str):
            all_settings = self.config.get_settings()
            sec_vals = all_settings.get(section, {})
            if key == min_key:
                try:
                    max_v = int(float(sec_vals.get(max_key, "0")))
                    cur = int(float(normalized))
                    if max_v and cur > max_v:
                        adjustments[max_key] = str(cur)
                except Exception:
                    pass
            if key == max_key:
                try:
                    min_v = int(float(sec_vals.get(min_key, "0")))
                    cur = int(float(normalized))
                    if cur < min_v:
                        adjustments[min_key] = str(cur)
                except Exception:
                    pass

        if section == "Behavior":
            adjust_pair("min_comment_delay", "max_comment_delay")
            adjust_pair("inter_account_delay_min", "inter_account_delay_max")
            adjust_pair("min_delay_between_actions", "max_delay_between_actions")
        if section == "Content":
            adjust_pair("comment_sentences_min", "comment_sentences_max")
            adjust_pair("min_post_length", "max_post_length")

        return True, normalized, "", adjustments

    async def open_setting_editor(self, message: Message, state: FSMContext, section: str, key: str):
        """Открывает понятный экран редактирования настройки."""
        await state.set_state(Form.waiting_for_setting_value)
        await state.update_data(section=section, key=key)

        settings = self.config.get_settings()
        current_value = settings.get(section, {}).get(key, "Не установлено")

        meta = self._settings_schema().get(section, {}).get(key, {})
        label = meta.get("label", key)
        desc = meta.get("desc", "")
        short = meta.get("short", "")
        t = meta.get("type", "str")
        unit = meta.get("unit", "")

        pretty = self._format_setting_value(section, key, current_value)
        hint_lines = []
        if unit:
            hint_lines.append(f"Единицы: <b>{unit}</b>.")
        if t == "bool":
            hint_lines.append("Введите: <code>yes</code>/<code>no</code> (можно: да/нет, вкл/выкл).")
        elif t == "csv":
            hint_lines.append("Введите список через запятую. Чтобы очистить — отправьте <code>clear</code>.")
        elif t == "enum":
            choices = meta.get("choices") or []
            if choices:
                hint_lines.append("Доступные варианты: " + ", ".join([f"<code>{c}</code>" for c in choices]) + ".")
        elif t in ("int", "float"):
            mn = meta.get("min")
            mx = meta.get("max")
            if mn is not None or mx is not None:
                hint_lines.append(f"Диапазон: <code>{mn}</code> — <code>{mx}</code>.")
        examples = meta.get("examples") or []
        if examples:
            hint_lines.append("Примеры: " + ", ".join([f"<code>{e}</code>" for e in examples[:3]]) + ".")

        kb = self._setting_editor_kb(section, key, meta)

        short_block = f"<i>{short}</i>\n\n" if short else ""
        body = "\n".join(hint_lines) if hint_lines else "Отправьте новое значение сообщением."

        await message.answer(
            f"✏️ <b>{label}</b>\n"
            f"🏗 Раздел: <b>{section}</b>\n"
            f"📉 Сейчас: <code>{pretty}</code>\n\n"
            f"{short_block}"
            f"{desc}\n\n"
            f"{body}",
            reply_markup=kb,
            parse_mode="HTML"
        )

    def _setting_editor_kb(self, section: str, key: str, meta: Dict[str, Any]) -> InlineKeyboardMarkup:
        rows = []

        if meta.get("type") == "enum" and meta.get("choices"):
            labels = meta.get("choice_labels") or {}
            row = []
            for c in meta["choices"]:
                text = labels.get(c, c)
                row.append(InlineKeyboardButton(text=str(text), callback_data=f"setv|{section}|{key}|{c}"))
                if len(row) == 2:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)

        if meta.get("type") in ("int", "float") and meta.get("quick"):
            row = []
            for v in meta["quick"]:
                row.append(InlineKeyboardButton(text=str(v), callback_data=f"setv|{section}|{key}|{v}"))
                if len(row) == 3:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)

        if meta.get("type") == "bool":
            rows.append([
                InlineKeyboardButton(text="Вкл.", callback_data=f"setv|{section}|{key}|yes"),
                InlineKeyboardButton(text="Выкл.", callback_data=f"setv|{section}|{key}|no"),
            ])

        rows.append([InlineKeyboardButton(text="ℹ️ Подсказка", callback_data=f"help|{section}|{key}")])
        rows.append([InlineKeyboardButton(text="🔙 Назад к разделу", callback_data=self._category_by_section(section))])
        rows.append([InlineKeyboardButton(text="⚙️ Все настройки", callback_data="settings_menu")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def show_setting_help(self, message: Message, section: str, key: str):
        meta = self._settings_schema().get(section, {}).get(key, {})
        label = meta.get("label", key)
        desc = meta.get("desc", "")
        short = meta.get("short", "")
        t = meta.get("type", "str")

        pieces = [f"ℹ️ <b>{label}</b>", ""]
        if short:
            pieces.append(f"<i>{short}</i>")
            pieces.append("")
        if desc:
            pieces.append(desc)
            pieces.append("")

        if t == "bool":
            pieces.append("Можно написать: <code>yes/no</code> (или да/нет, вкл/выкл).")
        elif t == "csv":
            pieces.append("Формат: слова через запятую. Чтобы очистить — <code>clear</code>.")
        elif t == "enum":
            choices = meta.get("choices") or []
            if choices:
                pieces.append("Варианты: " + ", ".join([f"<code>{c}</code>" for c in choices]) + ".")
        elif t in ("int", "float"):
            mn = meta.get("min")
            mx = meta.get("max")
            if mn is not None or mx is not None:
                pieces.append(f"Диапазон: <code>{mn}</code> — <code>{mx}</code>.")

        await message.answer("\n".join([p for p in pieces if p is not None]), reply_markup=self._back_to_section_kb(section), parse_mode="HTML")

    async def apply_setting_value_from_button(self, message: Message, section: str, key: str, value: str):
        ok, normalized, err, adjustments = self._normalize_and_validate_setting(section, key, value)
        if not ok:
            await message.answer(f"❌ Не удалось применить: <b>{err}</b>", parse_mode="HTML")
            return

        self.update_setting_in_file(section, key, normalized)
        for adj_key, adj_val in adjustments.items():
            self.update_setting_in_file(section, adj_key, adj_val)

        label = self._settings_schema().get(section, {}).get(key, {}).get("label", key)
        pretty = self._format_setting_value(section, key, normalized)
        await message.answer(
            f"✅ Применено: <b>{label}</b> → <code>{pretty}</code>",
            reply_markup=self._back_to_section_kb(section),
            parse_mode="HTML"
        )

    async def apply_settings_preset(self, message: Message, preset: str):
        """
        Применение готового пресета настроек с мгновенным обновлением.
        """
        presets = {
            "formal": {
                ("Behavior", "min_comment_delay"): "20",
                ("Behavior", "max_comment_delay"): "180",
                ("Behavior", "inter_account_delay_min"): "12",
                ("Behavior", "inter_account_delay_max"): "18",
                ("Behavior", "daily_comment_limit"): "40",
                ("Behavior", "mistral_request_gap_seconds"): "1.3",
                ("Content", "comment_tone"): "professional",  # ВАЖНО: это переключает стиль в генераторе
                ("Content", "comment_sentences_min"): "2",
                ("Content", "comment_sentences_max"): "3",
                ("Content", "allow_emoji"): "no",
                ("Mistral", "max_tokens"): "150",
                ("Mistral", "temperature"): "0.6",
                ("Mistral", "top_p"): "0.85",
            },
            "informal": {
                ("Behavior", "min_comment_delay"): "10",
                ("Behavior", "max_comment_delay"): "90",
                ("Behavior", "inter_account_delay_min"): "8",
                ("Behavior", "inter_account_delay_max"): "12",
                ("Behavior", "daily_comment_limit"): "80",
                ("Behavior", "mistral_request_gap_seconds"): "0.9",
                ("Content", "comment_tone"): "informal",  # ВАЖНО: это переключает стиль в генераторе
                ("Content", "comment_sentences_min"): "1",
                ("Content", "comment_sentences_max"): "2",
                ("Content", "allow_emoji"): "yes",
                ("Mistral", "max_tokens"): "100",
                ("Mistral", "temperature"): "0.85",
                ("Mistral", "top_p"): "0.95",
            },
            "friendly": {
                ("Behavior", "min_comment_delay"): "15",
                ("Behavior", "max_comment_delay"): "150",
                ("Behavior", "inter_account_delay_min"): "10",
                ("Behavior", "inter_account_delay_max"): "15",
                ("Behavior", "daily_comment_limit"): "60",
                ("Behavior", "mistral_request_gap_seconds"): "1.1",
                ("Content", "comment_tone"): "friendly",  # ВАЖНО: это переключает стиль в генераторе
                ("Content", "comment_sentences_min"): "1",
                ("Content", "comment_sentences_max"): "2",
                ("Content", "allow_emoji"): "auto",
                ("Mistral", "max_tokens"): "120",
                ("Mistral", "temperature"): "0.75",
                ("Mistral", "top_p"): "0.9",
            },
        }

        preset_key = (preset or "").strip().lower()
        if preset_key not in presets:
            await message.answer("❌ Неизвестный пресет. Доступно: formal / informal / friendly.")
            return

        # 1. Применяем настройки в БД и файл
        for (sec, k), v in presets[preset_key].items():
            self.config.update_setting(sec, k, v)

        # 2. МГНОВЕННОЕ ОБНОВЛЕНИЕ: Заставляем ChannelMonitor перечитать настройки
        if hasattr(self, 'channel_monitor') and self.channel_monitor:
            logger.info(f"🔄 Triggering real-time reload for preset '{preset_key}'...")
            self.channel_monitor.reload_settings()

        # 3. Красивый ответ пользователю
        names = {
            "formal": "🤵 Формальный (Скептик)", 
            "informal": "😄 Неформальный (Простой юзер)", 
            "friendly": "🤝 Дружелюбный (Помощник)"
        }
        
        pretty_name = names.get(preset_key, preset_key)
        
        await message.answer(
            f"✅ Профиль <b>{pretty_name}</b> успешно применен!\n\n"
            f"⚡ Настройки обновлены мгновенно.\n"
            f"🧠 Генератор теперь использует стиль: <code>{presets[preset_key][('Content', 'comment_tone')]}</code>.\n\n"
            "Следующий комментарий уже будет в новом стиле.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏩ Паузы и очередь", callback_data="behavior_settings")],
                [InlineKeyboardButton(text="📝 Текст и стиль", callback_data="content_settings")],
                [InlineKeyboardButton(text="🧠 Генерация комментариев", callback_data="mistral_settings")],
                [InlineKeyboardButton(text="⚙️ Все настройки", callback_data="settings_menu")]
            ]),
            parse_mode="HTML"
        )

    def update_setting_in_file(self, section: str, key: str, value: str):
        """Обновление настройки в settings.ini и перезагрузка модулей"""
        # 1. Сохраняем (код из ConfigManager)
        self.config.update_setting(section, key, value)
        
        # 2. МГНОВЕННОЕ ПРИМЕНЕНИЕ: Заставляем ChannelMonitor перечитать настройки
        if hasattr(self, 'channel_monitor') and self.channel_monitor:
            logger.info(f"🔄 Triggering real-time settings reload (changed {key})...")
            self.channel_monitor.reload_settings()

    async def run(self):
        """Запуск бота"""
        # Удаляем вебхуки, чтобы избегать конфликтов
        await self.bot.delete_webhook(drop_pending_updates=True)
    
        # Добавляем небольшую задержку
        await asyncio.sleep(1)
    
        # Запускаем поллинг
        await self.dp.start_polling(self.bot)