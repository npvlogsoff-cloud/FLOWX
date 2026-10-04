from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import asyncio
import json
import time
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, Annotated
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from pydantic import BaseModel, Field, BeforeValidator, ConfigDict
import bcrypt
import jwt
import websockets

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("flowx")

client = AsyncIOMotorClient(os.environ['MONGO_URL'])
db = client[os.environ['DB_NAME']]
app = FastAPI(title="FLOWX API")
api = APIRouter(prefix="/api")

JWT_ALG = "HS256"


def now():
    return datetime.now(timezone.utc)


def now_iso():
    return now().isoformat()


def oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise HTTPException(400, "Invalid id")


PyObjectId = Annotated[str, BeforeValidator(str)]


class BaseDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    id: Optional[PyObjectId] = Field(default=None, validation_alias="_id")

    def to_mongo(self) -> dict:
        return self.model_dump(exclude={"id"}, exclude_none=True)

    @classmethod
    def from_mongo(cls, doc):
        return cls.model_validate(doc) if doc else None


# ---------------------------------------------------------------- constants
PLANS = [
    {"id": "free", "name": "Starter", "price": 0, "period": "forever", "tagline": "Explore order flow on one market",
     "features": ["1 exchange · BINANCE", "Footprint + DOM (depth 20)", "Delta & volume profile", "5 price alerts", "Community support"]},
    {"id": "pro", "name": "Pro", "price": 49, "period": "month", "tagline": "Full terminal for active traders", "highlight": True,
     "features": ["All exchanges", "DOM depth up to 1000", "Imbalance & absorption detection", "Signals + unlimited alerts", "Replay & backtesting", "Trade journal"]},
    {"id": "institutional", "name": "Institutional", "price": 199, "period": "month", "tagline": "Desks, prop firms and quant teams",
     "features": ["Everything in Pro", "Multi-seat workspace", "Raw tick export", "Priority data routing", "Dedicated support SLA"]},
]
PLAN_PRICE = {p["id"]: p["price"] for p in PLANS}

DEFAULT_SETTINGS = {"exchange": "BINANCE", "symbol": "BTCUSDT", "marketType": "perp", "timeframe": "1m", "depth": 20,
                    "imbalanceRatio": 3, "stackedCount": 3, "absorptionMultiplier": 1.8, "largeTradeUsd": 50000,
                    "soundAlerts": False, "overlays": {"vwap": True, "bands": True, "session": True, "pdhl": True, "poc": True, "va": True, "profile": True}}

SYSTEM_DEFAULTS = {"exchanges": {"BINANCE": True, "BYBIT": True, "GATE.IO": True},
                   "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
                   "dataRetentionDays": 30, "imbalanceThreshold": 3.0, "absorptionThreshold": 1.8, "alertCooldownSec": 60,
                   "rateLimitPerMin": 600, "featureFlags": {"replay": True, "backtest": True, "journal": True, "signals": True}}


# ---------------------------------------------------------------- models
class User(BaseDocument):
    email: str
    name: str = ""
    password_hash: str = ""
    role: str = "trader"
    plan: str = "free"
    status: str = "active"
    settings: dict = Field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    favorites: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    created_at: str = Field(default_factory=now_iso)
    last_login: Optional[str] = None
    last_seen: Optional[str] = None
    login_count: int = 0


class UserOut(BaseDocument):
    email: str
    name: str = ""
    role: str = "trader"
    plan: str = "free"
    status: str = "active"
    settings: dict = {}
    favorites: list[str] = []
    created_at: str = ""
    last_login: Optional[str] = None
    last_seen: Optional[str] = None
    login_count: int = 0


class AlertRule(BaseDocument):
    user_id: str
    symbol: str
    condition: str = "above"
    value: float = 0
    note: str = ""
    enabled: bool = True
    triggered_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class JournalEntry(BaseDocument):
    user_id: str
    symbol: str
    side: str = "long"
    entry: float = 0
    exit: Optional[float] = None
    size: float = 0
    pnl: float = 0
    setup: str = ""
    notes: str = ""
    rating: int = 3
    created_at: str = Field(default_factory=now_iso)


class Layout(BaseDocument):
    user_id: str
    name: str
    config: dict = {}
    created_at: str = Field(default_factory=now_iso)


class SignalLog(BaseDocument):
    user_id: str
    symbol: str
    exchange: str = "BINANCE"
    bias: str
    confidence: str
    score: float = 0
    price: float = 0
    factors: list = []
    created_at: str = Field(default_factory=now_iso)


class RegisterIn(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class AlertIn(BaseModel):
    symbol: str
    condition: str = "above"
    value: float
    note: str = ""


class AlertPatch(BaseModel):
    enabled: Optional[bool] = None
    triggered_at: Optional[str] = None


class JournalIn(BaseModel):
    symbol: str
    side: str = "long"
    entry: float
    exit: Optional[float] = None
    size: float = 0
    setup: str = ""
    notes: str = ""
    rating: int = 3


class LayoutIn(BaseModel):
    name: str
    config: dict = {}


class SignalIn(BaseModel):
    symbol: str
    exchange: str = "BINANCE"
    bias: str
    confidence: str
    score: float = 0
    price: float = 0
    factors: list = []


class FavoritesIn(BaseModel):
    symbols: list[str]


class AdminUserPatch(BaseModel):
    plan: Optional[str] = None
    status: Optional[str] = None
    role: Optional[str] = None


class ContactIn(BaseModel):
    name: str
    email: str
    message: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str


# ---------------------------------------------------------------- auth helpers
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_token(user_id: str, email: str, kind: str, delta: timedelta) -> str:
    payload = {"sub": user_id, "email": email, "type": kind, "exp": now() + delta}
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALG)


def set_auth_cookies(response: Response, user_id: str, email: str) -> str:
    access = create_token(user_id, email, "access", timedelta(hours=12))
    refresh = create_token(user_id, email, "refresh", timedelta(days=7))
    response.set_cookie("access_token", access, httponly=True, secure=True, samesite="none", max_age=43200, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=True, samesite="none", max_age=604800, path="/")
    return access


async def get_current_user(request: Request) -> User:
    token = request.cookies.get("access_token")
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(401, "Invalid token type")
    doc = await db.users.find_one({"_id": oid(payload["sub"])})
    if not doc:
        raise HTTPException(401, "User not found")
    user = User.from_mongo(doc)
    if user.status != "active":
        raise HTTPException(403, "Account suspended")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def public_user(user: User) -> dict:
    return UserOut.model_validate(user.model_dump()).model_dump()


# ---------------------------------------------------------------- metrics + feed monitor
METRICS = {"requests": 0, "errors": 0, "total_ms": 0.0, "started": now_iso()}

FEEDS = {
    "BINANCE": {"url": "wss://data-stream.binance.vision/stream?streams=btcusdt@aggTrade/btcusdt@depth5@500ms", "subs": [], "ping": None},
    "BYBIT": {"url": "wss://stream.bybit.com/v5/public/spot", "subs": [{"op": "subscribe", "args": ["publicTrade.BTCUSDT", "orderbook.1.BTCUSDT"]}], "ping": {"op": "ping"}},
    "GATE.IO": {"url": "wss://api.gateio.ws/ws/v4/", "subs": [{"channel": "spot.trades", "event": "subscribe", "payload": ["BTC_USDT"]},
                                                           {"channel": "spot.book_ticker", "event": "subscribe", "payload": ["BTC_USDT"]}], "ping": {"channel": "spot.ping"}},
}
feed_state = {name: {"exchange": name, "status": "connecting", "messages": 0, "rate": 0, "_window": 0, "last_tick": None,
                     "last_book": None, "connected_at": None, "reconnects": 0, "latency_ms": None, "error": None, "last_price": None}
              for name in FEEDS}


def classify(name: str, msg: dict):
    if name == "BINANCE":
        stream = msg.get("stream", "")
        d = msg.get("data", {}) if isinstance(msg.get("data"), dict) else {}
        return ("book" if "depth" in stream else "trade"), d.get("E"), d.get("p")
    if name == "BYBIT":
        topic = msg.get("topic", "")
        data = msg.get("data")
        price = data[0].get("p") if isinstance(data, list) and data and isinstance(data[0], dict) else None
        return ("book" if topic.startswith("orderbook") else "trade" if topic else "ctrl"), msg.get("ts"), price
    ch = msg.get("channel", "")
    res = msg.get("result") or {}
    ms = None
    price = None
    if isinstance(res, dict):
        ms = res.get("create_time_ms") or res.get("t")
        price = res.get("price")
    return ("book" if "book" in ch else "trade" if "trades" in ch else "ctrl"), ms, price


async def run_feed(name: str, cfg: dict):
    st = feed_state[name]
    while True:
        try:
            async with websockets.connect(cfg["url"], ping_interval=20, open_timeout=10) as ws:
                for sub in cfg["subs"]:
                    await ws.send(json.dumps({**sub, "time": int(time.time())} if name == "GATE.IO" else sub))
                st.update(status="connected", connected_at=now_iso(), error=None)

                async def pinger():
                    while cfg["ping"]:
                        await asyncio.sleep(20)
                        await ws.send(json.dumps({**cfg["ping"], "time": int(time.time())} if name == "GATE.IO" else cfg["ping"]))

                ping_task = asyncio.create_task(pinger())
                try:
                    async for raw in ws:
                        st["messages"] += 1
                        st["_window"] += 1
                        try:
                            kind, ms, price = classify(name, json.loads(raw))
                        except Exception:
                            continue
                        if kind == "ctrl":
                            continue
                        st["last_tick" if kind == "trade" else "last_book"] = now_iso()
                        if price:
                            st["last_price"] = float(price)
                        try:
                            if ms:
                                st["latency_ms"] = max(0, int(time.time() * 1000 - float(ms)))
                        except Exception:
                            pass
                finally:
                    ping_task.cancel()
        except Exception as e:
            st.update(status="disconnected", error=str(e)[:140])
            st["reconnects"] += 1
            await asyncio.sleep(5)


async def rate_ticker():
    while True:
        await asyncio.sleep(1)
        for st in feed_state.values():
            st["rate"] = st["_window"]
            st["_window"] = 0


def feed_public():
    return [{k: v for k, v in st.items() if not k.startswith("_")} for st in feed_state.values()]


@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        METRICS["requests"] += 1
        METRICS["errors"] += 1
        raise
    METRICS["requests"] += 1
    METRICS["total_ms"] += (time.perf_counter() - t0) * 1000
    if resp.status_code >= 500:
        METRICS["errors"] += 1
    return resp


# ---------------------------------------------------------------- auth routes
@api.post("/auth/register")
async def register(body: RegisterIn, response: Response):
    email = body.email.strip().lower()
    if "@" not in email or len(body.password) < 6:
        raise HTTPException(400, "Valid email and a password of at least 6 characters are required")
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=email, name=body.name.strip() or email.split("@")[0], password_hash=hash_password(body.password),
                last_login=now_iso(), last_seen=now_iso(), login_count=1)
    res = await db.users.insert_one(user.to_mongo())
    user.id = str(res.inserted_id)
    token = set_auth_cookies(response, user.id, email)
    return {"user": public_user(user), "token": token}


@api.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    email = body.email.strip().lower()
    ident = f"{request.client.host if request.client else 'x'}:{email}"
    attempt = await db.login_attempts.find_one({"identifier": ident})
    if attempt and attempt.get("count", 0) >= 5:
        locked_until = datetime.fromisoformat(attempt["last"]) + timedelta(minutes=15)
        if now() < locked_until:
            raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes")
    doc = await db.users.find_one({"email": email})
    if not doc or not verify_password(body.password, doc.get("password_hash", "")):
        await db.login_attempts.update_one({"identifier": ident}, {"$inc": {"count": 1}, "$set": {"last": now_iso()}}, upsert=True)
        raise HTTPException(401, "Invalid email or password")
    await db.login_attempts.delete_one({"identifier": ident})
    await db.users.update_one({"_id": doc["_id"]}, {"$set": {"last_login": now_iso(), "last_seen": now_iso()}, "$inc": {"login_count": 1}})
    user = User.from_mongo(await db.users.find_one({"_id": doc["_id"]}))
    if user.status != "active":
        raise HTTPException(403, "Account suspended. Contact support")
    token = set_auth_cookies(response, user.id, email)
    return {"user": public_user(user), "token": token}


@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}


@api.get("/auth/me")
async def me(user: User = Depends(get_current_user)):
    return public_user(user)


@api.post("/auth/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(401, "No refresh token")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALG])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid token type")
    access = set_auth_cookies(response, payload["sub"], payload.get("email", ""))
    return {"token": access}


@api.post("/auth/forgot-password")
async def forgot_password(body: ForgotIn):
    email = body.email.strip().lower()
    doc = await db.users.find_one({"email": email})
    if doc:
        token = secrets.token_urlsafe(32)
        await db.password_reset_tokens.insert_one({"token": token, "user_id": str(doc["_id"]), "used": False,
                                                   "expires_at": now() + timedelta(hours=1)})
        logger.info("Password reset link: /reset-password?token=%s", token)
    return {"ok": True, "message": "If the account exists, a reset link has been issued"}


@api.post("/auth/reset-password")
async def reset_password(body: ResetIn):
    rec = await db.password_reset_tokens.find_one({"token": body.token, "used": False})
    if not rec or rec["expires_at"].replace(tzinfo=timezone.utc) < now():
        raise HTTPException(400, "Invalid or expired token")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    await db.users.update_one({"_id": oid(rec["user_id"])}, {"$set": {"password_hash": hash_password(body.password)}})
    await db.password_reset_tokens.update_one({"_id": rec["_id"]}, {"$set": {"used": True}})
    return {"ok": True}


# ---------------------------------------------------------------- user routes
@api.get("/me/settings")
async def get_settings(user: User = Depends(get_current_user)):
    return {**DEFAULT_SETTINGS, **user.settings}


@api.put("/me/settings")
async def put_settings(body: dict, user: User = Depends(get_current_user)):
    merged = {**DEFAULT_SETTINGS, **user.settings, **body}
    await db.users.update_one({"_id": oid(user.id)}, {"$set": {"settings": merged}})
    return merged


@api.put("/me/profile")
async def put_profile(body: dict, user: User = Depends(get_current_user)):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Name required")
    await db.users.update_one({"_id": oid(user.id)}, {"$set": {"name": name}})
    return public_user(User.from_mongo(await db.users.find_one({"_id": oid(user.id)})))


@api.put("/me/favorites")
async def put_favorites(body: FavoritesIn, user: User = Depends(get_current_user)):
    await db.users.update_one({"_id": oid(user.id)}, {"$set": {"favorites": body.symbols[:20]}})
    return {"favorites": body.symbols[:20]}


@api.post("/me/heartbeat")
async def heartbeat(user: User = Depends(get_current_user)):
    await db.users.update_one({"_id": oid(user.id)}, {"$set": {"last_seen": now_iso()}})
    return {"ok": True}


async def list_owned(coll, model, user_id: str, limit=200):
    docs = await db[coll].find({"user_id": user_id}).sort("created_at", -1).to_list(limit)
    return [model.from_mongo(d).model_dump() for d in docs]


@api.get("/me/alerts")
async def list_alerts(user: User = Depends(get_current_user)):
    return await list_owned("alerts", AlertRule, user.id)


@api.post("/me/alerts")
async def create_alert(body: AlertIn, user: User = Depends(get_current_user)):
    count = await db.alerts.count_documents({"user_id": user.id})
    if user.plan == "free" and count >= 5:
        raise HTTPException(402, "Starter plan allows 5 alerts. Upgrade to Pro for unlimited alerts")
    rule = AlertRule(user_id=user.id, **body.model_dump())
    res = await db.alerts.insert_one(rule.to_mongo())
    rule.id = str(res.inserted_id)
    return rule.model_dump()


@api.patch("/me/alerts/{alert_id}")
async def patch_alert(alert_id: str, body: AlertPatch, user: User = Depends(get_current_user)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    await db.alerts.update_one({"_id": oid(alert_id), "user_id": user.id}, {"$set": patch})
    doc = await db.alerts.find_one({"_id": oid(alert_id), "user_id": user.id})
    if not doc:
        raise HTTPException(404, "Alert not found")
    return AlertRule.from_mongo(doc).model_dump()


@api.delete("/me/alerts/{alert_id}")
async def delete_alert(alert_id: str, user: User = Depends(get_current_user)):
    await db.alerts.delete_one({"_id": oid(alert_id), "user_id": user.id})
    return {"ok": True}


@api.get("/me/journal")
async def list_journal(user: User = Depends(get_current_user)):
    return await list_owned("journal", JournalEntry, user.id)


@api.post("/me/journal")
async def create_journal(body: JournalIn, user: User = Depends(get_current_user)):
    pnl = 0.0
    if body.exit is not None:
        pnl = (body.exit - body.entry) * body.size * (1 if body.side == "long" else -1)
    entry = JournalEntry(user_id=user.id, pnl=round(pnl, 4), **body.model_dump())
    res = await db.journal.insert_one(entry.to_mongo())
    entry.id = str(res.inserted_id)
    return entry.model_dump()


@api.delete("/me/journal/{entry_id}")
async def delete_journal(entry_id: str, user: User = Depends(get_current_user)):
    await db.journal.delete_one({"_id": oid(entry_id), "user_id": user.id})
    return {"ok": True}


@api.get("/me/layouts")
async def list_layouts(user: User = Depends(get_current_user)):
    return await list_owned("layouts", Layout, user.id)


@api.post("/me/layouts")
async def create_layout(body: LayoutIn, user: User = Depends(get_current_user)):
    layout = Layout(user_id=user.id, **body.model_dump())
    res = await db.layouts.insert_one(layout.to_mongo())
    layout.id = str(res.inserted_id)
    return layout.model_dump()


@api.delete("/me/layouts/{layout_id}")
async def delete_layout(layout_id: str, user: User = Depends(get_current_user)):
    await db.layouts.delete_one({"_id": oid(layout_id), "user_id": user.id})
    return {"ok": True}


@api.get("/me/signals")
async def list_signals(user: User = Depends(get_current_user)):
    return await list_owned("signals", SignalLog, user.id, 50)


@api.post("/me/signals")
async def log_signal(body: SignalIn, user: User = Depends(get_current_user)):
    recent = await db.signals.find_one({"user_id": user.id, "symbol": body.symbol, "bias": body.bias, "confidence": body.confidence,
                                        "created_at": {"$gte": (now() - timedelta(minutes=2)).isoformat()}})
    if recent:
        return SignalLog.from_mongo(recent).model_dump()
    sig = SignalLog(user_id=user.id, **body.model_dump())
    res = await db.signals.insert_one(sig.to_mongo())
    sig.id = str(res.inserted_id)
    return sig.model_dump()


@api.get("/me/usage")
async def usage(user: User = Depends(get_current_user)):
    alerts, journal, layouts, signals = await asyncio.gather(
        db.alerts.count_documents({"user_id": user.id}), db.journal.count_documents({"user_id": user.id}),
        db.layouts.count_documents({"user_id": user.id}), db.signals.count_documents({"user_id": user.id}))
    limits = {"free": {"alerts": 5, "exchanges": 1, "depth": 20}, "pro": {"alerts": None, "exchanges": 3, "depth": 1000},
              "institutional": {"alerts": None, "exchanges": 3, "depth": 1000}}[user.plan if user.plan in PLAN_PRICE else "free"]
    return {"alerts": alerts, "journal": journal, "layouts": layouts, "signals": signals, "sessions": user.login_count,
            "lastLogin": user.last_login, "plan": user.plan, "limits": limits, "memberSince": user.created_at}


# ---------------------------------------------------------------- public
@api.get("/")
async def root():
    return {"service": "FLOWX API", "status": "ok", "time": now_iso()}


@api.get("/plans")
async def plans():
    return PLANS


@api.get("/health")
async def health():
    req = METRICS["requests"] or 1
    return {"feeds": feed_public(), "api": {"requests": METRICS["requests"], "errors": METRICS["errors"],
                                             "avgLatencyMs": round(METRICS["total_ms"] / req, 2),
                                             "errorRate": round(METRICS["errors"] / req * 100, 3), "started": METRICS["started"]}}


@api.post("/contact")
async def contact(body: ContactIn):
    await db.contacts.insert_one({**body.model_dump(), "created_at": now_iso()})
    return {"ok": True}


# ---------------------------------------------------------------- admin
@api.get("/admin/overview")
async def admin_overview(_: User = Depends(require_admin)):
    day_ago = (now() - timedelta(hours=24)).isoformat()
    online_cut = (now() - timedelta(minutes=2)).isoformat()
    total, active, online, signals24, alerts, journal = await asyncio.gather(
        db.users.count_documents({}), db.users.count_documents({"last_login": {"$gte": day_ago}}),
        db.users.count_documents({"last_seen": {"$gte": online_cut}}), db.signals.count_documents({"created_at": {"$gte": day_ago}}),
        db.alerts.count_documents({}), db.journal.count_documents({}))
    plan_counts = {p: 0 for p in PLAN_PRICE}
    async for row in db.users.aggregate([{"$group": {"_id": "$plan", "n": {"$sum": 1}}}]):
        plan_counts[row["_id"] or "free"] = row["n"]
    paid = sum(n for p, n in plan_counts.items() if PLAN_PRICE.get(p, 0) > 0)
    mrr = sum(PLAN_PRICE.get(p, 0) * n for p, n in plan_counts.items())
    feeds = feed_public()
    connected = sum(1 for f in feeds if f["status"] == "connected")
    req = METRICS["requests"] or 1
    return {"totalUsers": total, "activeUsers": active, "onlineUsers": online, "subscriptions": paid, "planCounts": plan_counts,
            "mrr": mrr, "apiConnections": connected, "wsHealth": round(connected / len(feeds) * 100), "feeds": feeds,
            "systemLatencyMs": round(METRICS["total_ms"] / req, 2), "errorRate": round(METRICS["errors"] / req * 100, 3),
            "requests": METRICS["requests"], "signals24h": signals24, "alertRules": alerts, "journalEntries": journal,
            "uptimeSince": METRICS["started"]}


@api.get("/admin/users")
async def admin_users(_: User = Depends(require_admin)):
    docs = await db.users.find().sort("created_at", -1).to_list(500)
    return [public_user(User.from_mongo(d)) for d in docs]


@api.patch("/admin/users/{user_id}")
async def admin_patch_user(user_id: str, body: AdminUserPatch, _: User = Depends(require_admin)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "plan" in patch and patch["plan"] not in PLAN_PRICE:
        raise HTTPException(400, "Unknown plan")
    await db.users.update_one({"_id": oid(user_id)}, {"$set": patch})
    doc = await db.users.find_one({"_id": oid(user_id)})
    if not doc:
        raise HTTPException(404, "User not found")
    return public_user(User.from_mongo(doc))


@api.get("/admin/settings")
async def admin_settings(_: User = Depends(require_admin)):
    doc = await db.system_settings.find_one({"_id": "system"})
    return {**SYSTEM_DEFAULTS, **{k: v for k, v in (doc or {}).items() if k != "_id"}}


@api.put("/admin/settings")
async def admin_put_settings(body: dict, _: User = Depends(require_admin)):
    body.pop("_id", None)
    await db.system_settings.update_one({"_id": "system"}, {"$set": {**body, "updated_at": now_iso()}}, upsert=True)
    doc = await db.system_settings.find_one({"_id": "system"})
    return {**SYSTEM_DEFAULTS, **{k: v for k, v in doc.items() if k != "_id"}}


app.include_router(api)

cors_origins = os.environ.get("CORS_ORIGINS", "*")
if cors_origins.strip() == "*":
    app.add_middleware(CORSMiddleware, allow_origin_regex=".*", allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
else:
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in cors_origins.split(",")], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])


async def seed_user(email: str, password: str, name: str, role: str, plan: str):
    existing = await db.users.find_one({"email": email})
    if existing is None:
        await db.users.insert_one(User(email=email, name=name, password_hash=hash_password(password), role=role, plan=plan).to_mongo())
    elif not verify_password(password, existing.get("password_hash", "")):
        await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_password(password)}})


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.login_attempts.create_index("identifier")
    for coll in ("alerts", "journal", "layouts", "signals"):
        await db[coll].create_index("user_id")
    await seed_user(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"], "FLOWX Admin", "admin", "institutional")
    await seed_user(os.environ["DEMO_EMAIL"], os.environ["DEMO_PASSWORD"], "Demo Trader", "trader", "pro")
    await db.system_settings.update_one({"_id": "system"}, {"$setOnInsert": SYSTEM_DEFAULTS}, upsert=True)
    for name, cfg in FEEDS.items():
        asyncio.create_task(run_feed(name, cfg))
    asyncio.create_task(rate_ticker())


@app.on_event("shutdown")
async def shutdown():
    client.close()
