# Repository Guidelines

## 项目结构与模块组织
- `qlib/` 存放核心 Python 包，包括数据层、策略、工作流与 `contrib` 扩展；`qlib/data/_libs` 提供必须的 Cython 扩展。
- `tests/` 与运行逻辑按域划分文件夹（如 backtest、rolling、rl），公共夹具集中在 `tests/conftest.py`。
- `examples/` 提供示例脚本与笔记本，`docs/` 保管文档与构建输出；`build/`、`mlruns/` 视作生成目录，勿直接提交。
- `scripts/` 收录自动化与数据管线脚本，新增工具需保证幂等并附注释说明输入输出。

## 构建、测试与开发命令
- `make prerequisite` 在缺失 `_libs` 产物时重建 Cython 扩展，新环境必须执行一次。
- `make dev` 以可编辑模式安装 pyqlib 及所有可选依赖；依赖更新后再次运行。
- `make test` 安装测试依赖后手动执行 `pytest`，常用示例：`pytest tests -m "not slow"`。
- `make black`、`make lint`、`make clean` 分别负责格式化校验、联合静态检查与清理缓存构建产物。

## 编码风格与命名约定
- 固定使用 Black（120 列）格式化；模块与函数采用 snake_case，类名使用 CamelCase。
- `flake8` 忽略列表为 `E501,F541,E266,E402,W503,E731,E203`，勿新增忽略项；公开接口需同步更新文档与注释。
- 建议在关键路径补充类型注解，与 `pyproject.toml` 中的工具配置保持一致；配置文件遵循现有 YAML/TOML 风格。

## 测试指南
- 使用 Pytest，测试文件命名遵循 `test_<功能>.py`，夹具逻辑集中管理并尊重 `pytest.ini` 中的标记。
- 长耗时或大规模数据用例请添加 `@pytest.mark.slow`，默认流水线与本地检查通过 `-m "not slow"` 跳过。
- 新增模型或算子时，至少覆盖数据加载与工作流评估路径；需要随机性的用例请调用 `qlib.utils.init` 固定种子。

## 提交与合并请求规范
- 延续现有 Conventional Commits 样式（如 `fix: download orderbook data error (#1990)`），消息精炼且可单独构建。
- 提交前执行必要的格式化、lint 与针对性 Pytest；拆分功能与重构改动，保持每个 commit 粒度清晰。
- Pull Request 需包含变更摘要、测试结果，文档或 Notebook 更新应附截图或产物说明，并链接相关 Issue。

## 配置与数据卫生
- 凭证与私钥仅存于本地 `.env` 或系统密钥管理器，避免提交大型原始数据；数据获取流程记录在 `docs/data` 或 `examples`。
- 遇到安全事件参考 `SECURITY.md` 汇报流程，协作交流遵循 `CODE_OF_CONDUCT.md` 所列行为准则。
