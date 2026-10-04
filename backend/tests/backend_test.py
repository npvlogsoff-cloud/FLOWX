"""FLOWX backend API tests (pytest)."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://liquidity-lens-11.preview.emergentagent.com").rstrip("/")
# Fallback to frontend/.env if env not set
if "liquidity" not in BASE_URL and "preview" not in BASE_URL:
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass

API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@flowx.io", "password": "Admin#2026"}
TRADER = {"email": "trader@flowx.io", "password": "Trader#2026"}


# ---------- fixtures ----------
@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def trader_token(session):
    r = session.post(f"{API}/auth/login", json=TRADER)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def admin_token(session):
    r = session.post(f"{API}/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- public ----------
def test_root(session):
    r = session.get(f"{API}/")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_plans(session):
    r = session.get(f"{API}/plans")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    ids = {p["id"] for p in data}
    assert ids == {"free", "pro", "institutional"}


def test_health(session):
    r = session.get(f"{API}/health")
    assert r.status_code == 200
    data = r.json()
    feeds = {f["exchange"] for f in data["feeds"]}
    assert feeds == {"BINANCE", "BYBIT", "GATE.IO"}
    assert "requests" in data["api"]


def test_contact(session):
    r = session.post(f"{API}/contact", json={"name": "T", "email": "t@t.io", "message": "hi"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------- auth ----------
def test_register_login_and_me(session):
    email = f"test_{uuid.uuid4().hex[:10]}@flowx.io"
    r = session.post(f"{API}/auth/register", json={"email": email, "password": "Secret123", "name": "T"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body and body["user"]["email"] == email
    token = body["token"]

    # duplicate
    r2 = session.post(f"{API}/auth/register", json={"email": email, "password": "Secret123"})
    assert r2.status_code == 409

    # short password
    r3 = session.post(f"{API}/auth/register", json={"email": f"x_{uuid.uuid4().hex[:6]}@f.io", "password": "123"})
    assert r3.status_code == 400

    # me via bearer
    me = session.get(f"{API}/auth/me", headers=auth(token))
    assert me.status_code == 200 and me.json()["email"] == email


def test_login_admin_trader_and_wrong_password(session):
    r = session.post(f"{API}/auth/login", json=ADMIN)
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    r2 = session.post(f"{API}/auth/login", json=TRADER)
    assert r2.status_code == 200 and r2.json()["user"]["plan"] == "pro"
    r3 = session.post(f"{API}/auth/login", json={"email": TRADER["email"], "password": "wrong"})
    assert r3.status_code == 401


def test_me_via_cookies_and_refresh_and_logout():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=TRADER)
    assert r.status_code == 200
    assert "access_token" in s.cookies or any(c.name == "access_token" for c in s.cookies)
    me = s.get(f"{API}/auth/me")
    assert me.status_code == 200
    rr = s.post(f"{API}/auth/refresh")
    assert rr.status_code == 200 and "token" in rr.json()
    lo = s.post(f"{API}/auth/logout")
    assert lo.status_code == 200


# ---------- settings / profile / favorites ----------
def test_settings_crud(session, trader_token):
    r = session.get(f"{API}/me/settings", headers=auth(trader_token))
    assert r.status_code == 200 and r.json()["exchange"] == "BINANCE"
    r2 = session.put(f"{API}/me/settings", headers=auth(trader_token), json={"imbalanceRatio": 4})
    assert r2.status_code == 200 and r2.json()["imbalanceRatio"] == 4


def test_profile_and_favorites(session, trader_token):
    r = session.put(f"{API}/me/profile", headers=auth(trader_token), json={"name": "Demo Trader"})
    assert r.status_code == 200 and r.json()["name"] == "Demo Trader"
    r2 = session.put(f"{API}/me/favorites", headers=auth(trader_token), json={"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]})
    assert r2.status_code == 200 and "SOLUSDT" in r2.json()["favorites"]


def test_heartbeat_and_usage(session, trader_token):
    r = session.post(f"{API}/me/heartbeat", headers=auth(trader_token))
    assert r.status_code == 200
    r2 = session.get(f"{API}/me/usage", headers=auth(trader_token))
    assert r2.status_code == 200 and r2.json()["plan"] == "pro"


# ---------- alerts (also free plan limit) ----------
def test_alerts_crud(session, trader_token):
    r = session.post(f"{API}/me/alerts", headers=auth(trader_token), json={"symbol": "BTCUSDT", "condition": "above", "value": 100000})
    assert r.status_code == 200
    aid = r.json()["id"]
    g = session.get(f"{API}/me/alerts", headers=auth(trader_token))
    assert g.status_code == 200 and any(a["id"] == aid for a in g.json())
    p = session.patch(f"{API}/me/alerts/{aid}", headers=auth(trader_token), json={"enabled": False})
    assert p.status_code == 200 and p.json()["enabled"] is False
    d = session.delete(f"{API}/me/alerts/{aid}", headers=auth(trader_token))
    assert d.status_code == 200


def test_free_plan_alert_limit(session):
    # register a fresh free user
    email = f"free_{uuid.uuid4().hex[:10]}@flowx.io"
    r = session.post(f"{API}/auth/register", json={"email": email, "password": "Secret123"})
    tok = r.json()["token"]
    ids = []
    for i in range(5):
        rr = session.post(f"{API}/me/alerts", headers=auth(tok), json={"symbol": "BTCUSDT", "condition": "above", "value": 100 + i})
        assert rr.status_code == 200
        ids.append(rr.json()["id"])
    # 6th should fail with 402
    r6 = session.post(f"{API}/me/alerts", headers=auth(tok), json={"symbol": "BTCUSDT", "condition": "above", "value": 999})
    assert r6.status_code == 402


# ---------- journal / layouts / signals ----------
def test_journal_crud(session, trader_token):
    r = session.post(f"{API}/me/journal", headers=auth(trader_token),
                     json={"symbol": "BTCUSDT", "side": "long", "entry": 100, "exit": 110, "size": 2, "setup": "t", "notes": "n"})
    assert r.status_code == 200
    data = r.json()
    assert data["pnl"] == pytest.approx(20.0)  # (110-100)*2*1
    jid = data["id"]
    g = session.get(f"{API}/me/journal", headers=auth(trader_token))
    assert any(j["id"] == jid for j in g.json())
    d = session.delete(f"{API}/me/journal/{jid}", headers=auth(trader_token))
    assert d.status_code == 200

    # short side
    r2 = session.post(f"{API}/me/journal", headers=auth(trader_token),
                      json={"symbol": "BTCUSDT", "side": "short", "entry": 100, "exit": 90, "size": 2})
    assert r2.json()["pnl"] == pytest.approx(20.0)
    session.delete(f"{API}/me/journal/{r2.json()['id']}", headers=auth(trader_token))


def test_layouts_crud(session, trader_token):
    r = session.post(f"{API}/me/layouts", headers=auth(trader_token), json={"name": "L1", "config": {"a": 1}})
    assert r.status_code == 200
    lid = r.json()["id"]
    g = session.get(f"{API}/me/layouts", headers=auth(trader_token))
    assert any(l["id"] == lid for l in g.json())
    session.delete(f"{API}/me/layouts/{lid}", headers=auth(trader_token))


def test_signals_dedupe(session, trader_token):
    payload = {"symbol": "BTCUSDT", "exchange": "BINANCE", "bias": "LONG BIAS", "confidence": "HIGH", "score": 0.8, "price": 100}
    r1 = session.post(f"{API}/me/signals", headers=auth(trader_token), json=payload)
    r2 = session.post(f"{API}/me/signals", headers=auth(trader_token), json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    g = session.get(f"{API}/me/signals", headers=auth(trader_token))
    assert g.status_code == 200


# ---------- admin ----------
def test_admin_overview(session, admin_token):
    r = session.get(f"{API}/admin/overview", headers=auth(admin_token))
    assert r.status_code == 200
    d = r.json()
    assert "totalUsers" in d and "feeds" in d and len(d["feeds"]) == 3


def test_admin_users_and_patch(session, admin_token):
    r = session.get(f"{API}/admin/users", headers=auth(admin_token))
    assert r.status_code == 200
    # find trader
    users = r.json()
    trader = next(u for u in users if u["email"] == TRADER["email"])
    # keep plan pro
    p = session.patch(f"{API}/admin/users/{trader['id']}", headers=auth(admin_token), json={"plan": "pro"})
    assert p.status_code == 200 and p.json()["plan"] == "pro"
    # unknown plan
    pb = session.patch(f"{API}/admin/users/{trader['id']}", headers=auth(admin_token), json={"plan": "ultra"})
    assert pb.status_code == 400


def test_admin_settings(session, admin_token):
    r = session.get(f"{API}/admin/settings", headers=auth(admin_token))
    assert r.status_code == 200 and "exchanges" in r.json()
    r2 = session.put(f"{API}/admin/settings", headers=auth(admin_token), json={"alertCooldownSec": 90})
    assert r2.status_code == 200 and r2.json()["alertCooldownSec"] == 90


def test_admin_forbidden_for_trader(session, trader_token):
    r = session.get(f"{API}/admin/overview", headers=auth(trader_token))
    assert r.status_code == 403


def test_health_feeds_connecting_or_connected(session):
    # Allow time for feeds to connect
    for _ in range(6):
        r = session.get(f"{API}/health")
        feeds = r.json()["feeds"]
        if all(f["status"] in ("connected", "connecting", "disconnected") for f in feeds):
            break
        time.sleep(2)
    # At least one should be connected eventually
    connected = [f["exchange"] for f in r.json()["feeds"] if f["status"] == "connected"]
    assert len(connected) >= 1, f"No feeds connected: {r.json()['feeds']}"
