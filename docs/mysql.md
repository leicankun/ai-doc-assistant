# MySQL 配置与旧数据迁移

业务数据使用 MySQL 8.0.13+、InnoDB、utf8mb4；Python 通过 SQLAlchemy 连接池和 PyMySQL 驱动访问。Chroma 向量库仍在 `chroma_data/`，上传原文件仍在 `uploads/`。生产入口只连接 MySQL，配置缺失或连接失败会报错。

## 本机项目独立实例

本项目当前使用独立实例 `127.0.0.1:13306`，数据库 `ai_doc_assistant`，账号 `ai_doc_user`。随机密码已写入本机 `.env`；账号只拥有项目库的查询、写入和建表维护权限。原有 `MySQL80` 和禅道 MySQL 服务不受影响。

实例数据、日志和管理配置位于 `.local_mysql/`，已加入 Git 忽略。该目录含管理凭据，Windows 下限制为当前用户、SYSTEM 和管理员访问；不要上传或共享。删除它会丢失独立实例的数据。旧连接配置保存在该目录的 `previous-mysql-config.json`。

启动和关闭：

```powershell
# 电脑重启后，先启动项目 MySQL，再启动后端。
.\.venv\Scripts\python.exe scripts/local_mysql.py start
.\.venv\Scripts\python.exe -m uvicorn main:app --reload

# 查看状态 / 正常关闭项目 MySQL
.\.venv\Scripts\python.exe scripts/local_mysql.py status
.\.venv\Scripts\python.exe scripts/local_mysql.py stop
```

此实例不是 Windows 自启动服务。关闭启动脚本所在终端后，MySQL 后台进程仍可运行；重启 Windows 后需重新执行 `start`。管理脚本会核对服务端数据目录，只操作项目实例。

首次创建新工作副本的实例时执行以下命令；已有实例请使用 `start`，重复 `init` 会拒绝覆盖：

```powershell
.\.venv\Scripts\python.exe scripts/local_mysql.py init --mysqld 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe' --port 13306
```

`init` 创建独立账号并更新本机 `.env`，之后再按下文迁移旧 SQLite。迁移结果见 `artifacts/mysql-migration.json`，复核结果见 `artifacts/mysql-verification.json`；SQLite 备份位于 `backups/`。

## 准备连接

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

用有管理权限的 MySQL 账号创建项目专用空库和账号，以下密码请自行替换：

```sql
CREATE DATABASE ai_doc_assistant CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER 'ai_doc_assistant'@'127.0.0.1' IDENTIFIED BY 'replace_with_your_password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX
ON ai_doc_assistant.* TO 'ai_doc_assistant'@'127.0.0.1';
```

将以下配置添加到现有 `.env`，保留已有模型配置。密码中的 `#`、空格等特殊字符请按 dotenv 语法加引号；无需进行 URL 编码。

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=ai_doc_assistant
MYSQL_PASSWORD='replace_with_your_password'
MYSQL_DATABASE=ai_doc_assistant
```

配置在进程启动时读取，修改后重启后端。数据库本身需预先创建，启动只自动创建不存在的表；`create_all` 不负责升级已有 MySQL 表结构。当前完整定义位于 `storage/schema.py`。

## 迁移 documents.db

先停止旧后端和所有写入 SQLite 的脚本，迁移完成并验证之前不要启动新后端写入目标库。保留 `documents.db`、`uploads/`、`chroma_data/` 备份。

```powershell
.\.venv\Scripts\python.exe scripts/migrate_sqlite_to_mysql.py --report artifacts/mysql-migration.json
.\.venv\Scripts\python.exe scripts/migrate_sqlite_to_mysql.py --verify-only --report artifacts/mysql-verification.json
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

迁移行为：

- 源 SQLite 以只读一致性快照读取；目标所有项目表必须为空，不清表、不覆盖、不合并。
- 保留文档、分块、会话、问答的 ID、正文、引用、处理版本和 Agent 摘要；旧 ORM 练习表 `chat_sessions`、`chat_messages` 也迁移。`sqlite_sequence` 是 SQLite 内部表，不复制。
- 各表按主键顺序计算内容 SHA-256，核对行数与正文一致后提交同一个数据事务。失败则回滚本轮写入；建表 DDL 独立执行，失败时空表可能保留。
- 再次执行迁移会拒绝非空目标；使用 `--verify-only` 核对已迁移数据。未知表或未知字段会使迁移停止，避免静默遗漏。
- 不改原文件和向量目录，保留分块 ID，因此原 Chroma 向量不必重新生成。迁移不会修复原本过期或缺失的索引。

迁移完成后只运行 MySQL 版本后端。旧 SQLite 文件作为备份，不会继续同步。若 MySQL 已新增数据，切回旧 SQLite 会遗漏这些新数据，不能直接把切换代码当成数据回滚。

## 事务与接口

`storage.database.connect()` 是独立连接入口；`get_connection()` 用于 FastAPI 依赖，SSE worker 和流式保存也使用同一入口。连接池具有断线探测和回收机制。查询采用自动提交；首次写操作及 `with connection:` 显式开启事务，正常提交、异常回滚、关闭时回滚未提交内容。读取隔离为 READ COMMITTED，避免模型生成期间持有旧快照。

返回行继续支持字段访问、位置访问和 `dict(row)`；MySQL DATETIME 对外仍返回 `YYYY-MM-DD HH:MM:SS` 字符串。长正文和工具调用 JSON 使用 LONGTEXT。重复关联或摘要首次插入只忽略唯一键冲突，不使用会掩盖字段错误的 `INSERT IGNORE`。业务关联仍由应用层维护，沿用原项目删除和重试语义。

文件系统和 Chroma 不参加 MySQL 事务，删除失败时保留业务记录供重试，已完成的外部清理不能自动回滚。

## 验证

离线回归沿用显式隔离 SQLite fixture；它们不验证 MySQL 方言。数据库专项使用真实 MySQL 检查中文与长正文、重复写入、事务、接口、SSE 保存和迁移回滚：

```powershell
$env:RERANK_ENABLED='false'
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe scripts/test_mysql_sandbox.py --mysqld 'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe'
```

专项脚本启动仅监听本机的临时 MySQL 实例，创建随机测试库，结束后关闭实例并清理临时目录，不使用现有 MySQL 服务。已有独立测试服务也可配置 `MYSQL_TEST_HOST/PORT/USER/PASSWORD`，直接运行 `test_mysql_database.py`；该测试账号需具有创建和删除测试库的权限。

SQLAlchemy 的 [MySQL 驱动说明](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)和[连接池说明](https://docs.sqlalchemy.org/en/20/core/pooling.html)；MySQL 的 [DDL 隐式提交说明](https://dev.mysql.com/doc/refman/8.0/en/implicit-commit.html)。
