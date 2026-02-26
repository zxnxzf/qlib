# 手动实盘标的池去北交所设计

**背景**
- 手动实盘使用 `market: all` 会包含 `BJ` 标的，但实盘无法交易北交所。
- 直接在 `~/.qlib/qlib_data/cn_data/instruments/` 新增文件会被数据更新覆盖。

**目标**
- 回测与实盘统一使用“去 BJ”的标的池。
- 不修改 Qlib 核心逻辑，避免更新覆盖问题。

**方案**
- 在仓库内新增标的池文件：`examples/custom/instruments/all_no_bj.txt`。
- 运行 `manual_daily_trade.py` 前自动同步该文件到 `~/.qlib/qlib_data/cn_data/instruments/all_no_bj.txt`。
- Workflow 配置使用 `market: all_no_bj`，确保回测和实盘一致。

**数据流**
- 启动脚本时执行同步。
- Qlib 初始化读取 `all_no_bj.txt` 作为 instruments。
- 即使更新覆盖数据目录，下一次运行仍会恢复自定义标的池。

**改动点**
- 新增 `examples/custom/instruments/all_no_bj.txt`。
- `manual_daily_trade.py` 增加启动前同步逻辑。
- `workflow_config_lightgbm_Alpha158_2020_2025.yaml` 中 `market` 改为 `all_no_bj`。

**风险与回滚**
- 若同步源文件不存在，脚本需输出错误并退出，避免误跑。
- 回滚只需恢复 workflow 的 `market: all` 并移除同步逻辑。
