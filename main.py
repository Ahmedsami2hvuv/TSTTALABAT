import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# إعدادات logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# التوكن من المتغيرات
TOKEN = os.getenv("TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت شغال! هلا بالغالي")

def main():
    if not TOKEN:
        logger.error("❌ TOKEN not found!")
        return
    
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    
    logger.info("🤖 Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
