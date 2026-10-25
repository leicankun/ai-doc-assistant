# Atlas 1.8 部署指南（旧版）

## 第 1 节

Atlas 1.8 的生产部署仅支持 Ubuntu 20.04，不支持 Windows。启动命令为 atlas-server --config /etc/atlas/v1.yaml。监听端口为 8100，健康检查路径为 /health。

## 第 2 节

Atlas 1.8 的启动顺序是先启动 PostgreSQL，再启动 Redis，最后启动应用。数据库必须为 PostgreSQL 13，Redis 必须为 6.2。健康检查成功前不得切换流量。

## 第 3 节

Atlas 1.8 使用静态 API Token，没有 OIDC。Token 通过环境变量 ATLAS_API_TOKEN 设置。修改配置后必须重启，旧版没有热加载。

## 第 4 节

Atlas 1.8 的备份每 24 小时执行一次，保留 7 天，只备份数据库。附件应由运维另行复制。该规则不得套用于 2.4 版本。

## 第 5 节

旧版目录 /var/lib/atlas-v1 保存运行数据。应用日志位于 /var/log/atlas-v1/app.log。升级前需要保存配置文件、数据库和附件目录的独立副本。

## 第 6 节

1.8 的维护窗口需要完全停止写请求，不支持滚动升级。两台机器不能同时以写入角色连接同一份数据库。旧版没有新增节点后自动分配任务的功能。
