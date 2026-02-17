import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# =========================
# LOGGING
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")

# =========================
# DEBUG HANDLER
# =========================
async def debug_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("============== UPDATE RECEIVED ==============")
    print(update.to_dict())
    print("=============================================")

# =========================
# MAIN
# =========================
def main():

    if not BOT_TOKEN:
        print("BOT TOKEN NOT FOUND")
        return

    print("Starting bot...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.ALL, debug_handler))

    print("Polling started...")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
