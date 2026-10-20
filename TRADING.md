# JARVIS Trading Control Centre

This extends the existing local assistant. Its main voice circle, desktop tools, memory and British speech remain available. Open the menu → **Trading** for the control centre.

## Installed TradingView application

**TradingView Desktop is the default target.** On your Windows PC, open the installed TradingView application, sign in normally, open its Trading Panel and select Paper Trading. In JARVIS choose **Connect desktop**. JARVIS uses its existing installed-application index, foreground/window verification, Windows accessibility and screenshot capture; it does not open a replacement website or a separate Playwright login profile.

The account check requires the actual `TradingView.exe` window and an observed selected Paper Trading account. An account-menu option, window title, model assertion, old screenshot, or client-supplied `verified: true` is not proof. Unknown funds, positions, currency and orders stay unknown. Connection observations expire after 30 seconds; a cached observation cannot authorize orders.

Connect first attaches an already-open native TradingView window, then uses fresh observed controls for **Trading Panel → Paper Trading → Connect** when available. A generic broker Connect button is insufficient: it must belong to an explicit Paper Trading container. Connection navigation performs at most four observed control clicks and never clicks order controls or repeats an input after uncertain delivery. Missing/inaccessible controls leave connection unverified. A failed accessibility query does not discard other readable account information or prevent a separate screenshot attempt; partially readable balances remain partial.

If the app opens but connection does not finish, expand **Connection checks**. It separates application identity, accessibility, Paper selection and account reading. **Download connection report** saves the measured status, attempted connection actions and bounded observed control names; it performs no new clicks or orders. **View captured TradingView window** opens the last available local screenshot. These let the installed application's actual interface guide subsequent compatibility fixes. Reports/images stay on your computer unless you choose to share them.

**Native TradingView order placement, bracket submission, position synchronisation and fill reconciliation are not enabled.** They have not been demonstrated against the installed application. Starting PAPER with this backend reports that limitation and stays OFF. Connecting or observing Paper Trading does not imply the order transport is validated. No real-money broker or undocumented TradingView order API is implemented.

The cloud environment has no authenticated Windows/TradingView session. On 20 October 2026, its proxy denied requests to TradingView documentation before reaching TradingView. That does not establish that TradingView prohibits automation. Current permission and native order-form compatibility remain unverified. The [official policies](https://www.tradingview.com/policies/) and [Paper Trading help](https://www.tradingview.com/support/solutions/43000473924-paper-trading-main-functionality/) must be checked before adding execution. No phase requiring a real native account/order is marked complete below.

## Independent paper simulator

The requested fallback is available as a separate, explicit backend choice while OFF. It never impersonates or synchronises with a TradingView account. It starts with configurable **100,000 USD simulated equity**; that is an initial simulation parameter, not an observed account balance or claimed profit. Simulator history retains its provenance even if you later select the desktop backend.

NQ means CME **E-mini Nasdaq-100**, $20 per index point, 0.25-point tick, integer contracts. ES means E-mini S&P 500 and is used only as a synchronised SMT reference. The service refuses ES orders, fractional NQ contracts, micros, brokers and model-supplied account-verification flags. Contract expiry/roll treatment must match the data source; the system does not invent roll-adjusted futures history.

There is **no bundled real-time or historical market-data entitlement/provider**. Import OHLCV you are entitled to use, with its source identified. Historical imports enable research only. Automated independent paper entries require a separately supplied, current authenticated local feed. No public webhook, account credentials, remote broker or exposed control endpoint is enabled. The existing local session, same-origin and client-header checks apply to trading routes.

## Data format and feeding

CSV columns: `time,open,high,low,close,volume`. `timestamp` is an accepted alias for `time`; volume is optional and stays unknown when omitted. Example header and timestamp format:

```csv
time,open,high,low,close,volume
```

Each row's time must be the **completed candle close** as an ISO 8601 timestamp with timezone (for example `2026-01-06T14:35:00Z`). Those are format examples, not market prices. A native export using bar-open times or Unix timestamps must be explicitly converted using the known timeframe/timezone before import. JARVIS does not guess timestamp semantics. Select NQ or ES, the actual timeframe and a genuine source in the import form.

Rows must be strictly increasing, finite, completed and consistent OHLC. Gaps are reported, not filled. Duplicate existing rows are idempotent; differing data at an existing timestamp is rejected without changing history. Historical and live datasets are separate. Future timestamps, malformed/naive dates and incomplete candles are rejected. Imports allow up to 20,000 candles/8 MB at a time.

An authorised local data adapter can POST `{"symbol":"NQ","kind":"live","source":"your licensed source","timeframe_minutes":5,"candles":[...]}` to `/api/trading/data` using the same dashboard session and `X-Jarvis-Client: dashboard`. ES uses a separate stream. This is an integration contract, not a ready provider connection; never publish the server or disable its access checks to receive external TradingView webhooks. Historical data cannot be relabelled as a fresh feed by the model tools.

## Deterministic decisions

The initial timeframe is configurable **5 minutes**. Higher-timeframe 1h/4h candles require every completed constituent candle. Liquidity includes previous completed higher-timeframe levels and completed session highs/lows. Initial swings use upward/downward two-candle transitions with configurable sensitivity. Sweeps must exceed levels; a later BOS must close beyond prior structure. A qualifying retracement must reach enabled FVG/IFVG/equilibrium confluence; merely displacing through a level is insufficient. IFVG needs opposite close-through plus later retest. Optional Wilder RSI divergence and synchronised ES SMT can be made mandatory.

Every evaluation records each condition's pass/fail reason, a 0–100 rating and observed evidence. Mandatory conditions cannot be replaced by a model's confidence. Stable sweep/BOS/version IDs prevent repeat submission while allowing rejected/monitor-only setups to qualify later.

London defaults to **08:00 Europe/London**, New York to **09:30 America/New_York**. Entries are permitted from 30 minutes before to 120 minutes after these openings, weekdays. These are configurable reference openings, not the entire nearly 24-hour CME futures session. IANA timezone data, including Windows' installed `tzdata`, handles DST. Existing brackets can be managed after entry windows close. A historical candle cannot prove the exact intrabar touch time, so fills use documented conservative session assumptions.

Defaults: 0.5% equity risk per trade, 2% daily loss, 5% weekly loss, 2 simultaneous positions including pending exposure, 2.5R target. Stops and targets align to NQ ticks. Sizing includes spread, two slippage legs, commissions, exposure and remaining loss budget; fills recheck those limits at the actual simulated entry. No martingale, increased percentage risk after losses or averaging into losing NQ positions is enabled.

Maximum notional exposure initially defaults to 200% of equity. A single NQ at 20,000 represents $400,000 notional, so a $100,000 simulator account with this cap cannot take even one contract. That rejection is intentional; configure an appropriate explicit notional cap or initial simulation equity rather than accepting fractional contracts or silently relaxing controls. This is not an exchange margin model. Default zero spread/slippage/commission inputs are **zero-cost assumptions**, not observed free trading; configure realistic costs before interpreting results.

## Modes, stops and restart

- **OFF:** no new automated entries; unfilled simulator orders are cancelled. Existing simulator positions retain bracket management with available live bars.
- **MONITOR:** evaluate current data and save qualifying/rejected setups; submit no orders.
- **PAPER:** submit only deterministic, risk-validated independent simulated entries when that backend was explicitly selected and data is fresh. Desktop PAPER remains unavailable pending validation.
- **BACKTEST:** historical analysis, with no TradingView or live simulator orders.

Emergency stop is persistent, cancels pending independent simulator orders and can optionally close simulator positions using a current observed price. It does not claim unverified native TradingView cancellations/closures succeeded. The Windows global emergency hotkey also latches the trading stop. Reset requires an explicit user request/button; it leaves entries OFF. A missing price retains positions rather than fabricating a closure. OFF/stop requests invalidate older model workflows so they cannot restart trading later.

Positive “stop trading”/“pause trading for today” commands run locally without a model request; recommendation questions, negation and conditional instructions are not treated as immediate stops. Busy voice has a separate stop callback. A new explicit start is still needed after stopping. Restart always suspends entries and cancels pending simulator entries; it never replays orders or assumes a desktop session survived. The optional simulator manages recorded positions only from real stored subsequent bars and suspends on missing reconciliation data.

“Today” and paper report/loss calendar boundaries default to **Australia/Sydney**, configurable through `report_timezone`. Backtest loss windows use UTC day/ISO-week boundaries, stated in each report. Session entry rules always use the chosen London/New York zones. The frontend displays dates in your computer/browser's local timezone.

## Journal, backtests and research

Paper and historical runs have separate journal datasets and globally distinct run IDs. Every closed trade records its version, setup evidence/conditions, execution prices/times, NQ contracts, brackets, session, rating, planned and realised R, gross P/L, explicit costs, net P/L, duration and exit events/reason. Rejected setups are retained. No actual TradingView history is imported or fabricated. Search the journal or export CSV/PDF.

Backtests replay only supplied completed history. Decisions see a completed prefix; orders fill no earlier than the next observed bar. Same-bar stop/target collisions resolve stop first; stop gaps fill adversely, favourable target gaps are capped at the target. Known costs apply to both sides. Break-even/trailing changes take effect on the following candle; partial exits require integer quantities. Open positions are closed at the last supplied price for terminal performance, explicitly recorded as end-of-data liquidation. Requested dates use available warm-up history and report actual coverage/gaps; absent dates are not invented. Limits: 20,000 candles per backtest, 3,000 per research request, six compared versions. Longer periods work only within available data and those explicit budgets.

Analytics use Decimal money and actual closed records; undefined ratios are null, not infinity or fictional values. Charts cover equity, drawdown, daily/monthly P/L, sessions, setup types, realised R, instruments, weekdays and versions. Sharpe/Sortino use recorded daily return samples with disclosed annualisation/sample limitations. Account-level curves/drawdown are unavailable for an aggregate of independent backtest experiments; choose one run and its own starting equity. They do not include an invented tick-by-tick unrealised path.

Research compares versions on identical data and assumptions, using a training/holdout split, walk-forward folds, supplied-parameter sensitivity and seeded bootstrap of recorded net trade outcomes. It warns about small samples, selection overlap and overfitting. Bootstrap outcomes are a resampling experiment, not a future-profit guarantee. Research never activates a version. Risk/session/strategy edits produce a **proposal**; a separate explicit **Activate** action is required while OFF and without unresolved positions/orders.

The model interprets questions and explains recorded data; it has no trading-order submission tool. Trading execution requests also reject raw cursor clicks, keyboard entry, argument-bearing launches, script opens and commands that would sidestep this service. This is request-scoped application control, not an OS sandbox: JARVIS's existing full-computer capabilities remain available for unrelated requests, and no absolute containment claim is made.

## Demonstrated implementation status

| Phase | What is demonstrated | What is still unverified |
|---|---|---|
| Existing JARVIS inspection | Shared FastAPI/SQLite/tools/voice integrated; original front preserved | Native microphone quality |
| TradingView connection | Installed-app launch/identity/account-observation adapter; fake-adapter fail-closed tests | Actual selected account and balances on your Windows app |
| Paper orders | Independent simulation, brackets, risk checks, fills/reconciliation/restart tests | Native TradingView order ticket, brackets, fills and synchronisation |
| Strategy and automation | Deterministic NQ/ES rules, sessions, live-data gates, idempotency and stops | Your live licensed feed and real market setups |
| Journal/backtests/analytics | Real local data import/replay, separate reports, CSV/PDF and dashboard tests | Results on your own historical data |
| Research/versioning | Controlled experiments and explicit active-version approval | Statistical evidence of profitability |
| Voice/reliability | Existing British transport, local/busy stops and authenticated integration tests | Windows end-to-end trading and real speech services |

Useful checks:

```sh
.venv/bin/python -m pytest -q
node --test tests/voice.test.js tests/speech.test.js
node tests/trading.browser.cjs
node tests/dashboard.browser.cjs
node tests/visual.browser.cjs
```

Browser fixtures and synthetic test prices verify code paths; they are never product performance history. No real order or account connection was made during cloud validation.
