"""
Sanal (Paper) Portföy Motoru
- Gerçek para kullanmaz. 1500$ sanal sermaye ile tarama sonuçlarına göre otomatik al-sat simülasyonu yapar.
- Giriş: Kademeli limit emirler (%40 / %40 / kırılım %20). Çıkış: Stop-loss, TP1/TP2/TP3, zaman stopu.
- Gerçekçilik: komisyon + kayma (slippage), BIST için USD/TRY çevrimi, sadece anlık fiyatla dolum (ileriye bakma yok).
- Durum bir JSON dosyasında tutulur, bilgisayar/bulut yeniden başlasa da kaldığı yerden devam eder.
"""
import os
import json
import time
import threading
from datetime import datetime, timezone

# ---------------- AYARLAR ----------------
INITIAL_CAPITAL = float(os.getenv("PAPER_CAPITAL", "1500"))
MIN_SCORE = int(os.getenv("PAPER_MIN_SCORE", "65"))          # Giriş için minimum güven puanı
MAX_OPEN = int(os.getenv("PAPER_MAX_POSITIONS", "8"))        # Aynı anda en fazla pozisyon + bekleyen plan
MAX_PER_CATEGORY = int(os.getenv("PAPER_MAX_PER_CATEGORY", "8"))
RISK_PER_TRADE = 0.015        # İşlem başına sermayenin %1.5'i riske edilir
MAX_POSITION_FRAC = 0.16      # Tek pozisyon en fazla sermayenin %16'sı
MAX_EXPOSURE_FRAC = 0.80      # Toplam açık risk en fazla %80 (en az %20 nakit)
FEE = 0.001                   # %0.1 komisyon (alış ve satışta)
SLIP = 0.0007                 # %0.07 kayma
TIER_SPLIT = (0.4, 0.4, 0.2)
PLAN_TTL_CRYPTO = 3 * 86400
PLAN_TTL_DEFAULT = 5 * 86400
MAX_HOLD = 10 * 86400
COOLDOWN_AFTER_CLOSE = 48 * 3600
MAX_CURVE_POINTS = 4000
SUMMARY_HOUR_UTC = 18         # Günlük özet: 21:00 (TR saati)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.getenv("PAPER_STATE_FILE", os.path.join(_BASE, "data", "paper_state.json"))
_lock = threading.RLock()

_fx_cache = {"v": None, "ts": 0}


# ---------------- DURUM ----------------
def _new_state():
    now = time.time()
    return {
        "version": 1,
        "initial": INITIAL_CAPITAL,
        "cash": INITIAL_CAPITAL,
        "created": now,
        "last_scan": 0,
        "fx": None,
        "plans": [],
        "positions": [],
        "closed": [],
        "cooldown": {},
        "equity_curve": [],
        "start_prices": {},
        "last_prices": {},
        "log": [],
        "last_summary_date": "",
        "last_weekly_key": "",
        "seq": 0,
    }


def load_state():
    with _lock:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
            base = _new_state()
            for k, v in base.items():
                st.setdefault(k, v)
            return st
        except Exception:
            return _new_state()


def save_state(st):
    with _lock:
        d = os.path.dirname(STATE_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)


# ---------------- YARDIMCILAR ----------------
def _get_fx(st):
    """USD/TRY kuru (30 dk önbellek). Alınamazsa son bilinen değer kullanılır."""
    now = time.time()
    if _fx_cache["v"] and now - _fx_cache["ts"] < 1800:
        return _fx_cache["v"]
    try:
        import yfinance as yf
        df = yf.download("USDTRY=X", period="5d", interval="1d", progress=False, auto_adjust=False, threads=False)
        if df is not None and not df.empty:
            col = df["Close"]
            if hasattr(col, "columns"):
                col = col.iloc[:, 0]
            v = float(col.dropna().iloc[-1])
            if v > 1:
                _fx_cache["v"] = v
                _fx_cache["ts"] = now
                return v
    except Exception:
        pass
    return st.get("fx") or _fx_cache["v"]


def _to_local(usd, cur, fx):
    return usd * fx if cur == "₺" else usd


def _to_usd(local, cur, fx):
    return local / fx if cur == "₺" else local


def _log(st, text):
    st["log"].append([int(time.time()), text])
    st["log"] = st["log"][-300:]


def _equity(st, fx):
    total = st["cash"]
    for p in st["plans"]:
        total += p["reserved"]
    for pos in st["positions"]:
        price = st["last_prices"].get(pos["symbol"], pos.get("avg_entry", 0))
        total += _to_usd(pos["qty"] * price, pos["currency"], fx or 1)
    return total


def _fmt(v, cur):
    if v is None:
        return "-"
    n = float(v)
    s = f"{n:,.2f}" if n >= 100 else (f"{n:,.3f}" if n >= 1 else f"{n:.5f}")
    return f"{cur}{s}"


def _release_plan(st, plan):
    st["cash"] += plan["reserved"]
    plan["reserved"] = 0.0
    st["plans"] = [p for p in st["plans"] if p["id"] != plan["id"]]


def _cancel_plan_for_symbol(st, symbol):
    for p in list(st["plans"]):
        if p["symbol"] == symbol:
            _release_plan(st, p)


def _find_pos(st, symbol):
    for pos in st["positions"]:
        if pos["symbol"] == symbol:
            return pos
    return None


# ---------------- ALIŞ / SATIŞ ----------------
def _sell(st, pos, qty, price, fx):
    qty = min(qty, pos["qty"])
    if qty <= 0:
        return 0.0
    sell_price = price * (1 - SLIP)
    usd = _to_usd(qty * sell_price, pos["currency"], fx) * (1 - FEE)
    pos["proceeds_usd"] += usd
    pos["qty"] -= qty
    st["cash"] += usd
    return usd


def _close_position(st, pos, price, fx, reason, now, events):
    if pos["qty"] > 1e-12:
        _sell(st, pos, pos["qty"], price, fx)
    cur = pos["currency"]
    invested = pos["invested_usd"]
    pnl = pos["proceeds_usd"] - invested
    pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
    avg = pos["avg_entry"]
    risk_frac = max((avg - pos["stop0"]) / avg, 1e-9) if avg > 0 else 1e-9
    risk_usd = invested * risk_frac
    r_mult = pnl / risk_usd if risk_usd > 0 else 0.0
    st["closed"].append({
        "id": pos["id"], "symbol": pos["symbol"], "short": pos["short"],
        "category": pos["category"], "currency": cur, "score": pos["score"],
        "opened": pos["opened"], "closed": int(now),
        "hold_hours": round((now - pos["opened"]) / 3600.0, 1),
        "avg_entry": pos["avg_entry"], "exit_price": price,
        "invested_usd": round(invested, 2), "pnl_usd": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2), "r": round(r_mult, 2),
        "reason": reason, "tp_hit": pos["tp_hit"],
    })
    st["positions"] = [p for p in st["positions"] if p["id"] != pos["id"]]
    _cancel_plan_for_symbol(st, pos["symbol"])
    st["cooldown"][pos["symbol"]] = now
    icon = "✅" if pnl >= 0 else "🔻"
    msg = (f"{icon} *SANAL KAPANIŞ — {pos['short']}* ({reason})\n"
           f"Çıkış: `{_fmt(price, cur)}` | Ort. giriş: `{_fmt(avg, cur)}`\n"
           f"K/Z: `{pnl:+.2f}$` ({pnl_pct:+.2f}%) | {r_mult:+.2f}R | Skor: {pos['score']}")
    events.append(msg)
    _log(st, f"KAPANIŞ {pos['short']} {reason} {pnl:+.2f}$ ({pnl_pct:+.2f}%)")


# ---------------- ANA MANTIK ----------------
def _manage_positions(st, prices, fx, now, events):
    for pos in list(st["positions"]):
        s = prices.get(pos["symbol"])
        if not s:
            continue
        p = s["price"]
        cur = pos["currency"]

        # 1) Stop
        if p <= pos["stop"]:
            reason = {0: "STOP-LOSS", 1: "BAŞABAŞ STOP", 2: "İZLEYEN STOP"}.get(pos["tp_hit"], "STOP")
            _close_position(st, pos, p, fx, reason, now, events)
            continue

        # 2) Zaman stopu
        if now - pos["opened"] > MAX_HOLD:
            _close_position(st, pos, p, fx, "ZAMAN STOPU (10 gün)", now, events)
            continue

        # 3) Kâr al kademeleri
        while pos["tp_hit"] < 3 and pos["qty"] > 1e-12 and p >= pos[f"tp{pos['tp_hit'] + 1}"]:
            k = pos["tp_hit"] + 1
            if k == 1:
                sell_qty = 0.4 * pos["orig_qty"]
            elif k == 2:
                sell_qty = 0.3 * pos["orig_qty"]
            else:
                sell_qty = pos["qty"]
            usd = _sell(st, pos, sell_qty, p, fx)
            pos["tp_hit"] = k
            if k == 1:
                pos["stop"] = max(pos["stop"], pos["avg_entry"])
                _cancel_plan_for_symbol(st, pos["symbol"])
            elif k == 2:
                pos["stop"] = max(pos["stop"], pos["tp1"])
            events.append(
                f"🎯 *SANAL KÂR AL TP{k} — {pos['short']}*\n"
                f"Fiyat: `{_fmt(p, cur)}` | Satılan: `{usd:.2f}$` | Yeni stop: `{_fmt(pos['stop'], cur)}`")
            _log(st, f"TP{k} {pos['short']} @ {p}")
            if pos["qty"] <= 1e-12:
                _close_position(st, pos, p, fx, "TP3 (tam çıkış)", now, events)
                break


def _process_plans(st, prices, fx, now, events):
    for plan in list(st["plans"]):
        s = prices.get(plan["symbol"])
        if now > plan["expires"]:
            _release_plan(st, plan)
            events.append(f"⌛ Sanal emir süresi doldu (dolmadı): *{plan['short']}*")
            _log(st, f"PLAN SÜRESİ DOLDU {plan['short']}")
            continue
        if not s:
            continue
        p = s["price"]
        cur = plan["currency"]
        if p <= plan["stop"]:
            _release_plan(st, plan)
            events.append(f"🚫 Sanal emir iptal (fiyat stop altına indi): *{plan['short']}*")
            continue
        if cur == "₺" and not fx:
            continue

        for t in plan["tiers"]:
            if t["filled"]:
                continue
            if t["kind"] == "limit":
                trig = p <= t["price"]
                base = min(p, t["price"])
            else:
                trig = p >= t["price"]
                base = max(p, t["price"])
            if not trig:
                continue
            fill_price = base * (1 + SLIP)
            usd = t["usd"]
            qty = _to_local(usd * (1 - FEE), cur, fx) / fill_price

            pos = _find_pos(st, plan["symbol"])
            if pos is None:
                pos = {
                    "id": plan["id"], "symbol": plan["symbol"], "short": plan["short"],
                    "category": plan["category"], "currency": cur, "tv_symbol": plan.get("tv_symbol", ""),
                    "score": plan["score"], "opened": int(now), "qty": 0.0, "orig_qty": 0.0,
                    "cost_local": 0.0, "invested_usd": 0.0, "proceeds_usd": 0.0,
                    "avg_entry": 0.0, "stop": plan["stop"], "stop0": plan["stop"],
                    "tp1": plan["tp1"], "tp2": plan["tp2"], "tp3": plan["tp3"], "tp_hit": 0,
                }
                st["positions"].append(pos)
            pos["qty"] += qty
            pos["orig_qty"] += qty
            pos["cost_local"] += qty * fill_price
            pos["invested_usd"] += usd
            pos["avg_entry"] = pos["cost_local"] / pos["orig_qty"]

            t["filled"] = True
            plan["reserved"] -= usd
            tier_no = plan["tiers"].index(t) + 1
            events.append(
                f"📥 *SANAL ALIŞ — {plan['short']}* (Kademe {tier_no}/3)\n"
                f"Fiyat: `{_fmt(fill_price, cur)}` | Tutar: `{usd:.2f}$`\n"
                f"Stop: `{_fmt(plan['stop'], cur)}` | TP1 `{_fmt(plan['tp1'], cur)}`")
            _log(st, f"ALIŞ {plan['short']} kademe {tier_no} @ {fill_price:.4f} ({usd:.2f}$)")

        if all(t["filled"] for t in plan["tiers"]):
            st["plans"] = [x for x in st["plans"] if x["id"] != plan["id"]]


def _create_plans(st, setups, fx, now, events):
    equity = _equity(st, fx)
    exposure = sum(p["reserved"] for p in st["plans"])
    for pos in st["positions"]:
        price = st["last_prices"].get(pos["symbol"], pos["avg_entry"])
        exposure += _to_usd(pos["qty"] * price, pos["currency"], fx or 1)

    slots = len(st["positions"]) + len(st["plans"])
    cat_count = {}
    for p in st["positions"] + st["plans"]:
        cat_count[p["category"]] = cat_count.get(p["category"], 0) + 1

    cands = sorted([s for s in setups if s["score"] >= MIN_SCORE], key=lambda x: -x["score"])
    for s in cands:
        if slots >= MAX_OPEN:
            break
        sym = s["symbol"]
        if _find_pos(st, sym) or any(p["symbol"] == sym for p in st["plans"]):
            continue
        if now - st["cooldown"].get(sym, 0) < COOLDOWN_AFTER_CLOSE:
            continue
        if cat_count.get(s["category"], 0) >= MAX_PER_CATEGORY:
            continue
        cur = s["currency"]
        if cur == "₺" and not fx:
            continue

        e1, e2, eb = s["entry_1"], s["entry_2"], s["entry_breakout"]
        stop = s["stop_loss"]
        price = s["price"]
        if not (0 < stop < e2 < e1 < price < eb):
            continue
        avg_full = TIER_SPLIT[0] * e1 + TIER_SPLIT[1] * e2 + TIER_SPLIT[2] * eb
        stop_frac = (avg_full - stop) / avg_full
        if not (0.01 <= stop_frac <= 0.25):
            continue

        budget = min(equity * MAX_POSITION_FRAC, equity * RISK_PER_TRADE / stop_frac)
        budget = min(budget, st["cash"], equity * MAX_EXPOSURE_FRAC - exposure)
        if budget < 50:
            continue

        st["seq"] += 1
        pid = f"P{st['seq']}"
        ttl = PLAN_TTL_CRYPTO if s["category"] == "KRİPTO" else PLAN_TTL_DEFAULT
        plan = {
            "id": pid, "symbol": sym, "short": s["short"], "category": s["category"],
            "currency": cur, "tv_symbol": s.get("tv_symbol", ""), "score": s["score"],
            "created": int(now), "expires": int(now + ttl), "price_at_plan": price,
            "stop": stop, "tp1": s["tp1"], "tp2": s["tp2"], "tp3": s["tp3"],
            "reserved": round(budget, 4), "budget": round(budget, 2),
            "tiers": [
                {"kind": "limit", "price": e1, "usd": round(budget * TIER_SPLIT[0], 4), "filled": False},
                {"kind": "limit", "price": e2, "usd": round(budget * TIER_SPLIT[1], 4), "filled": False},
                {"kind": "breakout", "price": eb, "usd": round(budget * TIER_SPLIT[2], 4), "filled": False},
            ],
        }
        plan["reserved"] = round(sum(t["usd"] for t in plan["tiers"]), 4)
        st["cash"] -= plan["reserved"]
        st["plans"].append(plan)
        slots += 1
        exposure += plan["reserved"]
        cat_count[s["category"]] = cat_count.get(s["category"], 0) + 1
        events.append(
            f"📝 *YENİ SANAL EMİR PLANI — {s['short']}* ({s['category']} · Skor {s['score']}/100)\n"
            f"Fiyat: `{_fmt(price, cur)}` | Bütçe: `{budget:.2f}$`\n"
            f"Kademe 1: `{_fmt(e1, cur)}` | Kademe 2: `{_fmt(e2, cur)}` | Kırılım: `{_fmt(eb, cur)}`\n"
            f"🛑 Stop: `{_fmt(stop, cur)}` (-%{stop_frac * 100:.1f}) | 🎯 TP1 `{_fmt(s['tp1'], cur)}` TP2 `{_fmt(s['tp2'], cur)}` TP3 `{_fmt(s['tp3'], cur)}`")
        _log(st, f"PLAN {s['short']} skor {s['score']} bütçe {budget:.0f}$")


# ---------------- İSTATİSTİK ----------------
def compute_stats(st):
    closed = st["closed"]
    n = len(closed)
    wins = [c for c in closed if c["pnl_usd"] > 0]
    losses = [c for c in closed if c["pnl_usd"] <= 0]
    gross_win = sum(c["pnl_usd"] for c in wins)
    gross_loss = -sum(c["pnl_usd"] for c in losses)
    stats = {
        "closed_count": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (None if not wins else 99.0),
        "avg_r": round(sum(c["r"] for c in closed) / n, 2) if n else None,
        "realized_pnl": round(sum(c["pnl_usd"] for c in closed), 2),
    }
    curve = [v for _, v in st["equity_curve"]]
    mdd = 0.0
    if curve:
        peak = curve[0]
        for v in curve:
            peak = max(peak, v)
            if peak > 0:
                mdd = max(mdd, (peak - v) / peak)
    stats["max_drawdown_pct"] = round(mdd * 100, 2)

    by_cat, by_bucket = {}, {}
    for c in closed:
        d = by_cat.setdefault(c["category"], {"n": 0, "pnl": 0.0, "wins": 0})
        d["n"] += 1
        d["pnl"] += c["pnl_usd"]
        d["wins"] += 1 if c["pnl_usd"] > 0 else 0
        b = "90+" if c["score"] >= 90 else ("75-89" if c["score"] >= 75 else "65-74")
        e = by_bucket.setdefault(b, {"n": 0, "pnl": 0.0, "wins": 0})
        e["n"] += 1
        e["pnl"] += c["pnl_usd"]
        e["wins"] += 1 if c["pnl_usd"] > 0 else 0
    for dct in (by_cat, by_bucket):
        for v in dct.values():
            v["pnl"] = round(v["pnl"], 2)
            v["win_rate"] = round(v["wins"] / v["n"] * 100, 1) if v["n"] else None
    stats["by_category"] = by_cat
    stats["by_score_bucket"] = by_bucket

    sp, lp = st["start_prices"], st["last_prices"]
    rets = [(lp[k] / sp[k] - 1) * 100 for k in sp if k in lp and sp[k] > 0]
    stats["bench_universe_pct"] = round(sum(rets) / len(rets), 2) if rets else None
    btc0, btc1 = sp.get("BTCUSDT"), lp.get("BTCUSDT")
    stats["bench_btc_pct"] = round((btc1 / btc0 - 1) * 100, 2) if btc0 and btc1 else None
    return stats


def get_dashboard():
    with _lock:
        st = load_state()
    fx = st.get("fx") or 1
    equity = _equity(st, fx)
    positions = []
    unreal = 0.0
    for pos in st["positions"]:
        price = st["last_prices"].get(pos["symbol"], pos["avg_entry"])
        value = _to_usd(pos["qty"] * price, pos["currency"], fx)
        pnl = value + pos["proceeds_usd"] - pos["invested_usd"]
        unreal += pnl
        positions.append({
            "short": pos["short"], "category": pos["category"], "currency": pos["currency"],
            "score": pos["score"], "avg_entry": pos["avg_entry"], "price": price,
            "stop": pos["stop"], "tp1": pos["tp1"], "tp2": pos["tp2"], "tp3": pos["tp3"],
            "tp_hit": pos["tp_hit"], "value_usd": round(value, 2),
            "invested_usd": round(pos["invested_usd"], 2), "pnl_usd": round(pnl, 2),
            "pnl_pct": round(pnl / pos["invested_usd"] * 100, 2) if pos["invested_usd"] else 0,
            "opened": pos["opened"], "tv_symbol": pos.get("tv_symbol", ""),
        })
    plans = []
    for p in st["plans"]:
        price = st["last_prices"].get(p["symbol"], p["price_at_plan"])
        plans.append({
            "short": p["short"], "category": p["category"], "currency": p["currency"],
            "score": p["score"], "price": price, "budget": p["budget"],
            "tiers": [{"kind": t["kind"], "price": t["price"], "filled": t["filled"]} for t in p["tiers"]],
            "stop": p["stop"], "expires": p["expires"],
        })
    curve = st["equity_curve"]
    if len(curve) > 240:
        step = len(curve) / 240.0
        curve = [curve[int(i * step)] for i in range(240)] + [curve[-1]]
    now = time.time()
    return {
        "initial": st["initial"],
        "equity": round(equity, 2),
        "return_pct": round((equity / st["initial"] - 1) * 100, 2),
        "cash": round(st["cash"], 2),
        "reserved": round(sum(p["reserved"] for p in st["plans"]), 2),
        "unrealized_pnl": round(unreal, 2),
        "fx": st.get("fx"),
        "started": st["created"],
        "days_running": round((now - st["created"]) / 86400.0, 2),
        "last_scan": st.get("last_scan", 0),
        "positions": positions,
        "plans": plans,
        "closed": list(reversed(st["closed"][-40:])),
        "equity_curve": curve,
        "stats": compute_stats(st),
        "log": list(reversed(st["log"][-25:])),
        "config": {"min_score": MIN_SCORE, "max_open": MAX_OPEN, "risk_per_trade_pct": RISK_PER_TRADE * 100,
                   "fee_pct": FEE * 100, "slip_pct": SLIP * 100},
    }


def _summary_text(st, fx, weekly=False):
    eq = _equity(st, fx)
    stats = compute_stats(st)
    wr = f"%{stats['win_rate']}" if stats["win_rate"] is not None else "-"
    header = "📅 *SANAL PORTFÖY HAFTALIK RAPOR*" if weekly else "📊 *SANAL PORTFÖY GÜNLÜK RAPOR*"
    lines = [
        header,
        f"💰 Varlık: `{eq:.2f}$` ({(eq / st['initial'] - 1) * 100:+.2f}%) | Nakit: `{st['cash']:.2f}$`",
        f"📈 Açık: {len(st['positions'])} | Bekleyen: {len(st['plans'])} | Kapanan: {stats['closed_count']} | Kazanma: {wr}",
        f"📏 Kıyas → Evren: {stats['bench_universe_pct']}% | BTC: {stats['bench_btc_pct']}%",
    ]

    if st["positions"]:
        lines.append("\n🟢 *Açık Pozisyonlar:*")
        for pos in st["positions"]:
            c = pos["currency"]
            p = st["last_prices"].get(pos["symbol"], pos["avg_entry"])
            val = _to_usd(pos["qty"] * p, c, fx or 1)
            pnl = val + pos["proceeds_usd"] - pos["invested_usd"]
            lines.append(
                f"• *{pos['short']}* ({pos['category']}) | Giriş `{_fmt(pos['avg_entry'], c)}` → `{_fmt(p, c)}` (`{pnl:+.2f}$`)\n"
                f"  🛑 Stop: `{_fmt(pos['stop'], c)}` | 🎯 TP1 `{_fmt(pos['tp1'], c)}` / TP2 `{_fmt(pos['tp2'], c)}` / TP3 `{_fmt(pos['tp3'], c)}`"
            )

    if st["plans"]:
        lines.append("\n📝 *Bekleyen Emir Planları (Kademeler & Kırılım):*")
        for pl in st["plans"]:
            c = pl["currency"]
            t1, t2, t3 = pl["tiers"][0], pl["tiers"][1], pl["tiers"][2]
            lines.append(
                f"• *{pl['short']}* ({pl['category']} · Skor {pl['score']}/100 · `{pl['budget']:.0f}$`)\n"
                f"  📥 K1 `{_fmt(t1['price'], c)}` | K2 `{_fmt(t2['price'], c)}` | ⚡ Kırılım `{_fmt(t3['price'], c)}` | 🛑 Stop `{_fmt(pl['stop'], c)}`"
            )

    window_sec = 7 * 86400 if weekly else 86400
    cutoff = time.time() - window_sec
    recent_closed = [c for c in st["closed"] if c.get("closed", 0) >= cutoff]
    if recent_closed:
        period_lbl = "Son 7 Gün" if weekly else "Bugün"
        tot_pnl = sum(c["pnl_usd"] for c in recent_closed)
        lines.append(f"\n✅ *{period_lbl} Kapanan İşlemler ({len(recent_closed)} adet · `{tot_pnl:+.2f}$`):*")
        for cl in recent_closed[-10:]:
            icon = "✅" if cl["pnl_usd"] >= 0 else "🔻"
            lines.append(f"{icon} *{cl['short']}* ({cl['reason']}): `{cl['pnl_usd']:+.2f}$` ({cl['pnl_pct']:+.1f}%)")

    lines.append("\n_Sanal simülasyon, gerçek para değildir._")
    return "\n".join(lines)


def build_report(weekly=False):
    """Telegram /sanal veya /rapor komutu için anlık tam rapor üretir."""
    with _lock:
        st = load_state()
    fx = st.get("fx") or _get_fx(st) or 1
    return _summary_text(st, fx, weekly=weekly)


def process_scan(setups, notify=None, now=None):
    """Her tarama sonrası çağrılır: pozisyonları yönetir, bekleyen emirleri doldurur, yeni plan açar."""
    if not setups:
        return None
    with _lock:
        st = load_state()
        now = now or time.time()
        events = []
        fx = _get_fx(st)
        st["fx"] = fx
        prices = {s["symbol"]: s for s in setups}
        for s in setups:
            st["start_prices"].setdefault(s["symbol"], s["price"])
            st["last_prices"][s["symbol"]] = s["price"]

        _manage_positions(st, prices, fx, now, events)
        _process_plans(st, prices, fx, now, events)
        _create_plans(st, setups, fx, now, events)

        equity = _equity(st, fx)
        st["equity_curve"].append([int(now), round(equity, 2)])
        st["equity_curve"] = st["equity_curve"][-MAX_CURVE_POINTS:]
        st["last_scan"] = int(now)

        utc = datetime.fromtimestamp(now, tz=timezone.utc)
        date_str = utc.strftime("%Y-%m-%d")
        week_key = utc.strftime("%Y-W%W")
        if notify and utc.hour >= SUMMARY_HOUR_UTC and st["last_summary_date"] != date_str:
            events.append(_summary_text(st, fx, weekly=False))
            st["last_summary_date"] = date_str
        # Haftalık rapor: Pazar günleri 21:00 TR (UTC 18:00)
        if notify and utc.weekday() == 6 and utc.hour >= SUMMARY_HOUR_UTC and st.get("last_weekly_key") != week_key:
            events.append(_summary_text(st, fx, weekly=True))
            st["last_weekly_key"] = week_key

        save_state(st)

    if events and notify:
        # Telegram 4096 karakter sınırı: olayları parçalara böl
        chunk, size = [], 0
        for ev in events:
            if size + len(ev) > 3500 and chunk:
                try:
                    notify("\n\n".join(chunk))
                except Exception:
                    pass
                chunk, size = [], 0
            chunk.append(ev)
            size += len(ev) + 2
        if chunk:
            try:
                notify("\n\n".join(chunk))
            except Exception:
                pass
    return {"equity": equity, "events": len(events)}
