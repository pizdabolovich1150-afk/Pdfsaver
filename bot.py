import os
import re
import asyncio
import logging
from io import BytesIO
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageService
from fpdf import FPDF
from PIL import Image
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------- Переменные окружения ----------
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
PHONE = os.environ["PHONE"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
SESSION_STRING = os.environ["SESSION_STRING"]
# ------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

async def ensure_authorized():
    if not client.is_connected():
        await client.connect()
    if not await client.is_user_authorized():
        raise Exception("Сессия недействительна. Пересоздайте SESSION_STRING.")

def generate_pdf(text: str, image_bytes: bytes = None) -> bytes:
    """Генерирует PDF: сначала картинка, потом текст."""
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)
    pdf.set_font("DejaVu", size=12)

    if image_bytes:
        img = Image.open(BytesIO(image_bytes))
        w, h = img.size
        max_w = 180  # мм
        if w > max_w:
            ratio = max_w / w
            w, h = int(w * ratio), int(h * ratio)
        tmp_path = "/tmp/tg_img.jpg"
        img.save(tmp_path, "JPEG")
        pdf.image(tmp_path, x=10, w=w, h=h)
        os.remove(tmp_path)
        pdf.ln(5)

    if text:
        for line in text.split("\n"):
            pdf.multi_cell(0, 10, line)
            pdf.ln(2)

    return pdf.output()

# ---------- Обработчики ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Я умею сохранять посты из Telegram в PDF.\n\n"
        "📎 **Вариант 1:** пришли ссылку на пост (публичный или приватный канал):\n"
        "`t.me/username/123`\n"
        "`t.me/username/thread/456`\n"
        "`t.me/c/123456789/789`\n\n"
        "↩️ **Вариант 2:** перешли мне сообщение из любого чата (с текстом, фото, видео, GIF).\n"
        "Я заберу текст и картинку и пришлю PDF.",
        parse_mode="Markdown"
    )

# Обработка ссылок
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text.strip()
    match_private = re.search(r"t\.me/c/(\d+)/(\d+)(?:/(\d+))?", msg_text)
    if match_private:
        raw_chat_id = match_private.group(1)
        channel = int(f"-100{raw_chat_id}")
        thread_id = int(match_private.group(2)) if match_private.group(2) else None
        if match_private.group(3):
            msg_id = int(match_private.group(3))
        else:
            msg_id = thread_id
            thread_id = None
    else:
        match_public = re.search(r"t\.me/([^/]+)/(\d+)(?:/(\d+))?", msg_text)
        if not match_public:
            await update.message.reply_text("❌ Неверный формат ссылки.")
            return
        channel = match_public.group(1)
        thread_id = int(match_public.group(2)) if match_public.group(2) else None
        if match_public.group(3):
            msg_id = int(match_public.group(3))
        else:
            msg_id = thread_id
            thread_id = None

    await update.message.reply_text("🔍 Получаю пост…")
    try:
        await ensure_authorized()
        message = await client.get_messages(channel, ids=msg_id)
        if not message:
            await update.message.reply_text("❌ Сообщение не найдено. Возможно, у технического аккаунта нет доступа.")
            return
        if isinstance(message, MessageService):
            await update.message.reply_text("❌ Это служебное сообщение (закреп, создание темы и т.п.).")
            return

        text = message.text or message.caption or ""
        image_bytes = await extract_media_from_telethon(message)

        if not text and not image_bytes:
            await update.message.reply_text("❌ В посте нет ни текста, ни картинки.")
            return

        pdf_data = generate_pdf(text, image_bytes)
        await update.message.reply_document(
            document=BytesIO(pdf_data),
            filename=f"post_{msg_id}.pdf",
            caption="✅ PDF готов!"
        )
    except ValueError as e:
        await update.message.reply_text("❌ Не удалось найти чат/канал. Возможно, он приватный, и у технического аккаунта нет доступа.")
    except Exception as e:
        logger.exception("Ошибка при обработке ссылки")
        await update.message.reply_text(f"⚠️ Произошла ошибка: {e}")

# Обработка пересланных сообщений
async def handle_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # Проверяем, что сообщение переслано
    if not msg.forward_origin:
        # Не пересланное — игнорируем (могут быть обычные текстовые сообщения, не ссылки)
        return

    await update.message.reply_text("📥 Обрабатываю пересланное сообщение…")
    try:
        text = msg.text or msg.caption or ""
        image_bytes = await extract_media_from_bot_message(msg)

        if not text and not image_bytes:
            await update.message.reply_text("❌ В пересланном сообщении нет ни текста, ни поддерживаемого медиа.")
            return

        pdf_data = generate_pdf(text, image_bytes)
        await update.message.reply_document(
            document=BytesIO(pdf_data),
            filename="forwarded_post.pdf",
            caption="✅ PDF готов!"
        )
    except Exception as e:
        logger.exception("Ошибка при обработке пересланного сообщения")
        await update.message.reply_text(f"⚠️ Ошибка: {e}")

# Вспомогательные функции для извлечения медиа
async def extract_media_from_telethon(message) -> bytes | None:
    """Извлекает картинку из сообщения Telethon."""
    if message.photo:
        path = await message.download_media(file="/tmp/")
        data = _read_and_remove(path)
        return data
    if message.video:
        # Пробуем миниатюру
        if message.video.thumbs:
            thumb = message.video.thumbs[0]
            path = await client.download_media(thumb, file="/tmp/")
            if path:
                return _read_and_remove(path)
    if message.document:
        mime = message.document.mime_type or ""
        if "image" in mime or mime == "video/mp4":  # некоторые GIF приходят как mp4
            path = await message.download_media(file="/tmp/")
            if path:
                if mime == "image/gif":
                    # Берем первый кадр GIF
                    img = Image.open(path)
                    img = img.convert("RGB")
                    jpg_path = "/tmp/tg_gif_frame.jpg"
                    img.save(jpg_path, "JPEG")
                    os.remove(path)
                    return _read_and_remove(jpg_path)
                else:
                    return _read_and_remove(path)
    return None

async def extract_media_from_bot_message(msg) -> bytes | None:
    """Извлекает картинку из сообщения бота (python-telegram-bot)."""
    # Фото
    if msg.photo:
        file = await msg.photo[-1].get_file()
        bio = BytesIO()
        await file.download_to_memory(bio)
        bio.seek(0)
        return bio.read()

    # Видео — берём миниатюру (thumbnail)
    if msg.video:
        if msg.video.thumbnail:
            file = await msg.video.thumbnail.get_file()
            bio = BytesIO()
            await file.download_to_memory(bio)
            bio.seek(0)
            return bio.read()

    # GIF (анимация) или документ-изображение
    if msg.document:
        mime = msg.document.mime_type or ""
        if "image" in mime or "gif" in mime:
            file = await msg.document.get_file()
            bio = BytesIO()
            await file.download_to_memory(bio)
            bio.seek(0)
            # Если это GIF, конвертируем первый кадр в JPEG
            if mime == "image/gif":
                img = Image.open(bio)
                img = img.convert("RGB")
                out = BytesIO()
                img.save(out, format="JPEG")
                out.seek(0)
                return out.read()
            return bio.read()

    return None

def _read_and_remove(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    os.remove(path)
    return data

async def main():
    await ensure_authorized()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
