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
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    return text

def is_relevant_message(text: str) -> tuple[bool, list, list]:
    """
    Проверяет, содержит ли сообщение ключевые слова 
    и не содержит исключаемых слов
    
    Returns:
        tuple: (is_relevant, found_keywords, found_excludes)
    """
    if not text:
        return False, [], []
    
    text_lower = text.lower()
    
    # Находим все ключевые слова в тексте
    found_keywords = [kw for kw in KEYWORDS if kw.lower() in text_lower]
    
    # Находим все исключаемые слова в тексте
    found_excludes = [ex for ex in EXCLUDE_WORDS if ex.lower() in text_lower]
    
    # Сообщение релевантно, если есть ключевые слова И нет исключаемых
    is_relevant = bool(found_keywords) and not bool(found_excludes)
    
    if is_relevant:
        logger.info(f"  ✅ Найдены ключевые слова: {found_keywords[:3]}")
    elif found_keywords and found_excludes:
        logger.debug(f"  ⚠️ Сообщение отфильтровано по исключениям: {found_excludes}")
    
    return is_relevant, found_keywords, found_excludes

def get_message_link(chat_id: int, message_id: int, is_private: bool = False) -> str:
    """Формирует корректную ссылку на сообщение"""
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
    """Безопасно получает entity чата с обработкой ошибок"""
    try:
        entity = await client.get_entity(chat_link)
        await asyncio.sleep(1)
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
    """Обрабатывает один чат и возвращает список найденных релевантных сообщений"""
    results = []
    
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
        
        async for message in client.iter_messages(entity, limit=limit_per_chat):
            message_count += 1
            
            if not message.text:
                continue
            
            message_id = str(message.id)
            
            # Проверяем, не отправляли ли мы уже это сообщение
            if is_message_sent(message_id, str(chat_id)):
                continue
            
            # Проверяем релевантность с детальной информацией
            is_relevant, found_keywords, found_excludes = is_relevant_message(message.text)
            
            if not is_relevant:
                continue
            
            relevant_count += 1
            
            cleaned_text = clean_message_text(message.text)
            message_link = get_message_link(chat_id, message.id, is_private)
            
            result = {
                "chat_title": chat_title,
                "chat_link": chat_link,
                "chat_id": str(chat_id),
                "message_id": message_id,
                "message_text": cleaned_text,
                "message_link": message_link,
                "message_date": message.date.isoformat() if message.date else None,
                "found_keywords": found_keywords[:5],  # Сохраняем найденные ключевые слова
            }
            results.append(result)
            
            mark_message_sent(message_id, str(chat_id))
            logger.info(f"  ✅ НАЙДЕНО: {chat_title} - {cleaned_text[:80]}...")
            
            await asyncio.sleep(0.3)
        
        if relevant_count > 0:
            logger.info(f"  📊 {chat_title}: проверено {message_count}, найдено {relevant_count} релевантных")
        else:
            logger.debug(f"  📊 {chat_title}: проверено {message_count}, релевантных нет")
        
        await asyncio.sleep(2)
        
    except errors.FloodWaitError as e:
        logger.warning(f"⚠️ Flood wait в чате {chat_link}: {e.seconds} сек")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        logger.error(f"❌ Ошибка обработки чата {chat_link}: {type(e).__name__}: {e}")
    
    return results

async def get_new_messages(limit_per_chat: int = 50) -> list:
    """Получает новые релевантные сообщения из всех чатов"""
    client = TelegramClient('user_session', API_ID, API_HASH)
    all_results = []
    
    try:
        await client.start()
        logger.info("✅ Telethon client started")
        
        me = await client.get_me()
        logger.info(f"👤 Авторизован как: {me.first_name}")
        
        # Выводим список ключевых слов для проверки
        logger.info(f"🔑 Ключевые слова ({len(KEYWORDS)}): {', '.join(KEYWORDS[:5])}...")
        logger.info(f"🚫 Исключения ({len(EXCLUDE_WORDS)}): {', '.join(EXCLUDE_WORDS[:3])}...")
        
        total_chats = len(TARGET_CHATS)
        logger.info(f"📋 Начинаю обработку {total_chats} чатов...")
        
        for idx, chat_link in enumerate(TARGET_CHATS, 1):
            logger.info(f"🔄 Обработка чата [{idx}/{total_chats}]: {chat_link}")
            
            chat_results = await process_single_chat(client, chat_link, limit_per_chat)
            all_results.extend(chat_results)
            
        logger.info(f"🏁 Парсинг завершён. Всего найдено {len(all_results)} новых сообщений")
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {type(e).__name__}: {e}", exc_info=True)
    
    finally:
        if client.is_connected():
            await client.disconnect()
            logger.info("🔌 Telethon client disconnected")
    
    return all_results

# Функция для тестирования ключевых слов
async def test_keyword_matching(chat_link: str, limit: int = 30):
    """Тестирует работу ключевых слов на конкретном чате"""
    client = TelegramClient('user_session', API_ID, API_HASH)
    
    try:
        await client.start()
        logger.info(f"🧪 ТЕСТИРОВАНИЕ КЛЮЧЕВЫХ СЛОВ в чате: {chat_link}")
        logger.info("=" * 60)
        
        entity = await safe_get_entity(client, chat_link)
        if not entity:
            return
        
        chat_title = getattr(entity, 'title', 'Без названия')
        logger.info(f"📌 Чат: {chat_title}")
        logger.info(f"🔑 Ключевые слова: {', '.join(KEYWORDS)}")
        logger.info(f"🚫 Исключения: {', '.join(EXCLUDE_WORDS)}")
        logger.info("-" * 60)
        
        checked = 0
        relevant = 0
        
        async for message in client.iter_messages(entity, limit=limit):
            if not message.text:
                continue
            
            checked += 1
            is_rel, found_kw, found_ex = is_relevant_message(message.text)
            
            status = "✅ РЕЛЕВАНТНО" if is_rel else "❌ НЕРЕЛЕВАНТНО"
            
            logger.info(f"\n📝 Сообщение {message.id}:")
            logger.info(f"   Текст: {message.text[:100]}...")
            logger.info(f"   Статус: {status}")
            
            if found_kw:
                logger.info(f"   🔑 Найдены ключевые слова: {found_kw}")
            else:
                logger.info(f"   🔑 Ключевые слова: НЕ НАЙДЕНЫ")
                
            if found_ex:
                logger.info(f"   🚫 Найдены исключения: {found_ex}")
            
            if is_rel:
                relevant += 1
                logger.info(f"   ⭐ БЫЛО БЫ ОТПРАВЛЕНО!")
            
            logger.info("-" * 40)
        
        logger.info("\n" + "=" * 60)
        logger.info(f"📊 ИТОГИ:")
        logger.info(f"   Проверено сообщений: {checked}")
        logger.info(f"   Релевантных: {relevant}")
        logger.info(f"   Нерелевантных: {checked - relevant}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
    
    finally:
        if client.is_connected():
            await client.disconnect()

if __name__ == "__main__":
    async def main_test():
        from db import init_db
        init_db()
        
        print("\n" + "="*60)
        print("🧪 ТЕСТИРОВАНИЕ ПАРСЕРА")
        print("="*60)
        print("\n1. Полный парсинг всех чатов")
        print("2. Тест ключевых слов на одном чате")
        print("3. Проверить текущие ключевые слова")
        
        choice = input("\nВыберите режим (1/2/3): ").strip()
        
        if choice == "1":
            messages = await get_new_messages(limit_per_chat=30)
            print(f"\n📬 Найдено релевантных сообщений: {len(messages)}")
            for i, msg in enumerate(messages[:5], 1):
                print(f"\n{i}. 🔹 {msg['chat_title']}")
                print(f"   📝 {msg['message_text'][:100]}...")
                print(f"   🔑 Ключевые слова: {msg.get('found_keywords', [])}")
                
        elif choice == "2":
            chat_link = input("\nВведите ссылку на чат: ").strip()
            if chat_link:
                await test_keyword_matching(chat_link, limit=30)
                
        elif choice == "3":
            print("\n🔑 ТЕКУЩИЕ КЛЮЧЕВЫЕ СЛОВА:")
            for i, kw in enumerate(KEYWORDS, 1):
                print(f"   {i}. {kw}")
            print(f"\n🚫 ТЕКУЩИЕ ИСКЛЮЧЕНИЯ:")
            for i, ex in enumerate(EXCLUDE_WORDS, 1):
                print(f"   {i}. {ex}")
    
    try:
        asyncio.run(main_test())
    except KeyboardInterrupt:
        print("\n👋 Прервано пользователем")
