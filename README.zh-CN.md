<p align="center"><img src="docs/assets/banner.svg" width="100%" alt="IBKR Agent Harness"></p>

# IBKR Agent Harness

[English](README.md) · [简体中文](README.zh-CN.md)

面向 Interactive Brokers 的本地优先金融工具与 MCP 工作流。确定性估值、账本与人工审批。

[快速开始](#快速开始) · [项目演示](#项目演示) · [MCP 工具目录](docs/MCP_TOOLS.md) · [路线图](ROADMAP.md) · [参与贡献](CONTRIBUTING.md)

## 项目演示

无需真实券商账户或模型 API Key。打开 [GitHub Actions 演示工作流](https://github.com/RyanYang053/IBKR-Agent-harness/actions/workflows/public-demo.yml)，选择成功的运行，下载 HTML 预览文件；也可按以下命令在本地生成。演示采用合成数据，并非实际收益或完整部署证明。

## 快速开始

```bash
git clone https://github.com/RyanYang053/IBKR-Agent-harness.git
cd IBKR-Agent-harness
python -m venv .venv
```

macOS/Linux 使用 `source .venv/bin/activate` 激活环境，Windows PowerShell 使用 `.\.venv\Scripts\Activate.ps1`。

```bash
python -m pip install -e '.[dev]'
python scripts/demo.py --output harness-demo.html
```

使用浏览器打开生成的文件。

## 技术文档与限制

[工程架构](IMPLEMENTATION.md) · [工程参考](ENGINEERING.md)

公开 MCP 工具面限制私有账本访问，但不是硬件级隔离。软件不提交订单。云模型接入前应审查数据路径。

[常见问题](FAQ.md) · [更新日志](CHANGELOG.md) · [安全说明](SECURITY.md) · [许可证](LICENSE)。

## 相关项目

- [Atlas Investment OS](https://github.com/RyanYang053/50agent-invest-system)
- [IBKR Portfolio Analyzer](https://github.com/RyanYang053/ibkr-portfolio-analyzer)

这些仓库独立维护，并非已经完成端到端技术集成。仅供研究和教育用途，不构成金融建议。
