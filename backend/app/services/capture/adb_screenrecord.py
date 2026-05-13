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
import threading
from typing import AsyncIterator, Optional

from .ffmpeg_pipe import FFmpegMjpegPipe
from .ffmpeg_runtime import detect_ffmpeg

logger = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

ADB_PATH = os.environ.get("ADB_PATH", "adb")

# screenrecord 최대 시간은 180초. 만료 직전 매끄럽게 재시작하기 위해 약간 짧게.
_SEGMENT_SECONDS = 175

# 첫 JPEG 프레임 수신 timeout (초). 일부 디바이스/SoC에서 첫 IDR keyframe까지
# 수 초 걸리는 케이스가 있어 넉넉히 잡는다. 폴백 전환이 다소 늦어지지만,
# 한번 성공한 뒤로는 segment 재시작에만 영향 없음.
_FIRST_FRAME_TIMEOUT = 5.0

# idle 상태에서 keep-alive 재송신 간격 (초).
# 화면이 정적이면 H.264 인코더가 새 frame 출력을 멈춘다. 이때 WebSocket에 데이터가
# 전혀 안 가면 일부 클라이언트/proxy가 "끊김"으로 판단할 수 있어, 이 간격으로 마지막
# frame을 다시 송신해 WS 생존을 유지한다 (실제 화면은 정지 상태이므로 시각적으로 동일).
# 사용자가 화면을 만져 H.264 frame이 다시 흐르기 시작하면 wait_for가 즉시 풀려
# 자동 재개되므로 wake-up latency는 SoC 인코더 wake-up 시간만큼만.
_IDLE_FRAME_TIMEOUT = 1.0

# 재시작 사이 잠깐 쉼 (디바이스 인코더 자원 해제 시간 확보).
# 50ms는 빠른 재시작에 좋지만 일부 SoC는 codec instance release에 더 오래 걸려
# 다음 spawn이 frame을 못 받는다. 500ms 정도면 대부분 환경에서 안전.
_RESTART_GAP = 0.5

# screenrecord 실패 시 진단을 위해 stderr 마지막 N바이트만 보관/로깅.
_STDERR_TAIL_BYTES = 1024


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
        size: Optional[str] = None,
        bitrate: int = 4_000_000,
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
            "--bit-rate", str(self.bitrate),
            "--time-limit", str(_SEGMENT_SECONDS),
        ]
        # --size를 명시하면 디바이스가 가로세로비를 맞추려 letterbox(위아래 검은 띠)
        # 또는 pillarbox를 추가해 미러링 화면의 좌표가 어긋난다 (IVI의 와이드 화면
        # 1920x720 등을 16:9로 강제 다운스케일하면 발생). None이면 디바이스 native
        # 해상도 그대로 인코딩하므로 좌표 변환이 정확하게 일치한다. HW 인코더 부담은
        # 거의 차이 없음.
        if self.size:
            cmd += ["--size", self.size]
        # --display-id 0은 보통 default(primary)와 같지만, 일부 단일 디스플레이
        # 디바이스 펌웨어가 이 옵션 자체를 거부하거나 검은 화면을 보낸다.
        # 0/None이면 옵션 자체를 생략해 default 디스플레이를 쓰도록 한다.
        if self.logical_id is not None and self.logical_id != 0:
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
            # 진단: screenrecord/ffmpeg 양쪽 stderr와 종료 상태를 같이 로그.
            # 해석 가이드:
            #   * screenrecord_rc != None + screenrecord_err 있음
            #         → 디바이스가 명령을 거부 (옵션/권한 문제)
            #   * screenrecord_rc = None + screenrecord_err 비어있음 + ffmpeg_err 있음
            #         → ffmpeg가 데이터를 받았지만 분석 실패 (잘못된 H.264 등)
            #   * 양쪽 모두 비어있고 timeout만 발생
            #         → screenrecord가 stdout으로 데이터를 보내지 않음 (정적 화면 등)
            sr_err = self._read_stderr_tail()
            ff_err = self._pipe.stderr_tail() if self._pipe else ""
            rc = self._proc.poll() if self._proc else None
            logger.info(
                "screenrecord first-frame check failed (serial=%s, display=%s): %s "
                "screenrecord_rc=%s screenrecord_err=%r ffmpeg_err=%r",
                self.serial, self.logical_id, type(e).__name__,
                rc, sr_err, ff_err,
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
                # 진단을 위해 stderr 캡처. 단, blocking read로 데드락이 나지 않도록
                # 별도 스레드에서 비동기적으로 tail에 적재한다.
                stderr=subprocess.PIPE,
                creationflags=_NO_WINDOW,
                bufsize=0,
            )
        except Exception as e:
            logger.warning("screenrecord spawn failed (%s): %s", self.serial, e)
            self._proc = None
            return False

        # stderr를 백그라운드로 빨아들여 _STDERR_TAIL_BYTES만큼만 보관.
        # 안 빨아들이면 stderr 파이프가 가득 차 screenrecord가 블록될 수 있음.
        self._stderr_tail = bytearray()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True,
        )
        self._stderr_thread.start()

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
        """JPEG 프레임 yield (producer-consumer 큐 패턴).

        설계 핵심:
            * frame iterator를 **별도 producer task에서 돌린다.** main loop는 queue에서
              꺼내기만 함 → queue.get이 timeout돼도 iterator generator state는 무사.
              (이전 `asyncio.wait_for(self._iter.__anext__(), ...)` 패턴은 timeout 시
              generator를 cancel해 state가 망가지고 stream이 실제 죽었는지 감지를 못해
              "fps 1로 줄어들고 복구 안 됨" 증상을 만들었다.)
            * idle 시 segment를 강제 재시작하지 않음. 마지막 frame을 keep-alive로
              재송신해 WS 생존 유지 + 사용자 화면은 자연스러운 정지 상태.
            * stream이 진짜로 끝나면 producer가 EOF 센티넬을 큐에 넣어 명확히 신호.
            * screenrecord --time-limit 만료(EOF)만 segment 재시작.
        """
        first = getattr(self, "_first_frame", None)
        last_frame: Optional[bytes] = None
        if first is not None:
            self._first_frame = None
            last_frame = first
            yield first

        EOF_SENTINEL: object = object()

        while not self._closed:
            queue: asyncio.Queue = asyncio.Queue(maxsize=2)

            async def _producer(it):
                """ffmpeg pipe iterator를 소비해 큐에 적재. cancel 시 EOF 미통보."""
                try:
                    async for f in it:
                        if self._closed:
                            return
                        await queue.put(f)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug("screenrecord producer error: %s", e)
                # 자연 종료(ffmpeg EOF 등) — main loop가 알 수 있도록 sentinel 전송.
                try:
                    await queue.put(EOF_SENTINEL)
                except Exception:
                    pass

            producer_task = asyncio.create_task(_producer(self._iter))

            segment_had_frames = last_frame is not None
            try:
                while not self._closed:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=_IDLE_FRAME_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        # idle — keep-alive. producer는 그대로 살아있어 generator
                        # state 영향 없음.
                        if last_frame is not None:
                            yield last_frame
                        continue

                    if item is EOF_SENTINEL:
                        break  # stream 자연 종료 → segment restart 분기로
                    segment_had_frames = True
                    last_frame = item  # type: ignore[assignment]
                    yield item
            except (asyncio.CancelledError, GeneratorExit):
                producer_task.cancel()
                raise
            finally:
                producer_task.cancel()
                try:
                    await producer_task
                except (asyncio.CancelledError, Exception):
                    pass

            if self._closed:
                return

            # spawn 직후 frame을 한 개도 못 받고 종료 — 인코더 비정상. 폴백 양보.
            if not segment_had_frames:
                logger.info(
                    "screenrecord segment produced no frames — giving up (serial=%s)",
                    self.serial,
                )
                return

            # segment 만료 시 재시작 (사용자가 거의 느끼지 못하는 175초 주기).
            logger.debug(
                "screenrecord segment ended — restarting (serial=%s)", self.serial,
            )
            await self._close_pipe()
            await asyncio.sleep(_RESTART_GAP)
            if self._closed:
                return
            if not await self._spawn_pipe():
                logger.info("screenrecord restart failed (serial=%s) — giving up", self.serial)
                return
            self._iter = self._pipe.__aiter__()

    def _drain_stderr(self) -> None:
        """screenrecord stderr를 끝까지 읽어 tail에 보관 (별도 스레드에서 호출).
        파이프가 가득 차 디바이스 측이 블록되는 것을 방지하기 위한 필수 동작.
        """
        proc = self._proc
        if not proc or not proc.stderr:
            return
        try:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                self._stderr_tail.extend(chunk)
                # tail이 너무 커지지 않도록 마지막 _STDERR_TAIL_BYTES만 유지.
                if len(self._stderr_tail) > _STDERR_TAIL_BYTES * 2:
                    del self._stderr_tail[:-_STDERR_TAIL_BYTES]
        except Exception:
            pass

    def _read_stderr_tail(self) -> str:
        """현재까지 수집된 stderr의 마지막 부분을 사람이 읽기 좋게 정리해 반환."""
        if not getattr(self, "_stderr_tail", None):
            return ""
        try:
            raw = bytes(self._stderr_tail[-_STDERR_TAIL_BYTES:])
            text = raw.decode("utf-8", errors="replace").strip()
            # 한 줄로 축약 (로그 가독성)
            return " | ".join(line.strip() for line in text.splitlines() if line.strip())
        except Exception:
            return repr(bytes(self._stderr_tail[-_STDERR_TAIL_BYTES:]))

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
        """idempotent 완전 종료. 재사용 불가.

        디바이스 측에 남아있을지 모를 stale screenrecord 프로세스도 함께 정리해
        다음 시도에서 HW 인코더 자원을 깨끗하게 잡을 수 있도록 한다.
        """
        if self._closed:
            return
        self._closed = True
        await self._close_pipe()
        await self._cleanup_device_side()
        logger.info("screenrecord backend closed: serial=%s", self.serial)

    async def _cleanup_device_side(self) -> None:
        """디바이스에 남은 screenrecord 프로세스를 정리.

        프로젝트는 동시 미러링을 한 디바이스당 1개 디스플레이로 제한하므로
        pkill로 전부 정리해도 안전하다. HW 인코더 release를 강제하기 위함.
        """
        loop = asyncio.get_event_loop()
        cmd = [ADB_PATH, "-s", self.serial, "shell", "pkill", "-f", "screenrecord"]
        try:
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd, capture_output=True, timeout=2,
                    creationflags=_NO_WINDOW,
                ),
            )
        except Exception as e:
            logger.debug("device-side screenrecord cleanup error (%s): %s", self.serial, e)

    def is_alive(self) -> bool:
        return not self._closed and self._pipe is not None and self._pipe.is_alive()
