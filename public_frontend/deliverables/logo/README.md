# CASINO vector logo

从提供的 PPTX 第 1 页直接提取原始贝塞尔路径，保留分组与逐字形对象。原 PPTX 未修改。

| 文件 | 内容 |
| --- | --- |
| casino-logo.svg | 完整 logo，包括图案、CASINO 字标和英文全称；65 条路径 |
| casino-symbol.svg | 仅上方图案；18 条路径 |
| casino-wordmark.svg | 仅 CASINO 字标；7 条路径 |
| preview.png | 浅色背景预览，设计时使用 SVG 源文件 |

可导入 Figma、Illustrator、Inkscape，解除分组后编辑形状、节点和颜色。SVG 没有嵌入位图，也不依赖字体安装。文字保留为轮廓，可以修改字形和颜色；替换文案需要重新输入文字，或使用原 PPT 第 2 页中的可编辑全称文本框。

画布透明；扑克牌底面与骰子接缝保留原稿米白色。源稿颜色：墨绿 `#00543C`、金色 `#C69A2B`、深灰绿 `#202A26`、米白 `#FFFEF7`。各图形与字母组沿用源文件名称；名称中的历史颜色描述不决定颜色，实际 SVG fill 与 PPT 的实际填充一致。

重复导出：

```sh
python3 scripts/export-logo.py '/absolute/path/to/CASINO_logo_Macau_editable(1).pptx'
```

脚本只支持本源稿的路径结构；遇到未支持的形状或变换会停止。网站仍使用原有 logo 文件，本次未替换线上素材。
