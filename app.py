import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

st.set_page_config(page_title="宏观投研决策系统", layout="wide", page_icon="⚖️")

# 初始化
if 'engine' not in st.session_state:
    st.session_state.engine = QuantEngine()
engine = st.session_state.engine

# --- 侧边栏 ---
st.sidebar.title("⚖️ 宏观决策系统")
st.sidebar.info("基于分层权重模型 (Tiered Priority Model)")

default_file = "holdings.csv"
csv_source = None

if os.path.exists(default_file):
    st.sidebar.success(f"已连接数据: {default_file}")
    csv_source = default_file
else:
    uploaded = st.sidebar.file_uploader("上传持仓 CSV", type=['csv'])
    if uploaded: csv_source = uploaded

if not csv_source:
    st.info("👈 请先上传持仓文件")
    st.stop()

# 加载数据 & 宏观环境
engine.load_portfolio(csv_source)

if 'macro_done' not in st.session_state:
    with st.spinner("正在扫描全球宏观环境 (QQQ, VIX, TNX)..."):
        engine.fetch_macro_context()
        st.session_state.macro_done = True

macro = engine.macro_cache
if not macro:
    st.error("网络错误：无法连接行情服务器")
    st.stop()

# --- 顶栏：宏观罗盘 ---
with st.expander("🌍 全球宏观罗盘 (Macro Context)", expanded=True):
    c1, c2, c3 = st.columns(3)
    
    # 纳指趋势
    trend_icon = "🟢" if macro['Market_Trend'] == "Bull" else "🔴"
    c1.metric("纳斯达克趋势", f"{trend_icon} {macro['Market_Trend']}", "SMA50 判定")
    
    # 恐慌指数
    vxn_val = macro['VXN']
    vxn_color = "normal"
    if vxn_val > 28: vxn_color = "inverse" # 红
    c2.metric("恐慌指数 (VXN)", f"{vxn_val:.2f}", help=">28 为高风险区")
    
    # 利率压力
    tnx_val = macro['TNX']
    c3.metric("10年美债收益率", f"{tnx_val:.2f}%", "无风险利率基准")

# --- 主界面：个股诊断 ---
st.subheader("🔍 持仓深度诊断")

# 提取持仓列表
tickers = engine.portfolio['YF_Ticker'].unique()
symbols = engine.portfolio['Symbol'].unique()
display_map = {row['Symbol']: row['YF_Ticker'] for idx, row in engine.portfolio.iterrows()}

selected_symbol = st.selectbox("选择要诊断的资产:", list(display_map.keys()))
selected_ticker = display_map[selected_symbol]

if st.button("开始诊断"):
    with st.spinner(f"正在通过 4 层权重模型分析 {selected_symbol}..."):
        result = engine.diagnose_stock(selected_ticker)
        
        if result:
            # --- 结果展示区 ---
            st.divider()
            
            # 1. 状态大标题
            state_id = result['ID']
            # 颜色映射
            if state_id <= 5: theme_color = "#d1e7dd" # Green (正向)
            elif state_id <= 10: theme_color = "#f8d7da" # Red (负向)
            else: theme_color = "#fff3cd" # Yellow (中性)
            
            st.markdown(f"""
            <div style="background-color: {theme_color}; padding: 20px; border-radius: 10px; border-left: 10px solid #666; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                <h4 style="margin:0; color: #555;">当前状态 ({result['Tier']})</h4>
                <h1 style="margin:0; color: #333;">{result['State']}</h1>
                <p style="margin-top: 10px; font-size: 18px;"><b>诊断理由：</b>{result['Reason']}</p>
            </div>
            """, unsafe_allow_html=True)
            
            st.write("")
            
            # 2. 最终建议与图表
            col_advice, col_chart = st.columns([1, 2])
            
            with col_advice:
                st.markdown("### 📢 操作建议")
                action = result['Action']
                
                # 建议样式
                btn_type = "secondary"
                if "买" in action or "持有" in action: btn_type = "primary"
                if "卖" in action or "减仓" in action: btn_type = "primary" # 红色实际上要自定义，但在streamlit里用primary突出
                
                st.button(action, type=btn_type, use_container_width=True)
                
                st.markdown("""
                ---
                **权重层级说明：**
                * **Tier 1 (黑天鹅/事件)**：一票否决权
                * **Tier 2 (大盘/量能)**：决定主要方向
                * **Tier 3 (指标/形态)**：辅助判断
                * **Tier 4 (日内波动)**：仅供参考
                """)

            with col_chart:
                df_chart = engine.get_chart_data(selected_ticker)
                if df_chart is not None:
                    fig = go.Figure()
                    # K线
                    fig.add_trace(go.Candlestick(
                        x=df_chart.index, open=df_chart['Open'], high=df_chart['High'],
                        low=df_chart['Low'], close=df_chart['Close'], name='K线'
                    ))
                    # 均线系统
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA50'], line=dict(color='orange', width=1.5), name='SMA 50 (生命线)'))
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA200'], line=dict(color='blue', width=2), name='SMA 200 (牛熊线)'))
                    
                    fig.update_layout(
                        title=f"{selected_symbol} 趋势全景图",
                        height=450,
                        margin=dict(l=20, r=20, t=40, b=20),
                        xaxis_rangeslider_visible=False
                    )
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("数据不足，无法生成诊断报告。")

# --- 底部：批量扫描 ---
st.markdown("---")
with st.expander("🚀 批量扫描持仓风险 (Batch Scan)"):
    if st.button("扫描所有持仓"):
        report_data = []
        prog = st.progress(0)
        
        for i, row in engine.portfolio.iterrows():
            res = engine.diagnose_stock(row['YF_Ticker'])
            if res:
                report_data.append({
                    "代码": row['Symbol'],
                    "状态": res['State'],
                    "层级": res['Tier'],
                    "建议": res['Action']
                })
            prog.progress((i + 1) / len(engine.portfolio))
            
        st.dataframe(pd.DataFrame(report_data), use_container_width=True)
