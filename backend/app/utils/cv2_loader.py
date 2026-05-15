"""cv2 안전 로더 — Windows 임베디드 Python 환경 대응.

Python 3.8+ Windows는 DLL 탐색 경로에서 %PATH%를 제외했기 때문에
cv2 패키지 내부 DLL이 자동으로 로드되지 않을 수 있다.
os.add_dll_directory()로 cv2 패키지 디렉토리를 명시적으로 등록한 뒤
표준 import → .pyd 직접 로드 순으로 시도한다.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import site
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _candidate_site_packages() -> list[str]:
    """site-packages 후보 디렉토리 목록 반환 (임베디드 Python 경로 포함)."""
    paths: list[str] = []
    try:
        paths += site.getsitepackages()
    except Exception:
        pass
    try:
        up = site.getusersitepackages()
        if up:
            paths.append(up)
    except Exception:
        pass
    # 임베디드 Python: 실행 파일 기준 상대 경로
    exe_dir = Path(sys.executable).parent
    for rel in ("Lib/site-packages", "lib/site-packages", "site-packages"):
        candidate = exe_dir / rel
        if candidate.is_dir():
            paths.append(str(candidate))
    return paths


def _add_cv2_dll_dirs() -> None:
    """cv2 패키지 디렉토리를 Windows DLL 검색 경로에 추가 (Python 3.8+)."""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    for sp in _candidate_site_packages():
        cv2_dir = Path(sp) / "cv2"
        if not cv2_dir.is_dir():
            continue
        for sub in (cv2_dir, cv2_dir / "libs"):
            if sub.is_dir():
                try:
                    os.add_dll_directory(str(sub))
                except OSError:
                    pass
        return  # 첫 번째 발견된 cv2 디렉토리만 처리


def _load_cv2_direct():
    """cv2 패키지 __init__.py를 우회하고 .pyd/.so를 직접 로드."""
    for sp in _candidate_site_packages():
        for name in ("cv2.pyd", "cv2.so"):
            pyd = Path(sp) / "cv2" / name
            if not pyd.exists():
                continue
            spec = importlib.util.spec_from_file_location("cv2", str(pyd))
            if spec and spec.loader:
                try:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    return mod
                except Exception as e:
                    logger.debug("cv2 직접 로드 실패 (%s): %s", pyd, e)
    return None


def load_cv2():
    """cv2 모듈을 로드하여 반환. 로드 불가 시 None."""
    if "cv2" in sys.modules:
        return sys.modules["cv2"]

    _add_cv2_dll_dirs()

    try:
        import cv2 as _cv2
        return _cv2
    except (ImportError, OSError):
        pass

    mod = _load_cv2_direct()
    if mod is not None:
        sys.modules["cv2"] = mod
        logger.info("cv2: .pyd 직접 로드 성공")
        return mod

    logger.warning("cv2 로드 실패 — 이미지 처리 기능 비활성화")
    return None


# 모듈 임포트 시 즉시 로드
cv2 = load_cv2()
