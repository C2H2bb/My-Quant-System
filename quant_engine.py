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

    # --- 智能分析与策略计算 ---

    def analyze_market_regime(self, ticker):
        """判断股票当前处于什么状态 (趋势 vs 震荡)"""
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy()
        
        # 1. 计算 ADX (趋势强度)
        try:
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            current_adx = 0
            if adx_df is not None and not adx_df.empty:
                current_adx = adx_df['ADX_14'].iloc[-1]
        except:
            current_adx = 0
        
        # 2. 计算 ATR (波动率)
        try:
            atr = ta.atr(df['High'], df['Low'], df['Close'], length=14).iloc[-1]
            price = df['Close'].iloc[-1]
            volatility = (atr / price) * 100
        except:
            volatility = 0

        # 3. 判定逻辑
        # 逻辑更新：如果趋势很强(ADX高)，推荐顺势(SMA Cross)
        # 如果趋势弱，推荐震荡策略(Bollinger)或反向策略(SMA Reversal)
        if current_adx > 25:
            trend_status = "强趋势 🔥"
            recommendation = "SMA Cross" 
        elif current_adx < 20:
            trend_status = "弱势/盘整 💤"
            recommendation = "SMA Reversal" # 震荡市推荐反向操作
        else:
            trend_status = "趋势不明 🤔"
            recommendation = "Bollinger"
            
        return {
            "ADX": current_adx,
            "Volatility": volatility,
            "Regime": trend_status,
            "Recommendation": recommendation
        }

    def calculate_strategy(self, ticker, strategy_name, params):
        """
        计算策略指标
        """
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy().sort_index()
        
        # 计算 ADX 用于过滤
        try:
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            df = pd.concat([df, adx_df], axis=1)
        except:
            df['ADX_14'] = 0

        try:
            df['Signal'] = 0 # 默认为0

            # --- 策略 1: SMA Cross (顺势) ---
            if strategy_name == "SMA Cross":
                s = params.get('short', 10)
                l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                
                prev_s = df['SMA_S'].shift(1)
                prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']
                curr_l = df['SMA_L']
                
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                strong_trend = df['ADX_14'] > 20
                
                df.loc[golden_cross & strong_trend, 'Signal'] = 1
                df.loc[death_cross & strong_trend, 'Signal'] = -1

            # --- 策略 2: SMA Reversal (反向/逆势) ---
            # 你的“神奇反向”策略：死叉买入，金叉卖出
            elif strategy_name == "SMA Reversal":
                s = params.get('short', 10)
                l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                
                prev_s = df['SMA_S'].shift(1)
                prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']
                curr_l = df['SMA_L']
                
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                
                # 这里我们依然使用 ADX 过滤，确保是有力度的反转信号
                strong_trend = df['ADX_14'] > 20 
                
                # 逻辑反转！
                df.loc[death_cross & strong_trend, 'Signal'] = 1  # 死叉 -> 买入 (抄底)
                df.loc[golden_cross & strong_trend, 'Signal'] = -1 # 金叉 -> 卖出 (逃顶)

            # --- 策略 3: RSI ---
            elif strategy_name == "RSI":
                length = params.get('length', 14)
                df['RSI'] = ta.rsi(df['Close'], length=length)
                df.loc[df['RSI'] < 30, 'Signal'] = 1
                df.loc[df['RSI'] > 70, 'Signal'] = -1

            # --- 策略 4: Bollinger ---
            elif strategy_name == "Bollinger":
                length = params.get('length', 20)
                bb = ta.bbands(df['Close'], length=length, std=2)
                if bb is not None:
                    df = pd.concat([df, bb], axis=1)
                    lower = bb.columns[0]; upper = bb.columns[2]
                    df.loc[df['Close'] < df[lower], 'Signal'] = 1
                    df.loc[df['Close'] > df[upper], 'Signal'] = -1

        except Exception as e:
            print(f"Error calc strategy for {ticker}: {e}")
            return None

        return df

    def get_signal_status(self, df):
        if df is None or 'Signal' not in df.columns: return "No Data"
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
