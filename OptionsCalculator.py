"""
Option P&L calculator
---------------------
- Market data from Yahoo Finance (yfinance): spot, option chain, IV, dividends, risk-free proxy
- Pricing: Cox-Ross-Rubinstein binomial tree, American exercise (US equity options),
  continuous dividend yield approximation. Set exercise style to European for index options.
- Output: cost of the position + P&L matrix (underlying price x date), green = gain, red = loss.

Data sources (switch in the sidebar):
  * Yahoo Finance  - no account needed, delayed, IV often unreliable.
  * Interactive Brokers - via a running TWS / IB Gateway (API enabled), using the `ib_async` package.
    Gives IBKR's own quotes, model IV and model price/Greeks, shown next to this app's calculation.

Run:  streamlit run app.py
"""
import asyncio
import math
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Option P&L Calculator", layout="wide")


# ----------------------------------------------------------------------------
# Pricing engine
# ----------------------------------------------------------------------------
def crr_price(S, K, T, r, q, sigma, is_call, american=True, steps=200):
    """CRR binomial tree, vectorised over an array of spot prices."""
    S = np.atleast_1d(np.asarray(S, dtype=float))
    sign = 1.0 if is_call else -1.0

    def payoff(x):
        return np.maximum(sign * (x - K), 0.0)

    if T <= 0 or sigma <= 0:
        return payoff(S)

    n = int(steps)
    dt_ = T / n
    sq = sigma * math.sqrt(dt_)
    u = math.exp(sq)
    d = 1.0 / u
    disc = math.exp(-r * dt_)
    p = (math.exp((r - q) * dt_) - d) / (u - d)
    p = min(max(p, 0.0), 1.0)

    j = np.arange(n + 1)
    V = payoff(S[:, None] * np.exp(sq * (2 * j - n)))
    for i in range(n - 1, -1, -1):
        V = disc * (p * V[:, 1:] + (1 - p) * V[:, :-1])
        if american:
            Si = S[:, None] * np.exp(sq * (2 * np.arange(i + 1) - i))
            V = np.maximum(V, payoff(Si))
    return V[:, 0]


def greeks(S, K, T, r, q, sigma, is_call, american, steps):
    f = lambda s=S, t=T, v=sigma: float(crr_price(s, K, t, r, q, v, is_call, american, steps)[0])
    h = S * 0.01
    p0, pu, pd_ = f(), f(s=S + h), f(s=S - h)
    delta = (pu - pd_) / (2 * h)
    gamma = (pu - 2 * p0 + pd_) / h ** 2
    vega = (f(v=sigma + 0.01) - p0)            # per 1 vol point
    theta = (f(t=max(T - 1 / 365, 0)) - p0)    # per calendar day
    return delta, gamma, vega, theta


# ----------------------------------------------------------------------------
# Market data - common helpers
# ----------------------------------------------------------------------------
YAHOO, IBKR = "Yahoo Finance", "Interactive Brokers (TWS / Gateway)"
MKT_TYPES = {"Delayed if no live subscription": 3, "Live": 1, "Frozen": 2, "Delayed-frozen": 4}


def _ok(x):
    """Valid positive float or None (IB uses nan / -1 for 'no data')."""
    try:
        x = float(x)
        return x if x == x and x > 0 else None
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def get_risk_free():
    """13-week US T-bill (^IRX) as a proxy, decimal (0.043 = 4.3%). Used for both sources."""
    try:
        v = yf.Ticker("^IRX").history(period="5d")["Close"].dropna().iloc[-1]
        return float(v) / 100.0
    except Exception:
        return 0.04


# ----------------------------------------------------------------------------
# Market data - Yahoo
# ----------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def yahoo_snapshot(symbol):
    tk = yf.Ticker(symbol)
    try:
        spot = float(tk.fast_info["last_price"])
    except Exception:
        spot = float(tk.history(period="5d")["Close"].dropna().iloc[-1])
    try:   # trailing-12m dividends / spot (robust to yfinance unit changes)
        div = tk.dividends
        cutoff = pd.Timestamp.now(tz=div.index.tz) - pd.Timedelta(days=365)
        dy = float(div[div.index >= cutoff].sum() / spot) if not div.empty else 0.0
    except Exception:
        dy = 0.0
    return dict(spot=spot, div_yield=dy, expirations=list(tk.options), strikes=None, multiplier=100, chain_info="")


@st.cache_data(ttl=300, show_spinner=False)
def yahoo_chain(symbol, expiry, kind):
    ch = yf.Ticker(symbol).option_chain(expiry)
    return (ch.calls if kind == "Call" else ch.puts).copy()


def yahoo_quote(chain, strike):
    row = chain[chain["strike"] == strike].iloc[0]
    iv = _ok(row.get("impliedVolatility"))
    return dict(bid=_ok(row.get("bid")), ask=_ok(row.get("ask")), last=_ok(row.get("lastPrice")),
                iv=iv if iv and iv > 0.01 else None, volume=row.get("volume"),
                oi=row.get("openInterest"), ibkr=None)


# ----------------------------------------------------------------------------
# Market data - Interactive Brokers (ib_async; falls back to ib_insync)
# ----------------------------------------------------------------------------
def _ib_imports():
    try:
        from ib_async import IB, Option, Stock
    except ImportError:
        try:
            from ib_insync import IB, Option, Stock
        except ImportError:
            raise RuntimeError("Package 'ib_async' is not installed:  pip install ib_async")
    return IB, Option, Stock


def _ib_connect(host, port, client_id, mkt_type):
    IB, _, _ = _ib_imports()
    # Streamlit runs scripts in a worker thread with no asyncio loop -> give it one.
    asyncio.set_event_loop(asyncio.new_event_loop())
    ib = IB()
    try:
        ib.connect(host, int(port), clientId=int(client_id), readonly=True, timeout=10)
    except Exception as e:
        raise RuntimeError(
            f"Cannot connect to TWS/Gateway at {host}:{port} ({type(e).__name__}). Check that it is running, "
            "API is enabled (Global Configuration > API > Settings), the port matches and, if the app runs "
            "in WSL while TWS runs in Windows, the host/trusted-IP settings allow it.") from e
    ib.reqMarketDataType(mkt_type)
    return ib


def _wait(ib, cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        ib.sleep(0.25)


def _ib_pick_chain(chains, symbol):
    pref = [c for c in chains if c.exchange == "SMART" and c.tradingClass == symbol]
    pref = pref or [c for c in chains if c.exchange == "SMART"] or list(chains)
    return max(pref, key=lambda c: len(c.expirations))


@st.cache_data(ttl=120, show_spinner="Contacting Interactive Brokers...")
def ibkr_snapshot(symbol, host, port, client_id, mkt_type, currency):
    _, _, Stock = _ib_imports()
    ib = _ib_connect(host, port, client_id, mkt_type)
    try:
        stk = Stock(symbol, "SMART", currency)
        if not ib.qualifyContracts(stk):
            raise RuntimeError(f"IBKR could not find stock/ETF {symbol} in {currency}.")
        t = ib.reqMktData(stk, "456", False, False)      # 456 = dividend info
        _wait(ib, lambda: _ok(t.marketPrice()) is not None, 8)
        ib.sleep(1)
        spot = _ok(t.marketPrice()) or _ok(t.close)
        if spot is None:
            raise RuntimeError("No price received (market data subscription / market data type?).")
        dy = 0.0
        d = getattr(t, "dividends", None)
        if d is not None and _ok(getattr(d, "past12Months", None)):
            dy = float(d.past12Months) / spot
        ib.cancelMktData(stk)
        chains = ib.reqSecDefOptParams(stk.symbol, "", stk.secType, stk.conId)
        if not chains:
            raise RuntimeError(f"No option chain returned for {symbol}.")
        ch = _ib_pick_chain(chains, symbol)
        today = pd.Timestamp.now().normalize()
        exps = [f"{e[:4]}-{e[4:6]}-{e[6:]}" for e in sorted(ch.expirations)]
        exps = [e for e in exps if pd.Timestamp(e) >= today]
        strikes = sorted(float(s) for s in ch.strikes if 0.5 * spot <= float(s) <= 1.5 * spot)
        return dict(spot=spot, div_yield=dy, expirations=exps, strikes=strikes,
                    multiplier=int(float(ch.multiplier or 100)),
                    chain_info=dict(exchange=ch.exchange, tradingClass=ch.tradingClass))
    finally:
        ib.disconnect()


@st.cache_data(ttl=60, show_spinner="Fetching option quote from Interactive Brokers...")
def ibkr_quote(symbol, expiry, kind, strike, host, port, client_id, mkt_type, currency, exchange, trading_class, mult):
    _, Option, _ = _ib_imports()
    ib = _ib_connect(host, port, client_id, mkt_type)
    try:
        opt = Option(symbol, expiry.replace("-", ""), float(strike), "C" if kind == "Call" else "P",
                     exchange, str(mult), currency, tradingClass=trading_class)
        if not ib.qualifyContracts(opt):
            raise RuntimeError("IBKR does not list this strike for this expiry - pick another strike.")
        t = ib.reqMktData(opt, "106", False, False)      # 106 = option implied vol
        _wait(ib, lambda: t.modelGreeks is not None, 10)
        ib.sleep(1.5)
        mg = t.modelGreeks
        ibkr = None
        if mg is not None:
            ibkr = dict(price=mg.optPrice, iv=mg.impliedVol, delta=mg.delta, gamma=mg.gamma,
                        vega=mg.vega, theta=mg.theta, und=mg.undPrice)
        iv = _ok(mg.impliedVol) if mg is not None else None
        iv = iv or _ok(t.impliedVolatility)
        oi = t.callOpenInterest if kind == "Call" else t.putOpenInterest
        q = dict(bid=_ok(t.bid), ask=_ok(t.ask), last=_ok(t.last), iv=iv,
                 volume=_ok(t.volume), oi=_ok(oi), ibkr=ibkr)
        ib.cancelMktData(opt)
        return q
    finally:
        ib.disconnect()


# ----------------------------------------------------------------------------
# UI - inputs
# ----------------------------------------------------------------------------
st.title("Option P&L calculator")

with st.sidebar:
    st.header("0. Data source")
    source = st.radio("Fetch data from", [YAHOO, IBKR], label_visibility="collapsed")
    conn = None
    if source == IBKR:
        host = st.text_input("TWS / Gateway host", "127.0.0.1")
        port = st.number_input("API port", 1, 65535, 7497,
                               help="TWS live 7496, TWS paper 7497, IB Gateway live 4001, Gateway paper 4002.")
        client_id = st.number_input("Client ID", 0, 999, 17)
        mkt_label = st.selectbox("Market data type", list(MKT_TYPES))
        ccy = st.text_input("Underlying currency", "USD").strip().upper()
        conn = (host, int(port), int(client_id), MKT_TYPES[mkt_label], ccy)
        st.caption("Read-only connection. Stocks/ETFs as underlying.")
    if st.button("Refresh market data"):
        st.cache_data.clear()

    st.header("1. Contract")
    symbol = st.text_input("Underlying ticker", "AAPL").strip().upper()
    kind = st.radio("Type", ["Call", "Put"], horizontal=True)
    side = st.radio("Position", ["Long (buy)", "Short (sell)"], horizontal=True)
    n_contracts = st.number_input("Number of contracts", 1, 1000, 1)

if not symbol:
    st.stop()

try:
    snap = yahoo_snapshot(symbol) if source == YAHOO else ibkr_snapshot(symbol, *conn)
except Exception as e:
    st.error(f"Could not fetch data for {symbol} from {source}: {e}")
    st.stop()

spot, expirations = snap["spot"], snap["expirations"]
if not expirations:
    st.error("No listed options found for this ticker.")
    st.stop()

with st.sidebar:
    multiplier = st.number_input("Multiplier (shares/contract)", 1, 1000, int(snap["multiplier"]))
    expiry = st.selectbox("Expiration date", expirations)
    try:
        if source == YAHOO:
            chain = yahoo_chain(symbol, expiry, kind)
            strikes = chain["strike"].tolist()
        else:
            strikes = snap["strikes"]
        if not strikes:
            raise RuntimeError("no strikes available")
        strike = st.selectbox("Strike", strikes, index=int(np.argmin(np.abs(np.array(strikes) - spot))))
        if source == YAHOO:
            quote = yahoo_quote(chain, strike)
        else:
            ci = snap["chain_info"]
            quote = ibkr_quote(symbol, expiry, kind, strike, *conn, ci["exchange"], ci["tradingClass"], multiplier)
    except Exception as e:
        st.error(f"Could not fetch the option quote: {e}")
        st.stop()

    st.header("2. Model inputs")
    american = st.checkbox("American exercise (equity options)", True,
                           help="Untick for European-style (e.g. index options such as SPX).")
    iv_default = quote["iv"] * 100 if quote["iv"] else 30.0
    iv = st.number_input("Implied volatility (%)", 1.0, 500.0, round(iv_default, 2), 0.5,
                         help="Pre-filled from the selected source (IBKR: model IV). Override if needed.") / 100
    r = st.number_input("Risk-free rate (%)", -2.0, 15.0, round(get_risk_free() * 100, 3), 0.05,
                        help="Defaults to the 13-week US T-bill from Yahoo (^IRX) for both sources.") / 100
    q = st.number_input("Dividend yield (%)", 0.0, 20.0, round(snap["div_yield"] * 100, 3), 0.05) / 100
    steps = st.slider("Binomial steps", 50, 1000, 200, 50)

    st.header("3. Entry price")
    bid, ask, last = quote["bid"] or 0.0, quote["ask"] or 0.0, quote["last"] or 0.0
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
    is_long = side.startswith("Long")
    days_now = max((pd.Timestamp(expiry) - pd.Timestamp.now().normalize()).days, 0)
    model_now = float(crr_price(spot, strike, days_now / 365, r, q, iv, kind == "Call", american, steps)[0])
    entry_mode = st.radio("Premium source", ["Ask/Bid (realistic)", "Mid", "Last", "Model", "Manual"])
    premium = {"Ask/Bid (realistic)": ask if is_long else bid, "Mid": mid, "Last": last, "Model": model_now}.get(entry_mode)
    if entry_mode == "Manual" or not premium:
        premium = st.number_input("Premium per share", 0.0, 100000.0, round(mid or model_now, 2), 0.05)
    commission = st.number_input("Commission per contract (entry)", 0.0, 50.0, 0.65, 0.05,
                                 help="IBKR Pro tiered US options is about $0.65/contract; exit commission not included.")

    st.header("4. Matrix range")
    lo_pct = st.number_input("Lowest price vs spot (%)", -99, 0, -30)
    hi_pct = st.number_input("Highest price vs spot (%)", 0, 1000, 30)
    n_rows = st.slider("Price rows", 5, 51, 21, 2)
    n_cols = st.slider("Date columns", 3, 20, 10)
    view = st.radio("Show", ["P&L ($)", "P&L (% of cost)"], horizontal=True)

# ----------------------------------------------------------------------------
# Calculations
# ----------------------------------------------------------------------------
is_call = kind == "Call"
sgn = 1 if is_long else -1
today = pd.Timestamp.now().normalize()
exp_ts = pd.Timestamp(expiry)
days_total = max((exp_ts - today).days, 0)
unit = n_contracts * multiplier
cost = sgn * premium * unit + commission * n_contracts     # >0 = debit, <0 = credit

prices = np.linspace(spot * (1 + lo_pct / 100), spot * (1 + hi_pct / 100), n_rows)
day_offsets = np.unique(np.round(np.linspace(0, days_total, n_cols)).astype(int))
dates = [today + pd.Timedelta(days=int(d)) for d in day_offsets]

pnl = np.zeros((len(prices), len(dates)))
for c, d in enumerate(day_offsets):
    T = (days_total - d) / 365
    val = crr_price(prices, strike, T, r, q, iv, is_call, american, steps)
    pnl[:, c] = sgn * val * unit - cost

# ----------------------------------------------------------------------------
# UI - results
# ----------------------------------------------------------------------------
T0 = days_total / 365
delta, gamma, vega, theta = greeks(spot, strike, T0, r, q, iv, is_call, american, steps)
be = strike + premium if is_call else strike - premium
st.subheader(f"{symbol} {expiry} {strike:g} {kind}  -  {side}  x{n_contracts}")
st.caption(f"Data source: {source}" + (f"  |  chain: {snap['chain_info']['exchange']} / {snap['chain_info']['tradingClass']}"
                                       if source == IBKR else ""))
c1, c2, c3, c4 = st.columns(4)
c1.metric("Underlying", f"{spot:,.2f}")
c2.metric("Premium / share", f"{premium:,.2f}")
c3.metric("Net cost" if cost >= 0 else "Net credit", f"{abs(cost):,.2f}")
c4.metric("Breakeven at expiry", f"{be:,.2f}")

c5, c6, c7, c8 = st.columns(4)
c5.metric("Model value / share (today)", f"{model_now:,.2f}")
c6.metric("Delta / Gamma", f"{sgn * delta:,.3f} / {sgn * gamma:,.4f}")
c7.metric("Vega (per 1 vol pt) / contract", f"{sgn * vega * multiplier:,.2f}")
c8.metric("Theta (per day) / contract", f"{sgn * theta * multiplier:,.2f}")

if is_long:
    st.caption(f"Max loss: {cost:,.2f} (premium + commission)  |  Max gain: "
               + ("unlimited" if is_call else f"{(strike * unit - cost):,.2f}"))
else:
    st.caption(f"Max gain: {abs(cost):,.2f} (credit)  |  Max loss: "
               + ("unlimited" if is_call else f"{(strike * unit + cost):,.2f}"))

if "%" in view:
    z = pnl / abs(cost) * 100 if cost else pnl * 0
    txt = [[f"{v:+.0f}%" for v in rw] for rw in z]
else:
    z = pnl
    txt = [[f"{v:+,.0f}" for v in rw] for rw in z]

m = float(np.nanmax(np.abs(z))) or 1.0
colorscale = [[0.0, "#7f0000"], [0.25, "#e57373"], [0.5, "#f5f5f5"], [0.75, "#81c784"], [1.0, "#1b5e20"]]
ylabels = [f"{p:,.2f} ({(p / spot - 1) * 100:+.0f}%)" for p in prices]
xlabels = [f"{d:%d %b %y}<br>({(days_total - o)}d left)" for d, o in zip(dates, day_offsets)]

fig = go.Figure(go.Heatmap(
    z=z[::-1], x=xlabels, y=ylabels[::-1], text=np.array(txt)[::-1], texttemplate="%{text}",
    colorscale=colorscale, zmin=-m, zmax=m, zmid=0, xgap=1, ygap=1,
    colorbar=dict(title="P&L"), hovertemplate="Price %{y}<br>%{x}<br>P&L %{text}<extra></extra>"))
fig.update_layout(height=max(450, 26 * n_rows + 150), yaxis=dict(title="Underlying price", type="category"),
                  xaxis=dict(title="Date (time to expiration)", type="category", side="top"),
                  margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, width="stretch")

if quote["ibkr"]:
    m_ = quote["ibkr"]
    f_ = lambda v, d=3: "n/a" if v is None or v != v else f"{v:,.{d}f}"
    st.info(
        f"**IBKR model (per share):** price {f_(m_['price'], 2)} | IV {f_(m_['iv'] * 100 if m_['iv'] else None, 1)}% | "
        f"delta {f_(m_['delta'])} | gamma {f_(m_['gamma'], 4)} | vega {f_(m_['vega'])} | theta {f_(m_['theta'])}   \n"
        f"**This app (CRR, same inputs as above):** price {model_now:,.2f} | delta {delta:,.3f} | gamma {gamma:,.4f} | "
        f"vega {vega:,.3f} | theta {theta:,.3f}  \n"
        "Differences come from the tree vs IBKR's internal model, dividend treatment and the interest-rate curve.")

with st.expander("Option quote and assumptions"):
    st.write(f"Bid {bid:.2f} | Ask {ask:.2f} | Last {last:.2f} | Volume {quote['volume']} | "
             f"Open interest {quote['oi']} | Source IV {(quote['iv'] or 0) * 100:.1f}%")
    st.markdown(
        "- Matrix values are **model values** (CRR binomial) at each date, holding IV, rates and dividend yield constant.\n"
        "- Exit commissions, taxes, FX, bid/ask spread at exit, and IV changes are not included.\n"
        "- Quotes may be delayed; check against your live IBKR ticket before trading.")
