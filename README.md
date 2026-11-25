# DMPresent · Group 24 海报展示网页

AIAA 3111 课程项目 Group 24 海报 *Spatiotemporal Association Patterns in NYC Taxi Trips* 的配套交互网页（英文）。
海报上放不下的内容都在这里：可点选的 taxi zone 地图、行政区流向矩阵、按小时/按天的出行曲线、
全部 5,182 条关联规则的筛选器（点开可看 2×2 列联表和每个指标的算式）、预测模型对比和数据质量检查。

- 纯静态页面：HTML + CSS + 原生 JavaScript，图表均为手写 SVG，没有第三方依赖或 CDN 请求，离线也能打开。
- 数据只包含聚合结果（约 510 KB JSON），不含任何行程级记录。
- 所有数字来自分析仓库 [dataMining](https://github.com/NanFeiDeXuMu/dataMining)（UrbanPulse v0.3.1）的同一次运行，与海报一致。

## 目录

```
index.html            页面结构与文字
assets/css/style.css  样式（与海报共用配色：墨黑 / 暖白 / 出租车黄）
assets/js/app.js      数据加载与全部图表、交互
data/*.json           由 scripts/build_data.py 生成的聚合数据
scripts/build_data.py 数据生成脚本
```

## 本地预览

页面用 `fetch` 读取 `data/`，因此需要通过 HTTP 打开，不能直接双击 `index.html`：

```powershell
python -m http.server 8000
```

然后打开 <http://localhost:8000>。

## 更新数据

数据来自 dataMining 仓库里 `materials_v2` 导出的表格和 TLC taxi zone 边界文件。在 dataMining 中先运行：

```powershell
.venv\Scripts\python.exe -X utf8 materials_v2\source\export_figures.py
```

再在本仓库运行（默认把上一级目录当作 dataMining；也可以用 `--datamining` 或环境变量 `URBANPULSE_ROOT` 指定）：

```powershell
python -X utf8 scripts/build_data.py --datamining ..\dataMining
```

脚本需要 numpy 和 pandas（dataMining 的虚拟环境里已有）。

## 部署

这是纯静态站点：没有构建步骤，发布目录就是仓库根目录。

- **GitHub Pages**：Settings → Pages → Build and deployment → Deploy from a branch，选 `main` 和 `/ (root)`。
  仓库里有 `.nojekyll`，文件会原样发布。
- **Vercel / Netlify / Cloudflare Pages**：导入本仓库，Framework 选 “Other / None”，Build command 留空，
  Output directory 填 `.`（或留空表示根目录）。

部署后可以把网址做成二维码，存为 dataMining 的 `materials_v2/assets/qr.svg`，重新运行
`build_poster.py`，二维码就会出现在海报右下角的预留位置。

## 说明

- 关联规则只表示共同出现，不表示因果；负的总金额通常对应退款或调整。
- 单月（2025 年 1 月）结果；跨月验证、taxi-zone 级规则和分层挖掘是后续工作。
- AI 使用：分析脚本、图表、本网页和文字初稿使用了 AI 辅助。
