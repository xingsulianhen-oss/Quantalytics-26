import pandas as pd
import pandas_ta as ta


class QuantalyticsEngine:
    """
    Quantalytics-26 的实时版引擎
    从 backtesting 框架剥离，专用于实盘信号计算
    """

    def __init__(self, params=None):
        # 默认参数 (与 strategy.py 完全对齐)
        self.params = {
            # --- 核心指标参数 ---
            "rsi_period": 14,
            "rsi_ob": 70,
            "rsi_os": 30,
            "bb_period": 20,
            "bb_std": 2.0,
            "sma_fast": 10,
            "sma_slow": 30,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,

            # --- 波动率与风控参数 (新增对齐) ---
            "vol_period": 20,  # 波动率周期
            "vol_ma_period": 50,  # 波动率过滤均线
            "atr_period": 14,  # [新增] ATR 周期

            # --- 资金管理参数 (虽然实盘由 UI 托管，但保留参数以兼容优化器) ---
            "risk_pct": 0.02,
            "sl_atr_mult": 1.8,
            "tp_atr_mult": 4.5,
            "max_trades_per_day": 15
        }

        # 如果传入了动态参数 (比如来自优化器)，覆盖默认值
        if params:
            self.params.update(params)

    def update_params(self, new_params):
        """用于动态适应机制：接收新参数"""
        print(f"[Engine] 更新策略参数: {new_params}")
        self.params.update(new_params)

    def calculate_indicators(self, df):
        """计算所有技术指标"""
        # 必须拷贝，避免污染原始数据
        data = df.copy()

        # 1. RSI
        data['RSI'] = ta.rsi(data['Close'], length=self.params['rsi_period'])

        # 2. Bollinger Bands
        bb = ta.bbands(data['Close'], length=self.params['bb_period'], std=self.params['bb_std'])
        # 处理可能的空值情况
        if bb is not None:
            # pandas_ta 列名格式通常为 BBL_20_2.0
            bbl_cols = [c for c in bb.columns if c.startswith('BBL')]
            bbu_cols = [c for c in bb.columns if c.startswith('BBU')]
            if bbl_cols and bbu_cols:
                data['BBL'] = bb[bbl_cols[0]]
                data['BBU'] = bb[bbu_cols[0]]

        # 3. MACD
        macd = ta.macd(data['Close'], fast=self.params['macd_fast'], slow=self.params['macd_slow'],
                       signal=self.params['macd_signal'])
        if macd is not None:
            macd_col = [c for c in macd.columns if c.startswith('MACD_') and 's' not in c and 'h' not in c][0]
            macds_col = [c for c in macd.columns if c.startswith('MACDs_')][0]
            data['MACD'] = macd[macd_col]
            data['MACD_SIG'] = macd[macds_col]

        # 4. SMA Trend
        data['SMA_F'] = ta.sma(data['Close'], length=self.params['sma_fast'])
        data['SMA_S'] = ta.sma(data['Close'], length=self.params['sma_slow'])

        # 5. Volatility (波动率)
        data['pct_change'] = data['Close'].pct_change()
        data['vol'] = data['pct_change'].rolling(window=self.params['vol_period']).std()
        data['vol_ma'] = data['vol'].rolling(window=self.params['vol_ma_period']).mean()

        # 6. [新增] ATR 计算
        # 这对 UI 显示波动幅度和后续风控很有用
        data['ATR'] = ta.atr(data['High'], data['Low'], data['Close'], length=self.params['atr_period'])

        return data

    def check_signal(self, df_raw):
        # 1. 确保数据量足够
        max_period = max(
            self.params['sma_slow'],
            self.params['vol_ma_period'],
            self.params['atr_period']
        )
        min_len = max_period + 2

        if len(df_raw) < min_len:
            return "NEUTRAL", "数据预热中...", df_raw

        # 2. 计算指标
        df = self.calculate_indicators(df_raw)
        curr = df.iloc[-1]
        prev = df.iloc[-2]  # 以此判断交叉

        # --- 信号逻辑优化 (放宽版) ---

        # A. 趋势判断 (维持原样)
        sma_bull = curr['SMA_F'] > curr['SMA_S']
        sma_bear = curr['SMA_F'] < curr['SMA_S']

        # B. 均值回归 (RSI / 布林带)
        # 优化：RSI 不需要非得等到极端值(30/70)，适当放宽，或者是从极端值回归时介入
        rsi = curr.get('RSI', 50)
        bbl = curr.get('BBL', 0)
        bbu = curr.get('BBU', 999999)

        # 只要碰到轨道，或者 RSI 接近极端区域
        rsi_buy_zone = rsi < (self.params['rsi_os'] + 5)  # 例如 < 35
        rsi_sell_zone = rsi > (self.params['rsi_ob'] - 5)  # 例如 > 65

        bb_lower_touch = curr['Close'] <= bbl * 1.0005  # 给万分之5的容错
        bb_upper_touch = curr['Close'] >= bbu * 0.9995

        # C. 波动率过滤 (已移除！)
        # 原因：Au99.99 分钟线有很多僵尸时间，波动率过滤会导致长期无信号
        # vol = curr.get('vol', 0)
        # vol_ma = curr.get('vol_ma', 0)
        # is_volatile = ... (已注释)

        signal = "NEUTRAL"
        reasons = []

        # --- 买入逻辑 (放宽) ---
        # 逻辑：趋势向上 + (RSI低位 或 踩到布林下轨)
        # 移除了 MACD 的强制要求，把它作为加分项
        if sma_bull and (rsi_buy_zone or bb_lower_touch):
            signal = "BUY"
            reasons.append("多头趋势回调")
            if rsi_buy_zone: reasons.append(f"RSI低位({rsi:.1f})")
            if bb_lower_touch: reasons.append("触布林下轨")

            # MACD 仅作为参考理由
            if curr.get('MACD', 0) > curr.get('MACD_SIG', 0):
                reasons.append("MACD金叉")

        # --- 卖出逻辑 (放宽) ---
        elif sma_bear and (rsi_sell_zone or bb_upper_touch):
            signal = "SELL"
            reasons.append("空头趋势反弹")
            if rsi_sell_zone: reasons.append(f"RSI高位({rsi:.1f})")
            if bb_upper_touch: reasons.append("触布林上轨")

        reason_str = " + ".join(reasons) if reasons else "等待机会"
        return signal, reason_str, df