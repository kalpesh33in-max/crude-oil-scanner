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

TRACK_SYMBOLS = ["BANKNIFTY","HDFCBANK","ICICIBANK","AXISBANK","SBIN"]

LOT_SIZES = {
    "BANKNIFTY":30,
    "HDFCBANK":550,
    "ICICIBANK":700,
    "AXISBANK":625,
    "SBIN":750
}

# ===============================
# MONEY FORMAT
# ===============================
def format_money(value):

    if value >= 1e7:
        return f"{value/1e7:.2f}Cr"

    elif value >= 1e5:
        return f"{value/1e5:.2f}L"

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
def get_bias_label(net_lots):

    if net_lots > 500:
        return "🔥 VERY STRONG BULLISH"

    elif net_lots > 150:
        return "🚀 STRONG BULLISH"

    elif net_lots > 0:
        return "🟢 Mild Bullish"

    elif net_lots < -500:
        return "🔥 VERY STRONG BEARISH"

    elif net_lots < -150:
        return "📉 STRONG BEARISH"

    elif net_lots < 0:
        return "🔴 Mild Bearish"

    else:
        return "⚖ Neutral"


# ===============================
# PARSE ALERT
# ===============================
def parse_alert(text):

    text=text.upper()

    symbol_match=re.search(r"SYMBOL:\s*([\w-]+)",text)
    lot_match=re.search(r"LOTS:\s*(\d+)",text)
    price_match=re.search(r"PRICE:\s*([\d.]+)",text)
    future_match=re.search(r"FUTURE:\s*([\d.]+)",text)

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

    if "PUT WRITER" in text:
        action="PUT_WR"

    elif "CALL WRITER" in text:
        action="CALL_WR"

    elif "SHORT COVERING" in text:

        if option_type=="CE":
            action="CALL_SC"
        else:
            action="PUT_SC"

    elif "LONG UNWINDING" in text:

        if option_type=="CE":
            action="CALL_UNW"
        else:
            action="PUT_UNW"

    elif "CALL BUY" in text:
        action="CALL_BUY"

    elif "PUT BUY" in text:
        action="PUT_BUY"

    elif "FUTURE BUY" in text:
        action="FUTURE_BUY"

    elif "FUTURE SELL" in text:
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
async def message_handler(update:Update,context:ContextTypes.DEFAULT_TYPE):

    msg=update.channel_post or update.message

    if msg and msg.text and str(msg.chat_id)==str(TARGET_CHANNEL_ID):

        parsed=parse_alert(msg.text)

        if parsed:
            alerts_buffer.append(parsed)


# ===============================
# SUMMARY PROCESS
# ===============================
async def process_summary(context:ContextTypes.DEFAULT_TYPE):

    global alerts_buffer

    if not alerts_buffer:
        return

    batch=list(alerts_buffer)
    alerts_buffer.clear()

    opt_data=defaultdict(lambda:defaultdict(lambda:defaultdict(int)))
    opt_turn=defaultdict(lambda:defaultdict(lambda:defaultdict(float)))

    fut_data=defaultdict(lambda:defaultdict(int))
    fut_turn=defaultdict(lambda:defaultdict(float))

    last_future={}

    for alert in batch:

        sym=alert["symbol"]
        act=alert["action"]
        zone=alert["zone"]
        lots=alert["lots"]
        price=alert["price"]

        lot_size=LOT_SIZES.get(sym,1)

        if alert["future"]:
            last_future[sym]=alert["future"]

        if zone:

            opt_data[sym][act][zone]+=lots

            if "WR" in act or "_SC" in act:

                multiplier=125000
                opt_turn[sym][act][zone]+=lots*multiplier

            else:

                if price:
                    opt_turn[sym][act][zone]+=lots*price*lot_size

        else:

            fut_data[sym][act]+=lots
            fut_turn[sym][act]+=lots*175000


    message="<pre>\n📊 2 MIN INSTITUTIONAL FLOW REPORT\n\n"

    for symbol in TRACK_SYMBOLS:

        if symbol not in opt_data and symbol not in fut_data:
            continue

        message+=f"💎 {symbol} (FUT: {last_future.get(symbol,'N/A')})\n"


        # OPTIONS FLOW
        if symbol in opt_data:

            message+="--- OPTIONS FLOW ---\n"

            message+=f"{'TYPE':10}{'ITM':>13}{'OTM':>13}{'TOT':>13}\n"
            message+="-"*49+"\n"

            s_bull=0
            s_bear=0
            s_bull_turn=0
            s_bear_turn=0

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
                    s_bull+=tot_l
                    s_bull_turn+=tot_t
                else:
                    s_bear+=tot_l
                    s_bear_turn+=tot_t

            message+="-"*49+"\n"

            net=s_bull-s_bear

            message+=f"Option Bias: {get_bias_label(net)}\n"
            message+=f"Bullish Turn: {format_money(s_bull_turn)}\n"
            message+=f"Bearish Turn: {format_money(s_bear_turn)}\n\n"


        # FUTURES FLOW
        if symbol in fut_data:

            message+="--- FUTURES FLOW ---\n"

            message+=f"{'TYPE':10}{'ITM':>13}{'OTM':>13}{'TOT':>13}\n"
            message+="-"*49+"\n"

            f_bull=0
            f_bear=0
            f_bull_turn=0
            f_bear_turn=0

            for act in fut_data[symbol]:

                lots=fut_data[symbol][act]
                turn=fut_turn[symbol][act]

                itm_l=lots
                otm_l=0

                itm_t=turn
                otm_t=0

                tot_l=lots
                tot_t=turn

                itm_str=f"{itm_l}({format_money(itm_t)})"
                otm_str=f"{otm_l}(0)"
                tot_str=f"{tot_l}({format_money(tot_t)})"

                message+=f"{act:10}{itm_str:>13}{otm_str:>13}{tot_str:>13}\n"

                if act in ["FUTURE_BUY","FUTURE_SC"]:
                    f_bull+=lots
                    f_bull_turn+=turn
                else:
                    f_bear+=lots
                    f_bear_turn+=turn

            message+="-"*49+"\n"

            message+=f"Future Bias: {get_bias_label(f_bull-f_bear)}\n"
            message+=f"Bullish Turn: {format_money(f_bull_turn)}\n"
            message+=f"Bearish Turn: {format_money(f_bear_turn)}\n"

        message+="="*49+"\n\n"


    message+="Validity: Next 2 Minutes\n"
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
