import os
from google import genai

# ================= 配置 =================
GEMINI_API_KEY = "AIzaSyBouH98ESv0aSwxh-JI6q6sb98kzhKtn6c"


# =======================================

def list_available_models():
    if GEMINI_API_KEY == "你的_GEMINI_API_KEY_粘贴在这里":
        print("错误：请先在脚本中填入 API Key")
        return

    try:
        print("正在连接 Google 服务器查询可用模型...")

        # 初始化客户端
        client = genai.Client(api_key=GEMINI_API_KEY)

        # 获取列表
        pager = client.models.list()

        print("\n=== 模型列表 (原始名称) ===")
        count = 0
        for model in pager:
            # 直接打印 model.name，不做任何属性检查
            # 有的模型 name 可能是 "models/gemini-1.5-flash"，有的可能是 "gemini-1.5-flash"
            print(f"- {model.name}")

            # 顺便看看有没有显示名，防止报错加个 try
            try:
                if hasattr(model, 'display_name'):
                    print(f"  ({model.display_name})")
            except:
                pass

            count += 1

        print(f"\n共找到 {count} 个模型。")
        print("-" * 30)
        print("【建议】")
        print("请从上面列表中复制一个名字（例如 'gemini-1.5-flash'），")
        print("填入 ai_agent.py 的 MODEL_NAME 变量中。")

    except Exception as e:
        print(f"\n❌ 查询失败: {e}")


if __name__ == "__main__":
    list_available_models()