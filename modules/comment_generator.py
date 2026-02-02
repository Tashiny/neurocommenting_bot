# modules/comment_generator.py
from __future__ import annotations

import asyncio
import configparser
import hashlib
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

class CommentGenerator:
    """
    Генератор комментариев v7.0 (Optimized Multi-Key Edition).
    
    Основные изменения:
    1. Multi-Key Support: Поддержка нескольких API ключей Mistral для ротации.
    2. Smart Rate Limiting: Лимиты отслеживаются для каждого ключа отдельно.
    3. Load Balancing: Алгоритм выбирает наиболее свободный ключ для мгновенного запроса.
    """

    # -----------------------------
    # Глобальное состояние
    # -----------------------------
    _recent_by_account: Dict[str, List[str]] = {}
    _recent_global: List[str] = []
    _recent_global_max: int = 200
    _recent_account_max: int = 50

    # -----------------------------
    # ПРЕСЕТЫ СТИЛЕЙ
    # -----------------------------
    STYLE_PRESETS: List[Dict[str, Any]] = [
        # === 1. НЕФОРМАЛЬНЫЙ ===
        dict(
            name="informal_dude",
            categories=["informal", "neutral", "humorous"], 
            desc="Ты обычный участник чата. Пишешь с телефона, на ходу. Не стараешься выглядеть умным. Можешь пошутить.",
            min_sent=1, max_sent=2,
            emoji_prob=0.2,      
            lowercase_prob=1.0,  
            no_dot_prob=1.0,     
        ),
        # === 2. ФОРМАЛЬНЫЙ ===
        dict(
            name="formal_pro",
            categories=["professional", "formal"],
            desc="Ты опытный специалист. Пишешь сдержанно, грамотно и по делу. Не используешь сленг и эмодзи. Ценишь точность.",
            min_sent=1, max_sent=2,
            emoji_prob=0.0,      
            lowercase_prob=0.05, 
            no_dot_prob=0.1,     
        ),
        # === 3. ДРУЖЕЛЮБНЫЙ ===
        dict(
            name="friendly_helper",
            categories=["friendly", "supportive"],
            desc="Ты добрый и отзывчивый человек. Тебе нравится помогать и поддерживать беседу. Тон мягкий и позитивный.",
            min_sent=1, max_sent=2,
            emoji_prob=0.6,      
            lowercase_prob=0.5,  
            no_dot_prob=0.8,
        ),
    ]

    def __init__(self, settings_path: str = "settings.ini", api_keys_path: str = "api_keys.txt"):
        self.settings_path = settings_path
        self.api_keys_path = api_keys_path
        
        # Пул клиентов и блокировка выбора
        self.clients_pool: List[Dict[str, Any]] = []
        self._pool_lock = asyncio.Lock()
        self._mistral_mode = ""
        self.use_real_api = False

        # Первичная загрузка
        self.settings = self._load_settings(self.settings_path)
        self._init_mistral_clients_pool()

    def reload_settings(self):
        """Обновление настроек и ключей на лету"""
        try:
            logger.info("♻️ Reloading settings in CommentGenerator...")
            self.settings = self._load_settings(self.settings_path)
            # Переинициализируем пул клиентов (на случай изменения ключей)
            self._init_mistral_clients_pool()
            logger.info("✅ Settings reloaded successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to reload settings: {e}")

    # --- Загрузчики ---
    @staticmethod
    def _load_settings(path: str) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        if os.path.exists(path): cfg.read(path, encoding="utf-8")
        return cfg

    @staticmethod
    def _parse_kv_file(path: str) -> Dict[str, str]:
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.split("=", 1)
                        data[k.strip()] = v.strip().strip('"\'')
        except Exception: pass
        return data

    def _load_mistral_keys(self) -> List[str]:
        """Загрузка списка ключей (поддержка запятых)"""
        raw_val = ""
        
        # 1. Приоритет: переменная окружения
        env = os.getenv("MISTRAL_API_KEY", "").strip()
        if env:
            raw_val = env
        else:
            # 2. Файл api_keys.txt
            kv = self._parse_kv_file(self.api_keys_path)
            val = kv.get("mistral_api_key")
            if val and val != "ВАШ_MISTRAL_API_KEY":
                raw_val = val
            elif self.settings.has_section("Mistral"):
                # 3. settings.ini (fallback)
                v = self.settings.get("Mistral", "mistral_api_key", fallback="")
                if v and v != "ВАШ_MISTRAL_API_KEY":
                    raw_val = v
        
        if not raw_val:
            return []

        # Разбиваем по запятой и чистим
        keys = [k.strip() for k in raw_val.split(",") if k.strip()]
        # Убираем дубликаты
        return list(set(keys))

    def _determine_mistral_mode(self):
        """Определяем доступную библиотеку один раз"""
        try:
            import mistralai
            # Проверка версии SDK (v1 vs v2)
            if hasattr(mistralai, "Mistral"):
                self._mistral_mode = "sdk_v2"
            else:
                self._mistral_mode = "sdk_v1"
        except ImportError:
            self._mistral_mode = "http"

    def _create_single_client(self, api_key: str) -> Any:
        """Создание экземпляра клиента для конкретного ключа"""
        try:
            if self._mistral_mode == "sdk_v2":
                from mistralai import Mistral
                return Mistral(api_key=api_key)
            
            if self._mistral_mode == "sdk_v1":
                from mistralai.client import MistralClient
                return MistralClient(api_key=api_key)
                
            # Для HTTP режима клиент не нужен, возвращаем None (ключ передадим в хедере)
            return None
        except Exception as e:
            logger.error(f"Error creating client for key {api_key[:5]}...: {e}")
            return None

    def _init_mistral_clients_pool(self):
        """Инициализация пула клиентов с индивидуальным учетом времени"""
        keys = self._load_mistral_keys()
        if not keys:
            self.use_real_api = False
            self.clients_pool = []
            return

        self._determine_mistral_mode()
        self.clients_pool = []
        
        for key in keys:
            client_obj = self._create_single_client(key)
            self.clients_pool.append({
                "client": client_obj,
                "api_key": key,
                "last_used": 0.0,  # Timestamp последнего использования ЭТОГО ключа
            })
            
        self.use_real_api = True
        logger.info(f"✅ Mistral pool initialized with {len(self.clients_pool)} keys (Mode: {self._mistral_mode})")

    # --- Логика выбора стиля ---
    
    def _get_setting_tone(self) -> str:
        try:
            return self.settings.get("Content", "comment_tone", fallback="auto").lower()
        except Exception: return "auto"

    def _pick_style_by_settings(self, account_name: str, post_key: str) -> Dict[str, Any]:
        tone_setting = self._get_setting_tone()
        target_categories = []
        
        if tone_setting == "professional": target_categories = ["professional", "formal"]
        elif tone_setting == "friendly": target_categories = ["friendly", "supportive"]
        elif tone_setting in ["informal", "humorous"]: target_categories = ["informal", "humorous"]

        allowed_presets = []
        if target_categories:
            allowed_presets = [p for p in self.STYLE_PRESETS if any(c in target_categories for c in p["categories"])]
        
        if not allowed_presets: allowed_presets = self.STYLE_PRESETS

        seed = int(hashlib.md5(f"{account_name}:{post_key}".encode()).hexdigest()[:8], 16)
        idx = seed % len(allowed_presets)
        return dict(allowed_presets[idx])

    def _get_account_specific_params(self, account_name: str) -> Dict[str, float]:
        seed = int(hashlib.md5(account_name.encode()).hexdigest()[:8], 16)
        random.seed(seed)
        base_temp = float(self.settings.get("Mistral", "temperature", fallback="0.7"))
        jitter = random.uniform(-0.05, 0.15) 
        final_temp = max(0.4, min(1.0, base_temp + jitter))
        return {"temperature": final_temp, "top_p": 0.95}

    # --- УМНАЯ РОТАЦИЯ И RATE LIMIT ---

    async def _select_client_optimized(self) -> Tuple[Any, str]:
        """
        Выбирает лучший доступный клиент.
        Алгоритм:
        1. Ищем ключи, которые готовы ПРЯМО СЕЙЧАС (прошло > gap времени).
        2. Если есть -> берем случайный из них (балансировка).
        3. Если нет -> ищем тот, который освободится раньше всех, и ждем.
        """
        gap = float(self.settings.get("Behavior", "mistral_request_gap_seconds", fallback="1.2"))
        
        async with self._pool_lock:
            now = time.time()
            ready_indices = []
            min_wait = float('inf')
            best_idx = -1
            
            for i, record in enumerate(self.clients_pool):
                wait_time = (record["last_used"] + gap) - now
                
                if wait_time <= 0:
                    ready_indices.append(i)
                else:
                    if wait_time < min_wait:
                        min_wait = wait_time
                        best_idx = i
            
            selected_idx = -1
            actual_wait = 0.0
            
            if ready_indices:
                # Есть готовые ключи — берем любой из них
                selected_idx = random.choice(ready_indices)
                actual_wait = 0.0
            elif best_idx != -1:
                # Все заняты — берем тот, что освободится быстрее
                selected_idx = best_idx
                actual_wait = min_wait
            else:
                # Пустой пул (не должно случаться, если есть ключи)
                return None, ""

            # Предиктивно обновляем время использования, чтобы следующий поток не взял этот же ключ сразу
            # Если нам нужно ждать, мы добавляем время ожидания к "сейчас"
            self.clients_pool[selected_idx]["last_used"] = now + actual_wait
            
            client_record = self.clients_pool[selected_idx]
        
        # Ожидание происходит вне блокировки пула, чтобы не тормозить другие запросы
        if actual_wait > 0:
            await asyncio.sleep(actual_wait)
            
        return client_record["client"], client_record["api_key"]

    # --- DETECT POST TYPE & PROMPTS ---

    def _detect_post_type(self, text: str) -> str:
        t = text.lower()[:300]
        question_markers = ["как", "почему", "зачем", "кто", "подскажите", "посоветуйте", "помогите", "вопрос", "знает", "сталкивался"]
        if "?" in t and any(w in t for w in question_markers): return "question"
        news_markers = ["анонс", "новость", "релиз", "вышло", "обновление", "update", "new", "выпустили", "представили"]
        if any(w in t for w in news_markers): return "news"
        return "discussion"

    def _build_prompt(self, post_text: str, style: Dict[str, Any]) -> str:
        clip = post_text.strip()[:1000]
        post_type = self._detect_post_type(clip)
        
        task_instruction = ""
        if post_type == "question":
            task_instruction = "Пост содержит ВОПРОС. Дай краткий совет или поделись мнением. Не пиши 'не знаю'."
        elif post_type == "news":
            task_instruction = "Это НОВОСТЬ. Оцени актуальность. Вырази интерес или скепсис (в зависимости от роли)."
        else:
            task_instruction = "Это контентный пост. Выдели главную мысль и прокомментируй её."

        format_instr = ""
        if style.get("lowercase_prob", 0.0) >= 0.9:
            format_instr = "Пиши как в чате: всё с маленькой буквы, без точек в конце."
        elif style.get("name") == "formal_pro":
            format_instr = "Пиши грамотно, с большой буквы, соблюдай пунктуацию."
        else:
            format_instr = "Пиши естественно. Соблюдай базовую пунктуацию."

        prompt = (
            f"Роль: {style['desc']}\n"
            f"Контекст: Telegram чат.\n\n"
            f"ПОСТ:\n---\n{clip}\n---\n\n"
            f"ЗАДАЧА: {task_instruction}\n\n"
            f"ПРАВИЛА:\n"
            f"1. {format_instr}\n"
            f"2. Длина: 3-15 слов.\n"
            f"3. ЗАПРЕТЫ: Не используй фразы 'Крутая находка', 'Отличная статья', 'Спасибо автору', 'Полезно'. Это спам.\n"
            f"4. Не начинай ответ с вводных слов ('Ну', 'Кстати', 'Вообще').\n\n"
            f"Твой комментарий:"
        )
        return prompt

    def _humanize_text(self, text: str, style: Dict[str, Any]) -> str:
        t = text.strip()
        if not t: return ""
        
        if style.get("lowercase_prob", 0.0) >= 0.9:
            t = t.lower()
        elif random.random() < style.get("lowercase_prob", 0.0):
            t = t.lower()
            
        if t.endswith(".") and style.get("no_dot_prob", 0.0) > 0.5:
            if random.random() < style.get("no_dot_prob", 0.0):
                t = t[:-1]
        
        t = re.sub(r"^(?:комментарий|ответ)[:\s\-]*", "", t, flags=re.IGNORECASE).strip()
        t = t.strip('"\'')
        t = re.sub(r"\bAI\b", "ии", t)
        return t.strip()

    def _is_duplicate(self, account_name: str, comment: str) -> bool:
        def norm(s): return re.sub(r"[^\w]", "", s.lower())
        c = norm(comment)
        
        for old in self._recent_by_account.get(account_name, [])[-20:]:
            if c == norm(old): return True
        for old_raw in self._recent_global[-50:]:
            old = norm(old_raw)
            if c == old: return True
        return False

    def _remember(self, account_name: str, comment: str):
        if account_name not in self._recent_by_account: self._recent_by_account[account_name] = []
        self._recent_by_account[account_name].append(comment)
        if len(self._recent_by_account[account_name]) > self._recent_account_max:
            self._recent_by_account[account_name].pop(0)
        self._recent_global.append(comment)
        if len(self._recent_global) > self._recent_global_max:
            self._recent_global.pop(0)

    # --- MAIN GENERATION ---

    async def generate_comment(self, post_text: str, channel_name: str = None, account_name: str = "def", post_key: str = None) -> Optional[str]:
        if not post_text or len(post_text) < 5: return None
        if not post_key: post_key = str(hash(post_text[:50]))

        if not self.use_real_api:
            logger.warning("Mistral API not configured or no keys found.")
            return None

        style = self._pick_style_by_settings(account_name, post_key)
        params = self._get_account_specific_params(account_name)
        prompt = self._build_prompt(post_text, style)
        
        for attempt in range(2):
            # Получаем лучший клиент с учетом ротации и лимитов
            client, api_key = await self._select_client_optimized()
            if not api_key: 
                logger.error("No available API keys in pool.")
                return None

            raw = await self._call_api_with_client(client, api_key, prompt, params)
            if not raw: continue
            
            final = self._humanize_text(raw, style)
            
            if len(final.split()) < 2: continue
            if self._is_duplicate(account_name, final): continue
            
            # Эмодзи
            if random.random() < style.get("emoji_prob", 0.0):
                pool = ["👍", "👀", "🤔", ")", "))"]
                if style.get("name") == "formal_pro": pool = [] 
                
                if pool:
                    emoji = random.choice(pool)
                    if emoji in [")", "))"]: final = f"{final}{emoji}"
                    else: final = f"{final} {emoji}"

            self._remember(account_name, final)
            logger.info(f"Generated for {account_name} [{style['name']}] using key ...{api_key[-4:]}: {final}")
            return final
            
        return None

    async def _call_api_with_client(self, client: Any, api_key: str, prompt: str, params: Dict[str, float]) -> Optional[str]:
        model = self.settings.get("Mistral", "model", fallback="mistral-small-latest")
        
        try:
            # SDK v2
            if self._mistral_mode == "sdk_v2" and client:
                r = await asyncio.to_thread(
                    client.chat.complete, 
                    model=model, 
                    messages=[{"role":"user", "content":prompt}], 
                    temperature=params["temperature"], 
                    top_p=params["top_p"], 
                    max_tokens=150
                )
                return r.choices[0].message.content
            
            # SDK v1
            if self._mistral_mode == "sdk_v1" and client:
                r = await asyncio.to_thread(
                    client.chat, 
                    model=model, 
                    messages=[{"role":"user", "content":prompt}], 
                    temperature=params["temperature"], 
                    top_p=params["top_p"], 
                    max_tokens=150
                )
                return r.choices[0].message.content
                
            # HTTP (fallback or explicit)
            if self._mistral_mode == "http":
                import httpx
                async with httpx.AsyncClient(timeout=15) as http_client:
                    r = await http_client.post(
                        "https://api.mistral.ai/v1/chat/completions", 
                        json={
                            "model": model, 
                            "messages": [{"role":"user", "content":prompt}], 
                            "temperature": params["temperature"], 
                            "top_p": params["top_p"], 
                            "max_tokens": 150
                        },
                        headers={"Authorization": f"Bearer {api_key}"}
                    )
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"]
                    else:
                        logger.error(f"HTTP Error {r.status_code}: {r.text}")
                        
        except Exception as e:
            logger.error(f"Mistral API Error (Key ...{api_key[-4:]}): {e}")
        return None
