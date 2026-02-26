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

# ===============================
# ✅ REAL OPTION ITM LOGIC
# ===============================
def classify_strike(strike, option_type, future_price):
    """
    CALL (CE):
        ITM -> Strike < Future
        OTM -> Strike > Future

    PUT (PE):
        ITM -> Strike > Future
        OTM -> Strike < Future
    """
    try:
        strike = float(strike)
        future_price = float(future_price)
    except:
        return None

    if option_type == "CE":
        return "ITM" if strike < future_price else "OTM"

    if option_type == "PE":
        return "ITM" if strike > future_price else "OTM"

    return None


# ===============================
# PARSE ALERT
# ===============================
def parse_alert(text):

    text_upper = text.upper()

    symbol_match = re.search(r"SYMBOL:\s*([\w-]+)", text_upper)
    lot_match = re.search(r"LOTS:\s*(\d+)", text_upper)
    future_match = re.search(r"FUTURE\s+PRICE:\s*([\d.]+)", text_upper)

    if not (symbol_match and lot_match):
        return None

    symbol_full = symbol_match.group(1)
    lots = int(lot_match.group(1))
    future_price = float(future_match.group(1)) if future_match else None

    base_symbol = next((s for s in TRACK_SYMBOLS if s in symbol_full), None)
    if not base_symbol:
        return None

    opt_match = re.search(r"(\d+)(CE|PE)", symbol_full)

    zone = None
    option_type = None

    if opt_match and future_price:
        strike = opt_match.group(1)
        option_type = opt_match.group(2)
        zone = classify_strike(strike, option_type, future_price)

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
        if option_type == "CE":
            action_type = "CALL_SC"
        elif option_type == "PE":
            action_type = "PUT_SC"
        else:
            action_type = "FUTURE_SC"
    elif "LONG UNWINDING" in text_upper:
        if option_type == "CE":
            action_type = "CALL_UNW"
        elif option_type == "PE":
            action_type = "PUT_UNW"
        else:
            action_type = "FUTURE_UNW"
    elif "FUTURE BUY" in text_upper:
        action_type = "FUTURE_BUY"
    elif "FUTURE SELL" in text_upper:
        action_type = "FUTURE_SELL"

    if not action_type:
        return None

    return {
        "symbol": base_symbol,
        "lots": lots,
        "zone": zone,
        "action_type": action_type,
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
# SUMMARY PROCESS
# ===============================
async def process_summary(context: ContextTypes.DEFAULT_TYPE):

    global alerts_buffer

    if not alerts_buffer:
        return

    batch = list(alerts_buffer)
    alerts_buffer.clear()

    data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    futures_data = defaultdict(lambda: defaultdict(int))
    last_future = {}

    total_bull = 0
    total_bear = 0

    for alert in batch:

        sym = alert["symbol"]
        act = alert["action_type"]
        zone = alert["zone"]
        lots = alert["lots"]

        if alert["future"]:
            last_future[sym] = alert["future"]

        if zone:
            data[sym][act][zone] += lots
        else:
            futures_data[sym][act] += lots

    message = "<pre>\n📊 2 MIN LOT FLOW REPORT\n\n"

    for symbol in TRACK_SYMBOLS:

        if symbol not in data and symbol not in futures_data:
            continue

        message += f"{symbol} (FUT: {last_future.get(symbol,'N/A')})\n"
        message += "-" * 50 + "\n"
        message += f"{'TYPE':12}{'ITM':>6}{'OTM':>6}{'TOT':>6}\n"
        message += "-" * 50 + "\n"

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
            message += f"{action.replace('_',' '):12}{itm:6}{otm:6}{total:6}\n"

        message += "-" * 50 + "\n"

        fb = futures_data[symbol]["FUTURE_BUY"]
        fs = futures_data[symbol]["FUTURE_SELL"]
        fsc = futures_data[symbol]["FUTURE_SC"]
        funw = futures_data[symbol]["FUTURE_UNW"]

        message += f"{'FUT BUY':12}{fb:6}\n"
        message += f"{'FUT SELL':12}{fs:6}\n"
        message += f"{'FUT SC':12}{fsc:6}\n"
        message += f"{'FUT UNW':12}{funw:6}\n\n"

        bull = (
            data[symbol]["PUT_WRITER"]["ITM"] + data[symbol]["PUT_WRITER"]["OTM"] +
            data[symbol]["CALL_BUY"]["ITM"] + data[symbol]["CALL_BUY"]["OTM"] +
            data[symbol]["CALL_SC"]["ITM"] + data[symbol]["CALL_SC"]["OTM"] +
            data[symbol]["PUT_UNW"]["ITM"] + data[symbol]["PUT_UNW"]["OTM"] +
            fb + fsc
        )

        bear = (
            data[symbol]["CALL_WRITER"]["ITM"] + data[symbol]["CALL_WRITER"]["OTM"] +
            data[symbol]["PUT_BUY"]["ITM"] + data[symbol]["PUT_BUY"]["OTM"] +
            data[symbol]["PUT_SC"]["ITM"] + data[symbol]["PUT_SC"]["OTM"] +
            data[symbol]["CALL_UNW"]["ITM"] + data[symbol]["CALL_UNW"]["OTM"] +
            fs + funw
        )

        total_bull += bull
        total_bear += bear

    net_lots = total_bull - total_bear
    total_flow = total_bull + total_bear
    dominance = (total_bull / total_flow * 100) if total_flow > 0 else 0

    if net_lots > 500:
        strength = "🔥 VERY STRONG BULLISH"
    elif net_lots > 200:
        strength = "🚀 STRONG BULLISH"
    elif net_lots > 0:
        strength = "🟢 Mild Bullish"
    elif net_lots < -500:
        strength = "🔥 VERY STRONG BEARISH"
    elif net_lots < -200:
        strength = "📉 STRONG BEARISH"
    elif net_lots < 0:
        strength = "🔴 Mild Bearish"
    else:
        strength = "⚖️ Balanced"

    message += "=" * 50 + "\n"
    message += "📈 NET DIRECTIONAL LOT FLOW\n"
    message += "=" * 50 + "\n"
    message += f"Bullish Lots : {total_bull}\n"
    message += f"Bearish Lots : {total_bear}\n"
    message += f"Net Lot Flow : {net_lots}\n"
    message += f"Bullish %    : {dominance:.1f}%\n\n"
    message += f"Bias         : {strength}\n"
    message += "Validity     : Next 2 Minutes Only\n"
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
