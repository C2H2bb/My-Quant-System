import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from quant_engine import QuantEngine

# --- 页面配置 ---
st.set_page_config(page_title="智能量化系统", layout="wide", page_icon="🧠")

# --- 初始化 ---
engine = QuantEngine()

# --- 侧边栏：数据加载 ---
st.sidebar.header("📂 数据中心")
default_file = "holdings.csv"
csv_source = None

if os.path.exists(default_file):
    st.sidebar.success(f"已加载本地数据: {default_file}")
    csv_source = default_file
else:
    uploaded = st.sidebar.file_uploader("上传 Wealthsimple CSV", type=['csv'])
    if uploaded: csv_source = uploaded

if not csv_source:
    st.info("👈 请上传数据文件以开始")
    st.stop()

engine.load_portfolio(csv_source)

# 自动下载数据 (缓存)
@st.cache_data(ttl=3600, show_spinner=False)
def get_market_data_cached(_engine_trigger):
    return engine.fetch_data_automatically()

with st.spinner("正在分析全球市场数据..."):
    status = engine.fetch_data_automatically()

# --- 策略默认参数 ---
default_params = {
    'SMA Cross': {'short': 10, 'long': 50},
    'RSI': {'length': 14},
    'Bollinger': {'length': 20}
}

# --- 页面布局 ---
tab1, tab2, tab3 = st.tabs(["📊 投资组合全览", "🧠 动态智能分析 (AI)", "⚙️ 全局设置"])

# ==========================
# Tab 1: 投资组合全览
# ==========================
with tab1:
    st.header("投资组合信号监控")
    
    # 准备表格数据
    dashboard_data = []
    valid_tickers = [t for t in engine.portfolio['YF_Ticker'].unique() if t in engine.market_data]
    
    # 全局默认策略 (Fallback)
    global_strategy = st.sidebar.selectbox("默认备用策略", ["SMA Cross", "RSI", "Bollinger"], index=0)
    
    for ticker in valid_tickers:
        # 1. 确定该股票使用什么策略 (锁定的 > 全局默认)
        active_strat = engine.get_active_strategy(ticker, global_strategy)
        
        # 2. 计算信号
        df_res = engine.calculate_strategy(ticker, active_strat, default_params[active_strat])
        signal_status = engine.get_signal_status(df_res)
        price = df_res['Close'].iloc[-1] if df_res is not None else 0
        
        # 3. 智能诊断：检查策略是否适合当前行情
        regime_info = engine.analyze_market_regime(ticker)
        recommended_strat = regime_info['Recommendation'] if regime_info else active_strat
        
        # 判断是否失配
        health_check = "✅ 匹配"
        if active_strat != recommended_strat:
            health_check = f"⚠️ 建议: {recommended_strat}"
            
        row_info = engine.portfolio[engine.portfolio['YF_Ticker'] == ticker].iloc[0]
        
        dashboard_data.append({
            "代码": row_info['Symbol'],
            "当前价格": f"${price:.2f}",
            "当前模型": active_strat,
            "信号": signal_status,
            "模型健康度": health_check, # 新增列：提示是否适合
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
        column_config={
            "模型健康度": st.column_config.TextColumn("模型诊断", help="如果不匹配，说明当前市场走势可能不适合该策略"),
            "YF代码": None # 隐藏列
        }
    )
    
    if st.button("🚀 一键扫描并推送", type="primary"):
        count = 0
        progress_text = "正在扫描..."
        my_bar = st.progress(0, text=progress_text)
        
        for idx, item in enumerate(dashboard_data):
            if "BUY" in item['信号'] or "SELL" in item['信号']:
                from quant_engine import send_telegram_message
                msg = f"🚨 *{item['信号']}* ({item['当前模型']})\nCode: `{item['代码']}`\nPrice: {item['当前价格']}"
                send_telegram_message(msg)
                count += 1
            my_bar.progress((idx + 1) / len(dashboard_data))
            
        my_bar.empty()
        if count > 0: st.success(f"已推送 {count} 条重要信号")
        else: st.info("暂无交易信号")

# ==========================
# Tab 2: 动态智能分析
# ==========================
with tab2:
    col_sel, col_detail = st.columns([1, 3])
    
    with col_sel:
        st.subheader("个股诊断")
        selected_asset = st.radio("选择资产进行分析", [d['代码'] for d in dashboard_data])
        # 反查 YF Ticker
        sel_yf = df_dash[df_dash['代码'] == selected_asset]['YF代码'].iloc[0]
        
    with col_detail:
        if sel_yf:
            # 1. 运行市场体制分析
            regime = engine.analyze_market_regime(sel_yf)
            
            if regime:
                # 显示诊断卡片
                c1, c2, c3 = st.columns(3)
                c1.metric("趋势强度 (ADX)", f"{regime['ADX']:.1f}", help=">25 为强趋势")
                c2.metric("市场状态", regime['Regime'])
                c3.metric("AI 推荐模型", regime['Recommendation'])
                
                # 2. 策略选择与锁定
                st.markdown("#### 🛠️ 模型配置")
                
                current_fixed = engine.get_active_strategy(sel_yf, "无 (跟随默认)")
                
                col_setting, col_btn = st.columns([2, 1])
                with col_setting:
                    # 默认选中推荐的策略
                    try:
                        idx = ["SMA Cross", "RSI", "Bollinger"].index(regime['Recommendation'])
                    except:
                        idx = 0
                    preview_strat = st.selectbox("预览策略效果", ["SMA Cross", "RSI", "Bollinger"], index=idx)
                
                with col_btn:
                    st.write("") # Spacer
                    st.write("") 
                    if st.button(f"🔒 锁定模型: {preview_strat}"):
                        engine.save_strategy_config(sel_yf, preview_strat)
                        st.toast(f"已将 {selected_asset} 锁定为 {preview_strat} 模型！", icon="✅")
                        st.rerun()

                # 显示当前锁定状态
                if current_fixed in ["SMA Cross", "RSI", "Bollinger"]:
                    st.caption(f"当前该股票已锁定为: **{current_fixed}**")
                else:
                    st.caption("当前跟随全局默认策略")

                # 3. 图表可视化
                df_chart = engine.calculate_strategy(sel_yf, preview_strat, default_params[preview_strat])
                
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=df_chart.index, open=df_chart['Open'], high=df_chart['High'],
                    low=df_chart['Low'], close=df_chart['Close'], name='Price'
                ))
                
                # 根据预览的策略画线
                if preview_strat == "SMA Cross":
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_S'], line=dict(color='orange'), name='SMA Short'))
                    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['SMA_L'], line=dict(color='blue'), name='SMA Long'))
                elif preview_strat == "Bollinger":
                    # 简单画一下上轨下轨示意
                    pass # 可以根据需要添加布林带轨道，目前保持K线清爽
                
                # 标记买卖点
                buys = df_chart[df_chart['Signal'] == 1]
                sells = df_chart[df_chart['Signal'] == -1]
                fig.add_trace(go.Scatter(x=buys.index, y=buys['Close'], mode='markers', marker=dict(symbol='triangle-up', size=12, color='green'), name='Buy'))
                fig.add_trace(go.Scatter(x=sells.index, y=sells['Close'], mode='markers', marker=dict(symbol='triangle-down', size=12, color='red'), name='Sell'))
                
                fig.update_layout(title=f"{selected_asset} - {preview_strat} 模拟回测", height=500, margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig, use_container_width=True)
                
            else:
                st.warning("数据不足，无法分析该股票的市场体制。")

# ==========================
# Tab 3: 全局设置
# ==========================
with tab3:
    st.write("这里可以调整各策略的默认参数（影响所有未锁定参数的股票）。")
    # 这里可以放之前的参数输入框，为了界面整洁，暂时隐藏或按需添加
    if st.button("🧹 清除所有缓存 (调试用)"):
        st.cache_data.clear()
        st.success("已清除")
