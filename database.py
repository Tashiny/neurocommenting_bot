# database.py
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple


class Database:
    """
    SQLite-слой для бота.

    Цели (под вашу текущую архитектуру):
    - Надёжное хранение пользователей, аккаунтов, каналов и истории комментариев
    - Безопасные UPSERT-операции (без падений на дублях)
    - Устойчивые миграции схемы при обновлениях
    - Включённые foreign keys и каскадное удаление

    Важно:
    - Статус "аккаунт готов" определяется по наличию Telethon-сессии на диске
      (sessions/<account_name>.session) — это не ответственность БД.
    """

    def __init__(self, db_path: str = "neurocommenting.db"):
        self.db_path = db_path
        self.init_database()

    # -----------------------------
    # Connection helpers
    # -----------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # обязательно включаем FK для SQLite
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    # -----------------------------
    # Schema / migrations
    # -----------------------------
    def init_database(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()

            # Users (telegram user id)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_admin INTEGER DEFAULT 0
                )
                """
            )

            # Accounts
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    name TEXT PRIMARY KEY,
                    phone TEXT,
                    user_id INTEGER,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
                """
            )

            # Channels assigned to accounts
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (account_name) REFERENCES accounts (name) ON DELETE CASCADE
                )
                """
            )

            # Comments history
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_name TEXT NOT NULL,
                    channel_url TEXT,
                    post_id TEXT,
                    comment_text TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (account_name) REFERENCES accounts (name) ON DELETE CASCADE
                )
                """
            )

            # Settings
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    category TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Индексы + уникальность каналов на аккаунт
            cur.execute("CREATE INDEX IF NOT EXISTS idx_accounts_user_id ON accounts(user_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_channels_account_name ON channels(account_name);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_account_name ON comments(account_name);")
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_channels_account_url ON channels(account_name, url);"
            )

            conn.commit()

            # Миграции для старых баз (если у вас уже есть db-файл)
            self._migrate(conn)

    def _column_exists(self, conn: sqlite3.Connection, table: str, col: str) -> bool:
        cur = conn.execute(f"PRAGMA table_info({table});")
        return any(row["name"] == col for row in cur.fetchall())

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """
        Мягкие миграции: добавляем недостающие колонки/индексы без разрушения данных.
        """
        cur = conn.cursor()

        # users: is_admin мог отсутствовать в ранних версиях
        if not self._column_exists(conn, "users", "is_admin"):
            cur.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0;")

        # accounts: is_active мог отсутствовать
        if not self._column_exists(conn, "accounts", "is_active"):
            cur.execute("ALTER TABLE accounts ADD COLUMN is_active INTEGER DEFAULT 1;")

        # channels: is_active мог отсутствовать
        if not self._column_exists(conn, "channels", "is_active"):
            cur.execute("ALTER TABLE channels ADD COLUMN is_active INTEGER DEFAULT 1;")

        conn.commit()

    # -----------------------------
    # Users
    # -----------------------------
    def add_user(self, user_id: int, username: str = "", first_name: str = "") -> None:
        """
        UPSERT пользователя. Telegram user_id = PK.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (id, username, first_name)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name
                """,
                (user_id, username, first_name),
            )
            conn.commit()

    def user_exists(self, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE id = ? LIMIT 1;", (user_id,)).fetchone()
            return row is not None

    # -----------------------------
    # Accounts
    # -----------------------------
    def add_account(self, name: str, phone: str, user_id: int, is_active: int = 1) -> None:
        """
        Добавляет/обновляет аккаунт. Имя аккаунта = PK.

        Важно: сохранение Telethon-сессии не в БД, а на диске.
        """
        if not name:
            raise ValueError("name пустой")
        if not phone:
            raise ValueError("phone пустой")

        # гарантируем, что пользователь есть
        if not self.user_exists(user_id):
            self.add_user(user_id=user_id)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (name, phone, user_id, is_active)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    phone=excluded.phone,
                    user_id=excluded.user_id,
                    is_active=excluded.is_active
                """,
                (name, phone, user_id, int(is_active)),
            )
            conn.commit()

    def account_exists(self, name: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT name FROM accounts WHERE name = ? LIMIT 1;", (name,)).fetchone()
            return row is not None

    def get_user_accounts(self, user_id: int, only_active: bool = True) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if only_active:
                rows = conn.execute(
                    "SELECT * FROM accounts WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC;",
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM accounts WHERE user_id = ? ORDER BY created_at DESC;",
                    (user_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_all_accounts(self, only_active: bool = False) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if only_active:
                rows = conn.execute(
                    "SELECT * FROM accounts WHERE is_active = 1 ORDER BY created_at DESC;"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC;").fetchall()
            return [dict(r) for r in rows]

    def delete_account(self, name: str) -> None:
        """
        Удаляет аккаунт. Каналы/комментарии удалятся каскадно благодаря FK + ON DELETE CASCADE.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM accounts WHERE name = ?;", (name,))
            conn.commit()

    # -----------------------------
    # Channels
    # -----------------------------
    def add_channel(self, account_name: str, url: str, is_active: int = 1) -> None:
        """
        Добавляет канал к аккаунту.
        Если такой (account_name, url) уже есть — просто обновляет is_active=1.
        """
        if not account_name:
            raise ValueError("account_name пустой")
        if not url:
            raise ValueError("url пустой")
        if not self.account_exists(account_name):
            raise ValueError(f"Аккаунт '{account_name}' не найден в БД")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO channels (account_name, url, is_active)
                VALUES (?, ?, ?)
                ON CONFLICT(account_name, url) DO UPDATE SET
                    is_active=excluded.is_active
                """,
                (account_name, url.strip(), int(is_active)),
            )
            conn.commit()

    def get_account_channels(self, account_name: str, only_active: bool = True) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if only_active:
                rows = conn.execute(
                    "SELECT * FROM channels WHERE account_name = ? AND is_active = 1 ORDER BY created_at DESC;",
                    (account_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM channels WHERE account_name = ? ORDER BY created_at DESC;",
                    (account_name,),
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_channel(self, channel_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM channels WHERE id = ?;", (int(channel_id),))
            conn.commit()

    # -----------------------------
    # Comments history
    # -----------------------------
    def add_comment(self, account_name: str, channel_url: str, post_id: str, comment_text: str) -> None:
        if not account_name:
            raise ValueError("account_name пустой")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO comments (account_name, channel_url, post_id, comment_text)
                VALUES (?, ?, ?, ?)
                """,
                (account_name, channel_url, str(post_id), comment_text),
            )
            conn.commit()

    def get_account_comments(self, account_name: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM comments
                WHERE account_name = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (account_name, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]

    # -----------------------------
    # Settings
    # -----------------------------
    def update_setting(self, key: str, value: str, category: str = "general") -> None:
        if not key:
            raise ValueError("key пустой")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, category)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    category=excluded.category,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (key, str(value), str(category)),
            )
            conn.commit()

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ? LIMIT 1;", (key,)).fetchone()
            return row["value"] if row else None

    def get_settings_by_category(self, category: str) -> Dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE category = ?;",
                (category,),
            ).fetchall()
            return {r["key"]: r["value"] for r in rows}