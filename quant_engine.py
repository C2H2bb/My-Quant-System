import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st
import time
import json
import os
import numpy as np

# Telegram 推送函数
def send_telegram_message(message):
    try:
        bot_token = st.secrets["BOT_TOKEN"]
        chat_id = st.secrets["CHAT_ID"]
        send_text = f'https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&parse_mode=Markdown&text={message}'
        requests.get(send_text, timeout=3) 
    except Exception:
        pass 

class QuantEngine:
    def __init__(self):
        self.portfolio = pd.DataFrame()
        self.market_data = {}
        self.config_file = "strategy_config.json"
        self.strategy_map = self.load_strategy_config()

    # --- 数据加载 ---
    def load_portfolio(self, file_path_or_buffer):
        try:
            df = pd.read_csv(file_path_or_buffer)
            df.columns = [c.strip() for c in df.columns]
            if 'Symbol' not in df.columns: return False, "CSV 缺少 'Symbol' 列"
            portfolio_list = []
            for index, row in df.iterrows():
                raw_symbol = row['Symbol']
                if pd.isna(raw_symbol): continue
                symbol = str(raw_symbol).strip()
                if not symbol or symbol.lower() == 'nan': continue
                name = str(row.get('Name', 'Unknown'))
                exchange = str(row.get('Exchange', ''))
                currency = str(row.get('Currency', ''))
                try: qty = float(row.get('Quantity', 0))
                except: qty = 0.0
                yf_ticker = self._map_symbol(symbol, exchange, name, currency)
                if 'nan' in yf_ticker.lower(): continue
                portfolio_list.append({"Symbol": symbol, "YF_Ticker": yf_ticker, "Quantity": qty, "Name": name})
            if not portfolio_list: return False, "未找到有效持仓"
            self.portfolio = pd.DataFrame(portfolio_list)
            return True, f"✅ 已加载 {len(self.portfolio)} 个持仓"
        except Exception as e: 
            return False, f"❌ 解析失败: {str(e)}"

    def _map_symbol(self, symbol, exchange, name, currency):
        symbol_upper = symbol.upper()
        name_upper = name.upper()
        if symbol_upper == 'GOLD' and 'BARRICK' not in name_upper: return 'GC=F'
        if '.' in symbol_upper and ('TO' in symbol_upper or 'NE' in symbol_upper): return symbol_upper
        is_cad = currency.upper() == 'CAD'
        if 'CDR' in name_upper or 'NEO' in exchange or 'CBOE' in exchange: return f"{symbol_upper.replace('.', '-')}.NE"
        if 'TSX' in exchange or 'TORONTO' in exchange.upper(): return f"{symbol_upper.replace('.', '-')}.TO"
        if is_cad and '.' not in symbol_upper: return f"{symbol_upper}.TO"
        crypto_list = ['BTC', 'ETH', 'SOL', 'DOGE', 'ADA', 'DOT']
        if (not exchange or exchange.lower() == 'nan') and symbol_upper in crypto_list: return f"{symbol_upper}-USD"
        return symbol_upper

    def fetch_data_automatically(self):
        if self.portfolio.empty: return "持仓为空"
        tickers = self.portfolio['YF_Ticker'].unique().tolist()
        valid_tickers = sorted(list(set([t for t in tickers if t and 'NAN' not in t.upper()])))
        if not valid_tickers: return "无有效代码"
        try:
            data = yf.download(" ".join(valid_tickers), period="2y", group_by='ticker', auto_adjust=True, threads=True)
            self.market_data = {}
            for t in valid_tickers:
                df = pd.DataFrame()
                if len(valid_tickers) == 1: df = data.copy()
                else:
                    try: df = data[t].copy()
                    except KeyError: continue
                df = df.dropna(how='all')
                if not df.empty and len(df) > 30: self.market_data[t] = df
            return f"✅ 数据更新完成 ({len(self.market_data)}/{len(valid_tickers)})"
        except Exception as e: 
            return f"❌ 下载异常: {e}"

    # --- 纳指全景扫描 (大幅升级) ---
    def analyze_nasdaq_crash_risk(self):
        """
        分析纳指 (QQQ) 的生命周期状态
        """
        try:
            # 获取 QQQ (纳指100), ^VXN (纳指波动率), ^TNX (10年期美债收益率)
            tickers = "QQQ ^VXN ^TNX"
            data = yf.download(tickers, period="2y", group_by='ticker', auto_adjust=True, threads=True)
            
            # 数据提取与清洗
            try:
                df_qqq = data['QQQ'].dropna()
                df_vxn = data['^VXN'].dropna()
                # TNX 可选，万一获取不到不影响主逻辑
                df_tnx = data['^TNX'].dropna() if '^TNX' in data else pd.DataFrame()
            except KeyError:
                return None
            
            if df_qqq.empty: return None

            # --- 核心指标计算 ---
            current_price = df_qqq['Close'].iloc[-1]
            current_vxn = df_vxn['Close'].iloc[-1] if not df_vxn.empty else 20.0
            
            # 1. 均线系统
            sma20 = ta.sma(df_qqq['Close'], length=20).iloc[-1]
            sma50 = ta.sma(df_qqq['Close'], length=50).iloc[-1]
            sma200 = ta.sma(df_qqq['Close'], length=200).iloc[-1]
            
            # 2. 历史高点回撤 (Drawdown from ATH)
            ath = df_qqq['High'].max()
            dd_from_ath = (current_price - ath) / ath * 100 # 负数，例如 -15%
            
            # 3. 相对强弱 RSI
            rsi = ta.rsi(df_qqq['Close'], length=14).iloc[-1]
            
            # 4. 乖离率 (Bias)
            bias_200 = (current_price - sma200) / sma200 * 100
            
            # --- 市场体制判定算法 (Phase Detection) ---
            phase = "未知"
            risk_level = "中"
            desc = "数据分析中..."
            
            # 判定逻辑优先级：从最极端的熊市开始判断
            
            # A. 暴跌/恐慌阶段
            if current_price < sma200 and current_vxn > 28:
                phase = "🚑 恐慌触底 (Panic)"
                risk_level = "高风险 (左侧机会)"
                desc = "市场处于极度恐慌，价格跌破长期均线，VXN高企。通常是暴跌中段或末段。"
            
            # B. 主跌浪/确立下行
            elif current_price < sma50 and current_price < sma200:
                phase = "📉 熊市确立 (Bear Market)"
                risk_level = "高风险 (趋势向下)"
                desc = "价格全面跌破生命线，趋势完好向下。切勿盲目抄底，等待止跌信号。"
            
            # C. 震荡修复阶段 (这正是你提到的情况)
            elif current_price < sma50 and current_price > sma200:
                # 依然在长期均线之上，但短期跌破了
                if dd_from_ath < -5:
                    phase = "🛠️ 震荡修复 (Repairing)"
                    risk_level = "中风险"
                    desc = "长期趋势未坏，但短期遭遇回调。目前处于消化估值和情绪修复的阶段。"
                else:
                    phase = "💸 轻微回撤 (Pullback)"
                    risk_level = "低风险"
                    desc = "良性回调，上升途中的歇脚。"

            # D. 顶部过热阶段
            elif current_price > sma50 and bias_200 > 15 and rsi > 70:
                phase = "🌋 顶部过热 (Overheated)"
                risk_level = "极高风险 (回调预警)"
                desc = "价格严重偏离长期均线，RSI超买，贪婪情绪过重。随时可能发生大级别回撤。"
            
            # E. 健康上涨
            elif current_price > sma50 and current_price > sma200:
                phase = "🚀 强势上涨 (Bull Run)"
                risk_level = "低风险"
                desc = "多头排列，趋势健康。持有为主，直到跌破20日线。"
            
            else:
                phase = "🦀 混沌震荡 (Choppy)"
                risk_level = "中"
                desc = "无明确方向，均线纠缠。"

            return {
                "Phase": phase,
                "Risk_Level": risk_level,
                "Description": desc,
                "VXN": current_vxn,
                "RSI": rsi,
                "DD_ATH": dd_from_ath, # 距最高点回撤
                "Price": current_price,
                "SMA200": sma200,
                "SMA200_Bias": bias_200,
                "TNX": df_tnx['Close'].iloc[-1] if not df_tnx.empty else 0
            }

        except Exception as e:
            print(f"Nasdaq risk calc error: {e}")
            return None

    # --- 智能分析与策略计算 (个股) ---
    def analyze_market_regime(self, ticker):
        """个股市场状态"""
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy()
        try:
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            current_adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
            atr = ta.atr(df['High'], df['Low'], df['Close'], length=14).iloc[-1]
            price = df['Close'].iloc[-1]
            volatility_pct = (atr / price) * 100
            days = len(df)
            ret_1m = df['Close'].pct_change(21).iloc[-1] if days > 21 else 0
            ret_6m = df['Close'].pct_change(126).iloc[-1] if days > 126 else 0
            ret_1y = df['Close'].pct_change(252).iloc[-1] if days > 252 else 0
        except: return None

        def get_status_desc(ret):
            if ret >= 0.20: return "🚀 强势上涨"
            if ret >= 0.05: return "📈 稳步上涨"
            if ret <= -0.20: return "📉 暴风骤跌"
            if ret <= -0.05: return "💸 轻微回撤"
            return "🦀 横盘震荡"

        if ret_1m <= -0.15: recommendation = "SMA Reversal"
        elif ret_1m >= 0.20: recommendation = "SMA Cross"
        elif current_adx < 20: recommendation = "Bollinger"
        else: recommendation = "SMA Cross"

        return {
            "ADX": current_adx,
            "Volatility": volatility_pct,
            "1M": {"Val": ret_1m, "Desc": get_status_desc(ret_1m)},
            "6M": {"Val": ret_6m, "Desc": get_status_desc(ret_6m)},
            "1Y": {"Val": ret_1y, "Desc": get_status_desc(ret_1y)},
            "Recommendation": recommendation
        }

    def calculate_strategy(self, ticker, strategy_name, params):
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy().sort_index()
        try:
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            df = pd.concat([df, adx_df], axis=1)
        except: df['ADX_14'] = 0

        try:
            df['Signal'] = 0 
            if strategy_name == "SMA Cross":
                s = params.get('short', 10); l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                prev_s = df['SMA_S'].shift(1); prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']; curr_l = df['SMA_L']
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                strong_trend = df['ADX_14'] > 20
                df.loc[golden_cross & strong_trend, 'Signal'] = 1
                df.loc[death_cross & strong_trend, 'Signal'] = -1

            elif strategy_name == "SMA Reversal":
                s = params.get('short', 10); l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                prev_s = df['SMA_S'].shift(1); prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']; curr_l = df['SMA_L']
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                strong_trend = df['ADX_14'] > 20 
                df.loc[death_cross & strong_trend, 'Signal'] = 1
                df.loc[golden_cross & strong_trend, 'Signal'] = -1

            elif strategy_name == "RSI":
                length = params.get('length', 14)
                df['RSI'] = ta.rsi(df['Close'], length=length)
                df.loc[df['RSI'] < 30, 'Signal'] = 1
                df.loc[df['RSI'] > 70, 'Signal'] = -1

            elif strategy_name == "Bollinger":
                length = params.get('length', 20)
                bb = ta.bbands(df['Close'], length=length, std=2)
                if bb is not None:
                    df = pd.concat([df, bb], axis=1)
                    lower = bb.columns[0]; upper = bb.columns[2]
                    df.loc[df['Close'] < df[lower], 'Signal'] = 1
                    df.loc[df['Close'] > df[upper], 'Signal'] = -1
        except Exception: return None
        return df

    def get_signal_status(self, df):
        if df is None or 'Signal' not in df.columns: return "No Data"
        last_signals = df[df['Signal'] != 0]
        if last_signals.empty: return "⚪ 无信号"
        last_sig = last_signals['Signal'].iloc[-1]
        last_date = last_signals.index[-1].strftime('%Y-%m-%d')
        if last_sig == 1: return f"🟢 买入 ({last_date})"
        elif last_sig == -1: return f"🔴 卖出 ({last_date})"
        return "⚪ 观望"

    # --- 配置管理 ---
    def load_strategy_config(self):
        if os.path.exists(self.config_file):
            try: with open(self.config_file, 'r') as f: return json.load(f)
            except: return {}
        return {}

    def save_strategy_config(self, ticker, strategy):
        self.strategy_map[ticker] = strategy
        with open(self.config_file, 'w') as f: json.dump(self.strategy_map, f)
            
    def get_active_strategy(self, ticker, default_strategy):
        return self.strategy_map.get(ticker, default_strategy)
