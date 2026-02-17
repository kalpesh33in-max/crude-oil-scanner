import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")

async def debug_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    print("FULL UPDATE RECEIVED:")
    print(update)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.ALL, debug_handler))

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == "__main__":
    main()
