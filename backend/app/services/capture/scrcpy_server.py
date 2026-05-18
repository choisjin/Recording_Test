"""scrcpy-server (v1.25) 기반 H.264 라이브 미러링 백엔드.

scrcpy-server.jar를 디바이스에 push 후 app_process로 실행해 MediaCodec API를
직접 호출한다. screenrecord와 달리:
  * idle 시에도 frame 출력이 자연스러움 (인코더 직접 제어)
  * 무한 streaming (segment 175초 제한 없음)
  * raw_video_stream=true 모드로 prefix bytes 없이 순수 H.264 NAL stream 수신

v1.25 + adb reverse 선택 이유:
  * v2.x SurfaceControl direct API는 자동차 IVI 컨테이너(HMG 등)에서 차단됨
  * v1.x Surface 간접 mirroring은 임베디드/자동차 Android 호환성 우수
  * tunnel_forward=false (adb reverse) 가 HMG 같은 컨테이너 환경에서 동작.
    forward 방향(device listen, PC connect)은 SELinux/컨테이너 정책에 막힐 수 있지만,
    reverse 방향(PC listen, device connect)은 허용되는 경우가 많다.

디코딩 파이프라인:
  * 과거: socket → ffmpeg subprocess → MJPEG → JPEG (ffmpeg 100MB 의존)
  * 현재: socket → PyAV CodecContext (H.264 직접 디코딩) → cv2.imencode JPEG
  PyAV 미설치/실패 시 try_start False 반환 → 호출자가 screencap PNG 폴백 사용.

흐름:
  1. tools/scrcpy-server.jar(v1.25) 를 /data/local/tmp/scrcpy-server.jar 로 push
  2. PC 측에서 TCP listen (asyncio.start_server, 동적 포트)
  3. adb reverse localabstract:scrcpy tcp:<PC_port>
  4. adb shell CLASSPATH=... app_process / com.genymobile.scrcpy.Server 1.25 ...
     server.jar가 localabstract:scrcpy 로 connect → adb reverse가 PC TCP로 forward
  5. 우리 listen socket이 connection 받음 → reader/writer 획득
  6. async task가 reader.read() → single-thread executor로 PyAV decode + JPEG encode
  7. JPEG 프레임을 asyncio.Queue에 put → stream_jpeg()에서 yield

폴백 트리거:
  * scrcpy-server.jar 부재 (배포 누락)
  * PyAV(av) 미설치
  * adb push / reverse 실패
  * app_process 실행 실패
  * 디바이스 connect 실패 또는 첫 프레임 timeout
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import os
import socket
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional, Tuple

logger = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

ADB_PATH = os.environ.get("ADB_PATH", "adb")

# scrcpy 버전 — 옵션 형식과 동작이 버전마다 다르므로 server.jar와 정확히 일치해야 한다.
# scrcpy 1.x server는 client_version과 BuildConfig.VERSION_NAME을 strict 비교하므로
# 불일치 시 즉시 IllegalArgumentException으로 종료. 배포된 jar(v1.25)와 일치시킨다.
SCRCPY_VERSION = "1.25"

# 디바이스 측 jar 경로.
DEVICE_JAR_PATH = "/data/local/tmp/scrcpy-server.jar"

# 첫 JPEG 프레임 수신 timeout (초). IVI 등 정적 화면에서 첫 IDR이 늦게 오는 케이스에
# 대응해 12초로 넉넉히 잡음. codec_options.i-frame-interval=1 로도 보완되지만 디바이스
# 별로 적용 시점에 차이가 있어 timeout 여유와 함께 사용.
_FIRST_FRAME_TIMEOUT = 12.0

# idle keep-alive 간격 (초) — 화면 변화가 없을 때 마지막 프레임을 재전송해 클라이언트
# 측 stale detection을 피한다.
_IDLE_FRAME_TIMEOUT = 1.0

# socket → decoder chunk 크기. 너무 크면 첫 프레임 latency 증가, 너무 작으면 syscall 폭주.
_READ_CHUNK = 64 * 1024

# JPEG 인코딩 품질 (cv2.IMWRITE_JPEG_QUALITY). 75~85 사이가 시각적/대역폭 균형점.
_JPEG_QUALITY = 80


# ----------------------------------------------------------------------
# scrcpy v1.x Control protocol constants
# ----------------------------------------------------------------------

# Control message types (server-side enum ControlMessage.TYPE_*)
SC_TYPE_INJECT_KEYCODE = 0
SC_TYPE_INJECT_TEXT = 1
SC_TYPE_INJECT_TOUCH_EVENT = 2
SC_TYPE_INJECT_SCROLL_EVENT = 3
SC_TYPE_BACK_OR_SCREEN_ON = 4
SC_TYPE_EXPAND_NOTIFICATION_PANEL = 5
SC_TYPE_COLLAPSE_PANELS = 7

# MotionEvent actions (Android KeyEvent.ACTION_*)
SC_ACTION_DOWN = 0
SC_ACTION_UP = 1
SC_ACTION_MOVE = 2

# AKEY_EVENT_ACTION_* mirror Android KeyEvent.ACTION_DOWN/UP (0/1).
# scrcpy 1.x ControlSender의 inject_keycode와 동일 의미.


# ----------------------------------------------------------------------
# PyAV 의존성 — 백엔드 활성 조건
# ----------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def detect_av() -> bool:
    """PyAV (av) 라이브러리 가용 여부. 미설치 시 scrcpy 백엔드 비활성 → screencap 폴백."""
    try:
        import av  # noqa: F401
        return True
    except ImportError:
        logger.info(
            "PyAV (av) not installed — scrcpy backend disabled, "
            "screencap PNG polling fallback will be used. "
            "Install with: pip install av"
        )
        return False


@functools.lru_cache(maxsize=1)
def detect_cv2() -> bool:
    """cv2 가용 여부. JPEG 인코딩에 필수."""
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        logger.info("cv2 not installed — scrcpy backend disabled")
        return False


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
    """기동 시 한 번 호출 — scrcpy-server.jar + PyAV 가용성 로그."""
    jar = detect_scrcpy_server()
    av_ok = detect_av()
    cv2_ok = detect_cv2()
    if jar and av_ok and cv2_ok:
        try:
            size = os.path.getsize(jar)
        except OSError:
            size = 0
        logger.info(
            "scrcpy backend ready: path=%s size=%d (PyAV+cv2 decode)",
            jar, size,
        )
    else:
        reasons = []
        if not jar:
            reasons.append("scrcpy-server.jar not found")
        if not av_ok:
            reasons.append("PyAV(av) not installed")
        if not cv2_ok:
            reasons.append("cv2 not installed")
        logger.info(
            "scrcpy backend disabled (%s) — screencap PNG fallback will be used.",
            ", ".join(reasons),
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


class ScrcpyServerBackend:
    """scrcpy-server.jar → PyAV (H.264 디코딩) → cv2 JPEG 인코딩 파이프라인.

    하나의 인스턴스는 (serial, logical_id) 조합 하나에 1:1 대응.

    동작 모델:
      * asyncio task가 socket에서 H.264 chunk 비동기 read
      * 백엔드 전용 single-thread executor에 디코딩+인코딩 위임 (codec context는
        스레드 안전성 보장 없음 → 워커 1개로 고정해 race 차단)
      * 인코딩된 JPEG bytes를 asyncio.Queue에 put → stream_jpeg()에서 yield
    """

    name = "scrcpy_server"

    def __init__(
        self,
        serial: str,
        logical_id: Optional[int] = None,
        *,
        bitrate: int = 4_000_000,
        max_fps: int = 0,
        jpeg_quality: int = _JPEG_QUALITY,
        enable_control: bool = True,
        resolution_provider: Optional[Callable[[], Tuple[int, int]]] = None,
    ):
        """
        enable_control: control=true 모드로 server 기동 (입력 채널 활성).
        resolution_provider: 디바이스 해상도 (w, h)를 동기 반환하는 callable.
            scrcpy v1.x INJECT_TOUCH_EVENT 패킷에 디바이스 해상도가 필요하지만
            raw_video_stream=true 모드에서는 video stream 헤더에 해상도가 없으므로
            외부(ADBService.wm size)에서 주입받는다. None이면 control 비활성화.
        """
        self.serial = serial
        self.logical_id = logical_id or 0
        self.bitrate = bitrate
        self.max_fps = max_fps
        self.jpeg_quality = jpeg_quality
        # control은 resolution_provider가 있을 때만 의미 있음
        self.enable_control = enable_control and resolution_provider is not None
        self._resolution_provider = resolution_provider
        # scrcpy v1.x는 single-instance 설계 — socket name "scrcpy" 고정, scid 옵션 없음.
        self.local_port = 0
        self._server_proc: Optional[subprocess.Popen] = None
        # adb reverse 방식: PC가 TCP listen, 디바이스 server.jar가 connect 옴.
        # control=true 모드에서는 두 개의 connection이 순차로 들어온다:
        # 첫 번째 = video socket, 두 번째 = control socket.
        self._listener: Optional[asyncio.base_events.Server] = None
        self._video_accept_event: Optional[asyncio.Event] = None
        self._control_accept_event: Optional[asyncio.Event] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._control_reader: Optional[asyncio.StreamReader] = None
        self._control_writer: Optional[asyncio.StreamWriter] = None
        self._control_sender: Optional[ControlSender] = None
        # 디코더 task와 디코더 전용 single-thread executor
        self._decoder_task: Optional[asyncio.Task] = None
        self._decoder_executor: Optional[ThreadPoolExecutor] = None
        # 인코딩된 JPEG 큐 — maxsize=2로 backpressure (디코더 < 소비자 속도 차 흡수)
        self._jpeg_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._first_frame_event: asyncio.Event = asyncio.Event()
        self._first_frame: Optional[bytes] = None
        self._closed = False
        # 진단용 stdout/stderr tail
        self._stderr_tail: bytearray = bytearray()
        self._stdout_tail: bytearray = bytearray()
        # 디코딩 통계 (진단용)
        self._total_bytes_in: int = 0
        self._total_frames_decoded: int = 0

    @property
    def control(self) -> Optional["ControlSender"]:
        """control_socket 기반 입력 sender. enable_control이 False거나 아직
        connection 미수립이면 None."""
        return self._control_sender

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def try_start(
        self, first_frame_timeout: float = _FIRST_FRAME_TIMEOUT,
    ) -> bool:
        """시작 + 첫 프레임 수신 검증. 실패 시 cleanup하고 False."""
        if not detect_av() or not detect_cv2():
            return False
        jar = detect_scrcpy_server()
        if not jar:
            return False

        try:
            # 1) jar push (해시 동일 시 skip)
            if not await self._push_jar(jar):
                return False
            # 2) PC에서 TCP listen 시작 (asyncio.start_server)
            if not await self._setup_reverse_listener():
                return False
            # 3) adb reverse 등록 — device의 localabstract:scrcpy → PC TCP
            if not await self._setup_reverse():
                return False
            # 4) server 프로세스 실행 (백그라운드)
            if not await self._spawn_server():
                return False
            # 5) 디바이스가 우리에게 connect 올 때까지 대기
            if not await self._accept_socket():
                return False
            # 6) PyAV 디코더 task 시작
            if not self._start_decoder_task():
                return False
            # 7) 첫 프레임 검증 — queue에 첫 JPEG가 들어올 때까지
            await asyncio.wait_for(
                self._first_frame_event.wait(), timeout=first_frame_timeout,
            )
            if self._jpeg_queue.empty():
                raise RuntimeError("first_frame event set but queue empty")
            # 첫 프레임을 미리 꺼내둠 — stream_jpeg가 이걸 먼저 yield
            self._first_frame = await self._jpeg_queue.get()
        except (asyncio.TimeoutError, Exception) as e:
            sr_err = self._stderr_tail_str()
            sr_out = self._stdout_tail_str()
            srv_rc = self._server_proc.poll() if self._server_proc else None
            logger.info(
                "scrcpy first-frame check failed (serial=%s display=%s): %s "
                "server_rc=%s bytes_in=%d frames=%d server_out=%r server_err=%r",
                self.serial, self.logical_id, type(e).__name__,
                srv_rc, self._total_bytes_in, self._total_frames_decoded,
                sr_out, sr_err,
            )
            await self.close()
            return False

        logger.info(
            "scrcpy backend started: serial=%s display=%s port=%d bitrate=%d (v%s, PyAV)",
            self.serial, self.logical_id, self.local_port, self.bitrate,
            SCRCPY_VERSION,
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

    async def _setup_reverse_listener(self) -> bool:
        """PC에서 TCP listen 시작. server.jar의 connect를 받는다.

        control=true 모드에서는 2개의 connection이 순서대로 들어온다:
          1. video socket
          2. control socket
        control=false 모드에서는 1개만 들어옴.
        """
        self._video_accept_event = asyncio.Event()
        self._control_accept_event = asyncio.Event()

        async def _on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            if self._reader is None:
                # 첫 번째 = video socket
                self._reader = reader
                self._writer = writer
                if self._video_accept_event:
                    self._video_accept_event.set()
            elif self.enable_control and self._control_reader is None:
                # 두 번째 = control socket
                self._control_reader = reader
                self._control_writer = writer
                if self._control_accept_event:
                    self._control_accept_event.set()
            else:
                # 추가 connect는 거절 (single instance / control 비활성).
                try:
                    writer.close()
                except Exception:
                    pass

        try:
            self._listener = await asyncio.start_server(_on_client, "127.0.0.1", 0)
            sockets = self._listener.sockets
            if not sockets:
                return False
            self.local_port = sockets[0].getsockname()[1]
            return True
        except Exception as e:
            logger.warning("scrcpy listener setup failed (%s): %s", self.serial, e)
            return False

    async def _setup_reverse(self) -> bool:
        """adb reverse 등록. 디바이스의 localabstract:scrcpy → PC tcp:<local_port>."""
        loop = asyncio.get_event_loop()
        cmd = [
            ADB_PATH, "-s", self.serial, "reverse",
            "localabstract:scrcpy",
            f"tcp:{self.local_port}",
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
            logger.warning("adb reverse failed (%s): %s", self.serial, e)
            return False
        if result.returncode != 0:
            logger.warning(
                "adb reverse failed (%s): %s",
                self.serial, result.stderr.decode(errors="replace").strip(),
            )
            return False
        return True

    async def _remove_reverse(self) -> None:
        loop = asyncio.get_event_loop()
        cmd = [
            ADB_PATH, "-s", self.serial, "reverse", "--remove",
            "localabstract:scrcpy",
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
        """app_process 명령 구성 — scrcpy v1.25 CLI 호환 옵션 셋.

        주요 옵션:
          * tunnel_forward=false: adb reverse 사용 (PC listen, device connect)
            HMG IVI 같은 컨테이너 환경에서 forward 방향 socket binding이 막혀있어
            반대 방향인 reverse가 통하는 경우가 많다 (CLI 검증됨).
          * control=false: 입력 채널 비활성 (현재는 ADB input 경로 사용 — Phase 3에서 변경)
          * power_off_on_close=false: 우리 close 시 디바이스 화면 꺼지지 않게
          * raw_video_stream=true: prefix bytes(dummy 1 + device_meta 64) +
            frame_meta(12/frame) 모두 비활성화. PyAV가 raw H.264 NAL stream을 바로
            디코딩 가능.
          * codec_options=i-frame-interval=1: 1초마다 IDR 키프레임 강제. 정적 화면
            디바이스에서 첫 IDR 대기로 인한 first-frame timeout 방지.

        scrcpy v1.25는 crop/codec_options/encoder_name 옵션에 "-" sentinel을 받지
        않는다 ("Crop must contains 4 values separated by colons: -" 에러). cli도
        user 미명시 시 이들을 안 보내므로 우리도 옵션 자체를 생략.
        """
        opts = [
            "log_level=info",
            f"bit_rate={self.bitrate}",
            "max_size=0",
            f"max_fps={self.max_fps}",
            "lock_video_orientation=-1",
            "tunnel_forward=false",
            f"control={'true' if self.enable_control else 'false'}",
            f"display_id={self.logical_id}",
            "show_touches=false",
            "stay_awake=false",
            "power_off_on_close=false",
            "raw_video_stream=true",
            "codec_options=i-frame-interval=1",
        ]
        inner = (
            f"CLASSPATH={DEVICE_JAR_PATH} "
            f"app_process / com.genymobile.scrcpy.Server {SCRCPY_VERSION} "
            + " ".join(opts)
        )
        return [ADB_PATH, "-s", self.serial, "shell", inner]

    async def _spawn_server(self) -> bool:
        """server 프로세스를 백그라운드로 spawn. stdout/stderr 모두 진단용으로 캡처.

        scrcpy의 app_process는 일부 로그를 stdout, 일부를 stderr로 출력하므로
        한 쪽만 받으면 단서를 놓칠 수 있음.
        """
        try:
            self._server_proc = subprocess.Popen(
                self._build_server_cmd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
                bufsize=0,
            )
        except Exception as e:
            logger.warning("scrcpy server spawn failed (%s): %s", self.serial, e)
            return False

        # stdout/stderr를 각각 백그라운드로 drain (pipe 막힘 방지 + 진단용).
        import threading
        threading.Thread(target=self._drain_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        return True

    def _drain_stdout(self) -> None:
        proc = self._server_proc
        if not proc or not proc.stdout:
            return
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                self._stdout_tail.extend(chunk)
                if len(self._stdout_tail) > 4096:
                    del self._stdout_tail[:-2048]
        except Exception:
            pass

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

    def _tail_str(self, buf: bytearray) -> str:
        if not buf:
            return ""
        try:
            raw = bytes(buf[-1024:])
            text = raw.decode("utf-8", errors="replace").strip()
            return " | ".join(line.strip() for line in text.splitlines() if line.strip())
        except Exception:
            return ""

    def _stderr_tail_str(self) -> str:
        return self._tail_str(self._stderr_tail)

    def _stdout_tail_str(self) -> str:
        return self._tail_str(self._stdout_tail)

    async def _accept_socket(self) -> bool:
        """디바이스 server.jar가 우리 PC로 connect 올 때까지 대기.

        adb reverse 방식이라 connect 시작 주체는 디바이스. server.jar가 시작 후
        localabstract:scrcpy로 connect → adb reverse가 우리 TCP listen으로 forward.

        control=true인 경우 video socket이 먼저 들어오고 control socket이 뒤따른다.
        control socket이 늦게 들어와도 video stream은 별개라 즉시 시작 가능 —
        control_socket은 일정 시간 폴링하면서 best-effort로 대기.
        """
        if not self._video_accept_event:
            return False
        try:
            # server 기동 + connect 까지 약 3초 안에 들어옴. 여유 5초.
            await asyncio.wait_for(self._video_accept_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.info(
                "scrcpy accept timed out (%s): server_err=%s",
                self.serial, self._stderr_tail_str(),
            )
            return False
        if self._reader is None:
            return False

        # control socket은 video 직후 들어옴 — 짧은 대기 후 best-effort. control 미수신
        # 시에도 video stream은 정상 동작하므로 fail로 처리하지 않음.
        if self.enable_control and self._control_accept_event:
            try:
                await asyncio.wait_for(self._control_accept_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.info(
                    "scrcpy control socket not received within 2s (%s) — "
                    "video only, falling back to ADB input",
                    self.serial,
                )
            if self._control_writer is not None and self._resolution_provider is not None:
                self._control_sender = ControlSender(
                    self._control_writer, self._resolution_provider,
                )
        return True

    # ------------------------------------------------------------------
    # PyAV 디코딩 파이프라인
    # ------------------------------------------------------------------

    def _start_decoder_task(self) -> bool:
        """디코더 task 시작 — socket reader → PyAV → JPEG → queue."""
        if not self._reader:
            return False
        # codec context는 thread-safe가 아니므로 single-worker executor 사용.
        self._decoder_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"scrcpy-decode-{self.serial[:8]}",
        )
        self._decoder_task = asyncio.create_task(self._decoder_loop())
        return True

    async def _decoder_loop(self) -> None:
        """async: socket에서 H.264 chunk를 읽고 executor로 디코딩 위임.

        디코딩+JPEG 인코딩은 CPU bound이므로 이벤트 루프 차단을 피하기 위해
        백엔드 전용 single-thread executor에서 실행.
        """
        import av  # detect_av로 이미 확인됨
        # CodecContext는 같은 스레드에서만 사용해야 안전 → executor 워커 1개 보장.
        codec = av.CodecContext.create("h264", "r")

        loop = asyncio.get_event_loop()
        reader = self._reader
        executor = self._decoder_executor
        if reader is None or executor is None:
            return

        try:
            while not self._closed:
                try:
                    chunk = await reader.read(_READ_CHUNK)
                except (asyncio.CancelledError, GeneratorExit):
                    raise
                except Exception as e:
                    logger.debug("scrcpy socket read error: %s", e)
                    break
                if not chunk:
                    # EOF — server.jar 종료 또는 disconnect
                    break
                self._total_bytes_in += len(chunk)

                # CPU bound 디코딩+인코딩을 executor로 위임.
                try:
                    jpegs = await loop.run_in_executor(
                        executor, _decode_chunk_to_jpegs,
                        codec, chunk, self.jpeg_quality,
                    )
                except Exception as e:
                    logger.debug("scrcpy decode error: %s", e)
                    continue

                for jpeg in jpegs:
                    self._total_frames_decoded += 1
                    # backpressure: queue가 가득 차면 가장 오래된 프레임 드롭.
                    # 라이브 스트림에서 stale 프레임은 가치가 낮으므로 drop이 정답.
                    if self._jpeg_queue.full():
                        try:
                            self._jpeg_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    try:
                        self._jpeg_queue.put_nowait(jpeg)
                    except asyncio.QueueFull:
                        pass
                    if not self._first_frame_event.is_set():
                        self._first_frame_event.set()
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as e:
            logger.debug("scrcpy decoder loop error: %s", e)
        finally:
            # EOF/에러 시 sentinel 넣어 stream_jpeg가 깔끔히 종료되도록.
            try:
                self._jpeg_queue.put_nowait(_EOF_SENTINEL)
            except asyncio.QueueFull:
                # 큐 가득 → 하나 비우고 sentinel 삽입
                try:
                    self._jpeg_queue.get_nowait()
                    self._jpeg_queue.put_nowait(_EOF_SENTINEL)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_jpeg(self) -> AsyncIterator[bytes]:
        """JPEG 프레임 yield.

        디코더 task가 _jpeg_queue에 넣는 프레임을 그대로 소비.
        idle (_IDLE_FRAME_TIMEOUT 동안 새 frame 없음) 시 마지막 프레임 재전송.
        디코더 종료(EOF/에러) 시 sentinel 받아 자연 종료.
        """
        first = self._first_frame
        last_frame: Optional[bytes] = None
        if first is not None:
            self._first_frame = None
            last_frame = first
            yield first

        try:
            while not self._closed:
                try:
                    item = await asyncio.wait_for(
                        self._jpeg_queue.get(), timeout=_IDLE_FRAME_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    if last_frame is not None:
                        yield last_frame
                    continue

                if item is _EOF_SENTINEL:
                    break
                last_frame = item
                yield item
        except (asyncio.CancelledError, GeneratorExit):
            raise

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """idempotent 완전 종료. 재사용 불가."""
        if self._closed:
            return
        self._closed = True

        # 1) decoder task cancel — socket read를 깨운다
        if self._decoder_task and not self._decoder_task.done():
            self._decoder_task.cancel()
            try:
                await self._decoder_task
            except (asyncio.CancelledError, Exception):
                pass
        self._decoder_task = None

        # 2) executor shutdown
        if self._decoder_executor is not None:
            try:
                self._decoder_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._decoder_executor = None

        # 3) control socket close (있을 때만)
        self._control_sender = None
        if self._control_writer:
            try:
                self._control_writer.close()
                try:
                    await asyncio.wait_for(self._control_writer.wait_closed(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            except Exception:
                pass
            self._control_writer = None
        self._control_reader = None

        # 4) video socket close → server.jar가 자연 종료
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

        # 5) server 프로세스 종료
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

        # 6) PC listener 종료
        if self._listener:
            try:
                self._listener.close()
                await self._listener.wait_closed()
            except Exception:
                pass
            self._listener = None

        # 7) 디바이스 측 잔존 app_process 정리
        await self._cleanup_device_side()

        # 8) adb reverse 제거
        await self._remove_reverse()

        logger.info(
            "scrcpy backend closed: serial=%s bytes_in=%d frames=%d",
            self.serial, self._total_bytes_in, self._total_frames_decoded,
        )

    async def _cleanup_device_side(self) -> None:
        """디바이스에 남아있을지 모를 scrcpy/screenrecord 프로세스 정리.

        v1.x는 single-instance(socket name "scrcpy" 고정)라 scid 구분 안 함.
        같은 디바이스의 HW 인코더 자원을 다른 백엔드도 쓸 수 있어 cross-cleanup.
        """
        loop = asyncio.get_event_loop()
        patterns = [
            "scrcpy.Server",   # scrcpy 서버 인스턴스 (어떤 것이든)
            "screenrecord",    # 다른 백엔드의 stale (cross-cleanup)
        ]
        for pat in patterns:
            cmd = [ADB_PATH, "-s", self.serial, "shell", "pkill", "-f", pat]
            try:
                await loop.run_in_executor(
                    None,
                    lambda c=cmd: subprocess.run(
                        c, capture_output=True, timeout=2,
                        creationflags=_NO_WINDOW,
                    ),
                )
            except Exception:
                pass

    def is_alive(self) -> bool:
        return (
            not self._closed
            and self._decoder_task is not None
            and not self._decoder_task.done()
            and self._server_proc is not None
            and self._server_proc.poll() is None
        )


# ----------------------------------------------------------------------
# 디코딩 worker (executor 스레드에서 실행)
# ----------------------------------------------------------------------

# stream_jpeg의 정상 종료 sentinel.
_EOF_SENTINEL: object = object()


def _decode_chunk_to_jpegs(codec, chunk: bytes, jpeg_quality: int) -> list[bytes]:
    """단일 H.264 chunk → JPEG 프레임 리스트.

    같은 codec context를 반복 호출해야 SPS/PPS 컨텍스트가 유지된다. ThreadPoolExecutor
    worker가 1개이므로 race 없음.

    raw H.264 NAL stream에서 chunk 경계는 NAL 경계와 일치하지 않을 수 있어,
    codec.parse(chunk)로 demuxer에 일임해 packet 단위로 정리한 뒤 decode.
    """
    import av
    import cv2

    out: list[bytes] = []
    try:
        packets = codec.parse(chunk)
    except av.InvalidDataError:
        return out
    except Exception:
        return out

    for packet in packets:
        try:
            frames = codec.decode(packet)
        except av.InvalidDataError:
            continue
        except Exception:
            continue
        for frame in frames:
            try:
                arr = frame.to_ndarray(format="bgr24")
            except Exception:
                continue
            try:
                ok, buf = cv2.imencode(
                    ".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
                )
            except Exception:
                continue
            if ok:
                out.append(bytes(buf))
    return out


# ----------------------------------------------------------------------
# ControlSender — scrcpy v1.x control_socket 입력 채널
# ----------------------------------------------------------------------

class ControlSender:
    """scrcpy v1.x ControlMessage wire format으로 입력 이벤트 전송.

    동작 방식:
      * 모든 send 메서드는 asyncio.Lock으로 직렬화 — 동시 다발 호출 시 패킷 인터리브 방지
      * 디바이스 해상도가 touch 패킷에 인코딩되어야 하므로 외부 provider로부터 주입
        (raw_video_stream=true 모드라 video 헤더에 해상도가 없음)

    wire format 참고 (scrcpy v1.25 ControlMessageReader.java):

      INJECT_KEYCODE:
        type(1) + action(1) + keycode(4) + repeat(4) + metastate(4) = 14 bytes
      INJECT_TEXT:
        type(1) + text_len(4) + text(utf-8 bytes)
      INJECT_TOUCH_EVENT:
        type(1) + action(1) + pointerId(8) + x(4) + y(4)
        + screenWidth(2) + screenHeight(2) + pressure(2) + buttons(4) = 28 bytes
      INJECT_SCROLL_EVENT:
        type(1) + x(4) + y(4) + screenWidth(2) + screenHeight(2)
        + hScroll(4) + vScroll(4) = 21 bytes
      BACK_OR_SCREEN_ON:
        type(1) + action(1) = 2 bytes
    """

    # 기본 pointer ID (single-touch 가상 손가락).
    DEFAULT_POINTER_ID = -1

    def __init__(
        self,
        writer: asyncio.StreamWriter,
        resolution_provider: Callable[[], Tuple[int, int]],
    ):
        self._writer = writer
        self._resolution_provider = resolution_provider
        self._lock = asyncio.Lock()
        self._closed = False

    def is_alive(self) -> bool:
        return not self._closed and not self._writer.is_closing()

    async def _send(self, payload: bytes) -> bool:
        """단일 패킷 직렬화 송신. socket 닫힘 등 실패 시 False."""
        if self._closed or self._writer.is_closing():
            return False
        async with self._lock:
            try:
                self._writer.write(payload)
                await self._writer.drain()
                return True
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                logger.debug("ControlSender send failed: %s", e)
                self._closed = True
                return False

    async def touch(
        self,
        x: int,
        y: int,
        action: int = SC_ACTION_DOWN,
        pointer_id: int = DEFAULT_POINTER_ID,
        pressure: int = 0xFFFF,
        buttons: int = 1,
    ) -> bool:
        """터치 이벤트. action: SC_ACTION_DOWN/UP/MOVE."""
        w, h = self._resolution_provider()
        if w <= 0 or h <= 0:
            return False
        x = max(0, min(int(x), w - 1))
        y = max(0, min(int(y), h - 1))
        # >BBqiiHHHi = type(1) + action(1) + pointer_id(8) + x(4) + y(4)
        #             + w(2) + h(2) + pressure(2) + buttons(4) = 28 bytes
        pkt = struct.pack(
            ">BBqiiHHHi",
            SC_TYPE_INJECT_TOUCH_EVENT, action & 0xFF,
            int(pointer_id), x, y,
            w & 0xFFFF, h & 0xFFFF,
            pressure & 0xFFFF, buttons,
        )
        return await self._send(pkt)

    async def tap(self, x: int, y: int, hold_ms: int = 50) -> bool:
        """DOWN → hold_ms 대기 → UP. tap 시퀀스 송신.

        hold_ms는 0 또는 너무 작으면 Android가 tap으로 인식하지 못하고 hover로
        처리하거나 아예 무시하는 디바이스가 있다 (특히 ViewConfiguration의 tap
        timeout 검사). 50ms 정도가 일반 사용자 입력과 가장 가까운 값.
        """
        if not await self.touch(x, y, SC_ACTION_DOWN):
            return False
        if hold_ms > 0:
            await asyncio.sleep(hold_ms / 1000.0)
        return await self.touch(x, y, SC_ACTION_UP)

    async def long_press(self, x: int, y: int, duration_ms: int = 1000) -> bool:
        """DOWN → duration 만큼 sleep → UP."""
        if not await self.touch(x, y, SC_ACTION_DOWN):
            return False
        await asyncio.sleep(duration_ms / 1000.0)
        return await self.touch(x, y, SC_ACTION_UP)

    async def swipe(
        self,
        x1: int, y1: int, x2: int, y2: int,
        duration_ms: int = 300,
        steps: int = 0,
    ) -> bool:
        """단일 핑거 스와이프 — DOWN → MOVE×N → UP.

        scrcpy v1.x control_socket에는 native swipe primitive가 없으므로 직접 합성.
        멀티핑거나 정교한 sendevent가 필요한 케이스는 호출자가 ADB 폴백으로 보냄.
        """
        if steps <= 0:
            # 약 16ms (60fps) 간격으로 자동 결정
            steps = max(2, min(60, duration_ms // 16))
        if not await self.touch(x1, y1, SC_ACTION_DOWN):
            return False
        sleep_s = duration_ms / 1000.0 / steps
        for i in range(1, steps + 1):
            t = i / steps
            ix = int(x1 + (x2 - x1) * t)
            iy = int(y1 + (y2 - y1) * t)
            if not await self.touch(ix, iy, SC_ACTION_MOVE):
                return False
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
        return await self.touch(x2, y2, SC_ACTION_UP)

    async def keycode(
        self,
        keycode: int,
        action: int = SC_ACTION_DOWN,
        repeat: int = 0,
        metastate: int = 0,
    ) -> bool:
        """Android KeyEvent.* 키코드 주입.

        wire: type(1) + action(1) + keycode(4) + repeat(4) + metastate(4)
        scrcpy 1.x ControlMessageReader는 keycode와 metastate를 int(signed 32)로 읽음.
        """
        pkt = struct.pack(
            ">BBiii",
            SC_TYPE_INJECT_KEYCODE, action & 0xFF,
            int(keycode), int(repeat), int(metastate),
        )
        return await self._send(pkt)

    async def key_press(self, keycode: int) -> bool:
        """DOWN + UP을 즉시 송신."""
        if not await self.keycode(keycode, SC_ACTION_DOWN):
            return False
        return await self.keycode(keycode, SC_ACTION_UP)

    async def back_or_screen_on(self, action: int = SC_ACTION_DOWN) -> bool:
        """BACK 키 (화면 꺼져있으면 DOWN 시 화면만 켜짐).

        wire: type(1) + action(1) = 2 bytes
        """
        pkt = struct.pack(">BB", SC_TYPE_BACK_OR_SCREEN_ON, action & 0xFF)
        return await self._send(pkt)

    async def text(self, text: str) -> bool:
        """UTF-8 텍스트 주입. 길이 prefix(int32) + 본문 bytes.

        wire: type(1) + len(4) + utf8_bytes
        """
        buf = text.encode("utf-8")
        pkt = struct.pack(">Bi", SC_TYPE_INJECT_TEXT, len(buf)) + buf
        return await self._send(pkt)
