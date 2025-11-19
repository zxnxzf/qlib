# Qlib 日志说明文档

## 日志格式

Qlib 的日志格式为：
```
[进程ID:线程名](时间戳) 日志级别 - 模块名 - [文件:行号] - 消息内容
```

例如：
```
[38724:MainThread](2025-10-02 14:00:23,645) INFO - qlib.Initialization - [config.py:452] - default_conf: client.
```

## 常见日志解读

### 1. 初始化相关

#### `default_conf: client`
- **含义**：使用客户端模式，数据存储在本地
- **对应配置**：数据路径 `~/.qlib/qlib_data/cn_data`
- **另一种模式**：`server` 模式（使用远程数据服务器）

#### `qlib successfully initialized`
- **含义**：Qlib 初始化成功
- **检查项**：
  - 数据路径存在
  - 配置文件正确
  - 依赖包加载成功

#### `data_path={'__DEFAULT_FREQ': WindowsPath(...)}`
- **含义**：数据路径配置
- `__DEFAULT_FREQ`：默认频率（日频数据）
- 如果有多个频率，会显示多个路径

### 2. 数据访问相关

当你调用 `D.features()` 等方法时，Qlib 会：

1. **加载数据**
   - 从 `.bin` 文件读取基础数据
   - 使用内存缓存加速

2. **计算特征**
   - 通过表达式引擎计算（如 `Mean($close, 5)`）
   - 使用 Cython 加速的算子

3. **返回结果**
   - 返回 pandas DataFrame
   - 使用 MultiIndex（instrument, datetime）

### 3. 数据结构说明

#### DataFrame 索引
```python
                         $close     $volume
instrument datetime
SH600000   2020-01-02  9.258679  69536336.0
           2020-01-03  9.355201  51205424.0
```

- **第一层索引**：`instrument` (股票代码)
- **第二层索引**：`datetime` (交易日期)
- **列**：特征名称

#### 字段命名规则
- `$close`, `$open`, `$high`, `$low`, `$volume` - 基础字段（以 `$` 开头）
- `Ref($close, 1)` - 计算字段（使用算子）
- `Mean($close, 5)` - 聚合字段

### 4. 常见问题

#### 问题：数据为空 (Empty DataFrame)
**原因**：
- 股票代码不存在于该时间段
- 时间范围超出数据范围
- 股票在该期间停牌

**解决**：
- 使用 `D.list_instruments()` 检查股票是否存在
- 检查数据的时间范围
- 使用实际交易日期

#### 问题：中文乱码
**原因**：
- Windows 默认使用 GBK 编码
- 终端不支持 UTF-8

**解决**：
1. 在代码中避免使用特殊字符
2. 使用 `chcp 65001` 切换终端编码
3. 在 Python 脚本开头添加：
   ```python
   import sys
   import io
   sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
   ```

## Qlib 日志级别

Qlib 使用 loguru 进行日志管理，支持以下级别：

- **DEBUG** - 详细的调试信息
- **INFO** - 一般信息（默认）
- **WARNING** - 警告信息
- **ERROR** - 错误信息

### 设置日志级别

```python
import qlib
from qlib.log import get_module_logger

# 初始化时设置
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

# 设置日志级别
logger = get_module_logger("test")
logger.setLevel("DEBUG")  # 或 "INFO", "WARNING", "ERROR"
```

### 关闭某些模块的日志

```python
import logging

# 关闭特定模块的日志
logging.getLogger("qlib.Initialization").setLevel(logging.WARNING)
```

## 性能相关日志

当处理大量数据时，可能会看到：

```
[INFO] - Loading data from cache
[INFO] - Caching results to disk
```

这表示 Qlib 正在使用缓存机制加速数据访问。

### 缓存类型

1. **内存缓存** - 存储在 RAM 中，最快
2. **磁盘缓存** - 存储在磁盘上，持久化
3. **表达式缓存** - 缓存计算结果

### 清空缓存

```python
from qlib.data.cache import H

# 清空内存缓存
H.clear()
```

## 实用技巧

### 1. 捕获日志到文件

```python
import logging

# 配置日志输出到文件
logging.basicConfig(
    filename='qlib.log',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

### 2. 只显示错误日志

```python
qlib.init(
    provider_uri="~/.qlib/qlib_data/cn_data",
    region="cn",
    logging_level="ERROR"  # 只显示错误
)
```

### 3. 调试模式

```python
# 开启详细日志用于调试
qlib.init(
    provider_uri="~/.qlib/qlib_data/cn_data",
    region="cn",
    logging_level="DEBUG"
)
```
