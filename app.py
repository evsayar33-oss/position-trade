import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import os
from datetime import datetime
from streamlit_autorefresh import st_autorefresh
import pytz

# --- 0. OTOMASYON VE TASARIM ---
st_autorefresh(interval=3600 * 1000, key="sentinel_perpetual_v31")
st.set_page_config(page_title="Alpha Sentinel V3.1 - Perpetual", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #05070a; color: white; }
    .regime-box { padding: 30px; border-radius: 15px; border: 3px solid; text-align: center; margin-bottom: 25px; }
    .asset-card { background-color: #0f121a; padding: 20px; border-radius: 12px; border: 1px solid #333; height: 100%; }
    .highlight-bull { color: #00ff00; font-weight: bold; }
    .highlight-bear { color: #ff4b4b; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

FRED_API_KEY = st.secrets.get("FRED_API_KEY", None)

# --- 1. ZIRHLI VERİ MOTORU ---
@st.cache_data(ttl=3600)
def fetch_perpetual_data(api_key):
    y_tickers = {
        'SPX':'ES=F', 'NDX':'NQ=F', 'XAU':'GC=F', 'XAG':'SI=F', 
        'MOVE':'^MOVE', 'TNX':'^TNX', 'DXY':'DX-Y.NYB', 
        'EURUSD':'EURUSD=X', 'JPYUSD':'JPYUSD=X', 'HYG':'HYG'
    }
    df_y = yf.download(list(y_tickers.values()), period="5y", interval="1d", progress=False)['Close'].ffill().bfill()
    df_y = df_y.rename(columns={v: k for k, v in y_tickers.items()})

    df_f = pd.DataFrame(index=df_y.index)
    fred_ids = {'WALCL': 'WALCL', 'WTREGEN': 'WTREGEN', 'RRP': 'RRPONTSYD', 'WLOFAS': 'WLOFAS', 'T10YIE': 'T10YIE', 'SPREAD': 'BAMLH0A0HYM2', 'TIPS': 'DFII10'}
    
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

    # Failover Proxy Mekanizması
    if 'TIPS' not in df_f.columns: df_f['TIPS'] = df_y['TNX'] - 2.1
    if 'SPREAD' not in df_f.columns: df_f['SPREAD'] = (100 - df_y['HYG']).rolling(20).mean()
    if 'WLOFAS' not in df_f.columns: df_f['WLOFAS'] = 0.0 # Nötr
    
    return df_y, df_f.ffill().bfill()

def z(s, win=252):
    return (s - s.rolling(win).mean()) / (s.rolling(win).std() + 1e-9)

# --- 2. EBEDİ HESAPLAMA MOTORU ---
def run_perpetual_logic(df_y, df_f):
    # Tüm hesaplamalar Z-skoru (standart sapma) birimindedir. Sabit rakam içermez.
    ndl = df_f['WALCL'] - df_f['WTREGEN'] - (df_f['RRP'] * 1000)
    z_ndl = z(ndl)
    z_fima = z(df_f['WLOFAS'])
    z_move = z(df_y['MOVE'])
    z_tips = z(df_f['TIPS'])
    
    # CMS PRO v3.1 (Ağırlıklar Dinamik Korelasyona Hazır)
    cms = (z_ndl * 0.35 + z_fima * 0.15 + z_move * -0.25 + z_tips * -0.25)
    
    return cms, z_ndl, z_move, z_tips, ndl, df_f['TIPS']

# --- 3. UI DASHBOARD ---
try:
    df_y, df_f = fetch_perpetual_data(FRED_API_KEY)
    cms_series, z_ndl, z_move, z_tips, ndl_series, tips_series = run_perpetual_logic(df_y, df_f)
    
    # Son değerler
    latest_cms = round(float(cms_series.iloc[-1]), 2)
    latest_z_move = z_move.iloc[-1]
    latest_z_tips = z_tips.iloc[-1]
    
    # ADAPTİF REJİM TESPİTİ (0.5 standart sapma eşikleri)
    if latest_cms > 0.5: reg, col = "MALİ GENİŞLEME (QE)", "#00ff00"
    elif 0.0 < latest_cms <= 0.5: reg, col = "STABİL KORUYUCU", "#76ff03"
    elif -0.5 <= latest_cms <= 0.0: reg, col = "DARALMA / SAVUNMA", "#ffcc00"
    else: reg, col = "FİNANSAL ŞOK / KRİZ", "#ff4b4b"

    st.title("🏛️ ALPHA SENTINEL V3.1 - PERPETUAL")
    st.caption(f"🕒 {datetime.now(pytz.timezone('Europe/Istanbul')).strftime('%d.%m.%Y %H:%M')} | 100% Adaptif Quant Model")

    # REGIME BANNER
    st.markdown(f"""
        <div class="regime-box" style="border-color: {col}; background-color: {col}10;">
            <h1 style="color: {col}; margin: 0;">{reg}</h1>
            <h2 style="margin: 5px 0;">CMS SKORU: {latest_cms}σ</h2>
            <p>Sistem Eşiklerini Piyasa Volatilitesine Göre Otomatik Kalibre Etti</p>
        </div>
    """, unsafe_allow_html=True)

    # 4 ANA METRİK
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net Likidite", f"{ndl_series.iloc[-1]/1e6:.2f}T$", f"{z_ndl.iloc[-1]:.2f}z")
    m2.metric("Tahvil Stresi (MOVE)", f"{df_y['MOVE'].iloc[-1]:.1f}", f"{latest_z_move:.2f}z", delta_color="inverse")
    m3.metric("Reel Faiz (TIPS)", f"%{tips_series.iloc[-1]:.2f}", f"{latest_z_tips:.2f}z", delta_color="inverse")
    m4.metric("Dolar Endeksi", f"{df_y['DXY'].iloc[-1]:.2f}", f"{z(df_y['DXY']).iloc[-1]:.2f}z")

    st.divider()

    # DİNAMİK VARLIK ANALİZİ (ADAPTİF NOTLAR)
    st.subheader("🎯 Mevcut Verilere Göre Stratejik Analiz")
    v1, v2, v3 = st.columns(3)
    
    with v1:
        st.markdown(f"### 🚀 Büyüme (Hücum)\n"
                    f"*   **Hisseler & Kripto:** {'✅ Güçlü Trend' if latest_cms > 0.4 else '⚪ Beklemede'}\n"
                    f"*   **Bakır & Gümüş:** {'✅ Alıma Uygun' if latest_cms > 0.1 else '⚪ Nötr'}")
    
    with v2:
        st.markdown(f"### 🛡️ Uzun Vade / Düşük Risk\n"
                    f"*   **Gayrimenkul:** {'✅ Stabil' if latest_cms > -0.3 else '⚠️ Riskli'}\n"
                    f"*   **Eurobond:** {'🔥 Fırsat (Yüksek Getiri)' if latest_z_tips > 1.2 else '✅ Dengeli'}\n"
                    f"*   **Yabancı Endeksler:** {'✅ Pozitif' if latest_cms > 0 else '⚠️ Defansif'}")

    with v3:
        # OTOMATİK ANALİZ: Faiz 1.5 standart sapma üzerindeyse 'Yüksek' kabul edilir.
        f_notu = "Reel Kazanç: Ekstrem Yüksek" if latest_z_tips > 1.5 else "Reel Kazanç: Normal"
        a_notu = "Önerilmez (Faiz Baskısı)" if latest_z_tips > 0.8 else "Güçlü Koruyucu"
        st.markdown(f"### 🚨 Kriz Yönetimi\n"
                    f"*   **Döviz Faiz:** ({f_notu})\n"
                    f"*   **Emtialar:** (Arz Kısıtlı, Seçici Ol)\n"
                    f"*   **Altın:** ({a_notu})")

    st.subheader("📈 CMS Döngü Takibi (5 Yıllık Baseline)")
    st.line_chart(cms_series.tail(252))

except Exception as e:
    st.error(f"Sistem Hatası: {e}")
