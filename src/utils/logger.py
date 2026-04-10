"""
로깅 유틸리티

프로젝트 전역 로깅 설정.
콘솔 + 파일 출력을 지원한다.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime


def setup_logger(
    name: str = "crawling",
    log_dir: str = "logs",
    level: int = logging.INFO,
) -> logging.Logger:
    """
    프로젝트 로거를 설정한다.

    Args:
        name: 로거 이름
        log_dir: 로그 파일 저장 디렉토리
        level: 로그 레벨

    Returns:
        설정된 Logger 인스턴스
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 콘솔 핸들러
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 파일 핸들러
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    file_handler = logging.FileHandler(
        log_path / f"crawl_{today}.log",
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
