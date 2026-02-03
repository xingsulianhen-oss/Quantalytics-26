import os
import datetime
import time
import feedparser
import ollama
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

# 3. LocalModel 配置
LOCAL_LLM_MODEL = "qwen3:14b"


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

        # --- 初始化 DeepSeek ---
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

        # --- 初始化本地 Ollama ---
        print(f"[AI Agent] 本地过滤器已启用，目标模型: {LOCAL_LLM_MODEL}")

    def _get_sentry_mode_config(self):
        """
        根据当前时间判断是'作战模式'还是'哨兵模式'
        返回: (check_interval_seconds, min_score_threshold)
        """
        now = datetime.datetime.now()
        wd = now.weekday()  # 0=周一 ... 6=周日

        # 定义周末：周六 06:00 (美盘收盘) 到 周一 05:00 (亚盘开盘前)
        # 简单判定：周六 06:00 以后，或者是周日全天
        is_weekend = False
        if wd == 5 and now.hour >= 6:  # 周六白天
            is_weekend = True
        elif wd == 6:  # 周日全天
            is_weekend = True
        elif wd == 0 and now.hour < 5:  # 周一凌晨
            is_weekend = True

        if is_weekend:
            # === 哨兵模式 (Sentry Mode) ===
            # 频率: 1小时 (3600秒)
            # 阈值: 8分 (只看核弹级新闻)
            return 3600, 8
        else:
            # === 作战模式 (Combat Mode) ===
            # 频率: 1分钟 (60秒)
            # 阈值: 6分 (关注常规财经数据)
            return 60, 6

    def _fetch_financial_news(self):
        """
        获取全球混合新闻源 (英文优先 + 中文兜底)
        """
        news_data = []

        # === 配置高质量英文源 (优先级: 高) ===
        rss_sources_en = [
            # 1. CNBC 全球市场 (宏观/美联储)
            {
                "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
                "tag": "[CNBC]"
            },
            # 2. FXStreet (外汇/黄金/加密 交易员必看)
            {
                "url": "https://www.fxstreet.com/rss/news",
                "tag": "[FXStreet]"
            },
            # 3. Investing.com 英文版 (大宗商品)
            {
                "url": "https://www.investing.com/rss/news_25.rss",
                "tag": "[Inv-US]"
            }
        ]

        # === 配置中文源 (作为补充) ===
        rss_sources_cn = [
            # Investing.com 中文版
            {
                "url": "https://cn.investing.com/rss/news_285.rss",
                "tag": "[Inv-CN]"
            }
        ]

        # 1. 抓取英文源 (每个源抓前 10 条)
        # print("[AI Agent] 正在连接华尔街英文情报源...")
        for source in rss_sources_en:
            try:
                # 设置超时，防止连不上外网导致卡死
                feed = feedparser.parse(source["url"])

                # 检查是否成功 (feedparser 不会抛异常，要检查 bozo 或 status)
                if hasattr(feed, 'status') and feed.status != 200:
                    print(f"  -> {source['tag']} 连接失败 (Status: {feed.status})")
                    continue

                count = 0
                for entry in feed.entries:
                    # 简单的关键词过滤，确保和钱有关 (可选)
                    # if any(k in entry.title.lower() for k in ['gold', 'usd', 'fed', 'rate', 'cpi', 'data']):
                    news_data.append({
                        'title': f"{source['tag']} {entry.title}",  # 加上来源标签
                        'link': entry.link,
                        'lang': 'en'
                    })
                    count += 1
                    if count >= 8: break  # 每个英文源只取最新 8 条
                # print(f"  -> {source['tag']} 获取成功: {count} 条")
            except Exception as e:
                print(f"  -> {source['tag']} 解析错误: {e}")

        # 2. 抓取中文源 (作为补充，抓 5 条)
        for source in rss_sources_cn:
            try:
                feed = feedparser.parse(source["url"])
                for entry in feed.entries[:5]:
                    news_data.append({
                        'title': f"{source['tag']} {entry.title}",
                        'link': entry.link,
                        'lang': 'cn'
                    })
            except:
                pass

        # 3. AkShare 兜底 (如果 RSS 全挂了)
        if len(news_data) < 5:
            try:
                df = ak.stock_news_em(symbol="601899")
                for index, row in df.head(5).iterrows():
                    news_data.append({
                        'title': f"[AkShare] {row['新闻标题']}",
                        'link': row.get('新闻链接', "#"),
                        'lang': 'cn'
                    })
            except:
                pass

        # 4. 去重 (防止同一条新闻中英文重复发，虽然标题不同很难完全去重，但防一下完全一样的)
        unique_news = []
        seen_titles = set()
        for n in news_data:
            # 简单的清理，去除多余空格
            clean_title = n['title'].strip()
            if clean_title not in seen_titles:
                unique_news.append(n)
                seen_titles.add(clean_title)

        # 5. 排序与截断 (为了 token 考虑，总共保留 35 条给本地 LLM 筛选)
        # 英文放前面
        unique_news.sort(key=lambda x: x['lang'] == 'cn')  # False(0) 在前，True(1) 在后 -> 英文在前

        # print(f"[AI Agent] 情报聚合完毕，共 {len(unique_news)} 条 (英文优先)。")
        return unique_news[:35]

    def _filter_by_local_llm(self, news_list):
        """
        [核心功能] 使用本地显卡 (Ollama) 快速过滤新闻
        """
        if not news_list: return []

        # print(f"[Local LLM] 正在筛选 {len(news_list)} 条新闻...")
        high_value_news = []

        for news in news_list:
            # 极简 Prompt，追求速度
            prompt = f"判断新闻对黄金/美元的影响(0-10分)，只返回一个数字。新闻：{news['title']}"

            try:
                # 调用本地 Ollama
                response = ollama.generate(model=LOCAL_LLM_MODEL, prompt=prompt)
                content = response['response'].strip()

                # 提取数字
                match = re.search(r'\d+', content)
                score = int(match.group()) if match else 0

                # 筛选阈值：6分以上保留
                if score >= 6:
                    print(f"  ★ 保留 [{score}分]: {news['title']}")
                    # 可以在这里把本地分数也存进去，供云端参考
                    news['local_score'] = score
                    high_value_news.append(news)
                # else:
                #     print(f"  pass [{score}分]: {news['title']}")

            except Exception as e:
                print(f"[Local LLM] 推理错误: {e}")

        print(f"[Local LLM] 筛选完毕，剩余 {len(high_value_news)} 条关键情报。")
        return high_value_news

    def _generate_prompt(self, news_data, price):
        news_text = "\n".join([f"- [{n.get('local_score', '?')}分] {n['title']}" for n in news_data])

        return f"""
        你是由 DeepSeek 和 Gemini 组成的专家委员会。
        本地 AI 已经对海量新闻进行了初筛，以下是**高价值情报**：

        【关键新闻】
        {news_text}

        【当前金价】
        {price}

        【任务】
        1. 综合分析这些高分新闻对 XAU/USD 的短期合力方向。
        2. 给出最终情绪打分：-10(极空) 到 +10(极多)。只输出整数。
        3. 简要说明逻辑。

        【格式】
        情绪：...
        打分：...
        逻辑：...
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
        """调用 DeepSeek"""
        if not self.ds_client: return None
        try:
            response = self.ds_client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "你是首席宏观分析师。"},
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

        # 状态记录
        last_check_time = 0

        while self.is_running:
            # === 1. 动态获取当前模式配置 ===
            check_interval, score_threshold = self._get_sentry_mode_config()

            now_ts = time.time()
            if now_ts - last_check_time < check_interval:
                self.msleep(1000)  # 没到时间就睡1秒
                continue

            last_check_time = now_ts

            try:
                # === 高频获取新闻 ===
                # print(f"[AI Agent] 扫描中 (当前阈值: {score_threshold}分)...")
                raw_news = self._fetch_financial_news()
                if not raw_news: continue

                # === 3. 核心优化：指纹比对 (Event Trigger) ===
                # 将所有标题连起来做个哈希或字符串，判断内容变没变
                current_fingerprint = "".join([n['title'] for n in raw_news])

                # 如果新闻没变，直接跳过 AI 分析！
                # 这意味着：如果没有新消息，AI 可以 1 个小时不工作；
                # 但如果有突发消息，AI 会在 1 分钟内响应。
                if current_fingerprint == self.last_news_fingerprint:
                    # 如果是周末，甚至可以打印个日志说"哨兵正在值班，无异常"
                    continue

                # print(f"[AI Agent] ⚡ 发现新情报！(阈值: >={score_threshold})")

                # === 4. 本地显卡初筛 ===
                high_value_news = []
                scored_news = self._filter_by_local_llm(raw_news)

                # 二次过滤：根据当前模式的阈值筛选
                for n in scored_news:
                    if n.get('local_score', 0) >= score_threshold:
                        high_value_news.append(n)

                # 如果全是垃圾新闻 (比如 "某公司股价微跌")，本地 LLM 拦截，不打扰云端
                if not high_value_news:
                    print(f"[AI Agent] 虽有新新闻，但未达到哨兵模式阈值 ({score_threshold}分)，忽略。")
                    self.last_news_fingerprint = current_fingerprint  # 更新指纹，避免重复检测
                    continue

                # === 5. 云端专家委员会 (DeepSeek + Gemini) ===
                # print(f"[AI Agent] 提交 {len(high_value_news)} 条关键情报给云端...")
                prompt = self._generate_prompt(high_value_news, "实盘")

                text_ds = None
                score_ds = 0
                text_gemini = None
                score_gemini = 0

                # 尝试 DeepSeek
                if self.ds_client:
                    print("--> DeepSeek 思考中...")
                    text_ds = self._call_deepseek(prompt)
                    score_ds = self._extract_score(text_ds)

                # 尝试 Gemini
                if self.gemini_client:
                    print("--> Gemini 思考中...")
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
                        f"【混合智能决策】加权分: {final_score}\n"
                        f"本地筛选: {len(raw_news)} -> {len(high_value_news)} 条\n"
                        f"{'-' * 30}\n"
                        f"🦅 [DeepSeek]: {score_ds} 分\n{text_ds[:200]}...\n\n"  # 只截取前200字展示
                        f"🌍 [Gemini]: {score_gemini} 分\n{text_gemini[:200]}..."
                    )

                # 情况 B: 只有 DeepSeek
                elif text_ds:
                    final_score = score_ds
                    final_text = f"【DeepSeek 独家】\n{text_ds}"

                # 情况 C: 只有 Gemini
                elif text_gemini:
                    final_score = score_gemini
                    final_text = f"【Gemini 独家】\n{text_gemini}"

                # 6. 发送结果并更新状态
                if final_text:
                    self.ai_advice_signal.emit(final_text, final_score, high_value_news)
                    # 只有分析成功了，才更新指纹和时间
                    self.last_news_fingerprint = current_fingerprint
                    self.last_analysis_time = datetime.datetime.now()

            except Exception as e:
                print(f"[Agent Loop Error] {e}")
                self.ai_advice_signal.emit(f"系统错误: {e}", 0, [])

            # 休息
            for _ in range(100):
                if not self.is_running: break
                self.msleep(100)

    def stop(self):
        self.is_running = False