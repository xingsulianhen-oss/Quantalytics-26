import pandas as pd
import pandas_ta as ta


class QuantalyticsEngine:
    """
    Quantalytics-26 的实时版引擎
    从 backtesting 框架剥离，专用于实盘信号计算
    """

    def __init__(self, params=None):
        # 默认参数 (对应 strategy.py 的默认值)
        self.params = {
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
            "vol_period": 20,  # 波动率周期
            "vol_ma_period": 50  # 波动率均值周期 (用于过滤死鱼行情)
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
        # pandas_ta 列名格式通常为 BBL_20_2.0
        bbl_col = [c for c in bb.columns if c.startswith('BBL')][0]
        bbu_col = [c for c in bb.columns if c.startswith('BBU')][0]
        data['BBL'] = bb[bbl_col]
        data['BBU'] = bb[bbu_col]

        # 3. MACD
        macd = ta.macd(data['Close'], fast=self.params['macd_fast'], slow=self.params['macd_slow'],
                       signal=self.params['macd_signal'])
        macd_col = [c for c in macd.columns if c.startswith('MACD_') and 's' not in c and 'h' not in c][0]
        macds_col = [c for c in macd.columns if c.startswith('MACDs_')][0]
        data['MACD'] = macd[macd_col]
        data['MACD_SIG'] = macd[macds_col]

        # 4. SMA Trend
        data['SMA_F'] = ta.sma(data['Close'], length=self.params['sma_fast'])
        data['SMA_S'] = ta.sma(data['Close'], length=self.params['sma_slow'])

        # 5. Volatility (修复原版缺失的逻辑)
        # 波动率计算：收益率的标准差
        data['pct_change'] = data['Close'].pct_change()
        data['vol'] = data['pct_change'].rolling(window=self.params['vol_period']).std()
        # 波动率的均线 (用于判断当前是否活跃)
        data['vol_ma'] = data['vol'].rolling(window=self.params['vol_ma_period']).mean()

        return data

    def check_signal(self, df_raw):
        """
        核心判断逻辑
        返回: (Signal_Type, Reason_String, Current_Price)
        Signal_Type: "BUY", "SELL", "NEUTRAL"
        """
        # 确保数据量足够
        min_len = max(self.params['sma_slow'], self.params['vol_ma_period']) + 10
        if len(df_raw) < min_len:
            return "NEUTRAL", "数据预热中...", 0.0

        # 计算指标
        df = self.calculate_indicators(df_raw)
        curr = df.iloc[-1]

        # --- 信号逻辑复刻 (基于 strategy.py) ---

        # 1. 趋势判断
        sma_bull = curr['SMA_F'] > curr['SMA_S']
        sma_bear = curr['SMA_F'] < curr['SMA_S']

        # 2. 动量判断
        macd_bull = curr['MACD'] > curr['MACD_SIG']
        macd_bear = curr['MACD'] < curr['MACD_SIG']

        # 3. 均值回归 (超买超卖)
        rsi_oversold = curr['RSI'] < self.params['rsi_os']
        rsi_overbought = curr['RSI'] > self.params['rsi_ob']
        bb_lower_touch = curr['Close'] <= curr['BBL']
        bb_upper_touch = curr['Close'] >= curr['BBU']

        # 4. 波动率过滤 (原版遗漏的关键逻辑!)
        # 只有当前波动率 > 50% 的平均波动率时，才允许交易
        # 避免死鱼行情下的假突破
        is_volatile = curr['vol'] > (0.5 * curr['vol_ma'])

        signal = "NEUTRAL"
        reasons = []

        if not is_volatile:
            return "NEUTRAL", f"波动率过低 (Vol:{curr['vol']:.5f} < Threshold)", curr['Close']

        # --- 买入逻辑 ---
        # 趋势向上 + (RSI超卖 或 触及下轨) + MACD金叉
        if sma_bull and (rsi_oversold or bb_lower_touch) and macd_bull:
            signal = "BUY"
            reasons.append("趋势向上")
            if rsi_oversold: reasons.append(f"RSI超卖({curr['RSI']:.1f})")
            if bb_lower_touch: reasons.append("触布林下轨")
            reasons.append("MACD动能增强")

        # --- 卖出逻辑 ---
        # 趋势向下 + (RSI超买 或 触及上轨) + MACD死叉
        elif sma_bear and (rsi_overbought or bb_upper_touch) and macd_bear:
            signal = "SELL"
            reasons.append("趋势向下")
            if rsi_overbought: reasons.append(f"RSI超买({curr['RSI']:.1f})")
            if bb_upper_touch: reasons.append("触布林上轨")
            reasons.append("MACD动能减弱")

        return signal, " + ".join(reasons), curr['Close']