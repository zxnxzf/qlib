#!/usr/bin/env python3
"""
Qlib实盘预测脚本 - 简化版
基于已训练的LightGBM模型生成每日预测结果

使用方法:
python daily_predict.py

输出文件:
- ./predictions/prediction_results_YYYYMMDD.csv
- ./predictions/prediction_results_YYYYMMDD.xlsx
- ./predictions/prediction_log_YYYYMMDD.txt
"""

import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# Qlib相关导入
import qlib
from qlib.constant import REG_CN
from qlib.utils import init_instance_by_config
from qlib.workflow import R
from qlib.data import D
from qlib.backtest import Exchange
from qlib.backtest.decision import Order, OrderDir

def main():
    print("[Qlib] Qlib实盘预测系统启动")
    print("=" * 50)

    # =================== 配置参数 ===================
    experiment_id = "866149675032491302"
    recorder_id = "3d0e78192f384bb881c63b32f743d5f8"
    prediction_date = "2025-01-03"  # 使用有数据的日期
    top_k = 50
    min_score_threshold = 0.0
    weight_method = "equal"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.abspath(os.path.join(script_dir, "..", "predictions"))
    provider_uri = "~/.qlib/qlib_data/cn_data"
    region = REG_CN

    # 交易执行参数
    total_cash = 100000  # 总资金量
    min_shares = 100  # 最小交易股数（A股100股）
    current_holdings = {}  # 当前持仓 {股票代码: 股数}

    # 交易系统配置
    use_exchange_system = True  # 是否使用Exchange系统
    price_search_days = 5  # 价格搜索回溯天数
    enable_trading_orders = True  # 是否生成交易指令
    enable_detailed_logs = True  # 是否生成详细日志

    print(f"[配置] 预测配置:")
    print(f"   预测日期: {prediction_date}")
    print(f"   实验ID: {experiment_id}")
    print(f"   推荐股票数: {top_k}")
    print(f"[交易] 交易配置:")
    print(f"   总资金: {total_cash:,}元")
    print(f"   最小股数: {min_shares}")
    print(f"   Exchange系统: {'启用' if use_exchange_system else '禁用'}")
    print(f"   交易指令: {'启用' if enable_trading_orders else '禁用'}")

    # =================== 初始化Qlib ===================
    try:
        qlib.init(provider_uri=provider_uri, region=region)
        print("[成功] Qlib环境初始化成功")
    except Exception as e:
        print(f"[错误] Qlib初始化失败: {e}")
        return False

    # =================== 加载模型 ===================
    try:
        print("\n[模型] 加载训练模型...")
        recorder = R.get_recorder(experiment_id=experiment_id, recorder_id=recorder_id)

        # 尝试加载模型 - 按优先级顺序尝试不同的文件名
        model = None
        model_files = ["params.pkl", "model.pkl", "trained_model.pkl", "lgb_model.pkl"]  # Qlib标准是params.pkl

        print("   尝试加载模型文件...")
        for model_file in model_files:
            try:
                model = recorder.load_object(model_file)
                print(f"[成功] 模型加载成功: {model_file}")
                break
            except Exception as file_err:
                print(f"   [错误] 无法加载 {model_file}: {file_err}")
                continue

        if model is None:
            print(f"[错误] 无法加载任何模型文件，尝试的文件: {model_files}")
            print("   [提示] 请检查recorder目录中是否有以上文件之一")
            return False

    except Exception as e:
        print(f"[错误] 模型加载失败: {e}")
        return False

    # =================== 数据集配置 ===================
    try:
        print("\n[数据] 配置数据集...")
        dataset_config = {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {
                "handler": {
                    "class": "Alpha158",
                    "module_path": "qlib.contrib.data.handler",
                    "kwargs": {
                        "start_time": "2020-01-01",
                        "end_time": prediction_date,
                        "instruments": "csi300"
                    }
                },
                "segments": {
                    "test": (prediction_date, prediction_date)
                }
            }
        }

        dataset = init_instance_by_config(dataset_config)
        print("[成功] 数据集配置成功")

    except Exception as e:
        print(f"[错误] 数据集配置失败: {e}")
        return False

    # =================== 执行预测 ===================
    try:
        print("\n[预测] 执行预测...")
        predictions = model.predict(dataset, segment="test")

        print(f"[成功] 预测完成: {predictions.shape}")
        print(f"   分数范围: {predictions.min():.4f} ~ {predictions.max():.4f}")

    except Exception as e:
        print(f"[错误] 预测失败: {e}")
        return False

    # =================== 处理结果 ===================
    try:
        print("\n[处理] 处理预测结果...")

        # 转换为DataFrame
        pred_df = predictions.reset_index()
        score_col = pred_df.columns[-1]
        pred_df = pred_df.rename(columns={score_col: 'score'})

        # 保留原始索引名称，确保 instrument 列为股票代码而非日期
        index_names = list(predictions.index.names or [])
        for col_name, idx_name in zip(pred_df.columns[:-1], index_names):
            if idx_name:
                pred_df = pred_df.rename(columns={col_name: idx_name})

        if not {'instrument', 'datetime'}.issubset(pred_df.columns):
            base_cols = list(pred_df.columns)
            if len(base_cols) >= 2:
                pred_df = pred_df.rename(columns={base_cols[0]: 'datetime', base_cols[1]: 'instrument'})

        pred_df = pred_df[['instrument', 'datetime', 'score']].copy()
        pred_df['instrument'] = pred_df['instrument'].astype(str)

        # 过滤和排序
        if min_score_threshold > 0:
            pred_df = pred_df[pred_df['score'] >= min_score_threshold]

        pred_df = pred_df.sort_values('score', ascending=False).head(top_k)

        len_pred = len(pred_df)
        if len_pred == 0:
            print("[警告] 没有符合条件的预测结果，生成空结果文件。")
            pred_df['target_weight'] = pd.Series(dtype=float)
        else:
            # 计算权重
            if weight_method == "equal":
                pred_df['target_weight'] = 1.0 / len_pred
            else:
                score_sum = pred_df['score'].sum()
                if score_sum == 0:
                    print("[警告] 预测得分总和为0，改用等权重分配。")
                    pred_df['target_weight'] = 1.0 / len_pred
                else:
                    pred_df['target_weight'] = pred_df['score'] / score_sum

        # 添加元数据
        pred_df['prediction_date'] = prediction_date
        pred_df['generated_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 重新排列列（包含价格列）
        available_columns = ['instrument', 'datetime', 'score', 'target_weight']
        if 'price' in pred_df.columns:
            available_columns.append('price')

        available_columns.extend(['prediction_date', 'generated_time'])
        pred_df = pred_df[available_columns]

        print(f"[成功] 结果处理完成: {len(pred_df)} 只股票")

    except Exception as e:
        print(f"[错误] 结果处理失败: {e}")
        return False

    # =================== 交易系统初始化 ===================
    trading_result = None
    if enable_trading_orders:
        try:
            print("\n[交易] 初始化Qlib标准交易系统...")

            # 从预测结果中提取股票池
            stock_pool = pred_df['instrument'].tolist()
            print(f"   股票池大小: {len(stock_pool)}只股票")

            # 计算数据时间范围（确保包含足够的历史数据）
            start_date = (pd.Timestamp(prediction_date) - pd.Timedelta(days=30)).strftime('%Y-%m-%d')
            end_date = (pd.Timestamp(prediction_date) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')

            print(f"   数据范围: {start_date} 到 {end_date}")

            # 初始化Exchange系统（标准Qlib方式）
            exchange = Exchange(
                codes=stock_pool,
                start_time=start_date,
                end_time=end_date,
                deal_price="close",  # 使用收盘价作为交易价格
                freq="day"
            )

            print("   Exchange系统初始化成功！")

            # 测试价格获取功能
            print("   测试价格获取功能...")
            test_stocks = stock_pool[:5]  # 测试前5只股票
            price_test_count = 0

            for stock in test_stocks:
                try:
                    # 使用标准Exchange接口获取价格
                    price = exchange.get_close(
                        stock_id=stock,
                        start_time=pd.Timestamp(prediction_date),
                        end_time=pd.Timestamp(prediction_date) + pd.Timedelta(days=1)
                    )

                    if price is not None and not np.isnan(price) and price > 0:
                        price_test_count += 1
                        print(f"   [成功] {stock}: {price:.2f}元")
                    else:
                        print(f"   [失败] {stock}: 价格无效 ({price})")
                except Exception as e:
                    print(f"   [失败] {stock}: 获取失败 ({e})")

            price_success_rate = price_test_count / len(test_stocks)
            print(f"   价格获取测试结果: {price_test_count}/{len(test_stocks)} 成功 ({price_success_rate:.1%})")

            if price_success_rate < 0.4:  # 如果成功率低于40%，给出警告
                print(f"   [警告] 价格获取成功率较低 ({price_success_rate:.1%})，可能影响交易指令生成")
            else:
                print(f"   [成功] 价格获取功能正常")

            # 使用标准Exchange接口为预测结果添加价格信息
            print("[数据] 获取所有股票价格信息...")
            pred_df['price'] = pred_df['instrument'].apply(
                lambda stock: exchange.get_close(
                    stock_id=stock,
                    start_time=pd.Timestamp(prediction_date),
                    end_time=pd.Timestamp(prediction_date) + pd.Timedelta(days=1)
                )
            )

            # 统计价格获取情况
            valid_prices = pred_df['price'].notna() & (pred_df['price'] > 0)
            price_success_rate = valid_prices.sum() / len(pred_df)

            print(f"   价格获取成功率: {price_success_rate:.1%} ({valid_prices.sum()}/{len(pred_df)})")

            if price_success_rate > 0.5:  # 如果超过50%的股票能获取到价格
                print("[成功] 价格数据质量良好，继续生成交易指令")

                # 过滤有价格的股票
                tradable_df = pred_df[valid_prices].copy()

                if len(tradable_df) == 0:
                    print("[错误] 没有可交易的股票（无价格数据）")
                else:
                    print(f"[结果] 可交易股票: {len(tradable_df)}只")

                    # 使用标准Exchange系统生成交易指令
                    print("[交易] 使用Qlib标准订单生成器...")

                    # 构建目标金额与目标股数
                    target_amounts: dict[str, float] = {}
                    target_share_position: dict[str, int] = {}
                    skipped_by_lot: list[str] = []
                    available_cash = total_cash * 0.95  # 使用95%资金

                    if weight_method == "equal":
                        per_stock_amount = available_cash / len(tradable_df)
                        for _, row in tradable_df.iterrows():
                            target_amounts[row['instrument']] = per_stock_amount
                    else:
                        # 按权重分配
                        total_weight = tradable_df['target_weight'].sum()
                        for _, row in tradable_df.iterrows():
                            normalized_weight = row['target_weight'] / total_weight
                            target_amount = available_cash * normalized_weight
                            target_amounts[row['instrument']] = target_amount

                    # 将目标金额转换为股数，并满足最小交易单位
                    for _, row in tradable_df.iterrows():
                        stock = row['instrument']
                        price = row['price']
                        if stock not in target_amounts:
                            continue

                        target_amount = target_amounts[stock]
                        shares = int(target_amount / price)
                        if min_shares > 1:
                            shares = (shares // min_shares) * min_shares

                        if shares >= (min_shares if min_shares > 0 else 1):
                            target_share_position[stock] = shares
                        else:
                            skipped_by_lot.append(stock)

                    if skipped_by_lot:
                        print(f"   [提示] 因最小交易单位限制未生成指令的股票: {len(skipped_by_lot)}只")

                    print(f"   目标金额配置: {len(target_amounts)}只股票")
                    print(f"   总可用资金: {available_cash:,.0f}元")

                    # 使用Exchange生成标准交易指令
                    if len(target_share_position) == 0:
                        print("[警告] 所有股票因最小交易单位限制无法生成交易指令")
                    else:
                        try:
                            trade_start = pd.Timestamp(prediction_date)
                            trade_end = trade_start + pd.Timedelta(days=1)
                            current_position = {k: int(v) for k, v in current_holdings.items()}

                            # 调用Exchange的标准订单生成函数
                            order_list = exchange.generate_order_for_target_amount_position(
                                target_position=target_share_position,
                                current_position=current_position,
                                start_time=trade_start,
                                end_time=trade_end
                            )

                            print(f"   生成的订单数量: {len(order_list)}")

                            # 转换为标准格式
                            trading_orders = []
                            total_buy_amount = 0

                            for order in order_list:
                                stock = order.stock_id
                                amount = order.amount  # 股数
                                direction = "买入" if order.direction == OrderDir.BUY else "卖出"

                                # 获取交易价格
                                try:
                                    price = exchange.get_deal_price(
                                        stock_id=stock,
                                        start_time=trade_start,
                                        end_time=trade_end,
                                        direction=order.direction
                                    )

                                    if price is None or np.isnan(price) or price <= 0:
                                        price = exchange.get_close(stock, trade_start, trade_end)
                                except Exception:
                                    price = tradable_df[tradable_df['instrument'] == stock]['price'].iloc[0]

                                # 计算交易金额
                                trade_amount = amount * price if price and price > 0 else 0

                                # 获取预测分数
                                stock_row = tradable_df[tradable_df['instrument'] == stock]
                                score = stock_row['score'].iloc[0] if not stock_row.empty else 0.0
                                weight = stock_row['target_weight'].iloc[0] if not stock_row.empty else 0.0

                                trading_orders.append({
                                    'order_id': f"{stock}_BUY_{prediction_date.replace('-', '')}",
                                    'stock': stock,
                                    'action': direction,
                                    'shares': int(amount),
                                    'price': float(price) if price and price > 0 else 0.0,
                                    'amount': trade_amount,
                                    'score': score,
                                    'weight': weight
                                })

                                if direction == "买入":
                                    total_buy_amount += trade_amount

                            trading_result = {
                                'orders': trading_orders,
                                'buy_orders': [o for o in trading_orders if o['action'] == '买入'],
                                'sell_orders': [o for o in trading_orders if o['action'] == '卖出'],
                                'total_buy_amount': total_buy_amount,
                                'total_sell_amount': 0,  # 当前为空仓，无卖出
                                'net_amount': total_buy_amount,
                                'tradable_stocks': len(tradable_df),
                                'price_success_rate': price_success_rate,
                                'exchange_used': True
                            }

                            print(f"[成功] 标准订单生成完成: {len(trading_orders)}个指令")

                        except Exception as e:
                            print(f"[错误] Exchange订单生成失败: {e}")
                            # 降级到手动计算
                            print("[降级] 使用手动计算方式...")

                            # 手动计算逻辑（保留原有逻辑作为降级方案）
                            trading_orders = []
                            available_cash = total_cash * 0.95

                            for _, row in tradable_df.iterrows():
                                stock = row['instrument']
                                if stock not in target_share_position:
                                    continue

                                price = row['price']
                                score = row['score']
                                weight = row['target_weight']
                                shares = target_share_position[stock]

                                actual_amount = shares * price

                                trading_orders.append({
                                    'order_id': f"{stock}_BUY_{prediction_date.replace('-', '')}",
                                    'stock': stock,
                                    'action': '买入',
                                    'shares': shares,
                                    'price': price,
                                    'amount': actual_amount,
                                    'score': score,
                                    'weight': weight
                                })

                            trading_result = {
                                'orders': trading_orders,
                                'buy_orders': trading_orders,
                                'sell_orders': [],
                                'total_buy_amount': sum(order['amount'] for order in trading_orders),
                                'total_sell_amount': 0,
                                'net_amount': sum(order['amount'] for order in trading_orders),
                                'tradable_stocks': len(tradable_df),
                                'price_success_rate': price_success_rate,
                                'exchange_used': False
                            }

                        print(f"[成功] 交易指令生成完成:")
                        print(f"   买入指令: {len(trading_orders)}个")
                        print(f"   预计交易金额: {trading_result['total_buy_amount']:,.0f}元")
                        print(f"   使用资金比例: {trading_result['total_buy_amount']/total_cash:.1%}")

                    # 显示交易指令样例
                    print(f"\n[交易指令] 样例（前5条）:")
                    for i, order in enumerate(trading_orders[:5]):
                        print(f"   {i+1}. {order['stock']:8s} | {order['shares']:6d}股 | "
                              f"{order['price']:8.2f}元 | {order['amount']:8.0f}元 | "
                              f"权重: {order['weight']:.4f}")

            else:
                print(f"[警告] 价格获取成功率过低({price_success_rate:.1%})，跳过交易指令生成")

        except Exception as e:
            print(f"[错误] 交易系统初始化失败: {e}")
            import traceback
            traceback.print_exc()

    # =================== 保存文件 ===================
    try:
        print("\n[保存] 保存预测结果...")

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成文件名
        date_str = prediction_date.replace('-', '')
        base_filename = f"prediction_results_{date_str}"

        saved_files = []

        # 保存CSV
        csv_file = os.path.join(output_dir, f"{base_filename}.csv")
        pred_df.to_csv(csv_file, index=False, encoding='utf-8-sig')
        saved_files.append(csv_file)

        # 保存Excel
        excel_file = os.path.join(output_dir, f"{base_filename}.xlsx")
        with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
            pred_df.to_excel(writer, sheet_name='预测结果', index=False)

            # 添加统计信息
            stats_df = pd.DataFrame({
                '指标': ['预测数量', '平均分数', '分数标准差', '最高分数', '最低分数'],
                '数值': [
                    len(pred_df),
                    pred_df['score'].mean(),
                    pred_df['score'].std(),
                    pred_df['score'].max(),
                    pred_df['score'].min()
                ]
            })
            stats_df.to_excel(writer, sheet_name='统计信息', index=False)

        saved_files.append(excel_file)

        # 保存交易指令文件（如果有的话）
        if trading_result and trading_result['orders']:
            try:
                trading_orders_file = os.path.join(output_dir, f"trading_orders_{date_str}.csv")
                orders_df = pd.DataFrame(trading_result['orders'])
                orders_df.to_csv(trading_orders_file, index=False, encoding='utf-8-sig')
                saved_files.append(trading_orders_file)
                print(f"[成功] 交易指令CSV已保存: {trading_orders_file}")

                # 保存目标仓位文件
                target_position_file = os.path.join(output_dir, f"target_position_{date_str}.csv")
                target_df = pred_df[valid_prices].copy() if 'valid_prices' in locals() else pred_df.copy()
                target_df['target_shares'] = target_df.apply(
                    lambda row: int((row.get('price', 10) * total_cash * row['target_weight'] / 100) // 100 * 100), axis=1
                )
                target_df.to_csv(target_position_file, index=False, encoding='utf-8-sig')
                saved_files.append(target_position_file)
                print(f"[成功] 目标仓位CSV已保存: {target_position_file}")

            except Exception as e:
                print(f"[警告] 保存交易指令文件失败: {e}")

        # 保存详细日志
        log_file = os.path.join(output_dir, f"prediction_log_{date_str}.txt")
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"Qlib实盘预测交易系统执行日志\\n")
            f.write(f"="*60 + "\\n")
            f.write(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\\n")
            f.write(f"预测日期: {prediction_date}\\n")
            f.write(f"实验ID: {experiment_id}\\n")
            f.write(f"记录ID: {recorder_id}\\n\\n")

            f.write(f"[系统配置]:\\n")
            f.write(f"总资金: {total_cash:,}元\\n")
            f.write(f"推荐股票数: {top_k}\\n")
            f.write(f"权重方法: {weight_method}\\n")
            f.write(f"交易指令: {'启用' if enable_trading_orders else '禁用'}\\n\\n")

            f.write(f"[预测结果]:\\n")
            f.write(f"预测股票数: {len(pred_df)}\\n")
            f.write(f"平均分数: {pred_df['score'].mean():.6f}\\n")
            f.write(f"分数标准差: {pred_df['score'].std():.6f}\\n")
            f.write(f"分数范围: {pred_df['score'].min():.6f} ~ {pred_df['score'].max():.6f}\\n")
            f.write(f"权重总和: {pred_df['target_weight'].sum():.6f}\\n\\n")

            # 添加交易系统信息
            if trading_result:
                f.write(f"[交易执行结果]:\\n")
                f.write(f"可交易股票数: {trading_result['tradable_stocks']}\\n")
                f.write(f"价格获取成功率: {trading_result['price_success_rate']:.1%}\\n")
                f.write(f"买入指令数: {len(trading_result['buy_orders'])}\\n")
                f.write(f"卖出指令数: {len(trading_result['sell_orders'])}\\n")
                f.write(f"预计买入金额: {trading_result['total_buy_amount']:,.0f}元\\n")
                f.write(f"预计卖出金额: {trading_result['total_sell_amount']:,.0f}元\\n")
                f.write(f"净交易金额: {trading_result['net_amount']:,.0f}元\\n\\n")

                f.write(f"[交易指令] 详情（前10条）:\\n")
                for i, order in enumerate(trading_result['orders'][:10]):
                    f.write(f"{i+1:2d}. {order['stock']} | {order['action']} | {order['shares']}股 | "
                           f"{order['price']:.2f}元 | {order['amount']:.0f}元 | 权重: {order['weight']:.4f}\\n")
                if len(trading_result['orders']) > 10:
                    f.write(f"... 还有 {len(trading_result['orders']) - 10} 个交易指令\\n")
            else:
                f.write(f"[交易执行结果]:\\n")
                f.write(f"未生成交易指令\\n\\n")

            f.write(f"[Top 10] 推荐股票:\\n")
            for i, row in pred_df.head(10).iterrows():
                price_info = f" | 价格: {row.get('price', 0):.2f}元" if 'price' in row and pd.notna(row.get('price')) else ""
                f.write(f"{i+1:2d}. {row['instrument']} | 分数: {row['score']:.6f} | "
                       f"权重: {row['target_weight']:.4f}{price_info}\\n")

            f.write(f"\\n[文件] 生成的文件:\\n")
            for file_path in saved_files:
                f.write(f"{file_path}\\n")

            f.write(f"\\n" + "="*60 + "\\n")
            f.write(f"注意：本系统基于历史数据训练，所有预测和交易指令仅供参考\\n")
            f.write(f"实际投资请谨慎决策，考虑市场风险和交易成本\\n")

        saved_files.append(log_file)

        print(f"[成功] 文件保存完成:")
        for file_path in saved_files:
            print(f"   [文件] {file_path}")

    except Exception as e:
        print(f"[错误] 文件保存失败: {e}")
        return False

    # =================== 显示结果 ===================
    print("\n[完成] 预测交易任务完成!")
    print("=" * 60)

    print(f"[预测摘要]:")
    print(f"   预测股票数: {len(pred_df)}")
    print(f"   平均分数: {pred_df['score'].mean():.6f}")
    print(f"   分数范围: {pred_df['score'].min():.6f} ~ {pred_df['score'].max():.6f}")

    print(f"\n[Top 5] 推荐:")
    for i, row in pred_df.head(5).iterrows():
        price_info = f" | 价格: {row.get('price', 0):.2f}元" if 'price' in row and pd.notna(row.get('price')) else ""
        print(f"   {i+1}. {row['instrument']} | 分数: {row['score']:.6f} | 权重: {row['target_weight']:.4f}{price_info}")

    # 显示交易系统结果
    if trading_result:
        print(f"\n[交易执行结果]:")
        print(f"   价格获取成功率: {trading_result['price_success_rate']:.1%}")
        print(f"   可交易股票: {trading_result['tradable_stocks']}只")
        print(f"   买入指令: {len(trading_result['buy_orders'])}个")
        print(f"   预计交易金额: {trading_result['total_buy_amount']:,.0f}元")
        print(f"   资金使用率: {trading_result['total_buy_amount']/total_cash:.1%}")

        if trading_result['orders']:
            print(f"\n[交易指令] 样例（前3条）:")
            for i, order in enumerate(trading_result['orders'][:3]):
                print(f"   {i+1}. {order['stock']:8s} | {order['shares']:6d}股 | "
                      f"{order['price']:8.2f}元 | {order['amount']:8.0f}元")
    else:
        print(f"\n[交易执行结果]:")
        print(f"   未生成交易指令（可能价格获取失败或交易功能被禁用）")

    print(f"\n[文件] 文件输出:")
    for file_path in saved_files:
        file_name = os.path.basename(file_path)
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            print(f"   [文件] {file_name} ({file_size:,} bytes)")
        else:
            print(f"   [错误] {file_name} (文件不存在)")

    print(f"\n[系统状态]:")
    print(f"   预测模型: {type(model).__name__}")
    print(f"   交易系统: {'专业模式' if trading_result else '简化模式'}")
    print(f"   数据质量: {'良好' if trading_result and trading_result['price_success_rate'] > 0.7 else '需要改善'}")

    print(f"\n[成功] 系统执行完成！生成的文件可用于量化交易系统")
    print(f"[警告]  重要提示：所有预测和交易指令仅供参考，投资有风险，请谨慎决策")

    return True

if __name__ == "__main__":
    success = main()
    if success:
        print("\n[完成] 预测成功完成!")
        sys.exit(0)
    else:
        print("\n[错误] 预测执行失败!")
        sys.exit(1)
