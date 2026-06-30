# vibe-diagram reference: code-sequence

> 本文件由 TASK_20260630_022 拆分自 `vibe-diagram/SKILL.md`。只有路由命中对应图型后才读取；读取失败必须 fail-closed。

## 代码时序图规则

- 使用“参与者列 + 时间自上而下”。
- 参与者建议 3 到 6 个，超过则合并为逻辑边界。
- 每行最多一个主调用；返回、异常、重试、异步回调分行表达。
- 每个步骤绑定源码证据：文件路径、函数名、调用点、输入、输出、异常；无证据写“待确认”。
- 事务边界用半透明区块标注 begin/commit/rollback。
