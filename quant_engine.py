import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st
import time

# Telegram 推送函数
def send_telegram_message(message):
    """发送消息到 Telegram，优先读取 Secrets，失败则忽略"""
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

    def load_portfolio(self, file_path_or_buffer):
        """加载持仓文件"""
        try:
            df = pd.read_csv(file_path_or_buffer)
            df.columns = [c.strip() for c in df.columns]
            
            if 'Symbol' not in df.columns:
                return False, "CSV 缺少 'Symbol' 列"

            portfolio_list = []
            for index, row in df.iterrows():
                raw_symbol = row['Symbol']
                if pd.isna(raw_symbol): continue
                
                symbol = str(raw_symbol).strip()
                if not symbol or symbol.lower() == 'nan': continue
                
                name = str(row.get('Name', 'Unknown'))
                exchange = str(row.get('Exchange', ''))
                currency = str(row.get('Currency', ''))
                
                try:
                    qty = float(row.get('Quantity', 0))
                except:
                    qty = 0.0
                
                # 映射 Yahoo Finance 代码
                yf_ticker = self._map_symbol(symbol, exchange, name, currency)
                
                # 过滤无效代码
                if 'nan' in yf_ticker.lower():
                    continue

                portfolio_list.append({
                    "Symbol": symbol,
                    "YF_Ticker": yf_ticker,
                    "Quantity": qty,
                    "Name": name
                })
            
            if not portfolio_list:
                return False, "未找到有效持仓"

            self.portfolio = pd.DataFrame(portfolio_list)
            return True, f"✅ 已加载 {len(self.portfolio)} 个持仓"
        except Exception as e:
            return False, f"❌ 文件解析失败: {str(e)}"

    def _map_symbol(self, symbol, exchange, name, currency):
        """
        智能映射 Ticker
        """
        symbol_upper = symbol.upper()
        name_upper = name.upper()
        
        # --- 1. 强制修正 Wealthsimple GOLD ---
        # 只要代码是 GOLD，且名字里没有 BARRICK (巴里克黄金公司)，就认为是实物黄金
        # 映射到 GC=F (黄金期货)
        if symbol_upper == 'GOLD' and 'BARRICK' not in name_upper:
            return 'GC=F'
        
        # --- 2. 已经是 Yahoo 格式 ---
        if '.' in symbol_upper and ('TO' in symbol_upper or 'NE' in symbol_upper):
            return symbol_upper
        
        # --- 3. 加股 ETF/CDR 处理 ---
        is_cad = currency.upper() == 'CAD'
        
        if 'CDR' in name_upper or 'NEO' in exchange or 'CBOE' in exchange:
            return f"{symbol_upper.replace('.', '-')}.NE"
            
        if 'TSX' in exchange or 'TORONTO' in exchange.upper():
            return f"{symbol_upper.replace('.', '-')}.TO"
            
        # 货币是 CAD 且无后缀，默认为 .TO
        if is_cad and '.' not in symbol_upper:
             return f"{symbol_upper}.TO"
        
        # --- 4. 加密货币 ---
        # Wealthsimple Crypto 通常没有 Exchange 信息
        crypto_list = ['BTC', 'ETH', 'SOL', 'DOGE', 'ADA', 'DOT']
        if (not exchange or exchange.lower() == 'nan') and symbol_upper in crypto_list:
            return f"{symbol_upper}-USD"
            
        # --- 5. 默认回退 (美股) ---
        return symbol_upper

    def fetch_data_automatically(self):
        """自动下载数据"""
        if self.portfolio.empty:
            return "持仓为空"

        tickers = self.portfolio['YF_Ticker'].unique().tolist()
        valid_tickers = [t for t in tickers if t and 'NAN' not in t.upper()]
        
        if not valid_tickers:
            return "无有效代码"

        valid_tickers = sorted(list(set(valid_tickers)))
        ticker_str = " ".join(valid_tickers)
        
        try:
            # 批量下载
            data = yf.download(ticker_str, period="1y", group_by='ticker', auto_adjust=True, threads=True)
            
            self.market_data = {}
            
            for t in valid_tickers:
                df = pd.DataFrame()
                if len(valid_tickers) == 1:
                    df = data.copy()
                else:
                    try:
                        df = data[t].copy()
                    except KeyError:
                        continue
                
                df = df.dropna(how='all')
                
                if not df.empty and len(df) > 10:
                    self.market_data[t] = df
            
            return f"✅ 数据更新完成 ({len(self.market_data)}/{len(valid_tickers)})"
        except Exception as e:
            return f"❌ 下载异常: {e}"

    def calculate_strategy(self, ticker, strategy_name, params):
        """计算策略"""
        if ticker not in self.market_data:
            return None
        
        df = self.market_data[ticker].copy()
        df = df.sort_index()
        
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
                    lower = bb.columns[0]
                    upper = bb.columns[2]
                    df['Signal'] = 0
                    df.loc[df['Close'] < df[lower], 'Signal'] = 1
                    df.loc[df['Close'] > df[upper], 'Signal'] = -1

        except Exception:
            return None

        return df

    def get_signal_status(self, df, strategy_name):
        if df is None or 'Signal' not in df.columns:
            return "No Data"
        last_sig = df['Signal'].iloc[-1]
        if last_sig == 1: return "🟢 BUY"
        elif last_sig == -1: return "🔴 SELL"
        return "⚪ HOLD"
