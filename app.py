from flask import Flask
import requests
import pandas as pd
from datetime import datetime
import threading
import time
import os
import asyncio
import telegram
import json

app = Flask(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = telegram.Bot(token=TOKEN) if TOKEN else None

# MCX Crude Oil - Real data source (free & fast)
MCX_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPI_SpanMarginData.json"
FUT_SYMBOL = "CRUDEOIL"

prev_oi = {}
sent_alerts = set()

def get_mcx_data():
    try:
        r = requests.get(MCX_URL, timeout=10)
        data = r.json()
        crude_data = [x for x in data if x['Symbol'] == FUT_SYMBOL and "OPT" not in x['InstrumentName']]
        opts_data = [x for x in data if x['Symbol'] == FUT_SYMBOL and "OPT" in x['InstrumentName']]
        return crude_data, opts_data
    except:
        return [], []

def lots_from_oi(oi):
    return int(oi) // 100  # MCX Crude lot size = 100 barrels

def send_mcx_alert(title, strike, strike_type, price, doi, lots, iv_roc, fut_price, change, change_pct):
    msg = f"<b>{title}</b>\n\n"
    msg += "<pre>OPTION DATA                 | FUTURE DATA\n"
    msg += "────────────────────────────┼────────────────────────────\n"
    msg += f"Strike: {strike} {strike_type:<12} | Future Price: {fut_price:>7,.0f}\n"
    msg += f"Price : ₹{price:<16} | Change     : {change:+.0f} ({change_pct:+.2f}%)\n"
    msg += f"∆OI   : {doi:+,} ({lots:,} Lots)\n"
    msg += f"Lots  : {lots:,} ({'Super Extreme' if lots >= 200 else 'Extreme' if lots >= 150 else 'High'})\n"
    msg += f"IV ROC: {iv_roc:+.1f}%\n"
    msg += "</pre>\n"
    msg += f"<b>Time:</b> {datetime.now().strftime('%H:%M:%S IST')}"

    if bot and CHAT_ID:
        try:
            for cid in CHAT_ID.split(','):
                asyncio.run(bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML'))
        except Exception as e:
            print(e)

def monitor_mcx():
    global prev_oi
    while True:
        try:
            fut_data, opt_data = get_mcx_data()
            if not fut_data:
                time.sleep(60)
                continue

            # Current future price
            fut = fut_data[0]
            current_price = float(fut['LTP'])
            prev_price = float(fut.get('PreviousClose', current_price))
            change = current_price - prev_price
            change_pct = (change / prev_price) * 100

            # Process options
            for opt in opt_data:
                strike = opt['StrikePrice']
                opt_type = "CE" if opt['OptionType'] == "CALL" else "PE"
                ltp = float(opt['LTP'])
                oi = int(opt['OpenInterest'])
                prev_oi_val = prev_oi.get(opt['UniqueKey'], oi)
                doi = oi - prev_oi_val
                lots = lots_from_oi(abs(doi))

                # Only trigger on big moves
                if lots >= 75:
                    if doi > 0:  # Buying
                        if lots >= 200:
                            title = "CALL BUY / PUT BUY → Super Extreme Spike (200+)"
                        elif lots >= 150:
                            title = "CALL BUY / PUT BUY → Extreme Spike (150+)"
                        else:
                            continue
                    else:  # Writing
                        title = f"CALL WRITE / PUT WRITE → {'Super Extreme' if lots >= 200 else 'Extreme' if lots >= 150 else 'High'}"

                    key = f"{title}{strike}{opt_type}"
                    if key not in sent_alerts:
                        send_mcx_alert(title, strike, opt_type, ltp, doi, lots, 0, current_price, change, change_pct)
                        sent_alerts.add(key)
                        if len(sent_alerts) > 20:
                            sent_alerts.clear()

            # Update prev OI
            for opt in opt_data:
                prev_oi[opt['UniqueKey']] = int(opt['OpenInterest'])

        except Exception as e:
            print("Error:", e)

        time.sleep(60)  # Check every 1 minute

threading.Thread(target=monitor_mcx, daemon=True).start()

@app.route('/')
def home():
    return "<h1>MCX CRUDE OIL SCANNER (₹) LIVE – INDIAN STYLE</h1>"

if __name__ == "__main__":
    if bot and CHAT_ID:
        try:
            asyncio.run(bot.send_message(
                chat_id=CHAT_ID.split(',')[0].strip(),
                text="MCX CRUDE OIL SCANNER STARTED (₹)\nReal NSE/MCX Data – No More Dollar\nKalpe Bhai Style Alerts ON"
            ))
        except:
            pass
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
