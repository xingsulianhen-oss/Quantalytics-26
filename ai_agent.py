import os
import datetime
import feedparser
from google import genai
from PyQt6.QtCore import QThread, pyqtSignal
import akshare as ak

# ================= 配置区域 =================
# 1. 在这里填入你的 Gemini API Key
GEMINI_API_KEY = ""

# 2. 模型选择 (建议使用 Flash 系列，速度快且便宜)
MODEL_NAME = "models/gemini-2.5-flash"


# ===========================================

class AIAgent(QThread):
    # 信号定义不变，但第三个参数 list 内部结构变了
    ai_advice_signal = pyqtSignal(str, int, list)

    def __init__(self):
        super().__init__()
        self.is_running = True
        self.last_analysis_time = None
        self.client = None
        self.last_news_fingerprint = ""

        if GEMINI_API_KEY != "你的_GEMINI_API_KEY_粘贴在这里":
            try:
                self.client = genai.Client(api_key=GEMINI_API_KEY)
            except Exception as e:
                print(f"[AI Agent] Client 初始化失败: {e}")

    def _fetch_financial_news(self):
        """获取新闻 + 链接"""
        news_data = []  # 结构: [{'title':Str, 'link':Str}]

        # 1. 尝试 RSS (Investing.com) - 这是带链接的最好源
        try:
            rss_url = "https://cn.investing.com/rss/news_285.rss"
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:5]:
                news_data.append({
                    'title': entry.title,
                    'link': entry.link  # RSS 自带链接
                })
        except Exception as e:
            print(f"[AI Agent] RSS 失败: {e}")

        # 2. 如果 RSS 挂了，用 AkShare 备选
        if not news_data:
            try:
                # 东方财富个股新闻 (紫金矿业)
                df = ak.stock_news_em(symbol="601899")
                # AkShare 返回的列通常有 '新闻标题' 和 '新闻链接' (或 url)
                # 这里做个容错，如果拿不到链接，就生成一个百度搜索链接
                for index, row in df.head(5).iterrows():
                    title = row['新闻标题']
                    # 尝试获取链接，不同版本列名可能不同
                    link = row.get('新闻链接', row.get('url', f"https://www.baidu.com/s?wd={title}"))
                    news_data.append({'title': title, 'link': link})
            except:
                pass

        # 3. 兜底数据
        if not news_data:
            news_data = [
                {'title': "市场静待美联储数据", 'link': "https://cn.investing.com/news/commodities-news"},
                {'title': "全球央行增持黄金趋势不减", 'link': "https://cn.investing.com/news/gold-news"}
            ]

        return news_data

    def _generate_prompt(self, news_data, current_price):
        # 构造 Prompt 时只需要标题，不需要链接给 AI 看
        news_text = "\n".join([f"- {n['title']}" for n in news_data])

        prompt = f"""
        你是一位宏观对冲基金经理。基于以下新闻和金价({current_price})，分析黄金走势。

        【最新快讯】
        {news_text}

        【要求】
        1. 简述核心情绪。
        2. 给情绪打分：-10(极空) 到 +10(极多)。
        3. 给出操作建议。

        【格式】
        情绪：...
        打分：...
        建议：...
        """
        return prompt

    def run(self):
        while self.is_running:
            now = datetime.datetime.now()
            if self.last_analysis_time and (now - self.last_analysis_time).seconds < 300:
                for _ in range(50):
                    if not self.is_running: break
                    self.msleep(100)  # QThread 的 sleep 单位是秒
                continue

            try:
                if not self.client:
                    self.ai_advice_signal.emit("API Key 未配置", 0, [])
                    for _ in range(600):
                        if not self.is_running: break
                        self.msleep(100)  # QThread 的 sleep 单位是秒
                    continue

                news_data = self._fetch_financial_news()
                if not news_data:
                    for _ in range(600):
                        if not self.is_running: break
                        self.msleep(100)  # QThread 的 sleep 单位是秒
                    continue
                current_fingerprint = "".join([n['title'] for n in news_data])
                if current_fingerprint == self.last_news_fingerprint:
                    print("[AI Agent] 新闻未更新，复用上次结论，节省 Token。")
                    if self.last_analysis_time is not None:
                        for _ in range(100):
                            if not self.is_running: break
                            self.msleep(100)  # QThread 的 sleep 单位是秒
                        continue
                print("[AI Agent] 检测到新消息，请求 Gemini 分析...")
                prompt = self._generate_prompt(news_data, "实盘中")

                response = self.client.models.generate_content(
                    model=MODEL_NAME, contents=prompt
                )

                result_text = response.text
                score = 0
                import re
                match = re.search(r"打分：\s*([-+]?\d+)", result_text)
                if match:
                    score = int(match.group(1))

                # 发送完整的 news_data (包含链接)
                self.ai_advice_signal.emit(result_text, score, news_data)
                self.last_news_fingerprint = current_fingerprint
                self.last_analysis_time = now

            except Exception as e:
                print(f"AI Error: {e}")
                self.ai_advice_signal.emit(f"AI 错误: {e}", 0, [])

            for _ in range(100):
                if not self.is_running: break
                self.msleep(100)  # QThread 的 sleep 单位是秒

    def stop(self):
        self.is_running = False