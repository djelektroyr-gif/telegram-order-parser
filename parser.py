import asyncio
import logging
import re
from telethon import TelegramClient, errors
from telethon.tl.types import Channel, Chat, User
from config import (
    API_ID, API_HASH, TARGET_CHATS, 
    HIRING_KEYWORDS, PAYMENT_KEYWORDS, URGENT_KEYWORDS, EXCLUDE_WORDS
)
from db import is_message_sent, mark_message_sent

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

def is_relevant_message(text: str) -> tuple[bool, dict]:
    """
    Новая логика проверки:
    1. Должно быть хотя бы одно слово из HIRING_KEYWORDS
    2. Должно быть либо слово из PAYMENT_KEYWORDS, либо слово из URGENT_KEYWORDS
    3. Не должно быть слов из EXCLUDE_WORDS
    """
    if not text:
        return False, {}
    
    text_lower = text.lower()
    
    # Проверяем исключения ПЕРВЫМ ДЕЛОМ
    found_excludes = [ex for ex in EXCLUDE_WORDS if ex.lower() in text_lower]
    if found_excludes:
        logger.debug(f"  🚫 Отфильтровано по исключениям: {found_excludes}")
        return False, {"excludes": found_excludes}
    
    # Ищем ключевые слова найма
    found_hiring = [kw for kw in HIRING_KEYWORDS if kw.lower() in text_lower]
    
    # Ищем ключевые слова оплаты
    found_payment = [kw for kw in PAYMENT_KEYWORDS if kw.lower() in text_lower]
    
    # Ищем ключевые слова срочности
    found_urgent = [kw for kw in URGENT_KEYWORDS if kw.lower() in text_lower]
    
    # Условия релевантности:
    # 1. Обязательно наличие слов найма
    if not found_hiring:
        return False, {"reason": "no_hiring"}
    
    # 2. Должна быть либо оплата, либо срочность
    has_payment_or_urgent = bool(found_payment) or bool(found_urgent)
    
    if not has_payment_or_urgent:
        return False, {"reason": "no_payment_no_urgent", "hiring": found_hiring}
    
    # Сообщение релевантно!
    info = {
        "hiring": found_hiring,
        "payment": found_payment,
        "urgent": found_urgent,
    }
    
    logger.info(f"  ✅ Найм: {found_hiring[:2]}, Оплата: {found_payment[:1] if found_payment else 'нет'}, Срочность: {found_urgent[:1] if found_urgent else 'нет'}")
    
    return True, info

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
    except Exception as e:
        logger.error(f"❌ Ошибка получения entity для {chat_link}: {type(e).__name__}")
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
            
            # Проверяем релевантность
            is_relevant, info = is_relevant_message(message.text)
            
            if not is_relevant:
                continue
            
            relevant_count += 1
            
            cleaned_text = clean_message_text(message.text)
            message_link = get_message_link(chat_id, message.id)
            
            result = {
                "chat_title": chat_title,
                "chat_link": chat_link,
                "chat_id": str(chat_id),
                "message_id": message_id,
                "message_text": cleaned_text,
                "message_link": message_link,
                "message_date": message.date.isoformat() if message.date else None,
                "hiring_keywords": info.get("hiring", [])[:3],
            }
            results.append(result)
            
            mark_message_sent(message_id, str(chat_id))
            logger.info(f"  ✅ НАЙДЕНО: {chat_title} - {cleaned_text[:80]}...")
            
            await asyncio.sleep(0.3)
        
        if relevant_count > 0:
            logger.info(f"  📊 {chat_title}: проверено {message_count}, найдено {relevant_count}")
        
        await asyncio.sleep(2)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки чата {chat_link}: {type(e).__name__}")
    
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
        
        logger.info(f"🔑 Ключевые слова найма: {len(HIRING_KEYWORDS)}")
        logger.info(f"💵 Ключевые слова оплаты: {len(PAYMENT_KEYWORDS)}")
        logger.info(f"⚡ Ключевые слова срочности: {len(URGENT_KEYWORDS)}")
        logger.info(f"🚫 Исключений: {len(EXCLUDE_WORDS)}")
        
        total_chats = len(TARGET_CHATS)
        logger.info(f"📋 Начинаю обработку {total_chats} чатов...")
        
        for idx, chat_link in enumerate(TARGET_CHATS, 1):
            logger.info(f"🔄 Обработка чата [{idx}/{total_chats}]: {chat_link}")
            
            chat_results = await process_single_chat(client, chat_link, limit_per_chat)
            all_results.extend(chat_results)
            
        logger.info(f"🏁 Парсинг завершён. Всего найдено {len(all_results)} новых сообщений")
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {type(e).__name__}: {e}")
    
    finally:
        if client.is_connected():
            await client.disconnect()
            logger.info("🔌 Telethon client disconnected")
    
    return all_results

# Функция для тестирования
async def test_filter(chat_link: str, limit: int = 30):
    """Тестирует новую логику фильтрации на конкретном чате"""
    client = TelegramClient('user_session', API_ID, API_HASH)
    
    try:
        await client.start()
        logger.info(f"\n{'='*60}")
        logger.info(f"🧪 ТЕСТИРОВАНИЕ НОВОЙ ЛОГИКИ ФИЛЬТРАЦИИ")
        logger.info(f"📌 Чат: {chat_link}")
        logger.info(f"{'='*60}\n")
        
        entity = await safe_get_entity(client, chat_link)
        if not entity:
            return
        
        chat_title = getattr(entity, 'title', 'Без названия')
        
        checked = 0
        relevant = 0
        false_positive = 0
        
        async for message in client.iter_messages(entity, limit=limit):
            if not message.text:
                continue
            
            checked += 1
            is_rel, info = is_relevant_message(message.text)
            
            if is_rel:
                relevant += 1
                status = "✅ ОТПРАВЛЕНО БЫ"
            else:
                # Проверяем, сработало бы по старой логике?
                text_lower = message.text.lower()
                old_logic = any(kw in text_lower for kw in ['сегодня', 'завтра', 'оплата', 'ставка'])
                if old_logic and not is_rel:
                    false_positive += 1
                    status = "🟡 ОТФИЛЬТРОВАНО (раньше бы отправилось)"
                else:
                    status = "❌ НЕРЕЛЕВАНТНО"
            
            logger.info(f"\n📝 Сообщение {message.id}:")
            logger.info(f"   Текст: {message.text[:100]}...")
            logger.info(f"   Статус: {status}")
            
            if info.get("hiring"):
                logger.info(f"   🔑 Найм: {info['hiring']}")
            else:
                logger.info(f"   🔑 Найм: НЕТ")
                
            if info.get("payment"):
                logger.info(f"   💵 Оплата: {info['payment']}")
            if info.get("urgent"):
                logger.info(f"   ⚡ Срочность: {info['urgent']}")
            if info.get("excludes"):
                logger.info(f"   🚫 Исключения: {info['excludes']}")
            
            logger.info("-" * 40)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 ИТОГИ:")
        logger.info(f"   Проверено сообщений: {checked}")
        logger.info(f"   Релевантных (будут отправлены): {relevant}")
        logger.info(f"   Отфильтровано ложных срабатываний: {false_positive}")
        logger.info(f"{'='*60}")
        
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
        print("2. Тест фильтрации на одном чате")
        
        choice = input("\nВыберите режим (1/2): ").strip()
        
        if choice == "1":
            messages = await get_new_messages(limit_per_chat=30)
            print(f"\n📬 Найдено релевантных сообщений: {len(messages)}")
            for i, msg in enumerate(messages[:5], 1):
                print(f"\n{i}. 🔹 {msg['chat_title']}")
                print(f"   📝 {msg['message_text'][:100]}...")
                
        elif choice == "2":
            chat_link = input("\nВведите ссылку на чат: ").strip()
            if chat_link:
                await test_filter(chat_link, limit=30)
    
    try:
        asyncio.run(main_test())
    except KeyboardInterrupt:
        print("\n👋 Прервано пользователем")
