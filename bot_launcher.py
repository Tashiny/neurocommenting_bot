# bot_launcher.py
import asyncio
import logging
import os
from bot_handler import NeuroCommentingBot

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def get_bot_token():
    """Получение токена бота"""
    # Сначала пробуем из переменной окружения
    token = os.getenv('BOT_TOKEN')
    
    # Если нет, пробуем из файла
    if not token and os.path.exists('bot_token.txt'):
        with open('bot_token.txt', 'r', encoding='utf-8') as f:
            token = f.read().strip()
    
    # Если всё ещё нет, создаём файл для ввода
    if not token:
        print("❌ Токен бота не найден!")
        print("\n📝 Создайте файл 'bot_token.txt' и поместите туда токен")
        print("Или установите переменную окружения BOT_TOKEN")
        print("\n📌 Получить токен можно у @BotFather в Telegram")
        print("\nПример содержимого файла bot_token.txt:")
        print("1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ")
        
        # Создаём файл с инструкцией
        with open('bot_token.txt', 'w', encoding='utf-8') as f:
            f.write("# Вставьте сюда токен вашего Telegram бота\n")
            f.write("# Получить токен можно у @BotFather\n")
            f.write("# Формат: 1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ\n")
        
        exit(1)
    
    return token

async def main():
    """Основная функция запуска"""
    print("=" * 50)
    print("🚀 Запуск NeuroCommenting Bot")
    print("=" * 50)
    
    # Проверяем наличие необходимых папок
    for folder in ['sessions', 'logs', 'data', 'modules', 'static']:
        os.makedirs(folder, exist_ok=True)
    
    # Проверяем наличие необходимых файлов
    required_files = ['accounts.txt', 'channels.txt', 'settings.ini', 'api_keys.txt']
    for file in required_files:
        if not os.path.exists(file):
            print(f"⚠️ Файл {file} не найден. Создаю...")
            with open(file, 'w', encoding='utf-8') as f:
                if file == 'api_keys.txt':
                    f.write("# ТОЛЬКО API КЛЮЧИ\n")
                    f.write("# Получите на https://my.telegram.org\n")
                    f.write("telegram_api_id = ВАШ_API_ID\n")
                    f.write("telegram_api_hash = ВАШ_API_HASH\n")
                    f.write("\n")
                    f.write("# Получите на https://console.mistral.ai\n")
                    f.write("mistral_api_key = ВАШ_MISTRAL_API_KEY\n")
                elif file == 'settings.ini':
                    f.write("[Behavior]\n")
                    f.write("min_delay_between_actions = 10\n")
                    f.write("max_delay_between_actions = 60\n")
                    f.write("min_comment_delay = 15\n")
                    f.write("max_comment_delay = 120\n")
                    f.write("daily_comment_limit = 50\n")
                    f.write("skip_sponsored_posts = yes\n")
                    f.write("skip_posts_with_links = no\n\n")
                    f.write("[Security]\n")
                    f.write("use_2fa = yes\n")
                    f.write("max_retries = 3\n")
                    f.write("flood_wait_handling = adaptive\n")
                    f.write("session_backup = yes\n\n")
                    f.write("[Content]\n")
                    f.write("min_post_length = 50\n")
                    f.write("max_post_length = 5000\n")
                    f.write("blacklist_words = реклама, купить, продам, спам, scam, купити, продать\n")
                    f.write("comment_tone = neutral\n")
                    f.write("language = auto\n\n")
                    f.write("[Logging]\n")
                    f.write("log_level = INFO\n")
                    f.write("log_to_file = yes\n")
                    f.write("max_log_size_mb = 10\n")
                    f.write("keep_logs_days = 7\n")
    
    # Получаем токен
    token = get_bot_token()
    
    print(f"✅ Токен бота получен (длина: {len(token)})")
    print("🤖 Создаю экземпляр бота...")
    
    try:
        # Создаём бота
        bot = NeuroCommentingBot(token)
        
        print("✅ Бот создан успешно!")
        print("\n📋 Инструкция по использованию:")
        print("1. Начните диалог с ботом в Telegram")
        print("2. Используйте команду /start для начала работы")
        print("3. Добавьте аккаунты через /add_account")
        print("4. Настройте API ключи для каждого аккаунта")
        print("5. Добавьте каналы для мониторинга")
        print("6. Запустите мониторинг командой /start")
        print("\n" + "=" * 50)
        print("🔄 Бот запущен. Ожидание сообщений...")
        print("=" * 50)
        
        await bot.run()
        
    except Exception as e:
        logging.error(f"Ошибка при работе бота: {e}")
        print(f"💥 Критическая ошибка: {e}")
        print("\nПроверьте:")
        print("1. Правильность токена бота")
        print("2. Наличие интернет-соединения")
        print("3. Установлены ли все зависимости (pip install -r requirements.txt)")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"\n💥 Неожиданная ошибка: {e}")