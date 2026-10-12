# 系统示意图复现

两张图使用可独立编辑的形状、连线和文字，图源为未压缩的单页drawio XML。PNG与PDF由同一图源生成，使用真正的Times New Roman，PNG为300 dpi，PDF保留矢量并嵌入字体。

- `tt-into-architecture.drawio`：固定业务路径、逐包门控、报告采集、共同缓存、编排和计费接口。
- `tt-into-decision-flow.drawio`：报告观察、决策截止、预测搜索、保护规则、逐字段更新和付费计数。

在仓库根目录执行，输出目录选择尚无同名图片的位置：

```bash
python3 experiments/render_drawio.py \
  --source figures/design/tt-into-01/tt-into-architecture.drawio \
  --output /tmp/tt-into-design-preview
python3 experiments/render_drawio.py \
  --source figures/design/tt-into-01/tt-into-decision-flow.drawio \
  --output /tmp/tt-into-design-preview
```

图源可在draw.io中打开修改。当前导出器支持顶层矩形、圆角矩形、菱形和显式折点连线，文字使用普通文本和换行。修改后保存未压缩XML，再重新导出两种格式。图形分组、富文本或新形状需要先扩展导出器。每张图的manifest保存图源、脚本、字体和输出摘要，fonts文件保存PDF字体核对结果。
