import streamlit as st
import pandas as pd
import os
import numpy as np

# --- 0. TASARIM VE CSS ---
st.set_page_config(page_title="Ultimate Macro Sentinel V3.2", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #05070a; color: white; }
    .regime-box { padding: 25px; border-radius: 15px; border: 4px solid; text-align: center; margin-bottom: 20px; }
    .asset-card { background-color: #0f121a; padding: 20px; border-radius: 12px; border: 1px solid #333; height: 100%; }
    .health-panel { background-color: #161b22; padding: 15px; border-radius: 10px; border-left: 5px solid #58a6ff; margin-bottom: 20px; }
    </style>
    """, unsafe_allow_html=True)

if os.path.exists("cms_history.csv"):
    df = pd.read_csv("cms_history.csv")
    if not df.empty:
        latest = df.iloc[-1]
        val, rr, move = latest['cms'], latest['real_rate'], latest['move']
        
        # REJİM BELİRLEME
        if val > 0.4: reg, col, status = "LİKİDİTE BOĞASI (QE)", "#00ff00", "HÜCUM"
        elif 0.0 < val <= 0.4: reg, col, status = "UZUN VADELİ KORUYUCU", "#76ff03", "STABİLİTE"
        elif -0.4 <= val <= 0.0: reg, col, status = "SIKIŞMA / SAVUNMA", "#ffcc00", "SAVUNMA"
        else: reg, col, status = "MAKRO ÇÖKÜŞ / RESESYON", "#ff4b4b", "KORUMA"

        st.title("🛡️ ULTIMATE MACRO SENTINEL (PRO)")

        # --- 1. SİSTEM SAĞLIK PANOSU ---
        with st.expander("📡 Veri Hattı Sağlık Raporu (Data Health Monitor)", expanded=False):
            c1, c2, c3 = st.columns(3)
            is_fred_ok = latest['ndl'] > 0
            c1.write(f"**FRED API:** {'🟢 AKTİF' if is_fred_ok else '🔴 PROXY'}")
            c2.write(f"**Global Kur Verisi:** 🟢 CANLI")
            c3.write(f"**Son Senkronizasyon:** {latest['date']}")
            if not is_fred_ok:
                st.warning("Veriler şu an tahmini modellerle (Proxy) besleniyor.")

        # --- 2. ANA REJİM BANNERI ---
        st.markdown(f"""
            <div class="regime-box" style="border-color: {col}; background-color: {col}05;">
                <h1 style="color:{col}; margin:0;">{reg}</h1>
                <h2 style="margin:5px 0;">CMS PRO SKORU: {val:.2f}σ</h2>
                <p style="font-size:14px; opacity:0.7;">Anlık Faktör Hakimiyeti (IC Weights): {latest.get('weights', '0.25')}</p>
            </div>
        """, unsafe_allow_html=True)

        # --- 3. AKILLI VARLIK ANALİZİ ---
        st.subheader("🎯 Stratejik Varlık Analizi & Notlar")
        v1, v2, v3 = st.columns(3)
        
        with v1:
            st.markdown(f"### 🚀 Büyüme (Risk-On)\n"
                        f"* **Hisseler:** {'✅ Tam Kapasite' if val > 0.4 else '⚪ İzle'}\n"
                        f"* **Kripto:** {'🚀 Agresif Al' if val > 0.4 else '⚪ Bekle'}\n"
                        f"* **Bakır/Gümüş:** {'🔥 Alıma Uygun' if val > 0.2 else '⚪ Nötr'}")
        with v2:
            st.markdown(f"### 🛡️ Sabit/Düşük Risk\n"
                        f"* **Gayrimenkul:** {'✅ Stabil' if val > -0.2 else '⚠️ Bekle'}\n"
                        f"* **Eurobond:** {'🔥 Alım (Yüksek Getiri)' if rr > 1.8 else '✅ Pozitif'}\n"
                        f"* **Yabancı Endeksler:** {'✅ Pozitif' if val > 0 else '⚠️ Defansif'}")
        with v3:
            f_notu = "Reel Kazanç Yüksek" if rr > 1.8 else "Kazanç Düşük"
            a_notu = "Önerilmez (Faiz Baskısı)" if rr > 0.8 else "Güçlü Koruyucu"
            st.markdown(f"### 🚨 Kriz Yönetimi\n"
                        f"* **Döviz Faiz:** ({f_notu})\n"
                        f"* **Emtialar:** (Seçici Ol)\n"
                        f"* **Altın:** ({a_notu})")

        st.divider()

        # --- 4. KURUMSAL DETAYLAR ---
        c1, c2, c3 = st.columns(3)
        with c1:
            st.write("#### 🌐 Global Likidite (L2)")
            st.write(f"G3 Bilanço: **{latest['g3_liq']/1e6:.2f}T$**")
            st.write(f"Net Dolar Likiditesi: **{latest['ndl']/1e6:.2f}T$**")
            st.write(f"FIMA Repo (Foreign): **{latest['fima']/1e3:.1f}B$**")
            st.progress(min(max((val + 1) / 2, 0.0), 1.0))
        with c2:
            st.write("#### ⚡ High-Freq & Growth (L3)")
            st.write(f"Bakır/Altın Rasyosu: **{latest['copper_gold']:.4f}**")
            st.write(f"MOVE Endeksi (Bessent): **{latest['move']}**")
            st.write(f"PMI Büyüme: **{latest['pmi_z']}σ**")
        with c3:
            st.write("#### 🧠 Sentiment & Stres (L5)")
            st.write(f"Put/Call Oranı: **{latest['pc_ratio']}**")
            st.write(f"Piyasa Korkusu (VIX): **{latest['vix']}**")
            st.write(f"10Y Reel Faiz: **%{rr}**")

        st.subheader("📈 CMS Döngü Takibi")
        st.line_chart(df.set_index('date')['cms'].tail(30))
else:
    st.info("Veri bekleniyor... Lütfen GitHub Actions manuel tetikleyin.")
