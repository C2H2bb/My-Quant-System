import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st
import time

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

    # --- 数据加载 (保持不变) ---
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

    # --- 智能分析与策略计算 (核心优化部分) ---

    def analyze_market_regime(self, ticker):
        """判断股票当前处于什么状态 (趋势 vs 震荡)"""
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy()
        
        # 1. 计算 ADX (趋势强度)
        adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        current_adx = 0
        if adx_df is not None and not adx_df.empty:
            current_adx = adx_df['ADX_14'].iloc[-1]
        
        # 2. 计算 ATR (波动率) 辅助判断
        atr = ta.atr(df['High'], df['Low'], df['Close'], length=14).iloc[-1]
        price = df['Close'].iloc[-1]
        volatility = (atr / price) * 100 # 波动率百分比

        # 3. 判定逻辑
        if current_adx > 25:
            trend_status = "强趋势 🔥"
            # 趋势强，适合 SMA 或 MACD
            recommendation = "SMA Cross" 
        elif current_adx < 20:
            trend_status = "弱势/盘整 💤"
            # 没趋势，SMA 会死得很惨，推荐布林带做高抛低吸
            recommendation = "Bollinger"
        else:
            trend_status = "趋势不明 🤔"
            recommendation = "RSI" # 中性情况用 RSI 辅助
            
        return {
            "ADX": current_adx,
            "Volatility": volatility,
            "Status": trend_status,
            "Recommendation": recommendation
        }

    def calculate_strategy(self, ticker, strategy_name, params):
        """
        计算策略指标 (已修复逻辑：只在交叉点发出信号，且增加 ADX 过滤)
        """
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy().sort_index()
        
        # 计算 ADX 用于过滤
        adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        df = pd.concat([df, adx_df], axis=1)

        try:
            df['Signal'] = 0 # 默认为0

            # --- 策略 1: SMA Cross (优化版) ---
            if strategy_name == "SMA Cross":
                s = params.get('short', 10)
                l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                
                # 逻辑修复：不是 > 就买，而是“昨天 < 今天 >” (交叉瞬间)
                # Shift(1) 代表昨天的数据
                prev_s = df['SMA_S'].shift(1)
                prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']
                curr_l = df['SMA_L']
                
                # 金叉: 昨天短<长 且 今天短>长
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                # 死叉: 昨天短>长 且 今天短<长
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                
                # 核心过滤：只有当 ADX > 20 时，才承认这个交叉信号
                # 如果 ADX 很低，说明是横盘震荡，此时的交叉通常是假动作
                strong_trend = df['ADX_14'] > 20
                
                df.loc[golden_cross & strong_trend, 'Signal'] = 1
                df.loc[death_cross & strong_trend, 'Signal'] = -1

            # --- 策略 2: RSI ---
            elif strategy_name == "RSI":
                length = params.get('length', 14)
                df['RSI'] = ta.rsi(df['Close'], length=length)
                
                # RSI < 30 买入
                df.loc[df['RSI'] < 30, 'Signal'] = 1
                # RSI > 70 卖出
                df.loc[df['RSI'] > 70, 'Signal'] = -1

            # --- 策略 3: Bollinger ---
            elif strategy_name == "Bollinger":
                length = params.get('length', 20)
                bb = ta.bbands(df['Close'], length=length, std=2)
                if bb is not None:
                    df = pd.concat([df, bb], axis=1)
                    lower = bb.columns[0]; upper = bb.columns[2]
                    
                    # 收盘价跌破下轨 -> 买
                    df.loc[df['Close'] < df[lower], 'Signal'] = 1
                    # 收盘价突破上轨 -> 卖
                    df.loc[df['Close'] > df[upper], 'Signal'] = -1

        except Exception as e:
            print(f"Error calc strategy for {ticker}: {e}")
            return None

        return df

    def get_signal_status(self, df):
        if df is None or 'Signal' not in df.columns: return "No Data"
        # 查找最近一次非0的信号
        last_signals = df[df['Signal'] != 0]
        if last_signals.empty:
            return "⚪ 无信号"
        
        last_sig = last_signals['Signal'].iloc[-1]
        last_date = last_signals.index[-1].strftime('%Y-%m-%d')
        
        if last_sig == 1: return f"🟢 买入 ({last_date})"
        elif last_sig == -1: return f"🔴 卖出 ({last_date})"
        return "⚪ 观望"

    # --- 配置管理 ---
    def load_strategy_config(self):
        import json, os
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f: return json.load(f)
            except: return {}
        return {}

    def save_strategy_config(self, ticker, strategy):
        import json
        self.strategy_map[ticker] = strategy
        with open(self.config_file, 'w') as f: json.dump(self.strategy_map, f)
            
    def get_active_strategy(self, ticker, default_strategy):
        return self.strategy_map.get(ticker, default_strategy)
