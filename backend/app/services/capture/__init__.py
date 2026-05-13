"""라이브 미러링 캡처 백엔드 패키지.

이 패키지의 백엔드는 라이브 화면 미러링 전용이다.
"""
from .ffmpeg_runtime import detect_ffmpeg, ffmpeg_version, log_runtime_status
from .ffmpeg_pipe import FFmpegMjpegPipe
from .adb_screenrecord import AdbScreenrecordBackend

__all__ = [
    "detect_ffmpeg", "ffmpeg_version", "log_runtime_status",
    "FFmpegMjpegPipe", "AdbScreenrecordBackend",
]
