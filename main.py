import asyncio
import os  # Добавили для работы с переменными окружения
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
# Если переменная не найдена, будет использовано пустое значение или стандартное
TOKEN = os.getenv("BOT_TOKEN")
# Преобразуем строку ID через запятую в список чисел
admin_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(i.strip()) for i in admin_raw.split(",") if i.strip()]
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))

bot = Bot(token=TOKEN)
dp = Dispatcher()

processed_requests = set()

class AdminStates(StatesGroup):
    waiting_for_reject_reason = State()

# --- КЛАВИАТУРЫ ---
def get_admin_kb(user_id, message_id):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"appr_{user_id}_{message_id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"rejc_{user_id}_{message_id}")
    )
    return builder.as_markup()

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **Приветствую, пользователь!**\n\n"
        "📩 Для отправки анонимного сообщения введите текст, прикрепите фото или стикер:"
    )

@dp.message(AdminStates.waiting_for_reject_reason)
async def process_rejection_reason(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    user_id, msg_id = data['rejection_target']
    reason = message.text
    
    try:
        await bot.send_message(int(user_id), f"⚠️ **Ваше сообщение отклонено**\n💬 Причина: {reason}")
        await message.answer(f"✅ Причина отправлена пользователю (ID: `{user_id}`)")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить уведомление пользователю: {e}")

    await state.clear()

@dp.message(F.text | F.photo | F.sticker)
async def handle_user_submission(message: types.Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        return 

    await message.answer("⌛ Ожидайте...")

    info = (
        f"📩 **Новая заявка!**\n"
        f"👤 Отправитель: @{message.from_user.username or 'скрыт'}\n"
        f"🆔 ID: `{message.from_user.id}`\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
    )
    
    for admin_id in ADMIN_IDS:
        try:
            kb = get_admin_kb(message.from_user.id, message.message_id)
            if message.text:
                await bot.send_message(admin_id, f"{info}📝 Текст: {message.text}", reply_markup=kb)
            elif message.photo:
                await bot.send_photo(admin_id, message.photo[-1].file_id, caption=info, reply_markup=kb)
            elif message.sticker:
                await bot.send_message(admin_id, f"{info}🎭 Стикер:")
                await bot.send_sticker(admin_id, message.sticker.file_id, reply_markup=kb)
        except Exception as e:
            print(f"Ошибка отправки админу {admin_id}: {e}")

# --- КНОПКИ ---

@dp.callback_query(F.data.startswith("appr_"))
async def approve_callback(callback: types.CallbackQuery):
    _, user_id, msg_id = callback.data.split("_")
    request_id = f"{user_id}_{msg_id}"

    if request_id in processed_requests:
        return await callback.answer("⚠️ Уже обработано!", show_alert=True)

    processed_requests.add(request_id)
    
    try:
        if callback.message.text:
            text_to_send = callback.message.text.split("📝 Текст: ")[-1]
            await bot.send_message(CHANNEL_ID, text_to_send)
        elif callback.message.photo:
            await bot.send_photo(CHANNEL_ID, callback.message.photo[-1].file_id)
        elif callback.message.sticker:
            await bot.send_sticker(CHANNEL_ID, callback.message.sticker.file_id)

        await bot.send_message(int(user_id), "📢 **Ваше сообщение опубликовано в канале!**")
        await callback.message.edit_text(f"{callback.message.text or 'Медиа'}\n\n✅ **ОПУБЛИКОВАНО**")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("rejc_"))
async def reject_callback(callback: types.CallbackQuery, state: FSMContext):
    _, user_id, msg_id = callback.data.split("_")
    request_id = f"{user_id}_{msg_id}"

    if request_id in processed_requests:
        return await callback.answer("⚠️ Уже обработано!", show_alert=True)

    processed_requests.add(request_id)
    await state.update_data(rejection_target=(user_id, msg_id))
    await state.set_state(AdminStates.waiting_for_reject_reason)
    
    await callback.message.answer("💬 **Введите причину отказа:**\n(Пользователь получит это сообщение)")
    await callback.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())