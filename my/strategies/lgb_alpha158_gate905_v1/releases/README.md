# 发布清单

每个正式季度模型对应一个 `<YYYYQn>.json` 和一份受版本管理的验证报告。清单至少记录：

- `strategy_id`、`release_id`、`status=published`；
- LightGBM原生模型相对路径与SHA-256；
- 三份配置的统一SHA-256；
- 评分、门控、信号包、共享规划器和执行适配代码的统一SHA-256；
- Alpha158特征列的完整顺序、列数及SHA-256；
- 训练、验证和预测窗口；
- 训练/回测所用的Git提交、批准时间，以及验证报告路径和SHA-256。发布清单本身所在的提交由Git历史/tag记录，避免清单自引用提交哈希。

影子/QMT会在读取模型前校验以上字段，并实际构造LightGBM Booster核对特征数；只有文件存在并不代表已经发布。仓库的 `.gitattributes` 固定策略文本为LF、模型文件不做换行转换，避免Windows `core.autocrlf` 破坏字节哈希。
