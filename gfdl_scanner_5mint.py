import os
import re
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_CHAT_ID = os.getenv("SUMMARY_CHAT_ID")

alerts_buffer = []

TRACK_SYMBOLS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK"]

# =========================
# PARSE ALERT
# =========================
def parse_alert(text):

    logging.info(f"Parsing text:\n{text}")

    symbol_match = re.search(r"Symbol\s*:\s*([A-Za-z0-9]+)", text, re.IGNORECASE)
    lot_match = re.search(r"LOTS\s*:\s*(\d+)", text, re.IGNORECASE)

    if not symbol_match or not lot_match:
        logging.info("Parsing failed - Symbol or LOTS not found")
        return None

    symbol_full = symbol_match.group(1).upper()
    lots = int(lot_match.group(1))

    base_symbol = None
    for s in TRACK_SYMBOLS:
        if s in symbol_full:
            base_symbol = s
            break

    if not base_symbol:
        logging.info("Parsing failed - Base symbol not matched")
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
        logging.info("Parsing failed - Action not detected")
        return None

    logging.info(f"Parsed Successfully: {base_symbol} | {action_type} | {lots}")

    return {
        "symbol": base_symbol,
        "lots": lots,
        "action_type": action_type,
    }


# =========================
# MESSAGE HANDLER
# =========================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.channel_post or update.message

    if not msg or not msg.text:
        return

    logging.info(f"Message received from chat id: {msg.chat.id}")

    if str(msg.chat.id) != str(TARGET_CHANNEL_ID):
        logging.info("Message ignored - Not target channel")
        return

    parsed = parse_alert(msg.text)

    if parsed:
        alerts_buffer.append(parsed)
        logging.info("Alert added to buffer")
    else:
        logging.info("Message not parsed")


# =========================
# PROCESS SUMMARY
# =========================
async def process_summary(context: ContextTypes.DEFAULT_TYPE):

    global alerts_buffer

    if not alerts_buffer:
        logging.info("No alerts in buffer")
        return

    current_batch = list(alerts_buffer)
    alerts_buffer.clear()

    data = defaultdict(lambda: defaultdict(int))

    for alert in current_batch:
        data[alert["symbol"]][alert["action_type"]] += alert["lots"]

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

        message += f"CALL WRITER : {cw}\n"
        message += f"PUT WRITER  : {pw}\n"
        message += f"CALL BUY    : {cb}\n"
        message += f"PUT BUY     : {pb}\n"
        message += f"SHORT COV   : {sc}\n"
        message += f"LONG UNWIND : {lu}\n"
        message += f"FUTURE BUY  : {fb}\n"
        message += f"FUTURE SELL : {fs}\n"
        message += "-----------------------------\n\n"

        bull = pw + cb + sc + fb
        bear = cw + pb + lu + fs

        total_bull += bull
        total_bear += bear

    net = total_bull - total_bear

    message += f"📈 Bullish Activity : {total_bull}\n"
    message += f"📉 Bearish Activity : {total_bear}\n"
    message += f"⚖ Net Dominance    : {net}\n\n"

    if net > 0:
        message += "🔥 Bullish Build-up"
    elif net < 0:
        message += "🔻 Bearish Build-up"
    else:
        message += "⚪ Neutral"

    logging.info("Sending summary message")

    await context.bot.send_message(
        chat_id=SUMMARY_CHAT_ID,
        text=message
    )


# =========================
# MAIN
# =========================
def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT, message_handler))

    app.job_queue.run_repeating(process_summary, interval=300, first=10)

    logging.info("Starting bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
