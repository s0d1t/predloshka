import asyncio
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# Загружаем переменные из системы или файла .env
load_dotenv()

# --- КОНФИГУРАЦИЯ ИЗ ПЕРЕМЕННЫХ ---
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
admin_ids_raw = os.getenv("ADMIN_IDS")

# Преобразуем строку "ID1,ID2" в список чисел
try:
    ADMIN_IDS = [int(id.strip()) for id in admin_ids_raw.split(",")] if admin_ids_raw else []
    CHANNEL_ID = int(CHANNEL_ID)
except (ValueError, TypeError):
    print("❌ ОШИБКА: Проверьте правильность ADMIN_IDS и CHANNEL_ID в переменных!")
    ADMIN_IDS = []

# Логирование для отслеживания работы на хостинге
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- БАЗА ДАННЫХ (Защита от дублей) ---
def init_db():
    conn = sqlite3.connect('bot_memory.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS processed (req_id TEXT PRIMARY KEY)''')
    conn.commit()
    conn.close()

def check_processed(req_id):
    conn = sqlite3.connect('bot_memory.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM processed WHERE req_id = ?', (req_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def mark_processed(req_id):
    conn = sqlite3.connect('bot_memory.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO processed (req_id) VALUES (?)', (req_id,))
    conn.commit()
    conn.close()

class AdminStates(StatesGroup):
    waiting_for_reason = State()

# --- КЛАВИАТУРЫ ---
def get_admin_kb(user_id, message_id):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{user_id}_{message_id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{user_id}_{message_id}")
    )
    return builder.as_markup()

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("👋 **Приветствую!**\n\nПришлите текст, фото или стикер, и я анонимно отправлю их в канал после проверки модератором.")

# Обработка причины отказа (только для админов в состоянии ожидания)
@dp.message(AdminStates.waiting_for_reason)
async def process_reject_reason(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    target_user = data['target_user']
    
    try:
        await bot.send_message(int(target_user), f"⚠️ **Модератор отклонил ваше сообщение.**\n💬 Причина: {message.text}")
        await message.answer("✅ Причина отправлена автору.")
    except Exception as e:
        await message.answer(f"❌ Не удалось уведомить автора: {e}")
    
    await state.clear()

# Прием сообщений от пользователей
@dp.message(F.text | F.photo | F.sticker)
async def handle_submission(message: types.Message):
    # Если пишет админ вне режима отказа — игнорируем, чтобы не было бага
    if message.from_user.id in ADMIN_IDS:
        return 

    await message.answer("⌛ Ожидайте...")

    header = f"📩 **Новая заявка**\n👤 От: @{message.from_user.username or 'скрыт'}\n🆔 ID: `{message.from_user.id}`\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    kb = get_admin_kb(message.from_user.id, message.message_id)

    for admin in ADMIN_IDS:
        try:
            if message.text:
                await bot.send_message(admin, f"{header}📝 Текст: {message.text}", reply_markup=kb)
            elif message.photo:
                await bot.send_photo(admin, message.photo[-1].file_id, caption=header, reply_markup=kb)
            elif message.sticker:
                await bot.send_message(admin, f"{header}🎭 Стикер:")
                await bot.send_sticker(admin, message.sticker.file_id, reply_markup=kb)
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin}: {e}")

# Кнопки админки
@dp.callback_query(F.data.startswith("ok_"))
async def approve(callback: types.CallbackQuery):
    _, uid, mid = callback.data.split("_")
    rid = f"{uid}_{mid}"

    if check_processed(rid):
        return await callback.answer("⚠️ Уже обработано!", show_alert=True)

    mark_processed(rid)

    try:
        if callback.message.text:
            text = callback.message.text.split("📝 Текст: ")[-1]
            await bot.send_message(CHANNEL_ID, text)
        elif callback.message.photo:
            await bot.send_photo(CHANNEL_ID, callback.message.photo[-1].file_id)
        elif callback.message.sticker:
            await bot.send_sticker(CHANNEL_ID, callback.message.sticker.file_id)

        await bot.send_message(int(uid), "🎉 **Ваше сообщение опубликовано в канале!**")
        await callback.message.edit_text("✅ **ОДОБРЕНО**")
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")

@dp.callback_query(F.data.startswith("no_"))
async def reject(callback: types.CallbackQuery, state: FSMContext):
    _, uid, mid = callback.data.split("_")
    rid = f"{uid}_{mid}"

    if check_processed(rid):
        return await callback.answer("⚠️ Уже обработано!", show_alert=True)

    mark_processed(rid)
    await state.update_data(target_user=uid)
    await state.set_state(AdminStates.waiting_for_reason)
    
    await callback.message.answer("💬 **Введите причину отказа:**")
    await callback.answer()

async def main():
    init_db()
    logger.info("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())