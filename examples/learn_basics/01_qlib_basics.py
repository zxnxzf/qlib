#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Qlib 入门学习脚本 - 基础数据访问
这个脚本展示了 Qlib 的基本使用流程
"""

import qlib
from qlib.constant import REG_CN
from qlib.data import D


def main():
	# ============================================
	# 第一步：初始化 Qlib
	# ============================================
	print("=" * 60)
	print("第一步：初始化 Qlib")
	print("=" * 60)

	provider_uri = "~/.qlib/qlib_data/cn_data"
	qlib.init(provider_uri=provider_uri, region=REG_CN)
	print(f"[OK] Qlib 初始化成功！数据路径: {provider_uri}")
	print()

	# ============================================
	# 第二步：获取交易日历
	# ============================================
	print("=" * 60)
	print("第二步：获取交易日历")
	print("=" * 60)

	# 获取 2020 年前 10 个交易日
	calendar = D.calendar(start_time='2020-01-01', end_time='2020-12-31', freq='day')
	print(f"2020年前10个交易日:")
	for i, date in enumerate(calendar[:10], 1):
		print(f"  {i}. {date}")
	print()

	# ============================================
	# 第三步：获取股票列表
	# ============================================
	print("=" * 60)
	print("第三步：获取股票列表（沪深300）")
	print("=" * 60)

	# 获取沪深300成分股
	instruments = D.instruments('csi300')
	stock_list = D.list_instruments(
		instruments=instruments,
		start_time='2020-01-01',
		end_time='2020-12-31',
		as_list=True
	)
	print(f"沪深300成分股数量: {len(stock_list)}")
	print(f"前10只股票: {stock_list[:10]}")
	print()

	# ============================================
	# 第四步：获取股票数据（基础数据）
	# ============================================
	print("=" * 60)
	print("第四步：获取股票的基础行情数据")
	print("=" * 60)

	# 使用沪深300中实际存在的股票
	if stock_list:
		test_stock = stock_list[0]
		print(f"测试股票: {test_stock}")

		instruments = [test_stock]
		fields = ['$close', '$volume', '$open', '$high', '$low']

		data = D.features(
			instruments,
			fields,
			start_time='2020-01-01',
			end_time='2020-01-10',
			freq='day'
		)
		print(f"股票 {test_stock} 2020年1月初数据:")
		print(data.head())
	print()

	# ============================================
	# 第五步：使用表达式引擎计算技术指标
	# ============================================
	print("=" * 60)
	print("第五步：使用表达式引擎计算技术指标")
	print("=" * 60)

	if stock_list:
		# 计算一些常用技术指标
		fields = [
			'$close',                          # 收盘价
			'Ref($close, 1)',                  # 昨日收盘价
			'($close - Ref($close, 1)) / Ref($close, 1)',  # 日收益率
			'Mean($close, 5)',                 # 5日均价
			'Std($close, 20)',                 # 20日标准差
		]

		data = D.features(
			[test_stock],
			fields,
			start_time='2020-01-01',
			end_time='2020-02-01',
			freq='day'
		)
		print("技术指标计算结果（前10行）:")
		print(data.head(10))
	print()

	# ============================================
	# 第六步：获取多只股票的截面数据
	# ============================================
	print("=" * 60)
	print("第六步：获取多只股票的截面数据")
	print("=" * 60)

	if stock_list and len(stock_list) >= 4:
		# 获取多只股票在某一天的数据
		instruments = stock_list[:4]
		fields = ['$close', '$volume', 'Mean($close, 5)']

		data = D.features(
			instruments,
			fields,
			start_time='2020-01-15',
			end_time='2020-01-15',
			freq='day'
		)
		print(f"多只股票的截面数据（2020-01-15）:")
		print(data)
	print()

	print("=" * 60)
	print("[OK] 基础学习完成！")
	print("=" * 60)
	print("\n下一步建议:")
	print("1. 查看 examples/workflow_by_code.py 了解完整的建模流程")
	print("2. 运行: cd examples && qrun benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml")
	print("3. 查看 examples/tutorial/detailed_workflow.ipynb 学习更多细节")


if __name__ == '__main__':
	main()
