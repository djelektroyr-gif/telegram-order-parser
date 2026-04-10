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
        if str(chat_id).startswith('-100'):
            clean_id = str(chat_id)[4:]
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
        logger.warning(f"Flood wait для {chat_link}: {e.seconds} секунд")
        await asyncio.sleep(e.seconds)
        return await client.get_entity(chat_link)
    except errors.UsernameNotOccupiedError:
        logger.error(f"Чат не найден: {chat_link}")
        return None
    except errors.InviteHashExpiredError:
        logger.error(f"Ссылка-приглашение истекла: {chat_link}")
        return None
    except Exception as e:
        logger.error(f"Ошибка получения entity для {chat_link}: {type(e).__name__}: {e}")
        return None

async def get_new_messages(limit_per_chat: int = 50) -> list:
    """
    Получает новые релевантные сообщения из всех чатов
    
    Args:
        limit_per_chat: Сколько последних сообщений проверять в каждом чате
    
    Returns:
        list: Список словарей с информацией о найденных сообщениях
    """
    client = TelegramClient('user_session', API_ID, API_HASH)
    
    try:
        await client.start()
        logger.info("✅ Telethon client started")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска клиента: {e}")
        return []

    results = []
    total_chats = len(TARGET_CHATS)
    
    for idx, chat_link in enumerate(TARGET_CHATS, 1):
        logger.info(f"📋 Обработка чата [{idx}/{total_chats}]: {chat_link}")
        
        # Получаем entity чата
        entity = await safe_get_entity(client, chat_link)
        if not entity:
            logger.warning(f"⏭️ Пропускаем чат: {chat_link}")
            continue
        
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
                logger.info(f"  ✅ Найдено релевантное сообщение в {chat_title}")
                
                # Небольшая задержка между сообщениями
                await asyncio.sleep(0.3)
            
            logger.info(f"  📊 Проверено {message_count} сообщений, найдено {relevant_count} релевантных")
            
            # Задержка между чатами
            await asyncio.sleep(2)
            
        except errors.FloodWaitError as e:
            logger.warning(f"⚠️ Flood wait в чате {chat_link}: {e.seconds} сек")
            await asyncio.sleep(e.seconds)
        except errors.ChatAdminRequiredError:
            logger.error(f"❌ Нет прав администратора для чата: {chat_link}")
        except errors.ChannelPrivateError:
            logger.error(f"❌ Чат приватный или бот не имеет доступа: {chat_link}")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки чата {chat_link}: {type(e).__name__}: {e}")
    
    await client.disconnect()
    logger.info(f"🏁 Парсинг завершён. Найдено {len(results)} новых сообщений")
    
    return results

async def get_single_chat_messages(chat_link: str, limit: int = 100) -> list:
    """
    Получает сообщения из одного конкретного чата (для тестирования)
    """
    client = TelegramClient('user_session', API_ID, API_HASH)
    
    try:
        await client.start()
        logger.info(f"🔍 Проверка одного чата: {chat_link}")
        
        entity = await safe_get_entity(client, chat_link)
        if not entity:
            return []
        
        results = []
        chat_title = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Без названия')
        
        async for message in client.iter_messages(entity, limit=limit):
            if not message.text:
                continue
            
            cleaned_text = clean_message_text(message.text)
            is_relevant = is_relevant_message(message.text)
            
            results.append({
                "chat_title": chat_title,
                "message_text": cleaned_text[:200] + "..." if len(cleaned_text) > 200 else cleaned_text,
                "is_relevant": is_relevant,
                "message_link": get_message_link(entity.id, message.id),
            })
        
        await client.disconnect()
        return results
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await client.disconnect()
        return []

# Для тестирования
if __name__ == "__main__":
    async def test():
        from db import init_db
        init_db()
        
        # Тестовый запуск
        messages = await get_new_messages(limit_per_chat=20)
        print(f"\n📬 Найдено сообщений: {len(messages)}")
        for msg in messages[:5]:  # Показываем первые 5
            print(f"\n🔹 {msg['chat_title']}")
            print(f"   {msg['message_text'][:100]}...")
            print(f"   🔗 {msg['message_link']}")
    
    asyncio.run(test())
