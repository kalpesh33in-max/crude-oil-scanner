import os
import re
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_CHAT_ID = os.getenv("SUMMARY_CHAT_ID")

alerts_buffer = []

TRACK_SYMBOLS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK"]

ATM_RANGE = {
    "BANKNIFTY": 100,
    "HDFCBANK": 5,
    "ICICIBANK": 10,
}


# ===================================
# STRIKE CLASSIFICATION
# ===================================
def classify_strike(symbol, strike, option_type, future_price):
    atm_width = ATM_RANGE.get(symbol, 0)

    if abs(strike - future_price) <= atm_width:
        return "ATM"

    if option_type == "CE":
        return "ITM" if strike < future_price else "OTM"

    if option_type == "PE":
        return "ITM" if strike > future_price else "OTM"

    return None


# ===================================
# PARSE ALERT
# ===================================
def parse_alert(text):
    symbol_match = re.search(r"Symbol:\s*([\w-]+)", text)
    lot_match = re.search(r"LOTS:\s*(\d+)", text)
    future_match = re.search(r"FUTURE PRICE:\s*([\d.]+)", text)

    if not (symbol_match and lot_match):
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

    opt_match = re.search(r"(\d+)(CE|PE)", symbol_full)

    strike = None
    option_type = None
    zone = None

    if opt_match:
        strike = int(opt_match.group(1))
        option_type = opt_match.group(2)

    future_price = float(future_match.group(1)) if future_match else None

    if strike and option_type and future_price:
        zone = classify_strike(base_symbol, strike, option_type, future_price)

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
        "zone": zone,
        "option_type": option_type
    }


# ===================================
# MESSAGE HANDLER
# ===================================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message

    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        parsed = parse_alert(msg.text)
        if parsed:
            alerts_buffer.append(parsed)


# ===================================
# SUGGESTION LOGIC
# ===================================
def generate_suggestion(data):
    call_sc = data["CALL_SC"]
    put_sc = data["PUT_SC"]
    call_writer = data["CALL_WRITER"]
    put_writer = data["PUT_WRITER"]
    call_buy = data["CALL_BUY"]
    put_buy = data["PUT_BUY"]

    bullish_score = call_sc + put_writer + call_buy
    bearish_score = put_sc + call_writer + put_buy

    if bullish_score > bearish_score * 1.5:
        return "✅ BUY CALL", "HIGH"
    elif bearish_score > bullish_score * 1.5:
        return "❌ BUY PUT", "HIGH"
    elif bullish_score > bearish_score:
        return "⚡ BUY CALL (Small)", "MEDIUM"
    elif bearish_score > bullish_score:
        return "⚡ BUY PUT (Small)", "MEDIUM"
    else:
        return "🚫 NO TRADE", "LOW"


# ===================================
# PROCESS SUMMARY
# ===================================
async def process_summary(context: ContextTypes.DEFAULT_TYPE):
    global alerts_buffer

    if not alerts_buffer:
        return

    current_batch = list(alerts_buffer)
    alerts_buffer.clear()

    data = defaultdict(lambda: defaultdict(int))

    for alert in current_batch:
        symbol = alert["symbol"]
        action = alert["action_type"]
        option_type = alert.get("option_type")

        lots = alert["lots"]

        if action == "SHORT_COVERING":
            if option_type == "CE":
                data[symbol]["CALL_SC"] += lots
            elif option_type == "PE":
                data[symbol]["PUT_SC"] += lots
        elif action == "LONG_UNWINDING":
            if option_type == "CE":
                data[symbol]["CALL_UNW"] += lots
            elif option_type == "PE":
                data[symbol]["PUT_UNW"] += lots
        else:
            data[symbol][action] += lots

    message = "<pre>\n"
    message += "📊 5 MIN FLOW WITH CE/PE SPLIT\n\n"

    for symbol in TRACK_SYMBOLS:
        if symbol not in data:
            continue

        d = data[symbol]

        message += f"{symbol}\n"
        message += "-" * 55 + "\n"
        message += f"{'TYPE':15}{'TOT':>8}\n"
        message += "-" * 55 + "\n"

        for key in [
            "CALL_WRITER", "PUT_WRITER",
            "CALL_BUY", "PUT_BUY",
            "CALL_SC", "PUT_SC",
            "CALL_UNW", "PUT_UNW"
        ]:
            message += f"{key.replace('_',' '):15}{d[key]:>8}\n"

        message += "-" * 55 + "\n"
        message += f"{'FUT BUY':15}{d['FUTURE_BUY']:>8}\n"
        message += f"{'FUT SELL':15}{d['FUTURE_SELL']:>8}\n"

        suggestion, confidence = generate_suggestion(d)

        message += "\n"
        message += f"🎯 SUGGESTION: {suggestion}\n"
        message += f"Confidence: {confidence}\n"
        message += "-" * 55 + "\n\n"

    message += "Validity: Next 5 Minutes Only\n"
    message += "</pre>"

    await context.bot.send_message(
        chat_id=SUMMARY_CHAT_ID,
        text=message,
        parse_mode="HTML"
    )


# ===================================
# MAIN
# ===================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))

    if app.job_queue:
        app.job_queue.run_repeating(process_summary, interval=300, first=10)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
