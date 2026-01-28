import pandas as pd
from strategy_engine import QuantalyticsEngine

# 1. 创建引擎
engine = QuantalyticsEngine()

# 2. 模拟一些数据 (或者读取你的 CSV)
print("正在加载数据...")
try:
    df = pd.read_csv(
        'data/XAUUSD_M1/DAT_MT_XAUUSD_M1_2024.csv',
        names=['Date', 'Time', 'Open', 'High', 'Low', 'Close', 'Volume'],
    )
    # 构造标准 DataFrame
    df['Datetime'] = pd.to_datetime(df['Date'] + ' ' + df['Time'], format='%Y.%m.%d %H:%M')
    df.set_index('Datetime', inplace=True)

    # 3. 喂入前 500 条数据进行计算
    sample_data = df.head(500)

    print("正在计算信号...")
    signal, reason, price = engine.check_signal(sample_data)

    print("-" * 30)
    print(f"当前价格: {price}")
    print(f"AI 引擎信号: {signal}")
    print(f"信号理由: {reason}")
    print("-" * 30)

    # 4. 测试动态参数更新
    new_params = {'rsi_period': 20, 'sma_slow': 50}
    engine.update_params(new_params)
    print("参数已更新，引擎现在的 RSI 周期是:", engine.params['rsi_period'])

except Exception as e:
    print(f"出错了: {e}")