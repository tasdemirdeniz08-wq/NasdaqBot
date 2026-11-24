"""
Bulut Sniper İşçisi (GitHub Actions için)
- Bilgisayar kapalıyken bile bulutta çalışır.
- 55+ varlığı (BIST 30, NASDAQ, Kripto, Altın) tarar.
- Güven puanı >= SNIPER_MIN_SCORE (varsayılan 90) olan fırsatları Telegram'a gönderir.
- Aynı fırsat için tekrar mesaj atmaz (state dosyası ile).

Ortam değişkenleri:
  TELEGRAM_TOKEN, CHAT_ID   -> zorunlu (GitHub Secrets)
  SNIPER_MIN_SCORE          -> opsiyonel (varsayılan 90)
  DRY_RUN=1                 -> mesaj göndermez, ekrana yazar (test için)
  STATE_FILE                -> opsiyonel (varsayılan sniper_state.json)
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import requests
import server  # tarama motoru burada

STATE_FILE = os.getenv("STATE_FILE", "sniper_state.json")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
MIN_SCORE = int(os.getenv("SNIPER_MIN_SCORE", "90"))

# Kripto 7/24 hareket eder (6 saat), hisse/altın günlük mumla çalışır (24 saat)
DEDUP_SECONDS = {"KRİPTO": 6 * 3600}
DEFAULT_DEDUP = 24 * 3600


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def build_message(s):
    c = s["currency"]
    return (
        f"🎯 *VAKTİ GELDİ — SNIPER FIRSAT ({s['score']}/100)*\n"
        f"📌 *{s['short']}* ({s['label']} · {s['category']})\n"
        f"💰 Anlık: `{c}{s['price']}` ({s['change']:+.2f}%)\n\n"
        f"⚡ Kırılım (R1): `{c}{s['breakout']}`\n"
        f"🛡️ Destekler: `{c}{s['support_1']}` / `{c}{s['support_2']}`\n\n"
        f"📥 *Kademeli Giriş:*\n"
        f"• 1. Kademe (%40): `{c}{s['entry_1']}`\n"
        f"• 2. Kademe (%40): `{c}{s['entry_2']}`\n"
        f"• Kırılım Onay (%20): `{c}{s['entry_breakout']}`\n\n"
        f"🛑 *Stop-Loss:* `{c}{s['stop_loss']}` (-%{s['risk_pct']})\n"
        f"🎯 *Hedefler:* TP1 `{c}{s['tp1']}` | TP2 `{c}{s['tp2']}` | TP3 `{c}{s['tp3']}`\n"
        f"🧠 {s['summary']}\n\n"
        f"_Teknik seviyelerdir, yatırım tavsiyesi değildir._"
    )


def send_telegram(text):
    if DRY_RUN:
        print("---- [DRY RUN] Telegram mesajı ----")
        print(text)
        print("-----------------------------------")
        return True
    if not TG_TOKEN or not CHAT_ID:
        print("HATA: TELEGRAM_TOKEN / CHAT_ID tanımlı değil.")
        return False
    for attempt in range(3):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown",
                      "disable_web_page_preview": True},
                timeout=15,
            )
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                wait = r.json().get("parameters", {}).get("retry_after", 10)
                print(f"Telegram 429, {wait}s bekleniyor...")
                time.sleep(min(int(wait) + 1, 40))
                continue
            print(f"Telegram hata {r.status_code}: {r.text[:200]}")
            return False
        except Exception as e:
            print(f"Telegram istisna: {e}")
            time.sleep(3)
    return False


def main():
    server.SNIPER_MIN_SCORE = MIN_SCORE
    # Sunucudaki yerleşik bildirimi devre dışı bırak; bildirimi burada yönetiyoruz
    server._notify_sniper_telegram = lambda s: None

    print(f"Tarama başlıyor (eşik: {MIN_SCORE}/100, dry_run={DRY_RUN})...")
    setups = server._run_full_market_scan()
    print(f"Taranan varlık: {len(setups)}")
    if setups:
        top = ", ".join(f"{s['short']}:{s['score']}" for s in setups[:6])
        print(f"En yüksek puanlar -> {top}")

    alerts = [s for s in setups if s["score"] >= MIN_SCORE]
    state = load_state()
    now = time.time()
    sent = 0

    for s in alerts:
        window = DEDUP_SECONDS.get(s["category"], DEFAULT_DEDUP)
        if now - state.get(s["symbol"], 0) < window:
            print(f"{s['short']} zaten bildirildi, atlanıyor.")
            continue
        if send_telegram(build_message(s)):
            state[s["symbol"]] = now
            sent += 1
            print(f"Bildirim gönderildi: {s['short']} ({s['score']})")
        else:
            print(f"Bildirim gönderilemedi: {s['short']} (bir sonraki turda tekrar denenecek)")

    # Yeni NASDAQ / Wall Street haberlerini Telegram'da paylaş (tekrarsız)
    import hashlib
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

    # Eski kayıtları temizle (3 günden eski)
    state = {k: v for k, v in state.items() if now - v < 3 * 86400}
    save_state(state)
    print(f"Bitti. Fırsat: {len(alerts)} | Gönderilen: {sent}")

    # Sanal portföy (paper trading) - PAPER_TRADING=0 ile kapatılır
    if os.getenv("PAPER_TRADING", "1") == "1" and setups:
        import paper_trader
        res = paper_trader.process_scan(setups, notify=send_telegram)
        if res:
            print(f"Sanal portföy varlık: {res['equity']:.2f}$ | olay: {res['events']}")


if __name__ == "__main__":
    main()
