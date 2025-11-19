# coding: gbk
# 测试脚本：逐步验证 iQuant API 是否正常工作

import traceback

# ===== 配置区 =====
ACCOUNT_ID = "410015004039"
ACCOUNT_TYPE = "STOCK"  # 必须大写
# ==================


def test_account_info(ContextInfo):
    """测试1：检查账号基本信息"""
    print("\n========== 测试1：账号基本信息 ==========")
    try:
        acc_info = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "account")
        if acc_info:
            print(f"[OK] 获取账号信息成功，数量: {len(acc_info)}")
            for i in acc_info:
                print(f"  账号ID: {getattr(i, 'm_strAccountID', 'N/A')}")
                print(f"  可用资金: {getattr(i, 'm_dAvailable', 'N/A')}")
                print(f"  总资产: {getattr(i, 'm_dBalance', 'N/A')}")
        else:
            print("[WARN] 账号信息为空")
    except NameError as e:
        print(f"[ERROR] get_trade_detail_data 函数不存在: {e}")
        print("[INFO] 请确保在 iQuant 策略编辑器中运行此脚本")
    except Exception as e:
        print(f"[ERROR] 获取账号信息失败: {e}")
        traceback.print_exc()


def test_positions(ContextInfo):
    """测试2：获取持仓"""
    print("\n========== 测试2：获取持仓 ==========")
    try:
        positions = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "position")
        if positions:
            print(f"[OK] 获取持仓成功，数量: {len(positions)}")
            for pos in positions[:5]:  # 只打印前5条
                code = getattr(pos, 'm_strInstrumentID', 'N/A')
                vol = getattr(pos, 'm_nVolume', 'N/A')
                available = getattr(pos, 'm_nCanUseVolume', 'N/A')
                cost = getattr(pos, 'm_dOpenPrice', 'N/A')
                print(f"  {code}: 持仓={vol}, 可用={available}, 成本={cost}")

                # 查看完整属性列表（调试用）
                if hasattr(pos, '__dict__'):
                    print(f"    对象属性: {list(vars(pos).keys())[:10]}")
                else:
                    print(f"    对象属性: {[a for a in dir(pos) if not a.startswith('_')][:10]}")
        else:
            print("[WARN] 持仓为空（可能账户无持仓）")
    except Exception as e:
        print(f"[ERROR] 获取持仓失败: {e}")
        traceback.print_exc()


def test_orders(ContextInfo):
    """测试3：获取委托"""
    print("\n========== 测试3：获取委托 ==========")
    try:
        orders = get_trade_detail_data(ACCOUNT_ID, ACCOUNT_TYPE, "order")
        if orders:
            print(f"[OK] 获取委托成功，数量: {len(orders)}")
            for order in orders[:3]:
                code = getattr(order, 'm_strInstrumentID', 'N/A')
                vol = getattr(order, 'm_nVolumeTotalOriginal', 'N/A')
                status = getattr(order, 'm_nOrderStatus', 'N/A')
                print(f"  {code}: 委托量={vol}, 状态={status}")
        else:
            print("[INFO] 无委托记录")
    except Exception as e:
        print(f"[ERROR] 获取委托失败: {e}")
        traceback.print_exc()


def test_quotes_simple(ContextInfo):
    """测试4：获取行情（仅在交易时间）"""
    print("\n========== 测试4：获取行情（交易时间才有效）==========")
    try:
        test_codes = ['600000.SH', '000001.SZ']
        quotes = ContextInfo.get_full_tick(test_codes)
        if quotes:
            print(f"[OK] 获取行情成功，数量: {len(quotes)}")
            for code, data in list(quotes.items())[:2]:
                last = data.get('lastPrice', 'N/A')
                bid1 = data.get('bidPrice', [None])[0] if 'bidPrice' in data else 'N/A'
                ask1 = data.get('askPrice', [None])[0] if 'askPrice' in data else 'N/A'
                print(f"  {code}: 最新价={last}, 买一={bid1}, 卖一={ask1}")
        else:
            print("[WARN] 行情为空（可能非交易时间）")
    except Exception as e:
        print(f"[WARN] 获取行情失败（非交易时间正常）: {e}")


def test_instrumentdetail(ContextInfo):
    """测试5：获取合约详情（涨跌停价格）"""
    print("\n========== 测试5：获取合约详情 ==========")
    try:
        test_code = '600000.SH'
        detail = ContextInfo.get_instrumentdetail(test_code)
        if detail:
            print(f"[OK] 获取合约详情成功")
            print(f"  合约: {detail.get('InstrumentID', 'N/A')}")
            print(f"  名称: {detail.get('InstrumentName', 'N/A')}")
            print(f"  涨停价: {detail.get('UpStopPrice', 'N/A')}")
            print(f"  跌停价: {detail.get('DownStopPrice', 'N/A')}")
            print(f"  前收盘: {detail.get('PreClose', 'N/A')}")
            print(f"  是否可交易: {detail.get('IsTrading', 'N/A')}")
        else:
            print("[WARN] 合约详情为空")
    except Exception as e:
        print(f"[ERROR] 获取合约详情失败: {e}")
        traceback.print_exc()


# ========== iQuant 入口函数 ==========
def init(ContextInfo):
    """初始化函数"""
    print("\n" + "="*60)
    print("iQuant API 测试脚本")
    print("="*60)
    if ACCOUNT_ID:
        ContextInfo.accid = ACCOUNT_ID
    print(f"账号ID: {ACCOUNT_ID}")
    print(f"账号类型: {ACCOUNT_TYPE}")
    print(f"ContextInfo.accid: {getattr(ContextInfo, 'accid', 'N/A')}")


def handlebar(ContextInfo):
    """主函数：在最后一根 bar 执行测试"""
    # 只在最后一根 bar 执行
    is_last = getattr(ContextInfo, "is_last_bar", lambda: True)
    if not is_last():
        return

    print("\n[INFO] 开始执行测试...")

    # 依次执行所有测试
    test_account_info(ContextInfo)
    test_positions(ContextInfo)
    test_orders(ContextInfo)
    test_instrumentdetail(ContextInfo)
    test_quotes_simple(ContextInfo)  # 放最后，因为非交易时间会失败

    print("\n" + "="*60)
    print("[INFO] 测试完成！")
    print("="*60)
