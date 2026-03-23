import os
import re
import logging
import pytz
from datetime import datetime
from collections import defaultdict
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)

IST = pytz.timezone('Asia/Kolkata')

# ================= ENV =================
BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
BOT_TOKEN_2 = os.getenv("SUMMARIZER_BOT_TOKEN_2")

TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_2MIN_CHAT_ID = os.getenv("SUMMARY_2MIN_CHAT_ID")
SUMMARY_5MIN_CHAT_ID = os.getenv("SUMMARY_5MIN_CHAT_ID")

bot2 = Bot(token=BOT_TOKEN_2) if BOT_TOKEN_2 else None

# ================= DATA =================
buffer_2min = []
buffer_5min = []

TRACK_SYMBOLS = ["BANKNIFTY","HDFCBANK","ICICIBANK","AXISBANK","SBIN"]

LOT_SIZES = {
    "BANKNIFTY": 30,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "AXISBANK": 625,
    "SBIN": 750
}

# ================= UTILS =================
def format_money(v):
    if v >= 1e7: return f"{v/1e7:.2f}Cr"
    elif v >= 1e5: return f"{v/1e5:.2f}L"
    return str(int(v))

def format_future(act):
    return act.replace("FUTURE_","FUT_")

def bias_label(x):
    if x > 500: return "🔥 VERY STRONG BULLISH"
    if x > 150: return "🚀 STRONG BULLISH"
    if x > 0: return "🟢 Mild Bullish"
    if x < -500: return "🔥 VERY STRONG BEARISH"
    if x < -150: return "📉 STRONG BEARISH"
    if x < 0: return "🔴 Mild Bearish"
    return "⚖ Neutral"

# ================= STRIKE =================
def classify_strike(strike, option_type, future_price):
    try:
        strike = float(strike)
        future_price = float(future_price)
        if option_type == "CE":
            return "ITM" if strike < future_price else "OTM"
        elif option_type == "PE":
            return "ITM" if strike > future_price else "OTM"
    except: pass
    return None

# ================= PARSER =================
def parse_alert(text):
    text = text.upper()

    # Improved symbol regex to capture symbols with spaces
    symbol = re.search(r"SYMBOL:\s*([^\n\r]+)", text)
    lots = re.search(r"LOTS:\s*(\d+)", text)
    price = re.search(r"PRICE:\s*([\d.]+)", text)
    fut = re.search(r"FUTURE PRICE:\s*([\d.]+)", text)

    if not symbol or not lots:
        return None

    symbol_full = symbol.group(1).strip()
    lots = int(lots.group(1))
    price = float(price.group(1)) if price else None
    fut_price = float(fut.group(1)) if fut else None

    base = next((s for s in TRACK_SYMBOLS if s in symbol_full), None)
    if not base:
        return None

    # Robust Option Match
    opt_match = re.search(r"(\d+)(CE|PE)$", symbol_full)
    zone = None
    option_type = None

    if opt_match and fut_price:
        strike = opt_match.group(1)
        option_type = opt_match.group(2)
        zone = classify_strike(strike, option_type, fut_price)

    is_future = (opt_match is None)

    # ACTION LOGIC
    if "WRITER" in text:
        act = "CALL_WRITER" if option_type == "CE" else "PUT_WRITER"

    elif "CALL BUY" in text:
        act = "CALL_BUY"

    elif "PUT BUY" in text:
        act = "PUT_BUY"

    elif "SHORT COVERING" in text:
        act = "FUTURE_SC" if is_future else ("CALL_SC" if option_type=="CE" else "PUT_SC")

    elif "LONG UNWINDING" in text:
        act = "FUTURE_UNW" if is_future else ("CALL_UNW" if option_type=="CE" else "PUT_UNW")

    elif "FUTURE BUY" in text or "BUY (LONG)" in text:
        act = "FUTURE_BUY"

    elif "FUTURE SELL" in text or "SELL (SHORT)" in text:
        act = "FUTURE_SELL"

    else:
        return None

    return {
        "symbol": base,
        "lots": lots,
        "price": price,
        "future": fut_price,
        "action": act,
        "zone": zone,
        "timestamp": datetime.now(IST)
    }

# ================= HANDLER =================
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message

    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        data = parse_alert(msg.text)
        if data:
            buffer_2min.append(data)
            buffer_5min.append(data)

# ================= SUMMARY =================
def build_summary(batch, mode):
    opt = defaultdict(lambda: defaultdict(lambda: {"ITM":0,"OTM":0}))
    opt_turn = defaultdict(lambda: defaultdict(lambda: {"ITM":0.0,"OTM":0.0}))
    fut = defaultdict(lambda: defaultdict(int))
    fut_turn = defaultdict(lambda: defaultdict(float))
    last_price = {}

    for a in batch:
        s, act, lots, price, zone = a["symbol"], a["action"], a["lots"], a["price"], a["zone"]

        if a["future"]:
            last_price[s] = a["future"]

        if "FUTURE" in act:
            fut[s][act] += lots
            fut_turn[s][act] += lots * 175000
        else:
            if zone:
                opt[s][act][zone] += lots

                if mode == "2min" and ("WRITER" in act or "_SC" in act):
                    opt_turn[s][act][zone] += lots * 125000
                else:
                    if price:
                        opt_turn[s][act][zone] += lots * price * LOT_SIZES[s]

    title = "2 MIN" if mode=="2min" else "5 MIN"
    msg = f"<pre>\n📊 {title} INSTITUTIONAL FLOW REPORT\n\n"

    for s in TRACK_SYMBOLS:
        if s not in opt and s not in fut:
            continue

        msg += f"💎 {s} (FUT: {last_price.get(s,'N/A')})\n\n"

        # OPTIONS
        if s in opt:
            msg += "--- OPTIONS FLOW ---\n"
            msg += f"{'TYPE':8}{'ITM':>14}{'OTM':>14}{'TOT':>14}\n"
            msg += "-"*50 + "\n"

            bull=bear=0
            bull_t=bear_t=0

            for act in opt[s]:
                itm_l = opt[s][act]["ITM"]
                otm_l = opt[s][act]["OTM"]

                itm_t = opt_turn[s][act]["ITM"]
                otm_t = opt_turn[s][act]["OTM"]

                tot_l = itm_l + otm_l
                tot_t = itm_t + otm_t

                name = act.replace("CALL_WRITER","CALL_WR").replace("PUT_WRITER","PUT_WR")

                msg += f"{name[:8]:8}{f'{itm_l}({format_money(itm_t)})':>14}{f'{otm_l}({format_money(otm_t)})':>14}{f'{tot_l}({format_money(tot_t)})':>14}\n"

                if act in ["PUT_WRITER","CALL_BUY","PUT_SC","CALL_UNW"]:
                    bull += tot_l; bull_t += tot_t
                else:
                    bear += tot_l; bear_t += tot_t

            msg += "-"*50 + "\n"
            msg += f"Option Bias: {bias_label(bull-bear)}\n"
            msg += f"Bullish Turn: {format_money(bull_t)}\n"
            msg += f"Bearish Turn: {format_money(bear_t)}\n\n"

        # FUTURES
        if s in fut:
            msg += "---- FUTURES FLOW ----\n"

            f_bull=f_bear=0
            f_bt=f_bt2=0

            for act in fut[s]:
                l = fut[s][act]
                t = fut_turn[s][act]

                name = format_future(act)

                msg += f"{name:10} : {l} lots ({format_money(t)})\n"

                if act in ["FUTURE_BUY","FUTURE_SC"]:
                    f_bull += l; f_bt += t
                else:
                    f_bear += l; f_bt2 += t

            msg += f"\nFuture Bias: {bias_label(f_bull-f_bear)}\n"
            msg += f"Bullish Turn: {format_money(f_bt)}\n"
            msg += f"Bearish Turn: {format_money(f_bt2)}\n"

        msg += "\n========================================\n\n"

    msg += f"Validity: Next {title}\n</pre>"
    return msg

# ================= JOBS =================
async def process_2min(context):
    global buffer_2min
    now = datetime.now(IST)
    
    # MARKET HOURS CHECK (9:15 AM to 3:30 PM IST)
    current_time_int = now.hour * 100 + now.minute
    if current_time_int < 915 or current_time_int > 1530:
        logging.info(f"⏳ Market Closed ({now.strftime('%H:%M')}). Skipping 2 MIN report.")
        return

    if not buffer_2min: return

    # Filter data for the last 2 minutes based on IST
    batch = [a for a in buffer_2min if a["timestamp"] >= now - timedelta(minutes=2)]
    buffer_2min = [a for a in buffer_2min if a["timestamp"] >= now - timedelta(minutes=2)]

    if not batch: return

    msg = build_summary(batch,"2min")
    await context.bot.send_message(chat_id=SUMMARY_2MIN_CHAT_ID,text=msg,parse_mode="HTML")

async def process_5min(context):
    global buffer_5min
    now = datetime.now(IST)

    # MARKET HOURS CHECK (9:15 AM to 3:30 PM IST)
    current_time_int = now.hour * 100 + now.minute
    if current_time_int < 915 or current_time_int > 1530:
        logging.info(f"⏳ Market Closed ({now.strftime('%H:%M')}). Skipping 5 MIN report.")
        return

    if not buffer_5min: return

    # Filter data for the last 5 minutes based on IST
    batch = [a for a in buffer_5min if a["timestamp"] >= now - timedelta(minutes=5)]
    buffer_5min = [a for a in buffer_5min if a["timestamp"] >= now - timedelta(minutes=5)]

    if not batch: return

    msg = build_summary(batch,"5min")

    target = bot2 if bot2 else context.bot
    await target.send_message(chat_id=SUMMARY_5MIN_CHAT_ID,text=msg,parse_mode="HTML")

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT, handler))

    app.job_queue.run_repeating(process_2min, interval=60, first=10)
    app.job_queue.run_repeating(process_5min, interval=60, first=20)

    app.run_polling()

if __name__ == "__main__":
    main()
