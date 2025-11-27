import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st
import json
import os

# Telegram 推送函数 (保持不变)
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
        self.strategy_map = self.load_strategy_config() # 加载用户锁定的策略

    # --- 1. 基础数据加载 (保持原有逻辑) ---
    def load_portfolio(self, file_path_or_buffer):
        """加载持仓文件"""
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
        except Exception as e: return False, f"❌ 文件解析失败: {str(e)}"

    def _map_symbol(self, symbol, exchange, name, currency):
        """映射 Ticker (含 GOLD 修复)"""
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
        """自动下载数据"""
        if self.portfolio.empty: return "持仓为空"
        tickers = self.portfolio['YF_Ticker'].unique().tolist()
        valid_tickers = sorted(list(set([t for t in tickers if t and 'NAN' not in t.upper()])))
        if not valid_tickers: return "无有效代码"
        
        try:
            data = yf.download(" ".join(valid_tickers), period="1y", group_by='ticker', auto_adjust=True, threads=True)
            self.market_data = {}
            for t in valid_tickers:
                df = pd.DataFrame()
                if len(valid_tickers) == 1: df = data.copy()
                else:
                    try: df = data[t].copy()
                    except KeyError: continue
                df = df.dropna(how='all')
                if not df.empty and len(df) > 20: self.market_data[t] = df
            return f"✅ 数据更新完成 ({len(self.market_data)}/{len(valid_tickers)})"
        except Exception as e: return f"❌ 下载异常: {e}"

    # --- 2. 智能分析与动态策略模块 (核心新增) ---

    def analyze_market_regime(self, ticker):
        """
        分析市场体制 (Trend vs Range)
        返回: dict 包含各项指标解读
        """
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy()
        
        # 计算 ADX (趋势强度)
        adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        if adx_df is None or adx_df.empty: return None
        current_adx = adx_df['ADX_14'].iloc[-1]
        
        # 计算 SMA 排列 (判断多空)
        sma50 = ta.sma(df['Close'], length=50).iloc[-1]
        sma200 = ta.sma(df['Close'], length=200).iloc[-1]
        price = df['Close'].iloc[-1]
        
        # 判定逻辑
        trend_strength = "弱"
        regime = "震荡/盘整"
        recommendation = "Bollinger" # 默认防守型
        
        if current_adx > 25:
            trend_strength = "强"
            if price > sma50:
                regime = "单边上涨"
                recommendation = "SMA Cross" # 趋势好时用均线
            elif price < sma50:
                regime = "单边下跌"
                recommendation = "SMA Cross" # 也可以考虑做空或者空仓等待
        else:
            # ADX 低于 25，震荡市
            regime = "无序震荡"
            recommendation = "Bollinger" # 震荡市用布林带高抛低吸
            
        return {
            "ADX": current_adx,
            "Trend_Strength": trend_strength,
            "Regime": regime,
            "Recommendation": recommendation
        }

    def calculate_strategy(self, ticker, strategy_name, params):
        """计算策略指标"""
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy().sort_index()
        
        try:
            if strategy_name == "SMA Cross":
                s = params.get('short', 10)
                l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                df['Signal'] = 0
                df.loc[df['SMA_S'] > df['SMA_L'], 'Signal'] = 1
                df.loc[df['SMA_S'] < df['SMA_L'], 'Signal'] = -1

            elif strategy_name == "RSI":
                length = params.get('length', 14)
                df['RSI'] = ta.rsi(df['Close'], length=length)
                df['Signal'] = 0
                df.loc[df['RSI'] < 30, 'Signal'] = 1
                df.loc[df['RSI'] > 70, 'Signal'] = -1

            elif strategy_name == "Bollinger":
                length = params.get('length', 20)
                bb = ta.bbands(df['Close'], length=length, std=2)
                if bb is not None:
                    df = pd.concat([df, bb], axis=1)
                    lower = bb.columns[0]; upper = bb.columns[2]
                    df['Signal'] = 0
                    df.loc[df['Close'] < df[lower], 'Signal'] = 1
                    df.loc[df['Close'] > df[upper], 'Signal'] = -1
        except Exception: return None
        return df

    def get_signal_status(self, df):
        if df is None or 'Signal' not in df.columns: return "No Data"
        last_sig = df['Signal'].iloc[-1]
        if last_sig == 1: return "🟢 BUY"
        elif last_sig == -1: return "🔴 SELL"
        return "⚪ HOLD"

    # --- 3. 配置管理 (持久化存储) ---
    
    def load_strategy_config(self):
        """读取用户锁定的策略配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save_strategy_config(self, ticker, strategy):
        """锁定某个股票的策略"""
        self.strategy_map[ticker] = strategy
        with open(self.config_file, 'w') as f:
            json.dump(self.strategy_map, f)
            
    def get_active_strategy(self, ticker, default_strategy):
        """获取当前股票应该使用的策略（优先使用锁定的，否则用默认）"""
        return self.strategy_map.get(ticker, default_strategy)
