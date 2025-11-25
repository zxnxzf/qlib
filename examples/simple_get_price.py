# coding: gbk
"""
最简单的iQuant价格获取示例
功能：在回测中获取000001.SZ的收盘价并打印
"""

def init(ContextInfo):
    """初始化函数"""
    print("=" * 60)
    print("[init] 脚本已加载")
    print("=" * 60)
    ContextInfo.stock_code = "000001.SZ"  # 平安银行


def handlebar(ContextInfo):
    """
    每个bar触发
    使用 get_full_tick 获取000001.SZ的价格
    （这个API在 iquant_qlib.py 中已验证可用）
    """
    stock = ContextInfo.stock_code

    # 获取当前bar的时间
    barpos = getattr(ContextInfo, 'barpos', -1)
    timetag = ContextInfo.get_bar_timetag(barpos)

    if timetag:
        from datetime import datetime
        date_str = datetime.fromtimestamp(timetag / 1000).strftime('%Y-%m-%d')
        print(f"\n[{date_str}] barpos={barpos}, 正在获取 {stock} 的价格...")
    else:
        print(f"\n[barpos={barpos}] 正在获取 {stock} 的价格...")

    # 使用 get_full_tick（iquant_qlib.py 验证可用的接口）
    try:
        print(f"  [调用] ContextInfo.get_full_tick(['{stock}'])")
        data = ContextInfo.get_full_tick([stock])

        print(f"  [返回] type={type(data)}")
        print(f"  [返回] value={data}")

        # 解析返回的字典
        if isinstance(data, dict) and stock in data:
            tick = data[stock]
            print(f"  [tick数据类型] {type(tick)}")
            print(f"  [tick数据内容] {tick}")

            # 尝试从字典中提取价格
            if isinstance(tick, dict):
                last_price = tick.get("lastPrice") or tick.get("last") or tick.get("price")
                bid_prices = tick.get("bidPrice", [])
                ask_prices = tick.get("askPrice", [])
                up_stop = tick.get("UpStopPrice")
                down_stop = tick.get("DownStopPrice")

                print(f"\n  ✅ 成功获取行情:")
                print(f"     最新价: {last_price}")
                print(f"     买一价: {bid_prices[0] if bid_prices else 'N/A'}")
                print(f"     卖一价: {ask_prices[0] if ask_prices else 'N/A'}")
                print(f"     涨停价: {up_stop}")
                print(f"     跌停价: {down_stop}")
            # 尝试从对象属性中提取
            elif hasattr(tick, 'lastPrice'):
                print(f"\n  ✅ 成功获取最新价: {tick.lastPrice}")
            else:
                print(f"  ⚠️  无法解析tick数据结构")
        else:
            print(f"  ❌ 返回数据中没有 {stock}")

    except Exception as e:
        print(f"  ❌ get_full_tick 失败: {e}")
        import traceback
        traceback.print_exc()

    print(f"  [结束] 本bar测试完成")
