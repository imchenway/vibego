# vibe-diagram reference: state-data-model

> 本文件由 TASK_20260630_022 拆分自 `vibe-diagram/SKILL.md`。只有路由命中对应图型后才读取；读取失败必须 fail-closed。


## 候选全集清单

- 生图类型：状态 / 数据模型图
- 首选候选：状态机图
- 必生成备选候选：
  - ER-lite
  - 生命周期轨道
  - 数据流图
  - 状态-事件矩阵热区
- 校准期要求：当前图型命中后，HTML 内必须按候选 A/B/C/D…纵向展开首选候选与全部备选候选；每个候选都必须是真图。信息不足时也必须生成该备选候选，用“待确认节点”标明缺口。

### HTML 模板资产

本图型必须优先复制 HTML 模板骨架，再替换槽位；禁止从零自由绘制整体布局。模板目录：`templates/state-data-model/`。模板只固定视觉语法、关系主轴、节点居中和响应式结构，不固定业务内容。

使用流程：

1. 先按用户真实问题选择当前图型内的主模板。
2. 从 `templates/state-data-model/` 复制最匹配的 HTML 文件。
3. 只替换 `data-slot` 槽位内容、标题、摘要和必要节点文本。
4. 保留 `data-diagram-type="state-data-model"`、`data-template-family="state-data-model"`、`data-template-id="..."`、`data-template-layout="..."`、主画布结构、节点居中 CSS 和响应式 CSS。
5. 如果模板槽位不够，先合并/隐藏/复制同类槽位；仍不够时才新建局部子结构，不能破坏主骨架。
6. 同一图型内的模板不能共享同一宏观骨架或同一 DOM 骨架；`data-template-layout` 必须体现该模板的版式语义，主画布结构要能在去掉文案和槽位名后仍可区分，避免退化成同一种 grid/card 预览。

模板清单：

| 模板文件 | data-template-layout | 用途 |
| --- | --- | --- |
| `state-machine.html` | `state-machine` | 状态机图 |
| `er-lite.html` | `er-lite` | ER-lite |
| `lifecycle-track.html` | `lifecycle-track` | 生命周期轨道 |
| `data-flow-model.html` | `data-flow-model` | 数据流图 |
| `state-event-matrix.html` | `state-event-matrix` | 状态-事件矩阵热区 |

## 状态 / 数据模型图规则

状态 / 数据模型图默认回答“对象有哪些状态、状态如何变化、数据如何约束流程”。

- 状态机：状态节点只写状态名、进入条件、退出条件；动作写在线上，不写进状态标题。
- 数据模型：使用 ER-lite，实体、关键字段、唯一约束、索引、外键/逻辑关联必须分清。
- 生命周期：按创建、处理中、完成、失败、归档或回滚等阶段组织，不要把所有字段塞进节点。
- 字段口径必须标注来源、单位、枚举值、是否可空、幂等键或版本号。
- 并发、一致性、补偿、重试、撤销、软删等数据风险必须作为辅助标记。
- 如果重点是“谁先做什么”，应切业务流程图；如果重点是“哪个函数调用哪个函数”，应切代码时序图。
