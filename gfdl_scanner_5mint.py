import os
import re
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ==============================
# LOGGING
# ==============================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# ==============================
# ENV VARIABLES
# ==============================

BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
TARGET_CHANNEL_ID = str(os.getenv("TARGET_CHANNEL_ID"))
SUMMARY_CHAT_ID = str(os.getenv("SUMMARY_CHAT_ID"))

alerts_buffer = []

TRACK_SYMBOLS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK"]

# ==============================
# PARSE ALERT
# ==============================

def parse_alert(text):
    symbol_match = re.search(r"Symbol:\s*([\w\d]+)", text, re.IGNORECASE)
    lot_match = re.search(r"LOTS:\s*(\d+)", text, re.IGNORECASE)

    if not symbol_match or not lot_match:
        return None

    symbol_full = symbol_match.group(1).upper()
    lots = int(lot_match.group(1))

    base_symbol = None
    for s in TRACK_SYMBOLS:
        if s in symbol_full:
            base_symbol = s
            break

    if not base_symbol:
        return None

    text_upper = text.upper()

    action_type = None
    if "CALL WRITER" in text_upper:
        action_type = "CALL_WRITER"
    elif "PUT WRITER" in text_upper:
        action_type = "PUT_WRITER"
    elif "CALL BUY" in text_upper:
        action_type = "CALL_BUY"
    elif "PUT BUY" in text_upper:
        action_type = "PUT_BUY"
    elif "SHORT COVERING" in text_upper:
        action_type = "SHORT_COVERING"
    elif "LONG UNWINDING" in text_upper:
        action_type = "LONG_UNWINDING"
    elif "FUTURE BUY" in text_upper:
        action_type = "FUTURE_BUY"
    elif "FUTURE SELL" in text_upper:
        action_type = "FUTURE_SELL"
    else:
        return None

    return {
        "symbol": base_symbol,
        "lots": lots,
        "action_type": action_type,
    }

# ==============================
# MESSAGE HANDLER
# ==============================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.channel_post or update.message

    if not msg:
        return

    chat_id = str(msg.chat.id)
    logger.info(f"Message received from chat id: {chat_id}")

    if chat_id != TARGET_CHANNEL_ID:
        logger.info("Message ignored - Not target channel")
        return

    if not msg.text:
        return

    parsed = parse_alert(msg.text)

    if parsed:
        alerts_buffer.append(parsed)
        logger.info(f"Alert added: {parsed}")
    else:
        logger.info("Message matched channel but not valid alert format")

# ==============================
# PROCESS SUMMARY
# ==============================

async def process_summary(context: ContextTypes.DEFAULT_TYPE):
    global alerts_buffer

    if not alerts_buffer:
        logger.info("No alerts in buffer")
        return

    current_batch = list(alerts_buffer)
    alerts_buffer.clear()

    data = defaultdict(lambda: defaultdict(int))

    for alert in current_batch:
        symbol = alert["symbol"]
        action = alert["action_type"]
        lots = alert["lots"]
        data[symbol][action] += lots

    message = "📊 5 MIN FLOW BREAKDOWN\n\n"

    total_bull = 0
    total_bear = 0

    for symbol in TRACK_SYMBOLS:
        if symbol not in data:
            continue

        message += f"🔹 {symbol}\n\n"

        cw = data[symbol]["CALL_WRITER"]
        pw = data[symbol]["PUT_WRITER"]
        cb = data[symbol]["CALL_BUY"]
        pb = data[symbol]["PUT_BUY"]
        sc = data[symbol]["SHORT_COVERING"]
        lu = data[symbol]["LONG_UNWINDING"]
        fb = data[symbol]["FUTURE_BUY"]
        fs = data[symbol]["FUTURE_SELL"]

        message += f"CALL WRITER : {cw} Lots\n"
        message += f"PUT WRITER  : {pw} Lots\n"
        message += f"CALL BUY    : {cb} Lots\n"
        message += f"PUT BUY     : {pb} Lots\n"
        message += f"SHORT COVER : {sc} Lots\n"
        message += f"LONG UNWIND : {lu} Lots\n"
        message += f"FUTURE BUY  : {fb} Lots\n"
        message += f"FUTURE SELL : {fs} Lots\n"
        message += "\n-----------------------------\n\n"

        bull = pw + cb + sc + fb
        bear = cw + pb + lu + fs

        total_bull += bull
        total_bear += bear

    net = total_bull - total_bear

    message += "📈 NET VIEW\n\n"
    message += f"Bullish Activity : {total_bull} Lots\n"
    message += f"Bearish Activity : {total_bear} Lots\n"
    message += f"Net Dominance    : {net}\n\n"

    if net > 0:
        bias = "🔥 Bullish Build-up"
    elif net < 0:
        bias = "🔻 Bearish Build-up"
    else:
        bias = "⚖ Neutral"

    message += f"Bias: {bias}\n"
    message += "⏳ Validity: Next 5 Minutes"

    await context.bot.send_message(chat_id=SUMMARY_CHAT_ID, text=message)

# ==============================
# MAIN
# ==============================

def main():
    logger.info("Starting bot...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.ALL,
            message_handler
        )
    )

    app.job_queue.run_repeating(process_summary, interval=300, first=10)

    logger.info("Polling started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
