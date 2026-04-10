import asyncio
import logging
import re
from telethon import TelegramClient, errors
from config import (
    API_ID, API_HASH, TARGET_CHATS,
    HELPER_KEYWORDS, HIRING_VERBS, ONE_TIME_JOB_KEYWORDS, PAYMENT_INDICATORS,
    EXCLUDE_CATEGORIES, STOP_PHRASES
)
from db import is_message_sent, mark_message_sent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def is_helper_message(text: str) -> tuple[bool, str, list]:
    """
    Строгая проверка: сообщение ТОЛЬКО про хелперов/промоутеров/аниматоров и т.д.
    
    Возвращает: (релевантно, причина, найденные ключевые слова)
    """
    if not text:
        return False, "empty", []
    
    text_lower = text.lower()
    
    # ШАГ 1: Проверяем стоп-фразы (исключаем сразу)
    for phrase in STOP_PHRASES:
        if phrase.lower() in text_lower:
            return False, f"stop_phrase: {phrase}", []
    
    # ШАГ 2: Проверяем, что это НЕ творческая профессия
    for category in EXCLUDE_CATEGORIES:
        if category.lower() in text_lower:
            # Дополнительная проверка: если есть "хелпер" или "промоутер" — оставляем
            if not any(hw in text_lower for hw in ["хелпер", "хэлпер", "промоутер", "аниматор", "грузчик"]):
                return False, f"excluded_category: {category}", []
    
    # ШАГ 3: Ищем прямые указания на хелперов
    found_helpers = [hw for hw in HELPER_KEYWORDS if hw.lower() in text_lower]
    
    # ШАГ 4: Ищем глаголы найма
    found_hiring = [hv for hv in HIRING_VERBS if hv.lower() in text_lower]
    
    # ШАГ 5: Ищем признаки разовой работы
    found_one_time = [ot for ot in ONE_TIME_JOB_KEYWORDS if ot.lower() in text_lower]
    
    # ШАГ 6: Ищем признаки оплаты
    found_payment = [pi for pi in PAYMENT_INDICATORS if pi.lower() in text_lower]
    
    # ЛОГИКА ПРИНЯТИЯ РЕШЕНИЯ:
    # Вариант А: Прямое указание на хелпера/промоутера + глагол найма
    if found_helpers and found_hiring:
        return True, "helper_plus_hiring", found_helpers + found_hiring[:2]
    
    # Вариант Б: Прямое указание на хелпера + признак разовой работы
    if found_helpers and found_one_time:
        return True, "helper_plus_one_time", found_helpers + found_one_time[:2]
    
    # Вариант В: Прямое указание на хелпера + оплата
    if found_helpers and found_payment:
        return True, "helper_plus_payment", found_helpers + found_payment[:2]
    
    # Вариант Г: Глагол найма + "хелпер" в тексте (даже если не в HELPER_KEYWORDS)
    if found_hiring and "хелпер" in text_lower:
        return True, "hiring_plus_helper_text", found_hiring + ["хелпер"]
    
    # Вариант Д: Глагол найма + "промоутер" в тексте
    if found_hiring and "промоутер" in text_lower:
        return True, "hiring_plus_promoter_text", found_hiring + ["промоутер"]
    
    # Если ничего не подошло — не релевантно
    return False, "no_match", []

def clean_message_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def get_message_link(chat_id: int, message_id: int) -> str:
    str_id = str(chat_id)
    if str_id.startswith('-100'):
        clean_id = str_id[4:]
        return f"https://t.me/c/{clean_id}/{message_id}"
    elif str_id.startswith('-'):
        clean_id = str_id[1:]
        return f"https://t.me/c/{clean_id}/{message_id}"
    return f"https://t.me/c/{chat_id}/{message_id}"

async def safe_get_entity(client: TelegramClient, chat_link: str):
    try:
        entity = await client.get_entity(chat_link)
        await asyncio.sleep(1)
        return entity
    except Exception as e:
        logger.error(f"❌ {chat_link}: {type(e).__name__}")
        return None

async def get_new_messages(limit_per_chat: int = 30) -> list:
    client = TelegramClient('user_session', API_ID, API_HASH)
    all_results = []
    
    try:
        await client.start()
        logger.info("✅ Telethon client started")
        
        logger.info(f"🎯 ИЩЕМ ТОЛЬКО: {', '.join(HELPER_KEYWORDS[:10])}...")
        logger.info(f"🚫 ИСКЛЮЧАЕМ: {', '.join(EXCLUDE_CATEGORIES[:10])}...")
        
        for chat_link in TARGET_CHATS:
            entity = await safe_get_entity(client, chat_link)
            if not entity:
                continue
            
            chat_title = getattr(entity, 'title', None) or 'Без названия'
            chat_id = str(entity.id)
            
            async for message in client.iter_messages(entity, limit=limit_per_chat):
                if not message.text:
                    continue
                
                message_id = str(message.id)
                
                # Проверяем, не обработано ли уже
                if is_message_sent(message_id, chat_id):
                    continue
                
                # СТРОГАЯ ПРОВЕРКА НА ХЕЛПЕРА
                is_relevant, reason, keywords = is_helper_message(message.text)
                
                if not is_relevant:
                    continue
                
                cleaned_text = clean_message_text(message.text)
                message_link = get_message_link(entity.id, message.id)
                
                result = {
                    "chat_title": chat_title,
                    "message_text": cleaned_text,
                    "message_link": message_link,
                    "keywords": keywords[:5],
                    "reason": reason,
                }
                all_results.append(result)
                
                mark_message_sent(message_id, chat_id)
                logger.info(f"✅ {chat_title}: {cleaned_text[:60]}... (причина: {reason})")
                
                await asyncio.sleep(0.3)
            
            await asyncio.sleep(2)
        
        logger.info(f"🏁 Найдено хелперов: {len(all_results)}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()
    
    return all_results

# Тестовая функция
async def test_filter(chat_link: str, limit: int = 30):
    client = TelegramClient('user_session', API_ID, API_HASH)
    
    try:
        await client.start()
        logger.info(f"\n{'='*60}")
        logger.info(f"🧪 ТЕСТ ФИЛЬТРА ХЕЛПЕРОВ: {chat_link}")
        logger.info(f"{'='*60}\n")
        
        entity = await safe_get_entity(client, chat_link)
        if not entity:
            return
        
        passed = 0
        blocked = 0
        
        async for message in client.iter_messages(entity, limit=limit):
            if not message.text:
                continue
            
            is_rel, reason, keywords = is_helper_message(message.text)
            
            if is_rel:
                passed += 1
                logger.info(f"✅ [{reason}] {message.text[:80]}...")
                logger.info(f"   Ключевые слова: {keywords}")
            else:
                blocked += 1
                logger.debug(f"❌ [{reason}] {message.text[:80]}...")
        
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 ПРОПУЩЕНО: {passed} | ОТСЕЯНО: {blocked}")
        logger.info(f"{'='*60}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()

if __name__ == "__main__":
    async def main():
        from db import init_db
        init_db()
        
        choice = input("1 - парсинг, 2 - тест: ").strip()
        if choice == "1":
            msgs = await get_new_messages()
            print(f"\nНайдено: {len(msgs)}")
        elif choice == "2":
            link = input("Ссылка на чат: ").strip()
            await test_filter(link)
    
    asyncio.run(main())
