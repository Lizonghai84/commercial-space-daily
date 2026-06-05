# 商业航天日报

一个按信源权重自动抓取、分类、去重并生成 Markdown 商业航天日报的 MVP。

## MVP 范围

| 模块 | 首版信源 | 处理方式 |
|---|---|---|
| 行业动态 | Spaceflight News API、SpaceNews/NASA/Spaceflight Now/NASASpaceflight/Ars RSS | 权重 + 关键词 + 时效排序 |
| 最新技术 | 同上 | 技术关键词分类 |
| 研究成果 | arXiv API、Crossref API | 最近 14 天增量 |
| 最新发射 | Launch Library 2 | 最近 36 小时 + 未来 7 天 |

去重采用标题近似匹配。DeepSeek V4 Flash 为所有最终入选内容生成中文摘要，并根据当天内容生成中文“今日速览”小结。所有信源失败都会记录在日报末尾，不会阻止其他信源生成。

## 本地生成

仅依赖 Python 3.11+ 标准库，生成日报需要通过环境变量提供 DeepSeek API key：

```bash
export DEEPSEEK_API_KEY="..."
python3 space_daily.py
```

输出文件为 `reports/YYYY-MM-DD.md`。

常用参数：

```bash
python3 space_daily.py --hours 48 --per-category 10
python3 space_daily.py --date 2026-06-05
```

## 信源权重

权重在 [`config/sources.json`](config/sources.json) 中维护。数值越高，排序优先级越高。首版遵循附件建议，优先选择稳定 API/RSS；中文公司官网、公众号、融资数据库与 CelesTrak/Space-Track 入轨核验留到后续迭代。

## 自动运行

GitHub Actions 每天北京时间 08:00 运行，也支持在 Actions 页面手动触发。工作流使用仓库 Secret `DEEPSEEK_API_KEY` 调用 `deepseek-v4-flash`，生成全中文摘要和今日小结并提交日报。API key 不写入仓库。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
