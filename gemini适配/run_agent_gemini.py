"""
LoopAgent 启动脚本 - Gemini 版本

用法：
    python run_agent_gemini.py
"""

from loop_agent_core import LoopAgent
from loop_agent_gemini import GeminiSession


# ============================================================
# 配置
# ============================================================

TASK_DESC = """
基于已有代码文件，优化 B 站评论数据清洗脚本：

- 删除点赞数小于 10 的评论
- 保留原始数据结构
- 添加错误处理
- 输出统计信息

输入文件：
comments_BV1GXJs6kEt3.json
"""

MAX_ITER = 5

CORE_GOAL = """
最终目标：实现一个健壮的 B 站评论数据清洗工具。

要求：
- 正确读取 JSON 格式的 B 站评论数据文件
- 删除点赞数（like 字段）小于 10 的评论
- 保留原始数据结构（url/title/bv/total_collected/top_comments）
- 清洗后更新 total_collected 为实际数量
- 输出清洗统计信息（原始数/保留数/删除数）
- 处理边界情况：空评论列表、缺失 like 字段、文件不存在等
- 代码必须自包含，可直接运行
"""


# ============================================================
# 浏览器模式
# ============================================================

# False：
#   使用 GeminiSession.start()
#   自动启动 Edge
#
# True：
#   使用 GeminiSession.attach_existing()
#   连接已经运行的 Edge

ATTACH_EXISTING = False


# ============================================================
# 已有浏览器配置
# ============================================================

# 当 ATTACH_EXISTING = True 时使用。
#
# Core 不解析这些参数，
# 会原样传给 GeminiSession.attach_existing()。
#
# 可以留空，使用适配器默认配置。

BROWSER_CONFIG = {
    # "url": "https://gemini.google.com/app",
    # "debug_port": 9222,
}


# ============================================================
# 主程序
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("LoopAgent 启动 [Gemini 版本]")
    print("=" * 60)

    print(
        f"[RUN] attach_existing = "
        f"{ATTACH_EXISTING}"
    )

    session = GeminiSession()

    agent = LoopAgent(
        session=session,
        runtime_workspace="agent_idle",
        core_goal=CORE_GOAL,
    )

    try:

        # ----------------------------------------------------
        # 启动 / 附着浏览器
        # ----------------------------------------------------

        agent.start(
            attach_existing=ATTACH_EXISTING,
            browser_config=BROWSER_CONFIG,
        )

        # ----------------------------------------------------
        # 开始自我完善循环
        # ----------------------------------------------------

        result = agent.self_improve_loop_enhanced(
            task_description=TASK_DESC,
            max_iter=MAX_ITER,
        )

        # ----------------------------------------------------
        # 输出结果
        # ----------------------------------------------------

        print(
            f"\n[ROUTER] 结果: "
            f"{result['status']}, "
            f"迭代: {result['iterations']}"
        )

        if result.get("data_mode"):
            print(
                "[ROUTER] "
                "本轮曾进入数据反馈模式"
            )

        if result.get("seed_used"):
            print(
                "[ROUTER] "
                "使用了代码种子"
            )

    except Exception as e:

        print(
            f"\n[RUN] 运行异常: {e}"
        )

    finally:

        agent.close()

        print(
            "\n完成"
        )
