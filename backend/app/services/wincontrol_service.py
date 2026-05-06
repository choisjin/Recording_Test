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
        self._exe_path: str = ""
        self._window_title: str = ""
        self._window_class: str = ""

    @staticmethod
    def is_available() -> bool:
        return _WIN32_AVAILABLE

    @staticmethod
    def import_error() -> Optional[str]:
        return _IMPORT_ERROR

    # ── 프로세스/윈도우 검색 ──────────────────────────────────────────
    def _enum_windows(self) -> list[dict]:
        """모든 가시 최상위 윈도우 (PID당 여러 창 허용). 내부 검색용."""
        if not _WIN32_AVAILABLE:
            return []
        results: list[dict] = []

        def _cb(hwnd: int, _: object) -> bool:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd)
                if not title:
                    return True
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w <= 0 or h <= 0:
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                try:
                    proc = psutil.Process(pid)
                    name = proc.name()
                    try:
                        exe_path = proc.exe()
                    except (psutil.AccessDenied, FileNotFoundError):
                        exe_path = ""
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return True
                try:
                    cls_name = win32gui.GetClassName(hwnd)
                except Exception:
                    cls_name = ""
                results.append({
                    "pid": int(pid),
                    "hwnd": int(hwnd),
                    "name": name,
                    "exe_path": exe_path,
                    "title": title,
                    "class_name": cls_name,
                    "width": w,
                    "height": h,
                })
            except Exception as e:
                logger.debug("enum_window callback error: %s", e)
            return True

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception as e:
            logger.warning("EnumWindows failed: %s", e)
        return results

    def list_processes(self) -> list[dict]:
        """가시 최상위 윈도우 + PID/프로세스명 목록.

        같은 PID의 여러 창은 첫 번째(가장 의미 있는) 창만 노출 — UI 콤보 표시용.
        """
        if not _WIN32_AVAILABLE:
            return []
        seen: set[int] = set()
        out: list[dict] = []
        for w in self._enum_windows():
            if w["pid"] in seen:
                continue
            seen.add(w["pid"])
            out.append(w)
        # 보기 좋게 프로세스명/타이틀 순 정렬
        return sorted(out, key=lambda d: (d["name"].lower(), d["title"].lower()))

    def find_window(
        self,
        process_name: str = "",
        exe_path: str = "",
        title_pattern: str = "",
        class_name: str = "",
    ) -> Optional[dict]:
        """주어진 조건과 일치하는 첫 번째 윈도우 정보 반환.

        매칭 규칙:
          - exe_path: 절대 경로 정확 일치 (대소문자 무시)
          - process_name: 파일명 정확 일치 (대소문자 무시)
          - title_pattern: 부분 문자열 일치 (대소문자 무시)
          - class_name: 정확 일치
        지정된 필드만 사용 (빈 값은 무시). 모두 빈 값이면 None.
        """
        if not _WIN32_AVAILABLE or not (process_name or exe_path or title_pattern or class_name):
            return None
        exe_path_norm = (exe_path or "").strip().lower()
        proc_name_norm = (process_name or "").strip().lower()
        title_norm = (title_pattern or "").strip().lower()
        cls_norm = (class_name or "").strip()
        for w in self._enum_windows():
            if exe_path_norm and (w.get("exe_path") or "").lower() != exe_path_norm:
                continue
            if proc_name_norm and (w.get("name") or "").lower() != proc_name_norm:
                continue
            if title_norm and title_norm not in (w.get("title") or "").lower():
                continue
            if cls_norm and (w.get("class_name") or "") != cls_norm:
                continue
            return w
        return None

    @staticmethod
    def launch_process(exe_path: str, args: Optional[list[str]] = None) -> int:
        """프로세스 실행. 성공 시 PID 반환. exe_path 가 비어있으면 ValueError."""
        if not exe_path:
            raise ValueError("exe_path is empty")
        import subprocess
        cmd = [exe_path] + (args or [])
        # 새 콘솔 분리 + 입력 무시 — 백엔드 종료와 독립적으로 살아남기.
        creationflags = 0
        if _WIN32_AVAILABLE:
            try:
                creationflags = win32con.DETACHED_PROCESS | win32con.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            except Exception:
                creationflags = 0x00000008  # DETACHED_PROCESS
        proc = subprocess.Popen(
            cmd, close_fds=True, creationflags=creationflags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("WinControl launched: %s pid=%d", exe_path, proc.pid)
        return proc.pid

    def ensure_attached(
        self,
        process_name: str = "",
        exe_path: str = "",
        title_pattern: str = "",
        class_name: str = "",
        launch_if_missing: bool = True,
        wait_seconds: float = 8.0,
    ) -> dict:
        """저장된 프로세스 정보를 사용해 임베드 상태를 보장.

        흐름:
          1) 이미 attached + 현재 프로세스명/타이틀이 일치하면 그대로 사용
          2) 일치 안 하거나 미임베드면 find_window로 매칭 윈도우 탐색 후 attach
          3) 못 찾고 launch_if_missing=True 이면 exe_path 실행 + 윈도우 등장 polling 후 attach
        성공 시 status() 반환, 실패 시 RuntimeError.
        """
        if not _WIN32_AVAILABLE:
            raise RuntimeError(f"WinControl unavailable: {_IMPORT_ERROR}")

        # 1) 현재 attach 가 조건과 일치하는지
        if self.is_attached():
            cur_name_match = (not process_name) or (
                (self._process_name or "").lower() == (process_name or "").lower()
            )
            cur_title_match = (not title_pattern) or (
                (title_pattern or "").lower() in (self._window_title or "").lower()
            )
            if cur_name_match and cur_title_match:
                return self.status()
            # 조건 불일치 → 새 attach 시도 (기존 핸들 유지하지 않음)
            self.detach()

        # 2) 현재 시스템에서 매칭 윈도우 탐색
        match = self.find_window(process_name, exe_path, title_pattern, class_name)
        if match:
            return self.attach(match["hwnd"])

        # 3) 프로세스 실행 후 윈도우 등장 대기
        if not launch_if_missing or not exe_path:
            raise RuntimeError(
                f"WinControl: matching window not found "
                f"(name={process_name!r}, exe={exe_path!r}, title~={title_pattern!r})"
            )
        try:
            self.launch_process(exe_path)
        except Exception as e:
            raise RuntimeError(f"WinControl: failed to launch {exe_path!r}: {e}")

        deadline = time.monotonic() + max(0.5, wait_seconds)
        while time.monotonic() < deadline:
            time.sleep(0.3)
            match = self.find_window(process_name, exe_path, title_pattern, class_name)
            if match:
                return self.attach(match["hwnd"])
        raise RuntimeError(
            f"WinControl: launched {exe_path!r} but window did not appear within {wait_seconds:.1f}s"
        )

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
            self._window_class = win32gui.GetClassName(hwnd) or ""
        except Exception:
            self._window_class = ""
        try:
            if self._pid:
                proc = psutil.Process(self._pid)
                self._process_name = proc.name()
                try:
                    self._exe_path = proc.exe()
                except (psutil.AccessDenied, FileNotFoundError):
                    self._exe_path = ""
            else:
                self._process_name = ""
                self._exe_path = ""
        except Exception:
            self._process_name = ""
            self._exe_path = ""
        logger.info("WinControl attached: hwnd=%d pid=%s name=%s exe=%s title=%r",
                    hwnd, self._pid, self._process_name, self._exe_path, self._window_title)
        return self.status()

    def detach(self) -> None:
        logger.info("WinControl detached: hwnd=%s", self._hwnd)
        self._hwnd = None
        self._pid = None
        self._process_name = ""
        self._exe_path = ""
        self._window_title = ""
        self._window_class = ""

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
            "exe_path": self._exe_path,
            "class_name": self._window_class,
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
    def capture_window(self, fmt: str = "jpeg", render_full_content: bool = False) -> bytes:
        """대상 윈도우의 client 영역을 PrintWindow 로 캡처.

        Args:
            render_full_content: True 면 PW_RENDERFULLCONTENT(0x02) 사용.
              GPU/Chromium 기반 앱에 필수일 수 있으나, Chrome 등에서 화면이
              깜박이는 부작용이 있어 기본값은 False(PW_CLIENTONLY 만 사용).
        """
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
            # 기본은 PW_CLIENTONLY(1) 만 사용 — Chrome/Edge 깜박임 회피.
            # render_full_content=True 또는 1차 캡처 결과가 빈 화면(전부 검정)일 때 0x03 재시도.
            base_flag = 0x00000003 if render_full_content else 0x00000001
            ok = windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), base_flag)
            if not ok and base_flag != 0x00000001:
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
    # 입력 모드:
    #   "post" (기본) — PostMessage 사용. 백그라운드 입력 가능, 깜박임 없음.
    #     단점: UWP(계산기), MMC 스냅인(장치관리자), DirectX/게임 등 메시지 큐를
    #     안 쓰는 앱에는 입력이 전달되지 않는다.
    #   "send" — SendInput 사용. 모든 앱 호환되지만 대상 윈도우에 포커스가
    #     필요하다 (SetForegroundWindow + 마우스 커서 이동 후 입력).
    def _check(self) -> int:
        if not self.is_attached():
            raise RuntimeError("No window attached")
        return self._hwnd  # type: ignore[return-value]

    @staticmethod
    def _lparam(x: int, y: int) -> int:
        return win32api.MAKELONG(int(x) & 0xFFFF, int(y) & 0xFFFF)

    def _focus(self) -> None:
        """대상 윈도우를 전면으로 + 포커스. SendInput 모드 전제 조건."""
        hwnd = self._hwnd
        if not hwnd:
            return
        try:
            # 최소화 상태면 복원
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            # 포어그라운드 락 회피 — AttachThreadInput 트릭
            try:
                fg = windll.user32.GetForegroundWindow()
                cur_thread = win32api.GetCurrentThreadId()
                fg_thread, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
                if fg_thread and fg_thread != cur_thread:
                    windll.user32.AttachThreadInput(cur_thread, fg_thread, True)
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    finally:
                        windll.user32.AttachThreadInput(cur_thread, fg_thread, False)
                else:
                    win32gui.SetForegroundWindow(hwnd)
            except Exception:
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            # 짧은 안정화 대기 — 포커스 전환 완료까지
            time.sleep(0.05)
        except Exception as e:
            logger.debug("WinControl focus failed: %s", e)

    def _client_to_screen(self, x: int, y: int) -> tuple[int, int]:
        """client 좌표 → screen 좌표 (SendInput 마우스 절대 위치용)."""
        hwnd = self._hwnd
        if not hwnd:
            return (int(x), int(y))
        try:
            return win32gui.ClientToScreen(hwnd, (int(x), int(y)))
        except Exception:
            return (int(x), int(y))

    def _send_input_mouse_move(self, screen_x: int, screen_y: int) -> None:
        """SendInput 으로 마우스 절대 위치 이동 (가상화면 좌표계)."""
        # MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK = 0x4001 | 0x8000
        # 절대 좌표는 0..65535 정규화 + 가상 데스크탑 기준
        vx = windll.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        vy = windll.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        vw = windll.user32.GetSystemMetrics(78) or 1  # SM_CXVIRTUALSCREEN
        vh = windll.user32.GetSystemMetrics(79) or 1  # SM_CYVIRTUALSCREEN
        nx = int(((screen_x - vx) * 65535) / vw)
        ny = int(((screen_y - vy) * 65535) / vh)
        # MOUSEEVENTF_MOVE=0x0001, MOUSEEVENTF_ABSOLUTE=0x8000, MOUSEEVENTF_VIRTUALDESK=0x4000
        win32api.mouse_event(0x0001 | 0x8000 | 0x4000, nx, ny, 0, 0)

    def _send_input_button(self, button: str, down: bool) -> None:
        # MOUSEEVENTF_LEFTDOWN=0x0002, LEFTUP=0x0004, RIGHTDOWN=0x0008, RIGHTUP=0x0010,
        # MIDDLEDOWN=0x0020, MIDDLEUP=0x0040
        if button == "right":
            flag = 0x0008 if down else 0x0010
        elif button == "middle":
            flag = 0x0020 if down else 0x0040
        else:
            flag = 0x0002 if down else 0x0004
        win32api.mouse_event(flag, 0, 0, 0, 0)

    # ── tap/click ───────────────────────────────────────────────
    def send_tap(self, x: int, y: int, button: str = "left", mode: str = "post") -> None:
        hwnd = self._check()
        if mode == "send":
            self._focus()
            sx, sy = self._client_to_screen(int(x), int(y))
            self._send_input_mouse_move(sx, sy)
            time.sleep(0.02)
            self._send_input_button(button, True)
            time.sleep(0.02)
            self._send_input_button(button, False)
            return
        lp = self._lparam(x, y)
        if button == "right":
            down, up, mk = win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON
        elif button == "middle":
            down, up, mk = win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.MK_MBUTTON
        else:
            down, up, mk = win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON
        win32api.PostMessage(hwnd, down, mk, lp)
        time.sleep(0.02)
        win32api.PostMessage(hwnd, up, 0, lp)

    def send_double_click(self, x: int, y: int, mode: str = "post") -> None:
        hwnd = self._check()
        if mode == "send":
            self._focus()
            sx, sy = self._client_to_screen(int(x), int(y))
            self._send_input_mouse_move(sx, sy)
            time.sleep(0.02)
            for _ in range(2):
                self._send_input_button("left", True)
                time.sleep(0.02)
                self._send_input_button("left", False)
                time.sleep(0.02)
            return
        lp = self._lparam(x, y)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, lp)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def send_long_press(self, x: int, y: int, duration_ms: int = 500, mode: str = "post") -> None:
        hwnd = self._check()
        if mode == "send":
            self._focus()
            sx, sy = self._client_to_screen(int(x), int(y))
            self._send_input_mouse_move(sx, sy)
            time.sleep(0.02)
            self._send_input_button("left", True)
            try:
                time.sleep(max(0.0, duration_ms / 1000.0))
            finally:
                self._send_input_button("left", False)
            return
        lp = self._lparam(x, y)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        try:
            time.sleep(max(0.0, duration_ms / 1000.0))
        finally:
            win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def send_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300, mode: str = "post") -> None:
        hwnd = self._check()
        steps = max(2, int(max(50, duration_ms) / 25))
        delay = max(0.0, duration_ms / 1000.0 / steps)
        if mode == "send":
            self._focus()
            sx1, sy1 = self._client_to_screen(int(x1), int(y1))
            self._send_input_mouse_move(sx1, sy1)
            time.sleep(0.02)
            self._send_input_button("left", True)
            for i in range(1, steps):
                t = i / steps
                x = int(x1 + (x2 - x1) * t)
                y = int(y1 + (y2 - y1) * t)
                sx, sy = self._client_to_screen(x, y)
                self._send_input_mouse_move(sx, sy)
                if delay > 0:
                    time.sleep(delay)
            sx2, sy2 = self._client_to_screen(int(x2), int(y2))
            self._send_input_mouse_move(sx2, sy2)
            time.sleep(0.02)
            self._send_input_button("left", False)
            return
        lp_start = self._lparam(x1, y1)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp_start)
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

    def send_text(self, text: str, mode: str = "post") -> None:
        hwnd = self._check()
        if mode == "send":
            self._focus()
            for ch in text:
                # KEYEVENTF_UNICODE=0x0004
                win32api.keybd_event(0, ord(ch), 0x0004, 0)
                time.sleep(0.005)
                win32api.keybd_event(0, ord(ch), 0x0004 | 0x0002, 0)
                time.sleep(0.005)
            return
        for ch in text:
            win32api.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)

    def send_key(self, key: str, mode: str = "post") -> None:
        """가상키 한 번 누르고 떼기 (modifier 미지원 — 단일 키만)."""
        hwnd = self._check()
        vk = _resolve_vk(key)
        if mode == "send":
            self._focus()
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            win32api.keybd_event(vk, 0, 0x0002, 0)  # KEYEVENTF_KEYUP
            return
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0)
