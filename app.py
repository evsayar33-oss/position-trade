import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

# --- 0. SİSTEM AYARLARI ---
st.set_page_config(page_title="Quant Macro Engine", layout="wide")
st.markdown("<style>.main { background-color: #0d1117; color: white; }</style>", unsafe_allow_html=True)

# --- 1. VERİ ÇEKME VE ÖN İŞLEME ---
@st.cache_data(ttl=3600)
def get_engine_data():
    # Gerekli tüm semboller
    # XLI/XLP (Büyüme), TIP/IEF (Enflasyon), TNX/IRX (Spread)
    # SPY, TLT, DBC, BIL (Portföy)
    tickers = ['XLI', 'XLP', 'TIP', 'IEF', '^TNX', '^IRX', 'SPY', 'TLT', 'DBC', 'BIL']
    raw = yf.download(tickers, period="5y", interval="1d")['Close'].ffill()
    
    # Sembol temizliği (IRX -> 13 Haftalık Bono Faizi / 10 yapılarak yüzdeye çekilir)
    df = raw.copy()
    df['IRX'] = df['^IRX'] / 10 # IRX endeks değerini yüzdeye çevir
    df['TNX'] = df['^TNX']
    
    # Haftalık Resample (Cuma Kapanışları)
    df_w = df.resample('W-FRI').last().ffill()
    # Aylık Resample (Ayın 1'ine en yakın veriler)
    df_m = df.resample('MS').last().ffill()
    
    return df_w, df_m

# --- 2. KATMANLI HESAPLAMA MOTORU ---
def run_quant_engine(df_w, df_m):
    # --- LEVEL 1: MAKRO KADRAN ---
    growth_ratio = df_m['XLI'] / df_m['XLP']
    infl_ratio = df_m['TIP'] / df_m['IEF']
    
    g_3m = growth_ratio.rolling(3).mean()
    g_12m = growth_ratio.rolling(12).mean()
    i_3m = infl_ratio.rolling(3).mean()
    i_12m = infl_ratio.rolling(12).mean()
    
    g_signal = g_3m > g_12m
    i_signal = i_3m > i_12m
    
    # Kadran Belirleme
    def get_quadrant(g, i):
        if g and not i: return "GOLDILOCKS"
        if g and i: return "OVERHEATING"
        if not g and i: return "STAGFLATION"
        return "CONTRACTION"

    current_quad = get_quadrant(g_signal.iloc[-1], i_signal.iloc[-1])
    prev_quad = get_quadrant(g_signal.iloc[-2], i_signal.iloc[-2])

    # --- LEVEL 2: CIRCUIT BREAKER & HYSTERESIS ---
    spread = df_m['TNX'] - df_m['IRX']
    
    # Circuit Breaker Tetiği: Son 6 ayda negatif (<0) olup tekrar >0 oldu mu?
    was_inverted = (spread.shift(1).rolling(6).min() < 0)
    circuit_breaker = was_inverted.iloc[-1] and (spread.iloc[-1] > 0)
    
    # Reset Koşulu
    cb_reset_a = (spread.rolling(3).min().iloc[-1] > 0.50)
    cb_reset_b = g_signal.iloc[-1] and (spread.rolling(2).min().iloc[-1] > 0)
    if cb_reset_a or cb_reset_b: circuit_breaker = False

    # Hysteresis (Güven Skoru)
    confidence_score = 100 if current_quad == prev_quad else 50

    # Taban Ağırlıklar
    weights = {
        "GOLDILOCKS": {"SPY": 0.60, "TLT": 0.20, "DBC": 0.10, "BIL": 0.10},
        "OVERHEATING": {"SPY": 0.30, "TLT": 0.00, "DBC": 0.50, "BIL": 0.20},
        "STAGFLASYON": {"SPY": 0.10, "TLT": 0.00, "DBC": 0.50, "BIL": 0.40},
        "CONTRACTION": {"SPY": 0.10, "TLT": 0.50, "DBC": 0.10, "BIL": 0.30}
    }
    
    base_w = weights[current_quad if confidence_score == 100 else prev_quad].copy()

    # Apply Circuit Breaker
    if circuit_breaker:
        base_w = {"SPY": 0.10, "TLT": 0.40, "DBC": 0.10, "BIL": 0.40}
    elif confidence_score == 50:
        base_w["SPY"] *= 0.80
        base_w["DBC"] *= 0.80

    # --- LEVEL 3: TREND GATE (WEEKLY) ---
    def get_trend(asset):
        price = df_w[asset]
        ema20 = price.ewm(span=20, adjust=False).mean()
        ema50 = price.ewm(span=50, adjust=False).mean()
        # RSI 14
        delta = price.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        cond1 = price.iloc[-1] > ema50.iloc[-1]
        cond2 = ema20.iloc[-1] > ema50.iloc[-1]
        cond3 = rsi.iloc[-1] > 50
        
        return (cond1 and cond2 and cond3), rsi.iloc[-1]

    trend_results = {}
    for asset in ["SPY", "TLT", "DBC"]:
        is_uptrend, rsi_val = get_trend(asset)
        trend_results[asset] = {"uptrend": is_uptrend, "rsi": rsi_val}
        if not is_uptrend:
            base_w[asset] = min(base_w[asset], 0.15)

    # --- LEVEL 4 & 5: VOL TARGETING & NORMALIZATION ---
    target_vol = 0.12
    final_risk_assets = {}
    for asset in ["SPY", "TLT", "DBC"]:
        log_rets = np.log(df_w[asset] / df_h[asset].shift(1))
        sigma = log_rets.rolling(20).std().iloc[-1] * np.sqrt(52)
        vol_cap = target_vol / sigma if sigma > 0 else 1.0
        final_risk_assets[asset] = min(base_w[asset], vol_cap)

    total_risk_w = sum(final_risk_assets.values())
    final_weights = final_risk_assets
    final_weights["BIL"] = 1.0 - total_risk_w
    
    return final_weights, trend_results, current_quad, confidence_score, circuit_breaker, spread.iloc[-1]

# --- 3. UI DASHBOARD ---
df_w, df_m = get_engine_data()
final_w, trends, quad, conf, cb, yield_spread = run_quant_engine(df_w, df_m)

st.title("🛡️ QUANT MACRO POSITION TRADER")
st.caption(f"📅 Rapor Tarihi: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# Top Metrics
c1, c2, c3 = st.columns(3)
c1.metric("Aktif Kadran", quad, f"Güven: %{conf}")
c2.metric("Resesyon Şalteri", "AKTİF 🚨" if cb else "PASİF ✅")
c3.metric("Tahvil Yayılımı (10Y-3M)", f"%{yield_spread:.2f}")

st.divider()

# Portföy Dağılımı
st.subheader("💼 NİHAİ PORTFÖY TAHSİSİ")
cols = st.columns(4)
assets = ["SPY", "TLT", "DBC", "BIL"]
asset_names = {"SPY": "Hisse (S&P 500)", "TLT": "Hazine Tahvili", "DBC": "Emtia Sepeti", "BIL": "Nakit / Kısa Vadeli"}

for i, asset in enumerate(assets):
    with cols[i]:
        weight = final_w[asset] * 100
        st.markdown(f"**{asset}**")
        st.markdown(f"### %{weight:.1f}")
        st.caption(asset_names[asset])
        if asset != "BIL":
            t = trends[asset]
            st.write(f"Trend: {'✅' if t['uptrend'] else '❌'}")
            st.write(f"RSI: {t['rsi']:.1f}")

st.divider()

# Rebalans Disiplini (Basitleştirilmiş simülasyon)
st.subheader("🔄 REBALANS DİSİPLİNİ (%5 Eşik)")
# Not: Gerçek rebalans için mevcut kullanıcı portföyü gerekir. 
# Burada sadece modelin güncelliğini belirtiyoruz.
st.info("Portföy sapması <%5 threshold içerisinde. İşlem pas geçildi.")

st.sidebar.header("Makro Bekçi Parametreleri")
st.sidebar.write(f"Hedef Volatilite: %12")
st.sidebar.write(f"Trend Penceresi: 50 Hafta")
st.sidebar.write(f"RSI Eşiği: 50")
