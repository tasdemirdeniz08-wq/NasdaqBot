"""
Görsel Mum Grafik (Candlestick) + Groq AI Analiz Modülü
- Matplotlib (Agg backend) ile koyu tema teknik analiz grafiği (Mumlar, EMA20/50, Hacim, Kırılım, Destek, Stop, TP1/TP2) üretir.
- Groq API (GROQ_API_KEY) varsa hisse haberlerini ve teknik verileri değerlendirerek kısa Türkçe AI yorumu üretir.
"""
import os
import tempfile
import requests


def generate_ai_commentary(setup, regime_label="NÖTR"):
    """Groq API ile kısa, gerçekçi Türkçe günlük al-sat (day-trade) AI değerlendirmesi üretir."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or not setup:
        return _fallback_commentary(setup, regime_label)

    opt_txt = setup.get("options", {}).get("summary", "Opsiyon verisi yok")
    news_txt = setup.get("latest_news", "Önemli son dakika haberi yok")
    vwap_txt = f"${setup['vwap']} ({'Üstünde' if setup['price'] >= setup['vwap'] else 'Altında'})" if setup.get("vwap") else "Bilinmiyor"
    earn_txt = "⚠️ YAKIN BİLANÇO RİSKİ VAR" if setup.get("earnings_warning") else "Yakın bilanço yok"

    prompt = (
        f"Sen deneyimli bir NASDAQ günlük al-sat (day-trade / swing) analistisin. "
        f"Aşağıdaki verileri değerlendirip Türkçe, net, abartısız ve 3 kısa madde halinde yorum yap "
        f"(toplam 55 kelimeyi geçme, kesin kazanç vaadi verme):\n"
        f"- Hisse: {setup['short']} ({setup.get('sector', 'NASDAQ')}) | Fiyat: ${setup['price']} ({setup['change']:+.2f}%)\n"
        f"- Skor: {setup['score']}/100 | Karar: {setup['decision']} | Piyasa Rejimi: {regime_label}\n"
        f"- RSI: {setup['rsi']} | Hacim: {setup['vol_ratio']}x | Günlük Volatilite (ATR%): %{setup.get('atr_pct', '-')}\n"
        f"- VWAP: {vwap_txt} | Kırılım: ${setup['breakout']} | Destek: ${setup['support_1']}\n"
        f"- Stop: ${setup['stop_loss']} (-%{setup['risk_pct']}) | TP1: ${setup['tp1']} | TP2: ${setup['tp2']}\n"
        f"- {opt_txt} | {earn_txt}\n"
        f"- Son Haber: {news_txt}\n"
        f"Format:\n• Görünüm: ...\n• Haber/Akış: ...\n• Risk Notu: ..."
    )

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 220,
            },
            timeout=15,
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content:
                return content
    except Exception as e:
        print(f"Groq AI error: {e}")

    return _fallback_commentary(setup, regime_label)


def classify_news_with_ai(symbol, headlines):
    """Groq API ile haber başlıklarının hisse için pozitif/negatif/nötr etkisini sınıflandırır."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or not headlines:
        return None
    joined = "\n".join(f"- {h[:120]}" for h in headlines[:4])
    prompt = (
        f"{symbol} hissesi için şu haber başlıklarının toplam hisse fiyatına etkisi nedir? "
        f"Sadece tek kelime cevap ver: POZITIF, NEGATIF veya NOTR.\n{joined}"
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 10,
            },
            timeout=10,
        )
        if r.status_code == 200:
            ans = r.json()["choices"][0]["message"]["content"].strip().upper()
            if "POZITIF" in ans or "POZİTİF" in ans:
                return 1, "POZİTİF"
            if "NEGATIF" in ans or "NEGATİF" in ans:
                return -1, "NEGATİF"
            return 0, "NÖTR"
    except Exception:
        pass
    return None


def _fallback_commentary(setup, regime_label):
    if not setup:
        return ""
    vwap_note = ""
    if setup.get("vwap"):
        vwap_note = "VWAP üstünde alıcılı" if setup["price"] >= setup["vwap"] else "VWAP altında baskılı"
    earn_note = "⚠️ Yakın bilanço riski mevcut." if setup.get("earnings_warning") else "Yakın bilanço riski görünmüyor."
    return (
        f"• Görünüm: {setup['decision']} ({setup['score']}/100), RSI {setup['rsi']}, hacim {setup['vol_ratio']}x"
        + (f", {vwap_note}." if vwap_note else ".")
        + f"\n• Piyasa: {regime_label} | Stop mesafesi -%{setup['risk_pct']}."
        + f"\n• Risk Notu: {earn_note}"
    )


def render_setup_chart(df, setup, out_path=None):
    """
    Hisse için koyu temalı mum grafiği + EMA20/50 + Hacim + Kırılım/Stop/TP çizgilerini PNG olarak çizer.
    Dönüş: PNG dosya yolu veya hata durumunda None.
    """
    if df is None or len(df) < 15 or not setup:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches
        import numpy as np

        plot_df = df.tail(55).copy().reset_index(drop=True)
        n = len(plot_df)
        xs = np.arange(n)

        o = plot_df["open"].astype(float).values
        h = plot_df["high"].astype(float).values
        l = plot_df["low"].astype(float).values
        c = plot_df["close"].astype(float).values
        v = plot_df["volume"].astype(float).values if "volume" in plot_df else np.zeros(n)

        ema20 = df["close"].astype(float).ewm(span=20, adjust=False).mean().tail(n).values
        ema50 = df["close"].astype(float).ewm(span=50, adjust=False).mean().tail(n).values

        bg = "#0d1117"
        panel_bg = "#161b22"
        grid_c = "#262c36"
        txt_c = "#e6edf3"
        green = "#26a69a"
        red = "#ef5350"

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(10.5, 6.2), gridspec_kw={"height_ratios": [3.5, 1.0]}, sharex=True
        )
        fig.patch.set_facecolor(bg)
        ax1.set_facecolor(panel_bg)
        ax2.set_facecolor(panel_bg)

        # Mumlar
        width = 0.62
        for i in range(n):
            col = green if c[i] >= o[i] else red
            ax1.plot([xs[i], xs[i]], [l[i], h[i]], color=col, linewidth=1.1, zorder=2)
            body_low = min(o[i], c[i])
            body_h = max(abs(c[i] - o[i]), (h[i] - l[i]) * 0.04, 1e-5)
            rect = patches.Rectangle((xs[i] - width / 2, body_low), width, body_h, facecolor=col, edgecolor=col, zorder=3)
            ax1.add_patch(rect)

        # EMA çizgileri
        ax1.plot(xs, ema20, color="#f59e0b", linewidth=1.4, label="EMA 20", zorder=4)
        ax1.plot(xs, ema50, color="#3b82f6", linewidth=1.4, label="EMA 50", zorder=4)

        # Seviye çizgileri
        levels = [
            (setup.get("tp2"), "#10b981", ":", f"TP2 ${setup.get('tp2')}"),
            (setup.get("tp1"), "#34d399", "--", f"TP1 ${setup.get('tp1')}"),
            (setup.get("breakout"), "#a855f7", "--", f"Kırılım ${setup.get('breakout')}"),
            (setup.get("entry_1"), "#38bdf8", "-.", f"Destek ${setup.get('entry_1')}"),
            (setup.get("stop_loss"), "#f43f5e", "--", f"Stop ${setup.get('stop_loss')}"),
        ]
        for val, col, ls, lbl in levels:
            if val and val > 0:
                ax1.axhline(val, color=col, linestyle=ls, linewidth=1.1, alpha=0.9, zorder=5)
                ax1.text(n - 0.3, val, f" {lbl}", color=col, fontsize=8.5, va="center", ha="left", fontweight="bold")

        # Anlık fiyat etiketi
        cur_p = setup["price"]
        ax1.axhline(cur_p, color="#ffffff", linestyle="-", linewidth=0.8, alpha=0.55, zorder=4)

        # Hacim
        vol_cols = [green if c[i] >= o[i] else red for i in range(n)]
        ax2.bar(xs, v, color=vol_cols, width=0.65, alpha=0.75)

        # Başlık & stil
        chg = setup.get("change", 0)
        chg_sign = "+" if chg >= 0 else ""
        title = (
            f"{setup['short']} (NASDAQ)  |  ${cur_p} ({chg_sign}{chg:.2f}%)  |  "
            f"Skor: {setup['score']}/100 ({setup['decision']})"
        )
        sub_info = (
            f"RSI: {setup.get('rsi', '-')}  •  Hacim: {setup.get('vol_ratio', '-')}x  •  "
            f"Risk: -%{setup.get('risk_pct', '-')}  •  ATR: %{setup.get('atr_pct', '-')}"
        )
        if setup.get("vwap"):
            sub_info += f"  •  VWAP: ${setup['vwap']}"

        ax1.set_title(title, color=txt_c, fontsize=12.5, fontweight="bold", loc="left", pad=18)
        ax1.text(0.0, 1.015, sub_info, transform=ax1.transAxes, color="#9ca3af", fontsize=9)

        ax1.set_xlim(-1, n + 9)
        ax1.grid(color=grid_c, linestyle="--", linewidth=0.5, alpha=0.6)
        ax2.grid(color=grid_c, linestyle="--", linewidth=0.5, alpha=0.6)
        ax1.tick_params(colors=txt_c, labelsize=8.5)
        ax2.tick_params(colors=txt_c, labelsize=8)
        ax2.set_ylabel("Hacim", color="#9ca3af", fontsize=8.5)
        ax1.set_ylabel("Fiyat ($)", color="#9ca3af", fontsize=8.5)
        ax1.legend(loc="upper left", facecolor=panel_bg, edgecolor=grid_c, labelcolor=txt_c, fontsize=8.5)

        for ax in (ax1, ax2):
            for sp in ax.spines.values():
                sp.set_color(grid_c)

        plt.tight_layout()
        if not out_path:
            fd, out_path = tempfile.mkstemp(prefix=f"chart_{setup['short']}_", suffix=".png")
            os.close(fd)
        fig.savefig(out_path, dpi=135, facecolor=bg)
        plt.close(fig)
        return out_path
    except Exception as e:
        print(f"Chart render error: {e}")
        return None
