import os
import re
import logging
from collections import defaultdict
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ===============================
# LOGGING
# ===============================
logging.basicConfig(level=logging.INFO)

# ===============================
# ENV
# ===============================
BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
BOT_TOKEN_2 = os.getenv("SUMMARIZER_BOT_TOKEN_2")

TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_2MIN_CHAT_ID = os.getenv("SUMMARY_2MIN_CHAT_ID")
SUMMARY_5MIN_CHAT_ID = os.getenv("SUMMARY_5MIN_CHAT_ID")

bot2 = Bot(token=BOT_TOKEN_2) if BOT_TOKEN_2 else None

# ===============================
# BUFFERS
# ===============================
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
# UTILS
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
# STRIKE LOGIC
# ===============================
def classify_strike(strike, option_type, fut):
    if option_type == "CE":
        return "ITM" if strike < fut else "OTM"
    else:
        return "ITM" if strike > fut else "OTM"

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

    option_type = "CE" if "CE" in symbol_full else ("PE" if "PE" in symbol_full else None)
    strike_match = re.search(r"(\d+)(CE|PE)", symbol_full)

    zone = None
    if strike_match and future:
        strike = float(strike_match.group(1))
        zone = classify_strike(strike, option_type, future)

    # ACTION
    if "WRITER" in text:
        act = "CALL_WRITER" if option_type == "CE" else "PUT_WRITER"
    elif "CALL BUY" in text:
        act = "CALL_BUY"
    elif "PUT BUY" in text:
        act = "PUT_BUY"
    elif "SHORT COVERING" in text:
        act = "FUTURE_SC" if "FUTURE" in text else ("CALL_SC" if option_type == "CE" else "PUT_SC")
    elif "LONG UNWINDING" in text:
        act = "FUTURE_UNW" if "FUTURE" in text else ("CALL_UNW" if option_type == "CE" else "PUT_UNW")
    elif "FUTURE BUY" in text:
        act = "FUTURE_BUY"
    elif "FUTURE SELL" in text:
        act = "FUTURE_SELL"
    else:
        return None

    return {
        "symbol": base,
        "lots": lots,
        "price": price,
        "future": future,
        "action": act,
        "zone": zone
    }

# ===============================
# HANDLER
# ===============================
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message

    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        data = parse_alert(msg.text)
        if data:
            buffer_2min.append(data)
            buffer_5min.append(data)

# ===============================
# COMMON SUMMARY BUILDER
# ===============================
def build_summary(batch, mode):
    opt_data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    opt_turn = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    fut_data = defaultdict(lambda: defaultdict(int))
    fut_turn = defaultdict(lambda: defaultdict(float))
    last_future = {}

    for a in batch:
        sym, act, zone, lots, price = a["symbol"], a["action"], a["zone"], a["lots"], a["price"]
        lot_size = LOT_SIZES.get(sym, 1)

        if a["future"]:
            last_future[sym] = a["future"]

        if zone:
            opt_data[sym][act][zone] += lots

            # 🔴 DIFFERENCE HERE
            if mode == "2min" and ("WRITER" in act or "_SC" in act):
                opt_turn[sym][act][zone] += lots * 125000
            else:
                if price:
                    opt_turn[sym][act][zone] += lots * price * lot_size
        else:
            fut_data[sym][act] += lots
            fut_turn[sym][act] += lots * 175000

    title = "2 MIN" if mode == "2min" else "5 MIN"
    message = f"<pre>\n📊 {title} INSTITUTIONAL FLOW REPORT\n\n"

    for symbol in TRACK_SYMBOLS:
        if symbol not in opt_data and symbol not in fut_data:
            continue

        message += f"💎 {symbol} (FUT: {last_future.get(symbol,'N/A')})\n\n"

        # OPTIONS
        if symbol in opt_data:
            message += "--- OPTIONS FLOW ---\n"
            message += f"{'TYPE':8}{'ITM':>14}{'OTM':>14}{'TOT':>14}\n"
            message += "-"*50 + "\n"

            bull, bear = 0, 0
            bull_t, bear_t = 0, 0

            for act in opt_data[symbol]:
                itm_l = opt_data[symbol][act]["ITM"]
                otm_l = opt_data[symbol][act]["OTM"]
                itm_t = opt_turn[symbol][act]["ITM"]
                otm_t = opt_turn[symbol][act]["OTM"]

                tot_l = itm_l + otm_l
                tot_t = itm_t + otm_t

                if act in ["PUT_WRITER","CALL_BUY","PUT_SC","CALL_UNW"]:
                    bull += tot_l; bull_t += tot_t
                else:
                    bear += tot_l; bear_t += tot_t

                name = act.replace("CALL_WRITER","CALL_WR").replace("PUT_WRITER","PUT_WR")

                message += f"{name[:8]:8}{f'{itm_l}({format_money(itm_t)})':>14}{f'{otm_l}({format_money(otm_t)})':>14}{f'{tot_l}({format_money(tot_t)})':>14}\n"

            message += "-"*50 + "\n"
            message += f"Option Bias: {get_bias_label(bull - bear)}\n"
            message += f"Bullish Turn: {format_money(bull_t)}\n"
            message += f"Bearish Turn: {format_money(bear_t)}\n\n"

        # FUTURES
        if symbol in fut_data:
            message += "---- FUTURES FLOW ----\n"

            f_bull, f_bear = 0, 0
            f_bull_t, f_bear_t = 0, 0

            for act in fut_data[symbol]:
                lots = fut_data[symbol][act]
                turn = fut_turn[symbol][act]

                name = format_future_name(act)

                message += f"{name:10} : {lots} lots ({format_money(turn)})\n"

                if act in ["FUTURE_BUY","FUTURE_SC"]:
                    f_bull += lots; f_bull_t += turn
                else:
                    f_bear += lots; f_bear_t += turn

            message += f"\nFuture Bias: {get_bias_label(f_bull - f_bear)}\n"
            message += f"Bullish Turn: {format_money(f_bull_t)}\n"
            message += f"Bearish Turn: {format_money(f_bear_t)}\n"

        message += "\n========================================\n\n"

    message += f"Validity: Next {title}\n</pre>"
    return message

# ===============================
# JOBS
# ===============================
async def process_2min(context):
    global buffer_2min
    if not buffer_2min: return

    batch = buffer_2min.copy()
    buffer_2min.clear()

    msg = build_summary(batch, "2min")
    await context.bot.send_message(chat_id=SUMMARY_2MIN_CHAT_ID, text=msg, parse_mode="HTML")

async def process_5min(context):
    global buffer_5min
    if not buffer_5min: return

    batch = buffer_5min.copy()
    buffer_5min.clear()

    msg = build_summary(batch, "5min")

    target_bot = bot2 if bot2 else context.bot
    await target_bot.send_message(chat_id=SUMMARY_5MIN_CHAT_ID, text=msg, parse_mode="HTML")

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
