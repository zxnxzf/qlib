"""my.quant —— 策略研究与影子/实盘运行的可复用核心包。

模块分层（每层职责单一，向下依赖）：
    config     全局口径与路径
    data       数据更新/校验/读取 + 免费指数备源
    gate       趋势门控规则
    signal_    季度滚动模型 + 每日打分
    portfolio  单步组合决策（订单生成）
    execution  执行器接口（Shadow / 将来 QMT）
    ledger     账本持久化
    nightly    每晚流程编排
"""
