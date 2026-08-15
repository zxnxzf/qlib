# 国金标准 QMT：Windows 首次试跑

第一轮只做“能力探针 → 只读预演”，不会报单。模拟报单必须等探针确认国金 QMT 的报单、查单、成交和撤单接口后，再由代码显式解锁；不要自行把配置改成交易模式。

## 1. 准备 Windows 仓库与环境

仓库放在个人Windows的短ASCII本地路径，例如`D:\code\qlib`。不要放在公司电脑、OneDrive/网盘、网络盘或自动同步目录。先拉取本次交付分支：

```powershell
git fetch origin
git switch codex/shadow-backtest-parity
git pull --ff-only origin codex/shadow-backtest-parity
git status --short
git rev-parse HEAD
```

`git rev-parse HEAD`必须与本次交付消息给出的提交一致，且工作区没有未确认的代码修改。

普通Python与QMT内置Python是两个环境：数据更新、信号生成和测试使用仓库`.venv`；QMT客户端只加载后文生成的薄入口。`.venv`和`my/data`不会随Git同步。若Windows还没有项目虚拟环境，使用已安装的Python 3.9～3.12创建；下面以3.10为例：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pytest -q my/tests
```

若环境或测试失败，先停在这里，不要把QMT策略改成直接引用系统Python。生产信号还需要Windows本地`my/data/cn_data`；首次生成信号时数据脚本会按现有流程检查/更新，但不会从Git获得数据。

`my/runtime/`只保存本机账户、行情和结果，已被Git忽略；Git忽略不等于隐私保护，禁止把该目录复制回公司Mac、提交Git或放进网盘。

## 2. 先运行严格只读探针

生成一个供 QMT 客户端加载的薄入口；把输出路径替换为国金 QMT 的策略目录：

```powershell
.\.venv\Scripts\python.exe -m my.qmt.deploy `
  --kind probe `
  --repo-root . `
  --output "D:\QMT\strategies\qlib_qmt_probe.py"
```

在国金QMT界面把这个探针策略**只绑定到独立模拟账户**，确认`ContextInfo.accid`能取得该账户，再加载`qlib_qmt_probe.py`并等到一个实时bar。探针不读取后续的`qmt_config.json`；账户未绑定时会安全报告`not_configured`，不要改用实盘账户排障。探针具有以下硬限制：

- 历史回放 bar 不执行；
- 只查询 Python、文件、账户、持仓、盘口、已有委托和成交的数据结构；
- 只发现报单/撤单函数名和签名，不调用它们；
- 报告不保存真实账号、现金、持仓代码/数量或委托成交明细。

报告应出现在：

```text
my/runtime/qmt_state/qmt_probe.json
```

先检查：

- `ready` 是否为 `true`；
- `ready_for_trading` 必须仍为 `false`；
- `failed_requirements`；
- `runtime.python_version`；
- `observed_fields`和`discovered_api_surface`。

若没有报告，先看 QMT 日志中的 `[qmt-probe]`；不要改用报单测试排障。

## 3. 生成当日只读信号

只有探针结果人工确认后才继续本节。仅当本地Qlib数据已覆盖T-1、正式季度模型校验通过，并且当前时间尚未到T日09:32时运行：

```powershell
.\.venv\Scripts\python.exe -m my.qmt.producer `
  --account-alias qmt_sim `
  --runtime-root my/runtime
```

成功后生成：

```text
my/runtime/qmt_inbox/<T日>/signal.json
```

同一路径不可被不同内容覆盖。数据过期、模型发布校验失败、日期不一致或信号过期都会硬停止。

## 4. 部署只读 QMT 入口

复制示例配置到本机运行目录：

```powershell
New-Item -ItemType Directory -Force my\runtime | Out-Null
Copy-Item my\qmt\qmt_config.example.json my\runtime\qmt_config.json
```

只在 Windows 本机编辑 `my/runtime/qmt_config.json`：

- `repo_root`：当前 Windows Qlib 仓库绝对路径；
- `account_alias`：必须与 `signal.json` 一致；
- `account_id`：国金 QMT 模拟账户；
- `mode`：保持 `read_only`。

首轮必须使用只运行本策略的独立模拟账户，不要混入手工持仓或其他策略委托；后续门控清仓会把账户持仓视为本策略持仓。

生成 QMT 策略入口：

```powershell
.\.venv\Scripts\python.exe -m my.qmt.deploy `
  --kind strategy `
  --repo-root . `
  --config my\runtime\qmt_config.json `
  --output "D:\QMT\strategies\qlib_qmt_entry.py"
```

QMT在T日09:30～09:31分钟内运行（09:32为排他截止时间）后，只应生成：

```text
my/runtime/qmt_state/<T日>/read_only_preflight.json
```

不应出现任何新委托。15:00后可生成券商账户收盘快照，但缺少可靠 `total_asset` 时只标记 `missing`，不能用本地估算补值。

## 5. 通过标准

第一轮 Windows 验收只要求：

1. 探针报告成功生成且不含账户事实值；
2. QMT Python 版本、文件访问、账户/持仓/盘口字段可识别；
3. 只读预演的卖出排名、跳过原因和盘口保护价合理；
4. QMT 委托列表没有新增订单；
5. 重启或重复触发不会重复生成不同批次。

配置里的桥接模块也被代码白名单锁定；改成其他模块会直接停止，不能通过自定义桥绕过只读保证。

完成后只需要提供脱敏的`qmt_probe.json`字段结构和脱敏日志。`read_only_preflight.json`包含现金、持仓和行情，原始QMT日志也可能包含总资产；不得上传原文件，账号、金额、证券代码、数量、路径全部脱敏后再提供。下一步才会针对已确认的国金模拟接口实现报单、30秒撤单、查单恢复和成交回写。

首轮绝不能运行或加载：`my/trading/iquant_qlib.py`、`my/trading/iquant_lizi.py`、任何旧iQuant脚本、手写报单探针、自定义bridge或`QmtExecutionEngine.run()`；也不能修改`mode`、桥接白名单或危险探针开关来排障。探针与正式只读入口不要同时加载。
