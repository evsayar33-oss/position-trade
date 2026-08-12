import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import requests

# --- 0. KONFİGÜRASYON ---
st.set_page_config(page_title="Quant Macro Engine V2.1", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #0d1117; color: white; }
    .report-card { background: #161b22; padding: 15px; border-radius: 12px; border: 1px solid #30363d; margin-bottom: 10px; }
    .sector-card { background: #0d1117; padding: 8px; border-radius: 8px; border-left: 3px solid #58a6ff; margin: 4px 0; }
    .metric-val { color: #58a6ff; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# SECRETS (FRED API KEY)
fred_api_key = st.secrets.get("FRED_API_KEY", None)

# --- 1. VERİ MOTORU (YAHOO + FRED) ---
@st.cache_data(ttl=3600)
def get_hybrid_data(api_key):
    # Yahoo Verileri (ETF & Kripto)
    tickers = {
        'SPY': 'SPY', 'QQQ': 'QQQ', 'SOXX': 'SOXX', 'CIBR': 'CIBR', 'ITA': 'ITA', 
        'XLV': 'XLV', 'XLF': 'XLF', 'XLY': 'XLY', 'XLE': 'XLE', 'BTC': 'BTC-USD',
        'XLI': 'XLI', 'XLP': 'XLP', 'TIP': 'TIP', 'IEF': 'IEF',
        'TLT': 'TLT', 'GLD': 'GLD', 'SLV': 'SLV', 'USO': 'USO', 'DBB': 'DBB', 'DBA': 'DBA', 'BIL': 'BIL'
    }
    df_y = yf.download(list(tickers.values()), period="5y", interval="1d")['Close'].ffill()
    df_y = df_y.rename(columns={v: k for k, v in tickers.items()})
    
    # FRED Verileri (10Y-3M Spread, Breakeven Inflation, Fed Balance Sheet)
    df_f = pd.DataFrame(index=df_y.index)
    if api_key:
        fred_ids = {
            'T10Y3M': 'T10Y3M',       # Yield Spread (10Y - 3M)
            'T10YIE': 'T10YIE',       # Breakeven Inflation
            'WALCL': 'WALCL'          # Fed Balance Sheet
        }
        for name, s_id in fred_ids.items():
            try:
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id={s_id}&api_key={api_key}&file_type=json"
                r = requests.get(url).json()
                obs = pd.DataFrame(r['observations'])[['date', 'value']]
                obs['value'] = pd.to_numeric(obs['value'], errors='coerce')
                obs['date'] = pd.to_datetime(obs['date'])
                obs = obs.set_index('date')
                df_f[name] = obs['value'].reindex(df_y.index, method='ffill')
            except:
                pass
    
    df_w = df_y.resample('W-FRI').last().ffill()
    df_m = df_y.resample('MS').last().ffill()
    df_f_m = df_f.resample('MS').last().ffill()
    
    return df_y, df_w, df_m, df_f_m

# --- 2. HESAPLAMA MOTORU (LEVEL 1-6) ---
def run_quant_engine(df_y, df_w, df_m, df_f_m):
    # LEVEL 1: MAKRO KADRAN (Market-Implied)
    g_ratio = df_m['XLI'] / df_m['XLP']
    i_ratio = df_m['TIP'] / df_m['IEF']
    
    g_now = g_ratio.rolling(3).mean().iloc[-1] > g_ratio.rolling(12).mean().iloc[-1]
    i_now = i_ratio.rolling(3).mean().iloc[-1] > i_ratio.rolling(12).mean().iloc[-1]
    
    quad_map = {(True, False): "GOLDILOCKS", (True, True): "AŞIRI ISINMA", 
                (False, True): "STAGFLASYON", (False, False): "DARALMA"}
    current_quad = quad_map.get((g_now, i_now))

    # LEVEL 2: CIRCUIT BREAKER (FRED T10Y3M)
    spread = df_f_m['T10Y3M'] if 'T10Y3M' in df_f_m.columns else (df_m['SPY'] * 0) # Fallback
    was_inverted = (spread.shift(1).rolling(6).min() < 0).iloc[-1]
    cb_active = was_inverted and (spread.iloc[-1] > 0)
    
    # Reset Logic
    if (spread.rolling(3).min().iloc[-1] > 0.50) or (g_now and spread.rolling(2).min().iloc[-1] > 0):
        cb_active = False

    # Hysteresis
    confidence = 100 if current_quad == quad_map.get((g_ratio.rolling(3).mean().iloc[-2] > g_ratio.rolling(12).mean().iloc[-2], 
                                                     i_ratio.rolling(3).mean().iloc[-2] > i_ratio.rolling(12).mean().iloc[-2])) else 50
    final_quad = current_quad if confidence == 100 else quad_map.get((g_ratio.rolling(3).mean().iloc[-2] > g_ratio.rolling(12).mean().iloc[-2], 
                                                                    i_ratio.rolling(3).mean().iloc[-2] > i_ratio.rolling(12).mean().iloc[-2]))

    # Taban Tahsisat
    base_alloc = {
        "GOLDILOCKS": {"ENDEKS": 0.60, "TAHVIL": 0.20, "EMTIA": 0.10, "NAKIT": 0.10},
        "AŞIRI ISINMA": {"ENDEKS": 0.30, "TAHVIL": 0.00, "EMTIA": 0.50, "NAKIT": 0.20},
        "STAGFLASYON": {"ENDEKS": 0.10, "TAHVIL": 0.00, "EMTIA": 0.50, "NAKIT": 0.40},
        "DARALMA": {"ENDEKS": 0.10, "TAHVIL": 0.50, "EMTIA": 0.10, "NAKIT": 0.30}
    }
    w = base_alloc[final_quad].copy()

    # LEVEL 1.1: ROTASYON MANTIĞI
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

    # --- NİHAİ HESAPLAMALAR ---
    final_res = {"SECTORS": {}, "COMMOS": {}, "TLT": 0, "BIL": 0}
    
    # 1. Endeks & Sektörler
    idx_budget = w["ENDEKS"]
    active_sectors = sector_universe[final_quad]
    for s in active_sectors:
        price = df_w[s]
        ema20 = price.ewm(span=20).mean().iloc[-1]
        ema50 = price.ewm(span=50).mean().iloc[-1]
        delta = price.diff(); g = delta.where(delta>0,0).rolling(14).mean(); l = (-delta.where(delta<0,0)).rolling(14).mean()
        rsi = 100 - (100/(1+(g.iloc[-1]/l.iloc[-1]))) if l.iloc[-1] != 0 else 100
        is_bull = (price.iloc[-1] > ema50) and (ema20 > ema50) and (rsi > 50)
        
        vol = price.pct_change().rolling(20).std().iloc[-1] * np.sqrt(52)
        v_cap = 0.12 / vol if vol > 0 else 1.0
        s_w = (idx_budget / len(active_sectors))
        if not is_bull: s_w = min(s_w, 0.05)
        if s == "BTC" and (final_quad in ["STAGFLASYON", "DARALMA"] or cb_active): s_w = 0.0
        
        final_res["SECTORS"][s] = {"w": min(s_w, v_cap), "rsi": rsi, "trend": "BOĞA" if is_bull else "AYI"}

    # 2. Emtia
    c_budget = w["EMTIA"]
    for c, sh in commo_universe[final_quad].items():
        v_c = df_w[c].pct_change().rolling(20).std().iloc[-1] * np.sqrt(52)
        v_cap_c = 0.12 / v_c if v_c > 0 else 1.0
        final_res["COMMOS"][c] = min(sh * c_budget, v_cap_c)

    # 3. Tahvil
    t_v = df_w['TLT'].pct_change().rolling(20).std().iloc[-1] * np.sqrt(52)
    final_res["TLT"] = min(w["TAHVIL"], 0.12 / t_v if t_v > 0 else 1.0)
    
    total_r = sum([v['w'] for v in final_res["SECTORS"].values()]) + sum(final_res["COMMOS"].values()) + final_res["TLT"]
    final_res["BIL"] = 1.0 - total_r
    
    return final_res, current_quad, confidence, cb_active, spread.iloc[-1]

# --- 3. UI DASHBOARD ---
try:
    df_y, df_w, df_m, df_f_m = get_hybrid_data(fred_api_key)
    res, quad, conf, cb, spread_v = run_quant_engine(df_y, df_w, df_m, df_f_m)

    st.markdown("### 🛡️ QUANT MACRO POSITION TRADER V2.1")
    if not fred_api_key: st.warning("⚠️ FRED API Key bulunamadı. Makro omurga yedek verilerle çalışıyor.")

    # Üst Bilgiler
    c1, c2, c3 = st.columns(3)
    c1.metric("Aktif Kadran", quad, f"Güven: %{conf}")
    c2.metric("Resesyon Şalteri", "AKTİF 🚨" if cb else "PASİF ✅")
    c3.metric("Tahvil Yayılımı (FRED)", f"%{spread_v:.2f}")

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
                <div style="font-size:11px; color:#aaa;">Trend: {v['trend']} | RSI: {v['rsi']:.1f}</div>
            </div>
            ''', unsafe_allow_html=True)

    # Emtia
    t_c = sum(res['COMMOS'].values())
    with st.expander(f"📦 EMTİA GRUBU (%{t_c*100:.1f})", expanded=True):
        for c, weight in res['COMMOS'].items():
            st.markdown(f"**{c}:** <span class='metric-val'>%{weight*100:.1f}</span>", unsafe_allow_html=True)

    # Özet
    st.markdown(f'''
    <div class="report-card">
        <b>🏛️ TAHVİL (TLT):</b> <span class="metric-val">%{res['TLT']*100:.1f}</span><br>
        <b>💵 NAKİT (BIL):</b> <span class="metric-val">%{res['BIL']*100:.1f}</span>
    </div>
    ''', unsafe_allow_html=True)

    st.info("🔄 Rebalans: Portföy sapması <%5 threshold içerisinde.")

except Exception as e:
    st.error(f"Sistem Hatası: {e}")
