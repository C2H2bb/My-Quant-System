import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st
import numpy as np
import time
import json
import os

def send_telegram_message(message):
    try:
        bot_token = st.secrets["BOT_TOKEN"]
        chat_id = st.secrets["CHAT_ID"]
        send_text = f'https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&parse_mode=Markdown&text={message}'
        requests.get(send_text, timeout=3) 
    except Exception: pass 

class QuantEngine:
    def __init__(self):
        self.portfolio = pd.DataFrame()
        self.market_data = {}
        self.config_file = "strategy_config.json"
        self.strategy_map = self.load_strategy_config()

    # --- 基础数据加载 (保持不变) ---
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

    # =========================================================
    # 🔥 纳斯达克专业级全维分析引擎 (Pro Market Analysis)
    # =========================================================
    def analyze_nasdaq_pro(self):
        """
        综合多维度数据分析纳指健康状况
        """
        try:
            # 1. 获取多维数据
            # QQQ(纳指), QQQE(纳指等权-用于看宽度), ^VXN(波动率), ^TNX(10年美债), DX-Y.NYB(美元)
            tickers = "QQQ QQQE ^VXN ^TNX DX-Y.NYB"
            data = yf.download(tickers, period="2y", group_by='ticker', auto_adjust=True, threads=True)
            
            # 数据完整性检查
            try:
                q = data['QQQ'].dropna()   # Price
                qe = data['QQQE'].dropna() # Breadth Proxy
                vxn = data['^VXN'].dropna() # Volatility
                tnx = data['^TNX'].dropna() # Macro Rates
                dxy = data['DX-Y.NYB'].dropna() if 'DX-Y.NYB' in data else pd.DataFrame() # Macro Currency
            except KeyError:
                return None
                
            if q.empty: return None

            current_price = q['Close'].iloc[-1]
            
            # --- Ⅰ. 趋势类指标 (Trend) ---
            sma20 = ta.sma(q['Close'], 20).iloc[-1]
            sma50 = ta.sma(q['Close'], 50).iloc[-1]
            sma200 = ta.sma(q['Close'], 200).iloc[-1]
            
            # 乖离率
            bias_50 = (current_price - sma50) / sma50 * 100
            bias_200 = (current_price - sma200) / sma200 * 100
            
            # 趋势强度 (ADX)
            adx_df = ta.adx(q['High'], q['Low'], q['Close'], 14)
            adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
            
            # MACD
            macd = ta.macd(q['Close'])
            macd_hist = macd['MACDh_12_26_9'].iloc[-1]
            
            # --- Ⅱ. 波动率与风险 (Volatility) ---
            curr_vxn = vxn['Close'].iloc[-1]
            vxn_ma20 = ta.sma(vxn['Close'], 20).iloc[-1]
            vxn_trend = "扩张" if curr_vxn > vxn_ma20 * 1.05 else "正常"
            
            # 回撤计算
            ath = q['High'].max()
            dd_current = (current_price - ath) / ath * 100
            
            # 历史波动率 HV20
            q['log_ret'] = np.log(q['Close'] / q['Close'].shift(1))
            hv20 = q['log_ret'].rolling(20).std().iloc[-1] * np.sqrt(252) * 100
            
            # --- Ⅲ. 结构性指标 (Breadth) ---
            # 使用 QQQE/QQQ 比率作为宽度代理
            # 如果 QQQE 跑输 QQQ，说明全靠巨头拉升，宽度差
            q_pct = q['Close'].pct_change(20).iloc[-1]
            qe_pct = qe['Close'].pct_change(20).iloc[-1]
            breadth_health = "健康" if qe_pct >= q_pct - 0.02 else "恶化 (仅巨头拉升)"
            
            # 资金流 (MFI)
            mfi = ta.mfi(q['High'], q['Low'], q['Close'], q['Volume'], 14).iloc[-1]
            
            # --- Ⅳ. 宏观 (Macro) ---
            tnx_val = tnx['Close'].iloc[-1]
            tnx_ma = ta.sma(tnx['Close'], 20).iloc[-1]
            macro_pressure = "高压力" if tnx_val > tnx_ma and tnx_val > 4.0 else "中性"

            # ========================
            # 🧠 核心逻辑判定层
            # ========================
            
            # 1. 市场状态分类 (9 States)
            state = "Choppy"
            
            # 熊市逻辑
            if current_price < sma200 and current_price < sma50:
                if curr_vxn > 35: state = "Panic"
                else: state = "Bear Market"
            # 牛市逻辑
            elif current_price > sma200:
                if current_price > sma50 and current_price > sma20:
                    if bias_200 > 20 and rsi > 75: state = "Overheated"
                    elif adx > 25: state = "Strong Bull"
                    else: state = "Healthy Uptrend"
                elif current_price < sma20:
                    if current_price > sma50: state = "Shallow Pullback"
                    else: state = "Deep Pullback"
                elif current_price < sma50 and current_price > sma200:
                     state = "Repairing"
            else:
                state = "Choppy"

            # 2. 趋势健康评分 (0-100)
            health_score = 50
            if current_price > sma200: health_score += 20
            if current_price > sma50: health_score += 15
            if current_price > sma20: health_score += 10
            if macd_hist > 0: health_score += 5
            if mfi > 50: health_score += 5
            if breadth_health == "健康": health_score += 10
            if curr_vxn < 20: health_score += 10
            elif curr_vxn > 30: health_score -= 15
            if bias_200 > 20: health_score -= 10 # 泡沫扣分
            health_score = max(0, min(100, health_score))
            
            # 3. 趋势方向
            trend_dir = "震荡"
            if current_price > sma50: trend_dir = "上升"
            elif current_price < sma50: trend_dir = "下降"
            
            trend_str = "弱"
            if adx > 25: trend_str = "强"
            elif adx > 40: trend_str = "极强"

            # 4. 风险预测 (Heuristic)
            # 短期风险：看超买和波动率突刺
            rsi = ta.rsi(q['Close'], 14).iloc[-1]
            prob_short_drop = 20 # Base
            if rsi > 70: prob_short_drop += 30
            if curr_vxn > 25: prob_short_drop += 20
            if macd_hist < 0 and current_price > sma20: prob_short_drop += 10
            
            # 中期风险：看宏观和结构
            prob_med_crash = 10 # Base
            if bias_200 > 20: prob_med_crash += 20
            if breadth_health != "健康": prob_med_crash += 15
            if macro_pressure == "高压力": prob_med_crash += 15
            if current_price < sma50: prob_med_crash += 10

            # 5. 信号汇总
            signals = []
            if curr_vxn > 25: signals.append(f"⚠️ VXN 高位 ({curr_vxn:.1f})，恐慌情绪蔓延")
            if breadth_health != "健康": signals.append("⚠️ 市场宽度恶化，仅靠巨头支撑")
            if bias_200 > 20: signals.append("⚠️ 年线乖离过大，长期回调风险高")
            if macro_pressure == "高压力": signals.append("⚠️ 美债收益率上行，压制估值")
            if not signals and health_score > 70: signals.append("✅ 结构健康，适合持仓")
            if state == "Repairing": signals.append("🛠️ 震荡修复期，多空博弈")

            return {
                "State": state,
                "Score": health_score,
                "Trend_Dir": trend_dir,
                "Trend_Str": trend_str,
                "Volatility": vxn_trend,
                "Breadth": breadth_health,
                "Risk_Short": min(prob_short_drop, 99),
                "Risk_Med": min(prob_med_crash, 99),
                "Signals": signals,
                "Metrics": {
                    "Price": current_price,
                    "SMA50": sma50,
                    "SMA200": sma200,
                    "RSI": rsi,
                    "ADX": adx,
                    "VXN": curr_vxn,
                    "TNX": tnx_val,
                    "DD": dd_current
                }
            }

        except Exception as e:
            print(f"Pro Analysis Error: {e}")
            return None

    # --- 个股策略计算 (保留之前逻辑) ---
    def analyze_market_regime(self, ticker):
        """个股多周期状态分析"""
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy()
        try:
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            current_adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
            atr = ta.atr(df['High'], df['Low'], df['Close'], length=14).iloc[-1]
            price = df['Close'].iloc[-1]
            volatility_pct = (atr / price) * 100
            days = len(df)
            ret_1m = df['Close'].pct_change(21).iloc[-1] if days > 21 else 0
            ret_6m = df['Close'].pct_change(126).iloc[-1] if days > 126 else 0
            ret_1y = df['Close'].pct_change(252).iloc[-1] if days > 252 else 0
        except: return None

        def get_status_desc(ret):
            if ret >= 0.20: return "🚀 强势上涨"
            if ret >= 0.05: return "📈 稳步上涨"
            if ret <= -0.20: return "📉 暴风骤跌"
            if ret <= -0.05: return "💸 轻微回撤"
            return "🦀 横盘震荡"

        if ret_1m <= -0.15: recommendation = "SMA Reversal"
        elif ret_1m >= 0.20: recommendation = "SMA Cross"
        elif current_adx < 20: recommendation = "Bollinger"
        else: recommendation = "SMA Cross"

        return {
            "ADX": current_adx,
            "Volatility": volatility_pct,
            "1M": {"Val": ret_1m, "Desc": get_status_desc(ret_1m)},
            "6M": {"Val": ret_6m, "Desc": get_status_desc(ret_6m)},
            "1Y": {"Val": ret_1y, "Desc": get_status_desc(ret_1y)},
            "Recommendation": recommendation
        }

    def calculate_strategy(self, ticker, strategy_name, params):
        if ticker not in self.market_data: return None
        df = self.market_data[ticker].copy().sort_index()
        try:
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            df = pd.concat([df, adx_df], axis=1)
        except: df['ADX_14'] = 0
        try:
            df['Signal'] = 0 
            if strategy_name == "SMA Cross":
                s = params.get('short', 10); l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                prev_s = df['SMA_S'].shift(1); prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']; curr_l = df['SMA_L']
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                strong_trend = df['ADX_14'] > 20
                df.loc[golden_cross & strong_trend, 'Signal'] = 1
                df.loc[death_cross & strong_trend, 'Signal'] = -1
            elif strategy_name == "SMA Reversal":
                s = params.get('short', 10); l = params.get('long', 50)
                df['SMA_S'] = ta.sma(df['Close'], length=s)
                df['SMA_L'] = ta.sma(df['Close'], length=l)
                prev_s = df['SMA_S'].shift(1); prev_l = df['SMA_L'].shift(1)
                curr_s = df['SMA_S']; curr_l = df['SMA_L']
                golden_cross = (prev_s < prev_l) & (curr_s > curr_l)
                death_cross = (prev_s > prev_l) & (curr_s < curr_l)
                strong_trend = df['ADX_14'] > 20 
                df.loc[death_cross & strong_trend, 'Signal'] = 1
                df.loc[golden_cross & strong_trend, 'Signal'] = -1
            elif strategy_name == "RSI":
                length = params.get('length', 14)
                df['RSI'] = ta.rsi(df['Close'], length=length)
                df.loc[df['RSI'] < 30, 'Signal'] = 1
                df.loc[df['RSI'] > 70, 'Signal'] = -1
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

    def load_strategy_config(self):
        if os.path.exists(self.config_file):
            try: with open(self.config_file, 'r') as f: return json.load(f)
            except: return {}
        return {}

    def save_strategy_config(self, ticker, strategy):
        self.strategy_map[ticker] = strategy
        with open(self.config_file, 'w') as f: json.dump(self.strategy_map, f)
            
    def get_active_strategy(self, ticker, default_strategy):
        return self.strategy_map.get(ticker, default_strategy)
