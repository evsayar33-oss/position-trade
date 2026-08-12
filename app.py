import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

# --- 0. SİSTEM AYARLARI ---
st.set_page_config(page_title="Quant Macro Position Trader", layout="wide")
st.markdown("<style>.main { background-color: #0d1117; color: white; }</style>", unsafe_allow_html=True)

# --- 1. VERİ MOTORU (API KEY GEREKTİRMEZ) ---
@st.cache_data(ttl=3600)
def get_engine_data():
    # Gerekli semboller: ES=F (SPY), TLT, DBC, BIL, XLI, XLP, TIP, IEF, ^TNX (10Y), ^IRX (13W)
    tickers = {
        'SPY': 'SPY', 'TLT': 'TLT', 'DBC': 'DBC', 'BIL': 'BIL',
        'XLI': 'XLI', 'XLP': 'XLP', 'TIP': 'TIP', 'IEF': 'IEF',
        'TNX': '^TNX', 'IRX': '^IRX'
    }
    # Veriyi çek (5 yıllık günlük veri)
    raw = yf.download(list(tickers.values()), period="5y", interval="1d")['Close'].ffill()
    df = raw.rename(columns={v: k for k, v in tickers.items()})
    
    # IRX endeks değerini yüzdeye çevir (Hazine bonosu faizi)
    df['IRX'] = df['IRX']
    
    # Haftalık (Cuma) ve Aylık (Ay Başı) resample
    df_w = df.resample('W-FRI').last().ffill()
    df_m = df.resample('MS').last().ffill()
    
    return df, df_w, df_m

# --- 2. QUANT MOTORU ---
def run_quant_engine(df_d, df_w, df_m):
    # --- LEVEL 1: MAKRO KADRAN ---
    growth_ratio = df_m['XLI'] / df_m['XLP']
    infl_ratio = df_m['TIP'] / df_m['IEF']
    
    # 3 Ay vs 12 Ay Ortalamalar
    g_signal = growth_ratio.rolling(3).mean().iloc[-1] > growth_ratio.rolling(12).mean().iloc[-1]
    i_signal = infl_ratio.rolling(3).mean().iloc[-1] > infl_ratio.rolling(12).mean().iloc[-1]
    
    def get_quad_name(g, i):
        if g and not i: return "GOLDILOCKS"
        if g and i: return "AŞIRI ISINMA"
        if not g and i: return "STAGFLASYON"
        return "DARALMA"

    current_quad = get_quad_name(g_signal, i_signal)
    prev_g = growth_ratio.rolling(3).mean().iloc[-2] > growth_ratio.rolling(12).mean().iloc[-2]
    prev_i = infl_ratio.rolling(3).mean().iloc[-2] > infl_ratio.rolling(12).mean().iloc[-2]
    prev_quad = get_quad_name(prev_g, prev_i)

    # --- LEVEL 2: CIRCUIT BREAKER & HYSTERESIS ---
    spread = df_m['TNX'] - df_m['IRX']
    # TİGGER: Son 6 ayda negatif (<0) olup tekrar >0 oldu mu?
    was_inverted = (spread.shift(1).rolling(6).min() < 0).iloc[-1]
    circuit_breaker = was_inverted and (spread.iloc[-1] > 0)
    
    # Reset Koşulları
    cb_reset_a = (spread.rolling(3).min().iloc[-1] > 0.50)
    cb_reset_b = g_signal and (spread.rolling(2).min().iloc[-1] > 0)
    if cb_reset_a or cb_reset_b: circuit_breaker = False

    # Hysteresis
    confidence = 100 if current_quad == prev_quad else 50

    # Taban Ağırlıklar
    base_weights = {
        "GOLDILOCKS": {"SPY": 0.60, "TLT": 0.20, "DBC": 0.10, "BIL": 0.10},
        "AŞIRI ISINMA": {"SPY": 0.30, "TLT": 0.00, "DBC": 0.50, "BIL": 0.20},
        "STAGFLASYON": {"SPY": 0.10, "TLT": 0.00, "DBC": 0.50, "BIL": 0.40},
        "DARALMA": {"SPY": 0.10, "TLT": 0.50, "DBC": 0.10, "BIL": 0.30}
    }
    
    w = base_weights[current_quad if confidence == 100 else prev_quad].copy()

    # CB ve Güven Düzeltmesi
    if circuit_breaker:
        w = {"SPY": 0.10, "TLT": 0.40, "DBC": 0.10, "BIL": 0.40}
    elif confidence == 50:
        w["SPY"] *= 0.80
        w["DBC"] *= 0.80

    # --- LEVEL 3: TREND GATE ---
    trend_data = {}
    for asset in ["SPY", "TLT", "DBC"]:
        price = df_w[asset]
        ema20 = price.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = price.ewm(span=50, adjust=False).mean().iloc[-1]
        # RSI 14
        delta = price.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean().iloc[-1]
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean().iloc[-1]
        rsi = 100 - (100 / (1 + (gain/loss))) if loss != 0 else 100
        
        is_up = (price.iloc[-1] > ema50) and (ema20 > ema50) and (rsi > 50)
        trend_data[asset] = {"is_up": is_up, "rsi": rsi, "status": "BOĞA" if is_up else "AYI"}
        if not is_up: w[asset] = min(w[asset], 0.15)

    # --- LEVEL 4 & 5: VOL TARGETING & NORMALIZATION ---
    target_vol = 0.12
    risk_weights = {}
    for asset in ["SPY", "TLT", "DBC"]:
        # 20 Haftalık Standart Sapma
        vol = df_w[asset].pct_change().rolling(20).std().iloc[-1] * np.sqrt(52)
        vol_cap = target_vol / vol if vol > 0 else 1.0
        risk_weights[asset] = min(w[asset], vol_cap)

    total_risk_w = sum(risk_weights.values())
    risk_weights["BIL"] = 1.0 - total_risk_w
    
    return risk_weights, trend_data, current_quad, confidence, circuit_breaker, spread.iloc[-1]

# --- 3. RAPORLAMA VE UI ---
try:
    df_d, df_w, df_m = get_engine_data()
    final_w, trends, quad, conf, cb, spread_val = run_quant_engine(df_d, df_w, df_m)

    st.markdown(f"""
    ---
    🛡️ **QUANT MACRO POSITION TRADER RAPORU**
    📅 Tarih: {datetime.now().strftime('%d %B %Y %H:%M')}

    📊 **MAKRO & İVME SİNYALLERİ:**
    • Aktif Kadran: **{quad}** (Güven Skoru: %{conf})
    • Resesyon Şalteri: **{'AKTİF 🚨' if cb else 'PASİF ✅'}** (Tahvil Yayılımı (10Y-3M): %{spread_val:.2f})
    
    💼 **NİHAİ PORTFÖY TAHSİSİ (NORMALİZE %100):**
    1. **SPY (Hisse):** %{final_w['SPY']*100:.1f} [EMA Trend: {trends['SPY']['status']} | RSI: {trends['SPY']['rsi']:.1f}]
    2. **TLT (Tahvil):** %{final_w['TLT']*100:.1f} [EMA Trend: {trends['TLT']['status']} | RSI: {trends['TLT']['rsi']:.1f}]
    3. **DBC (Emtia):** %{final_w['DBC']*100:.1f} [EMA Trend: {trends['DBC']['status']} | RSI: {trends['DBC']['rsi']:.1f}]
    4. **BIL (Nakit/Kısa Vadeli):** %{final_w['BIL']*100:.1f} [Tamamlayıcı Bakiye]

    🔄 **REBALANS KARARI:**
    • Durum: **İZLEMEDE**
    • Açıklama: Portföy sapması <%5 threshold içerisinde. İşlem pas geçildi.
    ---
    """)

except Exception as e:
    st.error(f"Sistem Hatası: {e}")
