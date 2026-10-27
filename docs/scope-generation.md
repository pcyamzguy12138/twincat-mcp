# Scope 候选生成

`inspect_scope` 查看输入 `.tcscopex` XML 中的 `AdsAcquisition` 符号和类型。
`create_scope_candidate` 从已有采集项克隆，生成包含新增 DataPool 采集项的候选 XML。
该算法来自作者既有 Scope 符号添加脚本的模板克隆流程，公开版增加显式模板选择、缺失类型报告和文本接口。

## 用法

1. 在自己的 TwinCAT Scope 版本中创建工程，并为每种需要的数据类型创建至少一个可用采集项。
2. 将工程 XML 文本交给工具。支持当前 `ScopeProject/SubMember/DataPool/SubMember` 格式；其他格式会拒绝。
3. 提交 [additions 示例](../examples/scope-additions.json)：每条必须指定 `symbol`、`data_type`、`template_symbol`，可附 `comment`。
4. `template_symbol` 必须来自输入工程，并与所需符号的数据类型和目标路由匹配。工具不推测 PLC 类型或机械意义。
5. 查看返回的 `added`、`skippedExisting`、`unresolved`、`removed`，将 `xml` 另存为独立 `.tcscopex` 候选。

支持类型：BIT、INT8/UINT8、INT16/UINT16、INT32/UINT32、INT64/UINT64、REAL32、REAL64。
BOOL 对应 BIT，REAL 对应 REAL32，LREAL 对应 REAL64。枚举/结构等需依据实际在线类型核对。

生成规则：

- 只加入清单内缺失的符号；不从历史文件恢复其他变量。
- 每个新增项使用新 GUID，继承明确指定模板的其余参数。
- 强制 `SymbolBased=true`，清零 `IndexOffset`、`FileHandle`、`ServerHandle`。
- 原有采集项保持不变；重复请求不会重复新增。
- `excluded_symbols` 可列出禁止加回的符号；它只限制新增，不删除当前工程已有项。
- 缺失模板、类型不匹配或模板字段不完整时，报告 `unresolved`；仍返回其他已支持的新增项，调用者必须检查该列表。

候选通过 XML、GUID 唯一性、原采集项保持和精确新增集合检查。XML 序列化可能改变引号/空白布局，不能用原始行数代替符号集合比较。
不生成图表/轴/通道显示绑定，不启动录波、不连接 ADS，也不验证私有成员的在线可访问性。
如需在图表中显示，须在 Scope 中把 DataPool 采集项加入相应图表通道并验证。

## 文件与验收边界

MCP 工具不读取或写入磁盘，所以不会覆盖原工程。若自行应用候选，先核对返回的 `sourceSha256`（按输入文本 UTF-8 计算），确认源内容未变化，再保留时间戳备份后替换。
必须在目标版本重新打开候选、确认符号解析及采集效果；离线 XML 通过不代表这些检查已完成。

[scope-test-fixture.xml](../examples/scope-test-fixture.xml) 是纯合成的单元测试片段，**不是可直接运行的完整 Scope 工程**。
公开仓库不提供客户 Scope 模板、AMS Net ID、路由、波形或现场符号清单。
