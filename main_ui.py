import sys
import time
import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QFrame, QTextEdit, QLineEdit,
                             QPushButton, QScrollArea, QGroupBox, QTextBrowser)
from PyQt6.QtGui import QFont, QDoubleValidator, QColor, QPicture, QPainter
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPointF, QRectF
import pyqtgraph as pg
from pyqtgraph import InfiniteLine, TextItem

# 引入模块
from data_dispatcher import DataHandler
from strategy_engine import QuantalyticsEngine
from ai_agent import AIAgent
from portfolio_manager import PortfolioManager


# --- 交易线程 ---
class TradingWorker(QThread):
    data_updated = pyqtSignal(float, str, str, object)

    def __init__(self):
        super().__init__()
        self.is_running = True
        self.data_handler = DataHandler(max_len=200)
        self.strategy = QuantalyticsEngine()

    def run(self):
        self.data_handler.initialize()
        while self.is_running:
            try:
                price = self.data_handler.fetch_realtime_price()
                if price is not None:
                    raw_df = self.data_handler.update_tick(price)
                    signal, reason, processed_df = self.strategy.check_signal(raw_df)
                    self.data_updated.emit(price, signal, reason, processed_df)
                time.sleep(3)
            except Exception as e:
                time.sleep(5)

    def stop(self):
        self.is_running = False


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
        self.setWindowTitle("AI 黄金理财终端 (Day 5 - 最终修复版)")
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

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # === 左侧：图表 ===
        chart_layout = QVBoxLayout()
        pg.setConfigOptions(antialias=True)
        self.plot_widget = pg.PlotWidget(axisItems={'bottom': pg.DateAxisItem()})
        self.plot_widget.setBackground('#000000')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setTitle("Au99.99 实时走势", color="#aaa", size="12pt")
        chart_layout.addWidget(self.plot_widget)
        main_layout.addLayout(chart_layout, stretch=6)

        # === 新增：初始化十字光标 ===
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

        action_layout.addWidget(self.lbl_action)
        action_layout.addWidget(self.lbl_amount)
        action_layout.addWidget(self.btn_calc)
        panel_layout.addWidget(group_action)

        panel_layout.addStretch()
        scroll.setWidget(panel)
        main_layout.addWidget(scroll, stretch=4)

    def update_tech_ui(self, price, signal, reason, df):
        """更新技术面图表 (专业版)"""
        self.current_price = price
        self.current_tech_signal = signal
        self.price_label.setText(f"¥{price:.2f}")

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

            # 1. 准备 K 线数据
            # 格式: (timestamp, open, close, low, high)
            ohlc_data = []
            for index, row in df.iterrows():
                t = index.timestamp()
                ohlc_data.append((t, row['Open'], row['Close'], row['Low'], row['High']))

            # 2. 绘制 K 线 (放到最底层)
            candle_item = CandlestickItem(ohlc_data)
            self.plot_widget.addItem(candle_item)

            timestamps = [x[0] for x in ohlc_data]

            # 3. 绘制均线 (SMA) - 这就是你要的"专业曲线"
            # 快线 (SMA_F): 黄色
            if 'SMA_F' in df.columns:
                self.plot_widget.plot(timestamps, df['SMA_F'].values, pen=pg.mkPen('#ffff00', width=1), name="SMA Fast")

            # 慢线 (SMA_S): 紫色
            if 'SMA_S' in df.columns:
                self.plot_widget.plot(timestamps, df['SMA_S'].values, pen=pg.mkPen('#da70d6', width=1), name="SMA Slow")

            # 4. 绘制布林带 (Bollinger Bands) - 蓝色细线
            if 'BBU' in df.columns:
                self.plot_widget.plot(timestamps, df['BBU'].values,
                                      pen=pg.mkPen('#00bfff', width=1, style=Qt.PenStyle.DashLine))
                self.plot_widget.plot(timestamps, df['BBL'].values,
                                      pen=pg.mkPen('#00bfff', width=1, style=Qt.PenStyle.DashLine))

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

    def on_mouse_moved(self, pos):
        """鼠标移动事件：更新十字光标和信息"""
        if self.df_cache is None or self.df_cache.empty:
            return

        # === 核心修改点 ===
        # 原来的写法: if self.plot_widget.sceneBoundingRect().contains(pos):
        # 现在的写法: 只判断 ViewBox (绘图区) 的范围，不包含坐标轴
        view_box = self.plot_widget.plotItem.vb
        if view_box.sceneBoundingRect().contains(pos):

            # 将鼠标的屏幕坐标(Pixels)转换为图表坐标(Axis Values)
            mouse_point = view_box.mapSceneToView(pos)
            x_val = mouse_point.x()
            y_val = mouse_point.y()

            # 2. 找到鼠标 X 轴位置对应的最近的数据点索引
            timestamps = [t.timestamp() for t in self.df_cache.index]

            # 简单的查找算法：找差值最小的那个
            import numpy as np
            arr = np.array(timestamps)
            idx = (np.abs(arr - x_val)).argmin()

            # 获取该行数据
            target_row = self.df_cache.iloc[idx]
            target_time = self.df_cache.index[idx]

            # 3. 更新十字线位置
            self.v_line.setPos(timestamps[idx])
            self.h_line.setPos(y_val)

            # 4. 构造显示文本 (HTML 格式)
            color = "#ff4444" if target_row['Close'] >= target_row['Open'] else "#00cc00"

            info_html = f"""
            <div style='color: #eee; font-size: 12px; font-weight: bold;'>
                <span style='color: #aaa;'>时间:</span> {target_time.strftime('%H:%M:%S')}<br>
                <span style='color: #aaa;'>开盘:</span> <span style='color: {color};'>{target_row['Open']:.2f}</span><br>
                <span style='color: #aaa;'>最高:</span> <span style='color: {color};'>{target_row['High']:.2f}</span><br>
                <span style='color: #aaa;'>最低:</span> <span style='color: {color};'>{target_row['Low']:.2f}</span><br>
                <span style='color: #aaa;'>收盘:</span> <span style='color: {color};'>{target_row['Close']:.2f}</span><br>
            """
            if 'RSI' in target_row:
                info_html += f"<span style='color: #aaa;'>RSI:</span> {target_row['RSI']:.1f}<br>"

            info_html += "</div>"

            # 5. 更新标签
            self.cursor_label.setHtml(info_html)

            # 让标签固定在左上角 (推荐)，避免遮挡 K 线
            # 获取 ViewBox 的当前可视范围 (X轴时间范围, Y轴价格范围)
            view_rect = view_box.viewRange()
            x_start = view_rect[0][0]  # 当前屏幕最左侧的时间戳
            y_top = view_rect[1][1]  # 当前屏幕最顶部的价格

            # 将标签移动到左上角 (稍微偏移一点，留出边距)
            # 注意：mapViewToScene 可以更精确控制，但简单设置坐标通常够用了
            # 这里的坐标是基于数据的，所以需要动态获取当前的 viewRange
            self.cursor_label.setPos(x_start, y_top)

    def closeEvent(self, event):
        self.worker.stop()
        self.ai_worker.stop()
        self.worker.wait()
        self.ai_worker.wait()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())