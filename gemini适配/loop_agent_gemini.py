"""
LoopAgent Gemini 适配层

实现与 Gemini 网页的交互能力。

支持两种浏览器模式：

1. 原有模式

    session.start()

由适配器自行启动 Edge，并连接 Gemini。

2. 已有浏览器模式

    session.attach_existing(
        debug_port=9222,
        url="https://gemini.google.com/app"
    )

连接已经运行并开启 Remote Debugging 的 Edge。

Core 不负责：
- 浏览器
- CDP
- URL
- Tab 选择
- Gemini DOM

这些全部属于适配器。

用法：

    from loop_agent_core import LoopAgent
    from loop_agent_gemini import GeminiSession

    session = GeminiSession()

    agent = LoopAgent(
        session=session,
        core_goal="..."
    )

    agent.start()

    agent.self_improve_loop_enhanced(
        "任务描述"
    )

    agent.close()
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
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
# Gemini / Edge 配置
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

DEFAULT_GEMINI_URL = (
    "https://gemini.google.com/app"
)

DEFAULT_GEMINI_KEYWORDS = [
    "gemini.google.com",
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
    # 备份剪贴板
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
                f"[CLIPBOARD] "
                f"备份剪贴板失败: {e}"
            )

            self._last_clipboard = None
            self._clipboard_backup_valid = False

    # --------------------------------------------------------
    # 恢复剪贴板
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

            print(
                "[CLIPBOARD] 剪贴板已恢复"
            )

        except Exception as e:

            print(
                f"[CLIPBOARD] "
                f"恢复剪贴板失败: {e}"
            )

    # --------------------------------------------------------
    # 写入文件列表
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

                valid_paths.append(
                    abs_path
                )

            else:

                print(
                    "[CLIPBOARD] "
                    f"文件不存在，跳过: {fp}"
                )

        if not valid_paths:

            print(
                "[CLIPBOARD] "
                "没有有效文件"
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

            offset = ctypes.sizeof(
                DROPFILES
            )

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
                "[CLIPBOARD] "
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
    # 上传一个文件
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
                    "[CLIPBOARD] "
                    "未找到输入框"
                )

                return False

            print(
                "[CLIPBOARD] "
                "聚焦输入框..."
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
            "[CLIPBOARD] "
            f"批量上传完成: "
            f"{success_count}/{len(file_paths)}"
        )

        return success_count > 0


# ============================================================
# Gemini Session
# ============================================================

class GeminiSession:
    """
    Gemini Web Session。

    start():
        自己启动 Edge。

    attach_existing():
        连接已有 Edge。

    attach_existing 模式下：
        owns_browser = False

    close() 不会关闭用户已有浏览器。
    """

    def __init__(self):

        self.edge_process = None

        self.browser = None
        self.context = None
        self.page = None

        self._playwright = None
        self._connected = False

        self.core_goal = None

        self.owns_browser = False

    # ========================================================
    # 原有启动模式
    # ========================================================

    def start(self):

        print(
            "[GEMINI] 使用原有启动模式"
        )

        print(
            "[GEMINI] 启动 Edge 浏览器..."
        )

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

        self.edge_process = (
            subprocess.Popen(
                [
                    EDGE_PATH,
                    f"--user-data-dir={USER_DATA}",
                    "--remote-debugging-port=9222",
                    "--no-first-run",
                    "--no-default-browser-check",
                ]
            )
        )

        self.owns_browser = True

        time.sleep(5)

        self._start_playwright()

        print(
            "[GEMINI] 连接 Edge CDP..."
        )

        try:

            self.browser = (
                self._playwright
                .chromium
                .connect_over_cdp(
                    "http://127.0.0.1:9222"
                )
            )

        except Exception as e:

            raise RuntimeError(
                "Edge CDP 连接失败。\n"
                f"原始错误: {e}"
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

        print(
            "[GEMINI] 打开 Gemini..."
        )

        self.page.goto(
            DEFAULT_GEMINI_URL
        )

        time.sleep(3)

        self._handle_login()

        self._connected = True

        print(
            "[GEMINI] 会话就绪"
        )

    # ========================================================
    # HTTP 辅助
    # ========================================================

    @staticmethod
    def _http_json(
        url: str,
        timeout: float = 3.0
    ):
        """
        请求 Edge DevTools HTTP endpoint。
        """

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "LoopAgent-Gemini"
            }
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            raw = response.read()

        return json.loads(
            raw.decode(
                "utf-8",
                errors="replace"
            )
        )

    # ========================================================
    # 找 Edge 特殊 endpoint
    # ========================================================

    def _discover_edge_websocket(
        self,
        debug_port: int,
        url=None,
        host=None,
        keywords=None,
    ):
        """
        针对 Edge Remote Debugging 的
        /msedge/json/list 路径进行发现。

        返回：

            {
                "ws_url": "...",
                "page_url": "...",
                "pid": ...
            }

        或 None。
        """

        if keywords is None:
            keywords = []

        base = (
            f"http://127.0.0.1:"
            f"{int(debug_port)}"
        )

        list_url = (
            base
            + "/msedge/json/list"
        )

        print(
            "[GEMINI] 尝试 Edge 特殊 endpoint:"
        )

        print(
            f"[GEMINI] {list_url}"
        )

        try:

            data = self._http_json(
                list_url,
                timeout=3
            )

        except Exception as e:

            print(
                "[GEMINI] "
                "Edge /msedge/json/list "
                f"不可用: {e}"
            )

            return None

        if not isinstance(
            data,
            list
        ):
            return None

        candidates = []

        for process in data:

            if not isinstance(
                process,
                dict
            ):
                continue

            version = process.get(
                "version",
                {}
            )

            info = process.get(
                "info",
                {}
            )

            pid = info.get(
                "browserProcessId"
            )

            targets = process.get(
                "targets",
                []
            )

            if not pid:
                continue

            for target in targets:

                if not isinstance(
                    target,
                    dict
                ):
                    continue

                if target.get(
                    "type"
                ) != "page":
                    continue

                page_url = (
                    target.get(
                        "url"
                    )
                    or ""
                )

                title = (
                    target.get(
                        "title"
                    )
                    or ""
                )

                candidates.append({
                    "pid": pid,
                    "version": version,
                    "target": target,
                    "url": page_url,
                    "title": title,
                })

        if not candidates:
            return None

        # ----------------------------------------------------
        # 页面筛选
        # ----------------------------------------------------

        selected = None

        # 1. URL 精确匹配

        if url:

            url_lower = (
                str(url).lower()
            )

            for item in candidates:

                if (
                    item["url"]
                    .lower()
                    == url_lower
                ):

                    selected = item
                    break

        # 2. URL 包含匹配

        if selected is None and url:

            url_lower = (
                str(url).lower()
            )

            for item in candidates:

                if url_lower in (
                    item["url"]
                    .lower()
                ):

                    selected = item
                    break

        # 3. Host 匹配

        if selected is None and host:

            host_lower = (
                str(host).lower()
            )

            for item in candidates:

                if host_lower in (
                    item["url"].lower()
                ):

                    selected = item
                    break

        # 4. 关键词匹配

        if selected is None:

            for keyword in keywords:

                keyword_lower = (
                    str(keyword).lower()
                )

                for item in candidates:

                    text = (
                        item["url"]
                        + " "
                        + item["title"]
                    ).lower()

                    if keyword_lower in text:

                        selected = item
                        break

                if selected:
                    break

        if selected is None:
            return None

        pid = selected["pid"]

        # ----------------------------------------------------
        # 根据 PID 获取浏览器 websocket
        # ----------------------------------------------------

        version_url = (
            base
            + f"/msedge/{pid}/json/version"
        )

        print(
            "[GEMINI] 获取 Edge 进程信息:"
        )

        print(
            f"[GEMINI] {version_url}"
        )

        try:

            version_data = self._http_json(
                version_url,
                timeout=3
            )

        except Exception as e:

            print(
                "[GEMINI] "
                f"读取 Edge /json/version 失败: {e}"
            )

            return None

        ws_url = (
            version_data.get(
                "webSocketDebuggerUrl"
            )
        )

        if not ws_url:

            return None

        return {
            "ws_url": ws_url,
            "page_url": selected["url"],
            "title": selected["title"],
            "pid": pid,
        }

    # ========================================================
    # 浏览器页面筛选
    # ========================================================

    def _select_existing_page(
        self,
        url=None,
        host=None,
        keywords=None,
    ):

        if keywords is None:
            keywords = []

        pages = []

        if self.browser is None:
            return None

        for context in self.browser.contexts:

            for page in context.pages:

                pages.append(page)

        if not pages:
            return None

        # ----------------------------------------------------
        # URL 精确
        # ----------------------------------------------------

        if url:

            url_lower = (
                str(url).lower()
            )

            for page in pages:

                try:

                    page_url = (
                        page.url or ""
                    ).lower()

                    if page_url == url_lower:

                        return page

                except Exception:
                    continue

            # URL 包含

            for page in pages:

                try:

                    page_url = (
                        page.url or ""
                    ).lower()

                    if url_lower in page_url:

                        return page

                except Exception:
                    continue

        # ----------------------------------------------------
        # Host
        # ----------------------------------------------------

        if host:

            host_lower = (
                str(host).lower()
            )

            for page in pages:

                try:

                    page_url = (
                        page.url or ""
                    ).lower()

                    if host_lower in page_url:

                        return page

                except Exception:
                    continue

        # ----------------------------------------------------
        # Keywords
        # ----------------------------------------------------

        for keyword in keywords:

            keyword_lower = (
                str(keyword).lower()
            )

            for page in pages:

                try:

                    page_url = (
                        page.url or ""
                    ).lower()

                    if keyword_lower in page_url:

                        return page

                except Exception:
                    continue

        return None

    # ========================================================
    # Attach Existing
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
        连接已经存在的 Edge。

        首先尝试标准 Chromium CDP：

            http://127.0.0.1:9222

        如果标准 endpoint 不可用，
        再尝试 Edge 专用：

            /msedge/json/list
            /msedge/<pid>/json/version

        这样可以兼容部分 Edge Remote Debugging
        模式。
        """

        print(
            "\n[GEMINI] "
            "使用 attach_existing 模式"
        )

        print(
            f"[GEMINI] "
            f"CDP 端口: {debug_port}"
        )

        if url is None:
            url = DEFAULT_GEMINI_URL

        if keywords is None:
            keywords = list(
                DEFAULT_GEMINI_KEYWORDS
            )

        self._start_playwright()

        # ----------------------------------------------------
        # 第一种：
        # 标准 Chromium endpoint
        # ----------------------------------------------------

        standard_endpoint = (
            f"http://127.0.0.1:"
            f"{int(debug_port)}"
        )

        print(
            "[GEMINI] "
            "尝试标准 CDP endpoint:"
        )

        print(
            f"[GEMINI] "
            f"{standard_endpoint}"
        )

        try:

            self.browser = (
                self._playwright
                .chromium
                .connect_over_cdp(
                    standard_endpoint
                )
            )

            print(
                "[GEMINI] "
                "标准 CDP 连接成功"
            )

        except Exception as standard_error:

            print(
                "[GEMINI] "
                "标准 CDP 连接失败:"
            )

            print(
                f"[GEMINI] "
                f"{standard_error}"
            )

            self.browser = None

        # ----------------------------------------------------
        # 第二种：
        # Edge 特殊 endpoint
        # ----------------------------------------------------

        discovered = None

        if self.browser is None:

            discovered = (
                self._discover_edge_websocket(
                    debug_port=debug_port,
                    url=url,
                    host=host,
                    keywords=keywords,
                )
            )

            if discovered:

                ws_url = discovered[
                    "ws_url"
                ]

                print(
                    "[GEMINI] "
                    "发现 Edge Browser WebSocket:"
                )

                print(
                    f"[GEMINI] {ws_url}"
                )

                try:

                    self.browser = (
                        self._playwright
                        .chromium
                        .connect_over_cdp(
                            ws_url
                        )
                    )

                    print(
                        "[GEMINI] "
                        "Edge WebSocket "
                        "连接成功"
                    )

                except Exception as e:

                    print(
                        "[GEMINI] "
                        "Edge WebSocket "
                        f"连接失败: {e}"
                    )

                    self.browser = None

        # ----------------------------------------------------
        # 连接完全失败
        # ----------------------------------------------------

        if self.browser is None:

            self._stop_playwright()

            raise RuntimeError(
                "无法连接已有 Edge。\n\n"
                f"CDP 端口: {debug_port}\n"
                f"标准 endpoint: "
                f"{standard_endpoint}\n\n"
                "尝试了：\n"
                "1. 标准 Chromium CDP\n"
                "2. Edge /msedge/json/list\n"
                "3. Edge /msedge/<pid>/json/version\n\n"
                "请确认：\n"
                "1. Edge 正在运行\n"
                "2. edge://inspect 已打开\n"
                "3. Remote debugging 已启用\n"
                "4. Gemini 页面已经打开\n"
            )

        self.owns_browser = False

        # ----------------------------------------------------
        # 页面上下文
        # ----------------------------------------------------

        deadline = (
            time.time()
            + float(wait_seconds)
        )

        selected_page = None

        while time.time() < deadline:

            selected_page = (
                self._select_existing_page(
                    url=url,
                    host=host,
                    keywords=keywords,
                )
            )

            if selected_page:
                break

            time.sleep(0.5)

        # ----------------------------------------------------
        # 找不到页面
        # ----------------------------------------------------

        if selected_page is None:

            available = []

            for context in self.browser.contexts:

                for page in context.pages:

                    try:
                        available.append(
                            page.url
                        )
                    except Exception:
                        pass

            self._stop_playwright()

            raise RuntimeError(
                "浏览器连接成功，"
                "但没有找到 Gemini Tab。\n\n"
                f"目标 URL: {url!r}\n"
                f"目标 Host: {host!r}\n"
                f"关键词: {keywords!r}\n\n"
                "当前页面:\n"
                + "\n".join(
                    f"  - {item}"
                    for item in available
                )
            )

        self.page = selected_page

        self.context = (
            selected_page.context
        )

        print(
            "[GEMINI] "
            "已连接到 Gemini Tab:"
        )

        print(
            f"[GEMINI] "
            f"{self.page.url}"
        )

        self._handle_login(
            only_if_needed=True
        )

        self._connected = True

        print(
            "[GEMINI] "
            "attach_existing 会话就绪"
        )

        return True

    # ========================================================
    # Playwright
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
    # 登录
    # ========================================================

    def _handle_login(
        self,
        only_if_needed=False
    ):

        if self.page is None:
            return

        try:

            current_url = (
                self.page.url or ""
            ).lower()

            if (
                "login" not in current_url
                and "signin" not in current_url
            ):
                return

            print(
                "[GEMINI] "
                "当前页面可能需要登录"
            )

            input(
                "[GEMINI] "
                "登录完成后按回车继续..."
            )

        except Exception as e:

            print(
                f"[GEMINI] "
                f"登录检查异常: {e}"
            )

    # ========================================================
    # 响应完成检测
    # ========================================================

    def get_response_indicators(self):

        if not self.page:
            return 0

        try:

            return self.page.evaluate(
                """
                () => document
                    .querySelectorAll(
                        'thumb-up-button'
                    ).length
                """
            )

        except Exception:
            return 0

    def get_conversation_count(self):

        if not self.page:
            return 0

        try:

            return self.page.evaluate(
                """
                () => document
                    .querySelectorAll(
                        '.conversation-container'
                    ).length
                """
            )

        except Exception:
            return 0

    # ========================================================
    # 生成
    # ========================================================

    def generate(
        self,
        task_description: str,
        task_idx: int = 0,
        workspace="agent_idle",
    ):
        """
        向 Gemini 发送任务，
        等待响应完成，
        提取最后一个 Python 代码块，
        保存到 workspace。
        """

        if not self._connected:

            raise RuntimeError(
                "GeminiSession 未连接，"
                "先调用 start() "
                "或 attach_existing()"
            )

        print(
            f"[GEMINI] "
            f"发送任务 (idx={task_idx})..."
        )

        initial_thumbs = (
            self.get_response_indicators()
        )

        print(
            "[GEMINI] "
            f"当前 thumb-up 数量: "
            f"{initial_thumbs}"
        )

        input_box = (
            self.page.query_selector(
                'textarea, '
                '[contenteditable="true"]'
            )
        )

        if not input_box:

            print(
                "[GEMINI] "
                "未找到输入框"
            )

            return None

        prompt = self._build_prompt(
            task_description
        )

        # ----------------------------------------------------
        # Gemini 输入
        # ----------------------------------------------------

        try:

            input_box.evaluate(
                """
                (el, text) => {
                    el.focus();
                    document.execCommand(
                        'insertText',
                        false,
                        text
                    );
                }
                """,
                prompt,
            )

        except Exception:

            # fallback
            input_box.fill(prompt)

        time.sleep(0.5)

        self.page.keyboard.press(
            "Enter"
        )

        print(
            "[GEMINI] "
            "等待生成..."
        )

        start_time = time.time()

        max_wait = 120

        seen_stop = False
        thumb_increased = False

        # ----------------------------------------------------
        # 第一阶段：
        # 等待响应完成
        # ----------------------------------------------------

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
                                '[data-mat-icon-name="stop"]'
                            ).length > 0
                        """
                    )
                )

            except Exception:

                stop_exists = False

            if stop_exists:

                if not seen_stop:

                    print(
                        "[GEMINI] "
                        "检测到生成开始"
                    )

                    seen_stop = True

            current_thumbs = (
                self.get_response_indicators()
            )

            if (
                current_thumbs
                > initial_thumbs
            ):

                print(
                    "[GEMINI] "
                    "检测到新响应完成 "
                    f"({initial_thumbs} "
                    f"-> {current_thumbs})"
                )

                thumb_increased = True

                break

            if seen_stop and not stop_exists:

                print(
                    "[GEMINI] "
                    "stop 按钮消失，"
                    "确认响应完成..."
                )

                time.sleep(0.5)

                current_thumbs = (
                    self.get_response_indicators()
                )

                if (
                    current_thumbs
                    > initial_thumbs
                ):

                    print(
                        "[GEMINI] "
                        "确认新响应完成 "
                        f"({initial_thumbs} "
                        f"-> {current_thumbs})"
                    )

                    thumb_increased = True

                    break

                print(
                    "[GEMINI] "
                    "警告：stop 消失但 "
                    "thumb-up 未增加"
                )

                break

            time.sleep(0.2)

        if (
            not thumb_increased
            and not seen_stop
        ):

            print(
                "[GEMINI] "
                "错误：未检测到生成开始"
            )

            return None

        if not thumb_increased:

            print(
                "[GEMINI] "
                "错误：生成未完成或失败"
            )

            return None

        # ----------------------------------------------------
        # 第二阶段：
        # 等待代码块稳定
        # ----------------------------------------------------

        print(
            "[GEMINI] "
            "阶段2.5："
            "确认代码块渲染完成..."
        )

        last_code_len = 0
        stable_text_count = 0

        for _ in range(20):

            time.sleep(1.0)

            try:

                current_len = (
                    self.page.evaluate(
                        """
                        () => {
                            const responses =
                                document.querySelectorAll(
                                    'model-response'
                                );

                            if (!responses.length) {
                                return 0;
                            }

                            const lastResponse =
                                responses[
                                    responses.length - 1
                                ];

                            const blocks =
                                lastResponse
                                    .querySelectorAll(
                                        'code.code-container'
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

                current_len = 0

            if (
                current_len
                == last_code_len
                and current_len > 0
            ):

                stable_text_count += 1

                if stable_text_count >= 2:

                    print(
                        "[GEMINI] "
                        f"代码块稳定，"
                        f"长度: {current_len}"
                    )

                    break

            else:

                last_code_len = current_len
                stable_text_count = 0

        else:

            print(
                "[GEMINI] "
                "警告：代码块未稳定，"
                "继续提取"
            )

        time.sleep(2)

        # ----------------------------------------------------
        # 第三阶段：
        # 提取代码
        # ----------------------------------------------------

        print(
            "[GEMINI] "
            "提取代码..."
        )

        try:

            code = self.page.evaluate(
                """
                () => {

                    const responses =
                        document.querySelectorAll(
                            'model-response'
                        );

                    if (!responses.length) {
                        return '';
                    }

                    const lastResponse =
                        responses[
                            responses.length - 1
                        ];

                    const selectors = [
                        'code.code-container',
                        'pre > code',
                        'code-block code',
                        'code'
                    ];

                    for (
                        const selector of selectors
                    ) {

                        const blocks =
                            lastResponse
                                .querySelectorAll(
                                    selector
                                );

                        if (!blocks.length) {
                            continue;
                        }

                        const lastBlock =
                            blocks[
                                blocks.length - 1
                            ];

                        const text =
                            lastBlock.innerText;

                        if (
                            text &&
                            text.length > 10
                        ) {

                            return text;
                        }
                    }

                    return '';
                }
                """
            )

        except Exception as e:

            print(
                "[GEMINI] "
                f"提取代码失败: {e}"
            )

            return None

        print(
            "[GEMINI] "
            f"提取代码长度: "
            f"{len(code or '')}"
        )

        if not code:

            try:

                self.page.screenshot(
                    path=(
                        "gemini_debug_empty.png"
                    )
                )

            except Exception:
                pass

            print(
                "[GEMINI] "
                "代码为空，截图保存"
            )

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
                "[GEMINI] "
                "警告："
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
# GENERATED_BY: gemini_web
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
            "[GEMINI] "
            f"已保存: {filepath} "
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
                "[SESSION] "
                "未连接，无法上传"
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
                "[SESSION] "
                "未连接，无法上传"
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
            "[GEMINI] "
            "关闭会话..."
        )

        if self.browser:

            try:
                self.browser.close()

            except Exception:
                pass

            self.browser = None

        self._stop_playwright()

        # 只有由 Session 自己启动的浏览器，
        # 才允许关闭。
        if (
            self.owns_browser
            and self.edge_process
        ):

            try:
                self.edge_process.terminate()

            except Exception:
                pass

            self.edge_process = None

        self.page = None
        self.context = None
        self._connected = False

        print(
            "[GEMINI] "
            "会话关闭"
        )
