# bot.py
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from config import BOT_TOKEN, YOUR_USER_ID
from parser import get_new_messages

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("👋 Бот запущен. Парсинг активен.")

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
        text = (
            f"🔔 **{order['chat_title']}**\n\n"
            f"{order['message_text'][:500]}\n\n"
            f"📎 [Перейти к сообщению]({order['message_link']})"
        )
        await bot.send_message(YOUR_USER_ID, text, parse_mode="Markdown")
    await message.answer(f"✅ Найдено и отправлено {len(orders)} заказов.")

async def periodic_check():
    while True:
        orders = await get_new_messages()
        if orders:
            for order in orders:
                text = (
                    f"🔔 **{order['chat_title']}**\n\n"
                    f"{order['message_text'][:500]}\n\n"
                    f"📎 [Перейти к сообщению]({order['message_link']})"
                )
                await bot.send_message(YOUR_USER_ID, text, parse_mode="Markdown")
        await asyncio.sleep(300)  # 5 минут