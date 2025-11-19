[![Python Versions](https://img.shields.io/pypi/pyversions/pyqlib.svg?logo=python&logoColor=white)](https://pypi.org/project/pyqlib/#files)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20windows%20%7C%20macos-lightgrey)](https://pypi.org/project/pyqlib/#files)
[![PypI Versions](https://img.shields.io/pypi/v/pyqlib)](https://pypi.org/project/pyqlib/#history)
[![Upload Python Package](https://github.com/microsoft/qlib/workflows/Upload%20Python%20Package/badge.svg)](https://pypi.org/project/pyqlib/)
[![Github Actions Test Status](https://github.com/microsoft/qlib/workflows/Test/badge.svg?branch=main)](https://github.com/microsoft/qlib/actions)
[![Documentation Status](https://readthedocs.org/projects/qlib/badge/?version=latest)](https://qlib.readthedocs.io/en/latest/?badge=latest)
[![License](https://img.shields.io/pypi/l/pyqlib)](LICENSE)
[![Join the chat at https://gitter.im/Microsoft/qlib](https://badges.gitter.im/Microsoft/qlib.svg)](https://gitter.im/Microsoft/qlib?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)

## :newspaper: **What's NEW!** &nbsp;   :sparkling_heart:

## :newspaper: **最新动态！** &nbsp;   :sparkling_heart:

Recent released features

近期发布的功能

### Introducing <a href="https://github.com/microsoft/RD-Agent"><img src="docs/_static/img/rdagent_logo.png" alt="RD_Agent" style="height: 2em"></a>: LLM-Based Autonomous Evolving Agents for Industrial Data-Driven R&D

### 介绍 RD-Agent：基于大语言模型的工业数据驱动研发自主进化智能体

We are excited to announce the release of **RD-Agent**📢, a powerful tool that supports automated factor mining and model optimization in quant investment R&D.

我们很高兴宣布发布 **RD-Agent**📢，这是一个强大的工具，支持量化投资研发中的自动因子挖掘和模型优化。

RD-Agent is now available on [GitHub](https://github.com/microsoft/RD-Agent), and we welcome your star🌟!

RD-Agent 现已在 [GitHub](https://github.com/microsoft/RD-Agent) 上发布，欢迎给我们点星🌟！

To learn more, please visit our [♾️Demo page](https://rdagent.azurewebsites.net/). Here, you will find demo videos in both English and Chinese to help you better understand the scenario and usage of RD-Agent.

要了解更多信息，请访问我们的[♾️演示页面](https://rdagent.azurewebsites.net/)。在这里，您将找到中英文演示视频，帮助您更好地理解 RD-Agent 的场景和用法。

---

<p align="center">
  <img src="docs/_static/img/logo/1.png" />
</p>

Qlib is an open-source, AI-oriented quantitative investment platform that aims to realize the potential, empower research, and create value using AI technologies in quantitative investment, from exploring ideas to implementing productions. Qlib supports diverse machine learning modeling paradigms, including supervised learning, market dynamics modeling, and reinforcement learning.

Qlib 是一个开源的、面向 AI 的量化投资平台，旨在通过 AI 技术在量化投资中实现潜力、赋能研究并创造价值，从探索想法到实现生产。Qlib 支持多种机器学习建模范式，包括监督学习、市场动态建模和强化学习。

An increasing number of SOTA Quant research works/papers in diverse paradigms are being released in Qlib to collaboratively solve key challenges in quantitative investment. For example, 1) using supervised learning to mine the market's complex non-linear patterns from rich and heterogeneous financial data, 2) modeling the dynamic nature of the financial market using adaptive concept drift technology, and 3) using reinforcement learning to model continuous investment decisions and assist investors in optimizing their trading strategies.

越来越多不同范式的 SOTA 量化研究成果/论文在 Qlib 中发布，以协同解决量化投资中的关键挑战。例如，1) 使用监督学习从丰富异构的金融数据中挖掘市场复杂的非线性模式，2) 使用自适应概念漂移技术对金融市场的动态性质进行建模，3) 使用强化学习对连续投资决策进行建模，并帮助投资者优化其交易策略。

It contains the full ML pipeline of data processing, model training, back-testing; and covers the entire chain of quantitative investment: alpha seeking, risk modeling, portfolio optimization, and order execution.
For more details, please refer to our paper ["Qlib: An AI-oriented Quantitative Investment Platform"](https://arxiv.org/abs/2009.11189).

它包含数据处理、模型训练、回测的完整机器学习流水线；涵盖量化投资的整个链条：alpha 挖掘、风险建模、投资组合优化和订单执行。
更多详情请参考我们的论文 ["Qlib: An AI-oriented Quantitative Investment Platform"](https://arxiv.org/abs/2009.11189)。

# Framework of Qlib

# Qlib 框架

<div style="align: center">
<img src="docs/_static/img/framework-abstract.jpg" />
</div>

The high-level framework of Qlib can be found above(users can find the [detailed framework](https://qlib.readthedocs.io/en/latest/introduction/introduction.html#framework) of Qlib's design when getting into nitty gritty).
The components are designed as loose-coupled modules, and each component could be used stand-alone.

上图展示了 Qlib 的高层框架（用户可以在深入了解时查看 Qlib 设计的[详细框架](https://qlib.readthedocs.io/en/latest/introduction/introduction.html#framework)）。
各组件被设计为松耦合模块，每个组件都可以独立使用。

Qlib provides a strong infrastructure to support Quant research. [Data](https://qlib.readthedocs.io/en/latest/component/data.html) is always an important part.
A strong learning framework is designed to support diverse learning paradigms (e.g. [reinforcement learning](https://qlib.readthedocs.io/en/latest/component/rl.html), [supervised learning](https://qlib.readthedocs.io/en/latest/component/workflow.html#model-section)) and patterns at different levels(e.g. [market dynamic modeling](https://qlib.readthedocs.io/en/latest/component/meta.html)).
By modeling the market, [trading strategies](https://qlib.readthedocs.io/en/latest/component/strategy.html) will generate trade decisions that will be executed. Multiple trading strategies and executors in different levels or granularities can be [nested to be optimized and run together](https://qlib.readthedocs.io/en/latest/component/highfreq.html).
At last, a comprehensive [analysis](https://qlib.readthedocs.io/en/latest/component/report.html) will be provided and the model can be [served online](https://qlib.readthedocs.io/en/latest/component/online.html) in a low cost.

Qlib 提供强大的基础设施来支持量化研究。[数据](https://qlib.readthedocs.io/en/latest/component/data.html)始终是重要的一部分。
设计了强大的学习框架来支持不同的学习范式（如[强化学习](https://qlib.readthedocs.io/en/latest/component/rl.html)、[监督学习](https://qlib.readthedocs.io/en/latest/component/workflow.html#model-section)）和不同层次的模式（如[市场动态建模](https://qlib.readthedocs.io/en/latest/component/meta.html)）。
通过对市场建模，[交易策略](https://qlib.readthedocs.io/en/latest/component/strategy.html)将生成将被执行的交易决策。不同层次或粒度的多个交易策略和执行器可以[嵌套以进行优化并一起运行](https://qlib.readthedocs.io/en/latest/component/highfreq.html)。
最后，将提供全面的[分析](https://qlib.readthedocs.io/en/latest/component/report.html)，并且模型可以以低成本[在线提供服务](https://qlib.readthedocs.io/en/latest/component/online.html)。

# Quick Start

# 快速开始

This quick start guide tries to demonstrate
1. It's very easy to build a complete Quant research workflow and try your ideas with _Qlib_.
2. Though with *public data* and *simple models*, machine learning technologies **work very well** in practical Quant investment.

本快速入门指南旨在演示：
1. 使用 _Qlib_ 构建完整的量化研究工作流并尝试您的想法非常简单。
2. 尽管使用*公开数据*和*简单模型*，机器学习技术在实际量化投资中**效果很好**。

Here is a quick **[demo](https://terminalizer.com/view/3f24561a4470)** shows how to install ``Qlib``, and run LightGBM with ``qrun``. **But**, please make sure you have already prepared the data following the [instruction](#data-preparation).

这是一个快速**[演示](https://terminalizer.com/view/3f24561a4470)**，展示了如何安装 ``Qlib`` 并使用 ``qrun`` 运行 LightGBM。**但是**，请确保您已按照[说明](#data-preparation)准备好数据。

## Installation

## 安装

This table demonstrates the supported Python version of `Qlib`:

下表展示了 `Qlib` 支持的 Python 版本：

**Note**:
1. **Conda** is suggested for managing your Python environment. In some cases, using Python outside of a `conda` environment may result in missing header files, causing the installation failure of certain packages.
2. Please pay attention that installing cython in Python 3.6 will raise some error when installing ``Qlib`` from source. If users use Python 3.6 on their machines, it is recommended to *upgrade* Python to version 3.8 or higher, or use `conda`'s Python to install ``Qlib`` from source.

**注意**：
1. 建议使用 **Conda** 管理您的 Python 环境。在某些情况下，在 `conda` 环境之外使用 Python 可能导致缺少头文件，从而导致某些包安装失败。
2. 请注意，在 Python 3.6 中安装 cython 会在从源代码安装 ``Qlib`` 时引发一些错误。如果用户在其机器上使用 Python 3.6，建议将 Python *升级*到 3.8 或更高版本，或使用 `conda` 的 Python 从源代码安装 ``Qlib``。

### Install with pip

### 使用 pip 安装

Users can easily install ``Qlib`` by pip according to the following command.

用户可以根据以下命令通过 pip 轻松安装 ``Qlib``。

```bash
pip install pyqlib
```

**Note**: pip will install the latest stable qlib. However, the main branch of qlib is in active development. If you want to test the latest scripts or functions in the main branch. Please install qlib with the methods below.

**注意**：pip 将安装最新的稳定版 qlib。然而，qlib 的主分支正在积极开发中。如果您想测试主分支中的最新脚本或功能，请使用以下方法安装 qlib。

### Install from source

### 从源代码安装

Also, users can install the latest dev version ``Qlib`` by the source code according to the following steps:

此外，用户可以按照以下步骤通过源代码安装最新的开发版本 ``Qlib``：

* Before installing ``Qlib`` from source, users need to install some dependencies:

* 从源代码安装 ``Qlib`` 之前，用户需要安装一些依赖项：

  ```bash
  pip install numpy
  pip install --upgrade cython
  ```

* Clone the repository and install ``Qlib`` as follows.

* 克隆仓库并按如下方式安装 ``Qlib``。

    ```bash
    git clone https://github.com/microsoft/qlib.git && cd qlib
    pip install .  # `pip install -e .[dev]` is recommended for development. check details in docs/developer/code_standard_and_dev_guide.rst
    ```

**Tips**: If you fail to install `Qlib` or run the examples in your environment,  comparing your steps and the [CI workflow](.github/workflows/test_qlib_from_source.yml) may help you find the problem.

**提示**：如果您在您的环境中安装 `Qlib` 或运行示例失败，比较您的步骤和 [CI 工作流](.github/workflows/test_qlib_from_source.yml) 可能有助于您找到问题。

**Tips for Mac**: If you are using Mac with M1, you might encounter issues in building the wheel for LightGBM, which is due to missing dependencies from OpenMP. To solve the problem, install openmp first with ``brew install libomp`` and then run ``pip install .`` to build it successfully.

**Mac 提示**：如果您使用 M1 芯片的 Mac，在构建 LightGBM 的 wheel 时可能会遇到问题，这是由于缺少 OpenMP 依赖项。要解决此问题，请先使用 ``brew install libomp`` 安装 openmp，然后运行 ``pip install .`` 成功构建。

## Data Preparation

## 数据准备

❗ Due to more restrict data security policy. The official dataset is disabled temporarily. You can try [this data source](https://github.com/chenditc/investment_data/releases) contributed by the community.
Here is an example to download the latest data.

❗ 由于更严格的数据安全策略，官方数据集暂时禁用。您可以尝试社区贡献的[这个数据源](https://github.com/chenditc/investment_data/releases)。
这是一个下载最新数据的示例。

```bash
wget https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz
mkdir -p ~/.qlib/qlib_data/cn_data
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=1
rm -f qlib_bin.tar.gz
```

The official dataset below will resume in short future.

下面的官方数据集将在不久的将来恢复。

---

Load and prepare data by running the following code:

通过运行以下代码加载和准备数据：

This dataset is created by public data collected by [crawler scripts](scripts/data_collector/), which have been released in the same repository.
Users could create the same dataset with it. [Description of dataset](https://github.com/microsoft/qlib/tree/main/scripts/data_collector#description-of-dataset)

该数据集是由在同一仓库中发布的[爬虫脚本](scripts/data_collector/)收集的公开数据创建的。
用户可以使用它创建相同的数据集。[数据集描述](https://github.com/microsoft/qlib/tree/main/scripts/data_collector#description-of-dataset)

*Please pay **ATTENTION** that the data is collected from [Yahoo Finance](https://finance.yahoo.com/lookup), and the data might not be perfect.
We recommend users to prepare their own data if they have a high-quality dataset. For more information, users can refer to the [related document](https://qlib.readthedocs.io/en/latest/component/data.html#converting-csv-format-into-qlib-format)*.

*请**注意**，数据是从 [Yahoo Finance](https://finance.yahoo.com/lookup) 收集的，数据可能不完美。
如果您有高质量的数据集，我们建议用户准备自己的数据。有关更多信息，用户可以参考[相关文档](https://qlib.readthedocs.io/en/latest/component/data.html#converting-csv-format-into-qlib-format)*。

## Auto Quant Research Workflow

## 自动量化研究工作流

Qlib provides a tool named `qrun` to run the whole workflow automatically (including building dataset, training models, backtest and evaluation). You can start an auto quant research workflow and have a graphical reports analysis according to the following steps:

Qlib 提供了一个名为 `qrun` 的工具来自动运行整个工作流（包括构建数据集、训练模型、回测和评估）。您可以按照以下步骤启动自动量化研究工作流并进行图形化报告分析：

1. Quant Research Workflow: Run  `qrun` with lightgbm workflow config ([workflow_config_lightgbm_Alpha158.yaml](examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml) as following.

1. 量化研究工作流：使用 lightgbm 工作流配置运行 `qrun`（[workflow_config_lightgbm_Alpha158.yaml](examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml)），如下所示。

    ```bash
      cd examples  # Avoid running program under the directory contains `qlib`
      qrun benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
    ```

    If users want to use `qrun` under debug mode, please use the following command:

    如果用户想在调试模式下使用 `qrun`，请使用以下命令：

    ```bash
    python -m pdb qlib/cli/run.py examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml
    ```

    The result of `qrun` is as follows, please refer to [docs](https://qlib.readthedocs.io/en/latest/component/strategy.html#result) for more explanations about the result.

    `qrun` 的结果如下，有关结果的更多解释，请参阅[文档](https://qlib.readthedocs.io/en/latest/component/strategy.html#result)。

    Here are detailed documents for `qrun` and [workflow](https://qlib.readthedocs.io/en/latest/component/workflow.html).

    这里是 `qrun` 和 [工作流](https://qlib.readthedocs.io/en/latest/component/workflow.html) 的详细文档。

2. Graphical Reports Analysis: First, run `python -m pip install .[analysis]` to install the required dependencies. Then run `examples/workflow_by_code.ipynb` with `jupyter notebook` to get graphical reports.

2. 图形化报告分析：首先，运行 `python -m pip install .[analysis]` 安装所需的依赖项。然后使用 `jupyter notebook` 运行 `examples/workflow_by_code.ipynb` 以获取图形化报告。

## Building Customized Quant Research Workflow by Code

## 通过代码构建自定义量化研究工作流

The automatic workflow may not suit the research workflow of all Quant researchers. To support a flexible Quant research workflow, Qlib also provides a modularized interface to allow researchers to build their own workflow by code. [Here](examples/workflow_by_code.ipynb) is a demo for customized Quant research workflow by code.

自动工作流可能不适合所有量化研究人员的研究工作流。为了支持灵活的量化研究工作流，Qlib 还提供了模块化接口，允许研究人员通过代码构建自己的工作流。[这里](examples/workflow_by_code.ipynb)是通过代码自定义量化研究工作流的演示。

# Main Challenges & Solutions in Quant Research

# 量化研究中的主要挑战与解决方案

Quant investment is a very unique scenario with lots of key challenges to be solved.
Currently, Qlib provides some solutions for several of them.

量化投资是一个非常独特的场景，有许多关键挑战需要解决。
目前，Qlib 为其中几个提供了一些解决方案。

## Forecasting: Finding Valuable Signals/Patterns

## 预测：寻找有价值的信号/模式

Accurate forecasting of the stock price trend is a very important part to construct profitable portfolios.
However, huge amount of data with various formats in the financial market which make it challenging to build forecasting models.

准确预测股票价格趋势是构建盈利投资组合的非常重要的部分。
然而，金融市场中大量各种格式的数据使得构建预测模型具有挑战性。

An increasing number of SOTA Quant research works/papers, which focus on building forecasting models to mine valuable signals/patterns in complex financial data, are released in `Qlib`

越来越多的 SOTA 量化研究成果/论文在 `Qlib` 中发布，这些研究专注于构建预测模型，以在复杂的金融数据中挖掘有价值的信号/模式。

### Run a single model

### 运行单个模型

All the models listed above are runnable with ``Qlib``. Users can find the config files we provide and some details about the model through the [benchmarks](examples/benchmarks) folder. More information can be retrieved at the model files listed above.

上面列出的所有模型都可以使用 ``Qlib`` 运行。用户可以通过 [benchmarks](examples/benchmarks) 文件夹找到我们提供的配置文件和有关模型的一些详细信息。可以在上面列出的模型文件中检索更多信息。

`Qlib` provides three different ways to run a single model, users can pick the one that fits their cases best:

`Qlib` 提供三种不同的方式来运行单个模型，用户可以选择最适合其情况的一种：

- Users can use the tool `qrun` mentioned above to run a model's workflow based from a config file.
- Users can create a `workflow_by_code` python script based on the [one](examples/workflow_by_code.py) listed in the `examples` folder.
- Users can use the script [`run_all_model.py`](examples/run_all_model.py) listed in the `examples` folder to run a model.

- 用户可以使用上面提到的工具 `qrun` 基于配置文件运行模型的工作流。
- 用户可以基于 `examples` 文件夹中列出的 [示例](examples/workflow_by_code.py) 创建 `workflow_by_code` python 脚本。
- 用户可以使用 `examples` 文件夹中列出的脚本 [`run_all_model.py`](examples/run_all_model.py) 运行模型。

## [Adapting to Market Dynamics](examples/benchmarks_dynamic)

## [适应市场动态](examples/benchmarks_dynamic)

Due to the non-stationary nature of the environment of the financial market, the data distribution may change in different periods, which makes the performance of models build on training data decays in the future test data.
So adapting the forecasting models/strategies to market dynamics is very important to the model/strategies' performance.

由于金融市场环境的非平稳性，数据分布在不同时期可能会发生变化，这使得基于训练数据构建的模型在未来测试数据上的性能下降。
因此，使预测模型/策略适应市场动态对模型/策略的性能非常重要。

##  Reinforcement Learning: modeling continuous decisions

## 强化学习：建模连续决策

Qlib now supports reinforcement learning, a feature designed to model continuous investment decisions. This functionality assists investors in optimizing their trading strategies by learning from interactions with the environment to maximize some notion of cumulative reward.

Qlib 现在支持强化学习，这是一个旨在建模连续投资决策的功能。此功能通过从与环境的交互中学习来帮助投资者优化其交易策略，以最大化某种累积奖励的概念。

# Quant Dataset Zoo

# 量化数据集集合

Dataset plays a very important role in Quant. Here is a list of the datasets built on `Qlib`:

数据集在量化中扮演非常重要的角色。以下是基于 `Qlib` 构建的数据集列表：

[Here](https://qlib.readthedocs.io/en/latest/advanced/alpha.html) is a tutorial to build dataset with `Qlib`.
Your PR to build new Quant dataset is highly welcomed.

[这里](https://qlib.readthedocs.io/en/latest/advanced/alpha.html)是使用 `Qlib` 构建数据集的教程。
非常欢迎您提交 PR 构建新的量化数据集。

# Learning Framework

# 学习框架

Qlib is high customizable and a lot of its components are learnable.
The learnable components are instances of `Forecast Model` and `Trading Agent`. They are learned based on the `Learning Framework` layer and then applied to multiple scenarios in `Workflow` layer.
The learning framework leverages the `Workflow` layer as well(e.g. sharing `Information Extractor`, creating environments based on `Execution Env`).

Qlib 具有高度可定制性，其许多组件都是可学习的。
可学习组件是 `Forecast Model` 和 `Trading Agent` 的实例。它们基于 `Learning Framework` 层学习，然后应用于 `Workflow` 层中的多个场景。
学习框架也利用了 `Workflow` 层（例如共享 `Information Extractor`，基于 `Execution Env` 创建环境）。

Based on learning paradigms, they can be categorized into reinforcement learning and supervised learning.

根据学习范式，它们可以分为强化学习和监督学习。

- For supervised learning, the detailed docs can be found [here](https://qlib.readthedocs.io/en/latest/component/model.html).
- For reinforcement learning, the detailed docs can be found [here](https://qlib.readthedocs.io/en/latest/component/rl.html).

- 对于监督学习，详细文档可以在[这里](https://qlib.readthedocs.io/en/latest/component/model.html)找到。
- 对于强化学习，详细文档可以在[这里](https://qlib.readthedocs.io/en/latest/component/rl.html)找到。

# More About Qlib

# 关于 Qlib 的更多信息

If you want to have a quick glance at the most frequently used components of qlib, you can try notebooks [here](examples/tutorial/).

如果您想快速浏览 qlib 中最常用的组件，可以尝试[这里](examples/tutorial/)的 notebooks。

The detailed documents are organized in [docs](docs/).

详细文档组织在 [docs](docs/) 中。

Qlib is in active and continuing development. Our plan is in the roadmap, which is managed as a [github project](https://github.com/microsoft/qlib/projects/1).

Qlib 正在积极持续开发中。我们的计划在路线图中，作为一个 [github 项目](https://github.com/microsoft/qlib/projects/1) 管理。

# Offline Mode and Online Mode

# 离线模式和在线模式

The data server of Qlib can either deployed as `Offline` mode or `Online` mode. The default mode is offline mode.

Qlib 的数据服务器可以部署为 `Offline` 模式或 `Online` 模式。默认模式是离线模式。

Under `Offline` mode, the data will be deployed locally.

在 `Offline` 模式下，数据将部署在本地。

Under `Online` mode, the data will be deployed as a shared data service. The data and their cache will be shared by all the clients. The data retrieval performance is expected to be improved due to a higher rate of cache hits. It will consume less disk space, too.

在 `Online` 模式下，数据将部署为共享数据服务。数据及其缓存将被所有客户端共享。由于更高的缓存命中率，数据检索性能预计会得到改善。它也将消耗更少的磁盘空间。

## Performance of Qlib Data Server

## Qlib 数据服务器性能

The performance of data processing is important to data-driven methods like AI technologies. As an AI-oriented platform, Qlib provides a solution for data storage and data processing. To demonstrate the performance of Qlib data server, we compare it with several other data storage solutions.

数据处理性能对于 AI 技术等数据驱动方法很重要。作为一个面向 AI 的平台，Qlib 提供了数据存储和数据处理的解决方案。为了展示 Qlib 数据服务器的性能，我们将其与其他几个数据存储解决方案进行比较。

We evaluate the performance of several storage solutions by finishing the same task, which creates a dataset (14 features/factors) from the basic OHLCV daily data of a stock market (800 stocks each day from 2007 to 2020). The task involves data queries and processing.

我们通过完成相同的任务来评估几种存储解决方案的性能，该任务从股票市场的基本 OHLCV 日数据（2007 年至 2020 年每天 800 只股票）创建数据集（14 个特征/因子）。该任务涉及数据查询和处理。

* `+(-)E` indicates with (out) `ExpressionCache`
* `+(-)D` indicates with (out) `DatasetCache`

* `+(-)E` 表示有（无）`ExpressionCache`
* `+(-)D` 表示有（无）`DatasetCache`

Most general-purpose databases take too much time to load data. After looking into the underlying implementation, we find that data go through too many layers of interfaces and unnecessary format transformations in general-purpose database solutions.
Such overheads greatly slow down the data loading process.
Qlib data are stored in a compact format, which is efficient to be combined into arrays for scientific computation.

大多数通用数据库加载数据需要太多时间。在研究底层实现后，我们发现数据在通用数据库解决方案中经历了太多层接口和不必要的格式转换。
这些开销大大减慢了数据加载过程。
Qlib 数据以紧凑格式存储，可有效地组合成数组进行科学计算。

# Contact Us

# 联系我们

- If you have any issues, please create issue [here](https://github.com/microsoft/qlib/issues/new/choose) or send messages in [gitter](https://gitter.im/Microsoft/qlib).
- If you want to make contributions to `Qlib`, please [create pull requests](https://github.com/microsoft/qlib/compare).
- For other reasons, you are welcome to contact us by email([qlib@microsoft.com](mailto:qlib@microsoft.com)).
  - We are recruiting new members(both FTEs and interns), your resumes are welcome!

- 如果您有任何问题，请在[这里](https://github.com/microsoft/qlib/issues/new/choose)创建问题或在 [gitter](https://gitter.im/Microsoft/qlib) 中发送消息。
- 如果您想为 `Qlib` 做出贡献，请[创建拉取请求](https://github.com/microsoft/qlib/compare)。
- 出于其他原因，欢迎您通过电子邮件联系我们（[qlib@microsoft.com](mailto:qlib@microsoft.com)）。
  - 我们正在招募新成员（全职员工和实习生），欢迎您的简历！

# Contributing

# 贡献

We appreciate all contributions and thank all the contributors!

我们感谢所有贡献并感谢所有贡献者！

Before we released Qlib as an open-source project on Github in Sep 2020, Qlib is an internal project in our group. Unfortunately, the internal commit history is not kept. A lot of members in our group have also contributed a lot to Qlib.

在我们于 2020 年 9 月在 Github 上将 Qlib 作为开源项目发布之前，Qlib 是我们团队的内部项目。不幸的是，内部提交历史没有保留。我们团队的许多成员也为 Qlib 做出了很多贡献。

## Guidance

## 指导

This project welcomes contributions and suggestions.
**Here are some [code standards and development guidance](docs/developer/code_standard_and_dev_guide.rst) for submiting a pull request.**

该项目欢迎贡献和建议。
**这里是一些[代码标准和开发指导](docs/developer/code_standard_and_dev_guide.rst)，用于提交拉取请求。**

Making contributions is not a hard thing. Solving an issue(maybe just answering a question raised in [issues list](https://github.com/microsoft/qlib/issues) or [gitter](https://gitter.im/Microsoft/qlib)), fixing/issuing a bug, improving the documents and even fixing a typo are important contributions to Qlib.

做出贡献并不是一件困难的事情。解决问题（也许只是回答[问题列表](https://github.com/microsoft/qlib/issues)或 [gitter](https://gitter.im/Microsoft/qlib) 中提出的问题）、修复/提出错误、改进文档甚至修复拼写错误都是对 Qlib 的重要贡献。

If you don't know how to start to contribute, you can refer to the following examples.

如果您不知道如何开始贡献，可以参考以下示例。

[Good first issues](https://github.com/microsoft/qlib/labels/good%20first%20issue) are labelled to indicate that they are easy to start your contributions.

[Good first issues](https://github.com/microsoft/qlib/labels/good%20first%20issue) 被标记为表示它们很容易开始您的贡献。

You can find some impefect implementation in Qlib by  `rg 'TODO|FIXME' qlib`

您可以通过 `rg 'TODO|FIXME' qlib` 在 Qlib 中找到一些不完善的实现。

If you would like to become one of Qlib's maintainers to contribute more (e.g. help merge PR, triage issues), please contact us by email([qlib@microsoft.com](mailto:qlib@microsoft.com)).  We are glad to help to upgrade your permission.

如果您想成为 Qlib 的维护者之一以贡献更多（例如帮助合并 PR、分类问题），请通过电子邮件联系我们（[qlib@microsoft.com](mailto:qlib@microsoft.com)）。我们很乐意帮助升级您的权限。

## License

## 许可证

Most contributions require you to agree to a Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us the right to use your contribution. For details, visit https://cla.opensource.microsoft.com.

大多数贡献要求您同意贡献者许可协议（CLA），声明您有权利并且实际上确实授予我们使用您贡献的权利。有关详细信息，请访问 https://cla.opensource.microsoft.com。

When you submit a pull request, a CLA bot will automatically determine whether you need to provide a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions provided by the bot. You will only need to do this once across all repos using our CLA.

当您提交拉取请求时，CLA 机器人将自动确定您是否需要提供 CLA 并适当地装饰 PR（例如状态检查、评论）。只需按照机器人提供的说明操作即可。您只需在使用我们 CLA 的所有仓库中执行一次此操作。

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

该项目已采用 [Microsoft 开源行为准则](https://opensource.microsoft.com/codeofconduct/)。
有关更多信息，请参阅[行为准则常见问题解答](https://opensource.microsoft.com/codeofconduct/faq/)或联系 [opencode@microsoft.com](mailto:opencode@microsoft.com) 提出任何其他问题或评论。
