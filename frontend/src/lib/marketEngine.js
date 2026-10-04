export const EXCHANGES = ["BINANCE", "BYBIT", "GATE.IO"];
export const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];
export const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h"];
export const DEPTHS = [10, 25, 50, 100, 200, 1000];
export const TICK = { BTCUSDT: 10, ETHUSDT: 1, SOLUSDT: 0.1, BNBUSDT: 0.5, XRPUSDT: 0.001 };
export const PRICE_DEC = { BTCUSDT: 1, ETHUSDT: 2, SOLUSDT: 3, BNBUSDT: 2, XRPUSDT: 4 };
export const TF_MS = { "1m": 6e4, "3m": 18e4, "5m": 3e5, "15m": 9e5, "30m": 18e5, "1h": 36e5 };
const BASE_PRICE = { BTCUSDT: 85240, ETHUSDT: 3520, SOLUSDT: 148.2, BNBUSDT: 592, XRPUSDT: 0.524 };
const VOL_SCALE = { BTCUSDT: 25, ETHUSDT: 400, SOLUSDT: 6000, BNBUSDT: 250, XRPUSDT: 600000 };

export const decimalsOf = (tick) => (tick >= 1 ? 0 : Math.ceil(-Math.log10(tick)));
export function levelNum(price, tick) {
  const q = price / tick;
  const r = Math.round(q);
  const base = Math.abs(q - r) < 1e-6 ? r : Math.floor(q);
  return +(base * tick).toFixed(decimalsOf(tick));
}
export const levelKey = (price, tick) => levelNum(price, tick).toFixed(decimalsOf(tick));

export class Candle {
  constructor(t, price) {
    Object.assign(this, { t, o: price, h: price, l: price, c: price, vol: 0, buy: 0, sell: 0, delta: 0, maxDelta: 0, minDelta: 0, trades: 0, cells: {}, approx: false });
  }
}

export function addTrade(c, price, qty, side, tick) {
  c.h = Math.max(c.h, price);
  c.l = Math.min(c.l, price);
  c.c = price;
  c.vol += qty;
  if (side === "buy") { c.buy += qty; c.delta += qty; } else { c.sell += qty; c.delta -= qty; }
  c.maxDelta = Math.max(c.maxDelta, c.delta);
  c.minDelta = Math.min(c.minDelta, c.delta);
  c.trades++;
  const k = levelKey(price, tick);
  const cell = c.cells[k] || (c.cells[k] = { bid: 0, ask: 0 });
  if (side === "buy") cell.ask += qty; else cell.bid += qty;
}

export function synthCandle(t, o, h, l, c, vol, buyVol, tick, approx = true) {
  const cd = new Candle(t, o);
  Object.assign(cd, { h, l, c, vol, buy: buyVol, sell: vol - buyVol, delta: buyVol - (vol - buyVol), approx });
  const lo = levelNum(l, tick), hi = levelNum(h, tick);
  const n = Math.max(1, Math.round((hi - lo) / tick) + 1);
  const center = (o + c) / 2, sigma = Math.max(tick, (h - l) / 3);
  const w = []; let sum = 0;
  for (let i = 0; i < n; i++) { const p = lo + i * tick; const x = Math.exp(-((p - center) ** 2) / (2 * sigma * sigma)) * (0.6 + Math.random() * 0.8); w.push(x); sum += x; }
  const buyShare = vol ? buyVol / vol : 0.5;
  for (let i = 0; i < n; i++) {
    const p = lo + i * tick; const lv = (vol * w[i]) / sum;
    const share = Math.min(0.95, Math.max(0.05, buyShare + (Math.random() - 0.5) * 0.5));
    cd.cells[levelKey(p, tick)] = { ask: lv * share, bid: lv * (1 - share) };
  }
  cd.maxDelta = Math.max(0, cd.delta); cd.minDelta = Math.min(0, cd.delta);
  return cd;
}

export class MarketEngine {
  constructor(cfg = {}) {
    this.cfg = { exchange: "BINANCE", symbol: "BTCUSDT", tf: "1m", marketType: "perp", depth: 20, ...cfg };
    this.listeners = new Set(); this.version = 0; this.session = 0; this.tid = 0; this._msgs = 0; this.dirty = false;
    this.bookShape = Array.from({ length: 1000 }, () => (0.3 + Math.random() * 1.2) * (Math.random() < 0.06 ? 4 : 1));
    this.reset();
  }
  reset() {
    const sym = this.cfg.symbol; this.tick = TICK[sym] || 1;
    this.state = { ...this.cfg, version: ++this.version, status: "connecting", source: "connecting…", latency: 0, msgRate: 0, tick: this.tick,
      ticker: { last: 0, prev: 0, dir: 0, changePct: 0, change: 0, volume: 0, quoteVolume: 0, high: 0, low: 0 },
      book: { bids: [], asks: [] }, trades: [], candles: [], pdh: 0, pdl: 0 };
    this.snap = { ...this.state };
  }
  subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  snapshot() { return this.snap; }
  touch() { this.dirty = true; }
  flush() { if (!this.dirty) return; this.dirty = false; this.state.version = ++this.version; this.snap = { ...this.state }; this.listeners.forEach((fn) => fn(this.snap)); }
  stop() {
    this.session++;
    clearInterval(this.flushTimer); clearInterval(this.rateTimer); clearInterval(this.bookTimer); clearInterval(this.depthTimer); clearTimeout(this.simTimer); clearTimeout(this.fallbackTimer);
    if (this.ws) { this.ws.onclose = null; this.ws.onmessage = null; try { this.ws.close(); } catch {} this.ws = null; }
  }
  start() {
    this.stop(); this.reset(); this.touch(); this.spotFallback = false;
    const token = this.session;
    this.flushTimer = setInterval(() => this.flush(), 100);
    this.rateTimer = setInterval(() => { this.state.msgRate = this._msgs; this._msgs = 0; this.touch(); }, 1000);
    if (this.cfg.exchange === "BINANCE") this.startBinance(token, this.cfg.marketType); else this.startSim(token, "exchange");
  }
  configure(patch) { Object.assign(this.cfg, patch); this.start(); }
  setDepth(n) { this.cfg.depth = n; this.state.depth = n; this.touch(); this.setupDepthPolling(); }
  rest(mt) { return mt === "perp" ? "https://fapi.binance.com/fapi/v1" : "https://data-api.binance.vision/api/v3"; }
  wsHost(mt) { return mt === "perp" ? "wss://fstream.binance.com/stream?streams=" : "wss://data-stream.binance.vision/stream?streams="; }

  startBinance(token, mt) {
    const S = this.state; const sym = this.cfg.symbol.toLowerCase();
    S.source = mt === "perp" ? "BINANCE · USDⓈ-M PERP" : this.spotFallback ? "BINANCE · SPOT FEED (perp data region-blocked)" : "BINANCE · SPOT";
    this.seedHistory(token, mt);
    const ws = new WebSocket(this.wsHost(mt) + [`${sym}@aggTrade`, `${sym}@depth20@100ms`, `${sym}@ticker`].join("/"));
    this.ws = ws;
    this.fallbackTimer = setTimeout(() => {
      if (token !== this.session || S.status !== "connecting") return;
      ws.onclose = null; try { ws.close(); } catch {}
      if (mt === "perp") this.switchToSpot(token); else this.startSim(token, "fallback");
    }, 6000);
    ws.onopen = () => { if (token !== this.session) return; clearTimeout(this.fallbackTimer); S.status = "live"; this.touch(); this.setupDepthPolling(); };
    ws.onmessage = (ev) => {
      if (token !== this.session) return;
      const { stream, data } = JSON.parse(ev.data); this._msgs++;
      if (stream.endsWith("aggTrade")) this.onTrade(+data.p, +data.q, data.m ? "sell" : "buy", data.T, data.E);
      else if (stream.includes("depth")) { if (this.cfg.depth <= 20) this.setBook(data.bids ?? data.b, data.asks ?? data.a); }
      else if (stream.endsWith("ticker")) { Object.assign(S.ticker, { changePct: +data.P, change: +data.p, volume: +data.v, quoteVolume: +data.q, high: +data.h, low: +data.l }); this.touch(); }
    };
    ws.onclose = () => {
      if (token !== this.session) return;
      if (S.status === "live") { S.status = "reconnecting"; this.touch(); setTimeout(() => token === this.session && this.startBinance(token, mt), 2000); }
    };
    ws.onerror = () => {};
  }

  async seedHistory(token, mt) {
    const S = this.state; const { symbol, tf } = this.cfg; const base = this.rest(mt);
    try {
      const [rows, days] = await Promise.all([
        fetch(`${base}/klines?symbol=${symbol}&interval=${tf}&limit=120`).then((r) => r.json()),
        fetch(`${base}/klines?symbol=${symbol}&interval=1d&limit=2`).then((r) => r.json()),
      ]);
      if (token !== this.session) return;
      if (!Array.isArray(rows)) throw new Error("history unavailable");
      const liveByT = Object.fromEntries(S.candles.map((c) => [c.t, c]));
      const seeded = rows.map((r) => synthCandle(+r[0], +r[1], +r[2], +r[3], +r[4], +r[5], +r[9], this.tick));
      seeded.forEach((sc) => { const lc = liveByT[sc.t]; if (lc) { Object.entries(lc.cells).forEach(([k, v]) => { const o = sc.cells[k] || (sc.cells[k] = { bid: 0, ask: 0 }); o.bid += v.bid; o.ask += v.ask; }); sc.c = lc.c; sc.h = Math.max(sc.h, lc.h); sc.l = Math.min(sc.l, lc.l); } });
      const lastT = seeded[seeded.length - 1].t;
      S.candles = seeded.concat(S.candles.filter((c) => c.t > lastT));
      if (Array.isArray(days) && days.length >= 2) { S.pdh = +days[0][2]; S.pdl = +days[0][3]; }
      if (!S.ticker.last) { S.ticker.last = seeded[seeded.length - 1].c; S.ticker.prev = S.ticker.last; }
      this.touch();
    } catch {
      if (token !== this.session) return;
      if (mt === "perp") { this.switchToSpot(token); return; }
      S.historyUnavailable = true; this.touch();
    }
  }

  switchToSpot(token) {
    if (token !== this.session || this.spotFallback) return;
    this.spotFallback = true; clearTimeout(this.fallbackTimer);
    if (this.ws) { this.ws.onclose = null; this.ws.onmessage = null; try { this.ws.close(); } catch {} this.ws = null; }
    const S = this.state; S.status = "connecting"; S.trades = []; S.candles = []; S.book = { bids: [], asks: [] }; this.touch();
    this.startBinance(token, "spot");
  }

  setupDepthPolling() {
    clearInterval(this.depthTimer);
    if (this.cfg.exchange !== "BINANCE" || this.cfg.depth <= 20 || this.state.status !== "live") return;
    const token = this.session; const d = this.cfg.depth; const lim = d <= 50 ? 50 : d <= 100 ? 100 : d <= 500 ? 500 : 1000;
    const mt = this.spotFallback ? "spot" : this.cfg.marketType;
    const url = `${this.rest(mt)}/depth?symbol=${this.cfg.symbol}&limit=${lim}`;
    const poll = async () => { try { const r = await (await fetch(url)).json(); if (token !== this.session || !r.bids) return; this.setBook(r.bids.slice(0, d), r.asks.slice(0, d)); } catch {} };
    poll(); this.depthTimer = setInterval(poll, 1500);
  }

  setBook(bids, asks) {
    const S = this.state;
    S.book = { bids: (bids || []).map(([p, q]) => [+p, +q]).filter((x) => x[1] > 0), asks: (asks || []).map(([p, q]) => [+p, +q]).filter((x) => x[1] > 0) };
    S.lastBookUpdate = Date.now(); this.touch();
  }

  onTrade(price, qty, side, T, E) {
    const S = this.state; const tfms = TF_MS[this.cfg.tf]; const t = Math.floor(T / tfms) * tfms;
    let c = S.candles[S.candles.length - 1];
    if (!c || c.t !== t) { if (c && t < c.t) return; c = new Candle(t, price); S.candles.push(c); if (S.candles.length > 400) S.candles.shift(); }
    addTrade(c, price, qty, side, this.tick);
    S.ticker.prev = S.ticker.last; S.ticker.last = price;
    S.ticker.dir = price > S.ticker.prev ? 1 : price < S.ticker.prev ? -1 : S.ticker.dir;
    if (E) S.latency = Math.max(0, Date.now() - E);
    S.trades.unshift({ id: ++this.tid, time: T, price, qty, side, value: price * qty });
    if (S.trades.length > 150) S.trades.length = 150;
    this.touch();
  }

  startSim(token, reason) {
    const S = this.state; const sym = this.cfg.symbol; const tick = this.tick; const tfms = TF_MS[this.cfg.tf]; const dec = PRICE_DEC[sym];
    S.status = "sim"; S.source = reason === "fallback" ? "SIMULATED · exchange unreachable" : `SIMULATED · ${this.cfg.exchange} ${this.cfg.marketType.toUpperCase()}`;
    const vscale = VOL_SCALE[sym]; const anchor = BASE_PRICE[sym] * (1 + (Math.random() - 0.5) * 0.01);
    const start = Math.floor(Date.now() / tfms) * tfms - 119 * tfms; let p = anchor * 0.994;
    S.candles = [];
    for (let i = 0; i < 120; i++) {
      const o = p; const range = anchor * 0.0012 * (0.5 + Math.random()) * Math.sqrt(tfms / 6e4);
      const c = o + (anchor - p) * 0.05 + (Math.random() - 0.5) * range;
      const h = Math.max(o, c) + Math.random() * range * 0.5, l = Math.min(o, c) - Math.random() * range * 0.5;
      const vol = vscale * (0.4 + Math.random() * 1.4) * (tfms / 6e4);
      const buy = Math.max(0, Math.min(vol, vol * (0.5 + (c > o ? 0.08 : -0.08) + (Math.random() - 0.5) * 0.3)));
      S.candles.push(synthCandle(start + i * tfms, +o.toFixed(dec), +h.toFixed(dec), +l.toFixed(dec), +c.toFixed(dec), vol, buy, tick)); p = c;
    }
    let price = p;
    S.ticker = { last: +price.toFixed(dec), prev: +price.toFixed(dec), dir: 0, changePct: (Math.random() - 0.4) * 4, change: 0, volume: vscale * 1440 * 0.8, quoteVolume: vscale * 1440 * 0.8 * price, high: price * 1.012, low: price * 0.985 };
    S.pdh = +(price * 1.018).toFixed(dec); S.pdl = +(price * 0.979).toFixed(dec);
    let drift = 0;
    const step = () => {
      if (token !== this.session) return;
      drift = drift * 0.9 + (Math.random() - 0.5) * tick * 0.6;
      const n = 1 + Math.floor(Math.random() * 3);
      for (let i = 0; i < n; i++) {
        price = Math.max(tick, price + drift + (Math.random() - 0.5) * tick * 1.5);
        const side = Math.random() < 0.5 + Math.sign(drift) * 0.15 ? "buy" : "sell";
        const qty = (Math.exp(Math.random() * 3 - 1.5) * vscale) / 600 * (Math.random() < 0.03 ? 8 : 1);
        this.onTrade(+price.toFixed(dec), qty, side, Date.now(), null);
      }
      S.latency = 6 + Math.round(Math.random() * 18); this._msgs += n;
      this.simTimer = setTimeout(step, 60 + Math.random() * 220);
    };
    step();
    this.bookTimer = setInterval(() => {
      if (token !== this.session) return;
      const depth = this.cfg.depth; const best = levelNum(S.ticker.last, tick); const base = vscale / 200; const bids = [], asks = [];
      for (let i = 0; i < depth; i++) {
        bids.push([+(best - i * tick).toFixed(dec), base * this.bookShape[i] * (0.85 + Math.random() * 0.3)]);
        asks.push([+(best + (i + 1) * tick).toFixed(dec), base * this.bookShape[999 - i] * (0.85 + Math.random() * 0.3)]);
      }
      this.setBook(bids, asks);
    }, 180);
    this.touch();
  }
}
