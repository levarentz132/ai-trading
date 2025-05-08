#!/usr/bin/env python3
"""
EMA-RSI scalper  +  15-minute trend filter   –   Binance Spot TESTNET
====================================================================
• 1-minute entries **only** if 15-minute EMA-9 > EMA-21  (up-trend)
• Buy: EMA-3 > EMA-8   &   RSI-14 < 65
• LIMIT_MAKER one tick below bid  (30-s timeout ➜ cancel & retry)
• Stop / Target  : 1 × ATR(14)  /  2 × ATR(14)
• Log  : ema_log.csv      • State : state_ema.json
"""

import os, time, json, csv, hmac, hashlib, uuid
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime
from typing import Tuple

import pandas as pd, requests
from dotenv import load_dotenv

# ─── CONFIG ───────────────────────────────────────────────────────────
load_dotenv()
API_KEY, SECRET_KEY = os.getenv("BINANCE_KEY"), os.getenv("BINANCE_SECRET")
BASE_URL   = "https://testnet.binance.vision"

SYMBOL      = "BTCUSDT"
LTF_INT     = "1m"     # low-time-frame for signals
HTF_INT     = "15m"    # high-time-frame for trend filter
EMA_FAST_HTF = 9
EMA_SLOW_HTF = 21

POS_PCT    = 0.20      # % of bankroll per entry
USDT_ALLOC = 30_000
RSI_MAX    = 65
STOP_ATR   = 1.0       # stop  = 1 × ATR(14)
TP_ATR     = 2.0       # target = 2 × ATR(14)
MAKER_TTL  = 30        # seconds before cancel & retry

STATE_F = Path("state_ema.json")
LOG_F   = Path("ema_log.csv")
BOT_TAG = "EMA"
HEADERS = {"X-MBX-APIKEY": API_KEY}
# ──────────────────────────────────────────────────────────────────────

# ─── REST helpers ─────────────────────────────────────────────────────
def _ts(): return int(time.time()*1000)
def _sign(p): q=urlencode(p,doseq=True); s=hmac.new(SECRET_KEY.encode(),q.encode(),hashlib.sha256).hexdigest(); return f"{q}&signature={s}"
def _get(path, params=None, signed=False):
    url = f"{BASE_URL}{path}"
    if signed:
        params=params or {}; params["timestamp"]=_ts()
        url=f"{url}?{_sign(params)}"; params=None
    r=requests.get(url, params=params, headers=HEADERS, timeout=10); r.raise_for_status()
    return r.json()
def _req(method, path, params, signed=True):
    url=f"{BASE_URL}{path}"
    if signed:
        params["timestamp"]=_ts()
        url=f"{url}?{_sign(params)}"; params=None
    r=requests.request(method, url, headers=HEADERS, data=params, timeout=10); r.raise_for_status()
    return r.json()
post   = lambda p: _req("POST",   "/api/v3/order", p)
delete = lambda p: _req("DELETE", "/api/v3/order", p)
def tag(side): return f"{BOT_TAG}-{side}-{uuid.uuid4().hex[:6]}"
# ──────────────────────────────────────────────────────────────────────

# ─── exchange filters & quantisers ────────────────────────────────────
flt=_get("/api/v3/exchangeInfo", {"symbol": SYMBOL})["symbols"][0]["filters"]
STEP = float(next(f for f in flt if f["filterType"]=="LOT_SIZE")["stepSize"])
TICK = float(next(f for f in flt if f["filterType"]=="PRICE_FILTER")["tickSize"])
MIN_N= float(next(f for f in flt if f["filterType"] in ("NOTIONAL","MIN_NOTIONAL"))["minNotional"])
q_qty = lambda q: round(q//STEP*STEP, 6)
q_px  = lambda p: round(p//TICK*TICK, 2)
# ──────────────────────────────────────────────────────────────────────

# ─── market & math helpers ────────────────────────────────────────────
klines = lambda inter, n: [float(c[4]) for c in _get("/api/v3/klines", {"symbol":SYMBOL,"interval":inter,"limit":n})]
def balances() -> Tuple[float,float]:
    bal=_get("/api/v3/account",signed=True)["balances"]; d={b["asset"]:float(b["free"]) for b in bal}
    return d.get("USDT",0), d.get("BTC",0)
ema = lambda s,p: pd.Series(s).ewm(span=p,adjust=False).mean().iloc[-1]
def rsi(s,p=14):
    d=pd.Series(s).diff().dropna(); g=d.clip(lower=0); l=-d.clip(upper=0)
    ag=g.rolling(p).mean(); al=l.rolling(p).mean()
    return 100 if al.iloc[-1]==0 else 100-100/(1+ag.iloc[-1]/al.iloc[-1])
atr = lambda s,p=14: pd.Series(s).diff().abs().rolling(p).mean().iloc[-1]
def bid_price():  # best bid
    return float(_get("/api/v3/ticker/bookTicker", {"symbol":SYMBOL})["bidPrice"])
# ──────────────────────────────────────────────────────────────────────

# ─── state & log ──────────────────────────────────────────────────────
load = lambda: json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
save = lambda s: STATE_F.write_text(json.dumps(s))
def log(act, price, qty, pnl, usdt, btc):
    fresh = not LOG_F.exists()
    with LOG_F.open("a", newline="") as f:
        w=csv.writer(f); 
        if fresh: w.writerow(["ts","act","price","qty","pnl","usdt","btc"])
        w.writerow([datetime.utcnow().isoformat(timespec="seconds"),
                    act,f"{price:.2f}",f"{qty:.6f}",f"{pnl:.2f}",
                    f"{usdt:.2f}",f"{btc:.6f}"])
# ──────────────────────────────────────────────────────────────────────

def main():
    st=load()
    print("EMA-RSI HTF-filtered bot live –", datetime.utcnow().isoformat(timespec="seconds"))

    while True:
        try:
            # ── fetch low-TF & high-TF data ────────────────────────────
            ltf_closes = klines(LTF_INT, 50)
            htf_closes = klines(HTF_INT, max(EMA_SLOW_HTF, 60))

            price = ltf_closes[-1]
            ema3, ema8 = ema(ltf_closes,3), ema(ltf_closes,8)
            rsi14      = rsi(ltf_closes)
            atr_now    = atr(ltf_closes)

            # HTF trend direction
            ema9_htf  = ema(htf_closes, EMA_FAST_HTF)
            ema21_htf = ema(htf_closes, EMA_SLOW_HTF)
            trend_ok  = ema9_htf > ema21_htf      # Only long in up-trend

            usdt, btc = balances()

            # ── CANCEL expired maker ───────────────────────────────────
            if st.get("mode") == "pending" and time.time() > st["ttl"]:
                delete({"symbol":SYMBOL,"origClientOrderId": st["cid"], "recvWindow":5000})
                print("⌛ maker not filled → cancelled")
                st={}; save(st)

            # ── detect maker fill ─────────────────────────────────────
            if st.get("mode") == "pending":
                filled = balances()[1] - st["btc_before"]
                if filled >= st["qty"]*0.99:
                    stop = st["limit"] - STOP_ATR*atr_now
                    tp   = st["limit"] + TP_ATR*atr_now
                    st={"mode":"live","entry":st["limit"],"qty":filled,"stop":stop,"tp":tp}
                    save(st); log("BUY",st["entry"],filled,0,*balances())
                    print(f"✅ FILLED {filled:.6f}@{st['entry']}")

            # ── ENTRY conditions ──────────────────────────────────────
            if st.get("mode") is None and trend_ok and ema3>ema8 and rsi14<RSI_MAX:
                spend = min(usdt, USDT_ALLOC)*POS_PCT
                if spend >= MIN_N:
                    qty = q_qty(spend/price)
                    limit = q_px(bid_price() - TICK)
                    cid = tag("BUY")
                    post({"symbol":SYMBOL,"side":"BUY","type":"LIMIT_MAKER",
                          "price":f"{limit:.2f}","quantity":f"{qty:.6f}",
                          "newClientOrderId":cid,"recvWindow":5000})
                    print(f"⏳ maker {qty:.6f}@{limit}")
                    st={"mode":"pending","cid":cid,"limit":limit,
                        "qty":qty,"btc_before":btc,"ttl":time.time()+MAKER_TTL}
                    save(st)

            # ── EXIT logic ────────────────────────────────────────────
            if st.get("mode") == "live":
                entry, qty, stop, tp = st["entry"], st["qty"], st["stop"], st["tp"]
                # optional exit if HTF trend flips
                trend_now_long = ema9_htf > ema21_htf
                exit_signal = price<=stop or price>=tp or (ema3<ema8 and rsi14>40) or not trend_now_long
                if exit_signal:
                    post({"symbol":SYMBOL,"side":"SELL","type":"MARKET",
                          "quantity":f"{q_qty(qty):.6f}",
                          "newClientOrderId":tag("SELL"),"recvWindow":5000})
                    pnl=(price-entry)*qty
                    log("SELL",price,qty,pnl,*balances())
                    print(f"✅ EXIT {qty:.6f}@{price} | PnL {pnl:.2f}")
                    st={}; save(st)

            # ── heartbeat ────────────────────────────────────────────
            debug=(f"HTF {'UP' if trend_ok else 'FLAT/↓'}  "
                   f"ATR {atr_now:4.1f}  SLΔ {(STOP_ATR*atr_now):4.1f}  "
                   f"TPΔ {(TP_ATR*atr_now):4.1f}  RSI {rsi14:5.1f}")
            status="LONG" if st.get("mode")=="live" else "----"
            print(f"{datetime.utcnow():%H:%M:%S} | Px {price:,.2f} | "
                  f"{status} | {debug}", end="\r")

        except Exception as e:
            print("\n❌", e)

        time.sleep(5)

if __name__=="__main__":
    main()
