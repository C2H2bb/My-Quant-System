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
    except Exception: pass 

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
                try: qty = float(row.get('Quantity', 0))
                except: qty = 0.0
                yf_ticker = self._map_symbol(symbol, str(row.get('Exchange', '')), name, str(row.get('Currency', '')))
                if 'nan' in yf_ticker.lower(): continue
                portfolio_list.append({"Symbol": symbol, "YF_Ticker": yf_ticker, "Name": name})
            self.portfolio = pd.DataFrame(portfolio_list)
            return True, f"✅ 已加载 {len(self.portfolio)} 个持仓"
        except Exception as e: return False, f"❌ 解析失败: {str(e)}"

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
            
            self.macro_cache = {
                "Market_Trend": market_trend,
                "Fear_Level": fear_level,
                "VXN": curr_vxn,
                "TNX": tnx['Close'].iloc[-1] if not tnx.empty else 4.0
            }
            return self.macro_cache
        except Exception as e:
            print(f"Macro fetch error: {e}")
            return None

    # =========================================================
    # 🧠 分层权重诊断模型 (修复版)
    # =========================================================
    def diagnose_stock(self, ticker):
        """
        基于 4 层优先级体系判断 15 种市场状态
        """
        # 1. 获取数据
        try:
            df = yf.download(ticker, period="2y", auto_adjust=True, progress=False) # 下载2年以确保有 SMA200
            if df.empty: return None
            
            # --- 修复：处理 yfinance 可能返回的 MultiIndex ---
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            # --- 修复：数据长度检查 ---
            if len(df) < 20:
                return self._pack_result(15, "数据不足 (IPO)", "Tier 4", 
                                         f"上市不足20天 ({len(df)}天)，无法分析。", "👀 观望")
        except: return None

        # 2. 计算关键指标
        close = df['Close']
        curr_price = close.iloc[-1]
        prev_price = close.iloc[-2]
        day_change_pct = (curr_price - prev_price) / prev_price * 100
        
        # 安全计算技术指标
        try:
            sma20_s = ta.sma(close, 20)
            sma50_s = ta.sma(close, 50)
            sma200_s = ta.sma(close, 200)
            
            # 如果数据不够算 200日线，sma200_s 可能是 None
            sma20 = sma20_s.iloc[-1] if sma20_s is not None else curr_price
            sma50 = sma50_s.iloc[-1] if sma50_s is not None else curr_price
            
            # 针对次新股的特殊处理
            has_sma200 = sma200_s is not None and not np.isnan(sma200_s.iloc[-1])
            sma200 = sma200_s.iloc[-1] if has_sma200 else 0
            
            rsi_s = ta.rsi(close, 14)
            rsi = rsi_s.iloc[-1] if rsi_s is not None else 50
            
            adx_df = ta.adx(df['High'], df['Low'], close, 14)
            adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
            
            vol = df['Volume']
            vol_ma_s = ta.sma(vol, 20)
            vol_ma = vol_ma_s.iloc[-1] if vol_ma_s is not None else 0
            vol_ratio = vol.iloc[-1] / vol_ma if vol_ma > 0 else 1.0
            
        except Exception as e:
            print(f"Indicator calc error: {e}")
            return self._pack_result(15, "计算异常", "Tier 4", "指标计算失败，数据可能不完整。", "👀 观望")
        
        # 宏观环境 (从缓存读取)
        macro = self.macro_cache if self.macro_cache else {"Market_Trend": "Bull", "Fear_Level": "Normal", "VXN": 20}

        # 3. 🛡️ 优先级判定树 (Decision Tree)
        
        # --- 第一层：最高优先级 (权重 100) ---
        
        # 状态 10: 黑天鹅
        if day_change_pct < -8.0:
            return self._pack_result(10, "黑天鹅/重大事件冲击", "Tier 1", 
                                     f"单日暴跌 {day_change_pct:.1f}%，远超正常波动范围。", 
                                     "🔴 暂停操作，等待稳定")
        
        # 状态 6: 趋势彻底反转 (有效跌破年线)
        if has_sma200 and prev_price > sma200 and curr_price < sma200 and day_change_pct < -2:
            return self._pack_result(6, "跌破关键指标/趋势反转", "Tier 1",
                                     "放量跌破 200 日年线，牛熊分界线失守。",
                                     "✂️ 立即减仓或卖出")

        # 状态 5: 风险偏好增强
        if day_change_pct > 6.0 and vol_ratio > 1.5:
            return self._pack_result(5, "风险偏好增强", "Tier 1",
                                     f"单日放量大涨 {day_change_pct:.1f}%，资金抢筹迹象明显。",
                                     "🔥 积极持有")

        # --- 第二层：高优先级 (权重 70-90) ---
        
        # 状态 9: 高波动风险
        if macro['Fear_Level'] == "High":
            return self._pack_result(9, "高波动风险", "Tier 2",
                                     f"纳指恐慌指数 (VXN) 高达 {macro['VXN']:.1f}，系统性风险高。",
                                     "👀 观望，暂不操作")
        
        # 状态 8: 成交量异常
        if day_change_pct < -3 and vol_ratio > 2.0:
            return self._pack_result(8, "成交量异常/恐慌抛售", "Tier 2",
                                     "下跌伴随 2 倍以上巨量，恐慌盘涌出。",
                                     "⚠️ 警告/减仓")

        # 状态 1: 趋势强势上涨 (需要有年线数据)
        if has_sma200 and curr_price > sma20 > sma50 > sma200 and macro['Market_Trend'] == "Bull":
            return self._pack_result(1, "趋势强势上涨", "Tier 2",
                                     "均线完美多头排列，且大盘环境向好。",
                                     "💪 继续持有")

        # --- 第三层：中优先级 (权重 40-60) ---
        
        # 状态 7: 上涨过度/泡沫
        if rsi > 78:
            return self._pack_result(7, "上涨过度/泡沫信号", "Tier 3",
                                     f"RSI 高达 {rsi:.1f}，进入严重超买区，短线回调压力大。",
                                     "💰 分批止盈")
        
        # 状态 12: 超卖情绪极端
        if rsi < 25:
            return self._pack_result(12, "超卖情绪极端", "Tier 3",
                                     f"RSI 降至 {rsi:.1f}，空头情绪释放过度，随时可能反抽。",
                                     "👀 警惕短线反转/轻仓博反弹")

        # 状态 3: 关键支撑反弹
        dist_sma50 = abs(curr_price - sma50) / sma50
        if dist_sma50 < 0.02 and day_change_pct > 0:
            return self._pack_result(3, "关键支撑反弹企稳", "Tier 3",
                                     "回踩 50 日均线附近获得支撑并收阳。",
                                     "➕ 可小规模加仓")

        # 状态 11: 盘整区间
        if adx < 20:
            return self._pack_result(11, "盘整区间，无趋势", "Tier 3",
                                     f"ADX 仅为 {adx:.1f}，显示当前缺乏明确趋势。",
                                     "⏳ 等待突破")

        # --- 第四层：最低优先级 (权重 10-30) ---
        
        # 状态 2: 短暂波动
        if curr_price < sma20 and curr_price > sma50:
            return self._pack_result(2, "短暂波动但趋势未变", "Tier 4",
                                     "跌破 20 日线但 50 日线趋势仍向上，属于良性回调。",
                                     "🧘‍♂️ 不要操作/持有")
        
        # 状态 4: 深度回调完成
        if has_sma200 and curr_price < sma50 and curr_price > sma200 and rsi > 40:
            return self._pack_result(4, "深度回调/尝试筑底", "Tier 4",
                                     "位于年线上方震荡，指标低位修复中。",
                                     "🛒 底部信号明确后买入")

        # 状态 14: 市场风格切换期/弱势
        if has_sma200 and curr_price < sma200:
            return self._pack_result(14, "市场风格切换期/弱势", "Tier 4",
                                     "运行于长期均线下方，走势偏弱。",
                                     "👀 观望")
        
        # 默认
        return self._pack_result(13, "关键支撑/阻力临界", "Tier 4",
                                 "当前方向不明，处于多空平衡点。",
                                 "👀 观望")

    def _pack_result(self, code, name, tier, reason, action):
        return {
            "ID": code,
            "State": name,
            "Tier": tier,
            "Reason": reason,
            "Action": action
        }

    # 用于绘图的数据获取
    def get_chart_data(self, ticker):
        try:
            df = yf.download(ticker, period="1y", auto_adjust=True, progress=False)
            if df.empty: return None
            # 修复：处理 MultiIndex
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            df['SMA50'] = ta.sma(df['Close'], 50)
            df['SMA200'] = ta.sma(df['Close'], 200)
            return df
        except: return None

    # --- Config ---
    def load_strategy_config(self):
        if os.path.exists(self.config_file):
            try: with open(self.config_file, 'r') as f: return json.load(f)
            except: return {}
        return {}
