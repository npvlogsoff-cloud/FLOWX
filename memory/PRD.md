# FLOWX — Professional Order-Flow Analytics SaaS

## Original problem statement
Build a premium, professional crypto order-flow trading terminal SaaS ("FLOWX"): landing page, login/register, user dashboard, multi-panel trading terminal (footprint chart, DOM, volume profile, VWAP overlays, trade tape, bottom analytics tabs: order flow / delta / volume / trades / signals / alerts / backtest / journal), absorption & imbalance alert cards, admin dashboard with data-source monitoring, dedicated mobile layout. Dark terminal aesthetic; green=buy, red=sell, cyan=info, amber=levels, purple=analytics; never imply signal certainty.

## User choices
- Market data: real Binance public WebSocket (browser-side); Bybit & Gate.io simulated (labelled SIM)
- Auth: JWT email/password (seeded admin + demo trader)
- Pricing: UI only (no payments)
- Brand: unique premium palette → champagne gold (#F2C879) on void navy-black, Chakra Petch / IBM Plex Sans / JetBrains Mono

## Architecture
- Frontend: React 19 + Tailwind + shadcn, canvas footprint renderer (`components/terminal/FootprintChart.jsx`), market engine (`lib/marketEngine.js`: Binance combined stream aggTrade/depth20/ticker, kline history seeding, spot fallback when perp REST geo-blocked, sim engine), analytics (`lib/analytics.js`: imbalances, stacked imbalance, volume profile POC/VAH/VAL/HVN/LVN, VWAP±σ, absorption, signal scoring, backtest)
- Backend: FastAPI + Motor. Auth (bcrypt + PyJWT, httpOnly cookies + Bearer, brute-force lockout), user data (settings, favorites, alerts, journal, layouts, signals, usage), admin (overview, users, system settings), feed monitor tasks connecting to Binance/Bybit/Gate.io public WS for health metrics.
- Routes: `/` landing, `/login`, `/register`, `/terminal` (public), `/dashboard`, `/settings`, `/admin`

## Credentials
See `/app/memory/test_credentials.md` (admin@flowx.io / Admin#2026, trader@flowx.io / Trader#2026)

## Implemented (2026-06)
- Landing (hero, animated sim terminal preview, 10 features, pricing from API, FAQ, risk/data disclosures, contact form)
- Auth pages with demo quick-fill
- Trading terminal: top bar (exchange/symbol/spot-perp/timeframe, LIVE status, last/24h/vol/spread/latency/msg-rate, settings/replay/alerts/fullscreen/theme/account), left toolbar (crosshair, trend, hline, rect, measure, profile toggle, zoom, reset, clear, screenshot), footprint canvas with heat cells, imbalance/stacked highlighting, candle Δ/volume rows, overlays (VWAP+bands, session H/L, PDH/PDL, POC, VAH/VAL), volume profile, price axis with last-price marker, crosshair tooltip, drawings; DOM with depth selector, walls, liquidity imbalance meter, spread/bps; trade tape with large-print highlight; bottom tabs (order flow, delta chart, volume + profile nodes, trades filter, signals with confidence, alerts: live detections + persistent price alerts, backtest with equity curve, journal CRUD); replay bar; settings sheet persisted locally + to account; theme toggle
- User dashboard (live favorite tiles, favorites, layouts, alerts, recent signals, usage, subscription), settings page
- Admin dashboard (10 metrics, 3 live data-source monitors, user management plan/status, system configuration)
- Mobile terminal (swipeable panels + bottom nav)
- Testing: iteration_1 — backend 20/20, frontend 100%

## Backlog
- P1: real Bybit/Gate.io browser feeds; price-alert sound/notification settings; layout apply directly from terminal
- P1: Stripe subscriptions; plan-gated features enforcement on frontend (exchanges/depth)
- P2: keyboard shortcuts; multi-chart layouts; order-book heatmap history (Bookmap-style); export journal CSV; password reset UI
