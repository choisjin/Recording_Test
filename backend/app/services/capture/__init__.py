"""라이브 미러링 캡처 백엔드 패키지.

scrcpy_server는 PyAV 기반 직접 디코딩 사용. ffmpeg_runtime / ffmpeg_pipe는
다른 캡처 백엔드(예: 데스크톱 스크린 캡처)를 위해 유지.
"""
from .ffmpeg_runtime import detect_ffmpeg, ffmpeg_version, log_runtime_status
from .ffmpeg_pipe import FFmpegMjpegPipe
from .scrcpy_server import (
    ScrcpyServerBackend, detect_scrcpy_server, detect_av, log_scrcpy_status,
)
__all__ = [
    "detect_ffmpeg", "ffmpeg_version", "log_runtime_status", "FFmpegMjpegPipe",
    "ScrcpyServerBackend", "detect_scrcpy_server", "detect_av", "log_scrcpy_status",
]
