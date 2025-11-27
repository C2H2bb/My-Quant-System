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
        # 尝试从 Streamlit Secrets 读取
        bot_token = st.secrets["BOT_TOKEN"]
        chat_id = st.secrets["CHAT_ID"]
        send_text = f'https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&parse_mode=Markdown&text={message}'
        requests.get(send_text, timeout=5)
    except Exception:
        pass 

class QuantEngine:
    def __init__(self):
        self.portfolio = pd.DataFrame()
        self.market_data = {}

    def load_portfolio(self, file_path_or_buffer):
        """加载持仓文件 (支持本地路径或上传的文件对象)"""
        try:
            df = pd.read_csv(file_path_or_buffer)
            # 清洗列名，去除空格
            df.columns = [c.strip() for c in df.columns]
            
            if 'Symbol' not in df.columns:
                return False, "CSV 缺少 'Symbol' 列"

            portfolio_list = []
            for index, row in df.iterrows():
                raw_symbol = row['Symbol']
                
                # --- 强力清洗无效数据 ---
                # 1. 如果是真正的空值 (NaN/None)
                if pd.isna(raw_symbol):
                    continue
                
                symbol = str(raw_symbol).strip()
                
                # 2. 如果是字符串 'nan' 或空字符串
                if not symbol or symbol.lower() == 'nan':
                    continue
                
                # 获取其他元数据
                name = str(row.get('Name', 'Unknown'))
                exchange = str(row.get('Exchange', ''))
                currency = str(row.get('Currency', '')) # 获取货币列辅助判断
                
                # 数量处理
                try:
                    qty = float(row.get('Quantity', 0))
                except:
                    qty = 0.0
                
                # 映射 Yahoo Finance 代码
                yf_ticker = self._map_symbol(symbol, exchange, name, currency)
                
                # 再次检查映射后的代码是否有效
                if 'nan' in yf_ticker.lower():
                    continue

                portfolio_list.append({
                    "Symbol": symbol,
                    "YF_Ticker": yf_ticker,
                    "Quantity": qty,
                    "Name": name
                })
            
            if not portfolio_list:
                return False, "文件中未找到有效的股票代码"

            self.portfolio = pd.DataFrame(portfolio_list)
            return True, f"✅ 已加载 {len(self.portfolio)} 个持仓"
        except Exception as e:
            return False, f"❌ 文件加载失败: {str(e)}"

    def _map_symbol(self, symbol, exchange, name, currency):
        """智能映射 Ticker (增强版)"""
        symbol_upper = symbol.upper()
        
        # 1. 已经是 Yahoo 格式 (包含 .TO, .NE 等)
        if '.' in symbol_upper and ('TO' in symbol_upper or 'NE' in symbol_upper):
            return symbol_upper
        
        # 2. 常见加股 ETF 特殊处理 (如 FEQT, XEQT, VFV 等)
        # 如果货币是 CAD 且没有后缀，尝试加 .TO
        is_cad = currency.upper() == 'CAD'
        
        if 'CDR' in name or 'NEO' in exchange or 'CBOE' in exchange:
            return f"{symbol_upper.replace('.', '-')}.NE"
            
        if 'TSX' in exchange or 'TORONTO' in exchange.upper():
            return f"{symbol_upper.replace('.', '-')}.TO"
            
        # 如果没明确写交易所，但货币是 CAD，默认尝试 .TO
        if is_cad and '.' not in symbol_upper:
             return f"{symbol_upper}.TO"
        
        # 3. 加密货币 (通常 Symbol 是 BTC, ETH 且 Exchange 为空)
        if (not exchange or exchange.lower() == 'nan') and symbol_upper in ['BTC', 'ETH', 'SOL', 'DOGE']:
            return f"{symbol_upper}-USD"
            
        # 4. 美股 (默认)
        return symbol_upper

    def fetch_data_automatically(self):
        """自动下载数据 (带重试和过滤)"""
        if self.portfolio.empty:
            return "持仓为空"

        tickers = self.portfolio['YF_Ticker'].unique().tolist()
        # 最终过滤：移除任何包含 'NAN' 的代码
        valid_tickers = [t for t in tickers if t and 'NAN' not in t.upper()]
        
        if not valid_tickers:
            return "无有效代码"

        ticker_str = " ".join(valid_tickers)
        print(f"Fetching: {ticker_str}") # 用于调试
        
        try:
            # 下载数据，增加线程
            data = yf.download(ticker_str, period="1y", group_by='ticker', auto_adjust=True, threads=True)
            
            self.market_data = {}
            
            # 处理数据
            for t in valid_tickers:
                df = pd.DataFrame()
                if len(valid_tickers) == 1:
                    df = data.copy()
                else:
                    try:
                        df = data[t].copy()
                    except KeyError:
                        # 某个股票下载失败，不影响其他的
                        continue
                
                # 删除全为空的行
                df = df.dropna(how='all')
                
                # 只有当数据行数足够计算指标时才保存 (例如至少20行)
                if not df.empty and len(df) > 20:
                    self.market_data[t] = df
            
            return f"✅ 成功更新 {len(self.market_data)}/{len(valid_tickers)} 只股票"
        except Exception as e:
            return f"❌ 下载部分失败: {e}"

    def calculate_strategy(self, ticker, strategy_name, params):
        """计算策略指标"""
        if ticker not in self.market_data:
            return None
        
        df = self.market_data[ticker].copy()
        # 确保按日期升序
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
                    lower_col = bb.columns[0] 
                    upper_col = bb.columns[2]
                    df['Signal'] = 0
                    df.loc[df['Close'] < df[lower_col], 'Signal'] = 1
                    df.loc[df['Close'] > df[upper_col], 'Signal'] = -1

        except Exception:
            return None

        return df

    def get_signal_status(self, df, strategy_name):
        """解析信号状态"""
        if df is None or 'Signal' not in df.columns:
            return "No Data"
        
        last_sig = df['Signal'].iloc[-1]
        if last_sig == 1: return "🟢 BUY"
        elif last_sig == -1: return "🔴 SELL"
        return "⚪ HOLD"
