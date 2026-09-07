"""LoopAgent DeepSeek 启动脚本"""

from pathlib import Path
import inspect
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loop_agent_core import LoopAgent
from loop_agent_deepseek import DeepSeekSession


TASK_DESC = (
    "基于已上传的代码文件，优化 B 站评论数据清洗脚本："
    "删除点赞数小于 10 的评论，"
    "保留数据结构，"
    "添加错误处理，"
    "输出统计信息。"
    "输入文件: comments_BV1GXJs6kEt3.json"
)

MAX_ITER = 5

CORE_GOAL = """最终目标：实现一个健壮的 B 站评论数据清洗工具。
要求：
- 正确读取 JSON 格式的 B 站评论数据文件
- 删除点赞数（like 字段）小于 10 的评论
- 保留原始数据结构（url/title/bv/total_collected/top_comments）
- 清洗后更新 total_collected 为实际数量
- 输出清洗统计信息（原始数/保留数/删除数）
- 处理边界情况：空评论列表、缺失 like 字段、文件不存在等
- 代码必须自包含，可直接运行
"""


def main() -> int:
    print("=" * 60)
    print("LoopAgent DeepSeek 启动")
    print("=" * 60)

    print(f"[RUN] 项目目录: {PROJECT_ROOT}")
    print(
        "[RUN] Core: "
        f"{Path(inspect.getfile(LoopAgent)).resolve()}"
    )
    print(
        "[RUN] DeepSeek适配层: "
        f"{PROJECT_ROOT / 'loop_agent_deepseek.py'}"
    )
    print(
        "[RUN] 运行空间: "
        f"{PROJECT_ROOT / 'agent_idle'}"
    )

    session = DeepSeekSession()
    agent = LoopAgent(
        session=session,
        core_goal=CORE_GOAL,
        runtime_workspace=PROJECT_ROOT / "agent_idle",
    )

    try:
        agent.start()

        result = agent.self_improve_loop_enhanced(
            task_description=TASK_DESC,
            max_iter=MAX_ITER,
        )

        print(
            f"\n[ROUTER] 结果: {result.get('status')}, "
            f"迭代: {result.get('iterations')}"
        )

        if result.get("data_mode"):
            print("[ROUTER] 本轮曾进入数据反馈模式")
        else:
            print("[ROUTER] 本轮未产生数据反馈文件")

        if result.get("seed_used"):
            print("[ROUTER] 使用了代码种子")
        else:
            print("[ROUTER] 未使用代码种子")

        print(
            "[ROUTER] runtime workspace: "
            f"{result.get('runtime_workspace')}"
        )
        print(
            "[ROUTER] data current: "
            f"{result.get('data_current')}"
        )
        return 0

    except KeyboardInterrupt:
        print("\n[RUN] 用户中断")
        return 130

    except Exception as exc:
        print(
            "\n[RUN] 运行失败: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    finally:
        try:
            agent.close()
        except Exception as exc:
            print(f"[RUN] Agent关闭失败: {exc}")
        print("\n完成")


if __name__ == "__main__":
    raise SystemExit(main())
