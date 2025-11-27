import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st

def send_telegram_alert(message):
    """发送消息到手机"""
    # 优先尝试读取 Secrets，如果没有则使用硬编码（仅供临时测试）
    try:
        bot_token = st.secrets["BOT_TOKEN"]
        chat_id = st.secrets["CHAT_ID"]
    except:
        # 这里填入你提供的硬编码值，作为 fallback
        bot_token = "8593529087:AAHyY1h6HSPtTdOl40SuHPGG7LYkiCWOL1w"
        chat_id = "5074684209"
    
    send_text = f'https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&parse_mode=Markdown&text={message}'
    
    try:
        response = requests.get(send_text, timeout=5)
        return response.json()
    except Exception as e:
        return str(e)

class QuantEngine:
    
    def __init__(self):
        self.portfolio = None
        self.market_data = {}

    def load_portfolio(self, file_path_or_buffer):
        """读取并解析 Wealthsimple 的 CSV 文件"""
        try:
            df = pd.read_csv(file_path_or_buffer)
            
            # 标准化列名
            df.columns = [c.strip() for c in df.columns]
            
            portfolio_data = []
            
            for index, row in df.iterrows():
                symbol = str(row['Symbol']).strip()
                # 简单清洗，跳过空行
                if symbol == 'nan' or not symbol:
                    continue
                    
                name = str(row.get('Name', 'Unknown'))
                exchange = str(row.get('Exchange', ''))
                
                try:
                    quantity = float(row['Quantity'])
                except:
                    quantity = 0.0
                
                if quantity <= 0:
                    continue

                # 计算平均成本
                try:
                    book_val = float(row['Book Value (Market)'])
                    avg_cost = book_val / quantity
                except:
                    avg_cost = 0.0
                
                yf_ticker = self._map_to_yahoo_symbol(symbol, exchange, name)
                
                # 二次过滤无效 ticker
                if 'nan' in yf_ticker.lower():
                    continue

                portfolio_data.append({
                    "Symbol": symbol,
                    "YF_Ticker": yf_ticker,
                    "Name": name,
                    "Quantity": quantity,
                    "AvgCost": avg_cost
                })
                
            self.portfolio = pd.DataFrame(portfolio_data)
            return True, f"成功加载 {len(self.portfolio)} 个持仓"
        except Exception as e:
            return False, f"文件解析错误: {str(e)}"

    def _map_to_yahoo_symbol(self, symbol, exchange, name):
        """智能映射 Ticker"""
        if pd.isna(exchange) or exchange == '' or exchange == 'nan':
            return f"{symbol}-USD"
            
        if "CDR" in name:
            return f"{symbol}.NE"

        if 'TSX' in exchange or 'Toronto' in exchange:
            clean_symbol = symbol.replace('.', '-')
            return f"{clean_symbol}.TO"
            
        if 'CBOE' in exchange or 'NEO' in exchange:
            clean_symbol = symbol.replace('.', '-')
            return f"{clean_symbol}.NE"
            
        if 'NASDAQ' in exchange or 'NYSE' in exchange:
            return symbol

        return symbol

    def fetch_market_data(self):
        """批量下载市场数据"""
        if self.portfolio is None or self.portfolio.empty:
            return

        tickers = self.portfolio['YF_Ticker'].unique().tolist()
        # 过滤掉潜在的坏数据
        tickers = [t for t in tickers if str(t).lower() != 'nan-usd']
        
        if not tickers:
            return

        tickers_str = " ".join(tickers)
        print(f"Fetching: {tickers_str}")
        
        # 使用 group_by='ticker' 确保结构统一
        data = yf.download(tickers_str, period="1y", group_by='ticker', auto_adjust=True, threads=True)
        
        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    df = data.copy()
                else:
                    df = data[ticker].copy()
                
                # 清洗空数据
                df = df.dropna(how='all')
                
                # 只有数据量足够才保存
                if not df.empty and len(df) > 10:
                    self.market_data[ticker] = df
                else:
                    print(f"Warning: No data found for {ticker}")
            except KeyError:
                print(f"KeyError fetching {ticker}")
                continue

    def apply_strategy(self, ticker, strategy_type, params):
        """
        核心策略计算方法
        返回: 带有指标列的 DataFrame
        """
        if ticker not in self.market_data:
            return None
            
        df = self.market_data[ticker].copy()
        
        # 确保按时间排序
        df = df.sort_index()

        try:
            if strategy_type == "SMA Cross":
                s_win = params.get('short', 10)
                l_win = params.get('long', 50)
                # 计算指标并添加到 DF
                df['SMA_S'] = ta.sma(df['Close'], length=s_win)
                df['SMA_L'] = ta.sma(df['Close'], length=l_win)
                
                # 生成信号 (1: Buy, -1: Sell)
                df['Signal_Code'] = 0
                # 简单的交叉逻辑
                df.loc[df['SMA_S'] > df['SMA_L'], 'Signal_Code'] = 1
                df.loc[df['SMA_S'] < df['SMA_L'], 'Signal_Code'] = -1

            elif strategy_type == "RSI":
                length = params.get('length', 14)
                lower = params.get('lower', 30)
                upper = params.get('upper', 70)
                
                df['RSI'] = ta.rsi(df['Close'], length=length)
                
                df['Signal_Code'] = 0
                df.loc[df['RSI'] < lower, 'Signal_Code'] = 1
                df.loc[df['RSI'] > upper, 'Signal_Code'] = -1
                
        except Exception as e:
            print(f"Strategy Error on {ticker}: {e}")
            return df # 返回原始数据防止崩溃

        return df

    def get_latest_signal_text(self, df, strategy_type, params):
        """将 apply_strategy 的结果转换为文字描述"""
        if df is None or df.empty:
            return "No Data"
            
        # 确保有指标列
        try:
            if strategy_type == "SMA Cross":
                if 'SMA_S' not in df.columns: return "Calc Error"
                curr_s = df['SMA_S'].iloc[-1]
                curr_l = df['SMA_L'].iloc[-1]
                # 防止 NaN
                if pd.isna(curr_s) or pd.isna(curr_l): return "Insufficient Data"
                
                if curr_s > curr_l: return "HOLD (Bullish)"
                else: return "AVOID (Bearish)"

            elif strategy_type == "RSI":
                if 'RSI' not in df.columns: return "Calc Error"
                curr_rsi = df['RSI'].iloc[-1]
                if pd.isna(curr_rsi): return "Insufficient Data"
                
                if curr_rsi < params['lower']: return f"BUY (Oversold {curr_rsi:.0f})"
                elif curr_rsi > params['upper']: return f"SELL (Overbought {curr_rsi:.0f})"
                else: return f"Neutral ({curr_rsi:.0f})"
        except:
            return "Error"
            
        return "WAIT"

    def check_and_alert(self, ticker, strategy_name, params):
        """检查信号并推送到手机"""
        df = self.apply_strategy(ticker, strategy_name, params)
        signal_text = self.get_latest_signal_text(df, strategy_name, params)
        
        # 只有当出现强买卖信号时才推送
        # 这里简单的逻辑：包含 BUY 或 SELL 字样
        if "BUY" in signal_text or "SELL" in signal_text:
            current_price = df.iloc[-1]['Close']
            msg = f"🚨 **交易信号提醒** 🚨\n\n" \
                  f"股票: `{ticker}`\n" \
                  f"价格: `${current_price:.2f}`\n" \
                  f"策略: {strategy_name}\n" \
                  f"信号: {signal_text}"
            self.send_telegram_alert(msg) # 假设调用外部函数或静态方法
            send_telegram_alert(msg) # 调用全局函数
            return True
        return False
