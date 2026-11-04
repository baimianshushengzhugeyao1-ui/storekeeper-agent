# 官方 Shop Skill 的只读商品查询

## 业务用途

售前客户给出品类、预算和配送国家时，助手需要最新商品、售价和可售状态，
这些动态信息不能靠本地静态规则库推断。此扩展复用 Shopify 官方 Shop Skill，
让模型先加载经审核的只读说明，再通过官方 CLI 查询公开商家目录。

**这是跨商家商品发现，不是本店私有库存或订单系统。** 不把搜索结果冒充
当前演示店铺商品，不将本项目店铺规则应用于这些外部商家。真实订单、物流、
取消与退款仍需要授权店铺/账户；本扩展不执行购买和订单写入。

## 调用链

`run_tool_agent(toolset="catalog")` 使用已有 LangGraph 循环：

1. 模型选择 `load_shop_skill`，加载版本固定的官方 Skill 只读部分及项目适配说明。
2. 模型按用户条件选择 `search_shop_catalog`，生成品类、目的国、币种与预算。
3. Python 按 Pydantic 检查参数，调用固定的官方 `shop search --arguments ...`。
4. 官方 CLI 通过 Shop MCP 获取公开商品，程序再次检查返回价格、币种和可售状态。
5. 结果作为 ToolMessage 回传，模型展示商家、选定规格、价格与完整商品链接。

Skill 是流程指导，工具是可执行函数，CLI/MCP 是实际数据访问路径。不是“只加载
一个 Markdown 就自动接上外部系统”。CLI使用受限环境和项目内独立空账户路径，
不会把 DeepSeek Key 或用户现有 Shop 账户凭证交给子进程。

官方 Skill 固定为 `@shopify/shop@1.2.0`，完整快照和 MIT 许可证保留在
`skills/shop-catalog/vendor/`。包下载到被忽略的 `var/vendor/`，不执行安装脚本。
传给模型的只有 Help/Search 等只读节选；不加载交易、登录或付款流程。
后端工具注册表也不提供这些能力，因此不能依靠模型的提示词自行解锁。

## 运行

需要项目的 Python 环境、已配置 DeepSeek 和 Node.js 24.5以上（或该CLI支持的22版本）。
若 node 不在 PATH，在本机 `.env` 设置 `STOREKEEPER_NODE` 指向 Node 可执行文件。

```sh
uv run python scripts/prepare_shop_skill.py
uv run python scripts/run_catalog_skill.py
```

也可以双击 `run_shop_skill.cmd`。运行会消耗模型额度。自定义问题示例：

```sh
uv run python scripts/run_catalog_skill.py --question "找两款寄到美国、每款不超过100美元的咖啡机，只查询不要下单。"
```

## 已完成的真实调用

2026-11-04 模型实际选择了 `load_shop_skill` 和 `search_shop_catalog`，
请求真实公开商家目录。这次返回 AeroPress Original USD39.95、Clear & Colors
USD49.95，以及一款其他商家的开箱商品。结果包含商家、变体价格、可售标记
与完整商品链接。价格与可售状态只代表当时查询结果，不保证以后不变，
不证明所有规格都有货，也不证明能在指定日期送达。

用户要求两款，模型额外给了第三款备选；因此本次证明功能调用成功，
不能包装成任务遵从率100%。单次样例也不构成任务完成率或幻觉率评测。
原始轨迹在 `var/catalog_skill/`，发布副本保留公开合成调用记录。

## 官方参考

- [Shopify官方Shop仓库与Skill](https://github.com/Shopify/shop)
- [Shop MCP无账户只读搜索](https://shop.dev/docs/quickstart)
- [搜索与商品数据](https://shop.dev/docs/find-products)

上游Skill是第三方能力，个人二开部分是审核、只读适配、模型加载/调用、参数与返回
结果校验及运行记录，不能把官方商品目录或Skill描述为本人原创。
