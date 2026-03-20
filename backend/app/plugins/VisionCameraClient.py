# -*- coding: utf-8 -*-
"""VisionCamera 클라이언트 — harvesters + Vimba GenTL 기반.

백그라운드 스레드에서 지속적으로 프레임을 캡처하여
최신 프레임을 즉시 반환하는 방식으로 동작.
"""

import io
import os
import glob
import logging
import threading
import time
import numpy as np
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)


def _find_cti_files() -> list[str]:
    """Vimba X GenTL producer (.cti) 파일을 자동 탐색."""
    search_dirs = [
        r"C:\Program Files\Allied Vision\Vimba X\cti",
        r"C:\Program Files\Allied Vision\VimbaX\cti",
        r"C:\Program Files (x86)\Allied Vision\Vimba X\cti",
    ]
    for var in ("VIMBA_X_HOME", "VIMBA_HOME"):
        val = os.environ.get(var, "")
        if val:
            search_dirs.append(os.path.join(val, "cti"))
    gentl = os.environ.get("GENICAM_GENTL64_PATH", "")
    if gentl:
        search_dirs.extend(gentl.split(os.pathsep))

    cti_files = []
    for d in search_dirs:
        if os.path.isdir(d):
            cti_files.extend(glob.glob(os.path.join(d, "*.cti")))
    return list(set(cti_files))


def _component_to_pil(comp) -> Image.Image:
    """harvesters component → PIL Image (RGB)."""
    data = comp.data
    w, h = comp.width, comp.height

    if data.ndim == 1:
        if w * h == len(data):
            img_arr = data.reshape(h, w)
        else:
            channels = len(data) // (w * h)
            img_arr = data.reshape(h, w, channels)
    else:
        img_arr = data

    if img_arr.ndim == 2:
        return Image.fromarray(img_arr, 'L').convert('RGB')
    elif img_arr.shape[2] == 1:
        return Image.fromarray(img_arr[:, :, 0], 'L').convert('RGB')
    elif img_arr.shape[2] == 3:
        return Image.fromarray(img_arr, 'RGB')
    else:
        return Image.fromarray(img_arr[:, :, :3], 'RGB')


class VisionCameraClient:
    """harvesters 기반 GigE Vision 카메라 제어.

    백그라운드 스레드에서 지속 캡처 → 최신 프레임 즉시 반환.
    """

    def __init__(self, model: str, port: dict, context=None):
        self._device = model
        self._isConnected = False
        self._macaddress = port.get("MACAddress", "")
        self._device_id = f"DEV_{self._macaddress}"

        self._harvester = None
        self._ia = None

        # 백그라운드 프레임 캡처
        self._frame_thread = None
        self._frame_stop = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: Image.Image | None = None  # 최신 PIL Image (RGB)

        self._cti_files = _find_cti_files()
        if not self._cti_files:
            raise FileNotFoundError(
                "GenTL producer (.cti) 파일을 찾을 수 없습니다. "
                "Vimba X SDK가 설치되어 있는지 확인하세요."
            )
        logger.info("VisionCameraClient: mac=%s, CTI files=%d",
                     self._macaddress, len(self._cti_files))

    # ------------------------------------------------------------------
    # 백그라운드 프레임 캡처 스레드
    # ------------------------------------------------------------------

    def _frame_loop(self):
        """백그라운드에서 지속적으로 프레임을 fetch하여 _latest_frame 갱신."""
        logger.info("VisionCamera frame loop started")
        while not self._frame_stop.is_set():
            if not self._ia:
                time.sleep(0.5)
                continue
            try:
                with self._ia.fetch(timeout=3) as buffer:
                    comp = buffer.payload.components[0]
                    img = _component_to_pil(comp)
                    with self._frame_lock:
                        self._latest_frame = img
            except Exception:
                # fetch 타임아웃 또는 일시적 오류 — 재시도
                time.sleep(0.1)
        logger.info("VisionCamera frame loop stopped")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def md_VisionConnect(self) -> tuple[bool, str]:
        """카메라 연결 + 백그라운드 캡처 시작."""
        if self._isConnected and self._ia:
            return True, "[VisionCamera] Already connected"

        try:
            from harvesters.core import Harvester

            self._harvester = Harvester()
            for cti in self._cti_files:
                self._harvester.add_file(cti)
            self._harvester.update()

            # MAC 주소로 카메라 찾기
            idx = None
            for i, info in enumerate(self._harvester.device_info_list):
                if self._device_id in info.id_ or self._macaddress in info.id_:
                    idx = i
                    break

            if idx is None:
                cam_list = [info.id_ for info in self._harvester.device_info_list]
                self._harvester.reset()
                self._harvester = None
                return False, (
                    f"[VisionCamera] 카메라 {self._device_id} 를 찾을 수 없습니다. "
                    f"검색된 카메라: {cam_list}"
                )

            self._ia = self._harvester.create(idx)
            self._ia.start()
            self._isConnected = True

            # 백그라운드 프레임 캡처 스레드 시작
            self._frame_stop.clear()
            self._frame_thread = threading.Thread(
                target=self._frame_loop, name="visioncam_frame", daemon=True
            )
            self._frame_thread.start()

            logger.info("VisionCamera connected: %s", self._device_id)
            return True, f"[VisionCamera] Connect OK ({self._device_id})"

        except Exception as e:
            self._cleanup()
            return False, f"[VisionCamera] Connect fail: {e}"

    def md_VisionDisconnect(self) -> tuple[bool, str]:
        if not self._isConnected:
            return True, "[VisionCamera] Already disconnected"
        try:
            self._cleanup()
            return True, "[VisionCamera] Disconnect OK"
        except Exception as e:
            return False, f"[VisionCamera] Disconnect fail: {e}"

    def md_IsConnect(self) -> tuple[bool, str]:
        if self._isConnected and self._ia:
            return True, "[VisionCamera] Connected"
        return False, "[VisionCamera] Not connected"

    # ------------------------------------------------------------------
    # Capture — 최신 프레임 즉시 반환 (블로킹 없음)
    # ------------------------------------------------------------------

    def md_VisionCapture(self, szPath: str, left=-1, top=-1, right=-1, bottom=-1) -> tuple[bool, str]:
        """최신 프레임을 파일로 저장."""
        if not self._isConnected:
            return False, "[VisionCamera] Not connected"

        with self._frame_lock:
            img = self._latest_frame

        if img is None:
            return False, "[VisionCamera] No frame available yet"

        try:
            if left >= 0 and top >= 0 and right >= 0 and bottom >= 0:
                img = img.crop((left, top, right, bottom))
            Path(szPath).parent.mkdir(parents=True, exist_ok=True)
            img.save(szPath)
            return True, "[VisionCamera] Capture OK"
        except Exception as e:
            return False, f"[VisionCamera] Capture fail: {e}"

    def md_CaptureBytes(self, fmt: str = "jpeg") -> bytes:
        """최신 프레임을 바이트로 즉시 반환."""
        if not self._isConnected:
            raise RuntimeError("[VisionCamera] Not connected")

        with self._frame_lock:
            img = self._latest_frame

        if img is None:
            raise RuntimeError("[VisionCamera] No frame available yet")

        buf = io.BytesIO()
        if fmt.lower() in ("jpg", "jpeg"):
            img.save(buf, format="JPEG", quality=85)
        else:
            img.save(buf, format="PNG")
        return buf.getvalue()

    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._isConnected

    def _cleanup(self):
        """리소스 정리."""
        self._isConnected = False
        # 프레임 스레드 중지
        self._frame_stop.set()
        if self._frame_thread and self._frame_thread.is_alive():
            self._frame_thread.join(timeout=5)
        self._frame_thread = None
        self._latest_frame = None
        # harvesters 정리
        if self._ia:
            try:
                self._ia.stop()
            except Exception:
                pass
            try:
                self._ia.destroy()
            except Exception:
                pass
            self._ia = None
        if self._harvester:
            try:
                self._harvester.reset()
            except Exception:
                pass
            self._harvester = None

    def dispose(self):
        self._cleanup()
