import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

# --- 页面配置 ---
st.set_page_config(page_title="智能量化系统", layout="wide", page_icon="🧠")

# --- 初始化 ---
engine = QuantEngine()

# --- 侧边栏 ---
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

with st.spinner("正在分析全球市场数据..."):
    status = engine.fetch_data_automatically()

# --- ⚠️ 纳指生命周期雷达 (NEW) ---
with st.expander("📡 纳指全景监控雷达 (Nasdaq Market Cycle)", expanded=True):
    risk_data = engine.analyze_nasdaq_crash_risk()
    
    if risk_data:
        # 这里的 Key 必须与 quant_engine.py 返回的字典一致 ('Phase')
        phase = risk_data['Phase'] 
        bg_color = "#f0f2f6"
        if "上涨" in phase: bg_color = "#d1e7dd"
        elif "恐慌" in phase or "熊市" in phase: bg_color = "#f8d7da"
        elif "修复" in phase or "过热" in phase: bg_color = "#fff3cd"

        st.markdown(f"""
        <div style="background-color: {bg_color}; padding: 15px; border-radius: 10px; margin-bottom: 15px;">
            <h3 style="margin:0; color: #333;">{phase}</h3>
            <p style="margin:5px 0 0 0; color: #555;">{risk_data['Description']}</p>
        </div>
        """, unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        
        with c1:
            st.metric("纳指现价", f"${risk_data['Price']:.2f}", f"{risk_data['DD_ATH']:.2f}% (距高点)")
        with c2:
            st.metric("恐慌指数 (VXN)", f"{risk_data['VXN']:.2f}", help=">30 极度恐慌, <15 贪婪")
        with c3:
            st.metric("RSI (14)", f"{risk_data['RSI']:.1f}", help=">70 超买, <30 超卖")
        with c4:
            tnx_val = f"{risk_data['TNX']:.2f}%" if risk_data['TNX'] > 0 else "N/A"
            st.metric("10年美债收益率", tnx_val, help="收益率飙升通常利空科技股")
            
        st.caption(f"📊 长期均线乖离率: {risk_data['SMA200_Bias']:.1f}% (正值代表在年线上方，负值代表破位)")
    else:
        st.info("正在获取纳指数据，请稍候... (如果长时间未显示，请尝试清除缓存)")


# --- 默认参数 ---
default_params = {
    'SMA Cross': {'short': 10, 'long': 50},
    'SMA Reversal': {'short': 10, 'long': 50},
    'RSI': {'length': 14},
    'Bollinger': {'length': 20}
}

# --- 布局 ---
tab1, tab2, tab3 = st.tabs(["📊 投资组合全览", "🧠 动态智能分析 (AI)", "⚙️ 设置"])

# ==========================
# Tab 1: 投资组合全览
# ==========================
with tab1:
    valid_tickers = [t for t in engine.portfolio['YF_Ticker'].unique() if t in engine.market_data]
    global_strategy = st.sidebar.selectbox("默认备用策略", ["SMA Cross", "SMA Reversal", "RSI", "Bollinger"], index=0)
    
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
            "当前价格": f"${price:.2f}",
            "当前模型": active_strat,
            "信号": signal_status,
            "状态": health,
            "YF代码": ticker
        })
    
    df_dash = pd.DataFrame(dashboard_data)
    def style_dashboard(val):
        if "BUY" in str(val): return 'color: green; font-weight: bold'
        if "SELL" in str(val): return 'color: red; font-weight: bold'
        if "⚠️" in str(val): return 'color: orange; font-weight: bold'
        return ''

    st.dataframe(
        df_dash.style.map(style_dashboard),
        use_container_width=True,
        column_config={"YF代码": None}
    )
    
    if st.button("🚀 推送信号"):
        count = 0
        for idx, item in enumerate(dashboard_data):
            if "BUY" in item['信号'] or "SELL" in item['信号']:
                from quant_engine import send_telegram_message
                msg = f"🚨 *{item['信号']}*\nCode: `{item['代码']}`\nModel: {item['当前模型']}"
                send_telegram_message(msg)
                count += 1
        if count > 0: st.success(f"推送了 {count} 条信号")
        else: st.info("无信号")

# ==========================
# Tab 2: 动态智能分析
# ==========================
with tab2:
    col_sel, col_detail = st.columns([1, 3])
    
    with col_sel:
        st.subheader("个股诊断")
        selected_asset = st.radio("选择资产", [d['代码'] for d in dashboard_data])
        sel_yf = df_dash[df_dash['代码'] == selected_asset]['YF代码'].iloc[0]
        
    with col_detail:
        if sel_yf:
            regime = engine.analyze_market_regime(sel_yf)
            
            if regime:
                st.markdown(f"### 📊 {selected_asset} 市场体检报告")
                
                c1, c2, c3 = st.columns(3)
                c1.metric("近1月状态", regime['1M']['Desc'], f"{regime['1M']['Val']*100:.1f}%")
                c2.metric("近半年状态", regime['6M']['Desc'], f"{regime['6M']['Val']*100:.1f}%")
                c3.metric("近1年状态",  regime['1Y']['Desc'], f"{regime['1Y']['Val']*100:.1f}%")
                
                st.info(f"💡 **AI 综合建议**：当前市场波动率 {regime['Volatility']:.1f}%，ADX {regime['ADX']:.1f}。推荐使用 **{regime['Recommendation']}** 模型。")

                st.divider()
                st.markdown("#### 🛠️ 策略沙盒")
                
                current_fixed = engine.get_active_strategy(sel_yf, "无 (跟随默认)")
                
                col_setting, col_btn = st.columns([2, 1])
                with col_setting:
                    try:
                        idx = ["SMA Cross", "SMA Reversal", "RSI", "Bollinger"].index(regime['Recommendation'])
                    except: idx = 0
                    preview_strat = st.selectbox("预览模型效果", ["SMA Cross", "SMA Reversal", "RSI", "Bollinger"], index=idx)
                
                with col_btn:
                    st.write("")
                    st.write("")
                    if st.button(f"🔒 锁定为: {preview_strat}"):
                        engine.save_strategy_config(sel_yf, preview_strat)
                        st.toast(f"已锁定 {selected_asset} 为 {preview_strat}", icon="✅")
                        st.rerun()

                if current_fixed in ["SMA Cross", "SMA Reversal", "RSI", "Bollinger"]:
                    st.success(f"当前已锁定策略: **{current_fixed}**")
                else:
                    st.caption("当前使用全局默认策略")

                df_chart = engine.calculate_strategy(sel_yf, preview_strat, default_params.get(preview_strat, {}))
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name='K线'))
                
                if "SMA" in preview_strat:
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_S'], line=dict(color='orange'), name='Short'))
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_L'], line=dict(color='blue'), name='Long'))
                
                buys = df_chart[df_chart['Signal'] == 1]
                sells = df_chart[df_chart['Signal'] == -1]
                fig.add_trace(go.Scatter(x=buys.index, y=buys['Close'], mode='markers', marker=dict(symbol='triangle-up', size=12, color='green'), name='Buy'))
                fig.add_trace(go.Scatter(x=sells.index, y=sells['Close'], mode='markers', marker=dict(symbol='triangle-down', size=12, color='red'), name='Sell'))
                
                fig.update_layout(height=500, margin=dict(l=20, r=20, t=20, b=20))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("数据不足，无法分析。")

# ==========================
# Tab 3: 设置
# ==========================
with tab3:
    st.write("系统工具")
    if st.button("🧹 清除缓存"):
        st.cache_data.clear()
        st.success("已清除")
