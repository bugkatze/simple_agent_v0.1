"""
LoopAgent Kimi 适配层

功能：
1. 原有模式：
       session.start()
   由适配器自行启动 Edge，并连接 Kimi。

2. attach_existing 模式：
       session.attach_existing(...)
   连接已经以 CDP Debug 模式运行的浏览器，
   自动寻找 Kimi 页面。

Core 不负责：
- 浏览器类型
- CDP 端口
- URL
- Tab 筛选

这些全部属于适配器。

用法：

    from loop_agent_kimi import KimiSession

    session = KimiSession()

    # 原有模式
    session.start()

    # 或已有浏览器模式
    session.attach_existing(
        debug_port=9222,
        url="https://kimi.moonshot.cn/"
    )

    session.generate("写一个排序算法")

    session.close()
"""

import subprocess
import time
import os
import re
from pathlib import Path


# ============================================================
# 可选依赖：pywin32
# ============================================================

try:
    import win32clipboard
    import win32con

    _CLIPBOARD_AVAILABLE = True

except ImportError:
    _CLIPBOARD_AVAILABLE = False

    print(
        "[WARN] pywin32 未安装，"
        "剪贴板上传功能不可用。"
        "运行: pip install pywin32"
    )


# ============================================================
# Kimi / Edge 配置
# ============================================================

EDGE_PATH = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)

USER_DATA = os.path.expanduser(
    r"~\AppData\Local\Microsoft\Edge\User Data"
)

CURRENT_BROWSER_EXE = "msedge.exe"
CURRENT_DEBUG_PORT = "9222"

CURRENT_USER_DATA = os.path.expanduser(
    r"~\AppData\Local\Microsoft\Edge\User Data"
)


# attach_existing 默认目标
DEFAULT_KIMI_URL = "https://kimi.moonshot.cn/"

# 支持的默认页面关键词
DEFAULT_KIMI_KEYWORDS = [
    "kimi.moonshot.cn",
]


# ============================================================
# 剪贴板上传器
# ============================================================

class ClipboardUploader:
    """
    通过 Windows 剪贴板 CF_HDROP
    向网页输入框粘贴文件。
    """

    def __init__(self, page):
        self.page = page
        self._last_clipboard = None
        self._clipboard_backup_valid = False

    # --------------------------------------------------------
    # 剪贴板备份
    # --------------------------------------------------------

    def _backup_clipboard(self):
        if not _CLIPBOARD_AVAILABLE:
            return

        try:
            win32clipboard.OpenClipboard()

            try:
                self._last_clipboard = (
                    win32clipboard.GetClipboardData(
                        win32clipboard.CF_UNICODETEXT
                    )
                )
                self._clipboard_backup_valid = True

            except Exception:
                self._last_clipboard = None
                self._clipboard_backup_valid = False

            finally:
                win32clipboard.CloseClipboard()

        except Exception as e:
            print(
                f"[CLIPBOARD] 备份剪贴板失败: {e}"
            )

            self._last_clipboard = None
            self._clipboard_backup_valid = False

    # --------------------------------------------------------
    # 剪贴板恢复
    # --------------------------------------------------------

    def _restore_clipboard(self):
        if not _CLIPBOARD_AVAILABLE:
            return

        if not self._clipboard_backup_valid:
            return

        if self._last_clipboard is None:
            return

        try:
            win32clipboard.OpenClipboard()

            try:
                win32clipboard.EmptyClipboard()

                win32clipboard.SetClipboardData(
                    win32clipboard.CF_UNICODETEXT,
                    self._last_clipboard
                )

            finally:
                win32clipboard.CloseClipboard()

            print("[CLIPBOARD] 剪贴板已恢复")

        except Exception as e:
            print(
                f"[CLIPBOARD] 恢复剪贴板失败: {e}"
            )

    # --------------------------------------------------------
    # 文件写入剪贴板
    # --------------------------------------------------------

    def _copy_files_to_clipboard(
        self,
        file_paths: list
    ) -> bool:

        if not _CLIPBOARD_AVAILABLE:
            print(
                "[CLIPBOARD] pywin32 不可用"
            )
            return False

        import ctypes
        from ctypes import wintypes

        valid_paths = []

        for fp in file_paths:

            abs_path = (
                os.path.abspath(fp)
                .replace("/", "\\")
            )

            if os.path.exists(abs_path):
                valid_paths.append(abs_path)

            else:
                print(
                    f"[CLIPBOARD] 文件不存在，"
                    f"跳过: {fp}"
                )

        if not valid_paths:
            print(
                "[CLIPBOARD] 没有有效文件"
            )
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

            files_data = (
                "\0".join(valid_paths)
                + "\0\0"
            )

            files_data = files_data.encode(
                "utf-16-le"
            )

            dropfiles = DROPFILES()

            dropfiles.pFiles = offset
            dropfiles.pt.x = 0
            dropfiles.pt.y = 0
            dropfiles.fNC = False
            dropfiles.fWide = True

            data = (
                bytes(dropfiles)
                + files_data
            )

            win32clipboard.OpenClipboard()

            try:
                win32clipboard.EmptyClipboard()

                win32clipboard.SetClipboardData(
                    win32con.CF_HDROP,
                    data
                )

            finally:
                win32clipboard.CloseClipboard()

            print(
                f"[CLIPBOARD] "
                f"{len(valid_paths)} 个文件已写入剪贴板"
            )

            return True

        except Exception as e:

            print(
                f"[CLIPBOARD] "
                f"写入剪贴板失败: {e}"
            )

            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

            return False

    def _copy_file_to_clipboard(
        self,
        file_path: str
    ) -> bool:

        return self._copy_files_to_clipboard(
            [file_path]
        )

    # --------------------------------------------------------
    # 上传单个文件
    # --------------------------------------------------------

    def upload_file(
        self,
        file_path: str,
        input_selector: str = None
    ) -> bool:

        self._backup_clipboard()

        try:

            if not self._copy_file_to_clipboard(
                file_path
            ):
                return False

            if input_selector:

                input_box = (
                    self.page.query_selector(
                        input_selector
                    )
                )

            else:

                input_box = (
                    self.page.query_selector(
                        'textarea, '
                        '[contenteditable="true"]'
                    )
                )

            if not input_box:
                print(
                    "[CLIPBOARD] 未找到输入框"
                )
                return False

            print(
                "[CLIPBOARD] 聚焦输入框..."
            )

            input_box.click()

            time.sleep(0.3)

            print(
                "[CLIPBOARD] "
                "触发粘贴 (Ctrl+V)..."
            )

            self.page.keyboard.press(
                "Control+v"
            )

            time.sleep(1.0)

            print(
                "[CLIPBOARD] "
                "粘贴操作完成"
            )

            return True

        finally:
            self._restore_clipboard()

    # --------------------------------------------------------
    # 批量上传
    # --------------------------------------------------------

    def upload_files(
        self,
        file_paths: list,
        input_selector: str = None
    ) -> bool:

        if not file_paths:
            return False

        success_count = 0

        for fp in file_paths:

            if self.upload_file(
                fp,
                input_selector
            ):
                success_count += 1

                time.sleep(0.5)

        print(
            "[CLIPBOARD] 批量上传完成: "
            f"{success_count}/{len(file_paths)}"
        )

        return success_count > 0


# ============================================================
# Kimi Session
# ============================================================

class KimiSession:
    """
    Kimi 网页会话。

    start()
        原有路径：
        自己启动 Edge。

    attach_existing()
        新路径：
        连接已经运行的 Debug 浏览器。

    attached mode 下：
        owns_browser = False

    因此 close() 时只断开 CDP，
    不关闭用户原本的浏览器。
    """

    def __init__(self):

        self.edge_process = None

        self.browser = None
        self.context = None
        self.page = None

        self._playwright = None
        self._connected = False

        self.core_goal = None

        # 是否由当前 Session 启动浏览器
        ##self.owns_browser = False
        self.owns_browser = True

    # ========================================================
    # 原有启动路径
    # ========================================================

    def start(self):
        """
        原有模式。

        关闭已有 Edge，
        启动指定 Edge，
        使用 9222，
        打开 Kimi。
        """

        print("[KIMI] 使用原有启动模式")

        print("[KIMI] 启动 Edge 浏览器...")

        # 保留原来的行为
        subprocess.run(
            [
                "taskkill",
                "/F",
                "/IM",
                "msedge.exe",
            ],
            capture_output=True
        )

        time.sleep(2)

        self.edge_process = subprocess.Popen(
            [
                EDGE_PATH,
                f"--user-data-dir={USER_DATA}",
                "--remote-debugging-port=9222",
                "--no-first-run",
                "--no-default-browser-check",
            ]
        )

        self.owns_browser = True

        time.sleep(5)

        self._start_playwright()

        print(
            "[KIMI] 连接 Edge CDP..."
        )

        self.browser = (
            self._playwright
            .chromium
            .connect_over_cdp(
                "http://localhost:9222"
            )
        )

        self.context = (
            self.browser.contexts[0]
            if self.browser.contexts
            else self.browser.new_context()
        )

        self.page = (
            self.context.pages[0]
            if self.context.pages
            else self.context.new_page()
        )

        print("[KIMI] 打开 Kimi...")

        self.page.goto(
            DEFAULT_KIMI_URL
        )

        time.sleep(3)

        self._handle_login()

        self._connected = True

        print(
            "[KIMI] 会话就绪"
        )

    # ========================================================
    # 新增：连接已有浏览器
    # ========================================================

    def attach_existing(
        self,
        debug_port=9222,
        url=None,
        host=None,
        keywords=None,
        wait_seconds=10,
    ):
        """
        连接已经运行的 Chromium / Edge 浏览器。

        参数全部属于适配器，不经过 Core。

        debug_port:
            CDP 调试端口。

        url:
            优先寻找包含该 URL 的 Tab。

        host:
            按 hostname 匹配。

        keywords:
            URL 关键词列表。

        wait_seconds:
            如果刚启动浏览器但页面尚未出现，
            等待多少秒。

        注意：
            此模式绝不启动浏览器，
            也不关闭已有浏览器。
        """

        print(
            "\n[KIMI] 使用 attach_existing 模式"
        )

        print(
            f"[KIMI] CDP 端口: {debug_port}"
        )

        # ----------------------------------------------------
        # 默认目标
        # ----------------------------------------------------

        if url is None and host is None and not keywords:
            url = DEFAULT_KIMI_URL

            keywords = list(
                DEFAULT_KIMI_KEYWORDS
            )

        elif keywords is None:
            keywords = []

        # ----------------------------------------------------
        # Playwright
        # ----------------------------------------------------

        self._start_playwright()

        cdp_url = (
            f"http://localhost:{int(debug_port)}"
        )

        print(
            f"[KIMI] 连接: {cdp_url}"
        )

        try:

            self.browser = (
                self._playwright
                .chromium
                .connect_over_cdp(
                    cdp_url
                )
            )

        except Exception as e:

            self._stop_playwright()

            raise RuntimeError(
                "无法连接已有浏览器。\n"
                f"CDP: {cdp_url}\n"
                "请确认浏览器已经以 "
                "--remote-debugging-port "
                "启动。\n"
                f"原始错误: {e}"
            )

        self.owns_browser = False

        # ----------------------------------------------------
        # 等待目标页面
        # ----------------------------------------------------

        deadline = (
            time.time()
            + float(wait_seconds)
        )

        selected_page = None

        while time.time() < deadline:

            selected_page = self._select_existing_page(
                url=url,
                host=host,
                keywords=keywords,
            )

            if selected_page is not None:
                break

            time.sleep(0.5)

        # ----------------------------------------------------
        # 找不到目标
        # ----------------------------------------------------

        if selected_page is None:

            available_pages = (
                self._list_pages()
            )

            self._stop_playwright()

            raise RuntimeError(
                "已连接浏览器，但没有找到目标 Tab。\n"
                f"目标 url={url!r}\n"
                f"目标 host={host!r}\n"
                f"关键词={keywords!r}\n"
                "当前页面:\n"
                + "\n".join(
                    f"  - {item}"
                    for item in available_pages
                )
            )

        # ----------------------------------------------------
        # 建立 Session
        # ----------------------------------------------------

        self.page = selected_page

        self.context = (
            selected_page.context
        )

        print(
            "[KIMI] 已连接到 Tab:"
        )

        print(
            f"[KIMI] {self.page.url}"
        )

        self._handle_login(
            only_if_needed=True
        )

        self._connected = True

        print(
            "[KIMI] attach_existing 会话就绪"
        )

        return True

    # ========================================================
    # Playwright 初始化
    # ========================================================

    def _start_playwright(self):

        if self._playwright is not None:
            return

        from playwright.sync_api import (
            sync_playwright
        )

        self._playwright = (
            sync_playwright().start()
        )

    def _stop_playwright(self):

        if self._playwright is not None:

            try:
                self._playwright.stop()

            except Exception:
                pass

            self._playwright = None

    # ========================================================
    # 页面筛选
    # ========================================================

    def _select_existing_page(
        self,
        url=None,
        host=None,
        keywords=None,
    ):
        """
        从所有已有 Context / Page 中选择目标 Tab。
        """

        if keywords is None:
            keywords = []

        pages = []

        # Playwright CDP 连接下，
        # browser.contexts 通常包含已有页面。
        for context in self.browser.contexts:

            for page in context.pages:

                pages.append(page)

        if not pages:
            return None

        # ----------------------------------------------------
        # 1. URL 精确 / 包含匹配
        # ----------------------------------------------------

        if url:

            url_lower = str(url).lower()

            for page in pages:

                try:
                    page_url = (
                        page.url
                        or ""
                    ).lower()

                    if page_url == url_lower:
                        print(
                            "[KIMI] URL 精确匹配:"
                            f" {page.url}"
                        )

                        return page

                except Exception:
                    continue

            for page in pages:

                try:
                    page_url = (
                        page.url
                        or ""
                    ).lower()

                    if url_lower in page_url:
                        print(
                            "[KIMI] URL 包含匹配:"
                            f" {page.url}"
                        )

                        return page

                except Exception:
                    continue

        # ----------------------------------------------------
        # 2. hostname 匹配
        # ----------------------------------------------------

        if host:

            host_lower = str(
                host
            ).lower()

            for page in pages:

                try:

                    page_url = (
                        page.url
                        or ""
                    ).lower()

                    if host_lower in page_url:

                        print(
                            "[KIMI] Host 匹配:"
                            f" {page.url}"
                        )

                        return page

                except Exception:
                    continue

        # ----------------------------------------------------
        # 3. 关键词匹配
        # ----------------------------------------------------

        for keyword in keywords:

            keyword_lower = str(
                keyword
            ).lower()

            for page in pages:

                try:

                    page_url = (
                        page.url
                        or ""
                    ).lower()

                    if keyword_lower in page_url:

                        print(
                            "[KIMI] 关键词匹配:"
                            f" {page.url}"
                        )

                        return page

                except Exception:
                    continue

        # ----------------------------------------------------
        # 没有明确匹配
        # ----------------------------------------------------

        return None

    # ========================================================
    # 列出当前页面
    # ========================================================

    def _list_pages(self):

        pages = []

        if self.browser is None:
            return pages

        for context in self.browser.contexts:

            for page in context.pages:

                try:
                    pages.append(
                        page.url or "(空白页)"
                    )

                except Exception:
                    pages.append(
                        "(无法读取 URL)"
                    )

        return pages

    # ========================================================
    # 登录检查
    # ========================================================

    def _handle_login(
        self,
        only_if_needed=False
    ):

        if self.page is None:
            return

        try:

            current_url = (
                self.page.url
                or ""
            ).lower()

            if (
                "login" not in current_url
                and "signin" not in current_url
            ):
                return

            print(
                "[KIMI] 当前页面可能需要登录"
            )

            print(
                "[KIMI] 请手动完成登录..."
            )

            if only_if_needed:
                input(
                    "[KIMI] 登录完成后按回车继续..."
                )
            else:
                input(
                    "[KIMI] 登录完成后按回车继续..."
                )

        except Exception as e:

            print(
                f"[KIMI] 登录检查异常: {e}"
            )

    # ========================================================
    # Actions
    # ========================================================

    def get_actions_count(self):

        if not self.page:
            return 0

        try:

            return self.page.evaluate(
                """
                () => document
                    .querySelectorAll(
                        '.segment-assistant-actions'
                    ).length
                """
            )

        except Exception:
            return 0

    # ========================================================
    # 代码生成
    # ========================================================

    def generate(
        self,
        task_description: str,
        task_idx: int = 0,
        workspace="agent_idle",
    ):
        """
        向 Kimi 发送任务，
        等待生成结束，
        提取最后一个 Python 代码块，
        保存到 workspace。
        """

        if not self._connected:
            raise RuntimeError(
                "KimiSession 未连接，"
                "先调用 start() 或 attach_existing()"
            )

        print(
            f"[KIMI] 发送任务 "
            f"(idx={task_idx})..."
        )

        initial_actions = (
            self.get_actions_count()
        )

        print(
            f"[KIMI] 当前 actions: "
            f"{initial_actions}"
        )

        input_box = (
            self.page.query_selector(
                'textarea, [contenteditable="true"]'
            )
        )

        if not input_box:

            print(
                "[KIMI] 未找到输入框"
            )

            return None

        prompt = self._build_prompt(
            task_description
        )

        input_box.fill(prompt)

        time.sleep(0.5)

        self.page.keyboard.press(
            "Enter"
        )

        print(
            "[KIMI] 等待生成..."
        )

        start_time = time.time()

        max_wait = 300

        # ----------------------------------------------------
        # 阶段 1：
        # 等待 stop 按钮
        # ----------------------------------------------------

        print(
            "[KIMI] 阶段1: "
            "等待 stop 按钮出现..."
        )

        while (
            time.time() - start_time
            < max_wait
        ):

            try:

                stop_exists = (
                    self.page.evaluate(
                        """
                        () => document
                            .querySelectorAll(
                                '.send-button-container.stop'
                            ).length > 0
                        """
                    )
                )

            except Exception:

                stop_exists = False

            if stop_exists:

                print(
                    "[KIMI] stop 按钮出现，"
                    "生成已开始"
                )

                break

            time.sleep(0.5)

        else:

            print(
                "[KIMI] stop 按钮未出现，"
                "发送可能失败"
            )

            return None

        # ----------------------------------------------------
        # 阶段 2：
        # 等待生成完成
        # ----------------------------------------------------

        print(
            "[KIMI] 阶段2: "
            "等待 stop 消失 + 代码稳定..."
        )

        last_code_len = 0
        stable_text_count = 0
        stop_gone_count = 0

        while (
            time.time() - start_time
            < max_wait
        ):

            time.sleep(1)

            try:

                stop_exists = (
                    self.page.evaluate(
                        """
                        () => document
                            .querySelectorAll(
                                '.send-button-container.stop'
                            ).length > 0
                        """
                    )
                )

                current_len = (
                    self.page.evaluate(
                        """
                        () => {
                            const blocks =
                                document.querySelectorAll(
                                    'code.language-python'
                                );

                            return blocks.length
                                ? blocks[
                                    blocks.length - 1
                                ].innerText.length
                                : 0;
                        }
                        """
                    )
                )

            except Exception:

                stop_exists = False
                current_len = 0

            # ------------------------------------------------
            # 代码稳定性
            # ------------------------------------------------

            code_stable = False

            if (
                current_len == last_code_len
                and current_len > 0
            ):

                stable_text_count += 1

                if stable_text_count >= 3:
                    code_stable = True

            else:

                last_code_len = current_len
                stable_text_count = 0

            # ------------------------------------------------
            # stop 消失
            # ------------------------------------------------

            if not stop_exists:

                stop_gone_count += 1

                if (
                    stop_gone_count >= 2
                    and code_stable
                ):

                    print(
                        "[KIMI] 生成完成 "
                        "(stop 消失 + 代码稳定, "
                        f"长度 {current_len})"
                    )

                    break

                elif stop_gone_count >= 2:

                    print(
                        "[KIMI] stop 已消失，"
                        "等待代码块稳定..."
                        f"（当前长度 {current_len}）"
                    )

            else:

                stop_gone_count = 0

                elapsed = (
                    time.time()
                    - start_time
                )

                if (
                    elapsed > 15
                    and int(elapsed) % 15 == 0
                ):

                    print(
                        "[KIMI] 仍在生成中..."
                        f" 已等待 {int(elapsed)}s，"
                        f"代码块长度 {current_len}"
                    )

        else:

            print(
                "[KIMI] 警告: "
                f"已达到最大等待时间 "
                f"{max_wait}s，强制继续"
            )

        # ----------------------------------------------------
        # 阶段 3：
        # actions 稳定
        # ----------------------------------------------------

        print(
            "[KIMI] 阶段3: "
            "等待 actions 稳定..."
        )

        initial_actions = (
            self.get_actions_count()
        )

        print(
            f"[KIMI] 当前 actions: "
            f"{initial_actions}"
        )

        stable_count = 0
        last_count = initial_actions

        action_wait_start = time.time()

        while (
            time.time()
            - action_wait_start
            < 10
        ):

            time.sleep(0.5)

            current_actions = (
                self.get_actions_count()
            )

            if current_actions == last_count:

                stable_count += 1

                if stable_count >= 3:

                    print(
                        f"[KIMI] actions 稳定: "
                        f"{current_actions}"
                    )

                    break

            else:

                print(
                    "[KIMI] actions 变化: "
                    f"{last_count} -> "
                    f"{current_actions}"
                )

                stable_count = 0

                last_count = current_actions

        else:

            print(
                "[KIMI] actions 未稳定，"
                "不影响代码提取"
            )

        time.sleep(2)

        # ----------------------------------------------------
        # 提取代码
        # ----------------------------------------------------

        print(
            "[KIMI] 提取代码..."
        )

        try:

            code = self.page.evaluate(
                """
                () => {
                    const codeBlocks =
                        document.querySelectorAll(
                            'code.language-python'
                        );

                    if (!codeBlocks.length) {

                        const preBlocks =
                            document.querySelectorAll(
                                'pre code'
                            );

                        if (preBlocks.length) {
                            return (
                                preBlocks[
                                    preBlocks.length - 1
                                ].innerText || ''
                            );
                        }

                        return '';
                    }

                    const lastBlock =
                        codeBlocks[
                            codeBlocks.length - 1
                        ];

                    let text =
                        lastBlock.innerText;

                    if (
                        text &&
                        text.length > 10
                    ) {
                        return text;
                    }

                    const walker =
                        document.createTreeWalker(
                            lastBlock,
                            NodeFilter.SHOW_TEXT
                        );

                    const texts = [];

                    let node;

                    while (
                        node =
                            walker.nextNode()
                    ) {
                        texts.push(
                            node.textContent
                        );
                    }

                    return texts.join('');
                }
                """
            )

        except Exception as e:

            print(
                f"[KIMI] 提取代码失败: {e}"
            )

            return None

        print(
            f"[KIMI] 提取代码长度: "
            f"{len(code or '')}"
        )

        if not code:

            try:
                self.page.screenshot(
                    path="kimi_debug_empty.png"
                )

                print(
                    "[KIMI] 代码为空，"
                    "截图保存"
                )

            except Exception:
                pass

            return None

        return self._save_code(
            task_description,
            code,
            task_idx,
            workspace,
        )

    # ========================================================
    # Prompt
    # ========================================================

    def _build_prompt(
        self,
        task_description: str
    ):

        core_section = ""

        if self.core_goal:

            core_section = f"""
【核心目标】

{self.core_goal}

"""

        return f"""
你是一个 Python 代码生成助手。

请遵循以下规则：

1. 只能生成一个完整的 Python 代码块
2. 代码必须自包含
3. 包含所有必要 import
4. 代码可以直接运行
5. 在代码末尾添加：
   # END_AND_END
6. 不要输出代码块之外的解释性文字

{core_section}

【用户请求】

{task_description}
"""

    # ========================================================
    # 保存代码
    # ========================================================

    def _save_code(
        self,
        task_description: str,
        code: str,
        task_idx: int,
        workspace: str,
    ):

        workspace = Path(
            workspace
        )

        workspace.mkdir(
            parents=True,
            exist_ok=True
        )

        filename = (
            f"{time.strftime('%Y%m%d')}_"
            f"{task_idx:03d}.py"
        )

        filepath = (
            workspace
            / filename
        )

        code = code.replace(
            "\u00a0",
            " "
        )

        has_marker = (
            "END_AND_END"
            in code
        )

        if not has_marker:

            print(
                "[KIMI] 警告："
                "代码缺少 END_AND_END 标记"
            )

        safe_desc = (
            task_description
            .replace("\n", " | ")
            .replace("\r", "")
        )

        header = f"""# {filename}
# TASK_IDX: {task_idx}
# SOURCE_DESC: {safe_desc}
# GENERATED_BY: kimi_web
# TIMESTAMP: {time.strftime("%Y-%m-%d %H:%M:%S")}
# HAS_END_MARKER: {has_marker}

"""

        with open(
            filepath,
            "w",
            encoding="utf-8",
        ) as f:

            f.write(
                header
                + code
            )

        print(
            f"[KIMI] 已保存: "
            f"{filepath} "
            f"(标记检测: {has_marker})"
        )

        return filepath

    # ========================================================
    # 上传文件
    # ========================================================

    def upload_file_via_clipboard(
        self,
        file_path: str
    ) -> bool:

        if not self._connected:

            print(
                "[SESSION] 未连接，"
                "无法上传"
            )

            return False

        uploader = ClipboardUploader(
            self.page
        )

        return uploader.upload_file(
            file_path
        )

    def upload_files_via_clipboard(
        self,
        file_paths: list
    ) -> bool:

        if not self._connected:

            print(
                "[SESSION] 未连接，"
                "无法上传"
            )

            return False

        uploader = ClipboardUploader(
            self.page
        )

        return uploader.upload_files(
            file_paths
        )

    # ========================================================
    # Core 兼容接口
    # ========================================================

    def upload_file(
        self,
        file_path: str
    ) -> bool:

        return self.upload_file_via_clipboard(
            file_path
        )

    def upload_files(
        self,
        file_paths: list
    ) -> bool:

        return self.upload_files_via_clipboard(
            file_paths
        )

    # ========================================================
    # 关闭
    # ========================================================

    def close(self):

        print(
            "[KIMI] 关闭会话..."
        )

        # ----------------------------------------------------
        # 先关闭 Playwright/CDP 连接
        # ----------------------------------------------------

        if self.browser:

            try:
                self.browser.close()

            except Exception:
                pass

            self.browser = None

        # ----------------------------------------------------
        # 停止 Playwright
        # ----------------------------------------------------

        self._stop_playwright()

        # ----------------------------------------------------
        # 只有自己启动的浏览器才关闭
        # ----------------------------------------------------

        if (
            self.owns_browser
            and self.edge_process
        ):

            try:
                self.edge_process.terminate()

            except Exception:
                pass

            self.edge_process = None

        # ----------------------------------------------------
        # 状态
        # ----------------------------------------------------

        self.page = None
        self.context = None
        self._connected = False

        print(
            "[KIMI] 会话关闭"
        )
