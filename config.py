# config.py
import configparser
import os
from typing import Dict, Any

class Config:
    def __init__(self):
        self.settings_file = 'settings.ini'
        self.api_keys_file = 'api_keys.txt'
        self.accounts_file = 'accounts.txt'
        self.channels_file = 'channels.txt'
        
        # Проверка существования файлов
        self.check_files()
    
    def check_files(self):
        """Проверка и создание необходимых файлов"""
        if not os.path.exists(self.settings_file):
            self.create_default_settings()
        
        if not os.path.exists(self.api_keys_file):
            self.create_default_api_keys()
        
        if not os.path.exists(self.accounts_file):
            with open(self.accounts_file, 'w', encoding='utf-8') as f:
                f.write("# Формат: имя_аккаунта:номер_телефона\n")
        
        if not os.path.exists(self.channels_file):
            with open(self.channels_file, 'w', encoding='utf-8') as f:
                f.write("# Список каналов для мониторинга\n")
    
    def create_default_settings(self):
        """Создание настроек по умолчанию"""
        config = configparser.ConfigParser()
        
        # Behavior
        config['Behavior'] = {
            'enable_channel_scan': 'yes',
            'post_comment_delay_min': '0',
            'post_comment_delay_max': '60',
            'min_delay_between_actions': '10',
            'max_delay_between_actions': '60',
            'min_comment_delay': '15',
            'max_comment_delay': '120',
            'daily_comment_limit': '50',
            'inter_account_delay_min': '10',
            'inter_account_delay_max': '15',
            'mistral_request_gap_seconds': '1.2',
            'skip_sponsored_posts': 'yes',
            'skip_posts_with_links': 'no'
        }
        
        # Security
        config['Security'] = {
            'use_2fa': 'yes',
            'max_retries': '3',
            'flood_wait_handling': 'adaptive',
            'session_backup': 'yes'
        }

        
        # Content

        
        config['Content'] = {
            'disable_all_filters': 'no',
            'comment_tone': 'auto',
            'comment_sentences_min': '1',
            'comment_sentences_max': '2',
            'allow_emoji': 'auto',
            'min_post_length': '0',
            'max_post_length': '20000',
            'blacklist_words': '',
            'language': 'auto'

        
        }
        
        # Logging
        config['Logging'] = {
            'log_level': 'INFO',
            'log_to_file': 'yes',
            'max_log_size_mb': '10',
            'keep_logs_days': '7'
        }
        
        with open(self.settings_file, 'w', encoding='utf-8') as f:
            config.write(f)
    
    def create_default_api_keys(self):
        """Создание файла с API ключами по умолчанию"""
        content = """# ТОЛЬКО API КЛЮЧИ
# Получите на https://my.telegram.org
telegram_api_id = ВАШ_API_ID
telegram_api_hash = ВАШ_API_HASH

# Получите на https://console.mistral.ai
mistral_api_key = ВАШ_MISTRAL_API_KEY
"""
        with open(self.api_keys_file, 'w', encoding='utf-8') as f:
            f.write(content)
    
    def get_settings(self) -> Dict[str, Any]:
        """Получение всех настроек"""
        config = configparser.ConfigParser()
        config.read(self.settings_file, encoding='utf-8')
        
        settings = {}
        for section in config.sections():
            settings[section] = dict(config.items(section))
        
        return settings
    
    def get_settings_section(self, section: str) -> Dict[str, str]:
        """Получение настроек конкретной секции"""
        config = configparser.ConfigParser()
        config.read(self.settings_file, encoding='utf-8')
        
        if config.has_section(section):
            return dict(config.items(section))
        return {}
    
    def update_setting(self, section: str, key: str, value: str):
        """Обновление настройки"""
        config = configparser.ConfigParser()
        config.read(self.settings_file, encoding='utf-8')
        
        if not config.has_section(section):
            config.add_section(section)
        
        config.set(section, key, value)
        
        with open(self.settings_file, 'w', encoding='utf-8') as f:
            config.write(f)
    
    def get_api_keys(self) -> Dict[str, str]:
        """Получение API ключей"""
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
        """Обновление API ключа"""
        lines = []
        
        if os.path.exists(self.api_keys_file):
            with open(self.api_keys_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        
        # Ищем и заменяем ключ
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(key + ' ='):
                lines[i] = f"{key} = {value}\n"
                found = True
                break
        
        # Если не нашли, добавляем в конец
        if not found:
            lines.append(f"\n{key} = {value}\n")
        
        # Записываем обратно
        with open(self.api_keys_file, 'w', encoding='utf-8') as f:
            f.writelines(lines)