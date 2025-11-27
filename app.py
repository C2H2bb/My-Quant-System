import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from quant_engine import QuantEngine

# 页面配置
st.set_page_config(page_title="Quant System", layout="wide")
st.title("📈 个人量化交易看板")

# 初始化引擎
if 'engine' not in st.session_state:
    st.session_state.engine = QuantEngine()

engine = st.session_state.engine

# --- 侧边栏：数据与配置 ---
st.sidebar.header("1. 数据导入")
uploaded_file = st.sidebar.file_uploader("上传 Wealthsimple 导出的 CSV", type=["csv"])

# 如果上传了文件，加载它
if uploaded_file is not None:
    success, msg = engine.load_portfolio(uploaded_file)
    if success:
        st.sidebar.success(msg)
    else:
        st.sidebar.error(msg)
        st.stop()
else:
    st.info("👋 请在左侧上传 CSV 文件以开始。")
    st.stop()

# 只有加载了数据才显示后续选项
st.sidebar.header("2. 策略配置")
strategy = st.sidebar.selectbox("选择策略", ["SMA Cross", "RSI"])

params = {}
if strategy == "SMA Cross":
    params['short'] = st.sidebar.number_input("短期均线", 5, 50, 10)
    params['long'] = st.sidebar.number_input("长期均线", 20, 200, 50)
elif strategy == "RSI":
    params['length'] = st.sidebar.number_input("RSI 周期", 5, 30, 14)
    params['lower'] = st.sidebar.slider("超卖线 (Buy)", 10, 40, 30)
    params['upper'] = st.sidebar.slider("超买线 (Sell)", 60, 90, 70)

# --- 主界面 ---

# 1. 更新数据按钮
if st.button("🔄 下载最新行情数据"):
    with st.spinner("正在连接 Yahoo Finance..."):
        engine.fetch_market_data()
        st.session_state.data_downloaded = True
        st.success("数据更新完毕！")

if not getattr(st.session_state, 'data_downloaded', False):
    st.warning("请先点击上方按钮下载行情数据。")
    st.stop()

# 2. 投资组合概览
st.subheader("📊 投资组合信号")

# 遍历持仓，计算信号
summary_data = []
progress_bar = st.progress(0)
tickers = engine.portfolio['YF_Ticker'].unique()

for i, ticker in enumerate(tickers):
    # 调用引擎计算，而不是在前端计算
    df_res = engine.apply_strategy(ticker, strategy, params)
    signal_txt = engine.get_latest_signal_text(df_res, strategy, params)
    
    # 获取当前价格
    price = 0.0
    if df_res is not None and not df_res.empty:
        price = df_res['Close'].iloc[-1]

    # 获取持仓成本信息
    holding = engine.portfolio[engine.portfolio['YF_Ticker'] == ticker].iloc[0]
    
    summary_data.append({
        "Ticker": ticker,
        "Name": holding['Name'],
        "Price": f"${price:.2f}",
        "Signal": signal_txt
    })
    progress_bar.progress((i + 1) / len(tickers))

summary_df = pd.DataFrame(summary_data)

def highlight_signal(val):
    color = ''
    if 'BUY' in val: color = 'background-color: #d4edda; color: green'
    elif 'SELL' in val: color = 'background-color: #f8d7da; color: red'
    elif 'HOLD' in val: color = 'color: orange'
    return color

st.dataframe(summary_df.style.map(highlight_signal, subset=['Signal']), use_container_width=True)

# 3. 深度图表
st.divider()
st.subheader("📈 个股详细分析")
selected_ticker = st.selectbox("选择股票查看详情", tickers)

if selected_ticker:
    # 从引擎获取计算好指标的数据
    df_chart = engine.apply_strategy(selected_ticker, strategy, params)
    
    if df_chart is not None and not df_chart.empty:
        fig = go.Figure()
        
        # K线
        fig.add_trace(go.Candlestick(
            x=df_chart.index,
            open=df_chart['Open'], high=df_chart['High'],
            low=df_chart['Low'], close=df_chart['Close'],
            name='Price'
        ))
        
        # 绘制指标线
        if strategy == "SMA Cross":
            # 注意：这里不再调用 ta.sma，而是直接取引擎返回的列
            if 'SMA_S' in df_chart.columns:
                fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_S'], line=dict(color='orange'), name='Short MA'))
            if 'SMA_L' in df_chart.columns:
                fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_L'], line=dict(color='blue'), name='Long MA'))
        
        fig.update_layout(title=f"{selected_ticker} - {strategy}", height=600)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.error("暂无该股票数据")

# 4. 推送按钮
st.divider()
if st.button("🚀 扫描并发送 Telegram 提醒"):
    count = 0
    with st.status("正在扫描..."):
        for ticker in tickers:
            triggered = engine.check_and_alert(ticker, strategy, params)
            if triggered:
                st.write(f"已推送: {ticker}")
                count += 1
    if count > 0:
        st.success(f"完成！共推送 {count} 条信号。")
    else:
        st.info("未发现强买卖信号，无推送。")
```

### 修复说明
1.  **移除了 `app.py` 中错误的 `ta.sma` 调用**：之前你的 `app.py` 试图自己在前端算指标，但没引入库。现在改为调用 `engine.apply_strategy(...)`，引擎会返回计算好 `SMA_S`, `SMA_L` 列的 DataFrame。
2.  **`app.py` 不再依赖本地 CSV 路径**：使用了 `st.file_uploader`。这样你在公司电脑打开网页时，直接把 CSV 拖进去就行，不需要改代码里的文件路径。
3.  **Telegram 修复**：在 `quant_engine.py` 里，我加了一个 fallback 机制。它会先试着读 Secrets，读不到就用你给的那个硬编码 Token。这样无论怎么配都能跑。
4.  **数据保护**：增加对 `nan-usd` 的过滤，这通常是 CSV 空行导致的，之前会卡住 yfinance。

### 如何运行
1.  更新这两个文件。
2.  Streamlit 网页会自动检测到文件变化并提示 Rerun（或者你手动刷新）。
3.  在侧边栏上传你的 CSV 文件即可开始。
