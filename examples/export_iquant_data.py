# coding: gbk
"""
从 iQuant 导出股票历史数据用于对比验证

功能：
1. 读取 stock_list.txt 中的股票列表和日期范围
2. 调用 iQuant API 获取历史K线数据
3. 导出收盘价数据到 predictions/iquant_data.csv

使用方法：
1. 在 iQuant 客户端中加载此脚本
2. 确保 predictions/stock_list.txt 已由 qlib 脚本生成
3. 运行后会在实时 bar 时自动导出数据
"""

import os
import pandas as pd
from datetime import datetime

# ===== 配置 =====
STOCK_LIST_PATH = r"D:\code\qlib\qlib\predictions\stock_list.txt"
OUTPUT_CSV_PATH = r"D:\code\qlib\qlib\predictions\iquant_data.csv"
# ================


def _parse_stock_list():
    """解析股票列表文件，提取股票代码和日期范围"""
    if not os.path.isfile(STOCK_LIST_PATH):
        print(f"[错误] 股票列表文件不存在: {STOCK_LIST_PATH}")
        print(f"[提示] 请先运行 qlib 导出脚本生成 stock_list.txt")
        return [], None, None

    stocks = []
    start_date = None
    end_date = None

    with open(STOCK_LIST_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            # 解析日期范围
            if "日期范围:" in line:
                parts = line.split("日期范围:")
                if len(parts) > 1:
                    date_range = parts[1].strip()
                    dates = date_range.split("至")
                    if len(dates) == 2:
                        start_date = dates[0].strip()
                        end_date = dates[1].strip()

            # 跳过注释行
            if line.startswith("#") or not line:
                continue

            # 读取股票代码
            stocks.append(line)

    return stocks, start_date, end_date


def _get_bar_data(ContextInfo, stock_code, start_date, end_date):
    """
    获取单只股票的历史K线数据

    参数:
        stock_code: 股票代码，格式如 '600000.SH'
        start_date: 开始日期，格式 'YYYY-MM-DD'
        end_date: 结束日期，格式 'YYYY-MM-DD'

    返回:
        DataFrame with columns: date, close
    """
    try:
        # 先下载历史数据到本地
        print(f"  [调试] 先下载历史数据: {stock_code}")
        try:
            ContextInfo.download_history_data(
                stock_code=[stock_code],
                period='1d',
                start_time=start_date,
                end_time=end_date
            )
            print(f"  [成功] 历史数据下载完成")
        except Exception as e_download:
            print(f"  [警告] 下载历史数据失败: {e_download}")
            # 继续尝试获取数据

        # 使用推荐的 get_market_data_ex API
        # 参考 _PyContextInfo.py 的提示
        print(f"  [调试] 调用 get_market_data_ex({stock_code}, {start_date}, {end_date})")

        # get_market_data_ex 的正确参数
        # 参考 _PyContextInfo.py line 132
        kline = ContextInfo.get_market_data_ex(
            fields=['close'],          # 正确的参数名是 fields
            stock_code=[stock_code],
            period='1d',
            start_time=start_date,
            end_time=end_date,
            count=-1,
            dividend_type='none',      # 不复权
            fill_data=False,
            subscribe=False
        )

        print(f"  [调试] 返回值类型: {type(kline)}")

        if kline is None:
            print(f"  [警告] {stock_code} 返回 None")
            return pd.DataFrame()

        # get_market_data_ex 返回的是字典格式
        # {股票代码: DataFrame}
        if isinstance(kline, dict):
            print(f"  [调试] 返回字典，键: {list(kline.keys())}")

            # 获取该股票的数据
            stock_data = kline.get(stock_code)

            # 打印数据类型和内容
            print(f"  [调试] stock_data 类型: {type(stock_data)}")
            print(f"  [调试] stock_data 内容: {stock_data}")

            if stock_data is None:
                print(f"  [警告] {stock_code} 返回 None")
                return pd.DataFrame()

            # 检查是否是 DataFrame
            if isinstance(stock_data, pd.DataFrame):
                print(f"  [调试] 这是一个 DataFrame，shape: {stock_data.shape}")
                print(f"  [调试] 列名: {list(stock_data.columns)}")
                print(f"  [调试] 前几行:\n{stock_data.head()}")

                if len(stock_data) == 0:
                    print(f"  [警告] DataFrame 为空")
                    return pd.DataFrame()
            elif hasattr(stock_data, '__len__'):
                print(f"  [调试] 数据长度: {len(stock_data)}")
                if len(stock_data) == 0:
                    print(f"  [警告] {stock_code} 数据长度为 0")
                    return pd.DataFrame()
            else:
                print(f"  [警告] 未知数据类型")
                return pd.DataFrame()

            print(f"  [调试] 数据长度: {len(stock_data)}")
            print(f"  [调试] 第一条数据: {stock_data[0] if len(stock_data) > 0 else 'N/A'}")

            # 解析数据
            records = []
            for i, item in enumerate(stock_data):
                try:
                    # get_market_data_ex 返回的数组: [time, close]
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        time_str = str(item[0])[:10] if item[0] else ''
                        close_val = float(item[1]) if item[1] is not None else None

                        if time_str and close_val is not None:
                            records.append({
                                "date": time_str,
                                "stock_code": stock_code,
                                "close": close_val
                            })
                    else:
                        print(f"  [警告] 第 {i} 条数据格式异常: {item}")

                except Exception as e:
                    print(f"  [警告] 解析第 {i} 条数据失败: {e}")
                    continue

            if records:
                print(f"  [成功] 解析 {len(records)} 条数据")
                return pd.DataFrame(records)
            else:
                print(f"  [警告] 未能解析任何数据")
                return pd.DataFrame()
        else:
            print(f"  [警告] 返回类型不是字典: {type(kline)}")
            return pd.DataFrame()

    except Exception as e:
        print(f"  [错误] 获取 {stock_code} 数据失败: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()


def _preload_history_data(ContextInfo, stocks, start_date, end_date):
    """提前批量下载历史数据，供 handlebar 直接读取。"""
    download_func = getattr(ContextInfo, "download_history_data", None)
    if not callable(download_func):
        print("[WARN] ContextInfo.download_history_data 不可用，跳过预下载")
        return False

    if not stocks:
        return False

    if not start_date or not end_date:
        return False

    print("\n[步骤0] 预下载历史数据...")
    success = False
    for code in stocks:
        try:
            download_func(
                stock_code=[code],
                period='1d',
                start_time=start_date,
                end_time=end_date,
            )
            success = True
            print(f"  [OK] 已触发 {code} {start_date} 至 {end_date} 的历史数据下载")
        except Exception as exc:
            print(f"  [警告] 下载 {code} 历史数据失败: {exc}")
    if success:
        print("[OK] 历史数据预下载完成")
    else:
        print("[WARN] 未能成功预下载历史数据")
    return success


def _ensure_history_for_date(ContextInfo, stock_code, date_str):
    """按日兜底触发历史数据下载，防止 get_market_data_ex 返回空。"""
    if getattr(ContextInfo, "_history_preloaded", False):
        return True

    download_func = getattr(ContextInfo, "download_history_data", None)
    if not callable(download_func):
        return False

    cache = getattr(ContextInfo, "_history_cache", None)
    if cache is None:
        cache = set()
        ContextInfo._history_cache = cache

    cache_key = f"{stock_code}|{date_str}"
    if cache_key in cache:
        return True

    try:
        download_func(
            stock_code=[stock_code],
            period='1d',
            start_time=date_str,
            end_time=date_str,
        )
        cache.add(cache_key)
        print(f"  [INFO] 已兜底下载 {stock_code} {date_str} 的数据")
        return True
    except Exception as exc:
        print(f"  [警告] 下载 {stock_code} {date_str} 数据失败: {exc}")
        return False


def _extract_close_value(payload):
    """从 ContextInfo 接口返回的数据里解析 close 值。"""
    if payload is None:
        return None, "payload 为 None"

    if isinstance(payload, pd.DataFrame):
        if "close" in payload.columns and not payload.empty:
            return float(payload["close"].iloc[-1]), f"DataFrame rows={len(payload)}"
        return None, f"DataFrame 缺少 close 列或为空 columns={list(payload.columns)} size={payload.shape}"

    if isinstance(payload, dict):
        if "close" in payload:
            try:
                return float(payload["close"]), "dict['close']"
            except Exception as exc:
                return None, f"dict['close'] 无法转换: {exc}"

        # 常见字段尝试深入解析
        for key in ("data", "values", "items", "records"):
            if key in payload:
                value = payload[key]
                result, reason = _extract_close_value(value)
                if result is not None:
                    return result, f"dict[{key}] -> {reason}"

        # 如果字典只有一个 value，也尝试深入
        if len(payload) == 1:
            value = next(iter(payload.values()))
            result, reason = _extract_close_value(value)
            if result is not None:
                return result, f"dict(single) -> {reason}"

        return None, f"dict keys={list(payload.keys())}"

    if isinstance(payload, (list, tuple)):
        if not payload:
            return None, "list/tuple 为空"

        for entry in payload:
            if isinstance(entry, dict):
                result, reason = _extract_close_value(entry)
                if result is not None:
                    return result, f"list(dict) -> {reason}"
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                close_candidate = entry[1]
                if close_candidate is not None:
                    try:
                        return float(close_candidate), f"list(tuple) sample={entry}"
                    except Exception as exc:
                        return None, f"list(tuple) close 无法转换: {exc}"
        return None, f"list 内容示例: {payload[0]}"

    return None, f"无法从类型 {type(payload)} 提取 close"


def _export_data(ContextInfo):
    """导出所有股票的历史数据"""
    print("\n" + "=" * 60)
    print("iQuant 数据导出工具")
    print("=" * 60)

    # 读取股票列表
    print("\n[步骤1] 读取股票列表...")
    stocks, start_date, end_date = _parse_stock_list()

    if not stocks:
        print("  [错误] 未找到股票列表")
        return

    print(f"  [OK] 已读取 {len(stocks)} 只股票")
    print(f"  [OK] 日期范围: {start_date} 至 {end_date}")
    for stock in stocks:
        print(f"     - {stock}")

    # 获取历史数据
    print("\n[步骤2] 获取历史K线数据...")
    all_data = []

    for i, stock in enumerate(stocks, 1):
        print(f"  [{i}/{len(stocks)}] 正在获取 {stock} ...")
        df = _get_bar_data(ContextInfo, stock, start_date, end_date)

        if not df.empty:
            all_data.append(df)
            print(f"      [OK] 获取 {len(df)} 条数据")
        else:
            print(f"      [警告] 无数据")

    if not all_data:
        print("\n[错误] 未获取到任何数据")
        return

    # 合并数据
    print("\n[步骤3] 合并数据...")
    final_df = pd.concat(all_data, ignore_index=True)
    print(f"  [OK] 共 {len(final_df)} 条数据记录")
    print(f"  [统计]")
    print(f"     - 股票数量: {final_df['stock_code'].nunique()}")
    print(f"     - 日期数量: {final_df['date'].nunique()}")
    print(f"     - 实际日期范围: {final_df['date'].min()} 至 {final_df['date'].max()}")

    # 保存到 CSV
    print("\n[步骤4] 保存数据...")
    os.makedirs(os.path.dirname(OUTPUT_CSV_PATH), exist_ok=True)
    final_df.to_csv(OUTPUT_CSV_PATH, index=False, encoding="utf-8")
    print(f"  [OK] 数据已保存到: {OUTPUT_CSV_PATH}")

    print("\n" + "=" * 60)
    print("[完成] 数据导出完成！")
    print("=" * 60)


def init(ContextInfo):
    """初始化函数"""
    print("\n" + "=" * 60)
    print("[iQuant] 数据导出脚本已加载")
    print("=" * 60)
    print(f"[iQuant] 股票列表路径: {STOCK_LIST_PATH}")
    print(f"[iQuant] 输出CSV路径: {OUTPUT_CSV_PATH}")

    # 初始化数据收集列表
    ContextInfo._collected_data = []
    ContextInfo._bar_count = 0  # 手动计数 bar
    ContextInfo._history_cache = set()

    # 立即读取股票列表（不等到 barpos=0）
    print("\n[步骤1] 读取股票列表...")
    stocks, start_date, end_date = _parse_stock_list()

    if stocks:
        ContextInfo._stocks = stocks
        print(f"[INFO] ✅ 成功读取 {len(stocks)} 只股票:")
        for stock in stocks:
            print(f"    - {stock}")
        print(f"[INFO] 日期范围: {start_date} 至 {end_date}")
    else:
        # 使用硬编码的 fallback 列表
        print("[WARN] ⚠️  无法从文件读取股票列表，使用硬编码列表")
        ContextInfo._stocks = [
            "002594.SZ",
            "000002.SZ",
            "000001.SZ",
            "600519.SH",
            "600036.SH"
        ]
        print(f"[INFO] 使用 {len(ContextInfo._stocks)} 只股票")

    ContextInfo._start_date = start_date
    ContextInfo._end_date = end_date
    if start_date and end_date:
        ContextInfo._history_preloaded = _preload_history_data(ContextInfo, ContextInfo._stocks, start_date, end_date)
        if ContextInfo._history_preloaded:
            print(f"[INFO] ✅ 已预下载 {start_date} 至 {end_date} 的历史数据")
        else:
            print("[WARN] 未能预下载历史数据，将在 handlebar 中逐日触发下载")
    else:
        ContextInfo._history_preloaded = False
        print("[WARN] stock_list.txt 缺少日期范围，handlebar 将按日下载历史数据")

    print("=" * 60 + "\n")


def handlebar(ContextInfo):
    """
    handlebar 在每个 bar 触发
    在回测中逐日收集数据
    """
    # 手动计数 bar
    ContextInfo._bar_count = getattr(ContextInfo, '_bar_count', 0) + 1
    barpos = getattr(ContextInfo, 'barpos', -1)

    print(f"\n{'='*60}")
    print(f"[handlebar] Bar #{ContextInfo._bar_count} (barpos={barpos})")
    print('='*60)

    # 检查股票列表是否已加载
    if not hasattr(ContextInfo, '_stocks') or not ContextInfo._stocks:
        print("[ERROR] ❌ 股票列表未初始化！检查 init() 函数")
        return

    # 获取当前日期
    timetag = getattr(ContextInfo, 'get_bar_timetag', lambda x: None)(barpos)

    if not timetag:
        print(f"[WARN] ⚠️  无法获取时间戳 (barpos={barpos})")
        return

    # 转换时间戳为日期字符串
    date_str = datetime.fromtimestamp(timetag / 1000).strftime('%Y-%m-%d')
    print(f"[INFO] 📅 当前日期: {date_str}")
    print(f"[INFO] 📊 开始收集 {len(ContextInfo._stocks)} 只股票的数据...")

    # 使用 get_history_data 获取当前 bar 的收盘价（正确的方法）
    # 在回测模式下，这个方法会返回当前 bar 对应日期的历史数据
    success_count = 0

    try:
        # 先设置股票池（get_history_data 要求先设置）
        ContextInfo.set_universe(ContextInfo._stocks)

        # 获取当前 bar 的收盘价数据
        # len=1: 获取1根K线（当前bar）
        # period='1d': 日线
        # field='close': 收盘价
        # dividend_type=0: 不复权
        hisdict = ContextInfo.get_history_data(1, '1d', 'close', 0)

        print(f"[INFO] get_history_data 返回类型: {type(hisdict)}")

        # hisdict 是一个字典，key 是股票代码，value 是收盘价数组
        if isinstance(hisdict, dict):
            print(f"[INFO] 返回 {len(hisdict)} 只股票数据")

            for i, stock in enumerate(ContextInfo._stocks, 1):
                if stock in hisdict:
                    close_data = hisdict[stock]

                    # 打印第一只股票的详细信息
                    if i == 1:
                        print(f"[调试] {stock} 数据类型: {type(close_data)}, 内容: {close_data}")

                    # close_data 可能是数组或单个值
                    if isinstance(close_data, (list, tuple)) and len(close_data) > 0:
                        close_price = float(close_data[-1])  # 取最后一个
                    elif isinstance(close_data, (int, float)):
                        close_price = float(close_data)
                    else:
                        print(f"  [{i}/{len(ContextInfo._stocks)}] [警告] {stock}: 无法解析数据格式")
                        continue

                    ContextInfo._collected_data.append({
                        'date': date_str,
                        'stock_code': stock,
                        'close': close_price
                    })
                    success_count += 1
                    print(f"  [{i}/{len(ContextInfo._stocks)}] [成功] {stock}: {close_price:.4f}")
                else:
                    print(f"  [{i}/{len(ContextInfo._stocks)}] [警告] {stock}: 未在返回数据中")
        else:
            print(f"[ERROR] get_history_data 返回类型异常: {type(hisdict)}")

    except Exception as e:
        print(f"[ERROR] get_history_data 调用失败: {e}")
        import traceback
        traceback.print_exc()

    print(f"[INFO] 本 bar 成功收集 {success_count}/{len(ContextInfo._stocks)} 只股票")
    print(f"[INFO] 累计收集 {len(ContextInfo._collected_data)} 条数据记录")

    # 检查是否是最后一根 bar
    is_last_bar_func = getattr(ContextInfo, 'is_last_bar', lambda: False)
    is_last = is_last_bar_func()
    print(f"[DEBUG] is_last_bar() = {is_last}")

    # 保存数据的条件：
    # 1. is_last_bar() 返回 True，或
    # 2. 已收集足够的数据（例如 >= 40 条，即 8 天 * 5 只股票）
    should_save = is_last or len(ContextInfo._collected_data) >= 40

    if should_save:
        print("\n" + "=" * 60)
        print("[iQuant] 数据收集完成，开始保存...")
        print("=" * 60)

        if ContextInfo._collected_data:
            df = pd.DataFrame(ContextInfo._collected_data)

            print(f"[统计] 共收集 {len(df)} 条数据记录")
            print(f"   - 股票数: {df['stock_code'].nunique()}")
            print(f"   - 日期数: {df['date'].nunique()}")
            print(f"   - 日期范围: {df['date'].min()} 至 {df['date'].max()}")

            # 打印前几条和后几条数据
            print(f"\n[预览] 前5条数据:")
            print(df.head(5).to_string(index=False))
            print(f"\n[预览] 后5条数据:")
            print(df.tail(5).to_string(index=False))

            # 保存到 CSV
            os.makedirs(os.path.dirname(OUTPUT_CSV_PATH), exist_ok=True)
            df.to_csv(OUTPUT_CSV_PATH, index=False, encoding='utf-8')
            print(f"\n[成功] ✅ 数据已保存到: {OUTPUT_CSV_PATH}")
        else:
            print("[警告] ⚠️  未收集到任何数据")

        print("=" * 60)
