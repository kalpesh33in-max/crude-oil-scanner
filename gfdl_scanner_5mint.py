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
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ==============================
# ENV VARIABLES
# ==============================

BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_CHAT_ID = os.getenv("SUMMARY_CHAT_ID")

alerts_buffer = []

# Track these base symbols
TRACK_SYMBOLS = ["BANKNIFTY", "NIFTY", "FINNIFTY"]

# ==============================
# PARSE ALERT
# ==============================

def parse_alert(text):

    if not text:
        return None

    text_upper = text.upper()

    # Extract symbol
    symbol_match = re.search(r"SYMBOL:\s*([A-Z0-9]+)", text_upper)
    if not symbol_match:
        return None

    full_symbol = symbol_match.group(1)

    # Extract lots
    lot_match = re.search(r"LOTS:\s*(\d+)", text_upper)
    if not lot_match:
        return None

    lots = int(lot_match.group(1))

    # Detect base symbol
    base_symbol = None
    for s in TRACK_SYMBOLS:
        if s in full_symbol:
            base_symbol = s
            break

    if not base_symbol:
        return None

    # Detect action type
    if "CALL WRITER" in text_upper:
        action = "CALL_WRITER"
    elif "PUT WRITER" in text_upper:
        action = "PUT_WRITER"
    elif "CALL BUY" in text_upper:
        action = "CALL_BUY"
    elif "PUT BUY" in text_upper:
        action = "PUT_BUY"
    elif "SHORT COVERING" in text_upper:
        action = "SHORT_COVERING"
    elif "LONG UNWINDING" in text_upper:
        action = "LONG_UNWINDING"
    elif "FUTURE BUY" in text_upper:
        action = "FUTURE_BUY"
    elif "FUTURE SELL" in text_upper:
        action = "FUTURE_SELL"
    else:
        return None

    logging.info(f"Parsed: {base_symbol} | {action} | {lots}")

    return {
        "symbol": base_symbol,
        "action": action,
        "lots": lots
    }


# ==============================
# MESSAGE HANDLER
# ==============================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.channel_post  # IMPORTANT: Only channel posts

    if not msg:
        return

    if str(msg.chat.id) != str(TARGET_CHANNEL_ID):
        return

    logging.info("Message received from target channel")

    parsed = parse_alert(msg.text)

    if parsed:
        alerts_buffer.append(parsed)
        logging.info("Alert added to buffer")
    else:
        logging.info("Message not parsed")


# ==============================
# SUMMARY PROCESSOR
# ==============================

async def process_summary(context: ContextTypes.DEFAULT_TYPE):

    global alerts_buffer

    if not alerts_buffer:
        return

    data = defaultdict(lambda: defaultdict(int))

    for alert in alerts_buffer:
        data[alert["symbol"]][alert["action"]] += alert["lots"]

    alerts_buffer.clear()

    message = "📊 5 MIN FLOW SUMMARY\n\n"

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

        message += f"CALL BUY        : {cb}\n"
        message += f"PUT BUY         : {pb}\n"
        message += f"CALL WRITER     : {cw}\n"
        message += f"PUT WRITER      : {pw}\n"
        message += f"FUTURE BUY      : {fb}\n"
        message += f"FUTURE SELL     : {fs}\n"
        message += "---------------------------\n\n"

        bull = pb + cb + sc + fb
        bear = cw + lu + fs

        total_bull += bull
        total_bear += bear

    net = total_bull - total_bear

    message += "📈 NET VIEW\n"
    message += f"Bullish Lots : {total_bull}\n"
    message += f"Bearish Lots : {total_bear}\n"
    message += f"Net          : {net}\n\n"

    if net > 0:
        bias = "🔥 Bullish Build-up"
    elif net < 0:
        bias = "🔻 Bearish Build-up"
    else:
        bias = "⚖ Neutral"

    message += f"Bias: {bias}\n"
    message += "⏳ Validity: Next 5 Minutes"

    await context.bot.send_message(
        chat_id=SUMMARY_CHAT_ID,
        text=message
    )


# ==============================
# MAIN
# ==============================

def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.ALL, message_handler)
    )

    app.job_queue.run_repeating(
        process_summary,
        interval=300,
        first=60
    )

    logging.info("Starting bot...")
    app.run_polling()


if __name__ == "__main__":
    main()
