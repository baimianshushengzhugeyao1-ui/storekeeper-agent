# 订单查找 Agent 真实模型回归记录

2026年11月4日，DeepSeek额度恢复后，使用四个固定合成开发场景完成验收。
订单源和取消接口为本地模拟；真实的是模型API和模型选择工具的过程。

## 首轮与修复

首轮3/4。唯一匹配、多个匹配、查不到订单均通过；“帮我取消订单”失败：
模型输出了一段追问文字，tool_calls为空，未调用`request_order_reference`，
因此未产生等待用户的interrupt。失败工单检查点和原报告均保留。

修复将`bind_tools`的`tool_choice`从`auto`改为`required`，要求这个执行节点
必须走工具协议，但不指定工具名或参数。模型仍在四个白名单工具中决定行动。
DeepSeek保持非思考模式；即使模型没有返回调用，代码仍拒绝将其视为成功。

仅复测失败场景1/1后，使用相同四例及更严格的计分规则回归4/4。
这些问题已经参与过定位修复，因此不是未见测试集，也不是通用任务完成率。

## 最终一轮实际轨迹

- 唯一时间匹配：`search_orders_by_time → select_order`，绑定#2036。
- 多个时间匹配：`search_orders_by_time → request_order_reference → select_order`；
  返回#2036/#2037，脚本代用户选择#2037，随后进入人工审批。
- 没有时间信息：`request_order_reference → select_order`；补充#2036后进入审批，未编造时间搜索。
- 时间内无匹配：`search_orders_by_time → request_order_reference → select_order`；
  查询结果0条，再由脚本明确补充#2036。

四例审批前写入数均为0。脚本提交批准后，各产生一次模拟取消受理回执，
`simulated=true`、`refund_status=unverified`。未操作真实店铺或验证实际退款。

## 用量与耗时口径

最终一轮15次真实模型调用，输入15520 Token、输出856 Token，合计16376 Token。
包含预检、分类和查单Agent，不含首轮、单例补测或后续页面体验；不是项目累计用量。
逐例自动API流程耗时为4.64、9.47、4.63、15.43秒，含脚本补充/审批，
不能当实际用户等待时间、生产SLA或模型选型性能基准。没有按价格估算费用。

验收不只看最终订单号，还核对工具执行、时区和时间范围、匹配数量、
候选集、用户选单记录、审批前零写入与模拟回执字段。无LLM Judge打分。
本地180项程序测试通过；公开分支不含未发布500题数据的4项检查，为176项。

## 原始记录

- 首轮：`var/order_agent_evaluation/20261104T131705918639/report.json`
- 失败项复测：`var/order_agent_evaluation/20261104T132506310952/report.json`
- 最终完整回归：`var/order_agent_evaluation/20261104T132658959797/report.json`

新的每次运行仍创建独立目录；不会用成功记录覆盖首轮失败。
