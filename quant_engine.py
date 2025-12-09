import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import streamlit as st
import numpy as np
import json
import os

# Telegram 推送函数
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
        self.macro_cache = {}

    # --- 基础功能：数据加载 ---
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
                try: 
                    qty = float(row.get('Quantity', 0))
                except: 
                    qty = 0.0
                yf_ticker = self._map_symbol(symbol, str(row.get('Exchange', '')), name, str(row.get('Currency', '')))
                if 'nan' in yf_ticker.lower(): continue
                portfolio_list.append({"Symbol": symbol, "YF_Ticker": yf_ticker, "Name": name})
            self.portfolio = pd.DataFrame(portfolio_list)
            return True, f"✅ 已加载 {len(self.portfolio)} 个持仓"
        except Exception as e: 
            return False, f"❌ 解析失败: {str(e)}"

    def _map_symbol(self, symbol, exchange, name, currency):
        symbol_upper = symbol.upper()
        if symbol_upper == 'GOLD' and 'BARRICK' not in name.upper(): return 'GC=F'
        if '.' in symbol_upper and ('TO' in symbol_upper or 'NE' in symbol_upper): return symbol_upper
        if currency.upper() == 'CAD':
            if 'CDR' in name.upper() or 'NEO' in exchange: return f"{symbol_upper.replace('.', '-')}.NE"
            return f"{symbol_upper.replace('.', '-')}.TO"
        crypto_list = ['BTC', 'ETH', 'SOL', 'DOGE', 'ADA']
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
            tickers = "QQQ QQQE ^VXN ^TNX DX-Y.NYB"
            data = yf.download(tickers, period="2y", group_by='ticker', auto_adjust=True, threads=True)
            
            try:
                q = data['QQQ'].dropna()   # Price
                qe = data['QQQE'].dropna() # Breadth Proxy
                vxn = data['^VXN'].dropna() # Volatility
                tnx = data['^TNX'].dropna() # Macro Rates
                dxy = data['DX-Y.NYB'].dropna() if 'DX-Y.NYB' in data else pd.DataFrame() 
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
            curr_vxn = vxn['Close'].iloc[-1] if not vxn.empty else 20
            vxn_ma20_s = ta.sma(vxn['Close'], 20)
            vxn_ma20 = vxn_ma20_s.iloc[-1] if vxn_ma20_s is not None else curr_vxn
            vxn_trend = "扩张" if curr_vxn > vxn_ma20 * 1.05 else "正常"
            
            # 回撤计算
            ath = q['High'].max()
            dd_current = (current_price - ath) / ath * 100
            
            # --- Ⅲ. 结构性指标 (Breadth) ---
            # QQQE vs QQQ
            q_pct = q['Close'].pct_change(20).iloc[-1]
            qe_pct = qe['Close'].pct_change(20).iloc[-1]
            breadth_health = "健康" if qe_pct >= q_pct - 0.02 else "恶化 (仅巨头拉升)"
            
            # 资金流 (MFI)
            mfi = ta.mfi(q['High'], q['Low'], q['Close'], q['Volume'], 14).iloc[-1]
            
            # --- Ⅳ. 宏观 (Macro) ---
            tnx_val = tnx['Close'].iloc[-1] if not tnx.empty else 0
            
            # ========================
            # 🧠 核心逻辑判定层 (9 States)
            # ========================
            state = "Choppy"
            
            # 熊市逻辑
            if current_price < sma200 and current_price < sma50:
                if curr_vxn > 35: state = "Panic"
                else: state = "Bear Market"
            # 牛市逻辑
            elif current_price > sma200:
                if current_price > sma50 and current_price > sma20:
                    if bias_200 > 20 and mfi > 80: state = "Overheated"
                    elif adx > 25: state = "Strong Bull"
                    else: state = "Healthy Uptrend"
                elif current_price < sma20:
                    if current_price > sma50: state = "Shallow Pullback"
                    else: state = "Deep Pullback"
                elif current_price < sma50 and current_price > sma200:
                     state = "Repairing"
            
            # 趋势健康评分
            health_score = 50
            if current_price > sma200: health_score += 20
            if current_price > sma50: health_score += 15
            if current_price > sma20: health_score += 10
            if macd_hist > 0: health_score += 5
            if mfi > 50: health_score += 5
            if breadth_health == "健康": health_score += 10
            if curr_vxn < 20: health_score += 10
            elif curr_vxn > 30: health_score -= 15
            if bias_200 > 20: health_score -= 10
            health_score = max(0, min(100, health_score))
            
            trend_dir = "震荡"
            if current_price > sma50: trend_dir = "上升"
            elif current_price < sma50: trend_dir = "下降"
            
            trend_str = "弱"
            if adx > 25: trend_str = "强"
            elif adx > 40: trend_str = "极强"

            rsi = ta.rsi(q['Close'], 14).iloc[-1]
            prob_short_drop = 20
            if rsi > 70: prob_short_drop += 30
            if curr_vxn > 25: prob_short_drop += 20
            
            prob_med_crash = 10
            if bias_200 > 20: prob_med_crash += 20
            if breadth_health != "健康": prob_med_crash += 15
            if tnx_val > 4.5: prob_med_crash += 15
            if current_price < sma50: prob_med_crash += 10

            signals = []
            if curr_vxn > 25: signals.append(f"⚠️ VXN 高位 ({curr_vxn:.1f})，恐慌情绪蔓延")
            if breadth_health != "健康": signals.append("⚠️ 市场宽度恶化，仅靠巨头支撑")
            if bias_200 > 20: signals.append("⚠️ 年线乖离过大，长期回调风险高")
            if tnx_val > 4.2: signals.append("⚠️ 美债收益率上行，压制估值")
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

    # =========================================================
    # 🧠 个股深度诊断 (含 diagnose_stock_pro 方法)
    # =========================================================
    def diagnose_stock_pro(self, ticker):
        """
        个股 4 层权重模型诊断
        """
        # 1. 获取数据
        try:
            df = yf.download(ticker, period="2y", auto_adjust=True, progress=False)
            if df.empty or len(df) < 60: return None
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
        except: return None

        # 2. 计算核心指标
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        curr_price = close.iloc[-1]
        prev_price = close.iloc[-2]
        day_chg = (curr_price - prev_price) / prev_price * 100
        
        sma20 = ta.sma(close, 20).iloc[-1]
        sma50 = ta.sma(close, 50).iloc[-1]
        sma200 = ta.sma(close, 200).iloc[-1]
        
        rsi = ta.rsi(close, 14).iloc[-1]
        macd = ta.macd(close)
        macd_hist = macd['MACDh_12_26_9'].iloc[-1]
        prev_macd_hist = macd['MACDh_12_26_9'].iloc[-2]
        
        # 结构突破
        high_20 = high.rolling(20).max().iloc[-2]
        low_20 = low.rolling(20).min().iloc[-2]
        is_breakout = curr_price > high_20
        is_breakdown = curr_price < low_20
        
        # 风险
        bias_50 = (curr_price - sma50) / sma50 * 100
        atr = ta.atr(high, low, close, 14).iloc[-1]
        
        # 相对强弱
        ret_20 = close.pct_change(20).iloc[-1]
        rs_ratio = ret_20 # 简化版，不依赖外部缓存以防空值
        
        vol_ma = ta.sma(volume, 20).iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_ma if vol_ma > 0 else 1.0
        
        bb = ta.bbands(close, 20, 2.0)
        bb_lower = bb['BBL_20_2.0'].iloc[-1]
        
        # ==================== 判定树 ====================
        
        # Tier 1
        if day_chg < -9.0:
            return self._pack(10, "黑天鹅/重大事件冲击", "Tier 1", 
                              f"单日暴跌 {day_chg:.1f}%，恐慌抛售。", "🔴 暂停操作")
        if prev_price > sma200 and curr_price < sma200 and vol_ratio > 1.5:
            return self._pack(6, "跌破关键指标/趋势反转", "Tier 1",
                              "放量跌破牛熊分界线(SMA200)。", "✂️ 立即卖出")

        # Tier 2
        if vol_ratio > 2.5 and day_chg < 0:
             return self._pack(8, "成交量异常 (出货)", "Tier 2",
                               "巨量下跌，资金出逃。", "⚠️ 减仓/警告")
        if is_breakout and curr_price > sma50:
            return self._pack(1, "趋势强势上涨 (突破)", "Tier 2",
                              "突破20日新高，趋势向上。", "💪 积极持有")
        if is_breakdown and curr_price < sma50:
            return self._pack(6, "跌破关键结构", "Tier 2",
                              "跌破20日区间下沿。", "✂️ 减仓/做空")

        # Tier 3
        if bias_50 > 15:
            return self._pack(7, "上涨过度/泡沫信号", "Tier 3",
                              f"偏离50日线 {bias_50:.1f}%，乖离过大。", "💰 分批止盈")
        if curr_price < bb_lower and rsi < 25:
            return self._pack(12, "超卖情绪极端", "Tier 3",
                              "跌破布林下轨且RSI超卖。", "🛒 左侧博反弹")
        if curr_price > sma200 and rsi > 30 and macd_hist > prev_macd_hist and macd_hist < 0:
            return self._pack(4, "深度回调完成/企稳", "Tier 3",
                              "年线支撑有效，动能修复。", "➕ 尝试买入")

        # Tier 4
        if curr_price > sma50 and day_chg < 0:
            return self._pack(2, "短暂波动但趋势未变", "Tier 4",
                              "上升趋势中的正常回撤。", "🧘‍♂️ 持有不动")
        if abs(day_chg) < 1.0 and vol_ratio < 0.8:
            return self._pack(11, "盘整区间 (缩量)", "Tier 4",
                              "波动率收缩，方向不明。", "⏳ 等待方向")

        return self._pack(14, "市场风格切换期", "Tier 4", "无明显信号，跟随大盘。", "👀 观望")

    def _pack(self, code, name, tier, reason, action):
        return {
            "ID": code, "State": name, "Tier": tier, "Reason": reason, "Action": action
        }

    # 为了兼容旧代码保留的方法
    def analyze_market_regime(self, ticker):
        return self.diagnose_stock_pro(ticker) # 转发到新方法

    # 兼容 app.py 可能调用的 calculate_strategy
    def calculate_strategy(self, ticker, strategy_name, params):
        try:
            df = yf.download(ticker, period="1y", auto_adjust=True, progress=False)
            if df.empty: return None
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            
            df['SMA50'] = ta.sma(df['Close'], 50)
            df['SMA200'] = ta.sma(df['Close'], 200)
            
            # 简单的信号占位，主要功能在 diagnose_stock_pro
            df['Signal'] = 0
            return df
        except: return None

    def get_signal_status(self, df):
        return "N/A"

    def get_chart_data(self, ticker):
        try:
            df = yf.download(ticker, period="1y", auto_adjust=True, progress=False)
            if df.empty: return None
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            df['SMA50'] = ta.sma(df['Close'], 50)
            df['SMA200'] = ta.sma(df['Close'], 200)
            bb = ta.bbands(df['Close'], 20, 2)
            if bb is not None: df = pd.concat([df, bb], axis=1)
            return df
        except: return None

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
