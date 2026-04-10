import asyncio
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from db import init_db
from parser import get_new_messages
from config import BOT_TOKEN, YOUR_USER_ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def escape_markdown(text: str) -> str:
    """Экранирует спецсимволы Markdown"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def format_order_message(order: dict) -> str:
    """Формирует безопасное сообщение с MarkdownV2"""
    chat_title = escape_markdown(order['chat_title'])
    message_text = escape_markdown(order['message_text'][:500])
    message_link = order['message_link']
    
    return (
        f"🔔 *{chat_title}*\n\n"
        f"{message_text}\n\n"
        f"📎 [Перейти к сообщению]({message_link})"
    )

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("👋 Бот запущен. Отправьте /check_now для поиска заказов.")

@dp.message(Command("check_now"))
async def check_now_cmd(message: types.Message):
    if message.from_user.id != YOUR_USER_ID:
        await message.answer("⛔ У вас нет прав.")
        return
    
    await message.answer("🔍 Начинаю проверку...")
    orders = await get_new_messages()
    
    if not orders:
        await message.answer("✅ Новых заказов не найдено.")
        return
    
    for order in orders:
        try:
            text = format_order_message(order)
            await bot.send_message(
                YOUR_USER_ID, 
                text, 
                parse_mode="MarkdownV2",
                disable_web_page_preview=True
            )
        except Exception as e:
            # Если даже с экранированием ошибка — отправляем без форматирования
            plain_text = f"🔔 {order['chat_title']}\n\n{order['message_text'][:500]}\n\n📎 {order['message_link']}"
            await bot.send_message(YOUR_USER_ID, plain_text)
    
    await message.answer(f"✅ Найдено и отправлено {len(orders)} заказов.")

async def periodic_check():
    """Автоматическая проверка каждые 5 минут"""
    while True:
        try:
            orders = await get_new_messages()
            for order in orders:
                try:
                    text = format_order_message(order)
                    await bot.send_message(
                        YOUR_USER_ID, 
                        text, 
                        parse_mode="MarkdownV2",
                        disable_web_page_preview=True
                    )
                except Exception:
                    plain_text = f"🔔 {order['chat_title']}\n\n{order['message_text'][:500]}\n\n📎 {order['message_link']}"
                    await bot.send_message(YOUR_USER_ID, plain_text)
        except Exception as e:
            print(f"Ошибка в periodic_check: {e}")
        
        await asyncio.sleep(300)  # 5 минут

async def main():
    init_db()
    # Запускаем фоновую периодическую проверку
    asyncio.create_task(periodic_check())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
