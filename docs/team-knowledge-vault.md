# 团队 Obsidian 知识库

团队管理员通过 `POST /api/team-knowledge/vault` 上传 Obsidian Vault ZIP。服务端只读取 UTF-8 Markdown 和 YAML 属性，忽略 `.obsidian`、隐藏文件及非 Markdown 附件。候选版本完成 Wiki 索引后才会成为当前团队版本；失败不会影响上一版。

Wiki 索引提取笔记标题、相对路径、YAML 标签、`[[双链]]`、店铺/ASIN/活动名等标识和正文。检索按精确标识、标题/路径/标签/双链、中文短语正文的顺序排序，不需要嵌入服务或 pgvector。API 容器挂载 `amazon_ops_team_knowledge` 卷保存上传归档；PostgreSQL 保存版本、文档和 Wiki 检索字段。所有已登录用户共享当前版本，但只有管理员可上传。创建对话任务时，`use_team_knowledge` 默认为 `true`；设为 `false` 时该任务不检索团队知识库。
