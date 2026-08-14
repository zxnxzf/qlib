# lgb_alpha158_gate905_v1

当前影子模式的唯一策略定义。三份配置、季度滚动代码、正式模型和发布清单必须一起评审、一起发布。

```text
workflow.yaml   Alpha158、5日标签和LightGBM参数
rolling.yaml    季度滚动窗口及开闭区间
strategy.yaml   门控、TopK/Drop2、成本和执行口径
workflow.py     显式候选训练、正式发布校验和每日评分
models/         通过回测批准的LightGBM原生文本模型（进入Git）
releases/       每季度发布清单和验证报告（进入Git）
```

日常影子/QMT只允许读取 `status=published` 的季度发布；缺模型、缺清单、配置/运行代码哈希或特征顺序不一致，以及原生Booster无法加载时直接停摆，不会临时训练。运行代码哈希同时覆盖Qlib的Alpha158 handler、loader、processor和表达式实现。模型、清单、验证报告、配置和运行代码还必须已被当前Git版本跟踪且本地无修改。每个新信号包都会保存策略ID、季度发布号、模型/配置/运行代码哈希和来源提交。研究阶段的评分、报告、HTML和候选模型仍放在 `my/artifacts/`，不能复制到这里冒充正式发布。

首个正式发布为2026Q3。历史379日对账仍使用 `my/artifacts/candidate1_pred.pkl` 归档评分；该文件不是模型，只有 `models/2026Q3.txt` 及对应发布清单才允许用于正式评分。

安全发布顺序：

1. `train-candidate YYYY-MM-DD` 只在 `my/artifacts/strategy_candidates/` 生成候选。
2. `compare-candidate YYYY-MM-DD --archive <已验收评分.pkl>` 重算季度重叠区间；要求索引完整、绝对误差不超过 `1e-12`、每日Top100全部一致。
3. 评审并写入 `releases/YYYYQn-validation.md`。
4. `promote-candidate YYYY-MM-DD` 只提升通过校验的候选，生成模型和清单。
5. 将模型、报告和清单提交Git后运行 `verify YYYY-MM-DD`；提交前生产入口仍会拒绝加载。
