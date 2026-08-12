import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import requests

# --- 0. KONFİGÜRASYON ---
st.set_page_config(page_title="Quant Macro Engine V2.3", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #0d1117; color: white; }
    .report-card { background: #161b22; padding: 15px; border-radius: 12px; border: 1px solid #30363d; margin-bottom: 10px; }
    .sector-card { background: #0d1117; padding: 8px; border-radius: 8px; border-left: 3px solid #58a6ff; margin: 4px 0; }
    .metric-val { color: #58a6ff; font-weight: bold; }
    .check-date { font-size: 11px; color: #8b949e; }
    </style>
    """, unsafe_allow_html=True)

# SECRETS (FRED API KEY)
fred_api_key = st.secrets.get("FRED_API_KEY", None)

# --- 1. VERİ MOTORU (SME UYUMLU) ---
@st.cache_data(ttl=3600)
def get_scheduled_data(api_key):
    tickers = {
        'SPY': 'SPY', 'QQQ': 'QQQ', 'SOXX': 'SOXX', 'CIBR': 'CIBR', 'ITA': 'ITA', 
        'XLV': 'XLV', 'XLF': 'XLF', 'XLY': 'XLY', 'XLE': 'XLE', 'BTC': 'BTC-USD',
        'XLI': 'XLI', 'XLP': 'XLP', 'TIP': 'TIP', 'IEF': 'IEF',
        'TLT': 'TLT', 'GLD': 'GLD', 'SLV': 'SLV', 'USO': 'USO', 'DBB': 'DBB', 'DBA': 'DBA', 'BIL': 'BIL'
    }
    df_y = yf.download(list(tickers.values()), period="5y", interval="1d")['Close'].ffill()
    df_y = df_y.rename(columns={v: k for k, v in tickers.items()})
    
    df_f = pd.DataFrame(index=df_y.index)
    if api_key:
        fred_ids = {'T10Y3M': 'T10Y3M', 'T10YIE': 'T10YIE', 'WALCL': 'WALCL'}
        for name, s_id in fred_ids.items():
            try:
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id={s_id}&api_key={api_key}&file_type=json"
                r = requests.get(url).json()
                if 'observations' in r:
                    obs = pd.DataFrame(r['observations'])[['date', 'value']]
                    obs['value'] = pd.to_numeric(obs['value'], errors='coerce')
                    obs['date'] = pd.to_datetime(obs['date'])
                    obs = obs.set_index('date')
                    df_f[name] = obs['value'].reindex(df_y.index, method='ffill')
            except: pass

    # HAFTALIK (Salı: 1, Cuma: 4)
    df_w = df_y[df_y.index.dayofweek.isin([1, 4])].ffill()
    
    # AYLIK (1-15 Kontrolü için SME Frekansı)
    df_m = df_y.resample('SME').last().ffill()
    df_f_m = df_f.resample('SME').last().ffill() if not df_f.empty else pd.DataFrame()
    
    return df_y, df_w, df_m, df_f_m

def z_roll(s): return (s - s.rolling(window=126).mean()) / s.rolling(window=126).std()

# --- 2. HESAPLAMA MOTORU ---
def run_quant_engine(df_y, df_w, df_m, df_f_m):
    g_ratio = df_m['XLI'] / df_m['XLP']
    i_ratio = df_m['TIP'] / df_m['IEF']
    
    g_now = g_ratio.rolling(6).mean().iloc[-1] > g_ratio.rolling(24).mean().iloc[-1]
    i_now = i_ratio.rolling(6).mean().iloc[-1] > i_ratio.rolling(24).mean().iloc[-1]
    
    quad_map = {(True, False): "GOLDILOCKS", (True, True): "AŞIRI ISINMA", 
                (False, True): "STAGFLASYON", (False, False): "DARALMA"}
    current_quad = quad_map.get((g_now, i_now), "DARALMA")

    # LEVEL 2: CIRCUIT BREAKER
    spread = df_f_m['T10Y3M'] if 'T10Y3M' in df_f_m.columns else pd.Series(0, index=df_m.index)
    was_inverted = (spread.shift(1).rolling(12).min() < 0).iloc[-1]
    cb_active = was_inverted and (spread.iloc[-1] > 0)
    
    # Hysteresis
    g_prev = g_ratio.rolling(6).mean().iloc[-2] > g_ratio.rolling(24).mean().iloc[-2]
    i_prev = i_ratio.rolling(6).mean().iloc[-2] > i_ratio.rolling(24).mean().iloc[-2]
    prev_quad = quad_map.get((g_prev, i_prev), "DARALMA")
    confidence = 100 if current_quad == prev_quad else 50
    final_quad = current_quad if confidence == 100 else prev_quad

    base_alloc = {
        "GOLDILOCKS": {"ENDEKS": 0.60, "TAHVIL": 0.20, "EMTIA": 0.10, "NAKIT": 0.10},
        "AŞIRI ISINMA": {"ENDEKS": 0.30, "TAHVIL": 0.00, "EMTIA": 0.50, "NAKIT": 0.20},
        "STAGFLASYON": {"ENDEKS": 0.10, "TAHVIL": 0.00, "EMTIA": 0.50, "NAKIT": 0.40},
        "DARALMA": {"ENDEKS": 0.10, "TAHVIL": 0.50, "EMTIA": 0.10, "NAKIT": 0.30}
    }
    w_base = base_alloc[final_quad].copy()

    # SEKTÖR EVRENİ
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
        w_base = {"ENDEKS": 0.10, "TAHVIL": 0.40, "EMTIA": 0.10, "NAKIT": 0.40}
        active_sectors = ["SPY"]
    else:
        active_sectors = sector_universe.get(final_quad, ["SPY"])
        
    if confidence == 50:
        w_base["ENDEKS"] *= 0.8; w_base["EMTIA"] *= 0.8

    # HESAPLAMA
    res_final = {"SECTORS": {}, "COMMOS": {}, "TLT": 0.0, "BIL": 0.0}
    
    # Endeks & Sektörler
    budget_idx = w_base["ENDEKS"]
    for s in active_sectors:
        price = df_w[s]
        ema50 = price.ewm(span=100).mean().iloc[-1]
        vol = price.pct_change().rolling(40).std().iloc[-1] * np.sqrt(104)
        v_cap = 0.12 / vol if (vol > 0) else 1.0
        s_w = (budget_idx / len(active_sectors))
        if price.iloc[-1] < ema50: s_w = min(s_w, 0.05)
        if s == "BTC" and (final_quad in ["STAGFLASYON", "DARALMA"] or cb_active): s_w = 0.0
        res_final["SECTORS"][s] = {"w": min(s_w, v_cap), "trend": "BOĞA" if price.iloc[-1] > ema50 else "AYI"}

    # Emtia
    budget_c = w_base["EMTIA"]
    for c, sh in commo_universe.get(final_quad, {}).items():
        v_c = df_w[c].pct_change().rolling(40).std().iloc[-1] * np.sqrt(104)
        res_final["COMMOS"][c] = min(sh * budget_c, 0.12 / v_c if v_c > 0 else 1.0)

    # Tahvil
    t_v = df_w['TLT'].pct_change().rolling(40).std().iloc[-1] * np.sqrt(104)
    res_final["TLT"] = min(w_base["TAHVIL"], 0.12 / t_v if t_v > 0 else 1.0)
    
    risky_total = sum([v['w'] for v in res_final["SECTORS"].values()]) + sum(res_final["COMMOS"].values()) + res_final["TLT"]
    res_final["BIL"] = max(0.0, 1.0 - risky_total)
    
    return res_final, current_quad, confidence, cb_active, spread.iloc[-1], df_w.index[-1], df_m.index[-1]

# --- 3. UI DASHBOARD ---
try:
    df_y, df_w, df_m, df_f_m = get_scheduled_data(fred_api_key)
    res, quad, conf, cb, spread_v, w_date, m_date = run_quant_engine(df_y, df_w, df_m, df_f_m)

    st.markdown("### 🛡️ QUANT MACRO POSITION TRADER V2.3")
    st.markdown(f"""<div class="report-card"><span class="check-date">📅 <b>Haftalık:</b> {w_date.strftime('%d.%m.%Y')}</span> | <span class="check-date">🗓️ <b>Aylık:</b> {m_date.strftime('%d.%m.%Y')}</span></div>""", unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Aktif Kadran", quad, f"%{conf}")
    c2.metric("Resesyon Şalteri", "AKTİF 🚨" if cb else "PASİF ✅")
    c3.metric("Yayılım (FRED)", f"%{spread_v:.2f}")

    st.divider()

    t_idx = sum([v['w'] for v in res['SECTORS'].values()])
    with st.expander(f"📌 ENDEKS & SEKTÖRLER (%{t_idx*100:.1f})", expanded=True):
        for s, v in res['SECTORS'].items():
            w_perc = v['w'] * 100
            st.markdown(f"""<div class="sector-card"><div style="display:flex; justify-content:space-between;"><b>{s}</b> <span class="metric-val">%{w_perc:.1f}</span></div><div style="font-size:11px; color:#aaa;">Trend: {v['trend']}</div></div>""", unsafe_allow_html=True)

    t_c = sum(res['COMMOS'].values())
    with st.expander(f"📦 EMTİA GRUBU (%{t_c*100:.1f})", expanded=True):
        for c, weight in res['COMMOS'].items():
            w_c_perc = weight * 100
            st.markdown(f"**{c}:** <span class='metric-val'>%{w_c_perc:.1f}</span>", unsafe_allow_html=True)

    tlt_p, bil_p = res['TLT']*100, res['BIL']*100
    st.markdown(f"""<div class="report-card"><b>🏛️ TAHVİL (TLT):</b> <span class="metric-val">%{tlt_p:.1f}</span><br><b>💵 NAKİT (BIL):</b> <span class="metric-val">%{bil_p:.1f}</span></div>""", unsafe_allow_html=True)

except Exception as e:
    st.error(f"Sistem Hatası: {e}")
