from backtesting import Backtest
from strategy import AdaptiveMomentumReversion  # 引用原版策略类用于回测
import pandas as pd


def run_optimization(df_history):
    """
    运行滚动优化
    输入: 最近一段时间的历史数据 (DataFrame, 必须包含 Open, High, Low, Close)
    输出: 最优参数字典 (dict)
    """
    print(f"正在对 {len(df_history)} 条 K 线数据进行参数优化...")

    # 初始化回测引擎
    # 资金和佣金设为常数即可，我们要的是参数排名
    bt = Backtest(df_history, AdaptiveMomentumReversion, cash=100000, commission=0.00002)

    # 运行优化 (参考 optimize_params.py 的逻辑)
    # 这里我们优化最核心的三个参数：RSI周期，SMA慢线，布林周期
    # 范围可以根据你的经验微调
    stats, heatmap = bt.optimize(
        rsi_period=range(10, 24, 2),  # 尝试 10 到 24
        sma_slow=range(20, 60, 5),  # 尝试 20 到 60
        bb_period=range(15, 30, 5),  # 尝试 15 到 30
        maximize='Sharpe Ratio',  # 目标：最大化夏普比率
        return_heatmap=True,
        max_tries=100  # 限制尝试次数，防止算太久
    )

    # 提取最优参数
    best_params = {
        'rsi_period': stats._strategy.rsi_period,
        'sma_slow': stats._strategy.sma_slow,
        'bb_period': stats._strategy.bb_period,
        # 其他参数保持默认，或者你也加入优化列表
    }

    print(f"优化完成! 最佳 Sharpe: {stats['Sharpe Ratio']:.2f}")
    print(f"推荐参数: {best_params}")

    return best_params


# 简单的测试桩
if __name__ == "__main__":
    # 读取你本地的 2024 数据文件测试一下
    # 注意：确保你的路径是正确的
    try:
        df = pd.read_csv('data/XAUUSD_M1/DAT_MT_XAUUSD_M1_2024.csv')
        # 模拟：只取最后 5000 条数据进行优化（模拟最近3个月）
        recent_data = df.tail(5000).copy()

        # 必须把时间列处理好供 backtesting 使用
        recent_data['Datetime'] = pd.to_datetime(recent_data['Date'] + ' ' + recent_data['Time'])
        recent_data.set_index('Datetime', inplace=True)
        recent_data.drop(['Date', 'Time'], axis=1, inplace=True)

        params = run_optimization(recent_data)
    except FileNotFoundError:
        print("未找到测试数据文件，请检查路径。")