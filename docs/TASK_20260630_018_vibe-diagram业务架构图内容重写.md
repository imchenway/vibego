# TASK_20260630_018 vibe-diagram 业务架构图内容重写

## 用户反馈

TASK_017 的布局变乱。TASK_016 的布局可接受，但内容不满意。

## 调整

保留 TASK_016 的四层布局：参与方边界、业务能力层、业务对象关系、规则约束热区、服务结果决策轴。

内容从技术对象改成业务对象：

- AppConfig -> 客服触点
- Session/Message -> 咨询工单
- Scene/Slot -> 业务场景
- Knowledge/Tool/Rule -> 服务依据
- Handoff -> 人工接续
- Trace/Feedback/Eval -> 服务质量资产

## 验证

- HTMLParser 解析通过。
- 关键内容检查通过。
- 不含字面量 `\n`。
