import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st
import time
import json
import os

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
        except Exception as e: return False, f"❌ 解析失败: {str(e)}"

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
            # 下载2年数据以计算长期指标
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
        except Exception as e: return f"❌ 下载异常: {e}"

    # --- 智能分析与策略计算 (重大升级) ---

    def analyze_market_regime(self, ticker):
        """
        多维度市场状态诊断
        """
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy()
        
        # 1. 基础指标计算
        try:
            # ADX 趋势强度
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            current_adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
            
            # ATR 波动率
            atr = ta.atr(df['High'], df['Low'], df['Close'], length=14).iloc[-1]
            price = df['Close'].iloc[-1]
            volatility_pct = (atr / price) * 100
            
            # 计算各周期收益率
            days = len(df)
            ret_1m = df['Close'].pct_change(21).iloc[-1] if days > 21 else 0
            ret_6m = df['Close'].pct_change(126).iloc[-1] if days > 126 else 0
            ret_1y = df['Close'].pct_change(252).iloc[-1] if days > 252 else 0
            
        except:
            return None

        # 2. 状态判定辅助函数
        def get_status_desc(ret):
            if ret >= 0.20: return "🚀 强势上涨"
            if ret >= 0.05: return "📈 稳步上涨"
            if ret <= -0.20: return "📉 暴风骤跌" # 对应 DJT 等暴跌情况
            if ret <= -0.05: return "💸 轻微回撤"
            return "🦀 横盘震荡"

        # 3. 综合推荐逻辑
        # 如果短期暴跌或暴涨，可能是反转机会
        if ret_1m <= -0.15:
            recommendation = "SMA Reversal" # 暴跌博反弹
        elif ret_1m >= 0.20:
            recommendation = "SMA Cross" # 暴涨顺势而为
        elif current_adx < 20:
            recommendation = "Bollinger" # 震荡市高抛低吸
        else:
            recommendation = "SMA Cross" # 默认趋势策略

        return {
            "ADX": current_adx,
            "Volatility": volatility_pct,
            "1M": {"Val": ret_1m, "Desc": get_status_desc(ret_1m)},
            "6M": {"Val": ret_6m, "Desc": get_status_desc(ret_6m)},
            "1Y": {"Val": ret_1y, "Desc": get_status_desc(ret_1y)},
            "Recommendation": recommendation
        }

    def calculate_strategy(self, ticker, strategy_name, params):
        """计算策略指标"""
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy().sort_index()
        
        # 计算 ADX 用于过滤
        try:
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            df = pd.concat([df, adx_df], axis=1)
        except:
            df['ADX_14'] = 0

        try:
            df['Signal'] = 0 

            # --- SMA Cross (顺势) ---
            if strategy_name == "SMA Cross":
                s = params.get('short', 10); l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                
                prev_s = df['SMA_S'].shift(1); prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']; curr_l = df['SMA_L']
                
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                strong_trend = df['ADX_14'] > 20 # 必须有趋势
                
                df.loc[golden_cross & strong_trend, 'Signal'] = 1
                df.loc[death_cross & strong_trend, 'Signal'] = -1

            # --- SMA Reversal (反向/逆势) ---
            elif strategy_name == "SMA Reversal":
                s = params.get('short', 10); l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                
                prev_s = df['SMA_S'].shift(1); prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']; curr_l = df['SMA_L']
                
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                
                # 逆势策略也需要在一定波动率下才有效，或者反过来思考
                # 这里简单逻辑：金叉卖，死叉买
                df.loc[death_cross, 'Signal'] = 1  # 死叉抄底
                df.loc[golden_cross, 'Signal'] = -1 # 金叉逃顶

            # --- RSI ---
            elif strategy_name == "RSI":
                length = params.get('length', 14)
                df['RSI'] = ta.rsi(df['Close'], length=length)
                df.loc[df['RSI'] < 30, 'Signal'] = 1
                df.loc[df['RSI'] > 70, 'Signal'] = -1

            # --- Bollinger ---
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
            try:
                with open(self.config_file, 'r') as f: return json.load(f)
            except: return {}
        return {}

    def save_strategy_config(self, ticker, strategy):
        self.strategy_map[ticker] = strategy
        with open(self.config_file, 'w') as f: json.dump(self.strategy_map, f)
            
    def get_active_strategy(self, ticker, default_strategy):
        return self.strategy_map.get(ticker, default_strategy)
