import pandas as pd
import akshare as ak
import datetime
import time
import os  # 用于文件路径判断


class DataHandler:
    """
    数据调度员 (SGE 上海黄金交易所版 + 本地缓存增强版)
    """

    def __init__(self, max_len=1000):
        self.max_len = max_len
        self.symbol = "Au99.99"  # 支付宝黄金的标的物
        self.buffer = pd.DataFrame()

        # 定义缓存文件路径 (保存在当前目录下)
        self.cache_file = "gold_price_cache.csv"

    def _fetch_intraday_data(self):
        try:
            # 1. 获取数据
            df = ak.spot_quotations_sge(symbol=self.symbol)
            if df.empty:
                return pd.DataFrame()

            # 2. 重命名
            df.rename(columns={'现价': 'Close', '时间': 'Time_Str'}, inplace=True)

            # 3. 处理时间
            today_str = datetime.date.today().strftime('%Y-%m-%d')
            df['Datetime'] = pd.to_datetime(today_str + ' ' + df['Time_Str'].astype(str))

            # 4. 设置索引并排序
            df.set_index('Datetime', inplace=True)
            df.sort_index(inplace=True)

            # === 核心修复：构造“合成 K 线” ===

            # 1. Open (开盘价) = 上一分钟的 Close (收盘价)
            # 这样就能形成连续的 K 线，且能体现涨跌颜色
            df['Open'] = df['Close'].shift(1)

            # 2. 修正第一行 (第一行没有“上一分钟”，只能 Open=Close)
            df['Open'] = df['Open'].fillna(df['Close'])

            # 3. High (最高价) = max(Open, Close)
            # 因为我们只有头尾两个点，只能假设最高价就是这俩里面高的那个
            df['High'] = df[['Open', 'Close']].max(axis=1)

            # 4. Low (最低价) = min(Open, Close)
            df['Low'] = df[['Open', 'Close']].min(axis=1)

            # 5. Volume (成交量)
            df['Volume'] = 0

            return df[['Open', 'High', 'Low', 'Close', 'Volume']]

        except Exception as e:
            print(f"[DataHandler] 网络数据清洗出错: {e}")
            return pd.DataFrame()

    def _save_to_cache(self):
        """将当前缓冲区保存到 CSV"""
        if not self.buffer.empty:
            try:
                # 实时写入，覆盖旧文件
                self.buffer.to_csv(self.cache_file)
            except Exception as e:
                print(f"[DataHandler] 缓存写入失败: {e}")

    def _load_from_cache(self):
        """从 CSV 读取缓存数据"""
        if not os.path.exists(self.cache_file):
            return pd.DataFrame()

        try:
            print(f"[DataHandler] 正在尝试读取本地缓存: {self.cache_file} ...")
            # index_col=0 表示第一列是索引(时间)，parse_dates=True 自动解析时间格式
            df = pd.read_csv(self.cache_file, index_col=0, parse_dates=True)
            return df
        except Exception as e:
            print(f"[DataHandler] 缓存读取失败: {e}")
            return pd.DataFrame()

    def initialize(self):
        """
        智能启动流程：
        1. 尝试拉取网络数据 (Priority 1)
        2. 如果失败，尝试加载本地缓存 (Priority 2)
        3. 无论哪种成功，都保存/更新一次缓存
        """
        print(f"[DataHandler] 正在初始化 {self.symbol} 数据...")

        # 1. 尝试网络请求
        df = self._fetch_intraday_data()

        if not df.empty:
            print("[DataHandler] ✅ 网络热启动成功!")
            self.buffer = df.tail(self.max_len)
            # 既然拿到了新数据，立刻刷新缓存
            self._save_to_cache()
        else:
            print("[DataHandler] ⚠️ 网络获取失败 (休市或网络异常)，转入断点续传模式...")
            # 2. 尝试读取缓存
            cached_df = self._load_from_cache()
            if not cached_df.empty:
                print(f"[DataHandler] ✅ 本地缓存加载成功! 恢复了 {len(cached_df)} 条数据。")
                print(f"[DataHandler] 缓存最新时间: {cached_df.index[-1]}")
                self.buffer = cached_df.tail(self.max_len)
            else:
                print("[DataHandler] ❌ 本地缓存也为空。进入冷启动等待模式。")

        if not self.buffer.empty:
            price = self.buffer.iloc[-1]['Close']
            print(f"[DataHandler] 当前基准价格: ¥{price}/克")

    def fetch_realtime_price(self):
        """获取最新价格 (网络优先)"""
        df = self._fetch_intraday_data()
        if not df.empty:
            return float(df.iloc[-1]['Close'])

        # 如果网络断了，暂时不返回缓存的旧价格，以免误导交易
        # 这里返回 None，UI 线程会暂时休眠等待网络恢复
        return None

    def fetch_long_history(self, days=30):
        """[参数优化专用] 获取长历史数据 (Au0 期货)"""
        try:
            print(f"[DataHandler] 正在拉取过去 {days} 天的历史数据 (Au0)...")
            df = ak.futures_zh_minute_sina(symbol="au0", period="15")
            df.rename(columns={'datetime': 'Datetime', 'open': 'Open', 'high': 'High',
                               'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            df.set_index('Datetime', inplace=True)
            cols = ['Open', 'High', 'Low', 'Close', 'Volume']
            df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
            return df
        except Exception as e:
            print(f"[DataHandler] 历史数据获取失败: {e}")
            return pd.DataFrame()

    def update_tick(self, current_price):
        """
        更新 K 线 + 自动保存缓存
        """
        if current_price is None:
            return self.buffer

        now = datetime.datetime.now().replace(second=0, microsecond=0)

        # 1. 新的一分钟 -> 创建新行
        if self.buffer.empty or self.buffer.index[-1] != now:
            new_row = pd.DataFrame({
                'Open': [current_price],
                'High': [current_price],
                'Low': [current_price],
                'Close': [current_price],
                'Volume': [0]
            }, index=[now])

            self.buffer = pd.concat([self.buffer, new_row])

            # 保持缓冲区大小
            if len(self.buffer) > self.max_len:
                self.buffer = self.buffer.iloc[-self.max_len:]

        # 2. 同一分钟 -> 更新当前 K 线
        else:
            last_idx = self.buffer.index[-1]
            if current_price > self.buffer.at[last_idx, 'High']:
                self.buffer.at[last_idx, 'High'] = current_price
            if current_price < self.buffer.at[last_idx, 'Low']:
                self.buffer.at[last_idx, 'Low'] = current_price
            self.buffer.at[last_idx, 'Close'] = current_price

        # === 核心修改：每次更新数据后，立刻写入硬盘 ===
        # 考虑到只有几百行数据，写入速度极快(毫秒级)，不会阻塞 UI
        self._save_to_cache()

        return self.buffer