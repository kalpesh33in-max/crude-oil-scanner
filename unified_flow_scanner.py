import os
import re
import logging
from collections import defaultdict
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
BOT_TOKEN_2 = os.getenv("SUMMARIZER_BOT_TOKEN_2")

TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_2MIN_CHAT_ID = os.getenv("SUMMARY_2MIN_CHAT_ID")
SUMMARY_5MIN_CHAT_ID = os.getenv("SUMMARY_5MIN_CHAT_ID")

bot2 = Bot(token=BOT_TOKEN_2) if BOT_TOKEN_2 else None

buffer_2min = []
buffer_5min = []

TRACK_SYMBOLS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN"]

LOT_SIZES = {
    "BANKNIFTY": 30,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "AXISBANK": 625,
    "SBIN": 750
}

# ===============================
# UTIL
# ===============================
def format_money(value):
    if value >= 1e7:
        return f"{value/1e7:.2f}Cr"
    elif value >= 1e5:
        return f"{value/1e5:.2f}L"
    return f"{value:.0f}"

def format_future_name(act):
    return act.replace("FUTURE_BUY","FUT_BUY") \
              .replace("FUTURE_SELL","FUT_SELL") \
              .replace("FUTURE_SC","FUT_SC") \
              .replace("FUTURE_UNW","FUT_UNW")

def get_bias_label(net):
    if net > 500: return "🔥 VERY STRONG BULLISH"
    elif net > 150: return "🚀 STRONG BULLISH"
    elif net > 0: return "🟢 Mild Bullish"
    elif net < -500: return "🔥 VERY STRONG BEARISH"
    elif net < -150: return "📉 STRONG BEARISH"
    elif net < 0: return "🔴 Mild Bearish"
    return "⚖ Neutral"

# ===============================
# PARSER
# ===============================
def parse_alert(text):
    text = text.upper()

    symbol = re.search(r"SYMBOL:\s*([\w-]+)", text)
    lots = re.search(r"LOTS:\s*(\d+)", text)
    price = re.search(r"PRICE:\s*([\d.]+)", text)
    future = re.search(r"FUTURE\s+PRICE:\s*([\d.]+)", text)

    if not (symbol and lots):
        return None

    symbol_full = symbol.group(1)
    lots = int(lots.group(1))
    price = float(price.group(1)) if price else None
    future = float(future.group(1)) if future else None

    base = next((s for s in TRACK_SYMBOLS if s in symbol_full), None)
    if not base:
        return None

    action = None

    if "WRITER" in text:
        action = "CALL_WRITER" if "CE" in symbol_full else "PUT_WRITER"
    elif "CALL BUY" in text:
        action = "CALL_BUY"
    elif "PUT BUY" in text:
        action = "PUT_BUY"
    elif "SHORT COVERING" in text:
        action = "FUTURE_SC" if "FUTURE" in text else ("CALL_SC" if "CE" in symbol_full else "PUT_SC")
    elif "LONG UNWINDING" in text:
        action = "FUTURE_UNW" if "FUTURE" in text else ("CALL_UNW" if "CE" in symbol_full else "PUT_UNW")
    elif "FUTURE BUY" in text:
        action = "FUTURE_BUY"
    elif "FUTURE SELL" in text:
        action = "FUTURE_SELL"

    return {
        "symbol": base,
        "lots": lots,
        "action": action,
        "price": price,
        "future": future
    }

# ===============================
# HANDLER
# ===============================
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message

    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        parsed = parse_alert(msg.text)
        if parsed:
            buffer_2min.append(parsed)
            buffer_5min.append(parsed)

# ===============================
# 2 MIN
# ===============================
async def process_2min(context):
    global buffer_2min
    if not buffer_2min:
        return

    batch = buffer_2min.copy()
    buffer_2min.clear()

    message = "<pre>\n📊 2 MIN FLOW\n\n"

    for b in batch:
        message += f"{b['symbol']} {b['action']} {b['lots']}\n"

    message += "</pre>"

    await context.bot.send_message(chat_id=SUMMARY_2MIN_CHAT_ID, text=message, parse_mode="HTML")

# ===============================
# 5 MIN
# ===============================
async def process_5min(context):
    global buffer_5min
    if not buffer_5min:
        return

    batch = buffer_5min.copy()
    buffer_5min.clear()

    message = "<pre>\n📊 5 MIN FLOW\n\n"

    for b in batch:
        message += f"{b['symbol']} {b['action']} {b['lots']}\n"

    message += "</pre>"

    target_bot = bot2 if bot2 else context.bot

    await target_bot.send_message(chat_id=SUMMARY_5MIN_CHAT_ID, text=message, parse_mode="HTML")

# ===============================
# MAIN
# ===============================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT, handler))

    app.job_queue.run_repeating(process_2min, interval=60, first=10)
    app.job_queue.run_repeating(process_5min, interval=60, first=20)

    app.run_polling()

if __name__ == "__main__":
    main()
