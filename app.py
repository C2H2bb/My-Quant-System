import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

st.set_page_config(page_title="宏观投研决策系统", layout="wide", page_icon="⚖️")

if 'engine' not in st.session_state:
    st.session_state.engine = QuantEngine()
engine = st.session_state.engine

# --- 侧边栏 ---
st.sidebar.title("⚖️ 宏观决策系统")
st.sidebar.info("Tiered Priority Model (Pro)")

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

# 修复：不再调用 fetch_data_automatically，而是 fetch_macro_context
if 'macro_done' not in st.session_state:
    with st.spinner("正在初始化宏观数据 (QQQ/VIX)..."):
        engine.fetch_macro_context()
        st.session_state.macro_done = True

macro = engine.macro_cache
if not macro:
    st.error("宏观数据获取失败，请检查网络。")
    st.stop()

# --- 顶栏：宏观摘要 ---
with st.expander("🌍 市场环境 (Macro Context)", expanded=True):
    c1, c2, c3, c4 = st.columns(4)
    
    # 纳指趋势
    trend_icon = "🟢" if macro['Market_Trend'] == "Bull" else "🔴"
    c1.metric("纳指趋势", f"{trend_icon} {macro['Market_Trend']}")
    
    # 恐慌指数
    vxn_val = macro['VXN']
    vxn_color = "normal"
    if vxn_val > 28: vxn_color = "inverse"
    c2.metric("恐慌指数 (VXN)", f"{vxn_val:.2f}", help=">28 高危")
    
    # 美债
    c3.metric("10年美债 (TNX)", f"{macro['TNX']:.2f}%")
    
    # QQQ 动能
    c4.metric("QQQ 20日涨幅", f"{macro.get('QQQ_Ret_20', 0)*100:.1f}%")

# --- 个股诊断 ---
st.subheader("🔍 深度诊断")

display_map = {row['Symbol']: row['YF_Ticker'] for idx, row in engine.portfolio.iterrows()}
selected_symbol = st.selectbox("选择资产:", list(display_map.keys()))
selected_ticker = display_map[selected_symbol]

if st.button("开始诊断"):
    with st.spinner(f"正在分析 {selected_symbol} (4层权重模型)..."):
        # 调用 Pro 方法
        result = engine.diagnose_stock_pro(selected_ticker)
        
        if result:
            st.divider()
            
            # 1. 结果卡片
            state_id = result['ID']
            if state_id <= 5: theme = "#d1e7dd" # Green
            elif state_id <= 10: theme = "#f8d7da" # Red
            else: theme = "#fff3cd" # Yellow
            
            st.markdown(f"""
            <div style="background-color: {theme}; padding: 20px; border-radius: 10px; border-left: 10px solid #555;">
                <h4 style="margin:0; color: #555;">优先级: {result['Tier']}</h4>
                <h1 style="margin:0; color: #222;">{result['State']}</h1>
                <p style="font-size: 18px;"><b>{result['Reason']}</b></p>
            </div>
            """, unsafe_allow_html=True)
            
            st.write("")
            
            # 2. 建议与图表
            c_left, c_right = st.columns([1, 2])
            
            with c_left:
                st.subheader("操作建议")
                action = result['Action']
                btn_type = "secondary"
                if "买" in action or "持有" in action: btn_type = "primary"
                if "卖" in action or "减" in action: btn_type = "primary"
                
                st.button(action, type=btn_type, use_container_width=True)
                
                st.info("""
                **参考指标说明：**
                * **RS (相对强弱)**: 对比 QQQ 涨幅
                * **乖离率**: 偏离 SMA50 的程度
                * **Tier 1**: 黑天鹅/趋势反转 (最高权)
                """)

            with c_right:
                df_chart = engine.get_chart_data(selected_ticker)
                if df_chart is not None:
                    fig = go.Figure()
                    # K线
                    fig.add_trace(go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name='K线'))
                    
                    if 'SMA50' in df_chart.columns:
                        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA50'], line=dict(color='orange'), name='SMA 50'))
                    if 'SMA200' in df_chart.columns:
                        fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA200'], line=dict(color='blue', width=2), name='SMA 200'))
                        
                    fig.update_layout(title=f"{selected_symbol} 结构图", height=450, margin=dict(l=20, r=20, t=40, b=20), xaxis_rangeslider_visible=False)
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("数据不足或计算错误。")

# --- 批量扫描 ---
st.markdown("---")
with st.expander("🚀 批量扫描 (Batch Scan)"):
    if st.button("扫描所有持仓"):
        res_list = []
        bar = st.progress(0)
        for i, row in engine.portfolio.iterrows():
            r = engine.diagnose_stock_pro(row['YF_Ticker'])
            if r:
                res_list.append({"代码": row['Symbol'], "状态": r['State'], "层级": r['Tier'], "建议": r['Action']})
            bar.progress((i+1)/len(engine.portfolio))
        st.dataframe(pd.DataFrame(res_list), use_container_width=True)
