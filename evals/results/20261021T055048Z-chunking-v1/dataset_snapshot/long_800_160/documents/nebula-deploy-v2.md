# Nebula 2.4 部署指南（不同产品）

第 1 节

Nebula 2.4 与 Atlas 是不同产品。Nebula 默认端口 9200，就绪路径 /status，支持 Windows Server 2022 生产部署，启动命令 nebula start。不能用于回答 Atlas 的配置。

第 2 节

Nebula 的数据库为 MySQL 8，缓存为 Redis 6.2。默认超时 45 秒，默认重试 5 次。Nebula 不使用 Atlas 的 database.url 配置键。

第 3 节

Nebula 每 12 小时备份一次，保留 60 天；访问令牌有效期 60 分钟。监控延迟告警阈值为 P95 超过 1200 毫秒持续 2 分钟。

第 4 节

Nebula 推荐 4 核 CPU 和 8 GB 内存，单节点最多 80 万条记录。单个上传文件不超过 16 MB。不同产品容量数字不能互相替代。

第 5 节

Nebula S17 指的是停止读服务并重建缓存，与 Atlas 的索引队列应急操作无关。执行前必须确认产品名称、版本和对应故障手册。

第 6 节

本指南与 Atlas 文档同时存放供产品辨识。用户未提及 Nebula 时，不得因为检索到同版本号就将 Nebula 的端口和数据库混入 Atlas 回答。

