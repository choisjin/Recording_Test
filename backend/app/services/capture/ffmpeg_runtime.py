"""FFmpeg 바이너리 감지 및 버전 진단.

라이브 미러링 백엔드(AdbScreenrecordBackend 등)는 ffmpeg에 의존한다.
시작 시 한 번 감지해 캐시하고, 미발견 시 해당 백엔드는 None을 반환하여
호출자가 기존 screencap PNG 폴백으로 자연 전환되도록 한다.

탐색 우선순위 (앞쪽이 먼저 적중):
    1. FFMPEG_PATH 환경변수 — 명시적 override
    2. 프로젝트 번들 경로 (개발) — <repo>/tools/ffmpeg(.exe)
    3. CWD 번들 경로 — ./tools/ffmpeg(.exe)
    4. 배포 설치 경로 — C:\\ReplayKit\\tools\\ffmpeg.exe / /opt/ReplayKit/tools/ffmpeg
    5. 시스템 PATH — shutil.which("ffmpeg")

이 순서는 "사용자가 같이 배포한 바이너리"를 시스템 ffmpeg보다 우선해 사용하기 위해서다.
설치 환경마다 ffmpeg 버전이 달라 인코더 옵션 호환성이 깨지는 사고를 막는다.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _binary_names() -> list[str]:
    return ["ffmpeg.exe"] if sys.platform == "win32" else ["ffmpeg"]


def _project_root() -> Path:
    """이 파일은 <root>/backend/app/services/capture/ffmpeg_runtime.py 위치 →
    parents[4]가 프로젝트 루트.
    """
    return Path(__file__).resolve().parents[4]


def _install_root_candidates() -> list[Path]:
    """배포 설치 환경의 ReplayKit 루트 후보 — tools/ 하위에 ffmpeg가 들어간다."""
    if sys.platform == "win32":
        return [Path(r"C:\ReplayKit")]
    # 추후 Linux 패키징 시 다음 경로들이 후보가 된다.
    return [Path("/opt/ReplayKit"), Path.home() / ".local" / "share" / "ReplayKit"]


def _candidate_paths() -> list[Path]:
    """모든 ffmpeg 후보 경로를 우선순위 순으로 평탄화."""
    paths: list[Path] = []
    names = _binary_names()

    # 2) 프로젝트 루트 번들
    for n in names:
        paths.append(_project_root() / "tools" / n)
    # 3) CWD 번들
    for n in names:
        paths.append(Path.cwd() / "tools" / n)
    # 4) 배포 설치 경로
    for root in _install_root_candidates():
        for n in names:
            paths.append(root / "tools" / n)

    return paths


@functools.lru_cache(maxsize=1)
def detect_ffmpeg() -> Optional[str]:
    """ffmpeg 바이너리 경로 반환. 미설치/미발견이면 None.

    lru_cache로 한 번만 평가되므로 호출 비용은 첫 호출 외에는 무시 가능.
    경로 캐시 무효화가 필요하면 detect_ffmpeg.cache_clear() 호출.
    """
    # 1) FFMPEG_PATH 환경변수
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2~4) 번들/배포 경로
    for cand in _candidate_paths():
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            # 권한/네트워크 경로 등에서 가끔 발생 — 다음 후보로 계속
            continue

    # 5) 시스템 PATH 폴백
    return shutil.which("ffmpeg")


@functools.lru_cache(maxsize=1)
def ffmpeg_version() -> Optional[str]:
    """`ffmpeg -version`의 첫 줄에서 버전 토큰 추출. 미설치/실패 시 None.

    버전 문자열은 진단/로그/디버깅 용도이며, 실제 동작 분기에는 사용하지 않는다.
    """
    ff = detect_ffmpeg()
    if not ff:
        return None
    try:
        proc = subprocess.run(
            [ff, "-version"],
            capture_output=True,
            timeout=5,
            creationflags=_NO_WINDOW,
        )
        out = proc.stdout.decode(errors="replace") if proc.stdout else ""
        first_line = out.splitlines()[0] if out else ""
        m = re.match(r"ffmpeg version (\S+)", first_line)
        return m.group(1) if m else (first_line.strip() or None)
    except Exception as e:
        logger.debug("ffmpeg -version probe failed: %s", e)
        return None


def log_runtime_status() -> None:
    """기동 시 한 번 호출 — ffmpeg 가용성을 로그로 노출.

    있으면 INFO, 없으면 WARNING. WARNING이라도 앱 동작은 정상 (폴백 경로 사용).
    """
    ff = detect_ffmpeg()
    if ff:
        ver = ffmpeg_version() or "unknown"
        logger.info("ffmpeg detected: path=%s version=%s", ff, ver)
    else:
        logger.warning(
            "ffmpeg not found in PATH (and FFMPEG_PATH not set). "
            "H.264 live mirroring backend disabled — screen mirroring will use "
            "screencap PNG fallback (low fps, higher device load). "
            "Install ffmpeg to enable hardware-encoded mirroring."
        )
