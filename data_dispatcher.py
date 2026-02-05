import pandas as pd
import akshare as ak
import datetime
import time
import os
import atexit
import requests

# === Selenium 依赖 ===
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class DataHandler:
    """
    数据调度员 (终极融合版：历史底仓 + 实时爬虫驻留)
    """

    def __init__(self, max_len=3000):
        # 扩容缓冲区至 3000，以容纳 15分钟级别的月度历史数据
        self.max_len = max_len
        self.symbol = "Au99.99"
        self.buffer = pd.DataFrame()
        self.cache_file = "gold_price_cache.csv"
        self.last_request_time = 0

        # === 爬虫专用状态 ===
        self.driver = None
        self.crawler_url = "https://finance.sina.com.cn/futures/quotes/AUTD.shtml"

        # 注册退出时的清理函数
        atexit.register(self.close_driver)

    def _init_driver(self):
        """启动驻留式隐形浏览器"""
        if self.driver is not None:
            return

        print("[DataHandler] 正在启动后台 Edge 浏览器引擎...")
        try:
            edge_options = Options()
            edge_options.add_argument("--headless")  # 无头模式 (生产环境建议开启)
            edge_options.add_argument("--disable-gpu")
            edge_options.add_argument("--no-sandbox")
            edge_options.add_argument("--log-level=3")

            current_dir = os.path.dirname(os.path.abspath(__file__))
            driver_path = os.path.join(current_dir, "msedgedriver.exe")

            if not os.path.exists(driver_path):
                print(f"❌ 严重错误: 未找到驱动 {driver_path}")
                return

            service = Service(executable_path=driver_path)
            self.driver = webdriver.Edge(service=service, options=edge_options)

            # 预加载页面
            self.driver.get(self.crawler_url)
            print("[DataHandler] ✅ 爬虫引擎启动就绪")

        except Exception as e:
            print(f"[DataHandler] ❌ 爬虫启动失败: {e}")
            self.driver = None

    def close_driver(self):
        if self.driver:
            try:
                self.driver.quit()
                self.driver = None
            except:
                pass

    def _fetch_from_crawler(self):
        """
        渠道C: Selenium 网页爬虫 (修正版：span.real-price)
        """
        if self.driver is None:
            self._init_driver()
            if self.driver is None: return None

        try:
            wait = WebDriverWait(self.driver, 5)

            # 刷新以确保数据最新
            self.driver.refresh()

            # 使用您验证成功的 CSS Selector
            element = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "span.real-price")
            ))
            price_text = element.text

            if not price_text: return None

            price_text = price_text.replace(',', '')
            price = float(price_text)

            # 使用系统时间对齐 K 线
            dt = datetime.datetime.now().replace(microsecond=0)

            return price, dt

        except Exception as e:
            # print(f"[Crawler] 读取微小波动: {e}")
            return None

    def _fetch_intraday_data(self):
        """获取实时数据 (优先爬虫，备用SGE)"""
        now = time.time()
        if now - self.last_request_time < 2:
            time.sleep(1)
        self.last_request_time = time.time()

        price = None
        dt = None
        source_used = "None"

        # 1. 爬虫 (Selenium)
        res_crawler = self._fetch_from_crawler()
        if res_crawler:
            price, dt = res_crawler
            source_used = "Selenium"

        # 2. SGE 官方 (备用)
        if price is None:
            try:
                df = ak.spot_quotations_sge(symbol=self.symbol)
                if df is not None and not df.empty and '最新价' in df.columns:
                    price = float(df['最新价'].iloc[0])
                    dt = datetime.datetime.now()
                    source_used = "SGE API"
            except:
                pass

        if price is None:
            return pd.DataFrame(), source_used

        try:
            df = pd.DataFrame({
                'Open': [price],
                'High': [price],
                'Low': [price],
                'Close': [price],
                'Volume': [0]
            }, index=[dt])
            return df, source_used
        except Exception:
            return pd.DataFrame(), source_used

    def fetch_long_history(self, days=30):
        """
        获取历史数据
        注意：使用黄金期货主力(Au0)的15分钟线作为历史底仓
        原因：spot_hist_sge 是日线数据，无法用于分钟级技术分析。
             Au0 15分钟线既提供了足够长的历史视窗，又能与实时分钟线平滑衔接。
        """
        try:
            # period="15" -> 15分钟级别
            df = ak.futures_zh_minute_sina(symbol="au0", period="1")
            df.rename(columns={'datetime': 'Datetime', 'open': 'Open', 'high': 'High',
                               'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            df.set_index('Datetime', inplace=True)
            cols = ['Open', 'High', 'Low', 'Close', 'Volume']
            df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
            return df
        except Exception:
            return pd.DataFrame()

    def update_tick(self, current_price):
        """更新 K 线 (核心：向前平移时间轴)"""
        if current_price is None: return self.buffer

        now = datetime.datetime.now().replace(second=0, microsecond=0)

        # 1. 新的一分钟 -> 追加新行
        if self.buffer.empty or self.buffer.index[-1] != now:
            open_price = current_price
            if not self.buffer.empty: open_price = self.buffer.iloc[-1]['Close']

            new_row = pd.DataFrame({
                'Open': [open_price],
                'High': [current_price],
                'Low': [current_price],
                'Close': [current_price],
                'Volume': [0]
            }, index=[now])

            self.buffer = pd.concat([self.buffer, new_row])

            # 保持缓冲区长度，实现"向前平移" (挤掉最旧的数据)
            if len(self.buffer) > self.max_len:
                self.buffer = self.buffer.iloc[-self.max_len:]

        # 2. 同一分钟 -> 更新 High/Low/Close
        else:
            last_idx = self.buffer.index[-1]
            self.buffer.at[last_idx, 'Close'] = current_price
            if current_price > self.buffer.at[last_idx, 'High']:
                self.buffer.at[last_idx, 'High'] = current_price
            if current_price < self.buffer.at[last_idx, 'Low']:
                self.buffer.at[last_idx, 'Low'] = current_price

        self._save_to_cache()
        return self.buffer

    def _save_to_cache(self):
        if not self.buffer.empty:
            try:
                self.buffer.to_csv(self.cache_file)
            except:
                pass

    def _load_from_cache(self):
        if not os.path.exists(self.cache_file): return pd.DataFrame()
        try:
            return pd.read_csv(self.cache_file, index_col=0, parse_dates=True)
        except:
            return pd.DataFrame()

    def initialize(self):
        """
        初始化流程 (修复版：先加载历史，再接实时)
        """
        print(f"[DataHandler] 正在初始化数据引擎 ({self.symbol})...")
        self._init_driver()  # 预启动爬虫

        # === 步骤1: 加载历史底仓 (解决只有几个点的问题) ===
        # 优先从 akshare 获取近期的 15分钟 K 线，构建完美的技术分析底图
        try:
            print("[DataHandler] 正在构建历史 K 线底仓 (基于 Au0 期货)...")
            history_df = self.fetch_long_history(days=30)

            if not history_df.empty:
                self.buffer = history_df
                print(f"[DataHandler] ✅ 历史数据构建完成: {len(self.buffer)} 根 K 线")
            else:
                # 如果没网，尝试读本地缓存
                print("[DataHandler] ⚠️ 在线历史获取失败，加载本地缓存...")
                self.buffer = self._load_from_cache()

        except Exception as e:
            print(f"[DataHandler] 历史初始化异常: {e}")
            self.buffer = self._load_from_cache()

        # === 步骤2: 获取当前实时价格 ===
        realtime_df, src = self._fetch_intraday_data()

        if not realtime_df.empty:
            print(f"[DataHandler] ✅ 实时连接成功! 来源: {src}")
            current_price = realtime_df.iloc[-1]['Close']

            # === 步骤3: 无缝拼接 ===
            # 将最新的实时价格，通过 update_tick 追加到历史数据的末尾
            # 这样界面上就会显示：[长长的历史曲线] --- [跳动的实时点]
            self.update_tick(current_price)
        else:
            print("[DataHandler] ⚠️ 实时数据暂不可用，等待下一轮更新...")

        # 再次保存，确保下次启动有数据
        self._save_to_cache()

    def fetch_realtime_price(self):
        df, src = self._fetch_intraday_data()
        if not df.empty:
            return float(df.iloc[-1]['Close'])
        return None


if __name__ == "__main__":
    handler = DataHandler()
    handler.initialize()
    print(f"当前缓冲区长度: {len(handler.buffer)}")
    print("测试 5 次连续抓取:")
    for i in range(5):
        p = handler.fetch_realtime_price()
        print(f"[{i + 1}] {p}")
        handler.update_tick(p)
        time.sleep(2)
    handler.close_driver()