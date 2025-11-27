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
        # 设置短超时，防止网络卡顿影响主程序
        requests.get(send_text, timeout=3) 
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
                if pd.isna(raw_symbol):
                    continue
                
                symbol = str(raw_symbol).strip()
                
                # 过滤无效字符
                if not symbol or symbol.lower() == 'nan':
                    continue
                
                # 获取其他元数据
                name = str(row.get('Name', 'Unknown'))
                exchange = str(row.get('Exchange', ''))
                currency = str(row.get('Currency', ''))
                
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
        """智能映射 Ticker (包含特殊资产处理)"""
        symbol_upper = symbol.upper()
        
        # --- 1. 特殊资产手动映射 ---
        # Wealthsimple GOLD -> 黄金期货 (COMEX Gold Futures)
        if symbol_upper == 'GOLD' and ('WEALTHSIMPLE' in name.upper() or not exchange):
            return 'GC=F' 
        
        # --- 2. 已经是 Yahoo 格式 ---
        if '.' in symbol_upper and ('TO' in symbol_upper or 'NE' in symbol_upper):
            return symbol_upper
        
        # --- 3. 常见加股 ETF/CDR 处理 ---
        is_cad = currency.upper() == 'CAD'
        
        if 'CDR' in name or 'NEO' in exchange or 'CBOE' in exchange:
            return f"{symbol_upper.replace('.', '-')}.NE"
            
        if 'TSX' in exchange or 'TORONTO' in exchange.upper():
            return f"{symbol_upper.replace('.', '-')}.TO"
            
        # 只有货币是 CAD 且没有后缀时，才尝试加 .TO
        if is_cad and '.' not in symbol_upper:
             return f"{symbol_upper}.TO"
        
        # --- 4. 加密货币 ---
        if (not exchange or exchange.lower() == 'nan') and symbol_upper in ['BTC', 'ETH', 'SOL', 'DOGE']:
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

        # 移除重复项并排序
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
                
                # 删除空行
                df = df.dropna(how='all')
                
                # 只有数据足够才保存
                if not df.empty and len(df) > 10:
                    self.market_data[t] = df
            
            return f"✅ 更新完成: {len(self.market_data)}/{len(valid_tickers)}"
        except Exception as e:
            return f"❌ 下载异常: {e}"

    def calculate_strategy(self, ticker, strategy_name, params):
        """计算策略指标"""
        if ticker not in self.market_data:
            return None
        
        df = self.market_data[ticker].copy()
        # 按日期升序
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
                    # 动态取列名: BBL, BBM, BBU
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
