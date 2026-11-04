# 订单查找 Agent

这是本学习分支新增的订单定位能力，不是修改上游历史，也不是生产店铺验证。

## 解决的具体问题

原缺号分支只会要求用户补订单号。用户说“取消昨天下午的订单”时，
新模块允许模型先选按时间查单工具，查看查询结果，再决定检查详情、
请用户选择或绑定唯一订单。目标明确后接回原有查单、规则、审批、执行链路。
模型没有取消或退款工具。

## 如何体验

- `start_order_agent.cmd`：真实 DeepSeek，端口8768；使用本机`.env`，调用会计费。
- `start_order_agent_replay.cmd`：端口8769，明确标注的脚本演练，不调用模型或Shopify。
- 两种模式使用不同SQLite目录，互不混用。原8765离线演示及默认图保持原有行为。

测试客户A与固定演示时间 `2026-11-04T10:00:00+08:00`：

- `帮我取消昨天下午13点到14点的订单`：应找到唯一候选#2036。
- `帮我取消昨天下午的订单`：会找到#2036耳机与#2037鼠标，必须由用户选择。
- `帮我取消订单`：无可用时间，询问订单号。
- `帮我取消昨天上午9点到10点的订单`：无匹配项，询问订单号。
- #2038为当日订单；#2039已发货，选择后仍被原规则拒绝。另一测试客户的#9036不能查到或选择。

选择目标后只到待审批状态，批准才生成模拟取消回执。工单处理结束不代表实际退款完成。

## 函数和数据流

入口仍由`create_ticket_endpoint`接收，`classify_ticket`输出任务字段。
只为单个`cancel_order`且订单号为空的任务启用新增子图：

`resolve_order_agent → agent → tools → agent ... → 原任务校验 → 查单 → 规则 → 审批 → 执行`

`order_resolution.py`的`build_order_resolution_graph`用`model.bind_tools`
提供四个白名单工具。模型返回工具名和参数，`execute`经Pydantic校验后
调用`tools[name].invoke(args)`，以`ToolMessage`回传真实工具结果。

- `search_orders_by_time(start_time,end_time)`：按有时区的时间范围查询当前测试客户订单；区间左闭右开，最长31天，最多显示5个候选并保留完整匹配数量。
- `get_order_details(order_reference)`：读取已经发现的候选详情。
- `request_order_reference(reason)`：调用`interrupt`等待候选选择或补号；返回结构仍是`{order_reference: ...}`。
- `select_order(order_reference)`：只接受唯一查询结果或用户明确选择，绑定原取消任务，不执行取消。

子图继承父图SQLite检查点。用户补充后，同一工单/`thread_id`通过
`Command(resume={interrupt_id:{order_reference:...}})`继续。完成的搜索不重跑，
包含interrupt的工具节点会重入，因此这里不放业务写操作。
`GraphBubbleUp`必须原样抛出，不能被通用异常捕获转成工具错误。

根任务保留原意图、任务编号和原话，只更新经过工具验证的订单号。
`order_resolution`保存工具输入输出、模型调用用量与选单结果，React可展开查看。
候选列表通过原补号接口提交，API先检查候选归属，再恢复图。

## 范围和限制

这是受约束的订单发现Agent加确定性执行流程，非多Agent协同。
最多6次工具执行、8次模型判断，每轮只接受一个工具调用；无调用、
错误工具或预算耗尽均不能视作成功。异常时确定性回复并转人工，不再额外调用模型编写失败文案。
选择多个匹配中的一个不能由模型自行完成，必须让用户确认。

`FixtureOrderDirectory`是本地合成订单适配器，客户上下文由服务端绑定，
不暴露customer_id工具参数。没有真实登录/RBAC系统，不构成生产环境的身份认证。
新模式只允许显式模拟业务客户端，不会启用真实店铺写入。
时间查询不是外部Shopify订单API联调；真实模型API与真实业务数据源是两个不同层次。
仅支持取消任务的订单定位，不支持自由文本改意图、任意多任务补槽或商品搜索入口合并。
Agent子图不通过重新分类来理解补号，不绕过原业务规则及审批。

## 本轮验证

2026-11-04：20项新增自动化测试，全量173项通过；前端TypeScript、Vite构建及oxlint通过。
四个合成场景的脚本端到端演练通过，包含等待、选单、审批及模拟提交。
它们验证代码与状态协议，不是LLM准确率。

真实DeepSeek预检返回HTTP402，尚未完成该新模块的真实模型验收，
不能声称真实模型已通过上述四例。首轮错误保存在
`var/order_agent_evaluation/20261104T123944082596/report.json`，未继续重复消耗请求。
额度恢复后运行：

```powershell
.venv\Scripts\python.exe -m scripts.evaluate_order_agent
```

先前脚本演练可用：

```powershell
.venv\Scripts\python.exe -m scripts.evaluate_order_agent --replay
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

每次验收写入新的`var/order_agent_evaluation/<时间>/report.json`，保留原始结果，
不覆盖失败、不把脚本演练替换成真实模型结果。
