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

    def fetch_long_history(self, days=30):
        """
        [新增] 获取长历史数据用于参数优化
        使用新浪期货接口 (Au0) 获取最近的 15分钟 K线数据
        """
        try:
            print(f"[DataHandler] 正在拉取过去 {days} 天的历史数据 (Au0 期货代理)...")
            # period='15' 代表15分钟线，适合做周级别的参数优化，数据量适中
            df = ak.futures_zh_minute_sina(symbol="au0", period="15")

            # 数据清洗
            df.rename(columns={'datetime': 'Datetime', 'open': 'Open', 'high': 'High',
                               'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            df.set_index('Datetime', inplace=True)

            # 确保是数值类型
            cols = ['Open', 'High', 'Low', 'Close', 'Volume']
            df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')

            return df
        except Exception as e:
            print(f"[DataHandler] 历史数据获取失败: {e}")
            return pd.DataFrame()

    def update_tick(self, current_price):
        """
        更新缓冲区 (Tick 合成 K 线逻辑)
        """
        if current_price is None:
            return self.buffer

        # 获取当前分钟的时间戳 (秒数置零)
        now = datetime.datetime.now().replace(second=0, microsecond=0)

        # 1. 如果缓冲区为空，或者当前时间是新的一分钟 -> 创建新 K 线
        if self.buffer.empty or self.buffer.index[-1] != now:
            new_row = pd.DataFrame({
                'Open': [current_price],
                'High': [current_price],
                'Low': [current_price],
                'Close': [current_price],
                'Volume': [0]  # 暂时没有成交量数据
            }, index=[now])

            self.buffer = pd.concat([self.buffer, new_row])

            # 保持缓冲区大小
            if len(self.buffer) > self.max_len:
                self.buffer = self.buffer.iloc[-self.max_len:]

        # 2. 如果还在同一分钟内 -> 更新当前 K 线 (High/Low/Close)
        else:
            # 获取最后一行的数据引用
            last_idx = self.buffer.index[-1]

            # 更新最高价: 如果现价更高，就更新 High
            if current_price > self.buffer.at[last_idx, 'High']:
                self.buffer.at[last_idx, 'High'] = current_price

            # 更新最低价: 如果现价更低，就更新 Low
            if current_price < self.buffer.at[last_idx, 'Low']:
                self.buffer.at[last_idx, 'Low'] = current_price

            # 更新收盘价: 总是更新为最新价
            self.buffer.at[last_idx, 'Close'] = current_price

        return self.buffer