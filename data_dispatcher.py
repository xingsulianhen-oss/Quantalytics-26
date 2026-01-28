import pandas as pd
import akshare as ak
import datetime
import time


class DataHandler:
    """
    数据调度员 (SGE 上海黄金交易所版)
    使用 Au99.99 数据，完美对标支付宝黄金理财
    """

    def __init__(self, max_len=1000):
        self.max_len = max_len
        self.symbol = "Au99.99"  # 支付宝黄金的标的物
        self.buffer = pd.DataFrame()

    def _fetch_intraday_data(self):
        try:
            # 1. 获取数据
            df = ak.spot_quotations_sge(symbol=self.symbol)
            if df.empty:
                return pd.DataFrame()

            # 2. 重命名
            df.rename(columns={'现价': 'Close', '时间': 'Time_Str'}, inplace=True)

            # --- 关键修正点 ---
            # 报错原因：df['Time_Str'] 是 datetime.time 类型，不能直接和字符串相加
            # 解决方法：加一个 .astype(str) 把它强制转成字符串
            today_str = datetime.date.today().strftime('%Y-%m-%d')
            df['Datetime'] = pd.to_datetime(today_str + ' ' + df['Time_Str'].astype(str))

            # 4. 设置索引
            df.set_index('Datetime', inplace=True)

            # 5. 补全其他 OHLC 列
            df['Open'] = df['Close']
            df['High'] = df['Close']
            df['Low'] = df['Close']
            df['Volume'] = 0

            # 6. 按时间排序 (防止数据乱序)
            df.sort_index(inplace=True)

            return df[['Open', 'High', 'Low', 'Close', 'Volume']]

        except Exception as e:
            print(f"[DataHandler] 数据清洗出错: {e}")
            # 打印一下出错时的数据长什么样，方便调试
            # print(df.head())
            return pd.DataFrame()

    def initialize(self):
        """热启动：直接拉取今天的历史分时"""
        print(f"[DataHandler] 正在从上海黄金交易所拉取 {self.symbol} 数据...")
        df = self._fetch_intraday_data()

        if not df.empty:
            self.buffer = df.tail(self.max_len)
            print(f"[DataHandler] 热启动成功! 已加载 {len(self.buffer)} 条 {self.symbol} 分时数据。")
            print(f"[DataHandler] 最新时间: {self.buffer.index[-1]}")
            print(f"[DataHandler] 最新价格: ¥{self.buffer.iloc[-1]['Close']}/克")
        else:
            print("[DataHandler] ⚠️ 未获取到数据 (可能是休市或网络原因)，进入冷启动模式。")

    def fetch_realtime_price(self):
        """
        获取最新价格
        策略：直接复用 _fetch_intraday_data 取最后一条
        虽然略显浪费（拉了全表只取最后一条），但 akshare 这个接口本身就是全量返回的，
        且数据量很小（几百行），性能损耗可忽略，稳定性最高。
        """
        df = self._fetch_intraday_data()
        if not df.empty:
            return float(df.iloc[-1]['Close'])
        return None

    def update_tick(self, current_price):
        """更新缓冲区"""
        if current_price is None:
            return self.buffer

        now = datetime.datetime.now()

        # 构造新行
        new_row = pd.DataFrame({
            'Open': [current_price],
            'High': [current_price],
            'Low': [current_price],
            'Close': [current_price],
            'Volume': [0]
        }, index=[now])

        if self.buffer.empty:
            self.buffer = new_row
        else:
            self.buffer = pd.concat([self.buffer, new_row])

        # 保持缓冲区大小
        if len(self.buffer) > self.max_len:
            self.buffer = self.buffer.iloc[-self.max_len:]

        return self.buffer