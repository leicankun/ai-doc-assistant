"""把旧业务 SQLite 只读迁移到空 MySQL 库，保留 ID，逐表校验后提交。"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DB_PATH
from storage import database
from storage.schema import metadata


def digest(rows):
    value = hashlib.sha256()
    for row in rows:
        value.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        value.update(b"\n")
    return value.hexdigest()


def migrate(source_path, *, verify_only=False):
    source_path = Path(source_path).resolve(strict=True)
    report = {"source": str(source_path), "verify_only": verify_only, "tables": {}}
    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("BEGIN")  # 单个一致性读快照；不修改源库。
        names = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        unknown = names - set(metadata.tables) - {"sqlite_sequence"}
        if unknown:
            raise ValueError("源库包含未支持的表，迁移已停止：" + ", ".join(sorted(unknown)))
        # DDL 先独立完成；MySQL 建表会隐式提交，不得混入数据迁移事务。
        database.init_db()
        with closing(database.connect()) as target, target:
            if not verify_only:
                nonempty = [name for name in metadata.tables
                            if target.execute(f"SELECT COUNT(*) FROM `{name}`").fetchone()[0]]
                if nonempty:
                    raise ValueError("目标业务表必须全部为空；不会覆盖已有数据：" + ", ".join(nonempty))
            for name, table in metadata.tables.items():
                if name not in names:
                    if verify_only and target.execute(f"SELECT COUNT(*) FROM `{name}`").fetchone()[0]:
                        raise ValueError(f"源库无 {name}，但目标表有数据")
                    report["tables"][name] = {"rows": 0, "source_missing": True}
                    continue
                columns = [r[1] for r in source.execute(f"PRAGMA table_info(`{name}`)")]
                unsupported = set(columns) - set(table.columns.keys())
                if unsupported:
                    raise ValueError(f"{name} 包含未支持字段：{sorted(unsupported)}")
                fields = ",".join(f"`{col}`" for col in columns)
                order = ",".join(f"`{c.name}`" for c in table.primary_key)
                rows = source.execute(f"SELECT {fields} FROM `{name}` ORDER BY {order}").fetchall()
                if not verify_only:
                    placeholders = ",".join("?" for _ in columns)
                    for offset in range(0, len(rows), 200):
                        target.executemany(f"INSERT INTO `{name}` ({fields}) VALUES ({placeholders})", rows[offset:offset + 200])
                actual = target.execute(f"SELECT {fields} FROM `{name}` ORDER BY {order}").fetchall()
                actual_values = [tuple(row[c] for c in columns) for row in actual]
                expected_hash, actual_hash = digest(rows), digest(actual_values)
                if len(rows) != len(actual) or expected_hash != actual_hash:
                    raise ValueError(f"{name} 内容校验失败，事务回滚")
                report["tables"][name] = {"rows": len(rows), "sha256": expected_hash, "verified": True}
    report["status"] = "verified" if verify_only else "migrated"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DB_PATH)
    parser.add_argument("--verify-only", action="store_true", help="只比对已迁入数据，不重复写入")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = migrate(args.source, verify_only=args.verify_only)
    except Exception as exc:
        # 数据库异常可能含参数/正文，不直接输出异常文本。
        if isinstance(exc, (ValueError, RuntimeError, FileNotFoundError)):
            parser.exit(1, f"迁移未完成：{exc}\n")
        parser.exit(1, f"迁移未完成（{type(exc).__name__}）；请检查 MySQL 连接、权限和表结构。源文件未修改。\n")
    result = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
