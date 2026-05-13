"""scrcpy-server 기반 H.264 라이브 미러링 백엔드.

scrcpy-server.jar를 디바이스에 push 후 app_process로 실행해 MediaCodec API를
직접 호출한다. screenrecord와 달리:
  * idle 시에도 frame 출력이 자연스러움 (인코더 직접 제어)
  * 무한 streaming (segment 175초 제한 없음)
  * raw_stream=true 모드로 prefix bytes 없이 순수 H.264 NAL stream 수신

흐름:
  1. tools/scrcpy-server.jar 를 /data/local/tmp/scrcpy-server.jar 로 push (해시 비교로 skip 가능)
  2. adb forward tcp:<local_port> localabstract:scrcpy_<scid>
  3. adb shell CLASSPATH=... app_process / com.genymobile.scrcpy.Server <version> ...
  4. localhost:<local_port>로 TCP connect (재시도 with backoff)
  5. socket → ffmpeg stdin (asyncio forwarding task)
  6. ffmpeg → MJPEG → JPEG 프레임 yield

폴백 트리거:
  * scrcpy-server.jar 부재 (배포 누락)
  * adb push 실패
  * app_process 실행 실패 (특정 GVM/IVI 환경)
  * TCP connect 실패 또는 첫 프레임 timeout
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import os
import random
import socket
import subprocess
import sys
from pathlib import Path
from typing import AsyncIterator, Optional

from .ffmpeg_pipe import FFmpegMjpegPipe
from .ffmpeg_runtime import detect_ffmpeg

logger = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

ADB_PATH = os.environ.get("ADB_PATH", "adb")

# scrcpy 버전 — 옵션 형식이 버전마다 다르므로 server.jar와 동일 버전 고정.
SCRCPY_VERSION = "2.4"

# 디바이스 측 jar 경로.
DEVICE_JAR_PATH = "/data/local/tmp/scrcpy-server.jar"

# 첫 JPEG 프레임 수신 timeout (초).
_FIRST_FRAME_TIMEOUT = 5.0

# idle keep-alive 간격 (초). screenrecord 백엔드와 동일 의미.
_IDLE_FRAME_TIMEOUT = 1.0

# TCP connect 재시도 — server.jar 시작 후 listen 까지 약간 지연 있음.
_CONNECT_RETRIES = 30
_CONNECT_RETRY_DELAY = 0.1

# adb forward에 사용할 로컬 포트 범위 (자동 할당이라 충돌 없음).
_LOCAL_PORT_BASE = 27183


# ----------------------------------------------------------------------
# Path discovery (ffmpeg_runtime과 동일 패턴)
# ----------------------------------------------------------------------

def _project_root() -> Path:
    """이 파일은 <root>/backend/app/services/capture/scrcpy_server.py → parents[4]."""
    return Path(__file__).resolve().parents[4]


def _install_root_candidates() -> list[Path]:
    if sys.platform == "win32":
        return [Path(r"C:\ReplayKit")]
    return [Path("/opt/ReplayKit"), Path.home() / ".local" / "share" / "ReplayKit"]


@functools.lru_cache(maxsize=1)
def detect_scrcpy_server() -> Optional[str]:
    """scrcpy-server.jar 경로 반환. 미발견 시 None.

    탐색 우선순위:
      1. SCRCPY_SERVER_PATH 환경변수
      2. <repo>/tools/scrcpy-server.jar (개발)
      3. ./tools/scrcpy-server.jar (CWD)
      4. C:\\ReplayKit\\tools\\scrcpy-server.jar (배포)
    """
    env_path = os.environ.get("SCRCPY_SERVER_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    name = "scrcpy-server.jar"
    candidates: list[Path] = [
        _project_root() / "tools" / name,
        Path.cwd() / "tools" / name,
    ]
    for root in _install_root_candidates():
        candidates.append(root / "tools" / name)

    for cand in candidates:
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    return None


def log_scrcpy_status() -> None:
    """기동 시 한 번 호출 — scrcpy-server.jar 가용성 로그."""
    jar = detect_scrcpy_server()
    if jar:
        try:
            size = os.path.getsize(jar)
        except OSError:
            size = 0
        logger.info("scrcpy-server detected: path=%s size=%d", jar, size)
    else:
        logger.info(
            "scrcpy-server.jar not found. scrcpy backend disabled — "
            "screenrecord/screencap fallback will be used."
        )


# ----------------------------------------------------------------------
# Backend
# ----------------------------------------------------------------------

# 디바이스 측 jar 해시 캐시 (push 중복 방지). key = (serial, local_jar_path).
_pushed_jar_hashes: dict[tuple[str, str], str] = {}


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _alloc_local_port() -> int:
    """사용 가능한 로컬 TCP 포트 1개 할당 — 사용자 환경에서 충돌 없는 임의 포트."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ScrcpyServerBackend:
    """scrcpy-server.jar → ffmpeg → MJPEG 파이프라인.

    하나의 인스턴스는 (serial, logical_id) 조합 하나에 1:1 대응.
    """

    name = "scrcpy_server"

    def __init__(
        self,
        serial: str,
        logical_id: Optional[int] = None,
        *,
        bitrate: int = 4_000_000,
        max_fps: int = 0,
        quality: int = 5,
    ):
        self.serial = serial
        self.logical_id = logical_id or 0
        self.bitrate = bitrate
        self.max_fps = max_fps
        self.quality = quality
        # scrcpy v2.x 필수: 8-hex SCID (랜덤). socket name에도 사용됨.
        self.scid = "%08x" % random.randint(0, 0xFFFFFFFF)
        self.local_port = 0
        self._server_proc: Optional[subprocess.Popen] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._pipe: Optional[FFmpegMjpegPipe] = None
        self._forward_task: Optional[asyncio.Task] = None
        self._first_frame: Optional[bytes] = None
        self._iter: Optional[AsyncIterator[bytes]] = None
        self._closed = False
        # 진단용 stderr tail
        self._stderr_tail: bytearray = bytearray()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def try_start(
        self, first_frame_timeout: float = _FIRST_FRAME_TIMEOUT,
    ) -> bool:
        """시작 + 첫 프레임 수신 검증. 실패 시 cleanup하고 False."""
        if not detect_ffmpeg():
            return False
        jar = detect_scrcpy_server()
        if not jar:
            return False

        try:
            # 1) jar push (해시 동일 시 skip)
            if not await self._push_jar(jar):
                return False
            # 2) 로컬 포트 할당 + adb forward
            self.local_port = _alloc_local_port()
            if not await self._setup_forward():
                return False
            # 3) server 프로세스 실행 (백그라운드)
            if not await self._spawn_server():
                return False
            # 4) TCP connect (server.jar listen 시작까지 backoff 재시도)
            if not await self._connect_socket():
                return False
            # 5) ffmpeg spawn + socket → stdin forwarding task 시작
            if not await self._attach_ffmpeg():
                return False
            # 6) 첫 프레임 검증
            self._iter = self._pipe.__aiter__()  # type: ignore[union-attr]
            first = await asyncio.wait_for(
                self._iter.__anext__(), timeout=first_frame_timeout
            )
            self._first_frame = first
        except (asyncio.TimeoutError, StopAsyncIteration, Exception) as e:
            ff_err = self._pipe.stderr_tail() if self._pipe else ""
            sr_err = self._stderr_tail_str()
            fwd_bytes = getattr(self, "_forward_total_bytes", 0)
            logger.info(
                "scrcpy first-frame check failed (serial=%s display=%s): %s "
                "fwd_bytes=%d server_err=%r ffmpeg_err=%r",
                self.serial, self.logical_id, type(e).__name__,
                fwd_bytes, sr_err, ff_err,
            )
            await self.close()
            return False

        logger.info(
            "scrcpy backend started: serial=%s display=%s scid=%s port=%d bitrate=%d",
            self.serial, self.logical_id, self.scid, self.local_port, self.bitrate,
        )
        return True

    async def _push_jar(self, local_jar: str) -> bool:
        """디바이스에 jar push. 해시 동일 시 skip."""
        cache_key = (self.serial, local_jar)
        try:
            local_hash = _file_sha256(local_jar)
        except OSError as e:
            logger.warning("scrcpy jar read error: %s", e)
            return False

        cached = _pushed_jar_hashes.get(cache_key)
        if cached == local_hash:
            # 이미 push됨. 다만 디바이스 측에서 파일이 실제로 존재하는지 한 번 확인.
            if await self._device_jar_exists():
                logger.debug("scrcpy jar push skipped (already pushed): %s", self.serial)
                return True

        loop = asyncio.get_event_loop()
        cmd = [ADB_PATH, "-s", self.serial, "push", local_jar, DEVICE_JAR_PATH]
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd, capture_output=True, timeout=10,
                    creationflags=_NO_WINDOW,
                ),
            )
        except Exception as e:
            logger.warning("scrcpy jar push failed (%s): %s", self.serial, e)
            return False
        if result.returncode != 0:
            logger.warning(
                "scrcpy jar push failed (%s): %s",
                self.serial, result.stderr.decode(errors="replace").strip(),
            )
            return False
        _pushed_jar_hashes[cache_key] = local_hash
        return True

    async def _device_jar_exists(self) -> bool:
        loop = asyncio.get_event_loop()
        cmd = [ADB_PATH, "-s", self.serial, "shell", "ls", DEVICE_JAR_PATH]
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd, capture_output=True, timeout=3,
                    creationflags=_NO_WINDOW,
                ),
            )
            return result.returncode == 0
        except Exception:
            return False

    async def _setup_forward(self) -> bool:
        """adb forward 등록. localabstract:scrcpy_<scid>로 connect 가능."""
        loop = asyncio.get_event_loop()
        cmd = [
            ADB_PATH, "-s", self.serial, "forward",
            f"tcp:{self.local_port}",
            f"localabstract:scrcpy_{self.scid}",
        ]
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd, capture_output=True, timeout=5,
                    creationflags=_NO_WINDOW,
                ),
            )
        except Exception as e:
            logger.warning("adb forward failed (%s): %s", self.serial, e)
            return False
        if result.returncode != 0:
            logger.warning(
                "adb forward failed (%s): %s",
                self.serial, result.stderr.decode(errors="replace").strip(),
            )
            return False
        return True

    async def _remove_forward(self) -> None:
        if not self.local_port:
            return
        loop = asyncio.get_event_loop()
        cmd = [
            ADB_PATH, "-s", self.serial, "forward", "--remove",
            f"tcp:{self.local_port}",
        ]
        try:
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd, capture_output=True, timeout=3,
                    creationflags=_NO_WINDOW,
                ),
            )
        except Exception:
            pass

    def _build_server_cmd(self) -> list[str]:
        """app_process 명령 구성 — scrcpy v2.4 옵션 형식.

        raw_stream=true: prefix bytes(dummy/meta) 없이 순수 H.264 NAL stream 출력.
          → ffmpeg에 바로 input으로 흘려넣을 수 있음.
        tunnel_forward=true: server가 listen, PC가 client (adb forward 사용).
        control=false: 입력 채널 비활성 (우리는 기존 ADB input 사용).
        cleanup=true: 비정상 종료 시 디바이스 측 자원 정리.
        """
        # shell 명령 한 줄로 합쳐서 전달 (CLASSPATH 환경변수 prefix).
        opts = [
            f"scid={self.scid}",
            # 진단을 위해 일시적으로 debug. 안정화 후 error로 환원.
            "log_level=debug",
            "video=true",
            "audio=false",
            "video_codec=h264",
            f"video_bit_rate={self.bitrate}",
            "max_size=0",
            f"max_fps={self.max_fps}",
            "video_source=display",
            f"display_id={self.logical_id}",
            "control=false",
            "cleanup=true",
            "power_on=false",
            "tunnel_forward=true",
            # raw_stream만 의존하지 않고 메타 옵션도 명시적으로 모두 false.
            # 일부 scrcpy 버전에서 raw_stream 처리가 빠질 수 있어 안전망으로 추가.
            "raw_stream=true",
            "send_dummy_byte=false",
            "send_device_meta=false",
            "send_codec_meta=false",
            "send_frame_meta=false",
        ]
        inner = (
            f"CLASSPATH={DEVICE_JAR_PATH} "
            f"app_process / com.genymobile.scrcpy.Server {SCRCPY_VERSION} "
            + " ".join(opts)
        )
        return [ADB_PATH, "-s", self.serial, "shell", inner]

    async def _spawn_server(self) -> bool:
        """server 프로세스를 백그라운드로 spawn. stderr는 진단용으로 캡처."""
        try:
            self._server_proc = subprocess.Popen(
                self._build_server_cmd(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
                bufsize=0,
            )
        except Exception as e:
            logger.warning("scrcpy server spawn failed (%s): %s", self.serial, e)
            return False

        # stderr를 백그라운드로 drain (pipe 막힘 방지 + 진단용).
        import threading
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        return True

    def _drain_stderr(self) -> None:
        proc = self._server_proc
        if not proc or not proc.stderr:
            return
        try:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > 4096:
                    del self._stderr_tail[:-2048]
        except Exception:
            pass

    def _stderr_tail_str(self) -> str:
        if not self._stderr_tail:
            return ""
        try:
            raw = bytes(self._stderr_tail[-1024:])
            text = raw.decode("utf-8", errors="replace").strip()
            return " | ".join(line.strip() for line in text.splitlines() if line.strip())
        except Exception:
            return ""

    async def _connect_socket(self) -> bool:
        """server.jar의 listen이 준비될 때까지 backoff로 connect 재시도."""
        for attempt in range(_CONNECT_RETRIES):
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    "127.0.0.1", self.local_port,
                )
                return True
            except (ConnectionRefusedError, OSError):
                # 아직 listen 안 됨. 짧게 sleep 후 재시도.
                await asyncio.sleep(_CONNECT_RETRY_DELAY)
            # server 프로세스가 일찍 죽었으면 그만.
            if self._server_proc and self._server_proc.poll() is not None:
                logger.info(
                    "scrcpy server exited before listen (%s): %s",
                    self.serial, self._stderr_tail_str(),
                )
                return False
        logger.info(
            "scrcpy connect timed out (%s): %s",
            self.serial, self._stderr_tail_str(),
        )
        return False

    async def _attach_ffmpeg(self) -> bool:
        """ffmpeg를 stdin=PIPE로 spawn하고 socket → stdin forwarding task 시작."""
        try:
            self._pipe = await FFmpegMjpegPipe.from_stdin_pipe(
                input_fmt="h264", quality=self.quality,
            )
        except Exception as e:
            logger.warning("ffmpeg attach failed (%s): %s", self.serial, e)
            return False

        self._forward_task = asyncio.create_task(self._forward_socket_to_ffmpeg())
        return True

    async def _forward_socket_to_ffmpeg(self) -> None:
        """TCP socket의 raw H.264 데이터를 ffmpeg stdin으로 전달.

        byte counter를 보관해 try_start 실패 시 진단에 사용:
          * total_bytes == 0 → server가 데이터를 안 보냄 (raw_stream 옵션 미적용 등)
          * total_bytes 있는데 ffmpeg가 stream 못 찾음 → 데이터 포맷 문제 (prefix bytes 등)
        """
        self._forward_total_bytes = 0
        if not self._reader or not self._pipe:
            return
        stdin = self._pipe.ffmpeg_stdin
        if not stdin:
            return
        try:
            while not self._closed:
                chunk = await self._reader.read(65536)
                if not chunk:
                    break
                self._forward_total_bytes += len(chunk)
                try:
                    stdin.write(chunk)
                    await stdin.drain()
                except (ConnectionResetError, BrokenPipeError):
                    break
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:
            logger.debug("scrcpy forward task error: %s", e)
        finally:
            try:
                stdin.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_jpeg(self) -> AsyncIterator[bytes]:
        """JPEG 프레임 yield.

        screenrecord 백엔드와 동일한 producer-consumer 큐 패턴 + idle keep-alive.
        scrcpy는 segment 만료가 없으므로 자연 종료는 거의 일어나지 않는다.
        """
        first = self._first_frame
        last_frame: Optional[bytes] = None
        if first is not None:
            self._first_frame = None
            last_frame = first
            yield first

        EOF_SENTINEL: object = object()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)

        async def _producer(it):
            try:
                async for f in it:
                    if self._closed:
                        return
                    await queue.put(f)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("scrcpy producer error: %s", e)
            try:
                await queue.put(EOF_SENTINEL)
            except Exception:
                pass

        producer_task = asyncio.create_task(_producer(self._iter))

        try:
            while not self._closed:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=_IDLE_FRAME_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    if last_frame is not None:
                        yield last_frame
                    continue

                if item is EOF_SENTINEL:
                    break
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

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """idempotent 완전 종료. 재사용 불가."""
        if self._closed:
            return
        self._closed = True

        # 1) socket close → server.jar가 자연 종료 (cleanup=true)
        if self._writer:
            try:
                self._writer.close()
                try:
                    await asyncio.wait_for(self._writer.wait_closed(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            except Exception:
                pass
            self._writer = None
        self._reader = None

        # 2) ffmpeg pipe 닫기 (stdin EOF 전달되어 ffmpeg가 종료)
        if self._pipe:
            try:
                await self._pipe.close()
            except Exception as e:
                logger.debug("scrcpy pipe close error: %s", e)
            self._pipe = None

        # 3) forwarding task cancel
        if self._forward_task and not self._forward_task.done():
            self._forward_task.cancel()
            try:
                await self._forward_task
            except (asyncio.CancelledError, Exception):
                pass
        self._forward_task = None

        # 4) server 프로세스가 아직 살아있으면 kill (cleanup=true라도 hung 가능성).
        if self._server_proc and self._server_proc.poll() is None:
            try:
                self._server_proc.terminate()
                try:
                    self._server_proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._server_proc.kill()
            except Exception:
                pass
        self._server_proc = None

        # 5) 디바이스 측 잔존 app_process 정리 (cleanup=true가 처리하지만 보강).
        await self._cleanup_device_side()

        # 6) adb forward 제거
        await self._remove_forward()

        logger.info("scrcpy backend closed: serial=%s", self.serial)

    async def _cleanup_device_side(self) -> None:
        """디바이스에 남아있을지 모를 scrcpy app_process 정리."""
        loop = asyncio.get_event_loop()
        cmd = [
            ADB_PATH, "-s", self.serial, "shell",
            "pkill", "-f", f"scrcpy.Server.*scid={self.scid}",
        ]
        try:
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd, capture_output=True, timeout=2,
                    creationflags=_NO_WINDOW,
                ),
            )
        except Exception:
            pass

    def is_alive(self) -> bool:
        return (
            not self._closed
            and self._pipe is not None
            and self._pipe.is_alive()
            and self._server_proc is not None
            and self._server_proc.poll() is None
        )
