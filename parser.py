import asyncio
import logging
import re
from telethon import TelegramClient, errors
from telethon.tl.types import Channel, Chat, User
from config import API_ID, API_HASH, TARGET_CHATS, KEYWORDS, EXCLUDE_WORDS
from db import is_message_sent, mark_message_sent

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def clean_message_text(text: str) -> str:
    """Очищает текст от лишних пробелов и переносов"""
    if not text:
        return ""
    # Убираем множественные переносы строк
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Убираем пробелы в начале и конце
    text = text.strip()
    return text

def is_relevant_message(text: str) -> bool:
    """
    Проверяет, содержит ли сообщение ключевые слова 
    и не содержит исключаемых слов
    """
    if not text:
        return False
    
    text_lower = text.lower()
    
    # Проверяем ключевые слова
    has_keyword = any(keyword.lower() in text_lower for keyword in KEYWORDS)
    if not has_keyword:
        return False
    
    # Проверяем исключаемые слова
    has_exclude = any(exclude.lower() in text_lower for exclude in EXCLUDE_WORDS)
    if has_exclude:
        logger.debug(f"Сообщение отфильтровано по исключаемому слову")
        return False
    
    return True

def get_message_link(chat_id: int, message_id: int, is_private: bool = False) -> str:
    """
    Формирует корректную ссылку на сообщение
    """
    if is_private:
        # Для приватных чатов
        return f"https://t.me/c/{chat_id}/{message_id}"
    else:
        # Для публичных чатов (если chat_id < 0, убираем -100 префикс)
        str_id = str(chat_id)
        if str_id.startswith('-100'):
            clean_id = str_id[4:]
            return f"https://t.me/c/{clean_id}/{message_id}"
        elif str_id.startswith('-'):
            clean_id = str_id[1:]
            return f"https://t.me/c/{clean_id}/{message_id}"
        else:
            return f"https://t.me/c/{chat_id}/{message_id}"

async def safe_get_entity(client: TelegramClient, chat_link: str):
    """
    Безопасно получает entity чата с обработкой ошибок
    """
    try:
        entity = await client.get_entity(chat_link)
        await asyncio.sleep(1)  # Задержка между запросами
        return entity
    except errors.FloodWaitError as e:
        logger.warning(f"⚠️ Flood wait для {chat_link}: {e.seconds} секунд")
        await asyncio.sleep(e.seconds)
        return await client.get_entity(chat_link)
    except errors.UsernameNotOccupiedError:
        logger.error(f"❌ Чат не найден: {chat_link}")
        return None
    except errors.InviteHashExpiredError:
        logger.error(f"❌ Ссылка-приглашение истекла: {chat_link}")
        return None
    except ValueError as e:
        logger.error(f"❌ Некорректная ссылка {chat_link}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка получения entity для {chat_link}: {type(e).__name__}: {e}")
        return None

async def process_single_chat(client: TelegramClient, chat_link: str, limit_per_chat: int) -> list:
    """
    Обрабатывает один чат и возвращает список найденных релевантных сообщений
    """
    results = []
    
    # Получаем entity чата
    entity = await safe_get_entity(client, chat_link)
    if not entity:
        logger.warning(f"⏭️ Пропускаем чат: {chat_link}")
        return results
    
    chat_id = entity.id
    chat_title = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Без названия')
    is_private = isinstance(entity, User)
    
    try:
        message_count = 0
        relevant_count = 0
        
        # Итерируемся по сообщениям
        async for message in client.iter_messages(entity, limit=limit_per_chat):
            message_count += 1
            
            # Проверяем только текстовые сообщения
            if not message.text:
                continue
            
            message_id = str(message.id)
            
            # Проверяем, не отправляли ли мы уже это сообщение
            if is_message_sent(message_id, str(chat_id)):
                continue
            
            # Проверяем релевантность
            if not is_relevant_message(message.text):
                continue
            
            relevant_count += 1
            
            # Очищаем текст
            cleaned_text = clean_message_text(message.text)
            
            # Формируем ссылку
            message_link = get_message_link(chat_id, message.id, is_private)
            
            # Добавляем в результаты
            result = {
                "chat_title": chat_title,
                "chat_link": chat_link,
                "chat_id": str(chat_id),
                "message_id": message_id,
                "message_text": cleaned_text,
                "message_link": message_link,
                "message_date": message.date.isoformat() if message.date else None,
            }
            results.append(result)
            
            # Отмечаем как отправленное
            mark_message_sent(message_id, str(chat_id))
            logger.info(f"  ✅ Найдено: {chat_title} - {cleaned_text[:50]}...")
            
            # Небольшая задержка между сообщениями
            await asyncio.sleep(0.3)
        
        logger.info(f"  📊 {chat_title}: проверено {message_count}, найдено {relevant_count}")
        
        # Задержка между чатами
        await asyncio.sleep(2)
        
    except errors.FloodWaitError as e:
        logger.warning(f"⚠️ Flood wait в чате {chat_link}: {e.seconds} сек")
        await asyncio.sleep(e.seconds)
    except errors.ChatAdminRequiredError:
        logger.error(f"❌ Нет прав администратора для чата: {chat_link}")
    except errors.ChannelPrivateError:
        logger.error(f"❌ Чат приватный или нет доступа: {chat_link}")
    except Exception as e:
        logger.error(f"❌ Ошибка обработки чата {chat_link}: {type(e).__name__}: {e}")
    
    return results

async def get_new_messages(limit_per_chat: int = 50) -> list:
    """
    Получает новые релевантные сообщения из всех чатов
    
    Args:
        limit_per_chat: Сколько последних сообщений проверять в каждом чате
    
    Returns:
        list: Список словарей с информацией о найденных сообщениях
    """
    client = TelegramClient('user_session', API_ID, API_HASH)
    all_results = []
    
    try:
        # Запускаем клиент
        await client.start()
        logger.info("✅ Telethon client started")
        
        # Проверяем авторизацию
        me = await client.get_me()
        logger.info(f"👤 Авторизован как: {me.first_name} (@{me.username if me.username else 'нет username'})")
        
        total_chats = len(TARGET_CHATS)
        logger.info(f"📋 Начинаю обработку {total_chats} чатов...")
        
        for idx, chat_link in enumerate(TARGET_CHATS, 1):
            logger.info(f"🔄 Обработка чата [{idx}/{total_chats}]: {chat_link}")
            
            # Обрабатываем чат
            chat_results = await process_single_chat(client, chat_link, limit_per_chat)
            all_results.extend(chat_results)
            
        logger.info(f"🏁 Парсинг завершён. Всего найдено {len(all_results)} новых сообщений")
        
    except errors.rpcerrorlist.AccessTokenExpiredError:
        logger.error("❌ Токен доступа истёк. Нужно пересоздать сессию")
    except errors.rpcerrorlist.AccessTokenInvalidError:
        logger.error("❌ Невалидный токен доступа. Нужно пересоздать сессию")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в get_new_messages: {type(e).__name__}: {e}", exc_info=True)
    
    finally:
        # ВАЖНО: Всегда закрываем клиент
        if client.is_connected():
            await client.disconnect()
            logger.info("🔌 Telethon client disconnected")
    
    return all_results

async def get_single_chat_messages(chat_link: str, limit: int = 100) -> list:
    """
    Получает сообщения из одного конкретного чата (для тестирования)
    
    Args:
        chat_link: Ссылка на чат
        limit: Количество сообщений для проверки
    
    Returns:
        list: Список сообщений с пометкой релевантности
    """
    client = TelegramClient('user_session', API_ID, API_HASH)
    results = []
    
    try:
        await client.start()
        logger.info(f"🔍 Тестовая проверка чата: {chat_link}")
        
        entity = await safe_get_entity(client, chat_link)
        if not entity:
            logger.error("❌ Не удалось получить чат")
            return []
        
        chat_title = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Без названия')
        message_count = 0
        
        async for message in client.iter_messages(entity, limit=limit):
            if not message.text:
                continue
            
            message_count += 1
            cleaned_text = clean_message_text(message.text)
            is_relevant = is_relevant_message(message.text)
            
            results.append({
                "chat_title": chat_title,
                "message_text": cleaned_text[:200] + "..." if len(cleaned_text) > 200 else cleaned_text,
                "is_relevant": is_relevant,
                "message_link": get_message_link(entity.id, message.id),
                "has_keywords": any(kw.lower() in message.text.lower() for kw in KEYWORDS),
                "has_excludes": any(ex.lower() in message.text.lower() for ex in EXCLUDE_WORDS),
            })
        
        logger.info(f"📊 Проверено {message_count} сообщений")
        
        # Статистика
        relevant = [r for r in results if r['is_relevant']]
        logger.info(f"✅ Релевантных: {len(relevant)}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {type(e).__name__}: {e}")
    
    finally:
        if client.is_connected():
            await client.disconnect()
            logger.info("🔌 Клиент отключен")
    
    return results

async def test_keywords_in_chat(chat_link: str, limit: int = 20):
    """
    Тестовая функция для проверки работы ключевых слов в чате
    """
    client = TelegramClient('user_session', API_ID, API_HASH)
    
    try:
        await client.start()
        logger.info(f"🧪 Тестирование ключевых слов в чате: {chat_link}")
        
        entity = await safe_get_entity(client, chat_link)
        if not entity:
            return
        
        chat_title = getattr(entity, 'title', 'Без названия')
        logger.info(f"📌 Чат: {chat_title}")
        logger.info(f"🔑 Ключевые слова: {', '.join(KEYWORDS[:5])}...")
        logger.info(f"🚫 Исключения: {', '.join(EXCLUDE_WORDS[:5])}...")
        logger.info("-" * 50)
        
        async for message in client.iter_messages(entity, limit=limit):
            if not message.text:
                continue
            
            text_lower = message.text.lower()
            
            # Находим какие ключевые слова сработали
            found_keywords = [kw for kw in KEYWORDS if kw.lower() in text_lower]
            found_excludes = [ex for ex in EXCLUDE_WORDS if ex.lower() in text_lower]
            
            is_relevant = bool(found_keywords) and not bool(found_excludes)
            
            logger.info(f"📝 Сообщение {message.id}:")
            logger.info(f"   Текст: {message.text[:100]}...")
            logger.info(f"   Ключевые слова: {found_keywords if found_keywords else '❌ нет'}")
            logger.info(f"   Исключения: {found_excludes if found_excludes else '✅ нет'}")
            logger.info(f"   Релевантно: {'✅ ДА' if is_relevant else '❌ НЕТ'}")
            logger.info("-" * 30)
            
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
    
    finally:
        if client.is_connected():
            await client.disconnect()

# Для тестирования
if __name__ == "__main__":
    async def main_test():
        from db import init_db
        init_db()
        
        print("\n" + "="*60)
        print("🧪 ТЕСТИРОВАНИЕ ПАРСЕРА")
        print("="*60 + "\n")
        
        # Выбор режима тестирования
        print("1. Полный парсинг всех чатов")
        print("2. Тест одного чата")
        print("3. Тест ключевых слов")
        
        choice = input("\nВыберите режим (1/2/3): ").strip()
        
        if choice == "1":
            print("\n📡 Запуск полного парсинга...\n")
            messages = await get_new_messages(limit_per_chat=20)
            
            print(f"\n📬 Найдено сообщений: {len(messages)}")
            for i, msg in enumerate(messages[:10], 1):
                print(f"\n{i}. 🔹 {msg['chat_title']}")
                print(f"   📅 {msg.get('message_date', 'Н/Д')}")
                print(f"   📝 {msg['message_text'][:100]}...")
                print(f"   🔗 {msg['message_link']}")
                
        elif choice == "2":
            chat_link = input("\nВведите ссылку на чат: ").strip()
            if chat_link:
                print(f"\n📡 Проверка чата {chat_link}...\n")
                messages = await get_single_chat_messages(chat_link, limit=30)
                
                print(f"\n📊 Статистика:")
                relevant = [m for m in messages if m['is_relevant']]
                print(f"   Всего сообщений: {len(messages)}")
                print(f"   Релевантных: {len(relevant)}")
                print(f"   Нерелевантных: {len(messages) - len(relevant)}")
                
                if relevant:
                    print(f"\n✅ Релевантные сообщения:")
                    for i, msg in enumerate(relevant[:5], 1):
                        print(f"\n{i}. {msg['message_text'][:150]}...")
                        print(f"   🔗 {msg['message_link']}")
                        
        elif choice == "3":
            chat_link = input("\nВведите ссылку на чат: ").strip()
            if chat_link:
                await test_keywords_in_chat(chat_link, limit=20)
        
        print("\n✅ Тестирование завершено")
    
    try:
        asyncio.run(main_test())
    except KeyboardInterrupt:
        print("\n👋 Прервано пользователем")
