import os
import sqlite3
import time
from datetime import datetime

from services.maintenance import backup_database, cleanup_downloads


def _age(path, days):
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


def test_cleanup_removes_only_old_media(tmp_path):
    root = tmp_path / "downloads"
    (root / "review_media").mkdir(parents=True)
    old_img = root / "1.jpg"
    old_vid = root / "review_media" / "item-2.mp4"
    fresh = root / "3.jpg"
    old_csv = root / "raw_feed_backup.csv"
    for p in (old_img, old_vid, fresh, old_csv):
        p.write_bytes(b"x" * 10)
    for p in (old_img, old_vid, old_csv):
        _age(p, 40)

    removed, freed = cleanup_downloads(root, days=30)

    assert removed == 2 and freed == 20
    assert fresh.exists() and old_csv.exists()
    assert not old_img.exists() and not old_vid.exists()


def test_cleanup_disabled_with_zero_days(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    _age(tmp_path / "a.jpg", 99)
    assert cleanup_downloads(tmp_path, days=0) == (0, 0)


def test_backup_creates_copy_and_rotates(tmp_path):
    db = tmp_path / "data.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()
    dest = tmp_path / "backups"

    for day in range(1, 5):
        backup_database(db, dest, keep=2, now=datetime(2026, 9, day))

    files = sorted(p.name for p in dest.glob("data-*.db"))
    assert files == ["data-2026-09-03.db", "data-2026-09-04.db"]
    conn = sqlite3.connect(dest / files[-1])
    try:
        assert conn.execute("SELECT x FROM t").fetchone() == (42,)
    finally:
        conn.close()


def test_backup_missing_db_returns_none(tmp_path):
    assert backup_database(tmp_path / "nope.db", tmp_path / "b", keep=3) is None
