# Aster 4.2 Windows 开发环境

## 启动命令

Aster 4.2 Windows 开发启动命令为 aster.exe serve --config .\aster.yaml，数据目录为 .\workspace。该模式只供本地开发。

## 热加载例外

Aster 4.2 Windows 开发模式不支持热加载，修改 log.level 也必须重启；Linux 生产环境的日志热加载规则不能套用到 Windows。

## 目录规则

Aster 4.2 Windows 开发索引目录必须位于本地磁盘，不使用网络共享盘。修改目录前先停止进程并保留原目录副本。

## 开发上传

Aster 4.2 Windows 开发环境单个文件上限为 12 MB；该限制独立于标准版或企业版的知识库文档数量上限。

## 开发连接

Aster 4.2 Windows 开发模式默认连接本机 5433 端口的 PostgreSQL，不能据此修改生产数据库地址。

## 文件锁处理

Aster 4.2 Windows 删除索引目录遇到文件占用，应先关闭应用和相关调试进程，再检查占用句柄；不要强行清空仍在写入的目录。

## 路径编码

Aster 4.2 Windows 配置文件使用 UTF-8 编码，含空格的命令行路径需要加引号。应用显示名不用于拼接存储路径。

## 测试账号

Aster 4.2 Windows 开发账号不能访问生产知识库；复制测试数据前移除真实密钥和用户正文。

## 开发监控

Aster 4.2 Windows 开发指标仅用于定位本机错误，不作为生产吞吐能力或可用率验证。

## 本地恢复

Aster 4.2 Windows 开发恢复先还原原文件，再重新分块入库，不混用其他向量模型产生的历史索引。

