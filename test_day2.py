from data_dispatcher import DataHandler
from strategy_engine import QuantalyticsEngine
import time
import pandas as pd
import akshare as ak


def test_day2_flow():
    print("=== Day 2 测试开始: 数据调度与策略联调 ===")

    # 1. 初始化数据处理器
    data_handler = DataHandler(max_len=100)  # 测试用，只存100条
    data_handler.initialize()  # 这一步会尝试联网拉历史数据

    # 2. 初始化策略引擎
    strategy_engine = QuantalyticsEngine()

    print("\n[测试] 开始模拟实时数据流 (按 Ctrl+C 停止)...")

    try:
        # 模拟运行 5 次循环 (实盘里是 while True)
        for i in range(5):
            print(f"\n--- 第 {i + 1} 次更新 ---")

            # A. 获取实时价格
            price = data_handler.fetch_realtime_price()
            if price is None:
                print("⚠️ 获取价格失败，使用模拟数据")
                price = 2030.0 + i * 0.5  # 模拟涨价

            print(f"当前国际金价: ¥{price}")

            # B. 更新缓冲区
            df = data_handler.update_tick(price)

            # C. 丢给策略引擎计算
            # 注意：如果热启动失败且 i 较小，df 长度可能不够，引擎会返回 '数据预热中'
            signal, reason, _ = strategy_engine.check_signal(df)

            print(f"缓冲区长度: {len(df)}")
            print(f"策略输出: [{signal}] -> {reason}")

            # 暂停一下，避免请求太快被封 IP
            time.sleep(2)

    except KeyboardInterrupt:
        print("测试手动停止")
    except Exception as e:
        print(f"测试出错: {e}")

    print("\n=== Day 2 测试结束 ===")


if __name__ == "__main__":
    test_day2_flow()
    # spot_quotations_sge_df = ak.spot_quotations_sge(symbol="Au99.99")
    # print(spot_quotations_sge_df)