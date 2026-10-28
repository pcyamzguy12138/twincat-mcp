# twincat-mcp

TwinCAT ADS 只读诊断、离线源码编辑预览与 Scope 候选生成的本地 MCP 服务。

当前开发版正在补齐工程、Scope 和只读诊断能力；进度及剩余验收见 [ROADMAP](docs/ROADMAP.md)。
新增工程/TMC 检索、多区段修改预览及完整 Scope 生成，尚未宣称整个扩展已完成。

Read-only ADS diagnostics and offline TwinCAT source-edit previews through MCP stdio.
Independent community tooling; not an official Beckhoff product.

## 安装和演示

需要 Python 3.10+。演示和离线 XML 工具不需要 TwinCAT；真实 ADS 采集仅支持 Windows。

```powershell
git clone https://github.com/pcyamzguy12138/twincat-mcp.git
cd twincat-mcp
python -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\twincat-mcp.exe --demo
```

最后一条命令启动 stdio MCP 服务，等待客户端 JSON-RPC 输入，不是交互式命令行。
MCP 客户端配置参考 [examples/mcp-client.example.json](examples/mcp-client.example.json)：
把 `command` 改为本机 `.venv/Scripts/twincat-mcp.exe` 的绝对路径。
演示值是固定合成数据，结果明确标记 `demo: true`，不会加载 ADS DLL。

## MCP 工具

| 工具 | 功能 |
| --- | --- |
| `describe_configuration` | 查看模式、允许读取的符号和限制，不连接 PLC |
| `read_symbols` | 一次读取 1..128 个配置白名单符号 |
| `inspect_source` | 检查用户提供的 TwinCAT XML 文本，列出可编辑 ST 区段 |
| `preview_source_edit` | 替换一个 ST 声明或正文区段，返回新 XML 和统一差异，不写任何文件 |
| `inspect_scope` | 查看 Scope XML 中的采集符号和类型 |
| `create_scope_candidate` | 从使用者的 Scope 模板克隆新增采集项，返回候选 XML 和精确差异 |
| `preview_source_batch` | 带源哈希检查的多个 ST 区段修改预览 |
| `inspect_tmc` / `inspect_tmc_type` | 导出符号浏览与结构、数组、枚举等类型查询 |
| `generate_scope_project` | 通过本机 Scope API 生成带图表及绑定的完整 YT 工程 |
| `plan_scope_from_tmc` | 把明确选择的 TMC 叶变量转换为 Scope 信号清单 |

指定 `--project-root <只读工程目录>` 后，还可使用 `inspect_project`、
`read_project_source`、`search_project`、`inspect_project_references`、
`preview_project_edits`、`browse_project_symbols` 和 `inspect_project_type`。
路径范围、搜索证据和格式限制见 [工程工具](docs/engineering.md)。

源码工具的输入是文本，不接受磁盘路径。支持范围、编码保存规则和受保护对象边界见 [源码格式说明](docs/source-format.md)。
增补 Scope 使用同类型采集模板；完整新建使用安装的厂商 API 并验证通道绑定。
两种路径均不编造厂商格式。见 [Scope 生成说明](docs/scope-generation.md)。

## 真实只读采集（需自行配置）

1. 在工程电脑上自行安装 Beckhoff TwinCAT ADS 运行环境，配置并核实已有路由。
2. 将 `examples/target.example.json` 复制为 `target.local.json`。
3. 填写自己的 AMS Net ID、PLC ADS 端口、读取超时、允许读取的完整符号名。
4. 明确启用 `enabled: true`。默认示例禁用，全部零的占位 Net ID 不允许启用。
5. 使用 `twincat-mcp --config <target.local.json绝对路径>` 启动。

配置在启动时读取并固定，工具调用不能修改目标、路由或白名单。符号匹配区分大小写。
使用本机 Windows PowerShell 和已安装的 `TcAdsDll.dll`；不附带厂商 DLL，不绕过 PowerShell 执行策略。
单次采集总等待上限 60 秒，同一进程不并发采集；超时终止桥接进程。

读取内核只暴露状态、符号版本、名称元数据查询和 SUMUP_READ。
`AdsSyncReadWriteReqEx2` 仅用于 `0xF009` 名称查询及 `0xF080` 批量读，发送的是符号名称/地址/长度，不是写 PLC 数值。
没有变量写入、写控制状态、PLC 方法调用、下载、Activate Configuration、重启或添加路由接口。

缺失变量、未知结构、通讯错误和版本变化会显式标错；符号版本变化时整批值作废。
读数不是同一 PLC 扫描周期的原子快照，不能替代 Scope 时序分析，也不证明磁盘源码与在线版本一致。
连接任何 MCP 客户端前，应确认该客户端是否会把返回的符号和数值发送给外部模型；本服务本身不额外上传或持久化采集结果。

## 测试

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell -NoProfile -File tests/Test-Offline.ps1
```

Python 测试覆盖配置边界、禁止未授权符号、合成模式、源码修改及真实 MCP stdio 客户端往返。
C# 测试通过模拟传输覆盖断线、缺失变量、版本变化、截断响应、类型解码及只读 API 检查。
所有测试不加载 ADS DLL、不连接 PLC。真实硬件、新封装的 PowerShell/ADS 联通和 TwinCAT 编译仍需使用者在自己的环境验证。

## 来源与公开内容

C# ADS 内核提取自作者既有只读采集工具，公开版统一通用命名空间。
MCP 服务、操作员白名单、合成演示及文本源码修改预览是本次新增包装。
公开仓库不含客户 PLC/HMI/NC/Boot 工程、真实变量清单、采集结果、现场截图、设备路由、凭据或原工作区历史。
配置、日志和输出目录已列入 `.gitignore`；提交前仍应人工检查内容。

使用官方 [MCP Python SDK v1 维护分支](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)，依赖明确限制为 `mcp>=1.28,<2`。
协议采用 [MCP stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)，不开放网络监听端口。

MIT License，见 [LICENSE](LICENSE)。Beckhoff / TwinCAT 商标归其所有者。
