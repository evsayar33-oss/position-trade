import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

# --- 0. AYARLAR VE TASARIM ---
st.set_page_config(page_title="Quant Macro Pro V2", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #0d1117; color: white; }
    .report-card { background: #161b22; padding: 15px; border-radius: 12px; border: 1px solid #30363d; margin-bottom: 10px; }
    .sector-card { background: #0d1117; padding: 8px; border-radius: 8px; border-left: 3px solid #58a6ff; margin: 4px 0; }
    .metric-val { color: #58a6ff; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- 1. VERİ HAVUZU ---
@st.cache_data(ttl=3600)
def get_dynamic_data():
    tickers = {
        'XLI': 'XLI', 'XLP': 'XLP', 'TIP': 'TIP', 'IEF': 'IEF', 'TNX': '^TNX', 'IRX': '^IRX',
        'SPY': 'SPY', 'QQQ': 'QQQ', 'SOXX': 'SOXX', 'CIBR': 'CIBR', 'ITA': 'ITA', 
        'XLV': 'XLV', 'XLF': 'XLF', 'XLY': 'XLY', 'XLE': 'XLE', 'BTC': 'BTC-USD',
        'TLT': 'TLT', 'GLD': 'GLD', 'SLV': 'SLV', 'USO': 'USO', 'DBB': 'DBB', 'DBA': 'DBA', 'BIL': 'BIL', 'DBC': 'DBC'
    }
    raw = yf.download(list(tickers.values()), period="5y", interval="1d")['Close'].ffill()
    df = raw.rename(columns={v: k for k, v in tickers.items()})
    
    df_w = df.resample('W-FRI').last().ffill()
    df_m = df.resample('MS').last().ffill()
    return df, df_w, df_m

def z_roll(s): return (s - s.rolling(126).mean()) / s.rolling(126).std()

# --- 2. QUANT MOTORU ---
def run_quant_engine(df_d, df_w, df_m):
    # LEVEL 1: MAKRO KADRAN
    g_ratio = df_m['XLI'] / df_m['XLP']
    i_ratio = df_m['TIP'] / df_m['IEF']
    
    g_now = g_ratio.rolling(3).mean().iloc[-1] > g_ratio.rolling(12).mean().iloc[-1]
    i_now = i_ratio.rolling(3).mean().iloc[-1] > i_ratio.rolling(12).mean().iloc[-1]
    
    quad_map = {(True, False): "GOLDILOCKS", (True, True): "AŞIRI ISINMA", 
                (False, True): "STAGFLASYON", (False, False): "DARALMA"}
    current_quad = quad_map.get((g_now, i_now))

    # LEVEL 2: CIRCUIT BREAKER
    spread = df_m['TNX'] - df_m['IRX']
    was_inverted = (spread.shift(1).rolling(6).min() < 0).iloc[-1]
    cb_active = was_inverted and (spread.iloc[-1] > 0)
    
    if (spread.rolling(3).min().iloc[-1] > 0.50) or (g_now and spread.rolling(2).min().iloc[-1] > 0):
        cb_active = False

    # Hysteresis
    g_prev = g_ratio.rolling(3).mean().iloc[-2] > g_ratio.rolling(12).mean().iloc[-2]
    i_prev = i_ratio.rolling(3).mean().iloc[-2] > i_ratio.rolling(12).mean().iloc[-2]
    prev_quad = quad_map.get((g_prev, i_prev))
    confidence = 100 if current_quad == prev_quad else 50
    final_quad = current_quad if confidence == 100 else prev_quad

    base_alloc = {
        "GOLDILOCKS": {"ENDEKS": 0.60, "TAHVIL": 0.20, "EMTIA": 0.10, "NAKIT": 0.10},
        "AŞIRI ISINMA": {"ENDEKS": 0.30, "TAHVIL": 0.00, "EMTIA": 0.50, "NAKIT": 0.20},
        "STAGFLASYON": {"ENDEKS": 0.10, "TAHVIL": 0.00, "EMTIA": 0.50, "NAKIT": 0.40},
        "DARALMA": {"ENDEKS": 0.10, "TAHVIL": 0.50, "EMTIA": 0.10, "NAKIT": 0.30}
    }
    w = base_alloc[final_quad].copy()

    # LEVEL 1.1: ROTASYON EVRENİ
    sector_universe = {
        "GOLDILOCKS": ["SOXX", "CIBR", "XLY", "QQQ", "BTC"],
        "AŞIRI ISINMA": ["XLF", "XLI", "XLE", "SPY"],
        "STAGFLASYON": ["XLV", "ITA", "SPY"],
        "DARALMA": ["XLV", "ITA", "SPY"]
    }
    commo_universe = {
        "GOLDILOCKS": {"GLD": 0.5, "SLV": 0.2, "DBB": 0.15, "DBA": 0.15},
        "DARALMA": {"GLD": 0.5, "SLV": 0.2, "DBB": 0.15, "DBA": 0.15},
        "AŞIRI ISINMA": {"USO": 0.4, "GLD": 0.3, "SLV": 0.15, "DBA": 0.15},
        "STAGFLASYON": {"USO": 0.4, "GLD": 0.3, "SLV": 0.15, "DBA": 0.15}
    }

    if cb_active:
        w = {"ENDEKS": 0.10, "TAHVIL": 0.40, "EMTIA": 0.10, "NAKIT": 0.40}
        sector_universe[final_quad] = ["SPY"]
        
    if confidence == 50:
        w["ENDEKS"] *= 0.8; w["EMTIA"] *= 0.8

    # --- HESAPLAMA ---
    final_res = {"SECTORS": {}, "COMMOS": {}, "TLT": 0, "BIL": 0}
    
    # 1. Sektörler
    idx_budget = w["ENDEKS"]
    active_sectors = sector_universe[final_quad]
    for s in active_sectors:
        price = df_w[s]
        ema20 = price.ewm(span=20).mean().iloc[-1]
        ema50 = price.ewm(span=50).mean().iloc[-1]
        delta = price.diff(); g = delta.where(delta > 0, 0).rolling(14).mean(); l = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (g.iloc[-1]/l.iloc[-1]))) if l.iloc[-1] != 0 else 100
        is_bull = (price.iloc[-1] > ema50) and (ema20 > ema50) and (rsi > 50)
        
        vol = price.pct_change().rolling(20).std().iloc[-1] * np.sqrt(52)
        v_cap = 0.12 / vol if vol > 0 else 1.0
        s_w = (idx_budget / len(active_sectors))
        if not is_bull: s_w = min(s_w, 0.05)
        final_res["SECTORS"][s] = {"w": min(s_w, v_cap), "rsi": rsi, "trend": "BOĞA" if is_bull else "AYI"}

    # 2. Emtia
    c_budget = w["EMTIA"]
    c_alloc = commo_universe[final_quad]
    for c, sh in c_alloc.items():
        v_c = df_w[c].pct_change().rolling(20).std().iloc[-1] * np.sqrt(52)
        v_cap_c = 0.12 / v_c if v_c > 0 else 1.0
        final_res["COMMOS"][c] = min(sh * c_budget, v_cap_c)

    # 3. Tahvil & Nakit
    t_v = df_w['TLT'].pct_change().rolling(20).std().iloc[-1] * np.sqrt(52)
    final_res["TLT"] = min(w["TAHVIL"], 0.12 / t_v if t_v > 0 else 1.0)
    
    total_r = sum([v['w'] for v in final_res["SECTORS"].values()]) + sum(final_res["COMMOS"].values()) + final_res["TLT"]
    final_res["BIL"] = 1.0 - total_r
    
    return final_res, current_quad, confidence, cb_active, spread.iloc[-1]

# --- 3. UI ---
try:
    d_d, d_w, d_m = get_dynamic_data()
    res, quad, conf, cb, spread_v = run_quant_engine(d_d, d_w, d_m)

    st.markdown("### 🛡️ QUANT MACRO POSITION TRADER")
    st.caption(f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    # Özet
    c1, c2, c3 = st.columns(3)
    c1.metric("Kadran", quad, f"%{conf}")
    c2.metric("Şalter", "AKTİF 🚨" if cb else "PASİF ✅")
    c3.metric("10Y-3M Spread", f"%{spread_v:.2f}")

    st.divider()

    # Sektörler
    t_idx = sum([v['w'] for v in res['SECTORS'].values()])
    with st.expander(f"📌 ENDEKS & SEKTÖRLER (%{t_idx*100:.1f})", expanded=True):
        for s, v in res['SECTORS'].items():
            st.markdown(f'''
            <div class="sector-card">
                <div style="display:flex; justify-content:space-between;">
                    <b>{s}</b> <span class="metric-val">%{v['w']*100:.1f}</span>
                </div>
                <div style="font-size:11px; color:#aaa;">EMA: {v['trend']} | RSI: {v['rsi']:.1f}</div>
            </div>
            ''', unsafe_allow_html=True)

    # Emtia
    t_c = sum(res['COMMOS'].values())
    with st.expander(f"📦 EMTİA GRUBU (%{t_c*100:.1f})", expanded=True):
        for c, weight in res['COMMOS'].items():
            st.markdown(f"**{c}:** <span class='metric-val'>%{weight*100:.1f}</span>", unsafe_allow_html=True)

    # Tahvil & Nakit
    st.markdown(f'''
    <div class="report-card">
        <b>🏛️ TAHVİL (TLT):</b> <span class="metric-val">%{res['TLT']*100:.1f}</span><br>
        <b>💵 NAKİT (BIL):</b> <span class="metric-val">%{res['BIL']*100:.1f}</span>
    </div>
    ''', unsafe_allow_html=True)

    st.info("🔄 Rebalans: Portföy sapması <%5 threshold içerisinde.")

except Exception as e:
    st.error(f"Sistem Hatası: {e}")
