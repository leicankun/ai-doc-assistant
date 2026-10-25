"""管理项目独立 MySQL 实例；不操作系统已有服务，不在输出中显示密码。"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

import pymysql
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / ".local_mysql"
STATE = HOME / "instance.json"
PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX"


def private_directory():
    HOME.mkdir(exist_ok=True)
    if os.name == "nt":
        sid = subprocess.check_output([
            "powershell", "-NoProfile", "-Command",
            "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ], text=True).strip()
        subprocess.run([
            "icacls", str(HOME), "/inheritance:r", "/grant:r",
            f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        HOME.chmod(0o700)


def save_state(state):
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if os.name != "nt":
        STATE.chmod(0o600)


def read_state():
    if not STATE.exists():
        raise RuntimeError("实例尚未初始化，请先运行 init")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if Path(state["data"]).resolve() != (HOME / "data").resolve():
        raise RuntimeError("实例数据路径不属于当前项目，已停止操作")
    return state


def admin_connection(state):
    return pymysql.connect(host="127.0.0.1", port=state["port"], user="root",
                           password=state["admin_password"], connect_timeout=2, autocommit=True)


def check_identity(connection, state):
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@datadir")
        data = cursor.fetchone()[0]
    if os.path.normcase(str(Path(data).resolve())) != os.path.normcase(str(Path(state["data"]).resolve())):
        raise RuntimeError("端口上的实例不是当前项目实例，已停止操作")


def is_listening(port):
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start(state):
    if is_listening(state["port"]):
        with closing(admin_connection(state)) as connection:
            check_identity(connection, state)
        return
    args = [state["mysqld"], "--no-defaults", f"--datadir={state['data']}",
            f"--port={state['port']}", "--bind-address=127.0.0.1", "--mysqlx=0",
            f"--pid-file={(HOME / 'mysql.pid').as_posix()}",
            f"--log-error={(HOME / 'mysql-error.log').as_posix()}",
            "--innodb-buffer-pool-size=128M", "--max-allowed-packet=64M", "--skip-log-bin"]
    init_file = HOME / "init.sql"
    if init_file.exists() and not state.get("ready"):
        args.append(f"--init-file={init_file.as_posix()}")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
    deadline = time.monotonic() + 45
    while True:
        try:
            with closing(admin_connection(state)) as connection:
                check_identity(connection, state)
            break
        except pymysql.MySQLError:
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("项目 MySQL 启动未完成，请检查 .local_mysql/mysql-error.log")
            time.sleep(0.25)


def initialize(mysqld, port):
    if STATE.exists():
        raise RuntimeError("项目实例已存在，请使用 start；不会覆盖账号或数据")
    if not mysqld:
        raise RuntimeError("请通过 --mysqld 指定已安装的 mysqld 二进制")
    executable = Path(mysqld).resolve(strict=True)
    if is_listening(port):
        raise RuntimeError("所选端口已被使用，请指定其他 --port")
    private_directory()
    data = HOME / "data"
    if data.exists() and any(data.iterdir()):
        raise RuntimeError("数据目录已有内容，不执行初始化")
    state = {"mysqld":str(executable), "data":str(data), "port":port,
             "user":"ai_doc_user", "database":"ai_doc_assistant",
             "admin_password":secrets.token_urlsafe(32), "password":secrets.token_urlsafe(32), "ready":False}
    flags = {"creationflags":subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with (HOME / "initialize.log").open("wb") as output:
        result = subprocess.run([str(executable), "--no-defaults", "--initialize-insecure", f"--datadir={data}"],
                                stdout=output, stderr=subprocess.STDOUT, timeout=120, **flags)
    if result.returncode:
        raise RuntimeError("初始化失败，请检查 .local_mysql/initialize.log")
    save_state(state)
    # 所有标识符固定，密码仅由 URL-safe 随机字符生成，不包含 SQL 引号。
    sql = (
        f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{state['admin_password']}';\n"
        "CREATE DATABASE IF NOT EXISTS `ai_doc_assistant` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;\n"
        f"CREATE USER IF NOT EXISTS 'ai_doc_user'@'127.0.0.1' IDENTIFIED BY '{state['password']}';\n"
        f"GRANT {PRIVILEGES} ON `ai_doc_assistant`.* TO 'ai_doc_user'@'127.0.0.1';\n"
    )
    (HOME / "init.sql").write_text(sql, encoding="utf-8")
    start(state)
    with closing(pymysql.connect(host="127.0.0.1", port=port, user=state["user"],
                                password=state["password"], database=state["database"], charset="utf8mb4")):
        pass
    state["ready"] = True
    save_state(state)
    (HOME / "init.sql").unlink(missing_ok=True)
    # 仅备份旧 MySQL 配置，不复制其他模型密钥。
    previous = dotenv_values(ROOT / ".env")
    (HOME / "previous-mysql-config.json").write_text(json.dumps({k:v for k,v in previous.items() if k.startswith("MYSQL_")}), encoding="utf-8")
    for key, value in {"MYSQL_HOST":"127.0.0.1", "MYSQL_PORT":str(port), "MYSQL_USER":state["user"],
                       "MYSQL_PASSWORD":state["password"], "MYSQL_DATABASE":state["database"]}.items():
        set_key(ROOT / ".env", key, value)
    return state


def stop(state):
    if not is_listening(state["port"]):
        return
    with closing(admin_connection(state)) as connection:
        check_identity(connection, state)
        with connection.cursor() as cursor:
            cursor.execute("SHUTDOWN")
    deadline = time.monotonic() + 20
    # 端口会先关闭，InnoDB 随后才释放数据文件；必须等 PID 文件也被移除。
    while is_listening(state["port"]) or (HOME / "mysql.pid").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("关闭尚未完成，不会强制终止其他进程")
        time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "start", "stop", "status"])
    parser.add_argument("--mysqld", default=shutil.which("mysqld"))
    parser.add_argument("--port", type=int, default=13306)
    args = parser.parse_args()
    try:
        if args.action == "init":
            state = initialize(args.mysqld, args.port)
        else:
            state = read_state()
            if args.action == "start":
                start(state)
            elif args.action == "stop":
                stop(state)
        running = is_listening(state["port"])
        if running:
            with closing(admin_connection(state)) as connection:
                check_identity(connection, state)
        print(json.dumps({"running":running,"host":"127.0.0.1","port":state["port"],
                          "user":state["user"],"database":state["database"]},ensure_ascii=False))
    except Exception as exc:
        message = str(exc) if isinstance(exc,(RuntimeError,FileNotFoundError)) else type(exc).__name__
        parser.exit(1, f"项目 MySQL 操作未完成：{message}\n")


if __name__ == "__main__":
    main()
