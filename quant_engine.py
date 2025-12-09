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
        self.macro_cache = {} # 存储宏观数据

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
            qqq_sma50 = ta.sma(qqq['Close'], 50).iloc[-1]
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
            if df.empty or len(df) < 60: return None
            
            # MultiIndex 清洗
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
        except: return None

        # 2. 计算核心指标库
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
        sma200 = ta.sma(close, 200).iloc[-1]
        
        # 动量
        rsi = ta.rsi(close, 14).iloc[-1]
        macd = ta.macd(close)
        macd_hist = macd['MACDh_12_26_9'].iloc[-1]
        prev_macd_hist = macd['MACDh_12_26_9'].iloc[-2]
        
        # 结构与突破
        high_20 = high.rolling(20).max().iloc[-2] # 昨日的20日高点
        low_20 = low.rolling(20).min().iloc[-2]   # 昨日的20日低点
        is_breakout = curr_price > high_20
        is_breakdown = curr_price < low_20
        
        # 风险与泡沫
        bias_50 = (curr_price - sma50) / sma50 * 100 # 乖离率
        atr = ta.atr(high, low, close, 14).iloc[-1]
        
        # 相对强弱 (RS)
        ret_20 = close.pct_change(20).iloc[-1]
        qqq_ret = self.macro_cache.get("QQQ_Ret_20", 0.0)
        rs_ratio = ret_20 - qqq_ret # 简单超额收益
        rs_status = "强于大盘" if rs_ratio > 0 else "弱于大盘"
        
        # 成交量
        vol_ma = ta.sma(volume, 20).iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_ma if vol_ma > 0 else 1.0
        
        # 布林带
        bb = ta.bbands(close, 20, 2.0)
        bb_lower = bb['BBL_20_2.0'].iloc[-1]
        bb_upper = bb['BBU_20_2.0'].iloc[-1]

        # 宏观读取
        macro_fear = self.macro_cache.get("Fear_Level", "Normal")
        
        # =====================================================
        # 🛡️ 优先级判定树 (Tiered Logic)
        # =====================================================
        
        # --- Tier 1: 最高优先级 (权重 100 - 一票否决) ---
        
        # 10. 黑天鹅
        if day_chg < -9.0:
            return self._pack(10, "黑天鹅/重大事件冲击", "Tier 1", 
                              f"单日暴跌 {day_chg:.1f}%，恐慌抛售。", "🔴 暂停操作")
        
        # 6. 趋势彻底反转 (有效跌破年线 + 放量)
        if prev_price > sma200 and curr_price < sma200 and vol_ratio > 1.5:
            return self._pack(6, "跌破关键指标/趋势反转", "Tier 1",
                              "放量跌破牛熊分界线(SMA200)，趋势转空。", "✂️ 立即卖出/止损")
        
        # 9. 高波动风险 (个股ATR剧烈 + 大盘恐慌)
        if macro_fear == "High" and atr/curr_price > 0.05:
             return self._pack(9, "高波动风险/系统性恐慌", "Tier 1",
                               "大盘恐慌 (VXN高) 且个股波动率极高。", "👀 观望/清仓避险")

        # --- Tier 2: 高优先级 (权重 70-90 - 结构与量能) ---
        
        # 8. 成交量异常 (放量滞涨 或 放量杀跌)
        if vol_ratio > 2.5 and day_chg < 0:
             return self._pack(8, "成交量异常 (出货)", "Tier 2",
                               "巨量下跌，主力资金可能在出逃。", "⚠️ 减仓/警告")
        
        # 1. 趋势强势上涨 (突破结构 + RS强)
        if is_breakout and rs_ratio > 0.05 and curr_price > sma50:
            return self._pack(1, "趋势强势上涨 (RS增强)", "Tier 2",
                              "突破20日新高，且显著强于大盘 (RS+)。", "💪 积极持有/加仓")
        
        # 6. 跌破结构 (新低)
        if is_breakdown and curr_price < sma50:
            return self._pack(6, "跌破关键结构 (破位)", "Tier 2",
                              "跌破20日区间下沿，结构恶化。", "✂️ 减仓/做空")

        # --- Tier 3: 中优先级 (权重 40-60 - 指标与乖离) ---
        
        # 7. 泡沫信号 (乖离率过大)
        if bias_50 > 15:
            return self._pack(7, "上涨过度/泡沫信号", "Tier 3",
                              f"偏离50日线 {bias_50:.1f}%，乖离率过高，均值回归风险大。", "💰 分批止盈")
        
        # 12. 超卖极端 (布林下轨 + RSI低)
        if curr_price < bb_lower and rsi < 25:
            return self._pack(12, "超卖情绪极端 (反弹一触即发)", "Tier 3",
                              "跌破布林下轨且RSI超卖，短期有修复需求。", "🛒 左侧博反弹")
        
        # 4. 深度回调完成 (底部信号)
        # 逻辑：价格在年线上，RSI金叉或回到30以上，MACD绿柱缩短
        if curr_price > sma200 and rsi > 30 and macd_hist > prev_macd_hist and macd_hist < 0:
            return self._pack(4, "深度回调完成/企稳", "Tier 3",
                              "年线支撑有效，MACD动能修复，回调可能结束。", "➕ 尝试买入")

        # 13. 关键支撑/阻力 (EMA死叉/金叉临界)
        # 简单用 MACD 死叉代表趋势转弱
        if macd_hist < 0 and prev_macd_hist > 0:
             return self._pack(13, "动能转弱 (MACD死叉)", "Tier 3",
                               "上涨动能耗尽，MACD高位死叉。", "👀 观望/减仓")

        # --- Tier 4: 低优先级 (权重 10-30 - 日常波动) ---
        
        # 2. 短暂波动
        if curr_price > sma50 and day_chg < 0:
            return self._pack(2, "短暂波动但趋势未变", "Tier 4",
                              "上升趋势中的正常回撤 (未破SMA50)。", "🧘‍♂️ 持有不动")
        
        # 11. 盘整区间
        if abs(day_chg) < 1.0 and vol_ratio < 0.8:
            return self._pack(11, "盘整区间 (缩量)", "Tier 4",
                              "波动率收缩，成交清淡，方向不明。", "⏳ 等待方向")

        # 默认
        return self._pack(14, "市场风格切换期", "Tier 4", "无明显信号，跟随大盘波动。", "👀 观望")

    def _pack(self, code, name, tier, reason, action):
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
            # 增加布林带用于展示
            bb = ta.bbands(df['Close'], 20, 2)
            if bb is not None:
                df = pd.concat([df, bb], axis=1)
            return df
        except: return None

    # Config
    def load_strategy_config(self):
        if os.path.exists(self.config_file):
            try: 
                with open(self.config_file, 'r') as f: return json.load(f)
            except: return {}
        return {}
