---
name: weread-readtime-for-lark
version: 1.3.2
description: "Read WeRead/微信读书 daily reading duration and either print it as a 日期/秒/分/时 table or sync it into a Feishu/Lark Base/多维表格. Trigger only when the request is specifically about 微信读书阅读时长读取，或把微信读书阅读时长同步到飞书 Base / 多维表格；不要用于飞书文档、电子表格、知识库、云文档或其他文档产品，也不要用于非微信读书数据同步。"
metadata:
  requires:
    bins: ["python3", "lark-cli"]
    env: ["WEREAD_API_KEY"]
---

# 微信读书阅读时长 → 飞书 Base / 表格输出

这个 skill 负责两件事：

1. **只读打印**：读取微信读书某段时间内的每日阅读时长，并按下列四列表格输出。
2. **写入 Base**：把同一份数据按日期 upsert 到飞书智能表格 / Base 的 `阅读时长` 数据表。

固定表头如下：

- `日期`
- `当日阅读时长（秒）`
- `当日阅读时长（分）`
- `当日阅读时长（时）`

## 依赖与配置

执行这个 skill 时，优先按下面的依赖关系工作：

### 依赖 skill

- `weread-skill`
  - 用来理解 WeRead 的接口能力与已有口径，尤其是 `/readdata/detail` 的 `monthly` 模式。
- `lark-base`
  - 用来读写 Base、校验字段、创建 Base / table / dashboard。
- `lark-shared`
  - 处理飞书认证、`--as user` 身份、权限不足、scope 缺失等问题。

如果遇到认证或权限报错，先切到 `lark-shared` 的流程，不要盲目重试写入。

### 必要环境

- 环境变量：`WEREAD_API_KEY`
- 可执行命令：`python3`、`lark-cli`
- 飞书默认身份：`--as user`

如果 `WEREAD_API_KEY` 缺失，直接告诉用户先配置，不要继续执行。

### 依赖安装

这个 skill 依赖三个外部组件：

1. `python3`
2. `weread-skill`
3. `lark-cli`

推荐的人工安装方式：

#### 安装 Python 3

当执行 skill 前发现 `python3` 命令不存在时，允许先在终端自动安装，再继续执行；**这属于环境准备，不要把安装逻辑写进 `scripts/sync_weread_readtime_to_lark_base.py`。**

优先按当前机器环境选择：

**macOS（推荐，已安装 Homebrew）**

```bash
brew install python@3.12
python3 --version
```

**macOS（未安装 Homebrew 时先安装 Homebrew，再安装 Python 3）**

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12
python3 --version
```

**Debian / Ubuntu**

```bash
sudo apt-get update
sudo apt-get install -y python3
python3 --version
```

如果用户环境里缺少 `python3`，应先执行上述环境安装流程，再继续使用本 skill；不要因为缺少 `python3` 直接放弃。

#### 安装微信读书 skill

1. 从 "https://cdn.weread.qq.com/skills/weread-skills.zip” 下载并安装 skill，重命名 skill 为 weread-skill
2. 提示用户下载最新版本移动端“微信读书App”。在“我->设置->微信读书Skill->获取API Key”中获取API Key并配置 WEREAD_API_KEY 环境变量

#### 安装飞书 CLI

```bash
npm install -g @larksuite/cli
```

重要限制：

- `scripts/sync_weread_readtime_to_lark_base.py` 只允许包含两类逻辑：
  1. 微信读书阅读时长获取
  2. 飞书 Base 写入 / 新建与其直接相关的最小逻辑
- 不要把 skill 安装、CLI 安装、依赖检测、环境探测、升级向导等非核心流程写进这个 Python 脚本。
- 后续如果要更新安装方式、依赖说明、触发条件、操作约束，优先修改 `SKILL.md`、`references/`、`assets/`，**不要因为这些非阅读时长获取 / Base 写入需求去改 Python 代码。**

## 何时触发

下列请求都应该触发这个 skill：

- “读取我的微信读书每日阅读时长”
- “按 日期 / 秒 / 分 / 时 给我整理 X 月 X 日到今天的阅读时长”
- “把微信读书阅读时长写入飞书多维表格 / Base”
- “同步 weread readtime 到 Base，避免重复写入”
- “我只想看阅读时长，不需要导入飞书”

下列请求**不要**触发这个 skill：

- 把微信读书数据同步到飞书文档、飞书电子表格（Sheets）、知识库、云文档、腾讯文档等非 Base 产品
- 把其他来源的数据同步到飞书多维表格 / Base
- 与微信读书阅读时长无关的文档整理、报表搬运、表格写入需求

## 模式判定

收到请求后，先判断用户要的是哪一种模式：

### A. print-only / 只读模式

如果用户明确表达以下意思，就走**只读打印**，不要写 Base：

- “只需要读取”
- “不用导入飞书”
- “先打印出来”
- “只看表格结果”

此时直接运行脚本的 `--print-only` 模式，然后把结果以 Markdown 表格返回给用户。

### B. 写入已有 Base

如果用户给了下面任一信息，就按已有 Base 写入：

- 完整 Base 链接（最好包含 `table=`）
- `base_token + table_id`

写入前先校验目标表结构；若缺字段或字段类型不匹配，先告诉用户修正表结构，不要盲写。

如果用户给的 `table_id` **格式不合法**（例如不是 `tbl...`，而是误贴了 dashboard id / block id / 其他 token），不要直接失败：

1. 先检索整个 Base 的所有数据表；
2. 找出字段满足 `日期 / 当日阅读时长（秒） / 当日阅读时长（分） / 当日阅读时长（时）` 的表；
3. 优先使用名为 `阅读时长` 的表；否则使用第一个符合要求的表；
4. 回复用户时明确说明发生了自动回退与最终命中的 `table_id`。

### C. 未给 Base，询问是否新建

如果用户要写入飞书，但**没有提供 Base 链接 / base_token + table_id**，必须先追问：

> 你要不要我直接新建一个保存微信读书阅读时长的飞书智能表格？

确认后，再走新建 Base 流程。**不要在用户未确认时直接创建。**

## 新建 Base 模板规则

新建 Base 的详细模板说明不要直接写在主文档里；当且仅当用户明确要新建 Base 时，再读取：

- `references/init-base.md`

结构化模板配置放在：

- `assets/init_base_template.json`

说明：

- 主脚本只在 `--init-base` 路径下按需读取该 JSON。
- 如果要改新建 Base 的表结构或 dashboard block，优先修改 `assets/init_base_template.json`，不要把模板内容重新内嵌回 `SKILL.md`。
- 新建 Base 时，`阅读时长` 表的默认视图需要按 `日期` 字段倒序排序，让最新日期排在最上面。

## 数据来源与口径

每日阅读时长来自：

```text
POST https://i.weread.qq.com/api/agent/gateway
api_name = /readdata/detail
mode = monthly
```

关键口径：

- `readTimes` 的 value 单位是 **秒**。
- `monthly` 模式下，返回的是按天分桶的时间戳 -> 秒数映射。
- 跨月区间需要**按自然月分段查询**后再拼接。
- 缺失日期按 `0` 秒补齐。
- 输出口径固定为：
  - 分钟 = `秒 / 60`，保留 1 位小数
  - 小时 = `秒 / 3600`，保留 2 位小数

## 目标表结构要求

写入的目标表必须包含以下字段：

- `日期`：`datetime`
- `当日阅读时长（秒）`：`number`
- `当日阅读时长（分）`：`number`
- `当日阅读时长（时）`：`number`

在写入已有表前，先执行字段校验；字段不对就停止。

## 推荐执行命令

### 1) print-only：只读取并打印

```bash
python3 ${HOME}/.trae/skills/weread-readtime-for-lark/scripts/sync_weread_readtime_to_lark_base.py \
  --start-date 2026-05-01 \
  --end-date 2026-05-18 \
  --print-only
```

### 2) 写入已有 Base（完整链接）

```bash
python3 ${HOME}/.trae/skills/weread-readtime-for-lark/scripts/sync_weread_readtime_to_lark_base.py \
  --table-url "https://bytedance.sg.larkoffice.com/base/<base_token>?table=<table_id>&view=<view_id>" \
  --start-date 2026-05-01 \
  --end-date 2026-05-18 \
  --as user
```

### 3) 写入已有 Base（token + table_id）

```bash
python3 ${HOME}/.trae/skills/weread-readtime-for-lark/scripts/sync_weread_readtime_to_lark_base.py \
  --base-token <base_token> \
  --table-id <table_id> \
  --start-date 2026-05-01 \
  --end-date 2026-05-18 \
  --as user
```

### 4) 用户确认后，新建 Base 模板并写入

```bash
python3 ${HOME}/.trae/skills/weread-readtime-for-lark/scripts/sync_weread_readtime_to_lark_base.py \
  --init-base \
  --base-name "微信读书书架" \
  --start-date 2026-05-01 \
  --end-date 2026-05-18 \
  --as user
```

## 常用参数

```bash
--print-only                  # 只读取打印，不做任何 Base 操作
--table-url <url>             # Base 链接，需带 table 参数
--base-token <token>          # Base token
--table-id <id>               # table id
--init-base                   # 新建模板 Base，并把数据写入其“阅读时长”表
--base-name <name>            # 新建 Base 的名字，默认“微信读书书架”
--folder-token <token>        # 可选，新建到指定文件夹
--time-zone <tz>              # 可选，新建 Base 时区
--start-date YYYY-MM-DD       # 默认最近 5 年的同一天
--end-date YYYY-MM-DD         # 默认今天
--dry-run                     # 只计算 upsert 结果，不实际写入已有 Base
--as user|bot                 # 默认 user
```

## 执行流程

### 1. 判断模式

- 只读 -> `--print-only`
- 已给 Base -> 直接校验并写入
- 要写入但没给 Base -> 先问是否新建模板 Base

### 2. 读取阅读时长

- 按月调用 `/readdata/detail`
- 过滤到目标日期区间
- 缺失日期补 0
- 用户没给范围时，默认查询最近 5 年到今天

### 3. 如需写入，解析并校验目标表

- 若用户给的是 URL，从中提取 `base_token` 和 `table_id`
- 若 `table_id` 格式不合法，则遍历整个 Base 自动寻找符合表头要求的数据表
- 读取字段结构
- 确认四个字段都存在且类型正确

### 4. upsert 写入

对每个目标日期：

- 当日阅读时长 = 0 -> 不创建
- 不存在且当日阅读时长 > 0 -> 创建
- 已存在但值变了 -> 更新
- 已存在且值相同 -> 跳过

### 5. 返回结果

返回时至少说明：

- `起止日期`
- `总天数`
- print-only 还是 sync
- 若写入：`新增 / 更新 / 跳过` 数量
- 若发生了 table_id 自动回退：返回原始 `table_id` 与最终命中的 `table_id`
- 若新建了 Base：返回 `base_token`、`table_id`、Base 名称、可访问链接（若 CLI 返回）
- 若新建了 Base：说明 `阅读时长` 表默认按 `日期` 倒序排序
- 若新建了 Base：说明 `微信读书概览` 只创建 `阅读总时长（小时）` 这一个 block

## 返回格式要求

### print-only 模式

优先返回脚本输出里的 `markdown_table`，直接展示成 Markdown 表格，不需要再写一遍 JSON。

### 写入模式

先给简要摘要，再按需附上表格：

- 起止日期
- 总天数
- 新增记录数
- 更新记录数
- 跳过记录数
- 非 0 阅读天数（可写入天数）
- 目标 Base / table
- 如果发生了 `table_id` 自动回退，明确说明是因为用户提供的 `table_id` 格式不合法
- 如果是 `dry-run`，明确说明未实际写入

## 注意事项

- 这是一个**可能包含写操作**的 skill；新建 Base、写入 Base 前都要先得到用户确认。
- 用户只说“读取 / 打印 / 看一下”时，不要顺手写入飞书。
- `readTimes` 单位始终是秒，不要误当分钟。
- 跨月区间一定按自然月拆分查询。
- 当用户误把 dashboard id / block id 当成 `table_id` 传入时，要自动扫描整个 Base 找到真正可写的 `阅读时长` 表，而不是直接报错结束。
- 新建 Base 时不要依赖任何外部模板链接；直接使用 skill 内置的 block 配置创建仪表盘。
