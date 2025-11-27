import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

# --- 页面配置 ---
st.set_page_config(page_title="自动量化系统", layout="wide", page_icon="📈")

# --- 调试工具：清除缓存 ---
# 如果代码更新后还是报错，可以点击左侧底部的这个按钮
if st.sidebar.checkbox("显示调试工具", value=False):
    if st.sidebar.button("🧹 清除数据缓存"):
        st.cache_data.clear()
        st.success("缓存已清除，请刷新页面")
        st.stop()

# --- 初始化引擎 ---
engine = QuantEngine()

# --- 数据源加载 ---
st.sidebar.header("📂 数据源")
default_file = "holdings.csv"
csv_source = None

if os.path.exists(default_file):
    st.sidebar.success(f"本地文件: {default_file}")
    csv_source = default_file
else:
    uploaded = st.sidebar.file_uploader("上传 Wealthsimple CSV", type=['csv'])
    if uploaded:
        csv_source = uploaded

if not csv_source:
    st.info("👈 请上传 CSV 文件")
    st.stop()

success, msg = engine.load_portfolio(csv_source)
if not success:
    st.error(msg)
    st.stop()

# --- 自动获取行情 (带缓存) ---
@st.cache_data(ttl=3600, show_spinner=False)
def get_market_data_cached(_engine_trigger):
    # 这里只用作触发缓存，实际上操作的是 engine 实例
    return engine.fetch_data_automatically()

with st.spinner("正在同步行情..."):
    # 使用一个简单字符串作为缓存键，或者直接调用
    # 为了避免对象序列化问题，这里我们在每次重载页面时直接运行一次
    # yfinance 自身有缓存，所以不用太担心频繁请求
    status = engine.fetch_data_automatically()
    if "❌" in status:
        st.warning(status)
    else:
        st.toast(status)

# --- 策略配置 ---
st.sidebar.divider()
st.sidebar.header("🧠 策略中心")
strategy = st.sidebar.selectbox("选择策略", ["SMA Cross", "RSI", "Bollinger"])

params = {}
if strategy == "SMA Cross":
    c1, c2 = st.sidebar.columns(2)
    params['short'] = c1.number_input("短周期", 5, 60, 10)
    params['long'] = c2.number_input("长周期", 20, 200, 50)
elif strategy == "RSI":
    params['length'] = st.sidebar.number_input("RSI 周期", 5, 30, 14)

# --- 主界面 ---
st.title("🚀 个人量化指挥台")

# 处理数据
valid_tickers = [t for t in engine.portfolio['YF_Ticker'].unique() if t in engine.market_data]
if not valid_tickers:
    st.error("没有获取到任何有效行情数据。")
    st.stop()

signal_data = []
for ticker in valid_tickers:
    df_res = engine.calculate_strategy(ticker, strategy, params)
    signal_status = engine.get_signal_status(df_res, strategy)
    price = df_res['Close'].iloc[-1] if df_res is not None else 0
    
    # 获取原始信息
    row_info = engine.portfolio[engine.portfolio['YF_Ticker'] == ticker].iloc[0]
    original_name = row_info['Name']
    original_symbol = row_info['Symbol']
    
    signal_data.append({
        "代码": original_symbol,
        "名称": original_name,
        "Yahoo代码": ticker, # 显示实际查询的代码，方便调试
        "价格": f"${price:.2f}",
        "信号": signal_status
    })

df_display = pd.DataFrame(signal_data)

def color_coding(val):
    if "BUY" in val: return 'background-color: #d1e7dd; color: green; font-weight: bold'
    if "SELL" in val: return 'background-color: #f8d7da; color: red; font-weight: bold'
    return ''

st.dataframe(
    df_display.style.map(color_coding, subset=['信号']), 
    use_container_width=True,
    column_config={
        "代码": "Symbol",
        "名称": "Name",
        "Yahoo代码": st.column_config.TextColumn("YF Ticker", help="实际用于查询行情的代码"),
    }
)

# --- 图表 ---
st.divider()
c_chart, c_list = st.columns([3, 1])

with c_list:
    st.subheader("📊 走势图")
    # 让用户选原始 Symbol，显示更友好
    choice = st.radio("选择资产", df_display['代码'].tolist())
    # 反查对应的 Yahoo Ticker
    sel_yf = df_display[df_display['代码'] == choice]['Yahoo代码'].iloc[0]

with c_chart:
    if sel_yf:
        df_chart = engine.calculate_strategy(sel_yf, strategy, params)
        if df_chart is not None:
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=df_chart.index, open=df_chart['Open'], high=df_chart['High'],
                low=df_chart['Low'], close=df_chart['Close'], name='K线'
            ))
            
            if strategy == "SMA Cross":
                fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_S'], line=dict(color='orange'), name='快线'))
                fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_L'], line=dict(color='blue'), name='慢线'))
            
            # 买卖点
            buys = df_chart[df_chart['Signal'] == 1]
            sells = df_chart[df_chart['Signal'] == -1]
            fig.add_trace(go.Scatter(x=buys.index, y=buys['Close'], mode='markers', marker=dict(symbol='triangle-up', size=12, color='green'), name='买入'))
            fig.add_trace(go.Scatter(x=sells.index, y=sells['Close'], mode='markers', marker=dict(symbol='triangle-down', size=12, color='red'), name='卖出'))
            
            fig.update_layout(height=500, margin=dict(t=30, b=20, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)

# --- 推送 ---
st.divider()
if st.button("📡 推送信号到 Telegram"):
    count = 0
    for item in signal_data:
        if "BUY" in item['信号'] or "SELL" in item['信号']:
            from quant_engine import send_telegram_message
            msg = f"🚨 *{item['信号']}*\nSymbol: `{item['代码']}`\nPrice: {item['价格']}"
            send_telegram_message(msg)
            count += 1
    if count > 0: st.success(f"已推送 {count} 条信号")
    else: st.info("无信号推送")
