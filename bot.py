import os
import logging
import asyncio
from PIL import Image
from moviepy.editor import VideoFileClip
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8852627951:AAFKdZUrKLo1Kkb2CPnuIud_FPDX7X5pgHI"
# Директории для временного хранения файлов
TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)
# --- КОНЕЦ НАСТРОЕК ---

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Словари для хранения данных пользователей
user_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для сжатия фото и видео.\n\n"
        "Просто отправь мне картинку или видео, и я предложу варианты сжатия.\n"
        "Попробуй отправить файл прямо сейчас!"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message

    # Получаем файл (это может быть фото, документ или видео)
    file_obj = None
    file_type = None
    if message.document and message.document.mime_type.startswith('image/'):
        file_obj = message.document
        file_type = 'image'
    elif message.photo:
        file_obj = message.photo[-1]  # берем самую большую версию
        file_type = 'image'
    elif message.video:
        file_obj = message.video
        file_type = 'video'
    elif message.document and message.document.mime_type.startswith('video/'):
        file_obj = message.document
        file_type = 'video'
    else:
        await message.reply_text("Пожалуйста, отправь изображение или видеофайл.")
        return

    # Отправляем сообщение о начале обработки
    status_msg = await message.reply_text("⏳ Получаю файл...")
    try:
        # Скачиваем файл
        new_file = await file_obj.get_file()
        file_path = os.path.join(TEMP_DIR, f"{user_id}_{new_file.file_unique_id}_{file_obj.file_name if hasattr(file_obj, 'file_name') else 'file'}")
        await new_file.download_to_drive(file_path)
        
        original_size = os.path.getsize(file_path) / (1024 * 1024)  # Размер в МБ
        
        # Сохраняем информацию для колбэков
        user_data[user_id] = {'file_path': file_path, 'file_type': file_type, 'original_size': original_size}
        
        # Создаем кнопки для выбора степени сжатия
        keyboard = [
            [InlineKeyboardButton("Сильное (70%)", callback_data='high')],
            [InlineKeyboardButton("Среднее (85%)", callback_data='medium')],
            [InlineKeyboardButton("Слабое (95%)", callback_data='low')],
            [InlineKeyboardButton("❌ Отмена", callback_data='cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await status_msg.edit_text(
            f"📁 Файл получен!\n"
            f"📏 Исходный размер: {original_size:.2f} МБ\n\n"
            f"🎚️ Выбери степень сжатия:",
            reply_markup=reply_markup
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при получении файла: {str(e)}")
        logging.error(f"Error in handle_file: {e}")

async def compress_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    if user_id not in user_data:
        await query.edit_message_text("⏳ Сессия истекла. Пожалуйста, отправь файл заново.")
        return
    
    compression_type = query.data
    file_info = user_data[user_id]
    file_path = file_info['file_path']
    file_type = file_info['file_type']
    original_size = file_info['original_size']
    
    await query.edit_message_text(f"🔄 Начинаю сжатие... Это может занять некоторое время.")
    
    output_path = None
    result_size_mb = 0
    try:
        if file_type == 'image':
            output_path = await compress_image(file_path, compression_type)
        elif file_type == 'video':
            output_path = await compress_video(file_path, compression_type)
        else:
            await query.edit_message_text("❌ Неподдерживаемый тип файла.")
            return
        
        result_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        compression_ratio = (1 - result_size_mb / original_size) * 100
        
        # Отправляем результат
        with open(output_path, 'rb') as f:
            caption = (
                f"✅ Файл успешно сжат!\n"
                f"📏 Исходный размер: {original_size:.2f} МБ\n"
                f"📏 Новый размер: {result_size_mb:.2f} МБ\n"
                f"🎯 Сжатие: {compression_ratio:.1f}%"
            )
            if file_type == 'image':
                await query.message.reply_document(document=f, filename=os.path.basename(output_path), caption=caption)
            else:
                await query.message.reply_video(video=f, caption=caption)
        
        await query.delete_message()  # Удаляем сообщение с кнопками
    except Exception as e:
        await query.edit_message_text(f"❌ Произошла ошибка при сжатии: {str(e)}")
        logging.error(f"Error in compress_callback: {e}")
    finally:
        # Очистка временных файлов
        if os.path.exists(file_path):
            os.remove(file_path)
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
        if user_id in user_data:
            del user_data[user_id]

async def compress_image(image_path, compression_type):
    quality_map = {'high': 70, 'medium': 85, 'low': 95}
    quality = quality_map[compression_type]
    
    output_path = image_path + "_compressed.jpg"  # Сохраняем как JPEG для лучшего сжатия
    
    with Image.open(image_path) as img:
        # Конвертируем в RGB, если нужно (для PNG с альфа-каналом)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(output_path, 'JPEG', quality=quality, optimize=True)
    
    return output_path

async def compress_video(video_path, compression_type):
    bitrate_map = {'high': '500k', 'medium': '1000k', 'low': '2000k'}
    bitrate = bitrate_map[compression_type]
    
    output_path = video_path + "_compressed.mp4"
    
    try:
        clip = VideoFileClip(video_path)
        
        # Устанавливаем параметры сжатия
        clip.write_videofile(
            output_path,
            codec='libx264',  # Кодек для сжатия
            audio_codec='aac',
            bitrate=bitrate,
            preset='medium',  # Баланс между скоростью и качеством
            threads=4,  # Количество потоков для ускорения
            logger=None,  # Отключаем логи moviepy
            verbose=False
        )
        clip.close()
    except Exception as e:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise e
    
    return output_path

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.IMAGE | filters.Document.VIDEO, handle_file))
    app.add_handler(CallbackQueryHandler(compress_callback))
    
    print("🤖 Бот для сжатия запущен и готов к работе...")
    app.run_polling()

if __name__ == '__main__':
    main()
