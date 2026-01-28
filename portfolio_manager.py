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
        核心算法：根据持仓、现金、技术信号、AI分数计算操作金额

        :param current_holdings: 当前黄金持仓 (元)
        :param cash_balance: 剩余可支配现金 (元)
        :param signal: 技术面信号 "BUY", "SELL", "NEUTRAL"
        :param ai_score: AI 情绪分 (-10 到 10)
        :param current_price: 当前金价
        :return: (建议方向, 建议金额, 理由)
        """

        if signal == "NEUTRAL":
            return "观望", 0.0, "技术面无明确方向，建议持仓不动。"

        amount = 0.0
        reason = ""
        action = "观望"

        # 动态调整系数 (AI 信心加成 0.5 ~ 1.5)
        # AI分数越高，买入/卖出力度越大
        confidence_multiplier = 1.0 + (abs(ai_score) / 20.0)

        # --- 买入逻辑 ---
        if signal == "BUY":
            action = "买入"

            # 检查是否有钱
            if cash_balance < 100:
                return "买入", 0.0, "技术面发出买入信号，但您的可用资金不足。"

            # 基础买入额：可用现金的 20% (或者最低起购额)
            base_buy = max(cash_balance * self.buy_ratio, self.min_trade_amount)

            # AI 修正
            if ai_score > 0:
                suggested_amount = base_buy * confidence_multiplier
                reason = f"技术金叉 + AI看多({ai_score}分)，建议积极加仓。"
            else:
                suggested_amount = base_buy * 0.5
                reason = f"技术看涨但AI看空({ai_score}分)，建议小额试仓。"

            # 资金上限约束 (不能买超了)
            amount = min(suggested_amount, cash_balance)

            # 如果算出来的建议额比现金还多(虽然min限制了，但为了提示)
            if suggested_amount > cash_balance:
                reason += " (受限于资金，已建议全额买入)"

        # --- 卖出逻辑 ---
        elif signal == "SELL":
            action = "卖出"

            # 检查是否有货
            if current_holdings < 100:
                return "卖出", 0.0, "技术面发出卖出信号，但您当前无持仓。"

            # 基础卖出额：持仓的 20%
            base_sell = max(current_holdings * self.sell_ratio, self.min_trade_amount)

            # AI 修正
            if ai_score < 0:
                suggested_amount = base_sell * confidence_multiplier * 1.5
                if ai_score < -7:
                    suggested_amount = current_holdings  # 极度恐慌，建议清仓
                    reason = "技术死叉 + AI极度看空，建议清仓止损！"
                else:
                    reason = f"技术死叉 + AI看空({ai_score}分)，建议大幅减仓。"
            else:
                suggested_amount = base_sell * 0.5
                reason = f"技术回调但AI看多({ai_score}分)，建议少量止盈。"

            # 持仓上限约束 (不能卖超了)
            amount = min(suggested_amount, current_holdings)

        return action, round(amount, 2), reason