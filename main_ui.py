import sys
import time
import datetime
import json
import os
import logging
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QFrame, QTextEdit, QLineEdit,
                             QPushButton, QScrollArea, QGroupBox, QTextBrowser)
from PyQt6.QtGui import QFont, QDoubleValidator, QColor, QPicture, QPainter
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPointF, QRectF
import pyqtgraph as pg
from pyqtgraph import InfiniteLine, TextItem

from data_dispatcher import DataHandler
from strategy_engine import QuantalyticsEngine
from ai_agent import AIAgent
from portfolio_manager import PortfolioManager
from optimizer_worker import OptimizerWorker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("quant_system.log", encoding='utf-8'), # 写入文件
        logging.StreamHandler() # 输出到控制台
    ]
)

# --- 交易线程 ---
class TradingWorker(QThread):
    data_updated = pyqtSignal(float, str, str, object)

    def __init__(self):
        super().__init__()
        self.is_running = True
        self.data_handler = DataHandler(max_len=200)
        self.strategy = QuantalyticsEngine()

    def is_trading_time(self):
        """
        判断当前是否为上海黄金交易所 (SGE) 的交易时间
        早盘: 09:00-11:30
        午盘: 13:30-15:30
        夜盘: 20:00-02:30 (次日)
        """
        now = datetime.datetime.now()
        t = now.time()
        wd = now.weekday()  # 0=周一, 6=周日

        # 定义时间节点
        t_02_30 = datetime.time(2, 30)
        t_09_00 = datetime.time(9, 0)
        t_11_30 = datetime.time(11, 30)
        t_13_30 = datetime.time(13, 30)
        t_15_30 = datetime.time(15, 30)
        t_20_00 = datetime.time(20, 0)

        # 1. 排除周末大休市
        # 周六：02:30 之后休市
        if wd == 5 and t > t_02_30:
            return False
        # 周日：全天休市
        if wd == 6:
            return False
        # 周一：早盘前 (09:00前) 没有夜盘延续，休市
        if wd == 0 and t < t_09_00:
            return False

        # 2. 判断具体时段
        # 夜盘 (20:00 - 24:00 或 00:00 - 02:30)
        # 注意：这里不用管周一凌晨，因为上面已经排除了
        is_night = (t >= t_20_00) or (t < t_02_30)

        # 早盘
        is_morning = (t >= t_09_00) and (t < t_11_30)

        # 午盘
        is_afternoon = (t >= t_13_30) and (t < t_15_30)

        return is_night or is_morning or is_afternoon

    def run(self):
        logging.info("[Worker] 交易线程启动，正在初始化数据...")
        self.data_handler.initialize()

        if not self.data_handler.buffer.empty:
            # 取出当前缓冲区里的最新数据
            current_price = self.data_handler.buffer.iloc[-1]['Close']

            # 即使没有新信号，也先算一遍指标以便画图
            # 注意：check_signal 会处理数据量不足的情况，返回 df_raw
            signal, reason, processed_df = self.strategy.check_signal(self.data_handler.buffer)

            # 马上发给 UI，让用户看见图
            self.data_updated.emit(current_price, signal, reason, processed_df)
            logging.info("[Worker] 首帧数据已发送至 UI")

        while self.is_running:
            # === 1. 交易时间检查 ===
            if not self.is_trading_time():
                # 如果是休市时间，打印一次日志（防止刷屏，实际可优化为只打印一次）
                # logging.info("[System] 休市中，暂停监控...")

                # 长时间休眠：1分钟 (600 * 0.1s)
                # 使用碎片化睡眠，确保能随时响应关闭信号
                for _ in range(600):
                    if not self.is_running: break
                    self.msleep(100)
                continue

            # === 2. 正常交易逻辑 ===
            try:
                price = self.data_handler.fetch_realtime_price()
                if price is not None:
                    # 更新数据
                    raw_df = self.data_handler.update_tick(price)

                    # 计算信号 (返回: 信号, 理由, 带指标的DF)
                    signal, reason, processed_df = self.strategy.check_signal(raw_df)

                    # 发送给 UI
                    self.data_updated.emit(price, signal, reason, processed_df)

                # 正常间隔：3秒 (30 * 0.1s)
                for _ in range(30):
                    if not self.is_running: break
                    self.msleep(100)

            except Exception as e:
                logging.error(f"[Worker] Error: {e}")
                # 出错后等待 5秒
                for _ in range(50):
                    if not self.is_running: break
                    self.msleep(100)

    def stop(self):
        self.is_running = False

class StringAxis(pg.AxisItem):
    """
    自定义坐标轴：将整数索引 (0, 1, 2) 映射回时间字符串 ("09:30", "09:31")
    """
    def __init__(self, orientation='bottom', **kwargs):
        super().__init__(orientation, **kwargs)
        self.ticks_mapper = {} # 存储 {index: time_str}

    def set_ticks(self, data_index):
        """传入 DataFrame 的 index (时间戳列表)"""
        self.ticks_mapper = {}
        for i, timestamp in enumerate(data_index):
            # 存一下映射关系，只显示时:分
            self.ticks_mapper[i] = timestamp.strftime('%H:%M')

    def tickStrings(self, values, scale, spacing):
        """重写父类方法：根据 value (整数索引) 返回显示文本"""
        strings = []
        for v in values:
            idx = int(v)
            # 如果索引在字典里，就返回时间；否则返回空
            if idx in self.ticks_mapper:
                strings.append(self.ticks_mapper[idx])
            else:
                strings.append("")
        return strings

class CandlestickItem(pg.GraphicsObject):
    """
    专业的 K 线蜡烛图组件
    """

    def __init__(self, data):
        """
        data: 列表，格式 [(time, open, close, low, high), ...]
        """
        pg.GraphicsObject.__init__(self)
        self.data = data
        self.picture = QPicture()
        self.generatePicture()

    def generatePicture(self):
        p = QPainter(self.picture)

        # 1. 计算宽度的逻辑
        # 如果数据少于2个，给个默认宽；否则计算相邻两点的时间差的 1/3 作为半宽
        if len(self.data) > 1:
            w = (self.data[1][0] - self.data[0][0]) / 3.0
        else:
            w = 60 / 3.0  # 假设是分钟线

        for (t, open, close, low, high) in self.data:
            # 2. 设定颜色 (中国习惯：红涨绿跌)
            if close >= open:
                # 涨 (红)
                p.setPen(pg.mkPen('#ff4444'))
                p.setBrush(pg.mkBrush('#ff4444'))
            else:
                # 跌 (绿)
                p.setPen(pg.mkPen('#00cc00'))
                p.setBrush(pg.mkBrush('#00cc00'))

            # 3. 画上下影线 (Low 到 High)
            p.drawLine(QPointF(t, low), QPointF(t, high))

            # 4. 画实体 (Open 到 Close)
            # drawRect(x, y, w, h)
            # 注意：Y轴向下是正方向(在屏幕坐标系)，但在PlotWidget里会自动翻转
            # 我们只需要画出矩形即可
            if open == close:
                # 十字星
                p.drawLine(QPointF(t - w, open), QPointF(t + w, close))
            else:
                # 实体矩形
                # 这里的 y 取 open，height 取 close-open 是没问题的
                p.drawRect(QRectF(t - w, open, w * 2, close - open))

        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return QRectF(self.picture.boundingRect())


# --- 主窗口 ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fin Tools")
        self.resize(1400, 900)
        self.setStyleSheet("""
            QMainWindow {background-color: #121212;}
            QLabel {color: #e0e0e0;}
            QGroupBox {
                border: 1px solid #444; 
                border-radius: 5px; 
                margin-top: 10px; 
                font-weight: bold;
                color: #aaa;
            }
            QGroupBox::title {
                subcontrol-origin: margin; 
                subcontrol-position: top left; 
                padding: 0 5px;
            }
        """)

        # 状态变量
        self.current_price = 0.0
        self.current_tech_signal = "NEUTRAL"
        self.current_ai_score = 0
        self.portfolio_manager = PortfolioManager()

        self.init_ui()

        self.worker = TradingWorker()
        self.worker.data_updated.connect(self.update_tech_ui)
        self.worker.start()

        self.ai_worker = AIAgent()
        self.ai_worker.ai_advice_signal.connect(self.update_ai_ui)
        self.ai_worker.start()

        self.opt_worker = OptimizerWorker()
        self.opt_worker.optimization_finished.connect(self.apply_new_params)

        self.settings_file = "config.json"
        self.load_settings()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # === 左侧：图表 ===
        chart_layout = QVBoxLayout()
        pg.setConfigOptions(antialias=True)
        self.x_axis = StringAxis(orientation='bottom')
        self.plot_widget = pg.PlotWidget(axisItems={'bottom': self.x_axis})
        self.plot_widget.setBackground('#000000')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setTitle("Au99.99 实时走势", color="#aaa", size="12pt")
        chart_layout.addWidget(self.plot_widget)
        main_layout.addLayout(chart_layout, stretch=6)

        # === 初始化十字光标 ===
        # 1. 垂直线 (时间轴)
        self.v_line = InfiniteLine(angle=90, movable=False)
        self.v_line.setPen(pg.mkPen('#aaa', width=1, style=Qt.PenStyle.DashLine))

        # 2. 水平线 (价格轴)
        self.h_line = InfiniteLine(angle=0, movable=False)
        self.h_line.setPen(pg.mkPen('#aaa', width=1, style=Qt.PenStyle.DashLine))

        self.plot_widget.addItem(self.v_line, ignoreBounds=True)
        self.plot_widget.addItem(self.h_line, ignoreBounds=True)

        # 3. 信息浮窗 (显示在左上角)
        self.cursor_label = TextItem(anchor=(0, 0), fill=(0, 0, 0, 200))  # 黑色半透明背景
        self.plot_widget.addItem(self.cursor_label, ignoreBounds=True)

        # 4. 监听鼠标移动事件
        # 使用 SignalProxy 是官方推荐的高性能做法，但直接连 signal 也行
        self.plot_widget.scene().sigMouseMoved.connect(self.on_mouse_moved)

        # 初始化数据缓存
        self.df_cache = None

        # === 右侧：情报面板 ===
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background-color: #1e1e1e; border: none;")

        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setSpacing(15)
        panel_layout.setContentsMargins(15, 15, 15, 15)

        # 1. 资产概览
        top_box = QFrame()
        top_layout = QHBoxLayout(top_box)

        price_box = QVBoxLayout()
        self.price_label = QLabel("¥ --.--")
        self.price_label.setFont(QFont("Arial", 36, QFont.Weight.Bold))
        self.price_label.setStyleSheet("color: #FFD700;")
        price_box.addWidget(QLabel("当前金价 (Au99.99)"))
        price_box.addWidget(self.price_label)
        top_layout.addLayout(price_box)

        input_box = QVBoxLayout()
        self.input_holdings = QLineEdit()
        self.input_holdings.setPlaceholderText("持仓金额")
        self.input_holdings.setStyleSheet("background: #333; color: white; padding: 5px; border: 1px solid #555;")
        self.input_cash = QLineEdit()
        self.input_cash.setPlaceholderText("可用现金")
        self.input_cash.setText("10000")
        self.input_cash.setStyleSheet("background: #333; color: white; padding: 5px; border: 1px solid #555;")
        input_box.addWidget(QLabel("💰 持仓:"))
        input_box.addWidget(self.input_holdings)
        input_box.addWidget(QLabel("💳 现金:"))
        input_box.addWidget(self.input_cash)
        top_layout.addLayout(input_box)
        panel_layout.addWidget(top_box)

        # 2. 技术面
        group_tech = QGroupBox("📊 技术面分析")
        tech_layout = QVBoxLayout(group_tech)

        tech_header = QHBoxLayout()
        self.lbl_tech_signal = QLabel("信号: 等待中")
        self.lbl_tech_signal.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        tech_header.addWidget(self.lbl_tech_signal)
        tech_layout.addLayout(tech_header)

        self.txt_tech_detail = QTextEdit()
        self.txt_tech_detail.setReadOnly(True)
        self.txt_tech_detail.setMaximumHeight(60)
        self.txt_tech_detail.setStyleSheet("background: #252525; color: #bbb; border: none; font-size: 13px;")
        tech_layout.addWidget(self.txt_tech_detail)
        panel_layout.addWidget(group_tech)

        # 3. 消息面 (使用 QTextBrowser 替换了 QTextEdit)
        group_news = QGroupBox("🌏 宏观消息面 (点击标题阅读原文)")
        news_layout = QVBoxLayout(group_news)

        # --- 核心修复：使用 QTextBrowser ---
        self.txt_news_list = QTextBrowser()  # <--- 改这里
        self.txt_news_list.setReadOnly(True)  # 虽然 Browser 默认就是只读，但显式写一下也没坏处
        self.txt_news_list.setMaximumHeight(130)
        self.txt_news_list.setOpenExternalLinks(True)  # QTextBrowser 支持此方法
        self.txt_news_list.setStyleSheet("""
            QTextBrowser {
                background: #252525; 
                color: #ddd; 
                border: 1px solid #444; 
                font-size: 13px;
                line-height: 1.5;
            }
        """)
        news_layout.addWidget(self.txt_news_list)

        ai_header = QHBoxLayout()
        ai_header.addWidget(QLabel("🤖 AI 深度分析:"))
        self.lbl_ai_score = QLabel("情绪分: 0")
        self.lbl_ai_score.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        ai_header.addWidget(self.lbl_ai_score)
        ai_header.addStretch()
        news_layout.addLayout(ai_header)

        self.txt_ai_reason = QTextEdit()
        self.txt_ai_reason.setReadOnly(True)
        self.txt_ai_reason.setMinimumHeight(120)
        self.txt_ai_reason.setStyleSheet("background: #252525; color: #bbb; border: none; font-size: 13px;")
        news_layout.addWidget(self.txt_ai_reason)
        panel_layout.addWidget(group_news)

        # 4. 决策框
        group_action = QGroupBox("🚀 最终操作建议")
        group_action.setStyleSheet("QGroupBox {border: 2px solid #666;}")
        action_layout = QVBoxLayout(group_action)

        self.lbl_action = QLabel("等待数据...")
        self.lbl_action.setFont(QFont("Arial", 22, QFont.Weight.Bold))
        self.lbl_action.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_amount = QLabel("---")
        self.lbl_amount.setFont(QFont("Arial", 16))
        self.lbl_amount.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_calc = QPushButton("刷新建议")
        self.btn_calc.setStyleSheet("background-color: #007acc; color: white; padding: 8px;")
        self.btn_calc.clicked.connect(self.calculate_final_advice)

        self.btn_optimize = QPushButton("🧬 AI 参数进化")
        self.btn_optimize.setStyleSheet("""
                    background-color: #6a0dad; 
                    color: white; 
                    padding: 8px; 
                    border-radius: 4px;
                    font-weight: bold;
                """)  # 用紫色区分，显得高级一点
        self.btn_optimize.clicked.connect(self.start_optimization)

        action_layout.addWidget(self.lbl_action)
        action_layout.addWidget(self.lbl_amount)
        action_layout.addWidget(self.btn_calc)
        action_layout.addWidget(self.btn_optimize)
        panel_layout.addWidget(group_action)

        panel_layout.addStretch()
        scroll.setWidget(panel)
        main_layout.addWidget(scroll, stretch=4)

    def load_settings(self):
        """加载配置文件 (config.json)"""
        config_file = "config.json"
        if not os.path.exists(config_file):
            return

        try:
            with open(config_file, 'r') as f:
                data = json.load(f)

            # 1. 恢复资产数据
            if 'assets' in data:
                self.input_holdings.setText(str(data['assets'].get('holdings', '0')))
                self.input_cash.setText(str(data['assets'].get('cash', '10000')))

            # 2. 恢复策略参数 (这是核心！)
            if 'strategy_params' in data:
                saved_params = data['strategy_params']
                # 确保 strategy 对象已存在
                if hasattr(self, 'worker') and hasattr(self.worker, 'strategy'):
                    self.worker.strategy.update_params(saved_params)
                    logging.info(f"[System] 成功加载历史策略参数: {saved_params}")

            # 3. 恢复窗口状态 (可选)
            if 'window_geometry' in data:
                # PyQt6 需要把 list 转回 QByteArray，略繁琐，这里先只做简单的
                pass

        except Exception as e:
            logging.error(f"[System] 读取配置失败: {e}")

    def save_settings(self):
        """保存配置文件"""
        # 1. 获取当前策略参数
        current_params = {}
        if hasattr(self, 'worker') and hasattr(self.worker, 'strategy'):
            current_params = self.worker.strategy.params

        # 2. 构造数据字典
        data = {
            'assets': {
                'holdings': self.input_holdings.text(),
                'cash': self.input_cash.text()
            },
            'strategy_params': current_params,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        # 3. 写入文件
        try:
            with open("config.json", 'w') as f:
                json.dump(data, f, indent=4)
            logging.info("[System] 配置已保存至 config.json")
        except Exception as e:
            logging.error(f"[System] 保存配置失败: {e}")

    def update_tech_ui(self, price, signal, reason, df):
        """更新技术面图表 (专业版)"""
        self.current_price = price
        self.current_tech_signal = signal
        self.price_label.setText(f"¥{price:.2f}")
        self.is_first_plot = True

        # 更新信号文字
        c = "#ff4444" if signal == "BUY" else "#00cc00" if signal == "SELL" else "#888"
        self.lbl_tech_signal.setText(f"信号: {signal}")
        self.lbl_tech_signal.setStyleSheet(f"color: {c}")
        self.txt_tech_detail.setText(reason)

        # --- 核心绘图逻辑优化 ---
        if not df.empty:
            self.df_cache = df
            self.plot_widget.clear()  # <--- 这一步删除了所有东西，包括十字线

            # === 【修复重点】重新添加被 clear() 删掉的十字光标组件 ===
            self.plot_widget.addItem(self.v_line, ignoreBounds=True)
            self.plot_widget.addItem(self.h_line, ignoreBounds=True)
            self.plot_widget.addItem(self.cursor_label, ignoreBounds=True)
            # 确保标签在最上层
            self.cursor_label.setZValue(999)
            self.v_line.setZValue(999)
            self.h_line.setZValue(999)
            # ===================================================

            self.x_axis.set_ticks(df.index)

            # 1. 准备 K 线数据
            # 格式: (timestamp, open, close, low, high)
            ohlc_data = []
            x_axis_indices = range(len(df))  # 0, 1, 2, ... len-1
            for i, (index_ts, row) in enumerate(df.iterrows()):
                # 注意：这里第一个参数传 i (0,1,2...)，而不是时间戳
                ohlc_data.append((i, row['Open'], row['Close'], row['Low'], row['High']))

            # 2. 绘制 K 线 (放到最底层)
            candle_item = CandlestickItem(ohlc_data)
            self.plot_widget.addItem(candle_item)

            # 3. 绘制均线 (SMA) - 这就是你要的"专业曲线"
            # 快线 (SMA_F): 黄色
            if 'SMA_F' in df.columns:
                self.plot_widget.plot(x_axis_indices, df['SMA_F'].values, pen=pg.mkPen('#ffff00', width=1), name="SMA Fast")

            # 慢线 (SMA_S): 紫色
            if 'SMA_S' in df.columns:
                self.plot_widget.plot(x_axis_indices, df['SMA_S'].values, pen=pg.mkPen('#da70d6', width=1), name="SMA Slow")

            # 4. 绘制布林带 (Bollinger Bands) - 蓝色细线
            if 'BBU' in df.columns:
                self.plot_widget.plot(x_axis_indices, df['BBU'].values,
                                      pen=pg.mkPen('#00bfff', width=1, style=Qt.PenStyle.DashLine))
                self.plot_widget.plot(x_axis_indices, df['BBL'].values,
                                      pen=pg.mkPen('#00bfff', width=1, style=Qt.PenStyle.DashLine))

            if self.is_first_plot:
                self.plot_widget.plotItem.autoRange()
                self.is_first_plot = False

        # 触发综合计算
        self.calculate_final_advice()

    def update_ai_ui(self, text, score, news_data):
        self.current_ai_score = score

        html_content = "<html><body>"
        for i, item in enumerate(news_data):
            title = item['title']
            link = item['link']
            # 链接样式
            html_content += f"""
            <p style='margin-bottom: 8px;'>
                {i + 1}. <a href='{link}' style='color: #5dade2; text-decoration: none; font-weight: bold;'>{title}</a>
            </p>
            """
        html_content += "</body></html>"

        self.txt_news_list.setHtml(html_content)

        color = "#ff4444" if score > 0 else "#00cc00" if score < 0 else "#ccc"
        self.lbl_ai_score.setText(f"情绪分: {score}")
        self.lbl_ai_score.setStyleSheet(f"color: {color}")
        self.txt_ai_reason.setText(text)
        self.calculate_final_advice()

    def calculate_final_advice(self):
        try:
            h = float(self.input_holdings.text())
        except:
            h = 0.0
        try:
            c = float(self.input_cash.text())
        except:
            c = 0.0

        action, amount, reason = self.portfolio_manager.calculate_suggestion(
            h, c, self.current_tech_signal, self.current_ai_score, self.current_price
        )

        self.lbl_action.setText(action)
        if action == "买入":
            self.lbl_action.setStyleSheet("color: #ff4444;")
            self.lbl_amount.setText(f"建议买入: ¥ {amount:,.2f}")
        elif action == "卖出":
            self.lbl_action.setStyleSheet("color: #00cc00;")
            self.lbl_amount.setText(f"建议卖出: ¥ {amount:,.2f}")
        else:
            self.lbl_action.setStyleSheet("color: #888;")
            self.lbl_amount.setText("建议金额: ¥ 0.00")

    def start_optimization(self):
        """点击按钮触发优化"""
        self.lbl_action.setText("正在计算最优策略...")
        self.lbl_action.setStyleSheet("color: #aaa;")
        self.btn_optimize.setEnabled(False)  # 禁用按钮防止重复点击
        self.btn_optimize.setText("🧬 正在进化中 (约需10秒)...")

        # 启动线程
        self.opt_worker.start()

    def apply_new_params(self, new_params):
        """优化完成，应用新参数"""
        logging.info(f"[System] 收到进化后的参数: {new_params}")

        # 1. 更新策略引擎参数
        # 确保 worker.strategy 是存在的
        self.worker.strategy.update_params(new_params)

        # 2. UI 反馈
        self.btn_optimize.setEnabled(True)
        self.btn_optimize.setText("🧬 AI 参数进化")

        # 3. 弹窗或在文本框提示
        msg = f"✅ 参数进化成功!\n\n" \
              f"RSI周期: {new_params.get('rsi_period')}\n" \
              f"布林周期: {new_params.get('bb_period')}\n" \
              f"SMA慢线: {new_params.get('sma_slow')}\n\n" \
              f"策略已自动更新，下个信号将基于新参数。"

        self.txt_tech_detail.setText(msg)

    def on_mouse_moved(self, pos):
        """鼠标移动事件 (去断层适配版)"""
        if self.df_cache is None or self.df_cache.empty:
            return

        view_box = self.plot_widget.plotItem.vb
        if view_box.sceneBoundingRect().contains(pos):
            mouse_point = view_box.mapSceneToView(pos)
            x_val = mouse_point.x()  # 这里的 x_val 现在是整数索引 (如 10.5)
            y_val = mouse_point.y()

            # === 修改逻辑：直接四舍五入获取索引 ===
            idx = int(round(x_val))

            # 边界保护：防止鼠标移出数据范围报错
            if idx < 0: idx = 0
            if idx >= len(self.df_cache): idx = len(self.df_cache) - 1

            # 直接按位置取数据，不需要 numpy 查时间了，超级快！
            target_row = self.df_cache.iloc[idx]
            target_time = self.df_cache.index[idx]

            # 更新十字线 (吸附到整数索引上)
            self.v_line.setPos(idx)
            self.h_line.setPos(y_val)

            # 构造文本
            color = "#ff4444" if target_row['Close'] >= target_row['Open'] else "#00cc00"
            info_html = f"""
            <div style='color: #eee; font-size: 12px; font-weight: bold;'>
                <span style='color: #aaa;'>时间:</span> {target_time.strftime('%H:%M')}<br>
                <span style='color: #aaa;'>开盘:</span> <span style='color: {color};'>{target_row['Open']:.2f}</span><br>
                <span style='color: #aaa;'>最高:</span> <span style='color: {color};'>{target_row['High']:.2f}</span><br>
                <span style='color: #aaa;'>最低:</span> <span style='color: {color};'>{target_row['Low']:.2f}</span><br>
                <span style='color: #aaa;'>收盘:</span> <span style='color: {color};'>{target_row['Close']:.2f}</span><br>
            """
            if 'RSI' in target_row:
                info_html += f"<span style='color: #aaa;'>RSI:</span> {target_row['RSI']:.1f}<br>"
            info_html += "</div>"

            self.cursor_label.setHtml(info_html)

            # 标签固定在左上角
            view_rect = view_box.viewRange()
            self.cursor_label.setPos(view_rect[0][0], view_rect[1][1])

    def closeEvent(self, event):
        logging.info("正在关闭程序，清理线程中...")

        # 1. 发出停止信号
        if hasattr(self, 'worker'): self.worker.stop()
        if hasattr(self, 'ai_worker'): self.ai_worker.stop()
        if hasattr(self, 'opt_worker'):
            # 优化线程通常没有 stop 标志，且 backtesting 很难中断
            # 这里我们可以选择 terminate (强制结束)，或者干脆不等待它
            if self.opt_worker.isRunning():
                self.opt_worker.terminate()  # 强制结束计算

        # 2. 有限等待 (最多等 1 秒)
        # wait(1000) 表示最多等 1000 毫秒，如果线程还在跑，就返回 False，但也继续往下执行
        if hasattr(self, 'worker'): self.worker.wait(1000)
        if hasattr(self, 'ai_worker'): self.ai_worker.wait(1000)

        self.save_settings()

        logging.info("程序已退出。")
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())