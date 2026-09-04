"""
LoopAgent Kimi 启动脚本

浏览器模式：

ATTACH_EXISTING = False
    使用 KimiSession 原来的启动路径。
    即由适配器自己启动浏览器。

ATTACH_EXISTING = True
    不走原来的启动路径，
    改为调用：

        KimiSession.attach_existing(**BROWSER_CONFIG)

    浏览器、CDP、URL、Tab 匹配等逻辑全部由
    loop_agent_kimi.py 适配器负责。

因此 Core 不需要知道 URL 是什么。
"""

from loop_agent_core import LoopAgent
from loop_agent_kimi import KimiSession


# ============================================================
# 配置（按需要自己改，你可以先用自带的东西跑一边确认功能正常）
# ============================================================

TASK_DESC = """
优化 B 站评论数据清洗脚本：
删除点赞数小于 10 的评论，
保留原始数据结构，
添加错误处理，
输出统计信息。

输入文件:
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
# 浏览器模式（还没做完，可能有bug，尽量别动）
# ============================================================

# False = 原来的 session.start()
# True  = session.attach_existing(...)
ATTACH_EXISTING = False


# ============================================================
# 浏览器配置
# ============================================================

"""
这些参数不会被 Core 解析。

Core 只会原样执行：

    session.attach_existing(**BROWSER_CONFIG)

所以具体参数名字由 loop_agent_kimi.py 决定。

例如将来适配器定义：

    attach_existing(
        url=None,
        host=None,
        debug_port=9222,
    )

这里就可以：

BROWSER_CONFIG = {
    "url": "https://kimi.moonshot.cn/",
    "debug_port": 9222,
}

也可以完全不指定：

BROWSER_CONFIG = {}

当 ATTACH_EXISTING=False 时，
这个字典不会参与原来的启动路径。
"""

BROWSER_CONFIG = {
    # "url": "https://kimi.moonshot.cn/",
    # "debug_port": 9222,
}


# ============================================================
# 主程序
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("LoopAgent Kimi 启动")
    print("=" * 60)

    print(
        f"[RUN] attach_existing = "
        f"{ATTACH_EXISTING}"
    )

    session = KimiSession()

    agent = LoopAgent(
        session=session,
        runtime_workspace="agent_idle",
        core_goal=CORE_GOAL,
    )

    try:
        # ----------------------------------------------------
        # 浏览器连接
        # ----------------------------------------------------

        agent.start(
            attach_existing=ATTACH_EXISTING,
            browser_config=BROWSER_CONFIG,
        )

        # ----------------------------------------------------
        # 开始循环
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
                "[ROUTER] 本轮曾进入数据反馈模式"
            )

        if result.get("seed_used"):
            print(
                "[ROUTER] 使用了代码种子"
            )

    except Exception as e:
        print(
            f"\n[RUN] 运行异常: {e}"
        )

    finally:
        agent.close()
        print("\n完成")
