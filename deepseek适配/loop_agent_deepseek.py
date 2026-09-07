"""LoopAgent DeepSeek 适配层

基于 loop_agent_core.py / loop_agent_core(2).py 的 Session 接口，
为 DeepSeek 网页版提供浏览器交互。

设计原则：
1. Session 接口保持与 Kimi / Grok 适配层一致。
2. 文件上传优先使用 Windows 剪贴板 CF_HDROP + Ctrl+V。
3. DeepSeek 输入框使用 textarea[name="search"]。
4. 生成状态使用动态 DOM 中的 .ds-button.ds-button--primary。
5. 消息使用 .ds-message / .ds-assistant-message-main-content。
6. Python 代码使用 .md-code-block 内的 <pre> 提取。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

try:
    import win32clipboard
    import win32con
    _CLIPBOARD_AVAILABLE = True
except ImportError:
    _CLIPBOARD_AVAILABLE = False
    print("[WARN] pywin32 未安装，剪贴板上传功能不可用。运行: pip install pywin32")


# ============================================================
# DeepSeek 配置
# ============================================================

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
USER_DATA = os.path.expanduser(r"~\AppData\Local\Microsoft\Edge\User Data")
CURRENT_BROWSER_EXE = "msedge.exe"
CURRENT_DEBUG_PORT = "9222"
CURRENT_USER_DATA = USER_DATA
CURRENT_WORKSPACE = os.path.abspath("agent_idle")

DEEPSEEK_URL = "https://chat.deepseek.com/"

STARTUP_WAIT = 5
READY_TIMEOUT = 60
MAX_GENERATE_WAIT = 300
STOP_APPEAR_TIMEOUT = 20
STOP_GONE_STABLE_POLLS = 2
STABLE_POLLS_REQUIRED = 3
POLL_INTERVAL = 1.0


# ============================================================
# 剪贴板上传器
# ============================================================

class ClipboardUploader:
    """通过系统剪贴板(CF_HDROP)上传文件。"""

    def __init__(self, page):
        self.page = page
        self._last_clipboard = None

    def _backup_clipboard(self) -> None:
        if not _CLIPBOARD_AVAILABLE:
            return
        try:
            win32clipboard.OpenClipboard()
            try:
                self._last_clipboard = win32clipboard.GetClipboardData(
                    win32clipboard.CF_UNICODETEXT
                )
            except Exception:
                self._last_clipboard = None
            finally:
                win32clipboard.CloseClipboard()
        except Exception as exc:
            print(f"[CLIPBOARD] 备份剪贴板失败: {exc}")
            self._last_clipboard = None

    def _restore_clipboard(self) -> None:
        if not _CLIPBOARD_AVAILABLE or self._last_clipboard is None:
            return
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(
                    win32clipboard.CF_UNICODETEXT,
                    self._last_clipboard,
                )
            finally:
                win32clipboard.CloseClipboard()
            print("[CLIPBOARD] 剪贴板已恢复")
        except Exception as exc:
            print(f"[CLIPBOARD] 恢复剪贴板失败: {exc}")

    def _copy_files_to_clipboard(self, file_paths: list[str]) -> bool:
        if not _CLIPBOARD_AVAILABLE:
            print("[CLIPBOARD] pywin32 不可用")
            return False

        valid_paths: list[str] = []
        for fp in file_paths:
            abs_path = os.path.abspath(fp).replace("/", "\\")
            if os.path.isfile(abs_path):
                valid_paths.append(abs_path)
            else:
                print(f"[CLIPBOARD] 文件不存在，跳过: {fp}")

        if not valid_paths:
            print("[CLIPBOARD] 没有有效文件可写入剪贴板")
            return False

        class DROPFILES(ctypes.Structure):
            _fields_ = [
                ("pFiles", wintypes.DWORD),
                ("pt", wintypes.POINT),
                ("fNC", wintypes.BOOL),
                ("fWide", wintypes.BOOL),
            ]

        try:
            offset = ctypes.sizeof(DROPFILES)
            files_data = ("\0".join(valid_paths) + "\0\0").encode("utf-16-le")

            dropfiles = DROPFILES()
            dropfiles.pFiles = offset
            dropfiles.pt.x = 0
            dropfiles.pt.y = 0
            dropfiles.fNC = False
            dropfiles.fWide = True

            data = bytes(dropfiles) + files_data

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
            finally:
                win32clipboard.CloseClipboard()

            print(f"[CLIPBOARD] {len(valid_paths)} 个文件已写入剪贴板")
            return True
        except Exception as exc:
            print(f"[CLIPBOARD] 写入剪贴板失败: {exc}")
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            return False

    def _copy_file_to_clipboard(self, file_path: str) -> bool:
        return self._copy_files_to_clipboard([file_path])

    def upload_file(self, file_path: str, input_selector: str | None = None) -> bool:
        """使用 Ctrl+V 向 DeepSeek 当前输入区粘贴文件。"""
        self._backup_clipboard()
        try:
            if not self._copy_file_to_clipboard(file_path):
                return False

            selector = input_selector or 'textarea[name="search"]'

            try:
                input_box = self.page.locator(selector).first
                input_box.wait_for(
                    state="visible",
                    timeout=READY_TIMEOUT * 1000,
                )
            except Exception as exc:
                print(f"[CLIPBOARD] 未找到 DeepSeek 输入框: {exc}")
                return False

            print("[CLIPBOARD] 聚焦 DeepSeek 输入框...")
            input_box.click()
            time.sleep(0.3)
            print("[CLIPBOARD] 触发粘贴 (Ctrl+V)...")
            self.page.keyboard.press("Control+v")
            time.sleep(1.0)
            print("[CLIPBOARD] 粘贴操作完成")
            return True
        finally:
            self._restore_clipboard()

    def upload_files(self, file_paths: list[str], input_selector: str | None = None) -> bool:
        success_count = 0
        for fp in file_paths:
            if self.upload_file(fp, input_selector):
                success_count += 1
                time.sleep(0.5)
        print(f"[CLIPBOARD] 批量上传完成: {success_count}/{len(file_paths)}")
        return success_count > 0


# ============================================================
# DeepSeek Session
# ============================================================

class DeepSeekSession:
    """复用 Edge 浏览器会话，为 LoopAgent 提供 DeepSeek 网页交互。"""

    def __init__(self):
        self.edge_process = None
        self.browser = None
        self.page = None
        self._playwright = None
        self._connected = False
        self.core_goal = None

    # --------------------------------------------------------
    # Browser lifecycle
    # --------------------------------------------------------

    def start(self):
        if self._connected:
            print("[DEEPSEEK] 会话已经启动")
            return

        print("[DEEPSEEK] 启动Edge浏览器...")
        subprocess.run(
            ["taskkill", "/F", "/IM", "msedge.exe"],
            capture_output=True,
            text=True,
        )
        time.sleep(2)

        self.edge_process = subprocess.Popen([
            EDGE_PATH,
            f"--user-data-dir={USER_DATA}",
            f"--remote-debugging-port={CURRENT_DEBUG_PORT}",
            "--no-first-run",
            "--no-default-browser-check",
        ])
        time.sleep(STARTUP_WAIT)

        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.connect_over_cdp(
            f"http://localhost:{CURRENT_DEBUG_PORT}"
        )

        context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        self.page = context.pages[0] if context.pages else context.new_page()

        print("[DEEPSEEK] 打开DeepSeek...")
        self.page.goto(
            DEEPSEEK_URL,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        print(f"[DEEPSEEK] DOMContentLoaded: {self.page.url}")

        if not self._wait_for_ready():
            self._save_debug_screenshot("deepseek_debug_not_ready.png")
            raise RuntimeError(
                "DeepSeek 页面在等待时间内未出现聊天输入框。"
            )

        self._connected = True
        print(f"[DEEPSEEK] 会话就绪: {self.page.url}")

    def _wait_for_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        deadline = time.time() + timeout
        announced = False

        while time.time() < deadline:
            try:
                # 明确登录页只通过 URL 判断；不要把加载中的页面当作登录失败。
                url = (self.page.url or "").lower()
                if "/login" in url or "/signin" in url or "/sign-in" in url:
                    print("[DEEPSEEK] 检测到登录页面，请在浏览器中完成登录...")

                editor = self.page.locator('textarea[name="search"]').first
                if editor.count() > 0 and editor.is_visible():
                    return True
            except Exception:
                pass

            if not announced:
                print(
                    f"[DEEPSEEK] 等待聊天输入框完成加载（最多 {timeout:.0f}s）..."
                )
                announced = True
            time.sleep(0.5)

        return False

    def _ensure_connected(self):
        if not self._connected or self.page is None:
            raise RuntimeError("DeepSeekSession未启动，先调用start()")

    # --------------------------------------------------------
    # DOM state
    # --------------------------------------------------------

    def get_message_count(self) -> int:
        self._ensure_connected()
        return int(self.page.evaluate(
            """
            () => document.querySelectorAll('.ds-message').length
            """
        ))

    def get_assistant_count(self) -> int:
        self._ensure_connected()
        return int(self.page.evaluate(
            """
            () => document.querySelectorAll(
                '.ds-assistant-message-main-content'
            ).length
            """
        ))

    def get_code_block_count(self) -> int:
        self._ensure_connected()
        return int(self.page.evaluate(
            """
            () => document.querySelectorAll('.md-code-block').length
            """
        ))

    def get_generating_state(self) -> bool:
        """检测截图中确认的 DeepSeek 生成态 primary button。"""
        self._ensure_connected()
        return bool(self.page.evaluate(
            """
            () => {
                const buttons = [
                    ...document.querySelectorAll(
                        '[role="button"].ds-button'
                    )
                ];

                return buttons.some(btn => {
                    const cls = String(btn.className || '');
                    const aria = btn.getAttribute('aria-label') || '';
                    const text = btn.innerText || '';

                    return (
                        cls.includes('ds-button--primary')
                        && !cls.includes('ds-button--disabled')
                    ) || /stop|停止|cancel|取消/i.test(
                        `${aria} ${text}`
                    );
                });
            }
            """
        ))

    def _get_last_assistant_signature(self) -> dict:
        self._ensure_connected()
        return self.page.evaluate(
            """
            () => {
                const messages = document.querySelectorAll(
                    '.ds-assistant-message-main-content'
                );

                if (!messages.length) {
                    return {
                        exists: false,
                        messageLength: 0,
                        codeLength: 0,
                        codeBlockCount: 0
                    };
                }

                const message = messages[messages.length - 1];
                const codeBlocks = message.querySelectorAll(
                    '.md-code-block pre'
                );

                const text = message.innerText || message.textContent || '';
                let codeText = '';

                if (codeBlocks.length) {
                    const last = codeBlocks[codeBlocks.length - 1];
                    codeText = last.innerText || last.textContent || '';
                }

                return {
                    exists: true,
                    messageLength: text.length,
                    codeLength: codeText.length,
                    codeBlockCount: codeBlocks.length
                };
            }
            """
        )

    def _get_last_python_code(self) -> str:
        self._ensure_connected()
        return self.page.evaluate(
            """
            () => {
                const messages = document.querySelectorAll(
                    '.ds-assistant-message-main-content'
                );

                if (!messages.length) {
                    return '';
                }

                const message = messages[messages.length - 1];
                const blocks = message.querySelectorAll('.md-code-block');

                if (!blocks.length) {
                    return '';
                }

                // DeepSeek 的 code block banner 中存在明确的 python 文本。
                for (let i = blocks.length - 1; i >= 0; i--) {
                    const block = blocks[i];
                    const banner = block.querySelector(
                        '.md-code-block-banner'
                    );
                    const langText = banner
                        ? (banner.innerText || banner.textContent || '')
                        : '';
                    const pre = block.querySelector('pre');

                    if (pre && /\\bpython\\b/i.test(langText)) {
                        return pre.innerText || pre.textContent || '';
                    }
                }

                const lastPre = blocks[blocks.length - 1].querySelector('pre');
                return lastPre
                    ? (lastPre.innerText || lastPre.textContent || '')
                    : '';
            }
            """
        ) or ""

    # --------------------------------------------------------
    # Generation
    # --------------------------------------------------------

    def generate(
        self,
        task_description: str,
        task_idx: int = 0,
        workspace: str = "code_library",
    ):
        self._ensure_connected()

        print(f"[DEEPSEEK] 发送任务 (idx={task_idx})...")

        initial_assistant = self.get_assistant_count()
        initial_messages = self.get_message_count()
        initial_codes = self.get_code_block_count()

        print(
            f"[DEEPSEEK] 当前 assistant: {initial_assistant}, "
            f"messages: {initial_messages}, code blocks: {initial_codes}"
        )

        input_box = self.page.locator('textarea[name="search"]').first
        try:
            input_box.wait_for(
                state="visible",
                timeout=READY_TIMEOUT * 1000,
            )
        except Exception as exc:
            print(f"[DEEPSEEK] 未找到输入框: {exc}")
            return None

        prompt = self._build_prompt(task_description)
        input_box.fill(prompt)
        time.sleep(0.5)

        print("[DEEPSEEK] 提交任务...")
        input_box.press("Enter")

        start_time = time.time()

        # ----------------------------------------------------
        # 阶段1：等待生成开始
        # ----------------------------------------------------
        print(
            "[DEEPSEEK] 阶段1: 等待动态 primary 按钮出现..."
        )

        started = False
        deadline = min(
            start_time + STOP_APPEAR_TIMEOUT,
            start_time + MAX_GENERATE_WAIT,
        )

        while time.time() < deadline:
            try:
                generating = self.get_generating_state()
                assistant_count = self.get_assistant_count()
                message_count = self.get_message_count()
                code_count = self.get_code_block_count()

                if generating:
                    print("[DEEPSEEK] 检测到生成状态")
                    started = True
                    break

                # 极短响应 fallback
                if assistant_count > initial_assistant:
                    print(
                        f"[DEEPSEEK] assistant 数量变化: "
                        f"{initial_assistant} -> {assistant_count}"
                    )
                    started = True
                    break

                if (
                    message_count > initial_messages
                    or code_count > initial_codes
                ):
                    print("[DEEPSEEK] 检测到新消息/代码块")
                    started = True
                    break
            except Exception:
                pass

            time.sleep(0.25)

        if not started:
            print("[DEEPSEEK] 未检测到生成开始")
            self._save_debug_screenshot("deepseek_debug_no_response.png")
            return None

        # ----------------------------------------------------
        # 阶段2：等待生成结束 + 内容稳定
        # ----------------------------------------------------
        print(
            "[DEEPSEEK] 阶段2: 等待生成结束并确认响应稳定..."
        )

        last_signature = None
        stable_count = 0
        primary_gone_count = 0

        while time.time() - start_time < MAX_GENERATE_WAIT:
            try:
                generating = self.get_generating_state()
                signature = self._get_last_assistant_signature()

                compact = (
                    signature.get("codeBlockCount", 0),
                    signature.get("codeLength", 0),
                    signature.get("messageLength", 0),
                )

                if (
                    compact == last_signature
                    and signature.get("messageLength", 0) > 0
                ):
                    stable_count += 1
                else:
                    stable_count = 0
                    last_signature = compact

                if generating:
                    primary_gone_count = 0
                else:
                    primary_gone_count += 1

                if (
                    not generating
                    and primary_gone_count >= STOP_GONE_STABLE_POLLS
                    and stable_count >= STABLE_POLLS_REQUIRED
                ):
                    print(
                        "[DEEPSEEK] 生成完成 "
                        f"（primary 已消失 + 内容稳定）: "
                        f"message={signature.get('messageLength', 0)}, "
                        f"code={signature.get('codeLength', 0)}, "
                        f"blocks={signature.get('codeBlockCount', 0)}"
                    )
                    break
            except Exception:
                pass

            elapsed = time.time() - start_time
            if int(elapsed) > 0 and int(elapsed) % 15 == 0:
                print(
                    f"[DEEPSEEK] 仍在生成中... 已等待 {int(elapsed)}s"
                )

            time.sleep(POLL_INTERVAL)
        else:
            print(
                f"[DEEPSEEK] 警告: 达到最大等待时间 {MAX_GENERATE_WAIT}s，"
                "强制提取当前结果"
            )

        # ----------------------------------------------------
        # 阶段3：提取代码
        # ----------------------------------------------------
        time.sleep(1)
        print("[DEEPSEEK] 阶段3: 提取 Python 代码...")

        code = self._get_last_python_code().replace("\u00a0", " ")
        print(f"[DEEPSEEK] 提取代码长度: {len(code)}")

        if not code.strip():
            self._save_debug_screenshot("deepseek_debug_empty_code.png")
            print("[DEEPSEEK] 代码为空，截图保存")
            return None

        return self._save_code(
            task_description,
            code,
            task_idx,
            workspace,
        )

    def _build_prompt(self, task_description: str) -> str:
        core_section = (
            f"\n【核心目标】\n{self.core_goal}\n"
            if self.core_goal
            else ""
        )
        return f"""你是一个Python代码生成助手。请遵循以下规则：
1. 只能生成一个完整的Python代码块，包含所有必要内容
2. 代码必须自包含，可直接运行，包含所有import和依赖
3. 在代码末尾添加 # END_AND_END 标记表示结束
4. 不要输出多个代码块，不要输出解释性文字在代码块外
{core_section}
用户请求：{task_description}
"""

    def _save_code(
        self,
        task_description: str,
        code: str,
        task_idx: int,
        workspace: str,
    ):
        workspace_path = Path(workspace)
        workspace_path.mkdir(parents=True, exist_ok=True)

        filename = f"{time.strftime('%Y%m%d')}_{task_idx:03d}.py"
        filepath = workspace_path / filename
        has_marker = "END_AND_END" in code

        if not has_marker:
            print("[DEEPSEEK] 警告: 代码缺少END_AND_END标记")

        safe_desc = task_description.replace("\n", " | ").replace("\r", "")
        header = f"""# {filename}
# TASK_IDX: {task_idx}
# KEYWORDS: (待补充)
# SOURCE_DESC: {safe_desc}
# GENERATED_BY: deepseek_web
# TIMESTAMP: {time.strftime('%Y-%m-%d %H:%M:%S')}
# HAS_END_MARKER: {has_marker}

"""
        filepath.write_text(header + code, encoding="utf-8")
        print(
            f"[DEEPSEEK] 已保存: {filepath} "
            f"(标记检测: {has_marker})"
        )
        return filepath

    # --------------------------------------------------------
    # Upload compatibility API
    # --------------------------------------------------------

    def upload_file_via_clipboard(self, file_path: str) -> bool:
        if not self._connected:
            print("[SESSION] 未连接，无法上传")
            return False
        return ClipboardUploader(self.page).upload_file(file_path)

    def upload_files_via_clipboard(self, file_paths: list[str]) -> bool:
        if not self._connected:
            print("[SESSION] 未连接，无法上传")
            return False
        return ClipboardUploader(self.page).upload_files(file_paths)

    def upload_file(self, file_path: str) -> bool:
        return self.upload_file_via_clipboard(file_path)

    def upload_files(self, file_paths: list[str]) -> bool:
        return self.upload_files_via_clipboard(file_paths)

    # --------------------------------------------------------
    # Diagnostics / cleanup
    # --------------------------------------------------------

    def _save_debug_screenshot(self, filename: str) -> None:
        try:
            if self.page:
                self.page.screenshot(path=filename, full_page=True)
                print(f"[DEEPSEEK] 调试截图: {filename}")
        except Exception as exc:
            print(f"[DEEPSEEK] 保存调试截图失败: {exc}")

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        except Exception as exc:
            print(f"[DEEPSEEK] 关闭 browser 失败: {exc}")

        try:
            if self._playwright:
                self._playwright.stop()
        except Exception as exc:
            print(f"[DEEPSEEK] 停止 Playwright 失败: {exc}")

        try:
            if self.edge_process:
                self.edge_process.terminate()
        except Exception as exc:
            print(f"[DEEPSEEK] 结束 Edge 进程失败: {exc}")

        self._connected = False
        print("[DEEPSEEK] 会话关闭")


__all__ = [
    "DeepSeekSession",
    "ClipboardUploader",
    "EDGE_PATH",
    "USER_DATA",
    "CURRENT_BROWSER_EXE",
    "CURRENT_DEBUG_PORT",
    "CURRENT_USER_DATA",
    "CURRENT_WORKSPACE",
]
