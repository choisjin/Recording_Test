"""라이브 미러링 캡처 백엔드 패키지.

이 패키지의 백엔드는 **라이브 화면 미러링 전용**이다.
검증/녹화 캡처(image diff용 원본 PNG)는 절대 이쪽 경로를 타지 않는다.

설계 원칙:
- 모든 백엔드는 시스템 ffmpeg 바이너리에 의존하며, 미설치 시 None 반환으로
  비활성화되어 호출자가 screencap 폴백으로 자연 전환된다.
- 미러링은 디바이스당 동시에 한 디스플레이만 활성. 인스턴스 풀은 단순한
  dict[serial] 형태로 유지.
- 추후 Linux X11/Wayland/DRM/KMS 등 다른 캡처 소스도 동일 추상화로 추가한다.
"""
from .ffmpeg_runtime import detect_ffmpeg, ffmpeg_version, log_runtime_status
from .ffmpeg_pipe import FFmpegMjpegPipe
from .adb_screenrecord import AdbScreenrecordBackend

__all__ = [
    "detect_ffmpeg",
    "ffmpeg_version",
    "log_runtime_status",
    "FFmpegMjpegPipe",
    "AdbScreenrecordBackend",
]
