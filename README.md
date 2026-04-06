# Shopify -> Meta Catalog Exporter

一个最小可运行的 Python 应用，用来把 Shopify 商品批量导出成 Meta / Facebook Catalog 可消费的 CSV，并尽量把变体图片对应正确。

这个仓库现在也带了 Shopify CLI 所需的最小配置，可以直接用 `shopify app dev` 拉起本地开发预览，并以嵌入式页面的方式显示在 Shopify Admin 里。

## 当前实现

- 后端：`FastAPI`
- Shopify 数据源：`Admin GraphQL API`
- 嵌入方式：`Shopify App Bridge + Shopify Admin iframe`
- 导出模式：
  - `bulk`：默认，适合大批量商品
  - `direct`：直接分页，适合小目录或调试
- 图片策略：
  - 优先使用变体自己的图片
  - 如果变体没有图片，回退到商品级图片
- 价格策略：
  - 如果 `compareAtPrice > price`，则导出为
    - `price = compareAtPrice`
    - `sale_price = price`
  - 否则只写 `price`

## 嵌入式后台页

当前首页已经按嵌入式应用方式运行：

- Shopify CLI 会把应用挂进 Shopify Admin 预览
- 前端通过 App Bridge 请求 session token
- 后端会校验 session token，再执行导出接口
- 页面会自动识别当前 shop，不需要商家手填 shop 域名
- 当前导出接口下载按钮也改成了鉴权 fetch，不再裸露下载链接

## CSV 字段

当前导出这些列：

- `id`
- `item_group_id`
- `title`
- `description`
- `availability`
- `condition`
- `price`
- `sale_price`
- `link`
- `image_link`
- `additional_image_link`
- `brand`
- `google_product_category`
- `product_type`
- `color`
- `size`
- `material`
- `pattern`
- `gtin`
- `mpn`

## 认证方式

这个版本为了尽快可用，支持两种接法：

1. 嵌入式运行时，优先使用 Shopify app 的 `client_id + client_secret`
2. 服务端会按 shop 去换取短期 Admin API access token
3. 如需脱离嵌入环境调试，仍然可以传 `access_token`

如果都没填，会尝试读取环境变量。

## 运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

然后打开 [http://127.0.0.1:9237](http://127.0.0.1:9237)

## 用 Shopify CLI 运行

先安装 Python 依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

然后直接启动：

```bash
shopify app dev
```

当前 `shopify.web.toml` 会调用项目内的 `./.venv/bin/uvicorn`，所以第一次运行前需要先准备好虚拟环境。
默认本地端口已设置为 `9237`，不会占用 `8000`。

启动后直接从 Shopify Admin 里的 app preview 打开即可。

## 部署到 Render

这个项目已经补了 [`Dockerfile`](/Users/Zhuanz/code/python/shopify_facebook_catelog/Dockerfile) 和 [`render.yaml`](/Users/Zhuanz/code/python/shopify_facebook_catelog/render.yaml)，可以直接按 Render 的 Web Service 方式上线。

### 1. 推到 GitHub

把这个仓库推到你的 GitHub。

### 2. 在 Render 创建 Web Service

- 连接 GitHub 仓库
- 选择 `Web Service`
- Render 会自动识别 `render.yaml`

### 3. 在 Render 配环境变量

至少填这几个：

- `SHOPIFY_API_KEY`
- `SHOPIFY_API_SECRET`
- `SHOPIFY_API_VERSION=2026-04`
- `SHOPIFY_DEFAULT_PRODUCT_QUERY=status:active`

### 4. 拿到公网 URL

部署完成后你会得到一个类似：

- `https://your-service.onrender.com`

### 5. 更新 Shopify 应用地址

把 [`shopify.app.toml`](/Users/Zhuanz/code/python/shopify_facebook_catelog/shopify.app.toml) 里的：

- `application_url`

改成你的 Render 公网地址，然后执行：

```bash
shopify app deploy
```

注意：

- `shopify app deploy` 只会把 Shopify 的应用配置发布上去
- 它不会替你部署你自己的 Python 服务

### 6. 验证

部署后检查：

- `https://your-service.onrender.com/healthz`
- 从 Shopify Admin 打开你的 app
- 点击导出，确认能生成 CSV

## 环境变量

参考 [`.env.example`](/Users/Zhuanz/code/python/shopify_facebook_catelog/.env.example)

## 目录结构

```text
app/
  exporters/meta_catalog.py   # CSV 字段映射和写文件
  job_store.py                # 后台导出任务状态
  main.py                     # FastAPI 入口
  shopify_client.py           # Shopify GraphQL / bulk operation 客户端
  domain.py                   # 领域模型
  templates/index.html        # 简单导出页面
  static/styles.css           # 页面样式
tests/
  test_meta_catalog.py
```

## 已知边界

- 现在已经是嵌入式后台页，但仍然偏“单店 / merchant-managed app”形态。
- 如果你后面要上架给多个商家安装，下一步应该补：
  - 标准 OAuth 安装流
  - 持久化会话存储
  - 更完整的 App Bridge navigation / top bar / deep link
  - Webhook 签名校验与同步任务
- 当前商品属性里像 `gender`、`age_group`、`custom_label_*` 还没从 metafield 自动映射。
- 当前导出任务状态存在内存里，见 [`app/job_store.py`](/Users/Zhuanz/code/python/shopify_facebook_catelog/app/job_store.py#L37)。服务重启后任务记录会消失。
- 当前 CSV 文件写在本地磁盘 `exports/`，见 [`app/settings.py`](/Users/Zhuanz/code/python/shopify_facebook_catelog/app/settings.py#L8) 和 [`app/main.py`](/Users/Zhuanz/code/python/shopify_facebook_catelog/app/main.py#L177)。在 Render / Railway 这类平台上，这种本地文件不适合长期保存。

## 参考资料

- Shopify Admin GraphQL API product/variant/media：<https://shopify.dev/docs/api/admin-graphql/latest>
- Shopify bulk operations：<https://shopify.dev/docs/api/usage/bulk-operations/queries>
- Shopify client credentials：<https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/client-credentials>
