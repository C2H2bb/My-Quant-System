import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

# --- 1. 页面设置 ---
st.set_page_config(page_title="自动量化系统", layout="wide", page_icon="📈")

# --- 2. 核心引擎初始化 (修复 AttributeError 的关键) ---
# 我们不再依赖 session_state 存储整个 engine 对象，防止代码更新后对象过期
# 每次运行都重新实例化轻量级 Engine，数据通过 @st.cache_data 缓存
engine = QuantEngine()

# --- 3. 自动加载数据逻辑 ---
st.sidebar.header("📂 数据源")

# A. 优先查找本地 holdings.csv
default_file = "holdings.csv"
csv_source = None

if os.path.exists(default_file):
    st.sidebar.success(f"已自动识别本地文件: {default_file}")
    csv_source = default_file
else:
    # B. 没找到则显示上传框
    st.sidebar.warning("未找到 holdings.csv，请上传：")
    uploaded = st.sidebar.file_uploader("上传 CSV", type=['csv'])
    if uploaded:
        csv_source = uploaded

# 如果没有数据源，停止运行
if not csv_source:
    st.info("👈 请在左侧上传持仓文件，或将 holdings.csv 放入项目根目录。")
    st.stop()

# 加载持仓
success, msg = engine.load_portfolio(csv_source)
if not success:
    st.error(msg)
    st.stop()

# --- 4. 自动获取行情 (带缓存) ---
# 使用 Streamlit 缓存装饰器，避免每次点击其他按钮都重新下载数据
@st.cache_data(ttl=3600) # 数据缓存 1 小时
def get_market_data_cached(_engine):
    return _engine.fetch_data_automatically()

with st.spinner("正在自动同步全球行情数据..."):
    status_msg = get_market_data_cached(engine)
    # 注意：缓存后 engine 内部的 market_data 可能会丢失，因为 engine 是重新实例化的
    # 所以我们需要稍微 hack 一下，或者让 fetch 直接返回 data 字典
    # 简便起见，这里我们再次调用一次 fetch (yfinance 本身有缓存，很快)
    engine.fetch_data_automatically()

# --- 5. 侧边栏：策略控制 ---
st.sidebar.divider()
st.sidebar.header("🧠 策略中心")
strategy = st.sidebar.selectbox("核心策略", ["SMA Cross", "RSI", "Bollinger"])

params = {}
if strategy == "SMA Cross":
    col1, col2 = st.sidebar.columns(2)
    params['short'] = col1.number_input("短周期", 5, 60, 10)
    params['long'] = col2.number_input("长周期", 20, 200, 50)
elif strategy == "RSI":
    params['length'] = st.sidebar.number_input("RSI 周期", 5, 30, 14)

# --- 6. 主界面：信号仪表盘 ---
st.title("🚀 个人量化指挥台")

# 计算所有信号
signal_data = []
valid_tickers = [t for t in engine.portfolio['YF_Ticker'].unique() if t in engine.market_data]

if not valid_tickers:
    st.warning("暂无有效行情数据，请检查网络或股票代码。")
    st.stop()

# 进度条体验优化
progress = st.progress(0)

for i, ticker in enumerate(valid_tickers):
    # 计算策略
    df_res = engine.calculate_strategy(ticker, strategy, params)
    signal_status = engine.get_signal_status(df_res, strategy)
    
    # 获取当前价格
    price = df_res['Close'].iloc[-1] if df_res is not None else 0
    
    # 找到对应的原始名称
    original_name = engine.portfolio[engine.portfolio['YF_Ticker'] == ticker].iloc[0]['Symbol']
    
    signal_data.append({
        "代码": original_name,
        "当前价格": f"${price:.2f}",
        "策略信号": signal_status,
        "执行策略": strategy
    })
    progress.progress((i + 1) / len(valid_tickers))

progress.empty() # 清除进度条

# 展示表格
res_df = pd.DataFrame(signal_data)

def color_coding(val):
    if "BUY" in val: return 'background-color: #d1e7dd; color: #0f5132' # Green
    if "SELL" in val: return 'background-color: #f8d7da; color: #842029' # Red
    return ''

st.dataframe(res_df.style.map(color_coding, subset=['策略信号']), use_container_width=True)

# --- 7. 可视化详情 ---
st.divider()
col_chart, col_info = st.columns([3, 1])

with col_info:
    st.subheader("🔍 深度透视")
    selected_symbol = st.radio("选择股票", valid_tickers)

with col_chart:
    if selected_symbol:
        df_chart = engine.calculate_strategy(selected_symbol, strategy, params)
        
        if df_chart is not None:
            fig = go.Figure()
            # 蜡烛图
            fig.add_trace(go.Candlestick(
                x=df_chart.index,
                open=df_chart['Open'], high=df_chart['High'],
                low=df_chart['Low'], close=df_chart['Close'],
                name='K线'
            ))
            
            # 策略线绘制
            if strategy == "SMA Cross":
                if 'SMA_S' in df_chart.columns:
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_S'], line=dict(color='orange', width=1.5), name='快线'))
                if 'SMA_L' in df_chart.columns:
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_L'], line=dict(color='blue', width=1.5), name='慢线'))
            
            # 买卖点标记
            buys = df_chart[df_chart['Signal'] == 1]
            sells = df_chart[df_chart['Signal'] == -1]
            
            fig.add_trace(go.Scatter(
                x=buys.index, y=buys['Close'], 
                mode='markers', marker=dict(symbol='triangle-up', size=12, color='green'), name='买入'
            ))
            fig.add_trace(go.Scatter(
                x=sells.index, y=sells['Close'], 
                mode='markers', marker=dict(symbol='triangle-down', size=12, color='red'), name='卖出'
            ))

            fig.update_layout(height=500, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig, use_container_width=True)

# --- 8. 手机推送 ---
st.divider()
if st.button("📡 立即推送信号到手机"):
    count = 0
    for item in signal_data:
        if "BUY" in item['策略信号'] or "SELL" in item['策略信号']:
            msg = f"🚨 **{item['策略信号']} 提醒**\n股票: {item['代码']}\n价格: {item['当前价格']}"
            # 调用 engine 外部的辅助函数，防止类实例问题
            from quant_engine import send_telegram_message
            send_telegram_message(msg)
            count += 1
    
    if count > 0:
        st.success(f"已发送 {count} 条重要信号！")
    else:
        st.info("当前无买卖信号，无需推送。")
