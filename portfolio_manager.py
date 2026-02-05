class PortfolioManager:
    """
    资金管理与仓位建议系统 (双账户版)
    """

    def __init__(self):
        # 资金分配策略
        self.buy_ratio = 0.2  # 基础买入：每次使用可用现金的 20%
        self.sell_ratio = 0.2  # 基础卖出：每次卖出当前持仓的 20%
        self.min_trade_amount = 1000.0  # 最小建议交易额

    def calculate_suggestion(self, current_holdings, cash_balance, signal, ai_score, current_price):
        """
        [改进版] 增加基于价格的手数计算
        """
        # 防止价格为 0 或 None 导致除零错误
        if not current_price or current_price <= 0:
            return "观望", 0.0, "当前价格数据异常，无法计算。"

        if signal == "NEUTRAL":
            return "观望", 0.0, "技术面无明确方向，建议持仓不动。"

        amount = 0.0
        reason = ""
        action = "观望"
        confidence_multiplier = 1.0 + (abs(ai_score) / 20.0)

        # --- 买入逻辑 ---
        if signal == "BUY":
            action = "买入"
            if cash_balance < 100:
                return "买入", 0.0, "资金不足。"

            base_buy = max(cash_balance * self.buy_ratio, self.min_trade_amount)

            if ai_score > 0:
                suggested_amount = base_buy * confidence_multiplier
                reason = f"技术金叉 + AI看多({ai_score}分)，建议积极加仓。"
            else:
                if ai_score < -2:
                    suggested_amount = 0.0
                    reason = f"技术看涨但 AI 严重看空({ai_score}分)，建议空仓避险！"
                    return "观望", 0.0, reason  # 直接返回观望
                else:
                    suggested_amount = base_buy * 0.5
                    reason = f"技术看涨但 AI 微弱分歧({ai_score}分)，建议减半试仓。"

            amount = min(suggested_amount, cash_balance)

            # 计算大概能买多少克 (仅作展示)
            grams = amount / current_price
            reason += f" (约 {grams:.2f} 克)"

        # --- 卖出逻辑 ---
        elif signal == "SELL":
            action = "卖出"
            if current_holdings < 100:
                return "卖出", 0.0, "无持仓。"

            base_sell = max(current_holdings * self.sell_ratio, self.min_trade_amount)

            if ai_score < 0:
                suggested_amount = base_sell * confidence_multiplier * 1.5
                if ai_score < -7:
                    suggested_amount = current_holdings
                    reason = "恐慌清仓！"
                else:
                    reason = f"技术死叉 + AI看空({ai_score}分)，减仓。"
            else:
                suggested_amount = base_sell * 0.5
                reason = f"AI看多({ai_score}分)，少量止盈。"

            amount = min(suggested_amount, current_holdings)

            # === 新增：计算卖出克重 ===
            grams = amount / current_price
            reason += f" (约 {grams:.2f} 克)"

        return action, round(amount, 2), reason