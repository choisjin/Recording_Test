"""Shared service instances — 모든 라우터에서 동일 인스턴스 사용.

scrcpy H.264 미러링은 ADBService 내부의 ScrcpyServerBackend로 통합되었음
(adb_service.ensure_scrcpy_backend). 별도 ScrcpyManager 싱글톤은 더 이상 사용하지 않음.
"""

from .services.adb_service import ADBService
from .services.device_manager import DeviceManager
from .services.image_compare_service import ImageCompareService
from .services.playback_service import PlaybackService
from .services.recording_service import RecordingService
from .services.monitor_client import MonitorClient

adb_service = ADBService()
device_manager = DeviceManager(adb_service)
image_compare_service = ImageCompareService()
recording_service = RecordingService(adb_service, device_manager)
playback_service = PlaybackService(adb_service, image_compare_service, device_manager)
monitor_client = MonitorClient()
