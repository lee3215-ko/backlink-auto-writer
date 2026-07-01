"""파일 로그."""

from __future__ import annotations

import logging
from pathlib import Path

from app_paths import data_file, migrate_legacy_data

LOG_FILE = data_file("backlink.log")


def setup_logger() -> logging.Logger:
    migrate_legacy_data()
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("backlink")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


log = setup_logger()
