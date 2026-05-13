"""ADB screenrecord 기반 H.264 라이브 미러링 백엔드.

`adb exec-out screenrecord --output-format=h264 - | ffmpeg -f h264 -i - -f mjpeg -`
파이프라인으로 디바이스의 하드웨어 H.264 인코더 출력을 받아 PC에서 MJPEG로
재인코딩한 뒤 JPEG 프레임을 WebSocket으로 전송한다.

- scrcpy.jar 같은 디바이스 측 별도 삽입물 없이 Android 내장 명령만 사용.
- 디바이스당 동시 한 디스플레이만 활성 (사용자 제약).
- screenrecord의 --time-limit 180초 제한은 175초 segment로 잡고 EOF 시 자동 재시작.
- 다음 폴백 트리거 시 try_start()가 False → 호출자가 screencap PNG 경로로 전환:
    * ffmpeg 미설치
    * subprocess spawn 실패
    * 첫 JPEG 프레임을 timeout 내에 받지 못함 (GVM/IVI 권한, secure surface,
      Android < 10에서 --display-id 거부 등)
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from typing import AsyncIterator, Optional

from .ffmpeg_pipe import FFmpegMjpegPipe
from .ffmpeg_runtime import detect_ffmpeg

logger = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

ADB_PATH = os.environ.get("ADB_PATH", "adb")

# screenrecord 최대 시간은 180초. 만료 직전 매끄럽게 재시작하기 위해 약간 짧게.
_SEGMENT_SECONDS = 175

# 첫 JPEG 프레임 수신 timeout (초). H.264 keyframe + ffmpeg 초기화 시간 고려.
_FIRST_FRAME_TIMEOUT = 2.5

# 재시작 사이 잠깐 쉼 (디바이스 인코더 자원 해제 시간 확보).
_RESTART_GAP = 0.05


class AdbScreenrecordBackend:
    """`adb exec-out screenrecord` → ffmpeg → MJPEG 파이프라인.

    하나의 인스턴스는 (serial, logical_id) 조합 하나에 1:1 대응.
    동일 인스턴스를 동시에 여러 코루틴에서 stream_jpeg() 호출하지 말 것.
    """

    name = "adb_screenrecord"

    def __init__(
        self,
        serial: str,
        logical_id: Optional[int] = None,
        *,
        size: str = "1280x720",
        bitrate: int = 2_000_000,
        quality: int = 5,
    ):
        self.serial = serial
        # screenrecord --display-id는 Android DisplayManager logical ID를 받는다.
        # screencap의 SurfaceFlinger uniqueId(sf_id)와 다른 체계라는 점에 주의.
        self.logical_id = logical_id
        self.size = size
        self.bitrate = bitrate
        self.quality = quality
        self._proc: Optional[subprocess.Popen] = None
        self._pipe: Optional[FFmpegMjpegPipe] = None
        self._closed = False

    # ------------------------------------------------------------------
    # Command builders
    # ------------------------------------------------------------------

    def _build_cmd(self) -> list[str]:
        cmd = [
            ADB_PATH, "-s", self.serial, "exec-out",
            "screenrecord",
            "--output-format=h264",
            "--size", self.size,
            "--bit-rate", str(self.bitrate),
            "--time-limit", str(_SEGMENT_SECONDS),
        ]
        if self.logical_id is not None:
            cmd += ["--display-id", str(self.logical_id)]
        cmd.append("-")
        return cmd

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def try_start(
        self, first_frame_timeout: float = _FIRST_FRAME_TIMEOUT
    ) -> bool:
        """시작 + 첫 프레임 수신 검증. 실패 시 cleanup하고 False.

        호출자는 False를 받으면 screencap 폴백을 사용해야 한다.
        """
        if not detect_ffmpeg():
            logger.debug("screenrecord backend skipped: ffmpeg not available")
            return False

        ok = await self._spawn_pipe()
        if not ok:
            return False

        # 첫 프레임 수신 검증. ffmpeg는 키프레임 받기 전엔 출력하지 않으므로
        # 일정 시간 안에 첫 JPEG가 안 나오면 디바이스 측 인코더가 사실상 안 돌고
        # 있는 것 (권한/HWcodec 미지원/display-id 거부 등).
        try:
            self._iter = self._pipe.__aiter__()
            first = await asyncio.wait_for(self._iter.__anext__(), timeout=first_frame_timeout)
        except (asyncio.TimeoutError, StopAsyncIteration, Exception) as e:
            logger.info(
                "screenrecord first-frame check failed (serial=%s, display=%s): %s",
                self.serial, self.logical_id, type(e).__name__,
            )
            await self._close_pipe()
            return False

        self._first_frame: Optional[bytes] = first
        logger.info(
            "screenrecord backend started: serial=%s display=%s size=%s bitrate=%d",
            self.serial, self.logical_id, self.size, self.bitrate,
        )
        return True

    async def _spawn_pipe(self) -> bool:
        """screenrecord Popen + ffmpeg pipe 연결. 자체적으로 cleanup 처리."""
        try:
            self._proc = subprocess.Popen(
                self._build_cmd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
                bufsize=0,
            )
        except Exception as e:
            logger.warning("screenrecord spawn failed (%s): %s", self.serial, e)
            self._proc = None
            return False

        try:
            self._pipe = await FFmpegMjpegPipe.from_input_proc(
                self._proc, input_fmt="h264", quality=self.quality,
            )
        except Exception as e:
            logger.warning("ffmpeg pipe attach failed (%s): %s", self.serial, e)
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
            return False

        return True

    async def stream_jpeg(self) -> AsyncIterator[bytes]:
        """JPEG 프레임 yield. screenrecord --time-limit 만료 시 자동 재시작.

        try_start()가 True를 반환했을 때만 호출해야 한다 (첫 프레임이 _first_frame에
        대기하고 있다고 가정).
        """
        # try_start()에서 받아둔 첫 프레임을 가장 먼저 emit.
        first = getattr(self, "_first_frame", None)
        if first is not None:
            self._first_frame = None
            yield first

        # 현재 iterator 소진 (현 segment의 나머지 프레임)
        while not self._closed:
            try:
                async for frame in self._iter_remaining():
                    yield frame
                    if self._closed:
                        return
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as e:
                logger.debug("screenrecord stream iteration error: %s", e)

            if self._closed:
                return

            # segment 만료 또는 비정상 EOF → 재시작
            await self._close_pipe()
            await asyncio.sleep(_RESTART_GAP)
            if self._closed:
                return
            if not await self._spawn_pipe():
                logger.info("screenrecord restart failed (serial=%s) — giving up", self.serial)
                return
            self._iter = self._pipe.__aiter__()

    async def _iter_remaining(self) -> AsyncIterator[bytes]:
        """현재 ffmpeg pipe의 남은 프레임을 소진. StopAsyncIteration까지."""
        if self._pipe is None or not hasattr(self, "_iter"):
            return
        while True:
            try:
                frame = await self._iter.__anext__()
            except StopAsyncIteration:
                return
            yield frame

    async def _close_pipe(self) -> None:
        """ffmpeg pipe만 닫고 인스턴스는 살림 (재시작 대비)."""
        if self._pipe is not None:
            try:
                await self._pipe.close()
            except Exception as e:
                logger.debug("ffmpeg pipe close error: %s", e)
            self._pipe = None
        # _pipe.close()가 input_proc도 정리하지만, 혹시 남았으면 한 번 더.
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass
        self._proc = None

    async def close(self) -> None:
        """idempotent 완전 종료. 재사용 불가."""
        if self._closed:
            return
        self._closed = True
        await self._close_pipe()
        logger.info("screenrecord backend closed: serial=%s", self.serial)

    def is_alive(self) -> bool:
        return not self._closed and self._pipe is not None and self._pipe.is_alive()
