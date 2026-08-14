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

当前仓库尚无可发布的季度模型。历史379日对账使用 `my/artifacts/candidate1_pred.pkl` 归档评分，不是模型；需要在允许的重训时段重新训练并完成回测验收后，才能新增首个季度模型和清单。
