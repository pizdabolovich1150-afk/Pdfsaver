import os
import re
import asyncio
import logging
from io import BytesIO
from telethon import TelegramClient
from telethon.sessions import StringSession
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

# Клиент Telethon с строковой сессией
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

async def ensure_authorized():
    """Подключает и проверяет авторизацию Telethon."""
    if not client.is_connected():
        await client.connect()
    if not await client.is_user_authorized():
        raise Exception("Сессия недействительна. Пересоздайте SESSION_STRING.")

def generate_pdf(text: str, image_bytes: bytes = None) -> bytes:
    """Генерирует PDF с текстом и картинкой."""
    pdf = FPDF()
    pdf.add_page()
    # Шрифт с кириллицей (на Koyeb есть DejaVu)
    pdf.add_font("DejaVu", "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", uni=True)
    pdf.set_font("DejaVu", size=12)

    if text:
        for line in text.split("\n"):
            pdf.multi_cell(0, 10, line)
            pdf.ln(2)

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

    return pdf.output()

# ---------- Обработчики команд ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Пришли мне ссылку на пост в формате:\n"
        "`t.me/имя_канала/номер`\n\n"
        "Я пришлю PDF с текстом и картинкой.",
        parse_mode="Markdown"
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text
    match = re.search(r"t\.me/([^/]+)/(\d+)", msg_text)
    if not match:
        await update.message.reply_text("❌ Неверный формат ссылки.")
        return

    channel, msg_id = match.group(1), int(match.group(2))
    await update.message.reply_text("🔍 Получаю пост…")

    try:
        await ensure_authorized()
        message = await client.get_messages(channel, ids=msg_id)
        if not message:
            await update.message.reply_text("❌ Сообщение не найдено.")
            return

        text = message.text or message.caption or ""
        image_bytes = None

        if message.photo:
            path = await message.download_media(file="/tmp/")
            with open(path, "rb") as f:
                image_bytes = f.read()
            os.remove(path)
        elif message.document and "image" in (message.document.mime_type or ""):
            path = await message.download_media(file="/tmp/")
            with open(path, "rb") as f:
                image_bytes = f.read()
            os.remove(path)

        if not text and not image_bytes:
            await update.message.reply_text("❌ В посте нет ни текста, ни изображения.")
            return

        await update.message.reply_text("📄 Создаю PDF…")
        pdf_data = generate_pdf(text, image_bytes)

        await update.message.reply_document(
            document=BytesIO(pdf_data),
            filename=f"post_{channel}_{msg_id}.pdf",
            caption="✅ Готово!"
        )

    except Exception as e:
        logger.exception("Ошибка при обработке поста")
        await update.message.reply_text(f"⚠️ Произошла ошибка: {e}")

async def main():
    await ensure_authorized()

    # Создаём приложение python-telegram-bot
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    # Запускаем без вложенных event loop'ов
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Бесконечное ожидание (бот работает)
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
