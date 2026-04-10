# parser.py
import asyncio
import logging
import os
from telethon import TelegramClient
from config import API_ID, API_HASH, TARGET_CHATS, KEYWORDS, EXCLUDE_WORDS
from db import is_message_sent, mark_message_sent

logging.basicConfig(level=logging.INFO)

async def get_new_messages():
    client = TelegramClient('user_session', API_ID, API_HASH)
    phone = os.getenv("PHONE_NUMBER")
    await client.start(phone=phone)
    logging.info("Telethon client started")

    results = []

    for chat_link in TARGET_CHATS:
        try:
            entity = await client.get_entity(chat_link)
            chat_id = entity.id

            async for message in client.iter_messages(entity, limit=50):
                if not message.text:
                    continue

                if is_message_sent(str(message.id), str(chat_id)):
                    continue

                text_lower = message.text.lower()
                # Проверяем ключевые слова
                if any(keyword in text_lower for keyword in KEYWORDS):
                    # Проверяем, что это не исключаемое слово
                    if not any(exclude in text_lower for exclude in EXCLUDE_WORDS):
                        results.append({
                            "chat_title": entity.title,
                            "chat_link": chat_link,
                            "message_text": message.text,
                            "message_link": f"https://t.me/c/{entity.id}/{message.id}"
                        })
                        mark_message_sent(str(message.id), str(chat_id))

        except Exception as e:
            logging.error(f"Error processing {chat_link}: {e}")

    await client.disconnect()
    return results
