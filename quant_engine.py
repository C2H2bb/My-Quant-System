import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st
import os

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
        pass # 如果没配置或发送失败，静默处理，不卡死主程序

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
            
            # 简单的列名检查
            if 'Symbol' not in df.columns:
                return False, "CSV 缺少 'Symbol' 列"

            portfolio_list = []
            for index, row in df.iterrows():
                symbol = str(row['Symbol']).strip()
                
                # 跳过无效行
                if not symbol or symbol.lower() == 'nan':
                    continue
                
                # 尝试获取数量，没有则默认为 0
                try:
                    qty = float(row.get('Quantity', 0))
                except:
                    qty = 0.0

                # 映射 Yahoo Finance 代码
                yf_ticker = self._map_symbol(symbol, str(row.get('Exchange', '')), str(row.get('Name', '')))
                
                portfolio_list.append({
                    "Symbol": symbol,
                    "YF_Ticker": yf_ticker,
                    "Quantity": qty,
                    "Name": row.get('Name', symbol)
                })
            
            self.portfolio = pd.DataFrame(portfolio_list)
            return True, f"✅ 已加载 {len(self.portfolio)} 个持仓"
        except Exception as e:
            return False, f"❌ 文件加载失败: {str(e)}"

    def _map_symbol(self, symbol, exchange, name):
        """将 Wealthsimple/本地代码映射为 Yahoo Finance 代码"""
        # 1. 已经是 Yahoo 格式 (包含点号或横线，且不是 CDR)
        if '.' in symbol and 'TO' in symbol: return symbol
        
        # 2. 加拿大股票 (TSX/NEO)
        if 'CDR' in name or 'NEO' in exchange:
            return f"{symbol.replace('.', '-')}.NE"
        if 'TSX' in exchange or 'Toronto' in exchange:
            return f"{symbol.replace('.', '-')}.TO"
        
        # 3. 加密货币 (通常没有交易所信息或特殊标记)
        if not exchange or exchange == 'nan':
            # 简单猜测，如果是常见的 BTC/ETH
            if symbol in ['BTC', 'ETH', 'SOL']: return f"{symbol}-USD"
            
        # 4. 美股 (默认)
        return symbol

    def fetch_data_automatically(self):
        """自动下载数据 (带缓存优化)"""
        if self.portfolio.empty:
            return "持仓为空，跳过下载"

        tickers = self.portfolio['YF_Ticker'].unique().tolist()
        valid_tickers = [t for t in tickers if t and 'nan' not in t.lower()]
        
        if not valid_tickers:
            return "无有效股票代码"

        # 使用 yfinance 批量下载
        try:
            ticker_str = " ".join(valid_tickers)
            data = yf.download(ticker_str, period="1y", group_by='ticker', auto_adjust=True, threads=True)
            
            self.market_data = {}
            
            for t in valid_tickers:
                # 提取单个股票数据
                if len(valid_tickers) == 1:
                    df = data.copy()
                else:
                    try:
                        df = data[t].copy()
                    except KeyError:
                        continue
                
                # 清洗无效数据
                df = df.dropna(how='all')
                if not df.empty:
                    self.market_data[t] = df
            
            return f"✅ 成功更新 {len(self.market_data)} 只股票的行情"
        except Exception as e:
            return f"❌ 数据下载异常: {e}"

    def calculate_strategy(self, ticker, strategy_name, params):
        """计算策略指标，返回处理后的 DataFrame"""
        if ticker not in self.market_data:
            return None
        
        df = self.market_data[ticker].copy()
        if df.empty: return None

        try:
            # --- 策略 1: 双均线 (SMA) ---
            if strategy_name == "SMA Cross":
                s = params.get('short', 10)
                l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                
                # 信号: 1=Buy, -1=Sell
                df['Signal'] = 0
                # 只有当短线大于长线时
                df.loc[df['SMA_S'] > df['SMA_L'], 'Signal'] = 1
                df.loc[df['SMA_S'] < df['SMA_L'], 'Signal'] = -1
                
            # --- 策略 2: RSI ---
            elif strategy_name == "RSI":
                length = params.get('length', 14)
                df['RSI'] = ta.rsi(df['Close'], length=length)
                
                df['Signal'] = 0
                df.loc[df['RSI'] < 30, 'Signal'] = 1  # 超卖 -> 买
                df.loc[df['RSI'] > 70, 'Signal'] = -1 # 超买 -> 卖

            # --- 策略 3: 布林带 (Bollinger) ---
            elif strategy_name == "Bollinger":
                length = params.get('length', 20)
                # pandas_ta 的 bbands 返回多列
                bb = ta.bbands(df['Close'], length=length, std=2)
                if bb is not None:
                    df = pd.concat([df, bb], axis=1)
                    # 动态获取列名 (BBL_20_2.0, BBU_20_2.0 等)
                    lower_col = bb.columns[0] 
                    upper_col = bb.columns[2]
                    
                    df['Signal'] = 0
                    df.loc[df['Close'] < df[lower_col], 'Signal'] = 1
                    df.loc[df['Close'] > df[upper_col], 'Signal'] = -1

        except Exception as e:
            print(f"Strategy calc error for {ticker}: {e}")
            return None

        return df

    def get_signal_status(self, df, strategy_name):
        """解析最后一日信号为文字"""
        if df is None or 'Signal' not in df.columns:
            return "No Data"
        
        last_sig = df['Signal'].iloc[-1]
        if last_sig == 1: return "🟢 BUY"
        elif last_sig == -1: return "🔴 SELL"
        return "⚪ HOLD"
