import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

st.set_page_config(page_title="智能量化系统 Pro", layout="wide", page_icon="🧠")

engine = QuantEngine()

st.sidebar.header("📂 数据中心")
default_file = "holdings.csv"
csv_source = None

if os.path.exists(default_file):
    st.sidebar.success(f"本地数据: {default_file}")
    csv_source = default_file
else:
    uploaded = st.sidebar.file_uploader("上传 CSV", type=['csv'])
    if uploaded: csv_source = uploaded

if not csv_source:
    st.info("👈 请上传数据")
    st.stop()

engine.load_portfolio(csv_source)

@st.cache_data(ttl=3600, show_spinner=False)
def get_market_data_cached(_engine_trigger):
    return engine.fetch_data_automatically()

with st.spinner("正在进行全维市场扫描..."):
    status = engine.fetch_data_automatically()

# ==========================================
# 🛡️ 纳指专业级市场状态分析 (Pro Dashboard)
# ==========================================
with st.expander("🛡️ 纳斯达克全维战态感知 (Nasdaq Pro Analysis)", expanded=True):
    nasdaq_pro = engine.analyze_nasdaq_pro()
    
    if nasdaq_pro:
        # 1. 状态标头
        state = nasdaq_pro['State']
        score = nasdaq_pro['Score']
        
        # 配色逻辑
        state_colors = {
            "Strong Bull": "#d4edda", "Healthy Uptrend": "#d1e7dd",
            "Overheated": "#fff3cd", "Shallow Pullback": "#cfe2ff",
            "Deep Pullback": "#ffe69c", "Repairing": "#e2e3e5",
            "Choppy": "#f8f9fa", "Bear Market": "#f8d7da",
            "Panic": "#f5c6cb"
        }
        bg = state_colors.get(state, "#f8f9fa")
        
        st.markdown(f"""
        <div style="background-color: {bg}; padding: 20px; border-radius: 12px; border-left: 8px solid #666;">
            <h2 style="margin:0; color: #333;">{state} <span style="font-size: 16px; color: #555;">(健康评分: {score}/100)</span></h2>
        </div>
        """, unsafe_allow_html=True)
        
        st.write("") # Spacer

        # 2. 核心四维数据
        c1, c2, c3, c4 = st.columns(4)
        m = nasdaq_pro['Metrics']
        
        with c1:
            st.caption("📈 趋势 (Trend)")
            st.metric("方向 / 强度", f"{nasdaq_pro['Trend_Dir']} / {nasdaq_pro['Trend_Str']}")
            st.metric("ADX 强度", f"{m['ADX']:.1f}", help=">25 为强趋势")
        
        with c2:
            st.caption("🌊 波动 (Risk)")
            st.metric("波动率状态", nasdaq_pro['Volatility'])
            st.metric("恐慌指数 VXN", f"{m['VXN']:.1f}", delta=None, help="纳指波动率")
            
        with c3:
            st.caption("🏗️ 结构 (Health)")
            st.metric("市场宽度", nasdaq_pro['Breadth'], help="对比等权指数与加权指数")
            st.metric("资金流 RSI", f"{m['RSI']:.1f}")
            
        with c4:
            st.caption("⚠️ 风险预测 (Prob)")
            st.metric("短期回撤概率", f"{nasdaq_pro['Risk_Short']}%", help="1-5天风险")
            st.metric("中期崩盘概率", f"{nasdaq_pro['Risk_Med']}%", help="1-4周风险")
            
        # 3. 关键信号汇总
        if nasdaq_pro['Signals']:
            st.markdown("---")
            st.caption("📢 **关键情报 (Key Signals)**")
            for sig in nasdaq_pro['Signals']:
                st.write(sig)
                
    else:
        st.warning("无法获取纳指全维数据，请检查网络或清除缓存重试。")

# --- 默认参数 ---
default_params = {
    'SMA Cross': {'short': 10, 'long': 50},
    'SMA Reversal': {'short': 10, 'long': 50},
    'RSI': {'length': 14},
    'Bollinger': {'length': 20}
}

# --- 布局 ---
tab1, tab2, tab3 = st.tabs(["📊 投资组合", "🧠 个股诊断", "⚙️ 设置"])

# Tab 1: 投资组合 (保持简洁)
with tab1:
    valid_tickers = [t for t in engine.portfolio['YF_Ticker'].unique() if t in engine.market_data]
    global_strategy = st.sidebar.selectbox("备用策略", ["SMA Cross", "SMA Reversal", "RSI", "Bollinger"], index=0)
    
    dashboard_data = []
    for ticker in valid_tickers:
        active_strat = engine.get_active_strategy(ticker, global_strategy)
        df_res = engine.calculate_strategy(ticker, active_strat, default_params.get(active_strat, {}))
        signal_status = engine.get_signal_status(df_res)
        price = df_res['Close'].iloc[-1] if df_res is not None else 0
        
        regime = engine.analyze_market_regime(ticker)
        health = "✅"
        if regime and regime['Recommendation'] != active_strat:
             if "SMA" in active_strat and "SMA" in regime['Recommendation'] and active_strat != regime['Recommendation']:
                 health = f"⚠️ 建议: {regime['Recommendation']}"
             elif "Bollinger" in regime['Recommendation'] and "SMA" in active_strat:
                 health = "⚠️ 建议: Bollinger"

        row_info = engine.portfolio[engine.portfolio['YF_Ticker'] == ticker].iloc[0]
        dashboard_data.append({
            "代码": row_info['Symbol'],
            "价格": f"${price:.2f}",
            "模型": active_strat,
            "信号": signal_status,
            "健康度": health,
            "YF": ticker
        })
    
    df_dash = pd.DataFrame(dashboard_data)
    def style_dashboard(val):
        if "BUY" in str(val): return 'color: green; font-weight: bold'
        if "SELL" in str(val): return 'color: red; font-weight: bold'
        if "⚠️" in str(val): return 'color: orange; font-weight: bold'
        return ''

    st.dataframe(df_dash.style.map(style_dashboard), use_container_width=True, column_config={"YF": None})
    
    if st.button("🚀 推送信号"):
        count = 0
        for idx, item in enumerate(dashboard_data):
            if "BUY" in item['信号'] or "SELL" in item['信号']:
                from quant_engine import send_telegram_message
                send_telegram_message(f"🚨 *{item['信号']}*\n{item['代码']}")
                count += 1
        if count > 0: st.success(f"已推 {count} 条")
        else: st.info("无信号")

# Tab 2: 个股诊断
with tab2:
    c_sel, c_det = st.columns([1, 3])
    with c_sel:
        sel_asset = st.radio("资产", [d['代码'] for d in dashboard_data])
        sel_yf = df_dash[df_dash['代码'] == sel_asset]['YF'].iloc[0]
    with c_det:
        if sel_yf:
            reg = engine.analyze_market_regime(sel_yf)
            if reg:
                st.markdown(f"### {sel_asset} 分析")
                c1, c2, c3 = st.columns(3)
                c1.metric("1月", reg['1M']['Desc'], f"{reg['1M']['Val']*100:.1f}%")
                c2.metric("半年", reg['6M']['Desc'], f"{reg['6M']['Val']*100:.1f}%")
                c3.metric("1年", reg['1Y']['Desc'], f"{reg['1Y']['Val']*100:.1f}%")
                st.info(f"AI 建议: **{reg['Recommendation']}** (ADX: {reg['ADX']:.1f})")
                
                st.divider()
                col_s, col_b = st.columns([2,1])
                with col_s:
                    try: idx = ["SMA Cross", "SMA Reversal", "RSI", "Bollinger"].index(reg['Recommendation'])
                    except: idx = 0
                    p_strat = st.selectbox("模型预览", ["SMA Cross", "SMA Reversal", "RSI", "Bollinger"], index=idx)
                with col_b:
                    st.write("")
                    st.write("")
                    if st.button(f"🔒 锁定 {p_strat}"):
                        engine.save_strategy_config(sel_yf, p_strat)
                        st.experimental_rerun()

                df_c = engine.calculate_strategy(sel_yf, p_strat, default_params.get(p_strat, {}))
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=df_c.index, open=df_c['Open'], high=df_c['High'], low=df_c['Low'], close=df_c['Close'], name='K'))
                if "SMA" in p_strat:
                    fig.add_trace(go.Scatter(x=df_c.index, y=df_c['SMA_S'], line=dict(color='orange'), name='S'))
                    fig.add_trace(go.Scatter(x=df_c.index, y=df_c['SMA_L'], line=dict(color='blue'), name='L'))
                
                bs = df_c[df_c['Signal']==1]; ss = df_c[df_c['Signal']==-1]
                fig.add_trace(go.Scatter(x=bs.index, y=bs['Close'], mode='markers', marker=dict(symbol='triangle-up', size=10, color='green'), name='B'))
                fig.add_trace(go.Scatter(x=ss.index, y=ss['Close'], mode='markers', marker=dict(symbol='triangle-down', size=10, color='red'), name='S'))
                fig.update_layout(height=400, margin=dict(l=10,r=10,t=10,b=10))
                st.plotly_chart(fig, use_container_width=True)

# Tab 3
with tab3:
    if st.button("🧹 清除缓存"):
        st.cache_data.clear()
        st.success("OK")
