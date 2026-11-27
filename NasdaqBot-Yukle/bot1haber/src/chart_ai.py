"""
Profesyonel FinTech Mum Grafik (Candlestick + Risk/Ödül Kutuları + Hacim + RSI) + Groq AI Analiz Modülü
- Matplotlib (Agg backend) ile 3 panelli (Fiyat+EMA20/50+VWAP+TP/Stop Bölgeleri, Hacim, RSI14) yüksek kaliteli koyu tema grafik üretir.
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
        f"- Skor: {setup['score']}/100 | Karar: {setup['decision']} | Risk: {setup.get('risk_label','-')} | Piyasa: {regime_label}\n"
        f"- RSI: {setup['rsi']} | Hacim: {setup['vol_ratio']}x | ATR%: %{setup.get('atr_pct', '-')} | RS(NDX): {setup.get('rs_vs_ndx', 0):+.1f}%\n"
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
        + f"\n• Piyasa: {regime_label} | {setup.get('risk_label', '')} (Stop -%{setup['risk_pct']})."
        + f"\n• Risk Notu: {earn_note}"
    )


def render_setup_chart(df, setup, out_path=None):
    """
    Profesyonel 3 Panelli FinTech Grafik (OPT Pro Signal tarzı):
    1) Mum Grafiği + EMA20/50 + VWAP + Renkli Kâr/Zarar Bölgeleri (Yeşil TP / Kırmızı Stop) + Hap Fiyat Etiketleri
    2) Hacim + 20 Günlük Ortalama Hacim Çizgisi
    3) RSI (14) + 30/70 Bantları
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

        full_c = df["close"].astype(float)
        ema20 = full_c.ewm(span=20, adjust=False).mean().tail(n).values
        ema50 = full_c.ewm(span=50, adjust=False).mean().tail(n).values
        vol_ma20 = df["volume"].astype(float).rolling(20).mean().tail(n).values if "volume" in df else np.zeros(n)

        delta = full_c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_vals = (100 - (100 / (1 + rs))).fillna(50).tail(n).values

        # Profesyonel Koyu Obsidian Tema
        bg = "#080c14"
        panel_bg = "#0f1623"
        grid_c = "#1e293b"
        txt_c = "#f1f5f9"
        muted_c = "#94a3b8"
        green = "#10b981"
        red = "#f43f5e"

        fig, (ax1, ax2, ax3) = plt.subplots(
            3, 1, figsize=(11.2, 7.6),
            gridspec_kw={"height_ratios": [4.0, 1.0, 1.1], "hspace": 0.08},
            sharex=True,
        )
        fig.patch.set_facecolor(bg)
        for ax in (ax1, ax2, ax3):
            ax.set_facecolor(panel_bg)
            ax.grid(color=grid_c, linestyle="--", linewidth=0.55, alpha=0.65)
            ax.tick_params(colors=muted_c, labelsize=8.5)
            for sp in ax.spines.values():
                sp.set_color(grid_c)
                sp.set_linewidth(0.8)

        # Renkli Risk/Ödül Kutuları (OPT Pro Signal tarzı: sağ 12 mum + projeksiyon alanı)
        cur_p = float(setup["price"])
        stop_p = float(setup.get("stop_loss") or 0)
        tp1_p = float(setup.get("tp1") or 0)
        tp2_p = float(setup.get("tp2") or 0)
        brk_p = float(setup.get("breakout") or 0)
        vwap_p = float(setup.get("vwap") or 0)

        box_x0 = max(0, n - 10)
        box_x1 = n + 3
        if tp1_p > cur_p:
            ax1.add_patch(patches.Rectangle((box_x0, cur_p), box_x1 - box_x0, tp1_p - cur_p,
                                            facecolor="#10b981", alpha=0.11, zorder=1))
        if tp2_p > tp1_p > cur_p:
            ax1.add_patch(patches.Rectangle((box_x0, tp1_p), box_x1 - box_x0, tp2_p - tp1_p,
                                            facecolor="#34d399", alpha=0.06, zorder=1))
        if 0 < stop_p < cur_p:
            ax1.add_patch(patches.Rectangle((box_x0, stop_p), box_x1 - box_x0, cur_p - stop_p,
                                            facecolor="#f43f5e", alpha=0.13, zorder=1))

        # Mumlar
        width = 0.62
        for i in range(n):
            col = green if c[i] >= o[i] else red
            ax1.plot([xs[i], xs[i]], [l[i], h[i]], color=col, linewidth=1.15, zorder=3)
            body_low = min(o[i], c[i])
            body_h = max(abs(c[i] - o[i]), (h[i] - l[i]) * 0.04, 1e-5)
            ax1.add_patch(patches.Rectangle((xs[i] - width / 2, body_low), width, body_h,
                                            facecolor=col, edgecolor=col, zorder=4))

        # EMA20 / EMA50 + VWAP
        ax1.plot(xs, ema20, color="#fbbf24", linewidth=1.5, label="EMA 20", zorder=5)
        ax1.plot(xs, ema50, color="#38bdf8", linewidth=1.5, label="EMA 50", zorder=5)
        if vwap_p > 0:
            ax1.axhline(vwap_p, color="#c084fc", linestyle=":", linewidth=1.2, alpha=0.85, label=f"VWAP ${vwap_p}", zorder=4)

        # Yatay Seviye Çizgileri + Sağda Hap (Pill) Etiketler
        def _pill(ax, y_val, text, bg_c, fg_c="#080c14", ls="--"):
            ax.axhline(y_val, color=bg_c, linestyle=ls, linewidth=1.05, alpha=0.85, zorder=5)
            ax.text(
                n + 0.6, y_val, f" {text} ",
                color=fg_c, fontsize=8.0, fontweight="bold", va="center", ha="left",
                bbox=dict(boxstyle="round,pad=0.22", facecolor=bg_c, edgecolor="none", alpha=0.95),
                zorder=8,
            )

        if tp2_p > 0:
            _pill(ax1, tp2_p, f"TP2 ${tp2_p}", "#059669", "#ffffff", ":")
        if tp1_p > 0:
            _pill(ax1, tp1_p, f"TP1 ${tp1_p}", "#10b981", "#042f2e", "--")
        if brk_p > 0 and abs(brk_p - tp1_p) / max(cur_p, 1) > 0.015:
            _pill(ax1, brk_p, f"R1 ${brk_p}", "#a855f7", "#ffffff", "--")
        _pill(ax1, cur_p, f"ANLIK ${cur_p}", "#f8fafc", "#0f172a", "-")
        if stop_p > 0:
            _pill(ax1, stop_p, f"STOP ${stop_p}", "#f43f5e", "#ffffff", "--")

        # Üst Başlık & Sağ Üst Skor/Risk Kartı
        chg = float(setup.get("change", 0))
        chg_c = green if chg >= 0 else red
        chg_s = f"{chg:+.2f}%"
        sc = int(setup.get("score", 0))
        sc_bg = "#065f46" if sc >= 75 else ("#92400e" if sc >= 55 else "#881337")

        fig.text(0.06, 0.955, f"{setup['short']}", color=txt_c, fontsize=16, fontweight="bold")
        fig.text(0.155, 0.957, f"NASDAQ · {setup.get('sector', 'Hisse')}", color=muted_c, fontsize=10)
        fig.text(0.06, 0.923, f"${cur_p}  ({chg_s})   |   ATR: %{setup.get('atr_pct', '-')}   |   Hacim: {setup.get('vol_ratio', '-')}x   |   RS(NDX): {setup.get('rs_vs_ndx', 0):+.1f}%",
                 color=chg_c, fontsize=9.5, fontweight="bold")

        hz = setup.get("horizons") or {}
        hz_txt = ""
        if hz:
            hz_txt = f"\nG:{hz.get('gunluk',{}).get('score','-')} · 2-3G:{hz.get('swing_2_3',{}).get('score','-')} · H:{hz.get('haftalik',{}).get('score','-')}"
        risk_clean = str(setup.get("risk_label", "")).replace("🟢 ", "").replace("🟡 ", "").replace("🔴 ", "")
        badge_txt = f"SKOR {sc}/100  |  {risk_clean}{hz_txt}"
        fig.text(
            0.94, 0.942, badge_txt, color="#ffffff", fontsize=8.8, fontweight="bold", ha="right", va="center",
            bbox=dict(boxstyle="round,pad=0.45", facecolor=sc_bg, edgecolor="#334155", linewidth=1.0),
        )

        ax1.set_xlim(-1, n + 10.5)
        ax1.set_ylabel("Fiyat ($)", color=muted_c, fontsize=8.5)
        ax1.legend(loc="upper left", facecolor=panel_bg, edgecolor=grid_c, labelcolor=txt_c, fontsize=8.2, framealpha=0.9)

        # Panel 2: Hacim + 20G Ortalama
        vol_cols = [green if c[i] >= o[i] else red for i in range(n)]
        ax2.bar(xs, v, color=vol_cols, width=0.65, alpha=0.72, zorder=2)
        if np.any(vol_ma20 > 0):
            ax2.plot(xs, vol_ma20, color="#fbbf24", linewidth=1.1, alpha=0.9, zorder=3)
        ax2.set_ylabel("Hacim", color=muted_c, fontsize=8.2)

        # Panel 3: RSI (14)
        ax3.plot(xs, rsi_vals, color="#38bdf8", linewidth=1.4, zorder=3)
        ax3.axhline(70, color="#f43f5e", linestyle=":", linewidth=0.9, alpha=0.75)
        ax3.axhline(30, color="#10b981", linestyle=":", linewidth=0.9, alpha=0.75)
        ax3.fill_between(xs, rsi_vals, 70, where=(rsi_vals >= 70), color="#f43f5e", alpha=0.18)
        ax3.fill_between(xs, rsi_vals, 30, where=(rsi_vals <= 30), color="#10b981", alpha=0.18)
        ax3.set_ylim(10, 90)
        ax3.set_ylabel(f"RSI ({rsi_vals[-1]:.0f})", color=muted_c, fontsize=8.2)

        # Alt filigran
        fig.text(0.06, 0.015, "NasdaqBot Pro Terminal · Teknik Görünüm (Yatırım tavsiyesi değildir)", color="#475569", fontsize=8)

        plt.subplots_adjust(left=0.065, right=0.945, top=0.895, bottom=0.065)
        if not out_path:
            fd, out_path = tempfile.mkstemp(prefix=f"chart_{setup['short']}_", suffix=".png")
            os.close(fd)
        fig.savefig(out_path, dpi=150, facecolor=bg)
        plt.close(fig)
        return out_path
    except Exception as e:
        print(f"Chart render error: {e}")
        return None
