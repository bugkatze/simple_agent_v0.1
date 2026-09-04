"""
LoopAgent Core - 通用逻辑层

核心职责：
1. 管理 agent_idle 工作空间
2. 管理 seed / 输入文件 / feedback 文件
3. 调用模型适配器生成代码
4. 执行生成的 Python
5. 驱动多轮自我改进循环

浏览器职责不属于 Core。
如果需要复用现有浏览器，由具体 Session 适配器实现：
    session.attach_existing(**browser_config)

如果 attach_existing=False，则继续调用：
    session.start()
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ============================================================
# 配置
# ============================================================

MAX_ITERATIONS = 10
RUN_TIMEOUT = 60

DANGEROUS_OPS = [
    "eval(",
    "exec(",
    "__import__(",
]


# ============================================================
# 工作空间
# ============================================================

class RuntimeWorkspace:
    """
    标准工作空间：

    agent_idle/
    ├── seed/
    ├── file_database/
    │   ├── current/
    │   └── data_history/
    └── *.py / 输入文件
    """

    def __init__(self, root="agent_idle"):
        self.root = Path(root).resolve()

        self.seed = self.root / "seed"

        self.file_database = self.root / "file_database"
        self.current = self.file_database / "current"
        self.data_history = self.file_database / "data_history"

        self.root.mkdir(parents=True, exist_ok=True)
        self.seed.mkdir(parents=True, exist_ok=True)
        self.current.mkdir(parents=True, exist_ok=True)
        self.data_history.mkdir(parents=True, exist_ok=True)

        print(f"[WORKSPACE] {self.root}")
        print(f"[WORKSPACE] seed: {self.seed}")
        print(f"[WORKSPACE] current: {self.current}")
        print(f"[WORKSPACE] history: {self.data_history}")

    def get_seed_file(self):
        seed_file = self.seed / "seed.py"
        return seed_file if seed_file.exists() else None

    def get_input_files(self):
        """
        自动发现 agent_idle 顶层输入文件。

        排除：
        - 所有目录
        - *.py
        """
        files = []

        for path in self.root.iterdir():
            if not path.is_file():
                continue

            if path.suffix.lower() == ".py":
                continue

            files.append(path)

        return sorted(files, key=lambda p: p.name.lower())

    def get_generated_python_files(self):
        """
        获取 agent_idle 顶层 Python 文件。

        seed.py 在 seed/ 目录，因此不会被算进来。
        """
        files = [
            p for p in self.root.iterdir()
            if p.is_file() and p.suffix.lower() == ".py"
        ]

        return sorted(
            files,
            key=lambda p: p.stat().st_mtime
        )

    def get_latest_python_file(self):
        files = self.get_generated_python_files()
        return files[-1] if files else None


# ============================================================
# 文件数据库
# ============================================================

class FileDatabase:
    """
    管理：
        file_database/current/
        file_database/data_history/

    使用 文件名 + size + mtime_ns + hash
    判断 current 中是否出现新的反馈文件或发生修改。
    """

    def __init__(self, workspace: RuntimeWorkspace):
        self.workspace = workspace
        self.current = workspace.current
        self.history = workspace.data_history

        self._last_signatures = {}
        self._scan()

    def _file_signature(self, path: Path):
        try:
            stat = path.stat()

            sha256 = hashlib.sha256()

            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    sha256.update(chunk)

            return (
                stat.st_size,
                stat.st_mtime_ns,
                sha256.hexdigest(),
            )

        except Exception:
            return None

    def _scan(self):
        signatures = {}

        for path in self.current.iterdir():
            if path.is_file():
                signatures[path.name] = self._file_signature(path)

        self._last_signatures = signatures

    def detect_changed_files(self):
        """
        检测：
        - 新文件
        - 文件内容修改
        """
        current_signatures = {}

        for path in self.current.iterdir():
            if path.is_file():
                current_signatures[path.name] = self._file_signature(path)

        changed = []

        for name, signature in current_signatures.items():
            if name not in self._last_signatures:
                changed.append(self.current / name)
                continue

            if signature != self._last_signatures[name]:
                changed.append(self.current / name)

        self._last_signatures = current_signatures

        return sorted(changed, key=lambda p: p.name.lower())

    def get_all_files(self):
        return sorted(
            [p for p in self.current.iterdir() if p.is_file()],
            key=lambda p: p.name.lower()
        )

    def has_files(self):
        return any(p.is_file() for p in self.current.iterdir())

    def archive_current(self):
        """
        当前反馈文件进入 data_history。
        """
        files = [
            p for p in self.current.iterdir()
            if p.is_file()
        ]

        if not files:
            return

        timestamp = time.strftime("%Y%m%d_%H%M%S")

        for path in files:
            target = self.history / f"{path.stem}_{timestamp}{path.suffix}"

            counter = 1

            while target.exists():
                target = self.history / (
                    f"{path.stem}_{timestamp}_{counter}{path.suffix}"
                )
                counter += 1

            shutil.move(str(path), str(target))

            print(
                f"[FILE_DB] 归档: "
                f"{path.name} -> data_history/{target.name}"
            )

        self._scan()

    def clear_current(self):
        for path in self.current.iterdir():
            if path.is_file():
                path.unlink()

        self._scan()

        print("[FILE_DB] current/ 已清空")

    def list_history(self):
        return sorted(
            [p for p in self.history.iterdir() if p.is_file()],
            key=lambda p: p.stat().st_mtime
        )


# ============================================================
# Python 执行器
# ============================================================

class CodeRunner:
    def __init__(self, workspace: RuntimeWorkspace, timeout=RUN_TIMEOUT):
        self.workspace = workspace
        self.timeout = timeout

    def write_code(self, filename: str, code: str):
        path = self.workspace.root / filename
        path.write_text(code, encoding="utf-8")
        return path

    def run(self, filename: str):
        path = self.workspace.root / filename

        if not path.exists():
            return {
                "success": False,
                "output": "",
                "stderr": f"文件不存在: {path}",
                "runtime": 0,
                "ended_by": "error",
            }

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        start_time = time.time()

        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(path.name),
                ],
                cwd=str(self.workspace.root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            stdout, stderr = proc.communicate(
                timeout=self.timeout
            )

            runtime = time.time() - start_time

            stdout = stdout or ""
            stderr = stderr or ""

            combined = stdout + stderr

            if proc.returncode == 0:
                if combined.strip():
                    print(
                        f"[RUNNER] 成功，"
                        f"耗时 {runtime:.2f}s，"
                        f"输出 {len(combined)} 字符"
                    )

                    return {
                        "success": True,
                        "output": combined,
                        "stderr": stderr,
                        "runtime": runtime,
                        "ended_by": "normal",
                    }

                print(
                    f"[RUNNER] 返回码 0，但无输出，"
                    f"耗时 {runtime:.2f}s"
                )

                return {
                    "success": False,
                    "output": "",
                    "stderr": stderr,
                    "runtime": runtime,
                    "ended_by": "no_output",
                }

            print(
                f"[RUNNER] 失败，返回码 {proc.returncode}"
            )

            if stderr:
                print(stderr)

            return {
                "success": False,
                "output": combined,
                "stderr": stderr,
                "runtime": runtime,
                "ended_by": "error",
            }

        except subprocess.TimeoutExpired:
            proc.kill()

            stdout, stderr = proc.communicate()

            runtime = time.time() - start_time

            stdout = stdout or ""
            stderr = stderr or ""

            print(
                f"[RUNNER] 超时 ({self.timeout}s)"
            )

            return {
                "success": False,
                "output": stdout + stderr,
                "stderr": stderr,
                "runtime": runtime,
                "ended_by": "timeout",
            }

        except Exception as e:
            runtime = time.time() - start_time

            print(f"[RUNNER] 执行异常: {e}")

            return {
                "success": False,
                "output": "",
                "stderr": str(e),
                "runtime": runtime,
                "ended_by": "error",
            }


# ============================================================
# 安全检查
# ============================================================

class SafetyChecker:

    @staticmethod
    def check_task(task: str):
        if not task:
            return False, "任务为空"

        task_lower = task.lower()

        self_refs = [
            "loop_agent_core",
            "loop_agent_kimi",
            "kimi_session",
        ]

        hits = [
            keyword
            for keyword in self_refs
            if keyword in task_lower
        ]

        if hits:
            return False, f"自引用风险: {hits}"

        return True, ""

    @staticmethod
    def check_code(code: str, workspace: RuntimeWorkspace):
        if not code:
            return False, "代码为空"

        code_lower = code.lower()

        for op in DANGEROUS_OPS:
            if op in code:
                return False, f"危险操作: {op}"

        workspace_path = str(
            workspace.root.resolve()
        ).lower()

        if workspace_path in code_lower:
            return False, "代码试图直接引用当前 Agent 工作目录"

        if "loop_agent_core" in code_lower:
            return False, "代码试图导入 LoopAgent Core"

        if "loop_agent_kimi" in code_lower:
            return False, "代码试图导入模型适配器"

        return True, ""


# ============================================================
# 提示词处理
# ============================================================

def sanitize_prompt(text: str):
    if not text:
        return ""

    replacements = {
        "，": ", ",
        "。": ". ",
        "：": ": ",
        "；": "; ",
        "！": "! ",
        "？": "? ",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "「": '"',
        "」": '"',
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }

    for source, target in replacements.items():
        text = text.replace(source, target)

    return text


# ============================================================
# 主控 Agent
# ============================================================

class LoopAgent:

    def __init__(
        self,
        session,
        runtime_workspace="agent_idle",
        core_goal: str = None,
        timeout=RUN_TIMEOUT,
    ):
        self.workspace = RuntimeWorkspace(
            runtime_workspace
        )

        self.file_db = FileDatabase(
            self.workspace
        )

        self.runner = CodeRunner(
            self.workspace,
            timeout=timeout,
        )

        self.safety = SafetyChecker()

        self.session = session

        self.core_goal = core_goal

        # 给适配器保留核心目标接口
        self.session.core_goal = core_goal

    # --------------------------------------------------------
    # 浏览器 / Session 启动
    # --------------------------------------------------------

    def start(
        self,
        attach_existing=False,
        browser_config=None,
    ):
        """
        attach_existing=False:
            继续走原来的 session.start()

        attach_existing=True:
            调用适配器提供的：
                session.attach_existing(**browser_config)

        Core 不理解：
            - URL
            - host
            - Tab
            - CDP port
            - 浏览器类型

        这些全部由具体适配器决定。
        """

        browser_config = browser_config or {}

        if attach_existing:
            attach_method = getattr(
                self.session,
                "attach_existing",
                None,
            )

            if not callable(attach_method):
                raise AttributeError(
                    "当前 Session 适配器没有实现 "
                    "attach_existing(**browser_config)"
                )

            print("[BROWSER] attach_existing=True")
            print("[BROWSER] 使用适配器的已有浏览器连接逻辑")

            result = attach_method(**browser_config)

            if result is False:
                raise RuntimeError(
                    "Session.attach_existing() 连接失败"
                )

            return

        print("[BROWSER] attach_existing=False")
        print("[BROWSER] 使用原有 session.start()")

        self.session.start()

    def close(self):
        try:
            self.session.close()
        except Exception as e:
            print(f"[CORE] Session 关闭异常: {e}")

    # --------------------------------------------------------
    # 生成
    # --------------------------------------------------------

    def generate_and_save(
        self,
        task_description: str,
        task_idx: int = 0,
    ):
        """
        将 workspace 交给适配器。

        适配器负责：
        - 调用具体模型
        - 提取代码
        - 保存代码

        Core 不负责网页解析。
        """

        return self.session.generate(
            task_description,
            task_idx,
            workspace=str(self.workspace.root),
        )

    # --------------------------------------------------------
    # Seed
    # --------------------------------------------------------

    def seed_with_file(
        self,
        file_path: str,
        task_description: str = None,
    ):
        path = Path(file_path)

        print("\n" + "=" * 50)
        print("[SEED] 上传代码种子")
        print(f"[SEED] 文件: {path}")
        print("=" * 50)

        if not path.exists():
            print("[SEED] 文件不存在")
            return False

        try:
            success = self.session.upload_file(
                str(path)
            )
        except Exception as e:
            print(f"[SEED] 上传异常: {e}")
            return False

        if not success:
            print("[SEED] 文件上传失败")
            return False

        print("[SEED] 文件上传成功")

        time.sleep(2)

        return True

    # --------------------------------------------------------
    # 输入文件
    # --------------------------------------------------------

    def upload_input_files(self):
        files = self.workspace.get_input_files()

        if not files:
            print("[INPUT] 没有顶层输入文件")
            return False

        print(
            f"\n[INPUT] 检测到 "
            f"{len(files)} 个顶层输入文件"
        )

        for path in files:
            print(f"  - {path.name}")

        try:
            success = self.session.upload_files(
                [str(p) for p in files]
            )
        except Exception as e:
            print(f"[INPUT] 上传异常: {e}")
            return False

        print(
            f"[INPUT] 上传结果: "
            f"{'成功' if success else '失败'}"
        )

        return success

    # --------------------------------------------------------
    # Feedback 文件
    # --------------------------------------------------------

    def upload_data_files(self, file_paths):
        if not file_paths:
            return False

        print(
            f"\n[DATA] 上传反馈文件: "
            f"{len(file_paths)} 个"
        )

        for path in file_paths:
            print(f"  - {path.name}")

        try:
            success = self.session.upload_files(
                [str(p) for p in file_paths]
            )
        except Exception as e:
            print(f"[DATA] 上传异常: {e}")
            return False

        print(
            f"[DATA] 上传结果: "
            f"{'成功' if success else '失败'}"
        )

        return success

    # --------------------------------------------------------
    # 运行最新代码
    # --------------------------------------------------------

    def run_latest(self):
        latest = self.workspace.get_latest_python_file()

        if latest is None:
            print("[AUTO-RUN] 没有找到 Python 文件")

            return {
                "status": "error",
                "error": "没有找到生成代码",
            }

        print("\n" + "=" * 50)
        print("[AUTO-RUN] 执行最新代码")
        print(f"[AUTO-RUN] 目标: {latest.name}")
        print("=" * 50)

        try:
            code = latest.read_text(
                encoding="utf-8"
            )
        except Exception as e:
            return {
                "status": "error",
                "file": latest.name,
                "error": str(e),
            }

        safe, reason = self.safety.check_code(
            code,
            self.workspace,
        )

        if not safe:
            print(f"[SAFETY] {reason}")

            return {
                "status": "rejected",
                "file": latest.name,
                "error": reason,
            }

        result = self.runner.run(
            latest.name
        )

        if result["success"]:
            print("[AUTO-RUN] 执行成功")

            return {
                "status": "ok",
                "file": latest.name,
                "output": result["output"],
                "stderr": result.get("stderr", ""),
                "runtime": result["runtime"],
            }

        if result["ended_by"] == "timeout":
            return {
                "status": "timeout",
                "file": latest.name,
                "output": result["output"][:2000],
                "stderr": result.get("stderr", ""),
            }

        if result["ended_by"] == "no_output":
            return {
                "status": "no_output",
                "file": latest.name,
                "output": "",
                "stderr": result.get("stderr", ""),
            }

        return {
            "status": "error",
            "file": latest.name,
            "output": result.get("output", ""),
            "stderr": result.get("stderr", ""),
        }

    # --------------------------------------------------------
    # 自我完善循环
    # --------------------------------------------------------

    def self_improve_loop_enhanced(
        self,
        task_description: str,
        max_iter: int = MAX_ITERATIONS,
    ):
        print("\n" + "#" * 60)
        print("# 自我完善循环")
        print(f"# 任务: {task_description}")
        print(f"# 最大迭代: {max_iter}")
        print(f"# 工作空间: {self.workspace.root}")
        print("#" * 60)

        task_description = sanitize_prompt(
            task_description
        )

        # ----------------------------------------------------
        # 自动发现 seed
        # ----------------------------------------------------

        seed_file = self.workspace.get_seed_file()

        has_seed = seed_file is not None

        if has_seed:
            print(
                f"[SEED] 检测到: {seed_file}"
            )

            if self.seed_with_file(
                seed_file,
                task_description,
            ):
                print("[SEED] 使用代码种子")
            else:
                print("[SEED] 上传失败，继续无种子模式")
                has_seed = False
        else:
            print("[SEED] 未检测到 seed.py")

        # ----------------------------------------------------
        # 自动上传初始输入文件
        # ----------------------------------------------------

        self.upload_input_files()

        # ----------------------------------------------------
        # 初始任务
        # ----------------------------------------------------

        if has_seed:
            current_task = sanitize_prompt(
                f"""
基于刚刚上传的代码文件完成以下任务：

{task_description}
"""
            )
        else:
            current_task = task_description

        history = []
        data_mode = False

        # ----------------------------------------------------
        # Loop
        # ----------------------------------------------------

        for i in range(max_iter):
            print("\n" + "=" * 50)
            print(
                f"[LOOP] 迭代 {i + 1}/{max_iter}"
            )

            if data_mode:
                print("[LOOP] 当前模式: 数据反馈")
            else:
                print("[LOOP] 当前模式: 普通代码反馈")

            print("=" * 50)

            # ------------------------------------------------
            # 生成
            # ------------------------------------------------

            saved = self.generate_and_save(
                current_task,
                task_idx=i,
            )

            if not saved:
                print("[LOOP] 代码生成失败")

                history.append({
                    "iteration": i + 1,
                    "task": current_task,
                    "result": {
                        "status": "gen_failed"
                    },
                })

                break

            # ------------------------------------------------
            # 执行
            # ------------------------------------------------

            result = self.run_latest()

            history.append({
                "iteration": i + 1,
                "task": current_task,
                "result": result,
            })

            # ------------------------------------------------
            # 检查 current/
            # ------------------------------------------------

            changed_files = (
                self.file_db.detect_changed_files()
            )

            if changed_files:
                print(
                    f"\n[DATA] 检测到 "
                    f"{len(changed_files)} 个反馈文件"
                )

                for path in changed_files:
                    print(
                        f"  - {path.name}"
                    )

                self.upload_data_files(
                    changed_files
                )

                data_mode = True

                file_names = ", ".join(
                    path.name
                    for path in changed_files
                )

                core_section = ""

                if self.core_goal:
                    core_section = (
                        f"【核心目标】\n"
                        f"{self.core_goal}\n\n"
                    )

                current_task = sanitize_prompt(
                    f"""
基于刚刚上传的数据反馈文件：

{file_names}

改进当前代码。

{core_section}
原始任务:
{task_description}

要求：
1. 分析反馈文件内容
2. 找出当前代码的问题或改进空间
3. 修改代码以更好满足原始任务
4. 输出完整 Python 代码
5. 末尾添加 # END_AND_END
6. 不要输出解释性文字
"""
                )

                continue

            # ------------------------------------------------
            # 正常成功
            # ------------------------------------------------

            if result["status"] == "ok":
                print("\n[LOOP] 执行成功")

                if i >= max_iter - 1:
                    print(
                        "[LOOP] 达到最大迭代次数，结束"
                    )
                    break

                output = result.get(
                    "output",
                    "",
                )

                output_snippet = output.strip()

                core_section = ""

                if self.core_goal:
                    core_section = (
                        f"【核心目标】\n"
                        f"{self.core_goal}\n\n"
                    )

                current_task = sanitize_prompt(
                    f"""
改进以下 Python 代码。

{core_section}
原始任务:
{task_description}

本轮程序输出:
{output_snippet[:5000]}

要求：
1. 优化代码，但必须服务于原始任务
2. 可以修复逻辑问题、边界问题或健壮性问题
3. 输出完整 Python 代码
4. 末尾添加 # END_AND_END
5. 不要输出解释性文字
"""
                )

                print(
                    f"[LOOP] 准备第 {i + 2} 轮"
                )

                continue

            # ------------------------------------------------
            # 无输出
            # ------------------------------------------------

            if result["status"] == "no_output":
                print(
                    "[LOOP] 程序无输出"
                )

                if i >= max_iter - 1:
                    break

                core_section = ""

                if self.core_goal:
                    core_section = (
                        f"【核心目标】\n"
                        f"{self.core_goal}\n\n"
                    )

                current_task = sanitize_prompt(
                    f"""
修复当前 Python 程序。

问题:
程序运行后没有产生任何输出。

{core_section}
原始任务:
{task_description}

要求：
1. 修复问题
2. 确保程序可以正常运行
3. 输出必要的运行结果或统计信息
4. 输出完整 Python 代码
5. 末尾添加 # END_AND_END
6. 不要输出解释性文字
"""
                )

                continue

            # ------------------------------------------------
            # timeout / rejected
            # ------------------------------------------------

            if result["status"] in (
                "timeout",
                "rejected",
            ):
                print(
                    f"[LOOP] {result['status']}，结束"
                )
                break

            # ------------------------------------------------
            # 普通错误
            # ------------------------------------------------

            if i < max_iter - 1:
                stderr = (
                    result.get("stderr", "")
                    or ""
                )

                output = (
                    result.get("output", "")
                    or ""
                )

                error_text = (
                    stderr
                    if stderr.strip()
                    else output
                )

                core_section = ""

                if self.core_goal:
                    core_section = (
                        f"【核心目标】\n"
                        f"{self.core_goal}\n\n"
                    )

                current_task = sanitize_prompt(
                    f"""
修复当前 Python 程序的运行错误。

{core_section}
原始任务:
{task_description}

错误信息:
{error_text[:3000]}

要求：
1. 修复错误
2. 保持原始任务目标不变
3. 输出完整 Python 代码
4. 末尾添加 # END_AND_END
5. 不要输出解释性文字
"""
                )

                print(
                    f"[LOOP] 准备错误修复，"
                    f"第 {i + 2} 轮"
                )

            else:
                print(
                    "[LOOP] 达到最大迭代次数"
                )

        # ----------------------------------------------------
        # 最终结果
        # ----------------------------------------------------

        if history:
            final_status = history[-1][
                "result"
            ].get("status", "unknown")
        else:
            final_status = "unknown"

        return {
            "status": final_status,
            "iterations": len(history),
            "history": history,
            "data_mode": data_mode,
            "seed_used": has_seed,
        }
