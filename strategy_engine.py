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
        # 考虑到 ATR 和 Vol_MA，需要更长的预热期
        max_period = max(
            self.params['sma_slow'],
            self.params['vol_ma_period'],
            self.params['atr_period']
        )
        min_len = max_period + 10

        if len(df_raw) < min_len:
            # 返回原始数据 df_raw 以便绘图
            return "NEUTRAL", "数据预热中...", df_raw

        # 2. 计算指标
        df = self.calculate_indicators(df_raw)
        curr = df.iloc[-1]

        # --- 信号逻辑复刻 ---

        # 趋势判断
        sma_bull = curr['SMA_F'] > curr['SMA_S']
        sma_bear = curr['SMA_F'] < curr['SMA_S']

        # 动量判断 (需处理 NaN)
        macd_val = curr.get('MACD', 0)
        macd_sig = curr.get('MACD_SIG', 0)
        macd_bull = macd_val > macd_sig
        macd_bear = macd_val < macd_sig

        # 均值回归 (超买超卖)
        # 使用 .get 以防指标计算失败
        rsi = curr.get('RSI', 50)
        bbl = curr.get('BBL', 0)
        bbu = curr.get('BBU', 999999)

        rsi_oversold = rsi < self.params['rsi_os']
        rsi_overbought = rsi > self.params['rsi_ob']
        bb_lower_touch = curr['Close'] <= bbl
        bb_upper_touch = curr['Close'] >= bbu

        # 波动率过滤
        vol = curr.get('vol', 0)
        vol_ma = curr.get('vol_ma', 0)

        # 保护逻辑：如果 vol_ma 为 NaN (数据刚够算指标但不够算均线)，暂不强行过滤
        if pd.isna(vol_ma) or vol_ma == 0:
            is_volatile = True
        else:
            is_volatile = vol > (0.5 * vol_ma)

        signal = "NEUTRAL"
        reasons = []

        if not is_volatile:
            return "NEUTRAL", f"波动率过低 (Vol:{vol:.5f})", df

        # --- 买入逻辑 ---
        if sma_bull and (rsi_oversold or bb_lower_touch) and macd_bull:
            signal = "BUY"
            reasons.append("趋势向上")
            if rsi_oversold: reasons.append(f"RSI超卖({rsi:.1f})")
            if bb_lower_touch: reasons.append("触布林下轨")
            reasons.append("MACD动能增强")

        # --- 卖出逻辑 ---
        elif sma_bear and (rsi_overbought or bb_upper_touch) and macd_bear:
            signal = "SELL"
            reasons.append("趋势向下")
            if rsi_overbought: reasons.append(f"RSI超买({rsi:.1f})")
            if bb_upper_touch: reasons.append("触布林上轨")
            reasons.append("MACD动能减弱")

        reason_str = " + ".join(reasons) if reasons else "无明确信号"
        return signal, reason_str, df