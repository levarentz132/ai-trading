#!/usr/bin/env python3
"""
Trend-filtered ATR breakout scalper – Binance Spot TESTNET
----------------------------------------------------------
• Longs only when 4-h close > EMA-200
• Entry: 1-m close > recent high + 0.25×ATR THEN pullback to EMA-8 (LIMIT_MAKER)
• Risk: 0.5 % of *allocated* USDT, stop = 1.25×ATR
• TP-1 at 2 R (half), TP-2 at 4 R, else stop at −1 R
• Daily circuit breaker: –3 % realised PnL
• State  → state_brk.json   • Log → brk_log.csv
"""

import os, time, json, csv, hmac, hashlib, uuid
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime, date
from typing import Tuple
import pandas as pd, requests
from dotenv import load_dotenv

# ───────── CONFIG ─────────
load_dotenv()
API_KEY, SECRET_KEY = os.getenv("BINANCE_KEY"), os.getenv("BINANCE_SECRET")
BASE_URL   = "https://testnet.binance.vision"
SYMBOL     = "BTCUSDT"

LTF_INT    = "1m"
HTF_INT    = "4h"
EMA_HTF    = 200
ATR_P      = 14
BREAK_K    = 0.25
RISK_PCT   = 0.005          # of USDT_ALLOC
STOP_MULT  = 1.25
TP1_R, TP2_R = 2, 4
DAILY_DD   = 0.03
USDT_ALLOC = 30_000         # bankroll for THIS bot

STATE_F = Path("state_brk.json")
LOG_F   = Path("brk_log.csv")
BOT_TAG = "BRK"
HEADERS = {"X-MBX-APIKEY": API_KEY}
# ──────────────────────────

# ─── request helpers ───
def _ts(): return int(time.time()*1000)
def _sign(p): q=urlencode(p,doseq=True); s=hmac.new(SECRET_KEY.encode(),q.encode(),hashlib.sha256).hexdigest(); return f"{q}&signature={s}"
def _get(path,params=None,signed=False):
    url=f"{BASE_URL}{path}"
    if signed: params=params or {}; params["timestamp"]=_ts(); url=f"{url}?{_sign(params)}"; params=None
    r=requests.get(url,params=params,headers=HEADERS,timeout=10); r.raise_for_status(); return r.json()
def _post(path,params,signed=True):
    url=f"{BASE_URL}{path}"
    if signed: params["timestamp"]=_ts(); url=f"{url}?{_sign(params)}"; params=None
    r=requests.post(url,headers=HEADERS,data=params,timeout=10); r.raise_for_status(); return r.json()
def tag(side): return f"{BOT_TAG}-{side}-{uuid.uuid4().hex[:6]}"
# ───────────────────────

# ─── filters & quantisers ───
flt=_get("/api/v3/exchangeInfo",{"symbol":SYMBOL})["symbols"][0]["filters"]
STEP=float(next(f for f in flt if f["filterType"]=="LOT_SIZE")["stepSize"])
TICK=float(next(f for f in flt if f["filterType"]=="PRICE_FILTER")["tickSize"])
MIN_NOT=float(next(f for f in flt if f["filterType"] in ("NOTIONAL","MIN_NOTIONAL"))["minNotional"])
q_qty=lambda q: round(q//STEP*STEP,6); q_price=lambda p: round(p//TICK*TICK,2)
# ──────────────────────────

# ─── data helpers ───
def klines(interval,limit=500):
    k=_get("/api/v3/klines",{"symbol":SYMBOL,"interval":interval,"limit":limit})
    return [float(c[4]) for c in k]
def balances()->Tuple[float,float]:
    bal=_get("/api/v3/account",signed=True)["balances"]
    d={b["asset"]:float(b["free"]) for b in bal}; return d.get("USDT",0),d.get("BTC",0)
atr = lambda p,per: pd.Series(p).diff().abs().rolling(per).mean().iloc[-1]
# ───────────────────────

# ─── state & log ───
def load():  return json.loads(STATE_F.read_text()) if STATE_F.exists() else {"qty":0}
def save(s): STATE_F.write_text(json.dumps(s))
def log(act,price,qty,pnl,usdt,btc):
    fresh=not LOG_F.exists()
    with LOG_F.open("a",newline="") as f:
        w=csv.writer(f); 
        if fresh: w.writerow(["ts","act","price","qty","pnl","usdt","btc"])
        w.writerow([datetime.utcnow().isoformat(timespec="seconds"),
                    act,f"{price:.2f}",f"{qty:.6f}",f"{pnl:.2f}",
                    f"{usdt:.2f}",f"{btc:.6f}"])
# ─────────────────────

def today_pnl():
    if not LOG_F.exists(): return 0
    df=pd.read_csv(LOG_F); 
    if "pnl" not in df.columns: return 0
    col="ts" if "ts" in df.columns else "Timestamp"
    df[col]=pd.to_datetime(df[col])
    return df[df[col].dt.date==date.today()]["pnl"].sum()

# ─── main loop ───
def main():
    st=load()
    print("Breakout bot live –", datetime.utcnow().isoformat(timespec="seconds"))

    while True:
        try:
            usdt, btc_wallet = balances()
            if today_pnl() <= -DAILY_DD*usdt:
                print("🟥 day DD hit; sleeping 60 s"); time.sleep(60); continue

            ltf     = klines(LTF_INT, max(ATR_P,50))
            price   = ltf[-1]
            atr_now = atr(ltf,ATR_P)

            htf  = klines(HTF_INT, EMA_HTF)
            ema200 = pd.Series(htf).ewm(span=EMA_HTF,adjust=False).mean().iloc[-1]
            regime_long = htf[-1] > ema200

            in_pos = st["qty"]>0

            # ENTRY  ─────────────────────
            if not in_pos and regime_long:
                recent_high=max(ltf[-4:-1])
                if price > recent_high + BREAK_K*atr_now and \
                   price <= pd.Series(ltf).ewm(span=8,adjust=False).mean().iloc[-1]:

                    risk   = min(usdt,USDT_ALLOC)*RISK_PCT
                    stop_d = STOP_MULT*atr_now
                    qty    = max(q_qty(risk/stop_d), q_qty(MIN_NOTIONAL/price))
                    limit  = q_price(price)

                    _post("/api/v3/order",{
                        "symbol":SYMBOL,"side":"BUY","type":"LIMIT_MAKER",
                        "price":f"{limit:.2f}","quantity":f"{qty:.6f}",
                        "newClientOrderId":tag("BUY"),"recvWindow":5000})

                    st={"entry":limit,"qty":qty,
                        "stop":limit-stop_d,
                        "tp1":limit+TP1_R*stop_d,
                        "tp2":limit+TP2_R*stop_d,
                        "half":False}; save(st)
                    log("BUY",limit,qty,0,*balances())
                    print(f"✅ BUY {qty:.6f}@{limit}")
                    continue

            # MANAGEMENT / EXIT ──────────
            if in_pos:
                q,entry = st["qty"], st["entry"]
                stop,tp1,tp2,half = st["stop"], st["tp1"], st["tp2"], st["half"]

                # TP-1
                if not half and price>=tp1:
                    sell=q_qty(q*0.5)
                    _post("/api/v3/order",{
                        "symbol":SYMBOL,"side":"SELL","type":"MARKET",
                        "quantity":f"{sell:.6f}",
                        "newClientOrderId":tag("TP1"),"recvWindow":5000})
                    pnl=(tp1-entry)*sell
                    log("TP1",tp1,sell,pnl,*balances())
                    st["qty"]-=sell; st["half"]=True; save(st)
                    print(f"⚪ TP-1 {sell:.6f}@{tp1}")
                    continue

                # final exit
                if price<=stop or price>=tp2:
                    sell=q_qty(st["qty"])
                    _post("/api/v3/order",{
                        "symbol":SYMBOL,"side":"SELL","type":"MARKET",
                        "quantity":f"{sell:.6f}",
                        "newClientOrderId":tag("EXIT"),"recvWindow":5000})
                    pnl=(price-entry)*sell
                    log("EXIT",price,sell,pnl,*balances())
                    print(f"✅ EXIT {sell:.6f}@{price} | PnL {pnl:.2f}")
                    st={"qty":0}; save(st)

            # heartbeat
            print(f"{datetime.utcnow():%H:%M:%S} | {price:,.2f} | "
                  f"{'LONG' if regime_long else 'FLAT'} | ATR {atr_now:.2f} | "
                  f"USDT {usdt:,.0f} | BTC {btc_wallet:.6f}", end="\r")

        except Exception as e:
            print("\n❌",e)

        time.sleep(5)

if __name__=="__main__":
    main()
