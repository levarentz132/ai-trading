#!/usr/bin/env python3
"""
EMA-RSI scalper – Binance Spot TESTNET
--------------------------------------
• Longs when EMA-3 > EMA-8 and RSI-14 < 70
• Sells on EMA cross-down, −2 % stop, or +4 % take-profit
• Risk: 20 % of *allocated* USDT per entry
• Keeps its own state  →  state_ema.json
• Logs trades         →  ema_log.csv
"""

import os, time, hmac, hashlib, csv, json, uuid
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime
from typing import Tuple

import requests, pandas as pd
from dotenv import load_dotenv

# ───────── CONFIG ─────────
load_dotenv()
API_KEY,  SECRET_KEY = os.getenv("BINANCE_KEY"), os.getenv("BINANCE_SECRET")
BASE_URL   = "https://testnet.binance.vision"

SYMBOL     = "BTCUSDT"
INTERVAL   = "1m"
POS_PCT    = 0.20          # 20 % of USDT_ALLOC per entry
USDT_ALLOC = 20_000        # bot’s private bankroll (set huge to disable)
STOP_LOSS  = 0.02          # 2 % below entry
TAKE_PROF  = 0.04          # 4 % above entry

STATE_F = Path("state_ema.json")
LOG_F   = Path("ema_log.csv")
BOT_TAG = "EMA"
HEADERS = {"X-MBX-APIKEY": API_KEY}
# ──────────────────────────

# ───── REST helpers ─────
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
# ────────────────────────

# ───── filters & quantisers ─────
flt=_get("/api/v3/exchangeInfo",{"symbol":SYMBOL})["symbols"][0]["filters"]
STEP=float(next(f for f in flt if f["filterType"]=="LOT_SIZE")["stepSize"])
TICK=float(next(f for f in flt if f["filterType"]=="PRICE_FILTER")["tickSize"])
MIN_NOT=float(next(f for f in flt if f["filterType"] in ("NOTIONAL","MIN_NOTIONAL"))["minNotional"])
q_qty   = lambda q: round(q//STEP*STEP,6)
# ────────────────────────────────

# ───── data helpers ─────
def klines(limit=50):
    k=_get("/api/v3/klines",{"symbol":SYMBOL,"interval":INTERVAL,"limit":limit})
    return [float(c[4]) for c in k]
def balances()->Tuple[float,float]:
    bal=_get("/api/v3/account",signed=True)["balances"]
    d={b["asset"]:float(b["free"]) for b in bal}; return d.get("USDT",0),d.get("BTC",0)
ema = lambda s,p: pd.Series(s).ewm(span=p,adjust=False).mean().iloc[-1]
def rsi(s,p=14):
    d=pd.Series(s).diff().dropna(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.rolling(p).mean(); al=l.rolling(p).mean()
    return 100 if al.iloc[-1]==0 else 100-100/(1+ag.iloc[-1]/al.iloc[-1])
# ────────────────────────

# ───── state & log ─────
def load():  return json.loads(STATE_F.read_text()) if STATE_F.exists() else {"entry":None,"qty":0}
def save(s): STATE_F.write_text(json.dumps(s))
def log(act,price,qty,pnl,usdt,btc):
    fresh=not LOG_F.exists()
    with LOG_F.open("a",newline="") as f:
        w=csv.writer(f); 
        if fresh: w.writerow(["ts","act","price","qty","pnl","usdt","btc"])
        w.writerow([datetime.utcnow().isoformat(timespec="seconds"),
                    act,f"{price:.2f}",f"{qty:.6f}",f"{pnl:.2f}",
                    f"{usdt:.2f}",f"{btc:.6f}"])
# ────────────────────────

def main():
    st=load()
    print("EMA bot live –", datetime.utcnow().isoformat(timespec="seconds"))

    while True:
        try:
            closes=klines(50); price=closes[-1]
            ema3, ema8, rsi14 = ema(closes,3), ema(closes,8), rsi(closes)
            usdt, btc = balances(); in_pos = st["qty"]>0

            # ENTRY
            if not in_pos and ema3>ema8 and rsi14<70:
                spend=min(usdt,USDT_ALLOC)*POS_PCT
                if spend>=MIN_NOT:
                    _post("/api/v3/order",{
                        "symbol":SYMBOL,"side":"BUY","type":"MARKET",
                        "quoteOrderQty":f"{spend:.2f}",
                        "newClientOrderId":tag("BUY"),"recvWindow":5000})
                    new_btc = balances()[1] - btc
                    st={"entry":price,"qty":new_btc}; save(st)
                    log("BUY",price,new_btc,0,*balances())
                    print(f"✅ BUY {new_btc:.6f}@{price}")

            # EXIT
            if in_pos:
                entry, qty = st["entry"], st["qty"]
                if (ema3<ema8 and rsi14>40) or price<=entry*(1-STOP_LOSS) or price>=entry*(1+TAKE_PROF):
                    _post("/api/v3/order",{
                        "symbol":SYMBOL,"side":"SELL","type":"MARKET",
                        "quantity":f"{q_qty(qty):.6f}",
                        "newClientOrderId":tag("SELL"),"recvWindow":5000})
                    pnl=(price-entry)*qty
                    log("SELL",price,qty,pnl,*balances())
                    print(f"✅ SELL {qty:.6f}@{price} | PnL {pnl:.2f}")
                    st={"entry":None,"qty":0}; save(st)

            # heartbeat
            print(f"{datetime.utcnow():%H:%M:%S} | {price:,.2f} | EMA3 {ema3:.2f} | EMA8 {ema8:.2f} "
                  f"| RSI {rsi14:.1f} | USDT {usdt:,.0f} | BTC {btc:.6f}", end="\r")

        except Exception as e:
            print("\n❌",e)

        time.sleep(5)

if __name__=="__main__":
    main()
