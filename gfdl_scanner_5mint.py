import os
import re
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Environment Variables
BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_CHAT_ID = os.getenv("SUMMARY_CHAT_ID")

alerts_buffer = []

# ===============================
# CONFIGURATION: STRIKE DIFFERENCES
# ===============================
TRACK_SYMBOLS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK"]

ATM_RANGE = {
    "BANKNIFTY": 100,
    "HDFCBANK": 5,
    "ICICIBANK": 10,
}

# ===============================
# PRECISION STRIKE CLASSIFICATION
# ===============================
def classify_strike(symbol, strike, option_type, future_price):
    width = ATM_RANGE.get(symbol, 0)
    if abs(strike - future_price) <= width:
        return "ATM"
    if option_type == "CE":
        return "ITM" if strike < (future_price - width) else "OTM"
    if option_type == "PE":
        return "ITM" if strike > (future_price + width) else "OTM"
    return None

# ===============================
# PARSE ALERT (Detailed Format)
# ===============================
def parse_alert(text):
    symbol_match = re.search(r"Symbol:\s*([\w-]+)", text)
    lot_match = re.search(r"LOTS:\s*(\d+)", text)
    future_match = re.search(r"FUTURE PRICE:\s*([\d.]+)", text)

    if not (symbol_match and lot_match):
        return None

    symbol_full = symbol_match.group(1).upper()
    lots = int(lot_match.group(1))
    future_price = float(future_match.group(1)) if future_match else None

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

    if strike and option_type and future_price:
        zone = classify_strike(base_symbol, strike, option_type, future_price)

    text_upper = text.upper()
    action_type = None

    # Logic for Options
    if "CALL WRITER" in text_upper: action_type = "CALL_WRITER"
    elif "PUT WRITER" in text_upper: action_type = "PUT_WRITER"
    elif "CALL BUY" in text_upper: action_type = "CALL_BUY"
    elif "PUT BUY" in text_upper: action_type = "PUT_BUY"
    elif "SHORT COVERING" in text_upper and (opt_match):
        action_type = "CALL_SC" if option_type == "CE" else "PUT_SC"
    elif "LONG UNWINDING" in text_upper and (opt_match):
        action_type = "CALL_UNW" if option_type == "CE" else "PUT_UNW"
    
    # Logic for Futures (Added SC/UNW)
    elif "FUTURE BUY" in text_upper: action_type = "FUTURE_BUY"
    elif "FUTURE SELL" in text_upper: action_type = "FUTURE_SELL"
    elif "FUTURE SHORT COVERING" in text_upper or ("SHORT COVERING" in text_upper and not opt_match):
        action_type = "FUTURE_SC"
    elif "FUTURE LONG UNWINDING" in text_upper or ("LONG UNWINDING" in text_upper and not opt_match):
        action_type = "FUTURE_UNW"
    else: return None

    return {
        "symbol": base_symbol,
        "lots": lots,
        "action_type": action_type,
        "zone": zone,
        "current_future": future_price
    }

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message
    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        parsed = parse_alert(msg.text)
        if parsed:
            alerts_buffer.append(parsed)

async def process_summary(context: ContextTypes.DEFAULT_TYPE):
    global alerts_buffer
    if not alerts_buffer: return

    current_batch = list(alerts_buffer)
    alerts_buffer.clear()

    data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    last_known_future = {}

    for alert in current_batch:
        sym = alert["symbol"]
        act = alert["action_type"]
        z = alert["zone"]
        l = alert["lots"]
        if alert["current_future"]:
            last_known_future[sym] = alert["current_future"]

        if z:
            data[sym][act][z] += l
        else:
            data[sym][act]["TOTAL"] += l

    message = "<pre>\n📊 2 MIN FLOW REPORT\n\n"
    for symbol in TRACK_SYMBOLS:
        if symbol not in data: continue

        f_price = last_known_future.get(symbol, "N/A")
        message += f"{symbol} (FUT: {f_price})\n"
        message += "-" * 55 + "\n"
        message += f"{'TYPE':15}{'ITM':>6}{'ATM':>6}{'OTM':>6}{'TOT':>6}\n"
        message += "-" * 55 + "\n"

        actions = ["CALL_WRITER", "PUT_WRITER", "CALL_BUY", "PUT_BUY", "CALL_SC", "PUT_SC", "CALL_UNW", "PUT_UNW"]
        for action in actions:
            itm, atm, otm = data[symbol][action]["ITM"], data[symbol][action]["ATM"], data[symbol][action]["OTM"]
            total = itm + atm + otm
            label = action.replace("_", " ")
            message += f"{label:15}{itm:6}{atm:6}{otm:6}{total:6}\n"

        fb = data[symbol]["FUTURE_BUY"]["TOTAL"]
        fs = data[symbol]["FUTURE_SELL"]["TOTAL"]
        fsc = data[symbol]["FUTURE_SC"]["TOTAL"]
        funw = data[symbol]["FUTURE_UNW"]["TOTAL"]

        message += "-" * 55 + "\n"
        message += f"{'FUT BUY':15}{fb:6}\n"
        message += f"{'FUT SELL':15}{fs:6}\n"
        message += f"{'FUT SC':15}{fsc:6}\n"
        message += f"{'FUT UNW':15}{funw:6}\n\n"

    message += "Validity: Next 2 Minutes Only\n</pre>"
    await context.bot.send_message(chat_id=SUMMARY_CHAT_ID, text=message, parse_mode="HTML")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    if app.job_queue:
        app.job_queue.run_repeating(process_summary, interval=120, first=10)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
