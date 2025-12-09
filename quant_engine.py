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

    # --- 核心：宏观数据获取 ---
    def fetch_macro_context(self):
        """获取大盘环境：纳指、VIX、美债"""
        try:
            data = yf.download("QQQ ^VXN ^TNX", period="1y", group_by='ticker', auto_adjust=True, threads=True)
            
            try:
                qqq = data['QQQ'].dropna()
                vxn = data['^VXN'].dropna()
                tnx = data['^TNX'].dropna() if '^TNX' in data else pd.DataFrame()
            except KeyError: return None
            
            if qqq.empty: return None

            curr_vxn = vxn['Close'].iloc[-1] if not vxn.empty else 20
            
            # 安全获取 SMA
            sma50_series = ta.sma(qqq['Close'], 50)
            if sma50_series is None or sma50_series.empty:
                qqq_sma50 = 0
            else:
                qqq_sma50 = sma50_series.iloc[-1]
                
            qqq_price = qqq['Close'].iloc[-1]
            
            market_trend = "Bull" if qqq_price > qqq_sma50 else "Bear"
            fear_level = "High" if curr_vxn > 28 else ("Low" if curr_vxn < 18 else "Normal")
            
            # 计算 QQQ 的近期收益，用于个股 RS 对比
            qqq_ret_20 = qqq['Close'].pct_change(20).iloc[-1]
            
            self.macro_cache = {
                "Market_Trend": market_trend,
                "Fear_Level": fear_level,
                "VXN": curr_vxn,
                "TNX": tnx['Close'].iloc[-1] if not tnx.empty else 4.0,
                "QQQ_Ret_20": qqq_ret_20
            }
            return self.macro_cache
        except Exception as e:
            print(f"Macro fetch error: {e}")
            return None

    # =========================================================
    # 🧠 分层权重诊断模型 (Pro Logic)
    # =========================================================
    def diagnose_stock_pro(self, ticker):
        """
        基于 4 层优先级体系判断 15 种市场状态
        """
        # 1. 获取数据
        try:
            df = yf.download(ticker, period="2y", auto_adjust=True, progress=False)
            if df.empty: return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) < 60:
                return self._pack_result(15, "数据不足 (IPO)", "Tier 4", "上市时间不足。", "👀 观望")
        except: return None

        # 2. 计算核心指标
        close = df['Close']
        high = df['High']
        low = df['Low']
        volume = df['Volume']
        
        curr_price = close.iloc[-1]
        prev_price = close.iloc[-2]
        day_chg = (curr_price - prev_price) / prev_price * 100
        
        # 均线
        sma20 = ta.sma(close, 20).iloc[-1]
        sma50 = ta.sma(close, 50).iloc[-1]
        sma200_s = ta.sma(close, 200)
        has_sma200 = sma200_s is not None and not np.isnan(sma200_s.iloc[-1])
        sma200 = sma200_s.iloc[-1] if has_sma200 else 0
        
        # 动量
        rsi = ta.rsi(close, 14).iloc[-1]
        macd = ta.macd(close)
        macd_hist = macd['MACDh_12_26_9'].iloc[-1]
        prev_macd_hist = macd['MACDh_12_26_9'].iloc[-2]
        
        # 结构
        high_20 = high.rolling(20).max().iloc[-2]
        low_20 = low.rolling(20).min().iloc[-2]
        is_breakout = curr_price > high_20
        is_breakdown = curr_price < low_20
        
        # 风险
        bias_50 = (curr_price - sma50) / sma50 * 100
        atr = ta.atr(high, low, close, 14).iloc[-1]
        
        # 相对强弱 (RS)
        qqq_ret = self.macro_cache.get("QQQ_Ret_20", 0.0)
        ret_20 = close.pct_change(20).iloc[-1]
        rs_ratio = ret_20 - qqq_ret 
        
        # 成交量
        vol_ma = ta.sma(volume, 20).iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_ma if vol_ma > 0 else 1.0
        
        # 布林带
        bb = ta.bbands(close, 20, 2.0)
        bb_lower = bb['BBL_20_2.0'].iloc[-1]
        
        # 宏观环境
        macro_fear = self.macro_cache.get("Fear_Level", "Normal")
        
        # ==================== 判定树 ====================
        
        # --- Tier 1 ---
        if day_chg < -9.0:
            return self._pack_result(10, "黑天鹅/重大事件冲击", "Tier 1", 
                              f"单日暴跌 {day_chg:.1f}%，恐慌抛售。", "🔴 暂停操作")
        if has_sma200 and prev_price > sma200 and curr_price < sma200 and vol_ratio > 1.5:
            return self._pack_result(6, "跌破关键指标/趋势反转", "Tier 1",
                              "放量跌破牛熊分界线(SMA200)。", "✂️ 立即卖出")
        if macro_fear == "High" and atr/curr_price > 0.05:
             return self._pack_result(9, "高波动风险/系统性恐慌", "Tier 1",
                               "大盘恐慌 (VXN高) 且个股波动率极高。", "👀 观望/清仓")

        # --- Tier 2 ---
        if vol_ratio > 2.5 and day_chg < 0:
             return self._pack_result(8, "成交量异常 (出货)", "Tier 2",
                               "巨量下跌，资金出逃。", "⚠️ 减仓/警告")
        if is_breakout and rs_ratio > 0.05 and curr_price > sma50:
            return self._pack_result(1, "趋势强势上涨 (RS增强)", "Tier 2",
                              f"突破新高，且跑赢大盘 {rs_ratio*100:.1f}%。", "💪 积极持有")
        if is_breakdown and curr_price < sma50:
            return self._pack_result(6, "跌破关键结构", "Tier 2",
                              "跌破20日区间下沿。", "✂️ 减仓/做空")

        # --- Tier 3 ---
        if bias_50 > 15:
            return self._pack_result(7, "上涨过度/泡沫信号", "Tier 3",
                              f"偏离50日线 {bias_50:.1f}%，乖离过大。", "💰 分批止盈")
        if curr_price < bb_lower and rsi < 25:
            return self._pack_result(12, "超卖情绪极端", "Tier 3",
                              "跌破布林下轨且RSI超卖。", "🛒 左侧博反弹")
        if has_sma200 and curr_price > sma200 and rsi > 30 and macd_hist > prev_macd_hist and macd_hist < 0:
            return self._pack_result(4, "深度回调完成/企稳", "Tier 3",
                              "年线支撑有效，动能修复。", "➕ 尝试买入")
        if macd_hist < 0 and prev_macd_hist > 0:
             return self._pack_result(13, "动能转弱 (MACD死叉)", "Tier 3",
                               "上涨动能耗尽，MACD高位死叉。", "👀 观望/减仓")

        # --- Tier 4 ---
        if curr_price > sma50 and day_chg < 0:
            return self._pack_result(2, "短暂波动但趋势未变", "Tier 4",
                              "上升趋势中的正常回撤。", "🧘‍♂️ 持有不动")
        if abs(day_chg) < 1.0 and vol_ratio < 0.8:
            return self._pack_result(11, "盘整区间 (缩量)", "Tier 4",
                              "波动率收缩，方向不明。", "⏳ 等待方向")

        return self._pack_result(14, "市场风格切换期", "Tier 4", "无明显信号，跟随大盘。", "👀 观望")

    def _pack_result(self, code, name, tier, reason, action):
        return {
            "ID": code, "State": name, "Tier": tier, "Reason": reason, "Action": action
        }

    # 绘图数据
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

    # Config (修复了单行try/with语法)
    def load_strategy_config(self):
        if os.path.exists(self.config_file):
            try: 
                with open(self.config_file, 'r') as f: 
                    return json.load(f)
            except: 
                return {}
        return {}

    def save_strategy_config(self, ticker, strategy):
        self.strategy_map[ticker] = strategy
        with open(self.config_file, 'w') as f: 
            json.dump(self.strategy_map, f)
            
    def get_active_strategy(self, ticker, default_strategy):
        return self.strategy_map.get(ticker, default_strategy)
