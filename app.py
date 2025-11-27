import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

# --- 页面配置 ---
st.set_page_config(page_title="Open Quant System", layout="wide", page_icon="🚀")
st.title("🚀 开源半自动量化系统")

# 默认文件名
DEFAULT_FILE = "holdings-report-2025-11-27.csv"

# 初始化引擎
if 'engine' not in st.session_state:
    st.session_state.engine = QuantEngine()
    st.session_state.data_loaded = False

# --- 侧边栏 ---
with st.sidebar:
    st.header("1. 数据源")
    
    # 优先使用上传的文件，如果没有，尝试使用仓库里的默认文件
    uploaded_file = st.file_uploader("更新持仓 (CSV)", type=['csv'])
    
    # 加载逻辑
    if uploaded_file is not None:
        success, msg = st.session_state.engine.load_portfolio(uploaded_file)
        if success:
            st.session_state.data_loaded = True
            st.success("已加载上传的文件")
            
    elif not st.session_state.data_loaded and os.path.exists(DEFAULT_FILE):
        success, msg = st.session_state.engine.load_portfolio(DEFAULT_FILE)
        if success:
            st.session_state.data_loaded = True
            st.info(f"已自动加载默认文件: {DEFAULT_FILE}")
            
    # 如果数据加载了，自动拉取行情
    if st.session_state.data_loaded and not st.session_state.engine.market_data:
        with st.spinner('正在同步 Yahoo Finance 数据...'):
            st.session_state.engine.fetch_market_data()

    st.divider()
    
    st.header("2. 策略引擎")
    strategy = st.selectbox("选择策略模型", ["SMA Cross", "RSI"])
    
    params = {}
    if strategy == "SMA Cross":
        params['short'] = st.slider("短期均线", 5, 50, 10)
        params['long'] = st.slider("长期均线", 20, 200, 50)
    elif strategy == "RSI":
        params['length'] = st.number_input("RSI 周期", value=14)
        params['lower'] = st.number_input("超卖线", value=30)
        params['upper'] = st.number_input("超买线", value=70)

# --- 主界面 ---

if not st.session_state.data_loaded:
    st.warning("⚠️ 尚未加载数据。请上传 CSV 或确保仓库中有默认文件。")
    st.stop()

# 1. 执行策略计算
df_res = st.session_state.engine.calculate_signals(strategy, params)

if df_res.empty:
    st.error("数据计算失败，请检查 CSV 格式。")
    st.stop()

# 2. 构建展示表格
display_list = []
for idx, row in df_res.iterrows():
    curr_price = st.session_state.engine.get_last_price(row['YF_Ticker'])
    pnl = (curr_price - row['AvgCost']) / row['AvgCost'] * 100 if row['AvgCost'] > 0 else 0
    
    display_list.append({
        "Symbol": row['Symbol'],
        "Name": row['Name'],
        "Price": curr_price,
        "Cost": row['AvgCost'],
        "PnL %": pnl,
        "Signal": row['Signal']
    })

df_display = pd.DataFrame(display_list)

# 3. 样式化显示
st.subheader(f"📊 策略分析: {strategy}")

def style_df(val):
    color = ''
    if isinstance(val, str):
        if 'BUY' in val: color = 'background-color: #d4edda; color: green; font-weight: bold'
        elif 'SELL' in val: color = 'background-color: #f8d7da; color: red; font-weight: bold'
    elif isinstance(val, (int, float)):
        # PnL logic if value is float and looks like PnL
        pass 
    return color

# 简化的样式应用
st.dataframe(
    df_display.style.applymap(style_df, subset=['Signal'])
    .format({"Price": "{:.2f}", "Cost": "{:.2f}", "PnL %": "{:.2f}%"}),
    use_container_width=True,
    height=500
)

# 4. 可视化图表
st.divider()
col1, col2 = st.columns([1, 3])

with col1:
    st.markdown("### 🔍 深度查看")
    selected_symbol = st.selectbox("选择股票", df_display['Symbol'].unique())

with col2:
    row_data = st.session_state.engine.portfolio[st.session_state.engine.portfolio['Symbol'] == selected_symbol].iloc[0]
    yf_ticker = row_data['YF_Ticker']
    
    if yf_ticker in st.session_state.engine.market_data:
        df_chart = st.session_state.engine.market_data[yf_ticker]
        
        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df_chart.index,
            open=df_chart['Open'], high=df_chart['High'],
            low=df_chart['Low'], close=df_chart['Close'],
            name='Price'
        ))
        
        # 添加策略辅助线
        if strategy == "SMA Cross":
            sma_s = ta.sma(df_chart['Close'], length=params['short'])
            sma_l = ta.sma(df_chart['Close'], length=params['long'])
            fig.add_trace(go.Scatter(x=df_chart.index, y=sma_s, line=dict(color='orange', width=1), name='Short MA'))
            fig.add_trace(go.Scatter(x=df_chart.index, y=sma_l, line=dict(color='blue', width=1), name='Long MA'))
            
        fig.update_layout(title=f"{selected_symbol} ({yf_ticker})", margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning(f"无法获取 {yf_ticker} 的图表数据")
