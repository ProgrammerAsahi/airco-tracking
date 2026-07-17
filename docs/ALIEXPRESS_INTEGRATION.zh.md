# AliExpress 推广联盟 API 集成

**中文** | [English](ALIEXPRESS_INTEGRATION.md)

## 当前状态

Airco Tracker 已为获批的 AliExpress Affiliate Standard API 与 SKU Dimension API 实现最小化签名客户端。法国与荷兰共用协议、校验、发现缓存、商品过滤和 SKU 解析代码，但保留两个独立适配器，因为是否可配送必须按目的国家判断。

这些适配器目前刻意**没有注册为生产库存来源**。获批的 `aliexpress.affiliate.product.sku.detail.get` 响应文档包含商品身份、本地化标题、价格与税、运费、发货国、预计交期和 SKU 属性，但没有文档化的库存量、availability 或 orderability。返回一个 SKU 可以证明目录和物流信息存在，却不足以证明现在有现货。

## 只读接口

- `aliexpress.affiliate.product.query` 低频发现便携式空调候选。
- `aliexpress.affiliate.product.sku.detail.get` 按一个商品、一个配送国家检查最多 20 个 SKU。
- 不使用 buyer、order、payment、卖家管理或其它个人数据接口。

所有请求固定 gateway 和方法白名单，采用 HMAC-SHA256 签名，限制请求及响应大小，并确保日志不包含凭据和业务参数。HTTP 重试只覆盖临时网络失败和 `429`、`502`、`503`、`504`。

## 库存安全边界

以下信息单独出现时绝不能解释为即时现货：

- 返回了 SKU；
- 存在正数价格、税或折扣；
- 存在运费或发货国；
- 存在最短、最长或预计配送天数；
- 存在商品链接或推广链接。

任何看起来像库存但未写入接口文档的字段都会被忽略。只有当某一个具体字段经过独立核验，并由适配器明确配置后，共用代码才允许读取这个白名单字段；未配置时，相关商品保持 `unknown`，站点进入 stale，不能变成有货或售罄。“没有查询结果”在线上既可能直接返回 `405`，也可能返回 `code=15, sub_code=405`；两种情况都属于未知，而不是售罄。

省略 `sku_ids` 时，接口最多返回 20 个 SKU。因此，即便恰好返回 20 个且都明确不可用，商品整体仍必须保持 `unknown`：响应无法证明不存在可下单的第 21 个变体。只要有一个变体明确可下单即可证明商品有货；如果接口返回超过 20 行，则视为违反文档契约并拒绝处理。

## 国家与商品规则

法国请求固定使用 `ship_to_country=FR`、`target_currency=EUR`、`target_language=FR`；荷兰使用 `NL`、`EUR`、`NL`。结果不会标记成 `eu`，因为同一 AliExpress 卖家可能允许配送一个国家而不允许另一个国家。

候选和 SKU 过滤只接受压缩机式便携/移动空调和 PortaSplit 类便携分体机；排除蒸发式冷风机、风扇、USB/桌面迷你冷风机、软管、窗封、遥控器、滤网、保护套、备件、固定壁挂 split、窗机和顶置空调。价格必须是安全范围内的正数 EUR 消费者价格。预售文字单独保留，并且绝不能触发即时现货提醒。

## Secret 与运行时安全

生产环境通过 Managed Identity 注入：

```text
ALIEXPRESS_APP_KEY    <- Key Vault: aliexpress-app-key
ALIEXPRESS_APP_SECRET <- Key Vault: aliexpress-app-secret
```

`ALIEXPRESS_TRACKING_ID` 是可选项，在获得真实 tracking ID 前不会存入 Key Vault。凭据不会进入源代码、镜像、Bicep 参数、Service Bus 消息或日志。凭据缺失时只让 AliExpress 适配器失败，scanner 构造和其它零售商仍会继续。

发现结果按配送国家缓存 12 小时；若未来满足启用条件，SKU 详情才会在适配器运行时刷新。当接口提供完整分页元数据时，只有分页信息和累计原始行数始终一致，快照才允许写入缓存。生产 Standard API 目前会省略 `total_page_no`，而请求看似有效的下一页还可能返回 `405`；这种响应只允许作为明确截断的单页诊断窗口，绝不能用来启用未来的库存字段。页数、候选数、流式响应大小和单次调用超时均有边界，并在每次调用前检查保守的单国家预算。缓存非法、商品 ID 或商品 URL 不匹配、URL host 异常、非 EUR 价格、响应格式错误或预算耗尽时全部 fail closed。库存身份 URL 固定由已经校验的 product ID 构造，因此 locale host 或 tracking query 变化不会制造虚假的库存切换。

## 生产契约探测（2026 年 7 月 17 日）

已使用生产 app 凭据调用官方 gateway；全程没有记录 Secret、签名或完整请求。脱敏结果如下：

- Standard 商品查询线上响应省略 `total_page_no`；广告式总数只能视为参考值，请求下一页可能直接得到 `405`。
- 真实 SKU 数组位于 `ae_item_sku_info.traffic_sku_info_list`；代码同时保留对文档中直接数组结构的兼容。
- 同一个示例商品对法国返回了一个可配送 SKU，对荷兰则返回 `code=15, sub_code=405`，证明配送证据必须严格按国家保存。
- 真实 SKU 字段只有身份、价格/税/折扣、运费、发货国、配送时段、图片、EAN 和属性，没有库存量、availability 或 orderability。
- AliExpress 搜索是模糊匹配：`Midea PortaSplit` 返回了目录行，但采样页没有商品通过严格的便携空调过滤。
- 连续研究请求触发了 `ApiCallLimit`。候选发现必须保持低频并使用缓存，不能跟随每十分钟一次的 scanner 周期重复运行。

这些线上差异已经加入回归测试，但仍不满足库存证据要求，因此适配器继续保持未注册状态。

## 生产启用清单

1. ~~使用生产 app 分别执行法国和荷兰只读查询。~~ 已于 2026 年 7 月 17 日完成。
2. ~~检查真实 SKU 响应字段名，不记录可能敏感的值。~~ 已完成；没有发现库存字段。
3. 按配送国家在结账页交叉核对返回/未返回 SKU，并覆盖售罄和预售样本。
4. 获取官方文档或可重复证据，确认一个无歧义的 SKU 可下单/库存字段。
5. 只配置该字段，为 true、false、缺失、非法和矛盾场景补齐回归 fixture，其它库存形字段继续忽略。
6. 分别注册法国与荷兰适配器，配送范围严格写成 `{fr}` 和 `{nl}`。
7. 首次部署时关闭 first-seen alert，建立生产基线并确认没有错误 outbox 事件，再恢复正常提醒。

如果第 4 步无法满足，客户端和检查适配器仍可用于研究，但 AliExpress 不能出现在实时库存页面。
