import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

st.set_page_config(page_title="宏观投研决策系统", layout="wide", page_icon="⚖️")

if 'engine' not in st.session_state:
    st.session_state.engine = QuantEngine()
engine = st.session_state.engine

st.sidebar.title("⚖️ 宏观决策系统")
st.sidebar.info("Tiered Priority Model (v2.0)")

default_file = "holdings.csv"
csv_source = None

if os.path.exists(default_file):
    st.sidebar.success(f"已连接: {default_file}")
    csv_source = default_file
else:
    uploaded = st.sidebar.file_uploader("上传 CSV", type=['csv'])
    if uploaded: csv_source = uploaded

if not csv_source:
    st.info("👈 请上传持仓")
    st.stop()

engine.load_portfolio(csv_source)

@st.cache_data(ttl=3600, show_spinner=False)
def get_market_data_cached(_engine_trigger):
    return engine.fetch_data_automatically()

with st.spinner("正在初始化全维分析..."):
    status = engine.fetch_data_automatically()

# --- 顶栏：宏观分析 ---
with st.expander("🛡️ 纳斯达克全维战态感知 (Nasdaq Pro)", expanded=True):
    nasdaq_pro = engine.analyze_nasdaq_pro()
    
    if nasdaq_pro:
        state = nasdaq_pro['State']
        score = nasdaq_pro['Score']
        
        state_colors = {
            "Strong Bull": "#d4edda", "Healthy Uptrend": "#d1e7dd",
            "Overheated": "#fff3cd", "Shallow Pullback": "#cfe2ff",
            "Deep Pullback": "#ffe69c", "Repairing": "#e2e3e5",
            "Choppy": "#f8f9fa", "Bear Market": "#f8d7da", "Panic": "#f5c6cb"
        }
        bg = state_colors.get(state, "#f8f9fa")
        
        st.markdown(f"""
        <div style="background-color: {bg}; padding: 20px; border-radius: 12px; border-left: 8px solid #666;">
            <h2 style="margin:0; color: #333;">{state} <span style="font-size: 16px; color: #555;">(健康评分: {score}/100)</span></h2>
        </div>
        """, unsafe_allow_html=True)
        st.write("")

        c1, c2, c3, c4 = st.columns(4)
        m = nasdaq_pro['Metrics']
        with c1: st.metric("趋势强度 (ADX)", f"{m['ADX']:.1f}", help=">25强")
        with c2: st.metric("恐慌指数 (VXN)", f"{m['VXN']:.1f}")
        with c3: st.metric("市场宽度", nasdaq_pro['Breadth'])
        with c4: st.metric("中期风险", f"{nasdaq_pro['Risk_Med']}%")
        
        if nasdaq_pro['Signals']:
            st.markdown("---")
            for sig in nasdaq_pro['Signals']: st.write(sig)
    else:
        st.warning("宏观数据获取失败")

# --- 主界面：个股诊断 ---
st.subheader("🔍 深度诊断")

display_map = {row['Symbol']: row['YF_Ticker'] for idx, row in engine.portfolio.iterrows()}
selected_symbol = st.selectbox("选择资产:", list(display_map.keys()))
selected_ticker = display_map[selected_symbol]

if st.button("开始诊断"):
    with st.spinner(f"正在分析 {selected_symbol}..."):
        # 调用 Pro 方法
        result = engine.diagnose_stock_pro(selected_ticker)
        
        if result:
            st.divider()
            
            # 结果卡片
            state_id = result['ID']
            if state_id <= 5: theme = "#d1e7dd"
            elif state_id <= 10: theme = "#f8d7da" 
            else: theme = "#fff3cd"
            
            st.markdown(f"""
            <div style="background-color: {theme}; padding: 20px; border-radius: 10px; border-left: 10px solid #555;">
                <h4 style="margin:0; color: #555;">优先级: {result['Tier']}</h4>
                <h1 style="margin:0; color: #222;">{result['State']}</h1>
                <p style="font-size: 18px;"><b>{result['Reason']}</b></p>
            </div>
            """, unsafe_allow_html=True)
            st.write("")
            
            # 建议与图表
            c_left, c_right = st.columns([1, 2])
            
            with c_left:
                st.subheader("操作建议")
                action = result['Action']
                btn_type = "primary" if "卖" in action or "减" in action else "secondary"
                if "买" in action or "持有" in action: btn_type = "primary"
                st.button(action, type=btn_type, use_container_width=True)
                
            with c_right:
                df_chart = engine.get_chart_data(selected_ticker)
                if df_chart is not None:
                    fig = go.Figure()
                    fig.add_trace(go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name='K线'))
                    if 'SMA50' in df_chart.columns:
                        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA50'], line=dict(color='orange'), name='SMA 50'))
                    if 'BBU_20_2.0' in df_chart.columns:
                        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['BBU_20_2.0'], line=dict(color='gray', width=0.5, dash='dot'), name='Upper BB'))
                        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['BBL_20_2.0'], line=dict(color='gray', width=0.5, dash='dot'), name='Lower BB'))
                    fig.update_layout(title=f"{selected_symbol} 结构图", height=450, margin=dict(l=20, r=20, t=40, b=20), xaxis_rangeslider_visible=False)
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("数据不足或计算错误。")

# --- 批量 ---
st.markdown("---")
with st.expander("🚀 批量扫描"):
    if st.button("一键扫描所有"):
        res_list = []
        bar = st.progress(0)
        for i, row in engine.portfolio.iterrows():
            r = engine.diagnose_stock_pro(row['YF_Ticker'])
            if r:
                res_list.append({"代码": row['Symbol'], "状态": r['State'], "层级": r['Tier'], "建议": r['Action']})
            bar.progress((i+1)/len(engine.portfolio))
        st.dataframe(pd.DataFrame(res_list), use_container_width=True)
