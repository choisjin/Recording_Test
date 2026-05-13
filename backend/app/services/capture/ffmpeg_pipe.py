"""ffmpeg subprocess를 통해 다양한 캡처 소스를 MJPEG 스트림으로 변환하고,
JPEG 프레임 단위로 비동기 yield하는 공용 헬퍼.

이 클래스는 라이브 미러링 백엔드(AdbScreenrecordBackend, 추후 Linux 캡처 등)에서
입력 소스만 다르고 "→ ffmpeg → MJPEG → JPEG 프레임 분리" 파이프라인은 동일하므로
공통화되어 재사용된다.

두 가지 생성 방식:
    1. from_input_proc(proc, input_fmt="h264")
        이미 spawn된 외부 subprocess(예: `adb exec-out screenrecord -`)의 stdout을
        ffmpeg에 stdin으로 연결.
    2. from_command(cmd)
        ffmpeg 자체가 캡처 소스인 경우(예: `-f x11grab -i :0.0` `-f gdigrab -i desktop`
        `-f kmsgrab -i -` 등). 추후 Linux/Windows 데스크톱 캡처용.

공통 사용 패턴:
    pipe = await FFmpegMjpegPipe.from_input_proc(proc, "h264")
    async for jpeg in pipe:
        await ws.send_bytes(jpeg)
    await pipe.close()

프레임 경계는 JPEG SOI(0xFFD8) ~ EOI(0xFFD9) 마커로 분리한다 — MJPEG 컨테이너가
이 형태로 출력하기 때문이며, ffmpeg `-f mjpeg pipe:1`의 표준 동작이다.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import AsyncIterator, Optional

from .ffmpeg_runtime import detect_ffmpeg

logger = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# JPEG 프레임 경계 마커
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"

# stdout에서 한 번에 읽을 chunk 크기 — 너무 작으면 syscall 폭주,
# 너무 크면 첫 프레임 latency 증가. 64KB가 무난.
_READ_CHUNK = 64 * 1024


class FFmpegMjpegPipe:
    """ffmpeg subprocess를 감싸 JPEG 프레임을 비동기 yield하는 파이프라인.

    인스턴스는 한 번만 iterate (소비형) 가능. 동일 인스턴스 재사용 금지.
    close()는 idempotent.
    """

    def __init__(
        self,
        ffmpeg_proc: asyncio.subprocess.Process,
        input_proc: Optional[subprocess.Popen] = None,
    ):
        self._ff = ffmpeg_proc
        self._input_proc = input_proc  # 있으면 close 시 함께 종료
        self._buf = bytearray()
        self._closed = False
        # ffmpeg stderr는 평소엔 무시되지만 첫 프레임이 안 올 때 진단을 위해
        # 백그라운드로 빨아들여 tail로 보관한다 (파이프 가득 차서 ffmpeg가
        # 블록되는 것도 함께 예방).
        self._stderr_tail: bytearray = bytearray()
        self._stderr_task: Optional[asyncio.Task] = None
        if ffmpeg_proc.stderr is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr())

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    async def from_input_proc(
        cls,
        input_proc: subprocess.Popen,
        input_fmt: str = "h264",
        quality: int = 5,
        extra_in: Optional[list[str]] = None,
        extra_out: Optional[list[str]] = None,
    ) -> "FFmpegMjpegPipe":
        """이미 살아있는 subprocess.Popen의 stdout을 ffmpeg에 stdin으로 연결.

        Args:
            input_proc: stdout=PIPE로 이미 spawn된 subprocess (예: adb screenrecord).
                       이 함수가 fd 소유권을 인수하고 ffmpeg로 넘겨 stdout을 직접
                       소비하므로, 호출자는 이후 input_proc.stdout을 만지면 안 된다.
            input_fmt: ffmpeg `-f` 입력 포맷 (예: "h264", "mp4", "mjpeg").
            quality: ffmpeg `-q:v` 값. 2(최고)~31(최저), 기본 5 ≈ 고품질.
                     라이브 미러링용 권장 5~10.
            extra_in: -i 앞에 끼울 추가 입력 옵션 (예: -framerate).
            extra_out: -f mjpeg 뒤에 끼울 추가 출력 옵션.

        Raises:
            RuntimeError: ffmpeg 미설치 / input_proc.stdout이 None일 때.
        """
        ff = detect_ffmpeg()
        if not ff:
            raise RuntimeError("ffmpeg binary not available (set FFMPEG_PATH or install ffmpeg)")
        if input_proc.stdout is None:
            raise RuntimeError("input_proc.stdout is None — spawn it with stdout=subprocess.PIPE")

        # 입력 옵션 주의사항 (실측 기반):
        #   * "-fflags nobuffer -flags low_delay" 는 raw H.264 elementary stream에서
        #     demuxer의 NAL unit 버퍼링까지 꺼버려 frame 분리가 실패한다 (frame=0).
        #     실시간 latency가 중요하더라도 raw H.264 입력에는 절대 쓰지 말 것.
        #   * "-probesize 32 -analyzeduration 0" 같은 극단값도 SPS/PPS 파싱 실패.
        #   * 적절한 값: probesize 64KB + analyzeduration 0.5초 면 IDR keyframe 안에
        #     충분히 들어오면서 첫 프레임까지 latency도 짧다.
        cmd: list[str] = [
            ff, "-hide_banner", "-loglevel", "error",
            "-probesize", "65536",
            "-analyzeduration", "500000",
            # screenrecord stream에는 오디오가 없지만 ffmpeg는 기본적으로
            # 오디오 stream을 탐색하므로 명시적으로 차단.
            "-an",
        ]
        if extra_in:
            cmd += extra_in
        cmd += ["-f", input_fmt, "-i", "pipe:0",
                "-f", "mjpeg",
                # Android screenrecord의 H.264는 TV-range YUV(yuv420p, limited 16-235)인데
                # MJPEG 표준은 JPEG-range YUV(yuvj420p, full 0-255)다. 명시하지 않으면
                # "Non full-range YUV is non-standard" 에러로 인코더 초기화가 실패한다.
                "-pix_fmt", "yuvj420p",
                # 색상 범위(tv→pc) 변환을 명시적으로 처리해 swscaler의 deprecated 경고 제거.
                "-vf", "scale=in_range=tv:out_range=pc",
                # 패킷이 들어오면 muxer가 즉시 stdout으로 flush — idle 후 wake-up 시
                # 첫 frame을 ffmpeg 내부 buffer에 묶어두지 않고 곧장 내보낸다. wake-up
                # latency를 100~200ms 수준으로 단축하는 데 효과적.
                "-flush_packets", "1",
                "-q:v", str(quality)]
        # 주의: 한 때 `-err_detect ignore_err`를 넣었었는데, 빠른 화면 변화 중 corrupt
        # frame이 발생하면 MJPEG 인코더가 stuck 상태에 빠져 stream이 1fps로 떨어지고
        # 복구가 안 되는 케이스가 발생. 차라리 에러 시 ffmpeg가 정상 종료(EOF)되어 우리
        # 백엔드가 segment restart 또는 폴백 분기를 타도록 두는 게 안전하다.
        if extra_out:
            cmd += extra_out
        cmd.append("pipe:1")

        ffproc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=input_proc.stdout,
            stdout=asyncio.subprocess.PIPE,
            # stderr를 잡아 진단에 활용. 평소엔 버려지지만 첫 프레임이 안 올 때
            # 마지막 메시지로 원인을 식별할 수 있다 (예: Invalid data, no IDR 등).
            stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        # input_proc.stdout fd는 ffmpeg 자식에 상속됐으므로 부모 프로세스에서는
        # 닫아야 ffmpeg가 EOF를 정상 인지하고 깔끔히 종료할 수 있다.
        try:
            input_proc.stdout.close()
        except Exception:
            pass

        return cls(ffproc, input_proc=input_proc)

    @classmethod
    async def from_command(cls, cmd: list[str]) -> "FFmpegMjpegPipe":
        """완결된 ffmpeg 명령을 직접 실행. 출력 MJPEG는 호출자가 cmd에 포함.

        ffmpeg 경로는 자동으로 prepend (cmd가 'ffmpeg'로 시작하면 치환). 예:
            await FFmpegMjpegPipe.from_command([
                "-hide_banner", "-loglevel", "error",
                "-f", "x11grab", "-i", ":0.0",
                "-f", "mjpeg", "-q:v", "5", "pipe:1",
            ])
        """
        ff = detect_ffmpeg()
        if not ff:
            raise RuntimeError("ffmpeg binary not available")
        if cmd and cmd[0] == "ffmpeg":
            cmd = [ff] + cmd[1:]
        elif not cmd or cmd[0] != ff:
            cmd = [ff] + cmd

        ffproc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        return cls(ffproc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        return not self._closed and self._ff.returncode is None

    async def _drain_stderr(self) -> None:
        """ffmpeg stderr를 끝까지 빨아들여 tail에 보관.
        파이프가 가득 차 ffmpeg가 멈추는 것을 막기 위한 필수 동작."""
        if self._ff.stderr is None:
            return
        try:
            while True:
                chunk = await self._ff.stderr.read(4096)
                if not chunk:
                    break
                self._stderr_tail.extend(chunk)
                # 최근 4KB만 유지.
                if len(self._stderr_tail) > 8192:
                    del self._stderr_tail[:-4096]
        except Exception:
            pass

    def stderr_tail(self, max_chars: int = 512) -> str:
        """현재까지 수집된 ffmpeg stderr를 사람이 읽기 좋게 정리해 반환."""
        if not self._stderr_tail:
            return ""
        try:
            raw = bytes(self._stderr_tail[-max_chars:])
            text = raw.decode("utf-8", errors="replace").strip()
            return " | ".join(line.strip() for line in text.splitlines() if line.strip())
        except Exception:
            return repr(bytes(self._stderr_tail[-max_chars:]))

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """JPEG 프레임을 SOI/EOI 마커로 잘라 yield. ffmpeg가 종료하면 자연 정지."""
        if self._ff.stdout is None:
            return
        while not self._closed:
            try:
                chunk = await self._ff.stdout.read(_READ_CHUNK)
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as e:
                logger.debug("ffmpeg stdout read error: %s", e)
                break
            if not chunk:
                break  # EOF — ffmpeg 종료
            self._buf.extend(chunk)

            # 버퍼에서 추출 가능한 모든 프레임 emit
            while True:
                soi = self._buf.find(_SOI)
                if soi < 0:
                    # SOI 없음 — 잔재 폐기 (스트림 시작 전 noise 등)
                    self._buf.clear()
                    break
                if soi > 0:
                    # SOI 앞쪽 garbage 제거
                    del self._buf[:soi]
                eoi = self._buf.find(_EOI, 2)
                if eoi < 0:
                    break  # 프레임 미완성 → 다음 chunk 대기
                frame = bytes(self._buf[: eoi + 2])
                del self._buf[: eoi + 2]
                yield frame

    async def close(self) -> None:
        """idempotent close. 입력 프로세스 → ffmpeg 순서로 정리."""
        if self._closed:
            return
        self._closed = True

        # 1) 입력 프로세스 종료 — ffmpeg가 EOF 보고 자연 종료하도록 유도.
        if self._input_proc and self._input_proc.poll() is None:
            try:
                self._input_proc.terminate()
                try:
                    self._input_proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        self._input_proc.kill()
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("input proc close error: %s", e)

        # 2) ffmpeg 종료. 위에서 EOF로 이미 죽었으면 즉시 wait.
        if self._ff.returncode is None:
            try:
                self._ff.terminate()
                await asyncio.wait_for(self._ff.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try:
                    self._ff.kill()
                    await self._ff.wait()
                except Exception:
                    pass
            except Exception as e:
                logger.debug("ffmpeg close error: %s", e)
