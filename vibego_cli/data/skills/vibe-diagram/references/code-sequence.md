# vibe-diagram reference: code-sequence

> 本文件由 TASK_20260630_022 拆分自 `vibe-diagram/SKILL.md`。只有路由命中对应图型后才读取；读取失败必须 fail-closed。


## 候选全集清单

- 生图类型：代码时序图
- 首选候选：参与者列 + 时间向下时序图
- 必生成备选候选：
  - 异步回调时序图
  - 事务边界时序图
  - 重试/异常返回时序图
- 校准期要求：当前图型命中后，HTML 内必须按候选 A/B/C/D…纵向展开首选候选与全部备选候选；每个候选都必须是真图。信息不足时也必须生成该备选候选，用“待确认节点”标明缺口。

### HTML 模板资产

本图型必须优先复制 HTML 模板骨架，再替换槽位；禁止从零自由绘制整体布局。模板目录：`templates/code-sequence/`。模板只固定视觉语法、关系主轴、节点居中和响应式结构，不固定业务内容。

使用流程：

1. 先按用户真实问题选择当前图型内的主模板。
2. 从 `templates/code-sequence/` 复制最匹配的 HTML 文件。
3. 只替换 `data-slot` 槽位内容、标题、摘要和必要节点文本。
4. 保留 `data-diagram-type="code-sequence"`、`data-template-family="code-sequence"`、`data-template-id="..."`、`data-template-layout="..."`、主画布结构、节点居中 CSS 和响应式 CSS。
5. 如果模板槽位不够，先合并/隐藏/复制同类槽位；仍不够时才新建局部子结构，不能破坏主骨架。
6. 同一图型内的模板不能共享同一宏观骨架或同一 DOM 骨架；`data-template-layout` 必须体现该模板的版式语义，主画布结构要能在去掉文案和槽位名后仍可区分，避免退化成同一种 grid/card 预览。

模板清单：

| 模板文件 | data-template-layout | 用途 |
| --- | --- | --- |
| `participant-timeline.html` | `participant-timeline` | 参与者列 + 时间向下时序图 |
| `async-callback-sequence.html` | `async-callback-sequence` | 异步回调时序图 |
| `transaction-boundary-sequence.html` | `transaction-boundary-sequence` | 事务边界时序图 |
| `retry-exception-sequence.html` | `retry-exception-sequence` | 重试/异常返回时序图 |

## 代码时序图规则

- 使用“参与者列 + 时间自上而下”。
- 参与者建议 3 到 6 个，超过则合并为逻辑边界。
- 每行最多一个主调用；返回、异常、重试、异步回调分行表达。
- 每个步骤绑定源码证据：文件路径、函数名、调用点、输入、输出、异常；无证据写“待确认”。
- 事务边界用半透明区块标注 begin/commit/rollback。
