import os
import re
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ===============================
# LOGGING
# ===============================
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

# ===============================
# STRIKE CLASSIFICATION
# ===============================
def classify_strike(symbol, strike, option_type, future_price):
    width = ATM_RANGE.get(symbol, 0)

    # Merge ATM into OTM
    if abs(strike - future_price) <= width:
        return "OTM"

    if option_type == "CE":
        return "ITM" if strike < (future_price - width) else "OTM"

    if option_type == "PE":
        return "ITM" if strike > (future_price + width) else "OTM"

    return None


# ===============================
# PARSE ALERT
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

    base_symbol = next((s for s in TRACK_SYMBOLS if s in symbol_full), None)
    if not base_symbol:
        return None

    opt_match = re.search(r"(\d+)(CE|PE)", symbol_full)
    strike = int(opt_match.group(1)) if opt_match else None
    option_type = opt_match.group(2) if opt_match else None

    zone = None
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
    elif "SHORT COVERING" in text_upper and opt_match:
        action_type = "CALL_SC" if option_type == "CE" else "PUT_SC"
    elif "LONG UNWINDING" in text_upper and opt_match:
        action_type = "CALL_UNW" if option_type == "CE" else "PUT_UNW"
    elif "FUTURE BUY" in text_upper:
        action_type = "FUTURE_BUY"
    elif "FUTURE SELL" in text_upper:
        action_type = "FUTURE_SELL"
    elif "FUTURE SHORT COVERING" in text_upper:
        action_type = "FUTURE_SC"
    elif "FUTURE LONG UNWINDING" in text_upper:
        action_type = "FUTURE_UNW"
    else:
        return None

    return {
        "symbol": base_symbol,
        "lots": lots,
        "action_type": action_type,
        "zone": zone,
        "future": future_price
    }


# ===============================
# TELEGRAM HANDLER
# ===============================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message
    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        parsed = parse_alert(msg.text)
        if parsed:
            alerts_buffer.append(parsed)


# ===============================
# PROCESS SUMMARY
# ===============================
async def process_summary(context: ContextTypes.DEFAULT_TYPE):
    global alerts_buffer

    if not alerts_buffer:
        return

    batch = list(alerts_buffer)
    alerts_buffer.clear()

    data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    futures_data = defaultdict(lambda: defaultdict(int))
    last_future_price = {}

    total_bull = 0
    total_bear = 0

    for alert in batch:
        sym = alert["symbol"]
        act = alert["action_type"]
        lots = alert["lots"]

        if alert["future"]:
            last_future_price[sym] = alert["future"]

        if alert["zone"]:
            data[sym][act][alert["zone"]] += lots
        else:
            futures_data[sym][act] += lots

    message = "<pre>\n📊 2 MIN FLOW REPORT\n\n"

    for symbol in TRACK_SYMBOLS:
        if symbol not in data and symbol not in futures_data:
            continue

        f_price = last_future_price.get(symbol, "N/A")

        message += f"{symbol} (FUT: {f_price})\n"
        message += "-" * 55 + "\n"
        message += f"{'TYPE':15}{'ITM':>8}{'OTM':>8}{'TOT':>8}\n"
        message += "-" * 55 + "\n"

        actions = [
            "CALL_WRITER","PUT_WRITER",
            "CALL_BUY","PUT_BUY",
            "CALL_SC","PUT_SC",
            "CALL_UNW","PUT_UNW"
        ]

        for action in actions:
            itm = data[symbol][action]["ITM"]
            otm = data[symbol][action]["OTM"]
            total = itm + otm
            label = action.replace("_"," ")
            message += f"{label:15}{itm:8}{otm:8}{total:8}\n"

        message += "-" * 55 + "\n"

        fb = futures_data[symbol]["FUTURE_BUY"]
        fs = futures_data[symbol]["FUTURE_SELL"]
        fsc = futures_data[symbol]["FUTURE_SC"]
        funw = futures_data[symbol]["FUTURE_UNW"]

        message += f"{'FUT BUY':15}{fb:8}\n"
        message += f"{'FUT SELL':15}{fs:8}\n"
        message += f"{'FUT SC':15}{fsc:8}\n"
        message += f"{'FUT UNW':15}{funw:8}\n\n"

        # Bullish Calculation
        bull = (
            data[symbol]["PUT_WRITER"]["ITM"] + data[symbol]["PUT_WRITER"]["OTM"] +
            data[symbol]["CALL_BUY"]["ITM"] + data[symbol]["CALL_BUY"]["OTM"] +
            data[symbol]["CALL_SC"]["ITM"] + data[symbol]["CALL_SC"]["OTM"] +
            data[symbol]["PUT_UNW"]["ITM"] + data[symbol]["PUT_UNW"]["OTM"] +
            fb + fsc
        )

        # Bearish Calculation
        bear = (
            data[symbol]["CALL_WRITER"]["ITM"] + data[symbol]["CALL_WRITER"]["OTM"] +
            data[symbol]["PUT_BUY"]["ITM"] + data[symbol]["PUT_BUY"]["OTM"] +
            data[symbol]["PUT_SC"]["ITM"] + data[symbol]["PUT_SC"]["OTM"] +
            data[symbol]["CALL_UNW"]["ITM"] + data[symbol]["CALL_UNW"]["OTM"] +
            fs + funw
        )

        total_bull += bull
        total_bear += bear

    net = total_bull - total_bear

    if net > 100:
        strength = "🔥 STRONG BULLISH"
    elif net > 0:
        strength = "🟢 Mild Bullish"
    elif net < -100:
        strength = "🔥 STRONG BEARISH"
    elif net < 0:
        strength = "🔴 Mild Bearish"
    else:
        strength = "⚖️ Balanced"

    message += "=" * 55 + "\n"
    message += "📈 NET DIRECTIONAL FLOW (All Symbols Combined)\n"
    message += "=" * 55 + "\n\n"
    message += f"Total Bullish Lots : {total_bull}\n"
    message += f"Total Bearish Lots : {total_bear}\n"
    message += f"Net Flow           : {net:+} Lots\n\n"
    message += f"Bias               : {strength}\n\n"
    message += "Validity: Next 2 Minutes Only\n"
    message += "</pre>"

    await context.bot.send_message(
        chat_id=SUMMARY_CHAT_ID,
        text=message,
        parse_mode="HTML"
    )


# ===============================
# MAIN
# ===============================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))

    if app.job_queue:
        app.job_queue.run_repeating(process_summary, interval=120, first=10)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
