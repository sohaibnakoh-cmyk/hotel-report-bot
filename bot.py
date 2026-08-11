import os
import logging
from telegram.ext import Application

# إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    TOKEN = os.environ.get("BOT_TOKEN")
    PORT = int(os.environ.get("PORT", 10000))  # Render يزودنا برقم بورت تلقائياً
    RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") # رابط موقعك على Render

    if not TOKEN or not RENDER_EXTERNAL_URL:
        logger.error("❌ BOT_TOKEN أو RENDER_EXTERNAL_URL مفقود في متغيرات البيئة!")
        return

    # إنشاء تطبيق تيليجرام
    application = Application.builder().token(TOKEN).build()

    # ====== أضف الهاندلرز (Handlers) الخاصة بك هنا ======
    # مثال: application.add_handler(CommandHandler("start", start))
    # ====================================================

    logger.info("🚀 جاري بدء تشغيل البوت عبر Webhook المدمج...")

    # تشغيل الـ Webhook مباشرة من المكتبة (تدير الـ Event Loop تلقائياً)
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_EXTERNAL_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
