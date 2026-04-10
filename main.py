# main.py
import asyncio
from db import init_db
from parser import get_new_messages
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from config import BOT_TOKEN, YOUR_USER_ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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
        text = (
            f"🔔 **{order['chat_title']}**\n\n"
            f"{order['message_text'][:500]}\n\n"
            f"📎 [Перейти к сообщению]({order['message_link']})"
        )
        await bot.send_message(YOUR_USER_ID, text, parse_mode="Markdown")
    await message.answer(f"✅ Найдено и отправлено {len(orders)} заказов.")

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())