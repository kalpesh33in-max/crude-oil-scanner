import os
import re
import logging
from collections import defaultdict
from datetime import datetime, timedelta
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

TRACK_SYMBOLS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN"]

LOT_SIZES = {
    "BANKNIFTY": 30,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "AXISBANK": 625,
    "SBIN": 750
}

# ===============================
# MONEY FORMAT
# ===============================
def format_money(value):
    if value >= 1e7:
        return f"{value/1e7:.2f}Cr"
    elif value >= 1e5:
        return f"{value/1e5:.2f}L"
    else:
        return f"{value:.0f}"

# ===============================
# ITM / OTM LOGIC
# ===============================
def classify_strike(strike, option_type, future_price):

    strike=float(strike)
    future_price=float(future_price)

    if option_type=="CE":
        return "ITM" if strike < future_price else "OTM"

    if option_type=="PE":
        return "ITM" if strike > future_price else "OTM"

# ===============================
# BIAS LOGIC
# ===============================
def get_bias_label(net):

    if net > 500: return "🔥 VERY STRONG BULLISH"
    elif net > 150: return "🚀 STRONG BULLISH"
    elif net > 0: return "🟢 Mild Bullish"
    elif net < -500: return "🔥 VERY STRONG BEARISH"
    elif net < -150: return "📉 STRONG BEARISH"
    elif net < 0: return "🔴 Mild Bearish"
    else: return "⚖ Neutral"

# ===============================
# PARSE ALERT
# ===============================
def parse_alert(text):

    t=text.upper()

    symbol_match=re.search(r"SYMBOL:\s*([\w-]+)",t)
    lot_match=re.search(r"LOTS:\s*(\d+)",t)
    price_match=re.search(r"PRICE:\s*([\d.]+)",t)
    future_match=re.search(r"FUTURE:\s*([\d.]+)",t)

    if not(symbol_match and lot_match):
        return None

    symbol_full=symbol_match.group(1)
    lots=int(lot_match.group(1))
    price=float(price_match.group(1)) if price_match else None
    future_price=float(future_match.group(1)) if future_match else None

    base_symbol=next((s for s in TRACK_SYMBOLS if s in symbol_full),None)
    if not base_symbol:
        return None

    opt_match=re.search(
        r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d+)(CE|PE)",
        symbol_full
    )

    zone=None
    option_type=None

    if opt_match and future_price:

        strike=opt_match.group(3)
        option_type=opt_match.group(4)
        zone=classify_strike(strike,option_type,future_price)

    action=None

    if "PUT WRITER" in t:
        action="PUT_WR"
    elif "CALL WRITER" in t:
        action="CALL_WR"
    elif "SHORT COVERING" in t:
        action="CALL_SC" if option_type=="CE" else "PUT_SC"
    elif "LONG UNWINDING" in t:
        action="CALL_UNW" if option_type=="CE" else "PUT_UNW"
    elif "CALL BUY" in t:
        action="CALL_BUY"
    elif "PUT BUY" in t:
        action="PUT_BUY"
    elif "FUTURE BUY" in t:
        action="FUTURE_BUY"
    elif "FUTURE SELL" in t:
        action="FUTURE_SELL"

    if not action:
        return None

    return {
        "symbol":base_symbol,
        "lots":lots,
        "zone":zone,
        "action":action,
        "future":future_price,
        "price":price
    }

# ===============================
# TELEGRAM HANDLER
# ===============================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.channel_post or update.message

    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):

        parsed = parse_alert(msg.text)

        if parsed:

            # USE TELEGRAM MESSAGE TIME
            parsed["ts"] = msg.date.replace(tzinfo=None)

            alerts_buffer.append(parsed)

# ===============================
# SUMMARY PROCESS
# ===============================
async def process_summary(context: ContextTypes.DEFAULT_TYPE):

    global alerts_buffer

    now=datetime.utcnow()

    window_start=now-timedelta(seconds=60)

    batch=[a for a in alerts_buffer if a["ts"]>=window_start]

    # cleanup old alerts
    alerts_buffer=[a for a in alerts_buffer if a["ts"]>=now-timedelta(minutes=3)]

    if not batch:
        return

    opt_data=defaultdict(lambda:defaultdict(lambda:defaultdict(int)))
    opt_turn=defaultdict(lambda:defaultdict(lambda:defaultdict(float)))

    fut_data=defaultdict(lambda:defaultdict(int))
    fut_turn=defaultdict(lambda:defaultdict(float))

    last_future={}

    for a in batch:

        sym=a["symbol"]
        act=a["action"]
        zone=a["zone"]
        lots=a["lots"]
        price=a["price"]

        lot_size=LOT_SIZES.get(sym,1)

        if a["future"]:
            last_future[sym]=a["future"]

        if zone:

            opt_data[sym][act][zone]+=lots

            if "WR" in act or "_SC" in act:
                opt_turn[sym][act][zone]+=lots*125000
            else:
                if price:
                    opt_turn[sym][act][zone]+=lots*price*lot_size

        else:

            fut_data[sym][act]+=lots
            fut_turn[sym][act]+=lots*175000

    message="<pre>\n📊 1 MIN INSTITUTIONAL FLOW REPORT\n\n"

    for symbol in TRACK_SYMBOLS:

        if symbol not in opt_data and symbol not in fut_data:
            continue

        message+=f"💎 {symbol} (FUT: {last_future.get(symbol,'N/A')})\n"

        if symbol in opt_data:

            message+="--- OPTIONS FLOW ---\n"
            message+=f"{'TYPE':10}{'ITM':>13}{'OTM':>13}{'TOT':>13}\n"
            message+="-"*49+"\n"

            bull=bear=0
            bull_turn=bear_turn=0

            for act in opt_data[symbol]:

                itm_l=opt_data[symbol][act]["ITM"]
                otm_l=opt_data[symbol][act]["OTM"]

                itm_t=opt_turn[symbol][act]["ITM"]
                otm_t=opt_turn[symbol][act]["OTM"]

                tot_l=itm_l+otm_l
                tot_t=itm_t+otm_t

                itm_str=f"{itm_l}({format_money(itm_t)})"
                otm_str=f"{otm_l}({format_money(otm_t)})"
                tot_str=f"{tot_l}({format_money(tot_t)})"

                message+=f"{act:10}{itm_str:>13}{otm_str:>13}{tot_str:>13}\n"

                if act in ["PUT_WR","CALL_BUY","CALL_SC","PUT_UNW"]:
                    bull+=tot_l
                    bull_turn+=tot_t
                else:
                    bear+=tot_l
                    bear_turn+=tot_t

            message+="-"*49+"\n"

            message+=f"Option Bias: {get_bias_label(bull-bear)}\n"
            message+=f"Bullish Turn: {format_money(bull_turn)}\n"
            message+=f"Bearish Turn: {format_money(bear_turn)}\n\n"

        if symbol in fut_data:

            message+="--- FUTURES FLOW ---\n"
            message+=f"{'TYPE':10}{'ITM':>13}{'OTM':>13}{'TOT':>13}\n"
            message+="-"*49+"\n"

            bull=bear=0
            bull_turn=bear_turn=0

            for act in fut_data[symbol]:

                lots=fut_data[symbol][act]
                turn=fut_turn[symbol][act]

                itm_str=f"{lots}({format_money(turn)})"
                otm_str="0(0)"
                tot_str=itm_str

                message+=f"{act:10}{itm_str:>13}{otm_str:>13}{tot_str:>13}\n"

                if act=="FUTURE_BUY":
                    bull+=lots
                    bull_turn+=turn
                else:
                    bear+=lots
                    bear_turn+=turn

            message+="-"*49+"\n"

            message+=f"Future Bias: {get_bias_label(bull-bear)}\n"
            message+=f"Bullish Turn: {format_money(bull_turn)}\n"
            message+=f"Bearish Turn: {format_money(bear_turn)}\n"

        message+="="*49+"\n\n"

    message+="Validity: Next 1 Minute\n"
    message+="</pre>"

    await context.bot.send_message(
        chat_id=SUMMARY_CHAT_ID,
        text=message,
        parse_mode="HTML"
    )

# ===============================
# MAIN
# ===============================
def main():

    app=Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND),message_handler)
    )

    if app.job_queue:

        app.job_queue.run_repeating(
            process_summary,
            interval=60,
            first=10
        )

    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
