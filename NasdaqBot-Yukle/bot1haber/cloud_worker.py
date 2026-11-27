"""
Bulut NASDAQ Günlük Al-Sat (Day-Trade) Sniper + Görsel Grafik + AI Analiz İşçisi
- Tüm NASDAQ borsasını (4.300+ hisse canlı ön eleme + 800 hisse derin analiz) tarar.
- ABD piyasa saati, piyasa rejimi (NDX/VIX), 7-45 DTE opsiyon akışı, 15m VWAP, bilanço koruması ve ucuz hisse filtresi uygular.
- Gerçekçi aracı kurum komisyonu ($1.50/emir + %0.05 kesinti + %0.07 kayma) ile 1500$ sanal portföyü yönetir.
- Mum grafiği (PNG) + Groq AI analizi üretir ve Telegram'a görsel olarak gönderir.
- Komutlar: /help, /durum, /sanal, /haftalik, /oneriler, /yukselenler, /analiz XLAB, /grafik NVDA, /ekle, /sil, /liste, /alarm
"""
import os
import sys
import json
import time
import hashlib
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import requests
import server
import chart_ai

STATE_FILE = os.getenv("STATE_FILE", "sniper_state.json")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
MIN_SCORE = int(os.getenv("SNIPER_MIN_SCORE", "90"))

DEDUP_SECONDS = {"KRİPTO": 6 * 3600}
DEFAULT_DEDUP = 24 * 3600
TR_TZ = timezone(timedelta(hours=3))


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def send_telegram(text, chat_id=None):
    target_chat = str(chat_id or CHAT_ID)
    if DRY_RUN:
        print(f"---- [DRY RUN -> {target_chat}] Telegram mesajı ----")
        print(text)
        print("--------------------------------------------------")
        return True
    if not TG_TOKEN or not target_chat:
        print("HATA: TELEGRAM_TOKEN / CHAT_ID tanımlı değil.")
        return False
    if len(text) > 3950:
        text = text[:3900] + "\n\n_(Devamı kısaltıldı)_"
    for attempt in range(3):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    "chat_id": target_chat,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 10)
                time.sleep(min(int(wait) + 1, 40))
                continue
            if r.status_code == 400 and "parse" in r.text.lower():
                r2 = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": target_chat, "text": text, "disable_web_page_preview": True},
                    timeout=15,
                )
                return r2.status_code == 200
            print(f"Telegram hata {r.status_code}: {r.text[:200]}")
            return False
        except Exception as e:
            print(f"Telegram istisna: {e}")
            time.sleep(3)
    return False


def send_telegram_photo(image_path, caption="", chat_id=None):
    target_chat = str(chat_id or CHAT_ID)
    if DRY_RUN:
        print(f"---- [DRY RUN PHOTO -> {target_chat}] {image_path} ----")
        print(caption)
        print("------------------------------------------------------")
        return True
    if not TG_TOKEN or not target_chat or not image_path or not os.path.exists(image_path):
        return False
    cap = caption[:1000] if len(caption) > 1000 else caption
    for attempt in range(2):
        try:
            with open(image_path, "rb") as f:
                r = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                    data={"chat_id": target_chat, "caption": cap, "parse_mode": "Markdown"},
                    files={"photo": f},
                    timeout=25,
                )
            if r.status_code == 200:
                return True
            if r.status_code == 400 and "parse" in r.text.lower():
                with open(image_path, "rb") as f:
                    r2 = requests.post(
                        f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                        data={"chat_id": target_chat, "caption": cap},
                        files={"photo": f},
                        timeout=25,
                    )
                return r2.status_code == 200
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 10)
                time.sleep(min(int(wait) + 1, 35))
                continue
        except Exception as e:
            print(f"Telegram photo hata: {e}")
    return False


def send_chart_with_ai(setup, regime_label="NÖTR", chat_id=None):
    """Hisse için mum grafiği (PNG) + Groq AI analizi oluşturur ve Telegram'a görsel olarak gönderir."""
    sym = setup["symbol"]
    df = server.LAST_DF_CACHE.get(sym)
    if df is None:
        df = server._fetch_yfinance_ohlcv(sym)
        if df is not None:
            server.LAST_DF_CACHE[sym] = df
    ai_txt = chart_ai.generate_ai_commentary(setup, regime_label=regime_label)
    c = setup["currency"]
    vwap_s = f" | VWAP `{c}{setup['vwap']}`" if setup.get("vwap") else ""
    earn_s = "\n⚠️ *UYARI: Yakın bilanço tarihi riski!*" if setup.get("earnings_warning") else ""
    caption = (
        f"📊 *{setup['short']}* — Skor `{setup['score']}/100` ({setup['decision']})\n"
        f"💰 Fiyat: `{c}{setup['price']}` ({setup['change']:+.2f}%){vwap_s}\n"
        f"⚡ Kırılım: `{c}{setup['breakout']}` | 🛡️ Destek: `{c}{setup['entry_1']}`\n"
        f"🛑 Stop: `{c}{setup['stop_loss']}` (-%{setup['risk_pct']}) | 🎯 TP1 `{c}{setup['tp1']}` / TP2 `{c}{setup['tp2']}`{earn_s}\n\n"
        f"🤖 *AI Analizi:*\n{ai_txt}\n\n"
        f"_Yatırım tavsiyesi değildir._"
    )
    png_path = chart_ai.render_setup_chart(df, setup)
    if png_path:
        ok = send_telegram_photo(png_path, caption=caption, chat_id=chat_id)
        try:
            if not DRY_RUN:
                os.remove(png_path)
        except Exception:
            pass
        if ok:
            return True
    return send_telegram(caption, chat_id=chat_id)


def build_message(s, ai_txt=None):
    c = s["currency"]
    opt_line = f"📊 {s['options']['summary']}\n" if s.get("options") and s["options"].get("summary") else ""
    vwap_line = f"📏 Gün İçi VWAP: `{c}{s['vwap']}` ({'Üstünde 🟢' if s['price'] >= s['vwap'] else 'Altında 🔴'})\n" if s.get("vwap") else ""
    news_line = f"📰 {s.get('news_bias', 'HABER')}: _{s['latest_news'][:85]}_\n" if s.get("latest_news") else ""
    earn_line = "⚠️ *Yakın Bilanço Uyarısı (Otomatik giriş kapalı)*\n" if s.get("earnings_warning") else ""
    ai_block = f"\n🤖 *AI Analizi:*\n{ai_txt}\n" if ai_txt else ""
    return (
        f"🎯 *NASDAQ GÜNLÜK AL-SAT FIRSATI ({s['score']}/100)*\n"
        f"📌 *{s['short']}* ({s.get('sector', 'NASDAQ')})\n"
        f"💰 Anlık: `{c}{s['price']}` ({s['change']:+.2f}%) | ATR: `%{s.get('atr_pct', '-')}`\n\n"
        f"⚡ Kırılım (R1): `{c}{s['breakout']}` (+%{s['breakout_dist_pct']})\n"
        f"🛡️ Destekler: `{c}{s['support_1']}` / `{c}{s['support_2']}`\n\n"
        f"📥 *Kademeli Giriş:*\n"
        f"• 1. Kademe Anlık (%60): `{c}{s['price']}`\n"
        f"• 2. Kademe Destek (%25): `{c}{s['entry_1']}`\n"
        f"• 3. Kademe Kırılım (%15): `{c}{s['entry_breakout']}`\n\n"
        f"🛑 *Stop-Loss:* `{c}{s['stop_loss']}` (-%{s['risk_pct']})\n"
        f"🎯 *Hedefler:* TP1 `{c}{s['tp1']}` | TP2 `{c}{s['tp2']}` | TP3 `{c}{s['tp3']}`\n"
        f"{vwap_line}{opt_line}{news_line}{earn_line}{ai_block}\n"
        f"_Teknik seviyelerdir, yatırım tavsiyesi değildir._"
    )


def build_movers_message(movers, limit=10):
    total_u = server.LIVE_MOVERS_CACHE.get("total_universe", 0) or 4300
    if not movers:
        return f"🔥 *NASDAQ GÜNÜN YÜKSELENLERİ ({total_u:,}+ hisse)*\nŞu an +%6 üstü hacimli hareket tespit edilmedi."
    lines = [f"🔥 *NASDAQ GÜNÜN SERT YÜKSELENLERİ ({total_u:,}+ hisse tarandı)*\n"]
    for i, m in enumerate(movers[:limit], 1):
        vol_m = m["volume"] / 1_000_000.0
        vol_s = f"{vol_m:.1f}M" if vol_m >= 1 else f"{m['volume']//1000}K"
        lines.append(f"{i}. *{m['symbol']}* — `${m['price']:.2f}` (`+{m['pct']:.1f}%`) | Hacim: `{vol_s}`")
    lines.append("\n💡 Mum grafik + AI analiz için: `/analiz SEMBOL`")
    return "\n".join(lines)


def build_gap_message(gappers, market_status):
    if not gappers:
        return f"🌅 *PRE-MARKET / AÇILIŞ GAP RADARI* (`{market_status}`)\nŞu an $3+ ve +%4 üstü hacimli gap adayı tespit edilmedi."
    lines = [f"🌅 *PRE-MARKET / AÇILIŞ GAP ADAYLARI* (`{market_status}`)\n"]
    for i, m in enumerate(gappers[:8], 1):
        vol_m = m["volume"] / 1_000_000.0
        vol_s = f"{vol_m:.1f}M" if vol_m >= 1 else f"{m['volume']//1000}K"
        lines.append(f"{i}. *{m['symbol']}* — `${m['price']:.2f}` (`+{m['pct']:.1f}%`) | Hacim: `{vol_s}`")
    lines.append("\n💡 Mum grafik + AI analiz için: `/analiz SEMBOL`")
    return "\n".join(lines)


def build_15min_bulletin(setups, market_open, market_status, regime, paper_summary=None):
    tr_now = datetime.now(TR_TZ).strftime("%H:%M")
    total_u = server.LIVE_MOVERS_CACHE.get("total_universe", 0) or len(setups)
    vix_s = f" | VIX: `{regime['vix']}`" if regime.get("vix") else ""
    lines = [
        f"⏱️ *NASDAQ CANLI BÜLTEN ({tr_now} TR)*",
        f"🏛️ Borsa: `{market_status}` | Rejim: *{regime['label']}*{vix_s}",
        f"🔎 Evren: `{total_u:,}` NASDAQ hissesi | Derin Analiz: `{len(setups)}` hisse\n",
        "🏆 *GÜNLÜK AL-SAT EN İYİ 5 ADAY:*",
    ]
    # Otomatik işlem kalitesini sağlayanları (fiyat>=$3, likit) öne çıkar
    tradeable = [s for s in setups if s.get("tradeable", True)]
    picks = (tradeable[:5] if len(tradeable) >= 5 else setups[:5])
    for i, s in enumerate(picks, 1):
        c = s["currency"]
        vwap_s = f" | VWAP `{c}{s['vwap']}`" if s.get("vwap") else ""
        badges = []
        if s.get("orb_breakout"):
            badges.append("🚀ORB")
        if s.get("oversold_bounce"):
            badges.append("🔄Dönüş")
        if s.get("squeeze_candidate"):
            badges.append(f"🔥Squeeze %{s.get('short_float_pct')}")
        if (s.get("options") or {}).get("unusual"):
            badges.append("⚡Olağandışı CALL")
        badge_s = (" · " + " ".join(badges)) if badges else ""
        opt_txt = f"\n   📊 {s['options']['summary']}" if s.get("options") and s["options"].get("summary") else ""
        lines.append(
            f"*{i}. {s['short']}* ({s.get('sector', 'NASDAQ')}) — Skor `{s['score']}/100` ({s['decision']}){badge_s}\n"
            f"   💰 `{c}{s['price']}` ({s['change']:+.2f}%){vwap_s} | ⚡ Kırılım `{c}{s['breakout']}`\n"
            f"   🛑 Stop `{c}{s['stop_loss']}` (-%{s['risk_pct']}) | 🎯 TP1 `{c}{s['tp1']}` / TP2 `{c}{s['tp2']}`{opt_txt}"
        )

    if "PRE-MARKET" in market_status:
        gappers = server.get_premarket_gappers(limit=5)
        if gappers:
            lines.append("\n🌅 *PRE-MARKET GAP & AÇILIŞ ADAYLARI ($3+):*")
            for m in gappers:
                vol_m = m["volume"] / 1_000_000.0
                vol_s = f"{vol_m:.1f}M" if vol_m >= 1 else f"{m['volume']//1000}K"
                lines.append(f"• *{m['symbol']}* `${m['price']:.2f}` (`+{m['pct']:.1f}%` · Hacim `{vol_s}`)")
    else:
        movers = server.get_live_nasdaq_movers(limit=5)
        if movers:
            lines.append("\n🔥 *GÜNÜN EN SERT YÜKSELENLERİ (Hacimli):*")
            for m in movers:
                vol_m = m["volume"] / 1_000_000.0
                vol_s = f"{vol_m:.1f}M" if vol_m >= 1 else f"{m['volume']//1000}K"
                lines.append(f"• *{m['symbol']}* `${m['price']:.2f}` (`+{m['pct']:.1f}%` · Hacim `{vol_s}`)")

    if paper_summary:
        lines.append(
            f"\n💼 *Sanal Portföy (Net):* `{paper_summary['equity']:.2f}$` (`{paper_summary['return_pct']:+.2f}%`) | "
            f"Açık: `{paper_summary['open_count']}` | Nakit: `{paper_summary['cash']:.2f}$` | "
            f"💸 Komisyon: `-{paper_summary['commissions']:.2f}$`"
        )

    lines.append("\n💬 `/analiz XLAB` (Grafik+AI) · `/ekle XLAB` · `/alarm XLAB 2.00` · `/sanal` · `/help`")
    lines.append("_Yatırım tavsiyesi değildir._")
    return "\n".join(lines)


def build_help_message():
    return (
        "🤖 *NASDAQ BOT KOMUT LİSTESİ (7/24 Bulut)*\n\n"
        "• `/analiz XLAB` veya `/grafik NVDA` — Mum grafiği (PNG) + Groq AI analizi + kırılım/stop/TP\n"
        "• `/durum` — Borsa saati, piyasa rejimi (NDX/VIX), son tarama ve net kâr/zarar\n"
        "• `/sanal` veya `/rapor` — 1500$ sanal portföy + komisyon dökümü\n"
        "• `/haftalik` — Haftalık performans raporu\n"
        "• `/oneriler` — En iyi 5 günlük al-sat adayı\n"
        "• `/yukselenler` — 4.300+ NASDAQ içinde günün sert yükselenleri\n"
        "• `/gap` — Pre-Market / Açılış gap adayları ($3+, +%4 ve üstü)\n"
        "• `/ekle XLAB` / `/sil XLAB` / `/liste` — Kişisel takip listen\n"
        "• `/alarm XLAB 2.00` — Fiyat alarmı kur (hedef geçilince anında uyarır)\n"
        "• `/help` — Bu yardım menüsü\n\n"
        "ℹ️ _Not: Bulut tarayıcı 15 dakikalık periyotlarla çalışır; borsa kapalıyken sessiz moda (2 saatte bir özet) geçer, komutlarını ise her turda yanıtlar._"
    )


def build_status_message(setups, market_status, regime, paper_dash=None):
    tr_now = datetime.now(TR_TZ).strftime("%d.%m.%Y %H:%M")
    total_u = server.LIVE_MOVERS_CACHE.get("total_universe", 0) or len(setups)
    vix_s = f" | VIX `{regime['vix']}`" if regime.get("vix") else ""
    lines = [
        "📡 *NASDAQ BOT DURUM RAPORU*",
        f"🕒 Son Tarama: `{tr_now} TR` | 🏛️ Borsa: `{market_status}`",
        f"🌡️ Piyasa Rejimi: *{regime['label']}*{vix_s}",
        f"🔎 Kapsam: `{total_u:,}` NASDAQ hissesi (`{len(setups)}` derin teknik + VWAP + opsiyon)",
    ]
    if paper_dash:
        lines.append(
            f"💰 Net Sanal Varlık: `{paper_dash['equity']:.2f}$` (`{paper_dash['return_pct']:+.2f}%`)\n"
            f"💵 Nakit: `{paper_dash['cash']:.2f}$` | Rezerve: `{paper_dash['reserved']:.2f}$` | 💸 Komisyon: `-{paper_dash.get('total_commissions_usd', 0.0):.2f}$`\n"
            f"📊 Açık Pozisyon: `{len(paper_dash['positions'])}` | Bekleyen Plan: `{len(paper_dash['plans'])}`"
        )
    if setups:
        top3 = ", ".join(f"*{s['short']}* ({s['score']})" for s in setups[:3])
        lines.append(f"🏆 En Güçlü 3: {top3}")
    return "\n".join(lines)


def check_price_alerts(state, setups):
    """Kullanıcının /alarm komutuyla kurduğu fiyat alarmlarını kontrol eder."""
    alerts = state.get("alerts", [])
    if not isinstance(alerts, list) or not alerts:
        return
    price_map = {s["short"]: s["price"] for s in setups}
    remaining = []
    for al in alerts:
        sym = al.get("symbol", "")
        target = float(al.get("target", 0))
        direction = al.get("dir", "above")
        chat_id = al.get("chat_id") or CHAT_ID
        cur_p = price_map.get(sym)
        if cur_p is None:
            remaining.append(al)
            continue
        hit = (cur_p >= target) if direction == "above" else (cur_p <= target)
        if hit:
            arrow = "📈 ÜSTÜNE ÇIKTI" if direction == "above" else "📉 ALTINA İNDİ"
            send_telegram(
                f"🔔 *FİYAT ALARMI TETİKLENDİ — {sym}*\n"
                f"Hedef: `${target:.3f}` ({arrow}) | Anlık: `${cur_p:.3f}`\n"
                f"Grafik ve AI yorumu için: `/analiz {sym}`",
                chat_id=chat_id,
            )
        else:
            remaining.append(al)
    state["alerts"] = remaining


def process_telegram_commands(state, setups, market_open, market_status, regime, paper_trader_mod=None):
    """Telegram grubundan veya özelden gelen komutları (getUpdates) okur ve yanıtlar."""
    if not TG_TOKEN:
        return
    offset = int(state.get("tg_offset", 0) or 0)
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 0, "limit": 30},
            timeout=12,
        )
        if r.status_code != 200:
            print(f"getUpdates hata {r.status_code}: {r.text[:150]}")
            return
        updates = r.json().get("result", []) or []
    except Exception as e:
        print(f"getUpdates istisna: {e}")
        return

    if not updates:
        return

    watchlist = list(state.get("watchlist", []))
    alerts = list(state.get("alerts", []))
    max_uid = offset
    handled = 0

    for upd in updates:
        uid = int(upd.get("update_id", 0))
        if uid >= max_uid:
            max_uid = uid + 1

        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue

        msg_chat = msg.get("chat", {})
        msg_chat_id = str(msg_chat.get("id", ""))
        if not msg_chat_id:
            continue
        if CHAT_ID and msg_chat_id != str(CHAT_ID) and msg_chat.get("type") != "private":
            continue

        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip().upper().replace("$", "") if len(parts) > 1 else ""
        arg2 = parts[2].strip().replace(",", ".") if len(parts) > 2 else ""

        reply = None
        if cmd in ("/help", "/start", "/yardim"):
            reply = build_help_message()
        elif cmd == "/durum":
            dash = paper_trader_mod.get_dashboard() if paper_trader_mod else None
            reply = build_status_message(setups, market_status, regime, dash)
        elif cmd in ("/sanal", "/rapor", "/portfoy"):
            reply = paper_trader_mod.build_report(weekly=False) if paper_trader_mod else "Sanal portföy kapalı."
        elif cmd == "/haftalik":
            reply = paper_trader_mod.build_report(weekly=True) if paper_trader_mod else "Sanal portföy kapalı."
        elif cmd in ("/oneriler", "/top5", "/sniper"):
            reply = build_15min_bulletin(setups, market_open, market_status, regime) if setups else "Tarama verisi yok."
        elif cmd in ("/yukselenler", "/movers", "/firsatlar"):
            reply = build_movers_message(server.get_live_nasdaq_movers(limit=10))
        elif cmd in ("/gap", "/premarket", "/acilis"):
            reply = build_gap_message(server.get_premarket_gappers(limit=8), market_status)
        elif cmd in ("/ekle", "/watch"):
            if not arg or not arg.isalpha():
                reply = "Kullanım: `/ekle XLAB`"
            elif arg in watchlist:
                reply = f"ℹ️ *{arg}* zaten takip listende."
            else:
                watchlist.append(arg)
                watchlist = watchlist[-25:]
                reply = f"✅ *{arg}* takip listene eklendi! Her taramada özel olarak izlenecek."
        elif cmd in ("/sil", "/unwatch"):
            if arg in watchlist:
                watchlist.remove(arg)
                reply = f"🗑️ *{arg}* takip listenden çıkarıldı."
            else:
                reply = f"ℹ️ *{arg or '?'}* takip listende yok."
        elif cmd in ("/liste", "/watchlist"):
            price_map = {s["short"]: s for s in setups}
            lines = ["📋 *KİŞİSEL TAKİP LİSTESİ*"]
            if watchlist:
                for w in watchlist:
                    s = price_map.get(w)
                    if s:
                        lines.append(f"• *{w}* — `${s['price']}` ({s['change']:+.2f}%) | Skor `{s['score']}/100` ({s['decision']})")
                    else:
                        lines.append(f"• *{w}* — _(bir sonraki taramada güncellenecek)_")
            else:
                lines.append("_Liste boş. Eklemek için: `/ekle XLAB`_")
            if alerts:
                lines.append("\n🔔 *Aktif Fiyat Alarmları:*")
                for a in alerts:
                    arrow = "≥" if a.get("dir") == "above" else "≤"
                    lines.append(f"• *{a['symbol']}* {arrow} `${a['target']}`")
            reply = "\n".join(lines)
        elif cmd == "/alarm":
            try:
                target = float(arg2)
                if not arg or not arg.isalpha() or target <= 0:
                    raise ValueError
                cur_s = next((s for s in setups if s["short"] == arg), None)
                if not cur_s:
                    cur_s = server.analyze_single_symbol(arg, enrich=False)
                cur_p = cur_s["price"] if cur_s else 0
                direction = "above" if (cur_p == 0 or target >= cur_p) else "below"
                alerts.append({"symbol": arg, "target": target, "dir": direction, "chat_id": msg_chat_id})
                alerts = alerts[-30:]
                if arg not in watchlist:
                    watchlist.append(arg)
                dir_lbl = "üstüne çıkınca" if direction == "above" else "altına inince"
                reply = f"🔔 Alarm kuruldu: *{arg}* `${target:.3f}` {dir_lbl} bildirim gelecek (anlık: `${cur_p}`)."
            except Exception:
                reply = "Kullanım: `/alarm XLAB 2.00`"
        elif cmd in ("/analiz", "/grafik", "/fiyat", "/hisse"):
            if not arg:
                reply = "Kullanım: `/analiz XLAB` veya `/grafik NVDA`"
            else:
                found = next((s for s in setups if s["short"] == arg or s["symbol"] == arg), None)
                if not found:
                    found = server.analyze_single_symbol(arg, enrich=True)
                if found:
                    send_chart_with_ai(found, regime_label=regime["label"], chat_id=msg_chat_id)
                    handled += 1
                    print(f"Grafik+AI yanıtlandı: {text} -> chat {msg_chat_id}")
                    continue
                else:
                    reply = f"❌ `{arg}` için NASDAQ verisi bulunamadı. Örnek: `/analiz NVDA`, `/analiz XLAB`"

        if reply:
            send_telegram(reply, chat_id=msg_chat_id)
            handled += 1
            print(f"Komut yanıtlandı: {text} -> chat {msg_chat_id}")

    state["tg_offset"] = max_uid
    state["watchlist"] = watchlist
    state["alerts"] = alerts
    if handled:
        print(f"Toplam {handled} Telegram komutu yanıtlandı.")


def main():
    server.SNIPER_MIN_SCORE = MIN_SCORE
    server._notify_sniper_telegram = lambda s: None

    state = load_state()
    now = time.time()

    market_open, market_status = server.is_us_market_open()
    regime = server.get_market_regime()
    print(f"Borsa: {market_status} | Rejim: {regime['label']} (VIX: {regime.get('vix')})")

    # Takip listesi + alarmlardaki hisseleri mutlaka taramaya dahil et
    extra_syms = list(state.get("watchlist", []))
    for al in state.get("alerts", []):
        if isinstance(al, dict) and al.get("symbol"):
            extra_syms.append(al["symbol"])

    print(f"Tarama başlıyor (eşik: {MIN_SCORE}/100, dry_run={DRY_RUN})...")
    setups = server._run_full_market_scan(extra_symbols=extra_syms)
    print(f"Taranan varlık: {len(setups)} (Evren: {server.LIVE_MOVERS_CACHE.get('total_universe', 0)})")
    if setups:
        top = ", ".join(f"{s['short']}:{s['score']}" for s in setups[:6])
        print(f"En yüksek puanlar -> {top}")

    # 1) Fiyat alarmlarını kontrol et
    check_price_alerts(state, setups)

    # 2) Sanal portföy (piyasa saati + rejim ölçeklemesi + gerçekçi komisyonlar)
    paper_trader_mod = None
    paper_summary = None
    if os.getenv("PAPER_TRADING", "1") == "1" and setups:
        import paper_trader as _pt
        paper_trader_mod = _pt
        res = _pt.process_scan(
            setups,
            notify=send_telegram,
            market_open=market_open,
            regime_scale=regime.get("scale", 1.0),
        )
        if res:
            dash = _pt.get_dashboard()
            paper_summary = {
                "equity": dash["equity"],
                "return_pct": dash["return_pct"],
                "cash": dash["cash"],
                "open_count": len(dash["positions"]),
                "commissions": dash.get("total_commissions_usd", 0.0),
            }
            print(f"Sanal portföy net varlık: {res['equity']:.2f}$ | komisyon: -{paper_summary['commissions']:.2f}$ | olay: {res['events']}")

    # 3) Sniper fırsat bildirimleri (>= 90/100) — Grafik + AI Analizi ile!
    alerts = [s for s in setups if s["score"] >= MIN_SCORE and s.get("tradeable", True)]
    sent = 0
    for s in alerts[:3]:
        window = DEDUP_SECONDS.get(s["category"], DEFAULT_DEDUP)
        if now - float(state.get(s["symbol"], 0) or 0) < window:
            continue
        if send_chart_with_ai(s, regime_label=regime["label"]):
            state[s["symbol"]] = now
            sent += 1
            print(f"Grafik+AI Sniper bildirimi gönderildi: {s['short']} ({s['score']})")

    # 4) Akıllı Bülten: Borsa AÇIKKEN 15 dk'da bir, KAPALIYKEN sessiz modda 2 saatte bir
    open_interval = int(os.getenv("TOP_PICKS_INTERVAL_SEC", "840"))
    closed_interval = int(os.getenv("QUIET_INTERVAL_SEC", str(2 * 3600)))
    bulletin_interval = open_interval if market_open else closed_interval
    if setups and (now - float(state.get("top_picks_ts", 0) or 0) >= bulletin_interval):
        if send_telegram(build_15min_bulletin(setups, market_open, market_status, regime, paper_summary=paper_summary)):
            state["top_picks_ts"] = now
            print("Canlı NASDAQ bülteni gönderildi.")
            # En iyi 1. aday için görsel grafik + AI analizi (2 saatte bir aynı hisse için)
            best = next((s for s in setups if s.get("tradeable", True)), setups[0])
            chart_key = f"chart_{best['symbol']}"
            if now - float(state.get(chart_key, 0) or 0) >= 2 * 3600:
                if send_chart_with_ai(best, regime_label=regime["label"]):
                    state[chart_key] = now

    # 5) Yeni NASDAQ / Wall Street haberleri (tekrarsız)
    news_items = server.fetch_nasdaq_news_headlines(limit=6)
    fresh_news = []
    for n in news_items:
        nid = "news_" + hashlib.md5(n["title"].encode("utf-8", errors="ignore")).hexdigest()[:12]
        if nid not in state:
            fresh_news.append((nid, n))
    if fresh_news:
        lines = ["📰 *NASDAQ & WALL STREET HABER AKIŞI*\n"]
        for _, n in fresh_news[:5]:
            link_part = f" ([Oku]({n['url']}))" if n.get("url") else ""
            lines.append(f"• [{n['source']}] {n['sentiment_label']} — {n['title']}{link_part}")
        if send_telegram("\n".join(lines)):
            for nid, _ in fresh_news[:5]:
                state[nid] = now
            print(f"Haber paketi gönderildi: {min(len(fresh_news), 5)} haber")

    # 6) Telegram komutlarını yanıtla (/help, /durum, /analiz, /ekle, /alarm vb.)
    process_telegram_commands(state, setups, market_open, market_status, regime, paper_trader_mod=paper_trader_mod)

    # Eski kayıtları temizle (3 günden eski), kalıcı anahtarları KORU!
    keep_keys = {"tg_offset", "top_picks_ts", "watchlist", "alerts"}
    cleaned = {}
    for k, v in state.items():
        if k in keep_keys:
            cleaned[k] = v
        elif isinstance(v, (int, float)) and (now - v < 3 * 86400):
            cleaned[k] = v
    save_state(cleaned)
    print(f"Bitti. Fırsat: {len(alerts)} | Gönderilen: {sent}")


if __name__ == "__main__":
    main()
