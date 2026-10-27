# TwinCAT 源码修改格式

本工具只处理用户明确提供的、未受保护的 XML 文本。示例 [demo-pou.xml](../examples/demo-pou.xml) 完全虚构。

| 对象 | 支持编辑区段 |
| --- | --- |
| `.TcPOU` / `POU` | 顶层 `Declaration`、`Implementation/ST` |
| POU 内的 `Method` | 指定名称的方法声明、ST 实现 |
| `.TcGVL` / `GVL` | `Declaration` |
| `.TcDUT` / `DUT` | `Declaration` |

实际 ST 文本必须位于单个 `<![CDATA[...]]>` 内。
编辑以 XML 解析器定位 CDATA 的字节边界，只替换目标正文；对象 Name/Id、方法 Id、属性、其他区段、注释和外围格式保持原样。
新正文换行统一为原文件的 CRLF 或 LF。工具返回 XML、修改前后 UTF-8 SHA256 和 unified diff，不写磁盘、不保存工程。

示例调用：

```json
{
  "name": "preview_source_edit",
  "arguments": {
    "xml_text": "在这里放入自己未受保护的完整 XML 文本",
    "section": "implementation",
    "replacement": "counter := counter + 2;\n",
    "method": null
  }
}
```

`method` 为 null 时选择顶层，指定名字时只选同名方法。每次只能改一个区段。
先用 `inspect_source` 看可编辑区段；不存在、同名歧义、多个 CDATA 拼接或非 ST 实现会拒绝修改。

## 保存与验证

1. 备份自己的原文件，保留原始编码和 BOM 信息。
2. 该接口接受 UTF-8 XML 声明或无编码声明的 Unicode 文本。其他编码声明会拒绝，不自动猜测或转换。
3. 将返回 XML 保存为独立 UTF-8 候选副本。若输入文本保留了起始 BOM 字符，输出也会保留；调用者解码时已去掉的 BOM 无法从字符串恢复。
4. 核对 diff 和对象身份，使用 TwinCAT XAE 完整编译、审查引用和类型，再按自己的项目流程应用。

XML 验证通过不代表 ST 语法、类型、继承关系、任务调用或运动逻辑正确。
此工具不新增对象、不生成对象 GUID、不修改 `.plcproj` 引用，也不修改 NC、轴配置、Boot 文件或在线程序。
Property、Action、FBD/LD/SFC 图形实现和二进制/加密受保护对象不在本版本支持范围。
受保护文件需由有权限的工程维护者在 XAE 中正常导出可读源码；本工具不解密或绕过保护。

拒绝 DTD/实体声明、过深 XML、NUL、超过 1 MiB 的文档，以及含 `]]>` 的替换正文。
这些约束避免猜测二进制格式或意外重写整个 XML。
