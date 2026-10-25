"""MySQL 连接池及事务入口，保留业务层的参数化 SQL 接口。"""
from collections.abc import Mapping
from datetime import date, datetime
from functools import lru_cache
import re
import sqlite3

import pymysql
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError

from config import database_url

DatabaseError = (pymysql.MySQLError, SQLAlchemyError, sqlite3.Error)


class Row(Mapping):
    """支持 row['id']、row[0]、dict(row)，时间字段保持 API 字符串格式。"""
    def __init__(self, columns, values):
        self._data = dict(zip(columns, (
            value.isoformat(sep=" ") if isinstance(value, datetime)
            else value.isoformat() if isinstance(value, date) else value
            for value in values
        )))
        self._values = tuple(self._data.values())

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


class Result:
    def __init__(self, rows=(), rowcount=0, lastrowid=None):
        self._rows = iter(rows)
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return next(self._rows, None)

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return self._rows


_SQL_PARTS = re.compile(r"('(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|`(?:``|[^`])*`|--[^\n]*|\#[^\n]*|/\*[\s\S]*?\*/|\?)")


def bind_sql(sql, parameters):
    """转换问号参数，跳过 SQL 字符串及注释，并转义字面量百分号。"""
    parameters = tuple(parameters)
    sql = sql.replace("%", "%%")
    count = 0

    def replace(match):
        nonlocal count
        if match.group() == "?":
            count += 1
            return "%s"
        return match.group()

    sql = _SQL_PARTS.sub(replace, sql)
    if count != len(parameters):
        raise ValueError("SQL 占位符数量与参数数量不一致")
    return sql, parameters


@lru_cache(maxsize=4)
def get_engine(url):
    engine = create_engine(
        url, pool_pre_ping=True, pool_recycle=1800,
        pool_size=5, max_overflow=10, pool_timeout=30,
        isolation_level="AUTOCOMMIT", hide_parameters=True,
        connect_args={"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def configure_session(dbapi_connection, _):
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cursor.execute("SET SESSION time_zone = '+00:00'")
            cursor.execute("SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION,NO_AUTO_VALUE_ON_ZERO'")
    return engine


class DatabaseConnection:
    """读取自动提交；首次写入或 with 块开启事务，关闭时回滚未提交写入。"""
    def __init__(self, raw):
        self._raw = raw
        self._transaction = False

    def _begin(self):
        if not self._transaction:
            self._raw.begin()
            self._transaction = True

    def execute(self, sql, parameters=None):
        if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", sql, re.I):
            self._begin()
        if parameters is not None:
            sql, parameters = bind_sql(sql, parameters)
        with self._raw.cursor() as cursor:
            cursor.execute(sql, parameters)
            columns = [c[0] for c in cursor.description] if cursor.description else []
            rows = [Row(columns, values) for values in cursor.fetchall()] if columns else []
            return Result(rows, cursor.rowcount, cursor.lastrowid)

    def executemany(self, sql, parameters):
        parameters = list(parameters)
        if not parameters:
            return Result()
        self._begin()
        bound, first = bind_sql(sql, parameters[0])
        values = [first] + [bind_sql(sql, item)[1] for item in parameters[1:]]
        with self._raw.cursor() as cursor:
            cursor.executemany(bound, values)
            return Result(rowcount=cursor.rowcount, lastrowid=cursor.lastrowid)

    def commit(self):
        self._raw.commit()
        self._transaction = False

    def rollback(self):
        self._raw.rollback()
        self._transaction = False

    def close(self):
        try:
            self.rollback()
        finally:
            self._raw.close()

    def __enter__(self):
        self._begin()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.commit() if exc_type is None else self.rollback()


def connect():
    return DatabaseConnection(get_engine(database_url()).raw_connection())


def get_connection():
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def insert_if_absent(connection, sql, parameters):
    """只忽略唯一键冲突；字段错误、数据截断、连接失败等继续抛出。"""
    try:
        return connection.execute(sql, parameters)
    except pymysql.IntegrityError as exc:
        if exc.args[0] != 1062:
            raise
        return Result()
    except sqlite3.IntegrityError as exc:
        if exc.sqlite_errorcode not in (sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, sqlite3.SQLITE_CONSTRAINT_UNIQUE):
            raise
        return Result()


def init_db():
    from storage.schema import metadata
    metadata.create_all(get_engine(database_url()))


def connect_test_db(path):
    """仅供离线测试、历史评测显式使用，不参与生产连接选择。"""
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


from storage.sqlite_test import init_test_db  # 测试/历史评测显式调用


if __name__ == "__main__":
    init_db()
    print("MySQL 表已准备好")
