"""Windows process window control service.

PrintWindow + PostMessage 기반으로 다른 Windows 프로세스의 윈도우를 캡처/조작.
디바이스 타입 "wincontrol" 의 백엔드 구현.
"""

from __future__ import annotations

import io
import logging
import time
from ctypes import windll
from typing import Optional

logger = logging.getLogger(__name__)

# Windows에서만 동작 — 모듈 import 자체는 항상 가능하도록 lazy import
_WIN32_AVAILABLE = False
_IMPORT_ERROR: Optional[str] = None
try:
    import win32gui  # type: ignore
    import win32con  # type: ignore
    import win32api  # type: ignore
    import win32process  # type: ignore
    import win32ui  # type: ignore
    import psutil
    from PIL import Image
    _WIN32_AVAILABLE = True
except Exception as _e:  # pragma: no cover — non-Windows
    _IMPORT_ERROR = str(_e)


# 가상키 매핑 (대표 키만 노출 — 추후 필요시 확장)
VK_MAP: dict[str, int] = {}
if _WIN32_AVAILABLE:
    VK_MAP = {
        "ENTER": win32con.VK_RETURN,
        "RETURN": win32con.VK_RETURN,
        "TAB": win32con.VK_TAB,
        "ESC": win32con.VK_ESCAPE,
        "ESCAPE": win32con.VK_ESCAPE,
        "BACKSPACE": win32con.VK_BACK,
        "BACK": win32con.VK_BACK,
        "DELETE": win32con.VK_DELETE,
        "DEL": win32con.VK_DELETE,
        "SPACE": win32con.VK_SPACE,
        "UP": win32con.VK_UP,
        "DOWN": win32con.VK_DOWN,
        "LEFT": win32con.VK_LEFT,
        "RIGHT": win32con.VK_RIGHT,
        "HOME": win32con.VK_HOME,
        "END": win32con.VK_END,
        "PAGEUP": win32con.VK_PRIOR,
        "PAGEDOWN": win32con.VK_NEXT,
        "F1": win32con.VK_F1, "F2": win32con.VK_F2, "F3": win32con.VK_F3,
        "F4": win32con.VK_F4, "F5": win32con.VK_F5, "F6": win32con.VK_F6,
        "F7": win32con.VK_F7, "F8": win32con.VK_F8, "F9": win32con.VK_F9,
        "F10": win32con.VK_F10, "F11": win32con.VK_F11, "F12": win32con.VK_F12,
    }


def _resolve_vk(key: str) -> int:
    """문자열 키 → 가상키 코드. 'A'~'Z'/'0'~'9'는 ord, 그 외는 VK_MAP."""
    if not key:
        raise ValueError("empty key")
    upper = key.upper()
    if upper in VK_MAP:
        return VK_MAP[upper]
    if len(upper) == 1 and (upper.isalpha() or upper.isdigit()):
        return ord(upper)
    raise ValueError(f"Unknown key: {key}")


class WinControlService:
    """단일 Win32 윈도우를 임베드 대상으로 잡고 캡처/입력 처리."""

    def __init__(self) -> None:
        self._hwnd: Optional[int] = None
        self._pid: Optional[int] = None
        self._process_name: str = ""
        self._window_title: str = ""

    @staticmethod
    def is_available() -> bool:
        return _WIN32_AVAILABLE

    @staticmethod
    def import_error() -> Optional[str]:
        return _IMPORT_ERROR

    # ── 프로세스/윈도우 검색 ──────────────────────────────────────────
    def list_processes(self) -> list[dict]:
        """가시 최상위 윈도우 + PID/프로세스명 목록.

        같은 PID의 여러 창은 가장 의미있는 첫 번째 윈도우(타이틀 있음)만 노출.
        """
        if not _WIN32_AVAILABLE:
            return []
        results: dict[int, dict] = {}

        def _cb(hwnd: int, _: object) -> bool:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd)
                if not title:
                    return True
                # Windows shell 등 0px 윈도우 제외
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w <= 0 or h <= 0:
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid in results:
                    return True
                try:
                    proc = psutil.Process(pid)
                    name = proc.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return True
                results[pid] = {
                    "pid": int(pid),
                    "hwnd": int(hwnd),
                    "name": name,
                    "title": title,
                    "width": w,
                    "height": h,
                }
            except Exception as e:
                logger.debug("enum_window callback error: %s", e)
            return True

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception as e:
            logger.warning("EnumWindows failed: %s", e)
        # 보기 좋게 프로세스명/타이틀 순 정렬
        return sorted(results.values(), key=lambda d: (d["name"].lower(), d["title"].lower()))

    # ── 임베드(대상 윈도우) ──────────────────────────────────────────
    def attach(self, hwnd: int) -> dict:
        if not _WIN32_AVAILABLE:
            raise RuntimeError(f"pywin32 not available: {_IMPORT_ERROR}")
        if not win32gui.IsWindow(hwnd):
            raise ValueError(f"Invalid window handle: {hwnd}")
        self._hwnd = int(hwnd)
        try:
            _, self._pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            self._pid = None
        self._window_title = win32gui.GetWindowText(hwnd) or ""
        try:
            self._process_name = psutil.Process(self._pid).name() if self._pid else ""
        except Exception:
            self._process_name = ""
        logger.info("WinControl attached: hwnd=%d pid=%s name=%s title=%r",
                    hwnd, self._pid, self._process_name, self._window_title)
        return self.status()

    def detach(self) -> None:
        logger.info("WinControl detached: hwnd=%s", self._hwnd)
        self._hwnd = None
        self._pid = None
        self._process_name = ""
        self._window_title = ""

    def is_attached(self) -> bool:
        if not _WIN32_AVAILABLE or self._hwnd is None:
            return False
        try:
            return bool(win32gui.IsWindow(self._hwnd))
        except Exception:
            return False

    def status(self) -> dict:
        if not self.is_attached():
            return {"attached": False, "available": _WIN32_AVAILABLE,
                    "import_error": _IMPORT_ERROR}
        w, h = self.get_window_size()
        return {
            "attached": True,
            "available": True,
            "hwnd": self._hwnd,
            "pid": self._pid,
            "name": self._process_name,
            "title": self._window_title,
            "width": w,
            "height": h,
        }

    def get_window_size(self) -> tuple[int, int]:
        """Client area 크기 (좌표계 기준)."""
        if not self.is_attached():
            return (0, 0)
        try:
            rect = win32gui.GetClientRect(self._hwnd)
            return (rect[2] - rect[0], rect[3] - rect[1])
        except Exception:
            return (0, 0)

    # ── 캡처 ─────────────────────────────────────────────────────────
    def capture_window(self, fmt: str = "jpeg") -> bytes:
        """대상 윈도우의 client 영역을 PrintWindow 로 캡처."""
        if not self.is_attached():
            raise RuntimeError("No window attached")
        hwnd = self._hwnd
        rect = win32gui.GetClientRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            raise RuntimeError(f"Window has invalid size: {w}x{h}")

        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = None
        saveDC = None
        saveBitMap = None
        try:
            mfcDC = win32ui.CreateDCFromHandle(hwndDC)
            saveDC = mfcDC.CreateCompatibleDC()
            saveBitMap = win32ui.CreateBitmap()
            saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
            saveDC.SelectObject(saveBitMap)
            # PW_CLIENTONLY=1, PW_RENDERFULLCONTENT=2 (Windows 8.1+, GPU/Chromium 호환)
            flags = 0x00000003
            ok = windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), flags)
            if not ok:
                # PW_RENDERFULLCONTENT 미지원 → 1로 재시도
                ok = windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 1)
            bmpinfo = saveBitMap.GetInfo()
            bmpstr = saveBitMap.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr, "raw", "BGRX", 0, 1,
            )
        finally:
            if saveBitMap is not None:
                try:
                    win32gui.DeleteObject(saveBitMap.GetHandle())
                except Exception:
                    pass
            if saveDC is not None:
                try:
                    saveDC.DeleteDC()
                except Exception:
                    pass
            if mfcDC is not None:
                try:
                    mfcDC.DeleteDC()
                except Exception:
                    pass
            try:
                win32gui.ReleaseDC(hwnd, hwndDC)
            except Exception:
                pass

        buf = io.BytesIO()
        if fmt.lower() == "png":
            img.save(buf, format="PNG")
        else:
            img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()

    # ── 입력 ─────────────────────────────────────────────────────────
    def _check(self) -> int:
        if not self.is_attached():
            raise RuntimeError("No window attached")
        return self._hwnd  # type: ignore[return-value]

    @staticmethod
    def _lparam(x: int, y: int) -> int:
        return win32api.MAKELONG(int(x) & 0xFFFF, int(y) & 0xFFFF)

    def send_tap(self, x: int, y: int, button: str = "left") -> None:
        hwnd = self._check()
        lp = self._lparam(x, y)
        if button == "right":
            down, up, mk = win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON
        elif button == "middle":
            down, up, mk = win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.MK_MBUTTON
        else:
            down, up, mk = win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON
        win32api.PostMessage(hwnd, down, mk, lp)
        # 클릭 처리 시간 확보
        time.sleep(0.02)
        win32api.PostMessage(hwnd, up, 0, lp)

    def send_double_click(self, x: int, y: int) -> None:
        hwnd = self._check()
        lp = self._lparam(x, y)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, lp)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def send_long_press(self, x: int, y: int, duration_ms: int = 500) -> None:
        hwnd = self._check()
        lp = self._lparam(x, y)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        try:
            time.sleep(max(0.0, duration_ms / 1000.0))
        finally:
            win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def send_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        hwnd = self._check()
        lp_start = self._lparam(x1, y1)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp_start)
        steps = max(2, int(max(50, duration_ms) / 25))
        delay = max(0.0, duration_ms / 1000.0 / steps)
        for i in range(1, steps):
            t = i / steps
            x = int(x1 + (x2 - x1) * t)
            y = int(y1 + (y2 - y1) * t)
            win32api.PostMessage(
                hwnd, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, self._lparam(x, y),
            )
            if delay > 0:
                time.sleep(delay)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, self._lparam(x2, y2))

    def send_text(self, text: str) -> None:
        hwnd = self._check()
        for ch in text:
            win32api.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)

    def send_key(self, key: str) -> None:
        """가상키 한 번 누르고 떼기 (modifier 미지원 — 단일 키만)."""
        hwnd = self._check()
        vk = _resolve_vk(key)
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0)
