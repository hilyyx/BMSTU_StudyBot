import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main():
    source = Path(os.environ["DATABASE_PATH"])
    directory = Path(os.environ["BACKUP_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    target = directory / f"bot-{now:%Y%m%dT%H%M%S%fZ}.sqlite3"
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            with closing(sqlite3.connect(target)) as backup:
                db.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    for old in directory.glob("bot-*.sqlite3"):
        if old != target and not old.is_symlink() and old.stat().st_mtime < (now - timedelta(days=14)).timestamp():
            old.unlink()
    print(f"Backup created: {target.name}")


if __name__ == "__main__":
    main()
