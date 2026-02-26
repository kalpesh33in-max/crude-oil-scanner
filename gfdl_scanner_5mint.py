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

LOT_SIZES = {
    "BANKNIFTY": 15,
    "HDFCBANK": 550,
    "ICICIBANK": 1375
}

# ===============================
# FORMAT MONEY
# ===============================
def format_money(value):
    if value >= 1e7:
        return f"{value/1e7:.2f}Cr"
    elif value >= 1e5:
        return f"{value/1e5:.2f}L"
    else:
        return f"{value:.0f}"

# ===============================
# REAL ITM LOGIC
# ===============================
def classify_strike(strike, option_type, future_price):
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
    price_match = re.search(r"PRICE:\s*([\d.]+)", text_upper)
    future_match = re.search(r"FUTURE\s+PRICE:\s*([\d.]+)", text_upper)

    if not (symbol_match and lot_match):
        return None

    symbol_full = symbol_match.group(1)
    lots = int(lot_match.group(1))
    price = float(price_match.group(1)) if price_match else None
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
        action_type = "CALL_SC" if option_type == "CE" else "PUT_SC"
    elif "LONG UNWINDING" in text_upper:
        action_type = "CALL_UNW" if option_type == "CE" else "PUT_UNW"
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
        "future": future_price,
        "price": price
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
    turnover_zone = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    futures_data = defaultdict(lambda: defaultdict(int))
    futures_turnover = defaultdict(lambda: defaultdict(float))
    last_future = {}

    total_bull = 0
    total_bear = 0
    bull_turnover = 0
    bear_turnover = 0
    total_turnover = 0

    for alert in batch:

        sym = alert["symbol"]
        act = alert["action_type"]
        zone = alert["zone"]
        lots = alert["lots"]
        price = alert["price"]
        lot_size = LOT_SIZES.get(sym, 1)

        if alert["future"]:
            last_future[sym] = alert["future"]

        if zone:
            data[sym][act][zone] += lots
            if price:
                turn = lots * price * lot_size
                turnover_zone[sym][act][zone] += turn
                total_turnover += turn
        else:
            futures_data[sym][act] += lots
            if alert["future"]:
                turn = lots * alert["future"] * lot_size
                futures_turnover[sym][act] += turn
                total_turnover += turn

    message = "<pre>\n📊 2 MIN LOT FLOW REPORT\n\n"

    for symbol in TRACK_SYMBOLS:

        if symbol not in data and symbol not in futures_data:
            continue

        message += f"{symbol} (FUT: {last_future.get(symbol,'N/A')})\n"
        message += "-" * 70 + "\n"
        message += f"{'TYPE':12}{'ITM':>18}{'OTM':>18}{'TOTAL':>18}\n"
        message += "-" * 70 + "\n"

        actions = [
            "CALL_WRITER","PUT_WRITER",
            "CALL_BUY","PUT_BUY",
            "CALL_SC","PUT_SC",
            "CALL_UNW","PUT_UNW"
        ]

        for action in actions:

            itm_l = data[symbol][action]["ITM"]
            otm_l = data[symbol][action]["OTM"]

            itm_t = turnover_zone[symbol][action]["ITM"]
            otm_t = turnover_zone[symbol][action]["OTM"]

            tot_l = itm_l + otm_l
            tot_t = itm_t + otm_t

            message += f"{action.replace('_',' '):12}" \
                       f"{(str(itm_l)+'('+format_money(itm_t)+')'):>18}" \
                       f"{(str(otm_l)+'('+format_money(otm_t)+')'):>18}" \
                       f"{(str(tot_l)+'('+format_money(tot_t)+')'):>18}\n"

        message += "-" * 70 + "\n"

        for f_act in ["FUTURE_BUY","FUTURE_SELL"]:
            lots = futures_data[symbol][f_act]
            turn = futures_turnover[symbol][f_act]
            message += f"{f_act.replace('_',' '):12}" \
                       f"{(str(lots)+'('+format_money(turn)+')'):>18}\n"

        message += "\n"

    # ================= NET FLOW CALC =================

    for symbol in TRACK_SYMBOLS:
        for action in data[symbol]:
            total_lots = data[symbol][action]["ITM"] + data[symbol][action]["OTM"]
            total_turn = turnover_zone[symbol][action]["ITM"] + turnover_zone[symbol][action]["OTM"]

            if action in ["PUT_WRITER","CALL_BUY","CALL_SC","PUT_UNW"]:
                total_bull += total_lots
                bull_turnover += total_turn
            elif action in ["CALL_WRITER","PUT_BUY","PUT_SC","CALL_UNW"]:
                total_bear += total_lots
                bear_turnover += total_turn

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

    message += "=" * 70 + "\n"
    message += "📈 NET DIRECTIONAL LOT FLOW (All Symbols)\n"
    message += "=" * 70 + "\n\n"
    message += f"Total Bullish Lots : {total_bull}\n"
    message += f"Total Bearish Lots : {total_bear}\n"
    message += f"Net Lot Flow       : {net_lots}\n"
    message += f"Bullish Dominance  : {dominance:.1f}%\n\n"
    message += f"Total Turnover     : {format_money(total_turnover)}\n"
    message += f"Bullish Turnover   : {format_money(bull_turnover)}\n"
    message += f"Bearish Turnover   : {format_money(bear_turnover)}\n\n"
    message += f"Bias               : {strength}\n"
    message += "Validity           : Next 2 Minutes Only\n"
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
