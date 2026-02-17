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


# ==============================
# STRIKE CLASSIFICATION
# ==============================
def classify_strike(symbol, strike, option_type, future_price):
    atm_width = ATM_RANGE.get(symbol, 0)

    if abs(strike - future_price) <= atm_width:
        return "ATM"

    if option_type == "CE":
        return "ITM" if strike < future_price else "OTM"

    if option_type == "PE":
        return "ITM" if strike > future_price else "OTM"

    return None


# ==============================
# PARSE ALERT
# ==============================
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

    action_map = {
        "CALL WRITER": "CALL_WRITER",
        "PUT WRITER": "PUT_WRITER",
        "CALL BUY": "CALL_BUY",
        "PUT BUY": "PUT_BUY",
        "SHORT COVERING": "SHORT_COVERING",
        "LONG UNWINDING": "LONG_UNWINDING",
        "FUTURE BUY": "FUTURE_BUY",
        "FUTURE SELL": "FUTURE_SELL",
    }

    action_type = None
    for key in action_map:
        if key in text_upper:
            action_type = action_map[key]
            break

    if not action_type:
        return None

    return {
        "symbol": base_symbol,
        "lots": lots,
        "action_type": action_type,
        "zone": zone,
    }


# ==============================
# MESSAGE HANDLER
# ==============================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message

    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        parsed = parse_alert(msg.text)
        if parsed:
            alerts_buffer.append(parsed)


# ==============================
# PROCESS SUMMARY
# ==============================
async def process_summary(context: ContextTypes.DEFAULT_TYPE):
    global alerts_buffer

    if not alerts_buffer:
        return

    current_batch = list(alerts_buffer)
    alerts_buffer.clear()

    data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    for alert in current_batch:
        symbol = alert["symbol"]
        action = alert["action_type"]
        zone = alert["zone"]
        lots = alert["lots"]

        if zone:
            data[symbol][action][zone] += lots
        else:
            data[symbol][action]["TOTAL"] += lots

    message = "<pre>\n"
    message += "📊 5 MIN FLOW WITH STRIKE ZONE\n\n"

    for symbol in TRACK_SYMBOLS:
        if symbol not in data:
            continue

        message += f"{symbol}\n"
        message += "-" * 60 + "\n"
        message += f"{'TYPE':20}{'ITM':>8}{'ATM':>8}{'OTM':>8}{'TOTAL':>10}\n"
        message += "-" * 60 + "\n"

        total_bull = 0
        total_bear = 0

        for action in [
            "CALL_WRITER",
            "PUT_WRITER",
            "CALL_BUY",
            "PUT_BUY",
            "SHORT_COVERING",
            "LONG_UNWINDING"
        ]:
            itm = data[symbol][action]["ITM"]
            atm = data[symbol][action]["ATM"]
            otm = data[symbol][action]["OTM"]
            total = itm + atm + otm

            label = action.replace("_", " ")
            message += f"{label:20}{itm:8}{atm:8}{otm:8}{total:10}\n"

            # Basic classification
            if action in ["PUT_BUY", "CALL_BUY", "SHORT_COVERING"]:
                total_bull += total
            if action in ["CALL_WRITER", "PUT_WRITER", "LONG_UNWINDING"]:
                total_bear += total

        fb = data[symbol]["FUTURE_BUY"]["TOTAL"]
        fs = data[symbol]["FUTURE_SELL"]["TOTAL"]

        total_bull += fb
        total_bear += fs

        message += "-" * 60 + "\n"
        message += f"{'FUTURE BUY':20}{fb:8}\n"
        message += f"{'FUTURE SELL':20}{fs:8}\n"

        net = total_bull - total_bear

        if net > 0:
            bias = "🔥 BULLISH BUILDUP"
        elif net < 0:
            bias = "🔻 BEARISH BUILDUP"
        else:
            bias = "⚖️ NEUTRAL"

        message += "-" * 60 + "\n"
        message += f"Net Flow: {net}\n"
        message += f"Bias: {bias}\n\n\n"

    message += "Validity: Next 5 Minutes Only\n"
    message += "</pre>"

    await context.bot.send_message(
        chat_id=SUMMARY_CHAT_ID,
        text=message,
        parse_mode="HTML"
    )


# ==============================
# MAIN
# ==============================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))

    if app.job_queue:
        app.job_queue.run_repeating(process_summary, interval=300, first=10)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
