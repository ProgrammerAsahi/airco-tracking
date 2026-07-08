# 零售商 403 / 反爬 backlog

<p align="center">
  <a href="./RETAILER_403_BACKLOG.zh.md"><img alt="简体中文" src="https://img.shields.io/badge/docs-简体中文-d73a49"></a>
  <a href="./RETAILER_403_BACKLOG.md"><img alt="English" src="https://img.shields.io/badge/docs-English-0969da"></a>
</p>

最后更新：2026-07-06

本文档记录值得后续重新探索、但目前因为普通 tracker 请求遇到直接 `403`、captcha 或类似反爬拦截而未启用的各国零售商站点。它们应与普通 parser backlog 分开管理：只有在找到稳定、公开、生产安全的数据源，并且从 Azure Container Apps 验证成功后，才可以注册 adapter。

修改本文档时，必须同步更新中文和英语版本。

## France

### 严格 direct-403 站点

这些站点在法国扩展工作中被明确观察或记录为直接 `403` blocker。

| Site | Entry URL | Type | Why it matters | Next exploration path |
| --- | --- | --- | --- | --- |
| Leroy Merlin | <https://www.leroymerlin.fr/recherche/climatiseur%20mobile> | DIY / home improvement | 法国最高价值的家装渠道之一。 | 寻找页面内部 product/search API、sitemap category URL 或官方/公开 product feed。 |
| Darty | <https://www.darty.com/nav/recherche/climatiseur%20mobile.html> | Electronics / appliance chain | 主要家电零售商，与 Fnac 同集团生态。 | 检查浏览器 network calls 和结构化 search endpoint；单独验证 product pages。 |
| ManoMano | <https://www.manomano.fr/q/climatiseur-mobile> | DIY marketplace | 类目非常相关，尤其是便携空调和 HVAC 配件。 | 寻找公开 search API 或 marketplace feed；配件常见，过滤必须严格。 |
| Fnac | <https://www.fnac.com/SearchResult/ResultList.aspx?Search=climatiseur%20mobile> | General marketplace / Fnac-Darty group | 可在 Darty 之外增加 marketplace 覆盖。 | 研究 Fnac/Darty 共享 API；注意第三方 marketplace stock 和预售歧义。 |
| Carrefour | <https://www.carrefour.fr/s?q=climatiseur%20mobile> | Hypermarket / marketplace | 法国覆盖广，热浪季节库存有可能出现。 | 寻找 search JSON endpoint、product feed 或与门店无关的在线可用性数据。 |
| Ubaldi | <https://www.ubaldi.com/recherche/climatiseur-mobile.php> | Appliance e-commerce | 法国强家电电商。 | 探索 category/product structured data 和浏览器暴露的公开 search endpoint。 |
| Bricomarché | <https://www.bricomarche.com/recherche?text=climatiseur%20mobile> | DIY / home improvement | 区域门店网络有长尾价值。 | 找到真实 category/search endpoint，并区分在线配送和仅门店库存。 |
| Mr.Bricolage | <https://www.mr-bricolage.fr/recherche?search_query=climatiseur%20mobile> | DIY / home improvement | 长尾 DIY 零售商，季节性便携空调可能有库存。 | 研究 product/category API，以及库存是否仅 local-store。 |
| Qlima France | <https://www.qlima.fr/climatiseur-mobile/> | Brand / HVAC | Qlima 在便携空调品类高度相关。 | 寻找官方 catalog/product feed 或 stock 语义可靠的替代 storefront URL。 |

### 403 / captcha / 反爬等效 blocker

这些站点即使最初症状不一定是普通 direct `403`，也按 tracker blocked 处理。处理原则相同：先找到稳定公开数据路径，再从 Azure 验证，最后才启用。

| Site | Entry URL | Type | Observed blocker | Next exploration path |
| --- | --- | --- | --- | --- |
| BUT | <https://www.but.fr/recherche?text=climatiseur%20mobile> | Furniture / appliance chain | 实现期间归入 403/captcha/anti-bot blocked 请求。 | 检查浏览器 search API 和 product detail JSON；强力过滤冷风机/风扇。 |
| Conforama | <https://www.conforama.fr/recherche?search=climatiseur%20mobile> | Furniture / appliance chain | 实现期间归入 403/captcha/anti-bot blocked 请求。 | 寻找 category API 和 online-delivery stock 字段；商品可能混有空调和冷风机。 |
| Weldom | <https://www.weldom.fr/> | DIY / home improvement | 调研时页面可达，但搜索 routing/data 不稳定，后续归入 blocked backlog。 | 先定位真实 search/category URL，再验证 server-rendered 或公开 API product data。 |
| Rakuten France | <https://fr.shopping.rakuten.com/s/climatiseur+mobile> | Marketplace | 归入 403/captcha/anti-bot blocked；第三方 listing 误报风险高。 | 只有稳定公开 listing API 暴露 condition、seller、stock 和 presale 字段时才考虑。 |
| La Redoute | <https://www.laredoute.fr/search.aspx?searchkeyword=climatiseur%20mobile> | Marketplace / home | 归入 403/captcha/anti-bot blocked；可能混有第三方 listing。 | 寻找公开 search data 并严格 marketplace 过滤；避免模糊 seller/backorder stock。 |
| Euro Accessoires | <https://www.euro-accessoires.fr/recherche?controller=search&s=climatiseur> | Camper / accessories | 普通请求得到很小的 JavaScript/AES challenge page，而不是可用商品数据。 | 寻找不需要解 JS challenge 的官方/公开 feed 或 sitemap path。 |
| Manutan France | <https://www.manutan.fr/fr/maf/search?text=climatiseur%20mobile> | B2B / industrial | 普通请求被重定向到 Radware Bot Manager captcha 页面。 | 不绕过 captcha；只有存在公开 API/feed 文档或无反爬门槛暴露时再看。 |

### 不计入 403 的相邻法国 backlog

这些应留在 403 list 之外，方便后续采用正确策略。

| Site | Current status | Notes |
| --- | --- | --- |
| Boulanger | Azure production read timeout | Local/GitHub-hosted 请求可读页面，但 Azure Container Apps 稳定遇到 60 秒 read timeout。 |
| Brico Dépôt France | Azure production receives too-small/unusable responses | Parser code 和 tests 保留，但普通 category page 和 smartcache fragment 从 Azure 都不稳定。 |
| Cdiscount | JS shell / anti-bot / 暂无稳定 server-side product data | 值得后续探索，但不记录为普通 direct-403。 |
| E.Leclerc | SPA / anti-bot / 暂无稳定 server-side product data | 值得后续探索，但不记录为普通 direct-403。 |
| Habitat et Jardin | 测试 search page 没有稳定 product cards | 需要更好的 category/search data source，而不是反爬路径。 |
| Olimpia Splendid France | Brand/catalog source without reliable direct stock | 适合作产品参考，不适合作当前 stock-alert source。 |
| Midea France | Brand/catalog source without reliable direct stock | 适合作产品参考，不适合作当前 stock-alert source。 |
| Climshop | Fixed/window/split-heavy tested entries | 只有能可靠分离 mobile/portable stock 后才可启用。 |
| Clim Planete | Fixed/window/split-heavy tested entries | 只有能可靠分离 mobile/portable stock 后才可启用。 |
| Alternate France | Search/sitemap 当前只返回 EcoFlow WAVE 配件，不是空调 | 低价值，直到公开 sitemap/search 出现真正便携空调。 |
| Maxiburo | B2B Nuxt/SPA search without stable server-rendered product data | 低优先级；只有找到带 stock 语义的公开 product API 才重看。 |
| Bruneau | B2B Nuxt/SPA search without stable server-rendered product data | 与 Maxiburo 平台类似；对消费者热浪提醒优先级低。 |
| Seton | Search returned safety/signage/mobile-industrial false positives rather than air conditioners | 测试 catalogsearch path 并非反爬 blocked，但相关性不足。 |
| Airton | Fixed split/monobloc installation-heavy catalog | 可作品牌/catalog 参考，不符合当前 mobile/portable stock model。 |
| Espace Aubade | Search paths returned 404/500-style unstable pages | HVAC/showroom 渠道；只有稳定 category/API 和明确 direct-stock 语义时重看。 |

## 重新探索 checklist

把任何站点从本 backlog 移入 `ADAPTERS` 之前：

1. 优先使用官方/公开 API、product feed、sitemap 或 server-rendered data。
2. 不绕过 captcha 或反爬控制。
3. 同时从本地开发和 Azure Container Apps 验证数据源；仅本地成功不够。
4. 证明 immediate stock、presale/backorder、store-only stock 和 unavailable 状态可以区分。
5. 过滤 air coolers、fans、humidifiers、accessories、fixed split systems 和 quote-only listings。
6. 添加 parser tests，覆盖 stock、presale、false-positive filtering 和 markup/API drift。
7. 部署后运行生产 job，并要求 `stale_site_count == 0` 后才视为 active。
