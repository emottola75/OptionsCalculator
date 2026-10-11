# Option P&L Calculator

An interactive, browser-based calculator that shows what an equity option position could be worth **before expiration**, for a range of future underlying prices and dates. It returns the cost of the position and a colour-coded profit/loss matrix: shades of green for gains, shades of red for losses.

Market data can be fetched from **Yahoo Finance** (no account needed) or from **Interactive Brokers** (through your own TWS / IB Gateway).

<!-- Add a screenshot here, e.g. ![Screenshot](docs/screenshot.png) -->

> **Disclaimer:** This is an educational tool, not financial advice. Model outputs are theoretical estimates and can differ materially from real market prices. Not affiliated with or endorsed by Interactive Brokers or Yahoo. Use at your own risk.

## Features

- Choose underlying, call/put, long/short, expiration, strike and number of contracts, with strikes and expirations taken from the real option chain
- Cost of the position: net debit or credit, breakeven at expiry, max loss and max gain
- P&L matrix of **underlying price (rows) x date (columns)**, symmetric green/red colour scale centred on zero
- Customisable price range (percent below/above spot), number of price rows and number of date columns
- Display in currency or as a percentage of the cost
- Model Greeks (delta, gamma, vega, theta)
- American or European exercise
- Switchable data source: Yahoo Finance or Interactive Brokers
- With Interactive Brokers: IBKR's own model price, implied volatility and Greeks shown next to this app's calculation, as a cross-check

## How it works

### Pricing model
Option values are computed with a **Cox-Ross-Rubinstein binomial tree** (default 200 steps, adjustable):

- American exercise by default (early exercise checked at every node); a switch selects European exercise (e.g. index options)
- Dividends are approximated by a **continuous dividend yield**
- Greeks are finite-difference estimates from the same tree: delta and gamma from a 1% spot bump, vega per 1 volatility point, theta per calendar day

This is a standard approach for listed equity options. It is **not guaranteed to reproduce Interactive Brokers' internal numbers exactly**; the IBKR mode shows both side by side so you can see the difference.

### P&L definition
For each cell (future price S, date t):

```
value(S, t) = model option value with remaining time = expiry - t, spot = S
P&L         = sign * value * contracts * multiplier - entry cost
```

where `sign` is +1 for long and -1 for short. At the expiry column the value is the intrinsic value.

## Assumptions and limitations

- **Implied volatility, risk-free rate and dividend yield are held constant** across the whole matrix. Real IV changes with price and time ("vol smile" and "vol crush"), which can move real P&L substantially, especially around earnings.
- Entry cost uses the selected premium source (ask for long, bid for short, mid, last, model or manual) plus an entry commission (default 0.65 per contract, editable). **Exit commissions, taxes, FX, margin/financing costs and exit bid-ask spread are not included.**
- Risk-free rate defaults to the 13-week US T-bill (`^IRX` from Yahoo) for both data sources, and is editable. For non-USD underlyings, set it manually.
- Dividend yield defaults to trailing 12-month dividends divided by spot (Yahoo) or IBKR's 12-month dividend figure; discrete dividends and ex-dividend dates are not modelled.
- Yahoo data is delayed, can be stale, and its implied volatility is often unreliable for illiquid strikes. Override IV when in doubt.
- The IBKR mode supports **stocks and ETFs** as underlying (not indices or futures options).
- The IBKR integration was developed and tested against mocked data; feedback from real TWS/Gateway setups is welcome via issues.
- Single-leg options only; no multi-leg strategies.
- Calendar-day count with a 365-day year.

## Installation

Requires Python 3.10+.

```bash
git clone https://github.com/<your-user>/<your-repo>.git
cd <your-repo>
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/WSL/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
streamlit run app.py
```

Then open <http://localhost:8501> and work through the sidebar:

0. **Data source**: Yahoo Finance or Interactive Brokers
1. **Contract**: ticker, call/put, long/short, number of contracts, then expiration and strike
2. **Model inputs**: IV, risk-free rate, dividend yield, American/European, tree steps (pre-filled; edit freely)
3. **Entry price**: premium source and commission
4. **Matrix range**: lowest/highest price vs spot, number of rows and columns, $ or % view

Use **Refresh market data** to bypass the short-lived cache.

### Using Interactive Brokers data

1. Start **TWS** or **IB Gateway** and log in.
2. Enable the API: *Global Configuration > API > Settings > Enable ActiveX and Socket Clients*. Reading is enough, so you can tick "Read-Only API".
3. In the app sidebar choose *Interactive Brokers*, then set host, port and client ID.

| Application | Live port | Paper port |
|---|---|---|
| TWS | 7496 | 7497 |
| IB Gateway | 4001 | 4002 |

- The app connects **read-only** and never places orders.
- Market data type defaults to "Delayed if no live subscription": you get live data if you are subscribed, delayed otherwise.
- **WSL users:** `127.0.0.1` inside WSL does not reach a TWS running on Windows. Easiest option: run the app from native Windows Python. Otherwise use WSL mirrored networking, or enter the Windows host IP and add it under *Trusted IPs* in the TWS API settings.

## Project structure

```
app.py             Streamlit app: pricing engine, data adapters, UI
requirements.txt   Python dependencies
LICENSE            MIT
```

## Built with

[Streamlit](https://streamlit.io), [Plotly](https://plotly.com/python/), [yfinance](https://github.com/ranaroussi/yfinance), [ib_async](https://github.com/ib-api-reloaded/ib_async), NumPy, pandas.

Note on data: `yfinance` is an open-source tool that uses Yahoo's publicly available APIs and is intended for personal and research use; check Yahoo's terms before any other use. Interactive Brokers data is subject to your own market data agreements.

## Possible improvements

- Implied-volatility scenarios (shift IV across the matrix)
- Discrete dividends and a proper rate curve
- Multi-leg strategies (spreads, straddles, covered calls)
- Index and futures options for the IBKR source
- Export of the matrix to CSV/Excel

## License

Released under the [MIT License](LICENSE). The MIT license is permissive and compatible with all the dependencies listed above; if you want an explicit patent grant, Apache-2.0 is a reasonable alternative.

## Contributing

Issues and pull requests are welcome, especially validation reports comparing the output with IBKR's own option analytics.
