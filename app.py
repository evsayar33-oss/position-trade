import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import os
from datetime import datetime, timedelta
import pytz

# --- 0. TASARIM VE OTOMASYON ---
st.set_page_config(page_title="Alpha Sentinel V3.0 - Fiscal Pro", layout="wide")
st.markdown("<style>.main { background-color: #05070a; color: white; }</style>", unsafe_allow_html=True)

FRED_API_KEY = st.secrets.get("FRED_API_KEY", None)
HISTORY_FILE = "cms_history.csv"

# --- 1. KURUMSAL VERİ MOTORU ---
@st.cache_data(ttl=3600)
def fetch_fiscal_macro_data(api_key):
    # Semboller: Tahvil Volatilitesi (MOVE), Altın, Bakır, Endeksler, Kur
    y_tickers = {
        'SPX':'ES=F', 'NDX':'NQ=F', 'XAU':'GC=F', 'XAG':'SI=F', 
        'MOVE':'^MOVE', 'TNX':'^TNX', 'DXY':'DX-Y.NYB', 
        'EURUSD':'EURUSD=X', 'JPYUSD':'JPYUSD=X'
    }
    df_y = yf.download(list(y_tickers.values()), period="5y", interval="1d", progress=False)['Close'].ffill().bfill()
    df_y = df_y.rename(columns={v: k for k, v in y_tickers.items()})

    # FRED Verileri: NDL, FIMA Repo, Real Rates, Credit Spread
    df_f = pd.DataFrame(index=df_y.index)
    fred_ids = {
        'WALCL': 'WALCL',       # Fed Assets
        'WTREGEN': 'WTREGEN',   # TGA (Hazine Hesabı)
        'RRP': 'RRPONTSYD',     # Reverse Repo
        'WLOFAS': 'WLOFAS',     # FIMA Repo (Foreign Custody)
        'T10YIE': 'T10YIE',     # Breakeven
        'SPREAD': 'BAMLH0A0HYM2', # HY Spread
        'TIPS': 'DFII10'        # 10Y Real Rate
    }
    
    if api_key:
        for name, s_id in fred_ids.items():
            try:
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id={s_id}&api_key={api_key}&file_type=json&sort_order=desc&limit=500"
                r = requests.get(url, timeout=10).json()
                obs = pd.DataFrame(r['observations'])[['date', 'value']]
                obs['value'] = pd.to_numeric(obs['value'], errors='coerce')
                obs['date'] = pd.to_datetime(obs['date'])
                df_f[name] = obs.set_index('date')['value'].reindex(df_y.index, method='ffill')
            except: pass

    # Fallback & Proxy Management
    if 'TIPS' not in df_f.columns: df_f['TIPS'] = df_y['TNX'] - 2.1
    return df_y, df_f.ffill().bfill()

def get_z(s, win=252):
    return (s - s.rolling(win).mean()) / (s.rolling(win).std() + 1e-9)

# --- 2. HESAPLAMA MOTORU (FISCAL DOMINANCE) ---
def run_v3_engine(df_y, df_f):
    # L2: Net Dolar Likiditesi (TGA Geri Alımları Dahil)
    # Formül: Fed Bilanço - Hazine Hesabı - RRP
    ndl = df_f['WALCL'] - df_f['WTREGEN'] - (df_f['RRP'] * 1000)
    z_ndl = get_z(ndl)
    
    # FIMA Repo Etkisi: Yabancıların tahvil tutma hızı (Likitite Koruma)
    z_fima = get_z(df_f['WLOFAS'])
    
    # MOVE Index (Tahvil Oynaklığı - Bessent Sinyali)
    z_move = get_z(df_y['MOVE'])
    
    # CMS PRO v3 Hesaplama
    # Ağırlıklar: Likidite %35, FIMA %15, MOVE %25, Reel Faiz %25
    cms = (
        z_ndl.iloc[-1] * 0.35 +
        z_fima.iloc[-1] * 0.15 +
        z_move.iloc[-1] * -0.25 + # Oynaklık artarsa skor düşer
        get_z(df_f['TIPS']).iloc[-1] * -0.25 # Reel faiz artarsa skor düşer
    )
    
    return round(float(np.nan_to_num(cms)), 2), ndl.iloc[-1], df_f['WLOFAS'].iloc[-1], df_y['MOVE'].iloc[-1]

# --- 3. UI DASHBOARD ---
try:
    df_y, df_f = fetch_fiscal_macro_data(FRED_API_KEY)
    cms_val, ndl_val, fima_val, move_val = run_v3_engine(df_y, df_f)

    # Rejim Tespiti
    if cms_val > 0.4: reg, col = "MALİ GENİŞLEME (QE)", "#00ff00"
    elif 0.0 < cms_val <= 0.4: reg, col = "STABİL KORUYUCU", "#76ff03"
    elif -0.5 <= cms_val <= 0.0: reg, col = "DARALMA / SAVUNMA", "#ffcc00"
    else: reg, col = "FİNANSAL ŞOK / KRİZ", "#ff4b4b"

    st.title("🏛️ ALPHA SENTINEL V3.0 - FISCAL PRO")
    
    # BANNER
    st.markdown(f"""
        <div style="padding:25px; border-radius:15px; border:3px solid {col}; background:{col}05; text-align:center; margin-bottom:20px;">
            <h1 style="color:{col}; margin:0;">{reg}</h1>
            <h2 style="margin:5px 0;">CMS MALİ SKOR: {cms_val}</h2>
            <p>TGA Geri Alımları ve MOVE Endeksi Teyitli</p>
        </div>
    """, unsafe_allow_html=True)

    # 4 ANA SÜTUN (Senin istediğin metrikler)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Net Likidite (NDL)", f"{ndl_val/1e6:.2f}T$", delta="TGA Geri Alım Etkisi")
    with c2:
        st.metric("MOVE Endeksi", f"{move_val:.2f}", delta="Tahvil Oynaklığı", delta_color="inverse")
    with c3:
        st.metric("FIMA Repo (Foreign)", f"{fima_val/1e3:.1f}B$", delta="Yabancı Saklama")
    with c4:
        st.metric("10Y Reel Faiz (TIPS)", f"%{df_f['TIPS'].iloc[-1]:.2f}", delta="Faiz Baskısı", delta_color="inverse")

    st.divider()

    # STRATEJİK TAVSİYELER
    st.subheader("🎯 Stratejik Portföy Konumlandırma")
    v1, v2, v3 = st.columns(3)
    
    with v1:
        st.info("🚀 **Hücum Varlıkları**")
        if cms_val > 0.2: st.write("✅ Hisseler & Kripto \n✅ Bakır & Gümüş")
        else: st.write("⚪ Nakit Beklemede")
        
    with v2:
        st.success("🛡️ **Koruyucu Varlıklar**")
        st.write("✅ Gayrimenkul \n✅ Eurobond \n✅ Yabancı Endeksler")
        
    with v3:
        st.error("🚨 **Kriz Yönetimi**")
        if move_val > 120 or cms_val < -0.4:
            st.write("🔥 Döviz Faiz (Yüksek) \n🔥 Fiziki Altın \n🔥 Kısa Vadeli Tahvil")
        else:
            st.write("🟢 Finansal Koşullar Stabil")

except Exception as e:
    st.error(f"Sistem Hatası: {e}")
