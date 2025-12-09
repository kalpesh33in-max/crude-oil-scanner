# --- (NO CHANGE TO IMPORTS OR GLOBAL VARIABLES) ---

def lots_from_oi_change(oi_change):
    return abs(oi_change) // LOT_SIZE

def get_level(lots, is_buy=False):
    # ... (No change to this function)
    if is_buy:
        if lots >= 200:   return "Super Extreme Spike (200+)"
        elif lots >= 150: return "Extreme Spike (150+)"
        else:             return None
    else: # Writing or Future
        for threshold, label in sorted(WRITE_THRESHOLDS.items(), reverse=True):
            if lots >= threshold:
                return label
        return None

def send_alert(title, lots_label, side, strike_type, strike, price, oi_change, iv_roc, fut_price, fut_change, pct_change, strike_category):
    """
    MODIFIED: Added strike_category (ATM/ITM/OTM) to the signature.
    """
    lots = lots_from_oi_change(oi_change)
    # Avoid division by zero if prev_oi is 0 (though less likely in this simulation)
    oi_pct = (oi_change / prev_oi * 100) if prev_oi and prev_oi != 0 else 0
    
    msg = f"<b>{title}</b>\n\n"
    msg += "<pre>OPTION DATA                       | FUTURE DATA\n"
    msg += "────────────────────────────┼────────────────────────────\n"
    msg += f"Strike: {strike} {strike_type:<12} | Future Price: {fut_price:>8,.2f}\n"
    msg += f"Price : ${price:<17} | Change      : {fut_change:+.2f} ({pct_change:+.2f}%)\n"
    msg += f"∆OI   : {oi_change:+,} ({lots:,} lots)\n"
    msg += f"OI %  : {oi_pct:+.1f}%\n"
    msg += f"Lots  : {lots:,} ({lots_label})\n"
    msg += f"IV ROC: {iv_roc:+.1f}%\n"
    msg += f"Type  : {strike_category}\n"  # NEW: Display ATM/ITM/OTM
    msg += "</pre>\n"
    msg += f"<b>Time:</b> {datetime.now().strftime('%H:%M:%S IST')}"

    # ... (Telegram sending logic remains the same)
    if bot and CHAT_ID:
        try:
            import asyncio
            for cid in CHAT_ID.split(','):
                asyncio.run(bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML'))
        except Exception as e:
            print(f"Telegram send error: {e}")

def monitor():
    global prev_oi, sent_alerts
    while True:
        try:
            ticker = yf.Ticker(FUT_SYMBOL)
            hist = ticker.history(period="2d", interval="5m")
            if len(hist) < 2:
                time.sleep(180)
                continue
                
            latest = hist.iloc[-1]
            prev_close = hist.iloc[-2]['Close']
            fut_price = latest['Close']
            price_change = fut_price - prev_close
            price_pct = (price_change / prev_close) * 100
            
            # --- SIMULATION SECTION ---
            # Simulate a specific strike price (e.g., nearest 0.5 or 1.0)
            sim_strike = round(fut_price * 2) / 2.0  # Simulating nearest 0.5 strike
            
            # Simulate two distinct OI events (one for CE, one for PE)
            ce_oi_change = random.randint(-800000, 1200000)
            pe_oi_change = random.randint(-800000, 1200000)
            
            # Update prev_oi based on combined change (for % calculation)
            total_oi_change = ce_oi_change + pe_oi_change
            base_oi = random.randint(5000000, 15000000)
            current_oi = (prev_oi or base_oi) + total_oi_change 
            # --- END SIMULATION SECTION ---
            
            
            # --- CE (CALL) LOGIC ---
            
            ce_lots = lots_from_oi_change(ce_oi_change)
            ce_iv_roc = round(random.uniform(-15, 25), 1)
            ce_option_price = round(random.uniform(0.50, 5.00), 2)
            
            # Determine ATM/ITM for CALL
            if sim_strike < fut_price:
                ce_category = "ITM" # Call is In-The-Money
            elif abs(sim_strike - fut_price) < 0.1: # Near current price
                ce_category = "ATM" # Call is At-The-Money
            else:
                ce_category = "OTM"
            
            # Only proceed if ITM or ATM
            if ce_category in ["ATM", "ITM"]:
                
                # CALL BUY (OI Increase > 0)
                if ce_oi_change > 0 and ce_lots >= BUY_SPIKE_THRESHOLD:
                    level = get_level(ce_lots, is_buy=True)
                    if level:
                        title = f"CALL BUY → {level} ({ce_category})" # NEW: Specific Title
                        key = f"BUY{level}CE{ce_category}"
                        if key not in sent_alerts:
                            send_alert(title, level, "BUY", "CE", sim_strike, ce_option_price, ce_oi_change, ce_iv_roc, fut_price, price_change, price_pct, ce_category)
                            sent_alerts.add(key)
                
                # CALL WRITE (OI Decrease < 0)
                elif ce_oi_change < 0 and ce_lots >= 75:
                    level = get_level(ce_lots, is_buy=False)
                    if level:
                        title = f"CALL WRITE → {level} ({ce_category})" # NEW: Specific Title
                        key = f"WRITE{level}CE{ce_category}"
                        if key not in sent_alerts:
                            send_alert(title, level, "WRITE", "CE", sim_strike, ce_option_price, ce_oi_change, ce_iv_roc, fut_price, price_change, price_pct, ce_category)
                            sent_alerts.add(key)


            # --- PE (PUT) LOGIC ---
            
            pe_lots = lots_from_oi_change(pe_oi_change)
            pe_iv_roc = round(random.uniform(-15, 25), 1)
            pe_option_price = round(random.uniform(0.50, 5.00), 2)
            
            # Determine ATM/ITM for PUT
            if sim_strike > fut_price:
                pe_category = "ITM" # Put is In-The-Money
            elif abs(sim_strike - fut_price) < 0.1:
                pe_category = "ATM" # Put is At-The-Money
            else:
                pe_category = "OTM"
            
            # Only proceed if ITM or ATM
            if pe_category in ["ATM", "ITM"]:
                
                # PUT BUY (OI Increase > 0)
                if pe_oi_change > 0 and pe_lots >= BUY_SPIKE_THRESHOLD:
                    level = get_level(pe_lots, is_buy=True)
                    if level:
                        title = f"PUT BUY → {level} ({pe_category})" # NEW: Specific Title
                        key = f"BUY{level}PE{pe_category}"
                        if key not in sent_alerts:
                            send_alert(title, level, "BUY", "PE", sim_strike, pe_option_price, pe_oi_change, pe_iv_roc, fut_price, price_change, price_pct, pe_category)
                            sent_alerts.add(key)
                            
                # PUT WRITE (OI Decrease < 0)
                elif pe_oi_change < 0 and pe_lots >= 75:
                    level = get_level(pe_lots, is_buy=False)
                    if level:
                        title = f"PUT WRITE → {level} ({pe_category})" # NEW: Specific Title
                        key = f"WRITE{level}PE{pe_category}"
                        if key not in sent_alerts:
                            send_alert(title, level, "WRITE", "PE", sim_strike, pe_option_price, pe_oi_change, pe_iv_roc, fut_price, price_change, price_pct, pe_category)
                            sent_alerts.add(key)


            # --- FUTURE BUY/SELL (Remains the same, using simulated total lots) ---
            fut_lots = lots_from_oi_change(total_oi_change)
            fut_level = get_level(fut_lots, is_buy=False)
            if fut_level and abs(price_pct) >= 0.4:
                fut_side = "BUY" if price_change > 0 else "SELL"
                title = f"FUTURE {fut_side} → {fut_level}"
                key = f"FUT{fut_level}{fut_side}"
                if key not in sent_alerts:
                    msg = f"<b>{title}</b>\n\n<pre>Futures Lots Buildup: {fut_lots:,} ({fut_level})\nPrice Move: {price_change:+.2f} ({price_pct:+.2f}%)\nTime: {datetime.now().strftime('%H:%M:%S IST')}</pre>"
                    if bot and CHAT_ID:
                        try:
                            for cid in CHAT_ID.split(','):
                                # Note: You'll need to use asyncio for bot.send_message outside the main thread
                                import asyncio
                                asyncio.run(bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML'))
                        except Exception as e:
                            print(f"Telegram send error: {e}")
                    sent_alerts.add(key)

            # Clear old alerts after 10 mins (simplified mechanism)
            if len(sent_alerts) > 100: # Increased limit to avoid missing alerts
                sent_alerts = set()
            
            prev_oi = current_oi
            
        except Exception as e:
            print(f"Monitor error: {e}")
            
        time.sleep(180)  # 3 minutes

# --- (NO CHANGE TO THREAD START OR FLASK APP RUN) ---
