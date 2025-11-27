import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests

def send_telegram_alert(message):
    """发送消息到手机"""
    bot_token = "BOT_TOKEN"
    chat_id = "CHAT_ID"
    
    send_text = f'https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&parse_mode=Markdown&text={message}'
    
    try:
        response = requests.get(send_text)
        return response.json()
    except Exception as e:
        return str(e)
        
class QuantEngine:
    
    def __init__(self):
        self.portfolio = None
        self.market_data = {}

    def load_portfolio(self, file_path_or_buffer):
        """
        读取并解析 Wealthsimple 的 CSV 文件
        """
        try:
            # 读取 CSV
            df = pd.read_csv(file_path_or_buffer)
            
            # 标准化列名（去除可能的空格）
            df.columns = [c.strip() for c in df.columns]
            
            portfolio_data = []
            
            for index, row in df.iterrows():
                symbol = str(row['Symbol']).strip()
                name = str(row['Name'])
                exchange = str(row['Exchange'])
                quantity = float(row['Quantity'])
                
                # 跳过已卖空或数量为0的（如果有）
                if quantity <= 0:
                    continue

                # 计算平均成本
                # Wealthsimple: 'Book Value (Market)' 是总成本
                try:
                    book_val = float(row['Book Value (Market)'])
                    avg_cost = book_val / quantity
                except:
                    avg_cost = 0.0
                
                # 获取 Yahoo Finance 对应的 Ticker
                yf_ticker = self._map_to_yahoo_symbol(symbol, exchange, name)
                
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
        """
        智能映射：处理 CDR, 美股, 加股, Crypto
        """
        # 1. 加密货币 (Exchange 通常为空或 NaN)
        if exchange == 'nan' or exchange == '' or pd.isna(exchange):
            return f"{symbol}-USD"
            
        # 2. 识别 CDR (Canadian Depositary Receipts)
        # 你的 CSV 里 NVDA 在 TSX，但名字含 "CDR"
        # Yahoo Finance 上 CDR 通常以 .NE (NEO Exchange) 结尾，但也可能在 .TO
        if "CDR" in name:
            # 优先尝试 .NE，因为大部分 CDR 在 Cboe Canada (原 NEO)
            return f"{symbol}.NE"

        # 3. 加拿大股市
        if 'TSX' in exchange or 'Toronto' in exchange:
            clean_symbol = symbol.replace('.', '-')
            return f"{clean_symbol}.TO"
            
        if 'CBOE' in exchange or 'NEO' in exchange:
            clean_symbol = symbol.replace('.', '-')
            return f"{clean_symbol}.NE"
            
        # 4. 美股 (NASDAQ, NYSE -> 直接用 Symbol)
        # 有时 CSV 里是 'NASDAQ ' 带空格
        if 'NASDAQ' in exchange or 'NYSE' in exchange:
            return symbol

        # 默认回退
        return symbol

    def fetch_market_data(self):
        """下载市场历史数据"""
        if self.portfolio is None or self.portfolio.empty:
            return

        tickers = self.portfolio['YF_Ticker'].unique().tolist()
        if not tickers:
            return

        tickers_str = " ".join(tickers)
        print(f"Fetching: {tickers_str}")
        
        # 下载数据
        data = yf.download(tickers_str, period="1y", group_by='ticker', auto_adjust=True, threads=True)
        
        for ticker in tickers:
            if len(tickers) == 1:
                df = data.copy()
            else:
                try:
                    df = data[ticker].copy()
                except KeyError:
                    print(f"Warning: Could not fetch data for {ticker}")
                    continue
            
            # 简单清洗
            df = df.dropna(how='all')
            if not df.empty:
                self.market_data[ticker] = df

    def calculate_signals(self, strategy_type, params):
        """计算策略信号"""
        results = []
        
        if self.portfolio is None:
            return pd.DataFrame()

        for index, row in self.portfolio.iterrows():
            ticker = row['YF_Ticker']
            signal = "WAIT" # 默认状态
            
            if ticker not in self.market_data:
                results.append("Data Error")
                continue
                
            df = self.market_data[ticker].copy()
            if df.empty or len(df) < 50: # 确保数据足够
                results.append("No Data")
                continue

            try:
                # --- 策略 1: SMA Cross (双均线) ---
                if strategy_type == "SMA Cross":
                    s_win = params.get('short', 10)
                    l_win = params.get('long', 50)
                    
                    df['SMA_S'] = ta.sma(df['Close'], length=s_win)
                    df['SMA_L'] = ta.sma(df['Close'], length=l_win)
                    
                    curr_s = df['SMA_S'].iloc[-1]
                    curr_l = df['SMA_L'].iloc[-1]
                    prev_s = df['SMA_S'].iloc[-2]
                    prev_l = df['SMA_L'].iloc[-2]
                    
                    if prev_s < prev_l and curr_s > curr_l:
                        signal = "BUY (Golden Cross)"
                    elif prev_s > prev_l and curr_s < curr_l:
                        signal = "SELL (Death Cross)"
                    elif curr_s > curr_l:
                        signal = "HOLD (Bullish)"
                    else:
                        signal = "AVOID (Bearish)"

                # --- 策略 2: RSI ---
                elif strategy_type == "RSI":
                    length = params.get('length', 14)
                    df['RSI'] = ta.rsi(df['Close'], length=length)
                    curr_rsi = df['RSI'].iloc[-1]
                    
                    if curr_rsi < params['lower']:
                        signal = f"BUY (Oversold {curr_rsi:.0f})"
                    elif curr_rsi > params['upper']:
                        signal = f"SELL (Overbought {curr_rsi:.0f})"
                    else:
                        signal = f"Neutral ({curr_rsi:.0f})"

            except Exception:
                signal = "Calc Error"
            
            results.append(signal)
            
        self.portfolio['Signal'] = results
        return self.portfolio

    def get_last_price(self, ticker):
        if ticker in self.market_data:
            return self.market_data[ticker]['Close'].iloc[-1]
        return 0.0
        

    def check_and_alert(self, ticker, strategy_name, params):
        """检查信号并推送到手机"""
        df = self.apply_strategy(ticker, strategy_name, params)
        signal = self.get_latest_signal(df)
        
        # 只有当出现买入或卖出信号时才推送
        if "BUY" in signal or "SELL" in signal:
            current_price = df.iloc[-1]['Close']
            msg = f"🚨 **交易信号提醒** 🚨\n\n" \
                  f"股票: `{ticker}`\n" \
                  f"价格: `${current_price:.2f}`\n" \
                  f"策略: {strategy_name}\n" \
                  f"信号: {signal}"
            send_telegram_alert(msg)
            return True # 触发了提醒
        return False


        
 
