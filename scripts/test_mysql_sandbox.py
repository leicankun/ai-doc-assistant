"""用本机 mysqld 二进制启动临时隔离实例并运行 MySQL 测试，不接触已有服务。"""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pymysql


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysqld", default=shutil.which("mysqld"))
    parser.add_argument("--migration-source", type=Path, help="额外只读验证现有 SQLite 到临时 MySQL 的完整迁移")
    args = parser.parse_args()
    if not args.mysqld:
        parser.error("需要安装 MySQL Server，并用 --mysqld 指定二进制路径")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with tempfile.TemporaryDirectory(prefix="ai-doc-mysql-test-") as folder:
        root = Path(folder)
        data = root / "data"
        log = root / "mysql.log"
        init = subprocess.run([args.mysqld, "--no-defaults", "--initialize-insecure", f"--datadir={data}"],
                              capture_output=True, **flags)
        if init.returncode:
            print(init.stderr.decode(errors="replace"), file=sys.stderr)
            return init.returncode
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        command = [args.mysqld, "--no-defaults", f"--datadir={data}", f"--port={port}",
                   "--bind-address=127.0.0.1", "--mysqlx=0", f"--log-error={log}",
                   "--innodb-buffer-pool-size=64M", "--max-allowed-packet=64M"]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
        try:
            deadline = time.monotonic() + 45
            while True:
                try:
                    connection = pymysql.connect(host="127.0.0.1", port=port, user="root", connect_timeout=1)
                    connection.close()
                    break
                except pymysql.MySQLError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(log.read_text(errors="replace") if log.exists() else "MySQL 启动失败")
                    time.sleep(0.25)
            env = {**os.environ, "MYSQL_TEST_HOST":"127.0.0.1", "MYSQL_TEST_PORT":str(port),
                   "MYSQL_TEST_USER":"root", "MYSQL_TEST_PASSWORD":""}
            if args.migration_source:
                env["MYSQL_MIGRATION_SOURCE"] = str(args.migration_source.resolve(strict=True))
            print("Running tests against isolated MySQL", flush=True)
            return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_mysql_database.py", "-v"], env=env, cwd=Path(__file__).resolve().parents[1]).returncode
        finally:
            try:
                admin = pymysql.connect(host="127.0.0.1", port=port, user="root", connect_timeout=2)
                with admin.cursor() as cursor:
                    cursor.execute("SHUTDOWN")
                admin.close()
            except pymysql.MySQLError:
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
