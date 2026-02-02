from PyQt6.QtCore import QThread, pyqtSignal
from backtesting import Backtest
from strategy import AdaptiveMomentumReversion  # 确保目录下有 strategy.py
from data_dispatcher import DataHandler
import pandas as pd
import numpy as np


class OptimizerWorker(QThread):
    """
    参数优化工作线程
    负责：拉取历史数据 -> 运行回测优化 -> 返回最优参数
    """
    # 信号：优化完成，传回一个字典 (best_params)
    optimization_finished = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.data_handler = DataHandler()  # 用于拉取历史数据

    def run(self):
        print("[Optimizer] 启动参数进化程序...")

        # 1. 拉取数据 (例如过去 60 天的 15分钟线)
        df = self.data_handler.fetch_long_history(days=60)

        if df.empty or len(df) < 500:
            print("[Optimizer] ⚠️ 历史数据不足，跳过优化。")
            return

        # 2. 运行优化逻辑
        try:
            best_params = self._run_optimization_logic(df)
            # 3. 发送结果
            self.optimization_finished.emit(best_params)
        except Exception as e:
            print(f"[Optimizer] 优化过程出错: {e}")

    def _run_optimization_logic(self, df):
        """
        核心回测优化逻辑 (保留了你原来的逻辑)
        """
        print(f"[Optimizer] 正在对 {len(df)} 条 K 线进行暴力计算...")

        # 初始化回测引擎
        # 必须指定 cash 和 commission，否则 backtesting 可能会报错
        bt = Backtest(df, AdaptiveMomentumReversion, cash=100000, commission=0.00002)

        def robust_sharpe(stats):
            # 1. 如果交易次数为 0，直接给负分
            if stats['# Trades'] == 0:
                return -1.0

            # 2. 如果夏普比率是 nan，给 0 分
            if np.isnan(stats['Sharpe Ratio']):
                return 0.0

            # 3. 正常返回
            return stats['Sharpe Ratio']

        # 运行优化
        # 注意：这里的参数名必须和 strategy.py 里的变量名一致
        stats = bt.optimize(
            rsi_period=range(10, 25, 2),  # 10, 12... 24
            sma_slow=range(20, 60, 5),  # 20, 25... 55
            bb_period=range(15, 30, 3),  # 15, 18... 27
            maximize=robust_sharpe,  # 目标：最大化夏普比率
            return_heatmap=False,  # 不需要热力图，只要结果
            max_tries=200  # 限制尝试次数
        )

        # 提取最优参数
        # stats._strategy 是回测中表现最好的那个策略实例
        best_params = {
            'rsi_period': stats._strategy.rsi_period,
            'sma_slow': stats._strategy.sma_slow,
            'bb_period': stats._strategy.bb_period,
        }

        print(f"[Optimizer] ✅ 优化完成! 最佳夏普比率: {stats['Sharpe Ratio']:.2f}")
        print(f"[Optimizer] 推荐参数: {best_params}")

        return best_params