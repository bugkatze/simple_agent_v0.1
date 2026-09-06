# LoopAgent

一个轻量、可修改、模型无关的 Agent Runtime。

LoopAgent 不试图重新发明一个复杂的 Agent 算法，而是提供一个尽可能简单的运行框架：

任务
↓
模型适配器
↓
生成代码
↓
本地执行
↓
产生输出 / 数据文件
↓
反馈给模型
↓
再次生成

核心目标：

- 尽可能降低固定运行成本
- 不绑定某一个模型厂商
- 让模型、执行器和运行状态彼此解耦
- 允许用户直接修改 Agent 本身
- 让 Agent 可以在没有人工逐步确认的情况下连续运行多轮
- 保持足够小的代码规模，使普通用户也能读懂并修改

本项目目前仍处于早期阶段。


## 1. 项目结构

当前版本的核心文件：

LoopAgent/
├── loop_agent_core.py
├── loop_agent_kimi.py
├── run_agent_kimi.py
├── requirements.txt
├── README.md
├── LICENSE
└── agent_idle/

运行后，agent_idle/ 会自动形成：

agent_idle/
├── seed/
│   └── seed.py
│
├── file_database/
│   ├── current/
│   └── data_history/
│
└── *.py


### agent_idle/

Agent 的实际工作目录。

生成的 Python 程序会直接放在这里，并在这里执行。


### agent_idle/seed/

可选的初始代码。

如果存在：

agent_idle/seed/seed.py

LoopAgent 会自动发现它，并通过当前模型适配器上传。

没有 seed.py 时，则从任务描述开始。

因此可以有两种使用方式：

从零开始：
任务 → 模型 → 代码

基于已有代码：
seed.py → 模型 → 修改后的代码


### agent_idle/file_database/current/

程序和 Agent 之间的数据交换区。

例如生成的程序运行后产生：

clean_stats.json
cleaned_comments.json
removed_comments.json

只要这些文件出现在：

agent_idle/file_database/current/

LoopAgent 就会检测到它们，并通过当前模型适配器重新上传。


### agent_idle/file_database/data_history/

保存历史反馈文件。

current/ 中的数据被新的反馈替代或归档时，会进入这里。


## 2. 当前运行方式

目前仓库提供 Kimi Web 适配器。

运行入口：

python run_agent_kimi.py

默认情况下：

ATTACH_EXISTING = False

这会使用 Kimi 适配器原来的浏览器启动路径。

也就是说：

run_agent_kimi.py
↓
LoopAgent.start()
↓
KimiSession.start()
↓
启动 Edge
↓
连接 CDP
↓
打开 Kimi


## 3. 安装

建议使用 Python 3.10+。

安装依赖：

pip install -r requirements.txt

当前 Kimi 适配器主要依赖：

playwright
pywin32

其中：

- playwright 用于连接和操作浏览器
- pywin32 用于 Windows 剪贴板文件上传


## 4. 第一次运行

首次运行前，需要准备任务。

编辑：

run_agent_kimi.py

主要修改：

TASK_DESC = """
你的任务描述
"""

MAX_ITER = 5

CORE_GOAL = """
你的最终目标
"""

例如：

TASK_DESC = """
基于输入的 JSON 文件，
清洗评论数据并输出统计结果。
"""

MAX_ITER = 5

然后：

python run_agent_kimi.py


## 5. 使用代码种子

如果已经有一份可以工作的代码，不必让模型从零开始。

将代码放到：

agent_idle/seed/seed.py

然后运行 Agent。

程序会自动检测：

[SEED] 检测到: .../agent_idle/seed/seed.py

随后通过当前 Session 上传给模型。

因此可以使用：

已有代码
↓
seed.py
↓
Agent
↓
模型修改
↓
执行
↓
反馈
↓
继续修改

这也可以作为一种简单的人工干预方式。

例如：

第一次运行
↓
发现方向不对
↓
停止程序
↓
修改 seed.py
↓
修改任务描述
↓
重新运行

当前版本不提供逐操作的人工确认机制。


## 6. 输入文件

原始输入文件可以直接放在：

agent_idle/

例如：

agent_idle/
├── comments.json
├── data.csv
├── image.png
├── seed/
└── file_database/

LoopAgent 会自动发现顶层非 Python 文件，并上传给模型。

以下内容不会被当作普通输入文件：

agent_idle/*.py
agent_idle/seed/*
agent_idle/file_database/*

因此通常只需要把数据文件直接放进：

agent_idle/

即可。


## 7. 多轮循环

LoopAgent 会执行类似下面的过程：

第 1 轮
任务
↓
模型生成代码
↓
本地执行

第 2 轮
↓
根据执行结果要求模型继续修改
↓
重新执行

第 3 轮
↓
如果 current/ 中产生新的反馈文件
↓
将反馈文件重新上传给模型
↓
模型基于真实数据继续修改

例如：

模型生成代码
↓
Python 执行
↓
current/
├── clean_stats.json
├── cleaned_comments.json
└── removed_comments.json
↓
Agent 检测
↓
重新上传
↓
模型读取实际结果
↓
继续修改代码

因此，当前版本已经具备：

模型 → 程序 → 数据 → 模型

这一基本反馈闭环。


## 8. 运行状态并不等于任务正确

当前版本需要特别注意这一点。

LoopAgent 的基础执行判断主要来自：

return code
stdout
stderr
timeout

因此：

程序正常退出

并不意味着：

程序完成了正确的任务

例如一个程序可以：

print("success")

然后错误地完成整个任务。

因此，当前版本更接近：

Runtime + 简单执行反馈

而不是完整的任务评测系统。

后续版本计划加入独立 Evaluator。


## 9. 浏览器 Attach 模式

当前 Core 支持一个可选的：

ATTACH_EXISTING = True

当它为：

False

时：

session.start()

当它为：

True

时：

session.attach_existing(...)

浏览器相关逻辑不放在 Core 中，而由具体模型适配器负责。

即：

LoopAgent Core
│
├── start()
│
└── attach_existing()
           ↓
       KimiSession
           ↓
     浏览器 / CDP / Tab

Core 不需要知道：

- 使用什么浏览器
- 使用哪个 CDP 端口
- 目标网站是什么
- 如何寻找目标 Tab

这些属于适配器实现。


## 10. Kimi 的 attach_existing

Kimi 适配器目前提供：

session.attach_existing(
    debug_port=9222,
    url="https://kimi.moonshot.cn/"
)

运行入口中的配置：

ATTACH_EXISTING = True

BROWSER_CONFIG = {
    "url": "https://kimi.moonshot.cn/",
    "debug_port": 9222,
}

也可以：

BROWSER_CONFIG = {}

此时 Kimi 适配器使用自己的默认目标。


## 11. Attach 模式的注意事项

attach_existing=True 的含义是：

浏览器由用户自己启动
↓
Agent 连接已有浏览器
↓
Agent 不负责启动浏览器
↓
Agent 不负责关闭该浏览器

因此这种模式不会主动终止用户自己的浏览器进程。

不过，当前 Kimi Web 适配器的 attach_existing 属于实验性功能。

不同浏览器版本、浏览器启动方式以及浏览器自身的 Remote Debugging 行为可能影响其可用性。

目前不要把：

attach_existing=True

视为所有环境下都保证可用的功能。

如果 attach 模式无法使用，可以继续使用默认的：

ATTACH_EXISTING = False


## 12. 为什么 Browser 代码放在适配器里

LoopAgent 并不应该知道：

page.goto(...)

也不应该知道：

Kimi
Edge
Chrome
CDP
Tab
URL

因为这些东西都是模型/网站交互层的具体实现。

理论上可以出现：

loop_agent_core.py
        │
        ├── KimiSession
        ├── GeminiSession
        ├── ClaudeSession
        └── LocalModelSession

Core 只需要依赖一个简单的 Session 接口。

例如：

session.start()
session.attach_existing(...)
session.generate(...)
session.upload_file(...)
session.upload_files(...)
session.close()

这使得模型后端可以被替换。


## 13. 修改 Agent

本项目有意保持 Core 较小。

你可以直接修改：

loop_agent_core.py

改变：

- 迭代策略
- 文件处理逻辑
- 执行逻辑
- 安全检查
- Prompt
- 工作空间结构
- 反馈方式
- 停止条件

也可以修改：

loop_agent_kimi.py

改变：

- 浏览器启动
- 浏览器连接
- Tab 筛选
- 页面操作
- 文件上传
- 代码提取
- Kimi 页面适配


## 14. 推荐的阅读顺序

如果你第一次接触 Agent Runtime，建议按下面的顺序阅读：

run_agent_kimi.py
↓
loop_agent_core.py
↓
loop_agent_kimi.py

先理解：

谁启动 Agent
↓
Core 如何循环
↓
Session 如何和模型交互

而不是先阅读整个项目的所有实现细节。


## 15. 安全说明

当前版本的代码执行器会直接在本地运行模型生成的 Python。

这意味着：

请不要把不可信的 Agent 代码当作安全代码运行。

当前安全检查主要用于降低明显的自引用和部分危险操作风险，例如：

eval(
exec(
__import__(

以及部分针对 Agent 自身工作目录、Core、模型适配器的检查。

这些检查不是完整的沙箱。

当前项目没有把虚拟机、容器或操作系统级沙箱作为核心依赖。

因此：

可信模型
+
可信任务
+
可信输入

是当前版本更合适的使用环境。

如果未来需要执行不可信代码，应加入真正的进程隔离、容器或虚拟机级别的安全边界。


## 16. 当前项目定位

LoopAgent 当前更适合被理解为：

Agent Harness / Runtime

而不是：

新的大模型
新的 Agent 算法
完整的软件工程平台

它主要解决的是：

模型
↓
可替换

执行环境
↓
简单、本地

状态
↓
文件化

反馈
↓
可继续迭代

人工干预
↓
通过 seed / task 重新启动

因此它可以使用不同能力、不同价格的模型。

模型不是 LoopAgent 本身。


## 17. Roadmap

当前优先级不是继续扩大 Core，而是逐步补齐真正需要的能力。

计划包括：

1. Evaluator
   ↓
   判断“程序运行成功”与“任务完成”之间的区别

2. Local Evaluator
   ↓
   使用本地模型生成结构化反馈

3. Web Evaluator
   ↓
   使用网页模型 / 浏览器进行评估

4. 更标准的反馈协议
   ↓
   pass / fail / unknown + evidence

5. 更多模型适配器

6. 更完善的浏览器 Runtime

7. 更好的隔离和安全机制

这些功能不会默认全部塞进 Core。

项目会尽量维持：

小 Core
+
可替换 Adapter
+
可选 Runtime
+
用户自行修改

的结构。


## 18. License

LoopAgent 使用 MIT License。

详见：

LICENSE


## 19. 项目状态

这是一个正在开发中的个人开源项目。

当前版本已经验证的主要能力包括：

✓ 本地代码生成与执行
✓ 多轮代码迭代
✓ agent_idle 工作空间
✓ seed.py 自动发现
✓ 顶层输入文件自动上传
✓ current/ 数据反馈
✓ data_history/ 历史归档
✓ Kimi Web 适配器
✓ 浏览器 Session 复用
✓ 可选 attach_existing 接口

但项目仍然可能存在：

- 浏览器兼容性问题
- 网站 DOM 变化导致的适配器失效
- 模型输出格式变化
- 任务完成判断不足
- 安全隔离不足

因此请把当前版本视为实验性工具，而不是生产级 Agent 平台。


## 20. 最简单的运行流程

第一次使用时，可以只记住：

1. 安装依赖

pip install -r requirements.txt

2. 准备输入文件

agent_idle/
└── your_input_file

3. 修改任务

run_agent_kimi.py

4. 运行

python run_agent_kimi.py

5. 查看结果

agent_idle/

如果需要从已有代码继续：

agent_idle/
├── seed/
│   └── seed.py
└── your_input_file

然后运行：

python run_agent_kimi.py

就可以开始。
