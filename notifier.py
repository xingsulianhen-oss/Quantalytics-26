import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

import threading


class EmailNotifier:
    def __init__(self, config=None):
        # ================= 配置区域 =================
        self.smtp_server = "smtp.qq.com"  # SMTP 服务器 (QQ: smtp.qq.com, 163: smtp.163.com)
        self.smtp_port = 465  # SSL 端口通常是 465
        self.sender_email = ""  # 发件人邮箱
        self.password = ""  # 邮箱授权码 (不是登录密码!)
        self.receiver_email = ""  # 收件人 (通常就是发给自己)

        # 从配置字典加载
        if config:
            self.sender_email = config.get('sender', '')
            self.password = config.get('password', '')
            self.receiver_email = config.get('receiver', '')
        # ===========================================

    def send_email(self, subject, content):
        """发送邮件 (异步执行，不卡顿主界面)"""
        if not self.password or "xxxx" in self.password:
            print("[Notifier] 邮箱未配置，跳过发送。")
            return

        def _send():
            try:
                msg = MIMEText(content, 'html', 'utf-8')
                msg['From'] = formataddr(["Quantalytics 交易系统", self.sender_email])
                msg['To'] = formataddr(["Master", self.receiver_email])

                msg['Subject'] = Header(subject, 'utf-8')

                # 连接服务器
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)
                server.login(self.sender_email, self.password)

                # 发送
                server.sendmail(self.sender_email, [self.receiver_email], msg.as_string())
                server.quit()
                print(f"[Notifier] ✅ 邮件发送成功: {subject}")

            except Exception as e:
                # 打印详细错误以便调试
                print(f"[Notifier] ❌ 邮件发送失败: {e}")

        # 启动线程发送
        threading.Thread(target=_send, daemon=True).start()


# 测试代码
if __name__ == "__main__":
    print("正在测试邮件发送...")
    notifier = EmailNotifier()
    notifier.send_email("测试标题", "<h1>你好!</h1><p>这是一封来自量化系统的测试邮件。</p>")
    import time
    time.sleep(3)