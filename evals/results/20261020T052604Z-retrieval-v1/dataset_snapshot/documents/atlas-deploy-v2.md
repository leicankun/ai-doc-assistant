# Atlas 2.4 部署指南

## 第 1 节

Atlas 2.4 支持 Ubuntu 22.04 生产部署；Windows 11 仅供本地开发，不属于生产支持范围。Linux 启动命令为 atlas serve --config /etc/atlas/atlas.yaml。Windows 开发命令为 atlas.exe serve --config .\atlas.yaml。

## 第 2 节

Atlas 2.4 的默认监听端口是 8200，存活检查路径是 /live，就绪检查路径是 /ready。/live 成功只代表进程存活，接入业务流量必须以 /ready 成功为准。

## 第 3 节

Atlas 2.4 的启动顺序是 PostgreSQL 15、Redis 7.2、Atlas 应用。就绪检查同时验证数据库连接、Redis 连接和本机索引加载，任何一项失败均返回 503。

## 第 4 节

应用运行账号必须为 atlas 服务账号，不应使用 root。Linux 数据目录 /srv/atlas/data，日志目录 /var/log/atlas；Windows 开发数据目录为 .\data，日志目录为 .\logs。

## 第 5 节

启用反向代理时需要保留 X-Request-ID 请求头。代理应转发到本机 8200 端口，TLS 在代理终止。反向代理的健康检查必须请求 /ready，而不是旧版的 /health。

## 第 6 节

生产环境先检查数据库、缓存和数据目录权限，再启动应用，然后核对 /ready，最后逐步引入流量。首次启动的索引加载耗时与数据规模有关，本指南没有规定统一启动秒数。

## 第 7 节

Windows 开发模式使用单进程，不提供自动故障切换；开发数据不能作为生产容量验证的依据。开发时修改配置也必须遵循配置参考的热加载分类。

