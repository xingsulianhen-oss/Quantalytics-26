import os
import datetime
import feedparser
from google import genai
from openai import OpenAI
from PyQt6.QtCore import QThread, pyqtSignal
import akshare as ak
import re

# ================= 配置区域 =================
# 1. Gemini 配置
GEMINI_MODEL = "models/gemini-2.5-flash"

# 2. DeepSeek 配置
DEEPSEEK_MODEL = "deepseek-reasoner"


# ===========================================

class AIAgent(QThread):
    # 信号: (分析文本, 打分, 新闻列表)
    ai_advice_signal = pyqtSignal(str, int, list)

    def __init__(self, api_config=None):
        super().__init__()
        self.is_running = True
        self.last_analysis_time = None
        self.last_news_fingerprint = ""

        # 从配置中读取 Key
        self.gemini_key = ""
        self.deepseek_key = ""
        if api_config:
            self.gemini_key = api_config.get('gemini', '')
            self.deepseek_key = api_config.get('deepseek', '')

        # --- 初始化 Gemini ---
        self.gemini_client = None
        if self.gemini_key:
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_key)
                print("[AI Agent] Gemini 客户端加载成功")
            except Exception as e:
                print(f"[AI Agent] Gemini 初始化失败: {e}")

        # --- 初始化 DeepSeek (新增) ---
        self.ds_client = None
        if self.deepseek_key:
            try:
                # DeepSeek 使用 OpenAI 兼容接口
                self.ds_client = OpenAI(
                    api_key=self.deepseek_key,
                    base_url="https://api.deepseek.com"
                )
                print("[AI Agent] DeepSeek 客户端加载成功")
            except Exception as e:
                print(f"[AI Agent] DeepSeek 初始化失败: {e}")

    def _fetch_financial_news(self):
        """获取新闻 + 链接"""
        news_data = []
        # 1. RSS (Investing.com)
        try:
            rss_url = "https://cn.investing.com/rss/news_285.rss"
            feed = feedparser.parse(rss_url)
            for entry in feed.entries[:5]:
                news_data.append({'title': entry.title, 'link': entry.link})
        except Exception as e:
            print(f"[AI Agent] RSS 失败: {e}")

        # 2. AkShare 备选
        if not news_data:
            try:
                df = ak.stock_news_em(symbol="601899")
                for index, row in df.head(5).iterrows():
                    title = row['新闻标题']
                    link = row.get('新闻链接', row.get('url', f"https://www.baidu.com/s?wd={title}"))
                    news_data.append({'title': title, 'link': link})
            except:
                pass

        # 3. 兜底
        if not news_data:
            news_data = [
                {'title': "市场静待美联储数据", 'link': "https://cn.investing.com/news/commodities-news"},
                {'title': "全球央行增持黄金趋势不减", 'link': "https://cn.investing.com/news/gold-news"}
            ]
        return news_data

    def _generate_prompt(self, news_data, price):
        news_text = "\n".join([f"- {n['title']}" for n in news_data])
        return f"""
        你是一位宏观交易员。基于新闻和现价({price})分析黄金(XAU/USD)走势。

        【新闻】
        {news_text}

        【任务】
        1. 简述核心情绪。
        2. 情绪打分：-10(极空) 到 +10(极多)。只输出整数。
        3. 给出建议。

        【格式】
        情绪：...
        打分：...
        建议：...
        """

    def _call_gemini(self, prompt):
        """调用 Gemini"""
        if not self.gemini_client: return None
        try:
            response = self.gemini_client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"[Gemini Error] {e}")
            return None

    def _call_deepseek(self, prompt):
        """调用 DeepSeek (新增)"""
        if not self.ds_client: return None
        try:
            response = self.ds_client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "你是专业的金融分析师。"},
                    {"role": "user", "content": prompt},
                ],
                stream=False
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[DeepSeek Error] {e}")
            return None

    def _extract_score(self, text):
        """辅助函数：从文本中提取分数"""
        if not text: return 0
        import re
        # 匹配 "打分：8" 或 "打分：+8" 或 "打分: -5"
        match = re.search(r"打分[：:]\s*([-+]?\d+)", text)
        if match:
            try:
                # 限制在 -10 到 10 之间，防止模型胡说
                score = int(match.group(1))
                return max(-10, min(10, score))
            except:
                pass
        return 0

    def run(self):
        # 定义权重
        WEIGHT_DS = 1.2  # DeepSeek 权重 (逻辑推理强)
        WEIGHT_GEMINI = 1.0  # Gemini 权重 (信息整合快)

        while self.is_running:
            now = datetime.datetime.now()
            if self.last_analysis_time and (now - self.last_analysis_time).seconds < 3600:
                for _ in range(50):
                    if not self.is_running: break
                    self.msleep(100)
                continue

            try:
                if not self.gemini_client and not self.ds_client:
                    self.ai_advice_signal.emit("API Key 未配置", 0, [])
                    for _ in range(600):
                        if not self.is_running: break
                        self.msleep(100)
                    continue

                # 2. 获取新闻
                news_data = self._fetch_financial_news()
                if not news_data:
                    for _ in range(600):
                        if not self.is_running: break
                        self.msleep(100)
                    continue

                # 3. 检查新闻指纹
                current_fingerprint = "".join([n['title'] for n in news_data])
                if current_fingerprint == self.last_news_fingerprint:
                    print("[AI Agent] 新闻未更新，复用上次结论，节省 Token。")
                    if self.last_analysis_time is not None:
                        for _ in range(100):
                            if not self.is_running: break
                            self.msleep(100)
                        continue

                print("[AI Agent] 开始分析...")
                prompt = self._generate_prompt(news_data, "实盘")

                # === 核心逻辑：双模并行 ===

                text_ds = None
                score_ds = 0
                text_gemini = None
                score_gemini = 0

                # 尝试 DeepSeek
                if self.ds_client:
                    print("--> 正在调用 DeepSeek...")
                    text_ds = self._call_deepseek(prompt)
                    score_ds = self._extract_score(text_ds)

                # 尝试 Gemini
                if self.gemini_client:
                    print("--> 正在调用 Gemini...")
                    text_gemini = self._call_gemini(prompt)
                    score_gemini = self._extract_score(text_gemini)

                # === 加权决策计算 ===
                final_score = 0
                final_text = ""

                # 情况 A: 两个专家都给了意见
                if text_ds and text_gemini:
                    # 加权平均公式
                    total_weight = WEIGHT_DS + WEIGHT_GEMINI
                    weighted_sum = (score_ds * WEIGHT_DS) + (score_gemini * WEIGHT_GEMINI)
                    final_score = int(round(weighted_sum / total_weight))

                    final_text = (
                        f"【联合决策】加权分: {final_score} (DS:{score_ds} | Gem:{score_gemini})\n"
                        f"{'-' * 30}\n"
                        f"🦅 [DeepSeek 观点]\n{text_ds}\n\n"
                        f"🌍 [Gemini 观点]\n{text_gemini}"
                    )

                    # 情况 B: 只有 DeepSeek
                elif text_ds:
                    final_score = score_ds
                    final_text = f"【单模决策】(DeepSeek)\n{text_ds}"

                # 情况 C: 只有 Gemini
                elif text_gemini:
                    final_score = score_gemini
                    final_text = f"【单模决策】(Gemini)\n{text_gemini}"

                    # 情况 D: 全挂了
                else:
                    self.ai_advice_signal.emit("所有 AI 服务均不可用", 0, news_data)
                    for _ in range(100):
                        if not self.is_running: break
                        self.msleep(100)
                    continue

                # 发送最终结果
                self.ai_advice_signal.emit(final_text, final_score, news_data)
                self.last_news_fingerprint = current_fingerprint
                self.last_analysis_time = now

            except Exception as e:
                print(f"[Agent Loop Error] {e}")
                import traceback
                traceback.print_exc()

            # 每次循环休息 10 秒 (碎片化睡眠)
            for _ in range(100):
                if not self.is_running: break
                self.msleep(100)

    def stop(self):
        self.is_running = False