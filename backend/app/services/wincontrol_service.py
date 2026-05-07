"""Windows process window control service.

PrintWindow + PostMessage 기반으로 다른 Windows 프로세스의 윈도우를 캡처/조작.
디바이스 타입 "wincontrol" 의 백엔드 구현.
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import logging
import time
from ctypes import windll
from ctypes.wintypes import HANDLE, HWND
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


# Per-Window DPI 컨텍스트 API (Win10 1607+) — DPI-unaware 타겟을 캡처할 때
# 스레드 awareness 를 타겟에 맞춰 GetClientRect/PrintWindow/ClientToScreen 를
# 일관된 좌표계로 동작시키기 위함. argtype/restype 미지정 시 64bit 핸들이 잘림.
if _WIN32_AVAILABLE:
    try:
        windll.user32.GetWindowDpiAwarenessContext.argtypes = [HWND]
        windll.user32.GetWindowDpiAwarenessContext.restype = HANDLE
        windll.user32.SetThreadDpiAwarenessContext.argtypes = [HANDLE]
        windll.user32.SetThreadDpiAwarenessContext.restype = HANDLE
    except (AttributeError, OSError):
        # Win10 1607 미만 — 타겟 매칭 불가, 프로세스 기본 awareness 그대로 사용.
        pass


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
        # UWP/WinUI3 감지 — PrintWindow 가 PW_RENDERFULLCONTENT 없으면 검은 화면을 반환,
        # PostMessage 도 거의 안 먹는다. attach 시 1회 판정 후 캡처/사용자 알림에 활용.
        self._is_uwp: bool = False
        # UWP 의 진짜 콘텐츠 윈도우(Windows.UI.Core.CoreWindow) — 캡처 시 우선 사용.
        self._content_hwnd: Optional[int] = None
        # UWP AppUserModelID — 종료된 UWP 앱 재실행에 필요(.exe 직접 실행 불가).
        # 예: "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
        self._aumid: str = ""

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

        같은 PID 라도 별개의 최상위 창(예: VS_BASE 메인 + CANDB TX CONTROL 자식 툴윈도우)이
        있으면 모두 노출 — 사용자가 임베드할 창을 직접 선택할 수 있게.
        프로세스명/타이틀 순으로 정렬.
        """
        if not _WIN32_AVAILABLE:
            return []
        return sorted(self._enum_windows(),
                      key=lambda d: (d["name"].lower(), d["title"].lower()))

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
    def _wait_for_input_idle(hwnd: int, timeout_ms: int = 3000) -> None:
        """대상 윈도우의 프로세스가 입력 받을 준비 될 때까지 대기.

        새로 spawn 한 프로세스(또는 UWP 활성화 직후)는 메시지 큐/페인팅이 안정화되기
        전에는 입력을 무시한다. user32!WaitForInputIdle 은 프로세스가 첫 GetMessage
        호출(=메인 루프 idle)에 도달할 때까지 블록 — Windows 표준 동기화 방법.
        """
        if not _WIN32_AVAILABLE or not hwnd:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if not pid:
            return
        PROCESS_QUERY_INFORMATION = 0x0400
        SYNCHRONIZE = 0x00100000
        h = windll.kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | SYNCHRONIZE, False, int(pid),
        )
        if not h:
            return
        try:
            # 0=성공, WAIT_TIMEOUT=258, WAIT_FAILED=0xFFFFFFFF.
            # 콘솔 앱은 WAIT_FAILED — 무시하고 진행해도 무해.
            windll.user32.WaitForInputIdle(h, int(timeout_ms))
        except Exception as e:
            logger.debug("WaitForInputIdle failed: %s", e)
        finally:
            try:
                windll.kernel32.CloseHandle(h)
            except Exception:
                pass

    @staticmethod
    def launch_process(exe_path: str, args: Optional[list[str]] = None) -> int:
        """일반 .exe 실행. 성공 시 PID 반환. exe_path 가 비어있으면 ValueError."""
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

    @staticmethod
    def launch_uwp(aumid: str) -> None:
        """UWP/Packaged 앱 활성화 — explorer.exe shell:AppsFolder\\<AUMID>.

        UWP 는 .exe 직접 실행 시 AppContainer 가 없어 빈 호스트만 뜨고 실제 앱은 안 뜸.
        explorer 가 AppX 매니페스트를 따라 정상 launch 한다.
        """
        if not aumid:
            raise ValueError("aumid is empty")
        import subprocess
        cmd = ["explorer.exe", f"shell:AppsFolder\\{aumid}"]
        creationflags = 0
        if _WIN32_AVAILABLE:
            try:
                creationflags = win32con.DETACHED_PROCESS  # type: ignore[attr-defined]
            except Exception:
                creationflags = 0x00000008
        subprocess.Popen(
            cmd, close_fds=True, creationflags=creationflags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("WinControl launched UWP: %s", aumid)

    def ensure_attached(
        self,
        process_name: str = "",
        exe_path: str = "",
        title_pattern: str = "",
        class_name: str = "",
        aumid: str = "",
        launch_if_missing: bool = True,
        wait_seconds: float = 8.0,
        target_width: int = 0,
        target_height: int = 0,
    ) -> dict:
        """저장된 프로세스 정보를 사용해 임베드 상태를 보장.

        흐름:
          1) 이미 attached + 현재 프로세스명/타이틀이 일치하면 그대로 사용
          2) 일치 안 하거나 미임베드면 find_window로 매칭 윈도우 탐색 후 attach
          3) 못 찾고 launch_if_missing=True 이면 launch:
             - aumid 가 있으면 UWP 활성화 (explorer shell:AppsFolder\\AUMID) — UWP 우선
             - 아니면 exe_path 로 일반 .exe 실행
          4) target_width/height > 0 이면 attach 후 client area 를 해당 크기로 리사이즈
             — 좌표 기반 입력이 녹화 시점과 동일한 위치를 가리키도록 보장.
        성공 시 status() 반환, 실패 시 RuntimeError.
        """
        if not _WIN32_AVAILABLE:
            raise RuntimeError(f"WinControl unavailable: {_IMPORT_ERROR}")

        def _maybe_resize() -> None:
            if target_width > 0 and target_height > 0:
                cur_w, cur_h = self.get_window_size()
                if cur_w != int(target_width) or cur_h != int(target_height):
                    self.resize_client(int(target_width), int(target_height))

        # 1) 현재 attach 가 조건과 일치하는지
        if self.is_attached():
            cur_name_match = (not process_name) or (
                (self._process_name or "").lower() == (process_name or "").lower()
            )
            cur_title_match = (not title_pattern) or (
                (title_pattern or "").lower() in (self._window_title or "").lower()
            )
            if cur_name_match and cur_title_match:
                _maybe_resize()
                return self.status()
            # 조건 불일치 → 새 attach 시도 (기존 핸들 유지하지 않음)
            self.detach()

        # 2) 현재 시스템에서 매칭 윈도우 탐색
        match = self.find_window(process_name, exe_path, title_pattern, class_name)
        if match:
            self.attach(match["hwnd"])
            _maybe_resize()
            return self.status()

        # 3) 프로세스 실행 후 윈도우 등장 대기
        if not launch_if_missing or not (exe_path or aumid):
            raise RuntimeError(
                f"WinControl: matching window not found "
                f"(name={process_name!r}, exe={exe_path!r}, aumid={aumid!r}, title~={title_pattern!r})"
            )
        launched_what = ""
        try:
            if aumid:
                # UWP/Packaged 앱: explorer 로 활성화 — .exe 직접 실행 불가.
                self.launch_uwp(aumid)
                launched_what = f"AUMID={aumid}"
            elif exe_path:
                self.launch_process(exe_path)
                launched_what = exe_path
        except Exception as e:
            raise RuntimeError(f"WinControl: failed to launch ({launched_what or aumid or exe_path!r}): {e}")

        deadline = time.monotonic() + max(0.5, wait_seconds)
        while time.monotonic() < deadline:
            time.sleep(0.3)
            match = self.find_window(process_name, exe_path, title_pattern, class_name)
            if match:
                # 새로 launch 한 프로세스: 메시지 큐가 idle 상태에 도달할 때까지 대기.
                # 이게 없으면 첫 send_tap 이 paint/init 중인 윈도우에 흡수돼 무시된다.
                self._wait_for_input_idle(match["hwnd"], timeout_ms=3000)
                # 추가 안정화 — 페인팅/레이아웃 완료까지 약간 더 대기 (UWP 는 더 길게 필요).
                time.sleep(0.5)
                self.attach(match["hwnd"])
                _maybe_resize()
                return self.status()
        raise RuntimeError(
            f"WinControl: launched ({launched_what}) but window did not appear within {wait_seconds:.1f}s "
            f"(name={process_name!r}, title~={title_pattern!r})"
        )

    # ── UWP/WinUI3 감지 + AUMID 추출 ─────────────────────────────
    @staticmethod
    def _get_aumid_for_pid(pid: int) -> str:
        """프로세스의 AppUserModelID 반환. UWP/Packaged 앱이 아니면 빈 문자열.

        Win32 API: kernel32!GetApplicationUserModelId(hProcess, *pulLength, pBuf)
        반환 0=성공, 122(ERROR_INSUFFICIENT_BUFFER)=버퍼 부족, 그 외=비-패키지 앱.
        """
        if not _WIN32_AVAILABLE or not pid:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return ""
        try:
            size = ctypes.c_uint32(260)
            buf = ctypes.create_unicode_buffer(size.value)
            res = windll.kernel32.GetApplicationUserModelId(h, ctypes.byref(size), buf)
            if res == 122:  # ERROR_INSUFFICIENT_BUFFER → 더 큰 버퍼로 재시도
                buf = ctypes.create_unicode_buffer(size.value)
                res = windll.kernel32.GetApplicationUserModelId(h, ctypes.byref(size), buf)
            if res == 0:
                return buf.value or ""
            return ""
        except Exception as e:
            logger.debug("GetApplicationUserModelId failed for pid %d: %s", pid, e)
            return ""
        finally:
            try:
                windll.kernel32.CloseHandle(h)
            except Exception:
                pass

    @staticmethod
    def _detect_uwp(hwnd: int) -> tuple[bool, Optional[int]]:
        """UWP/WinUI3 여부 + 진짜 콘텐츠 윈도우(CoreWindow) hwnd 반환.

        UWP 앱은 ApplicationFrameWindow 가 호스트이고 콘텐츠는 자식 CoreWindow.
        WinUI3 (Win11 새 메모장 등) 도 비슷한 자식 윈도우 구조.
        """
        if not _WIN32_AVAILABLE or not hwnd:
            return (False, None)
        try:
            cls = win32gui.GetClassName(hwnd) or ""
        except Exception:
            cls = ""
        is_host = (cls == "ApplicationFrameWindow" or "Microsoft.UI" in cls)

        content_hwnd: Optional[int] = None
        # 자식 중 CoreWindow / Microsoft.UI.* 검색
        try:
            container = [None]  # type: list[Optional[int]]

            def _cb(child: int, _: object) -> bool:
                try:
                    ccls = win32gui.GetClassName(child) or ""
                except Exception:
                    return True
                if ccls == "Windows.UI.Core.CoreWindow" or "Microsoft.UI.Content" in ccls:
                    container[0] = child
                    return False
                return True
            win32gui.EnumChildWindows(hwnd, _cb, None)
            content_hwnd = container[0]
        except Exception:
            content_hwnd = None

        is_uwp = is_host or (content_hwnd is not None)
        return (is_uwp, content_hwnd)

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
        # UWP/WinUI3 판정 — 캡처 플래그 자동 결정 + 사용자에게 입력 모드 권장 정보 노출용
        self._is_uwp, self._content_hwnd = self._detect_uwp(hwnd)
        # UWP 면 AUMID 추출 — 콘텐츠 자식 PID 우선 (호스트 ApplicationFrameHost 는 비-패키지 프로세스).
        self._aumid = ""
        if self._is_uwp:
            target_pid: Optional[int] = None
            if self._content_hwnd:
                try:
                    _, target_pid = win32process.GetWindowThreadProcessId(self._content_hwnd)
                except Exception:
                    target_pid = None
            if not target_pid:
                target_pid = self._pid
            if target_pid:
                self._aumid = self._get_aumid_for_pid(target_pid)
        logger.info("WinControl attached: hwnd=%d pid=%s name=%s exe=%s title=%r class=%s uwp=%s aumid=%r content=%s",
                    hwnd, self._pid, self._process_name, self._exe_path,
                    self._window_title, self._window_class, self._is_uwp, self._aumid, self._content_hwnd)
        return self.status()

    def detach(self) -> None:
        logger.info("WinControl detached: hwnd=%s", self._hwnd)
        self._hwnd = None
        self._pid = None
        self._process_name = ""
        self._exe_path = ""
        self._window_title = ""
        self._window_class = ""
        self._is_uwp = False
        self._content_hwnd = None
        self._aumid = ""

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
        ow, oh = self.get_outer_size()
        ox, oy = self.get_client_offset()
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
            # outer (타이틀바 포함) 크기 — 풀 윈도우 캡처 비트맵의 자연 크기.
            "outer_width": ow,
            "outer_height": oh,
            # client 영역의 outer 비트맵 내 좌상단 오프셋 — 프론트가 클릭 좌표를
            # client-space 로 변환할 때 빼는 값. (보더/타이틀바 두께)
            "client_offset_x": ox,
            "client_offset_y": oy,
            "is_uwp": self._is_uwp,
            "content_hwnd": self._content_hwnd,
            "aumid": self._aumid,
        }

    @contextlib.contextmanager
    def _target_dpi_ctx(self):
        """스레드 DPI 컨텍스트를 타겟 윈도우의 것과 일시적으로 일치시킴.

        백엔드 프로세스는 Per-Monitor V2(고DPI 타겟용) 인데 타겟 앱이 DPI-unaware
        면 GetClientRect 가 반환하는 크기와 PrintWindow 가 실제로 칠하는 영역이
        어긋나서 캡처 비트맵 우/하단이 잘린다. 스레드 awareness 를 타겟에 맞추면
        GetClientRect/PrintWindow/ClientToScreen/GetSystemMetrics 가 모두 같은
        단위(타겟 좌표계)로 동작 → 캡처가 잘리지 않고 입력 좌표도 정확.
        """
        if not _WIN32_AVAILABLE or self._hwnd is None:
            yield
            return
        user32 = windll.user32
        prev_ctx = None
        target_ctx = None
        try:
            target_ctx = user32.GetWindowDpiAwarenessContext(self._hwnd)
        except Exception:
            target_ctx = None
        if target_ctx:
            try:
                prev_ctx = user32.SetThreadDpiAwarenessContext(target_ctx)
            except Exception:
                prev_ctx = None
        try:
            yield
        finally:
            if prev_ctx:
                try:
                    user32.SetThreadDpiAwarenessContext(prev_ctx)
                except Exception:
                    pass

    def get_window_size(self) -> tuple[int, int]:
        """Client area 크기 (물리 픽셀, Per-Monitor V2 기준)."""
        if not self.is_attached():
            return (0, 0)
        try:
            rect = win32gui.GetClientRect(self._hwnd)
            return (rect[2] - rect[0], rect[3] - rect[1])
        except Exception:
            return (0, 0)

    def get_outer_size(self) -> tuple[int, int]:
        """Window 외곽(타이틀바/보더 포함) 크기 — 풀 윈도우 캡처 비트맵 크기."""
        if not self.is_attached():
            return (0, 0)
        try:
            rect = win32gui.GetWindowRect(self._hwnd)
            return (rect[2] - rect[0], rect[3] - rect[1])
        except Exception:
            return (0, 0)

    def get_client_offset(self) -> tuple[int, int]:
        """Window 외곽 비트맵 기준 client 좌상단의 오프셋 (물리 픽셀).

        프론트가 풀 윈도우 캔버스에서 받은 클릭 좌표를 client-space 로 변환할 때
        빼는 값. 일반적으로 (왼쪽 보더 두께, 타이틀바+상단 보더 두께).
        """
        if not self.is_attached():
            return (0, 0)
        try:
            wr = win32gui.GetWindowRect(self._hwnd)
            cx, cy = win32gui.ClientToScreen(self._hwnd, (0, 0))
            return (cx - wr[0], cy - wr[1])
        except Exception:
            return (0, 0)

    def resize_client(self, target_w: int, target_h: int) -> tuple[int, int]:
        """대상 윈도우의 client area 를 (target_w, target_h) 로 리사이즈.

        녹화 시점과 동일한 client 크기로 맞춰서 좌표 기반 입력이 항상 같은
        UI 요소를 가리키도록 보장. 외곽(타이틀바/보더) 크기를 빼고 더해
        실제 client 가 정확히 일치하도록 SetWindowPos 호출.

        반환: 리사이즈 후 실제 client (w, h). 윈도우가 최소/최대 크기 제약
        때문에 요청 크기보다 작거나 클 수 있으므로 호출자에게 실측값 전달.
        """
        if not self.is_attached() or target_w <= 0 or target_h <= 0:
            return self.get_window_size()
        hwnd = self._hwnd
        try:
            # 최대화/최소화 상태면 정상 크기로 복원 — 그래야 SetWindowPos 가 먹힘.
            try:
                if win32gui.IsZoomed(hwnd) or win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    time.sleep(0.05)
            except Exception:
                pass
            # 외곽-클라이언트 차이(보더/타이틀바)를 측정해서 outer 목표 크기 산출.
            cur_window = win32gui.GetWindowRect(hwnd)
            cur_outer_w = cur_window[2] - cur_window[0]
            cur_outer_h = cur_window[3] - cur_window[1]
            cur_client = win32gui.GetClientRect(hwnd)
            cur_client_w = cur_client[2] - cur_client[0]
            cur_client_h = cur_client[3] - cur_client[1]
            dx = cur_outer_w - cur_client_w
            dy = cur_outer_h - cur_client_h
            new_outer_w = max(1, int(target_w) + dx)
            new_outer_h = max(1, int(target_h) + dy)
            # SWP_NOMOVE: 위치 유지, SWP_NOZORDER: z-order 유지, SWP_NOACTIVATE: 포커스 안 뺏음.
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            win32gui.SetWindowPos(
                hwnd, 0, 0, 0, new_outer_w, new_outer_h,
                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
            )
            # 레이아웃 반영 시간.
            time.sleep(0.05)
        except Exception as e:
            logger.debug("WinControl resize_client failed: %s", e)
        return self.get_window_size()

    # ── 캡처 ─────────────────────────────────────────────────────────
    def _capture_via_screen(self, hwnd: int) -> Optional[Image.Image]:
        """Screen DC 에서 윈도우 영역을 BitBlt 로 복사.

        WYSIWYG: 사용자가 화면에서 보는 그대로 (DWM 업스케일/DPI 가상화 반영).
        PrintWindow 의 DPI 가상화 문제(레거시 앱 우/하단 잘림) 회피.
        - 풀 윈도우(타이틀바 포함) 캡처 — GetWindowRect 영역 그대로 BitBlt.
        - 윈도우가 occluded(가려짐) 상태면 위에 있는 픽셀이 섞일 수 있음 → 호출자가
          blank/이상 감지 시 PrintWindow 로 폴백.
        - 최소화/오프스크린 상태면 None 반환.
        """
        try:
            if not win32gui.IsWindow(hwnd):
                return None
            if win32gui.IsIconic(hwnd):
                return None  # 최소화 상태 — 화면에 안 보이므로 BitBlt 무의미
            wr = win32gui.GetWindowRect(hwnd)
        except Exception:
            return None
        sx, sy = wr[0], wr[1]
        w, h = wr[2] - wr[0], wr[3] - wr[1]
        if w <= 0 or h <= 0:
            return None
        screen_dc_handle = windll.user32.GetDC(0)
        if not screen_dc_handle:
            return None
        mfc_screen = None
        save_dc = None
        bmp = None
        try:
            mfc_screen = win32ui.CreateDCFromHandle(screen_dc_handle)
            save_dc = mfc_screen.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(mfc_screen, w, h)
            save_dc.SelectObject(bmp)
            # SRCCOPY = 0x00CC0020. (0,0) 비트맵 좌상단으로 (sx, sy) 화면 영역 복사.
            save_dc.BitBlt((0, 0), (w, h), mfc_screen, (sx, sy), win32con.SRCCOPY)
            info = bmp.GetInfo()
            bits = bmp.GetBitmapBits(True)
            return Image.frombuffer(
                "RGB",
                (info["bmWidth"], info["bmHeight"]),
                bits, "raw", "BGRX", 0, 1,
            )
        except Exception:
            return None
        finally:
            if bmp is not None:
                try:
                    win32gui.DeleteObject(bmp.GetHandle())
                except Exception:
                    pass
            if save_dc is not None:
                try:
                    save_dc.DeleteDC()
                except Exception:
                    pass
            if mfc_screen is not None:
                try:
                    mfc_screen.DeleteDC()
                except Exception:
                    pass
            try:
                windll.user32.ReleaseDC(0, screen_dc_handle)
            except Exception:
                pass

    def _capture_with_flag(self, hwnd: int, flag: int) -> Optional[Image.Image]:
        """주어진 PrintWindow 플래그로 hwnd 를 캡처해 PIL Image 반환. 실패 시 None.

        flag 에 PW_CLIENTONLY(0x1) 가 빠져있으면 비트맵을 GetWindowRect 크기로 잡아
        타이틀바/보더 까지 포함한 풀 윈도우를 캡처한다 (그렇지 않으면 GetClientRect).
        """
        is_client_only = bool(flag & 0x00000001)
        try:
            if is_client_only:
                rect = win32gui.GetClientRect(hwnd)
                w, h = rect[2] - rect[0], rect[3] - rect[1]
            else:
                wr = win32gui.GetWindowRect(hwnd)
                w, h = wr[2] - wr[0], wr[3] - wr[1]
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
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
            ok = windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), flag)
            if not ok:
                return None
            bmpinfo = saveBitMap.GetInfo()
            bmpstr = saveBitMap.GetBitmapBits(True)
            return Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr, "raw", "BGRX", 0, 1,
            )
        except Exception:
            return None
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

    @staticmethod
    def capture_hwnd_bgr(hwnd: int) -> "Optional['np.ndarray']":  # type: ignore[name-defined]
        """임의 hwnd를 BGR numpy 배열로 캡처 (UWP/WinUI3 자동 폴백 포함).

        CompositorService 등 attach 상태와 무관하게 여러 윈도우를 동시에 캡처해야 할 때 사용.
        실패 시 None 반환.
        """
        if not _WIN32_AVAILABLE or not hwnd:
            return None
        try:
            import numpy as _np
        except Exception:
            return None
        try:
            if not win32gui.IsWindow(hwnd):
                return None
        except Exception:
            return None

        # WinControlService 인스턴스 메서드를 stateless 헬퍼로 재사용
        helper = WinControlService()
        # 자식 CoreWindow 탐지 (UWP)
        is_uwp, content_hwnd = helper._detect_uwp(hwnd)
        first_flag = 0x00000003 if is_uwp else 0x00000001
        img = helper._capture_with_flag(hwnd, first_flag)
        if helper._is_blank_image(img) and first_flag != 0x00000003:
            img = helper._capture_with_flag(hwnd, 0x00000003)
        if helper._is_blank_image(img) and content_hwnd:
            try:
                if win32gui.IsWindow(content_hwnd):
                    img = helper._capture_with_flag(content_hwnd, 0x00000003)
                    if helper._is_blank_image(img):
                        img = helper._capture_with_flag(content_hwnd, 0x00000001)
            except Exception:
                pass
        if img is None:
            return None
        # PIL RGB → BGR ndarray (cv2 호환)
        try:
            arr = _np.asarray(img)  # RGB
            if arr.ndim != 3 or arr.shape[2] < 3:
                return None
            # RGB → BGR (마지막 채널 순서만 뒤집음)
            return arr[:, :, ::-1].copy()
        except Exception:
            return None

    @staticmethod
    def _is_blank_image(img: Optional[Image.Image]) -> bool:
        """이미지가 사실상 단색(검정/흰색) 인지 — UWP 캡처 실패 감지."""
        if img is None:
            return True
        try:
            extrema = img.getextrema()
            # 각 채널의 (min,max) 가 거의 같으면 단색
            if not extrema:
                return True
            # RGB → tuple of 3 (min,max). RGBA 또는 단일채널 케이스도 안전 처리.
            if isinstance(extrema[0], tuple):
                for mn, mx in extrema:
                    if mx - mn > 8:  # 채널 변화 폭이 충분하면 정상
                        return False
                return True
            mn, mx = extrema
            return (mx - mn) <= 8
        except Exception:
            return False

    def capture_window(self, fmt: str = "jpeg", render_full_content: bool = False) -> bytes:
        """대상 윈도우 캡처 (타이틀바 포함 풀 윈도우).

        시도 순서:
          1) Screen BitBlt (Per-Monitor V2 컨텍스트) — WYSIWYG, DPI 가상화/DWM 업스케일
             반영된 실제 렌더 픽셀. 가장 정확한 크기/내용. 단 occluded 시 위 윈도우가 섞임.
          2) PrintWindow (target DPI 컨텍스트) — occluded/blank 폴백.
          3) UWP/WinUI3 는 host 외곽이 의미 없어 client-only render 우선 + content_hwnd 폴백.
        """
        if not self.is_attached():
            raise RuntimeError("No window attached")
        host_hwnd = self._hwnd

        img: Optional[Image.Image] = None
        # 1) UWP 가 아니면 BitBlt 우선 — DPI-virtualized 레거시 앱도 정확한 크기로 캡처.
        #    BitBlt 는 우리 프로세스 기본 awareness(Per-Monitor V2) 에서 실행해야 DWM
        #    업스케일 후 픽셀이 잡힘. 따라서 _target_dpi_ctx 밖에서 호출.
        if not self._is_uwp:
            img = self._capture_via_screen(host_hwnd)

        # 2) BitBlt 가 None/단색이면 PrintWindow 폴백 (target DPI 컨텍스트 안에서).
        if img is None or self._is_blank_image(img):
            first_flag = 0x00000003 if (render_full_content or self._is_uwp) else 0x00000002
            with self._target_dpi_ctx():
                img = self._capture_with_flag(host_hwnd, first_flag)
                if self._is_blank_image(img):
                    img = self._capture_with_flag(host_hwnd, 0x00000003)
                # UWP 는 콘텐츠 자식(CoreWindow) 으로 추가 폴백
                if self._is_blank_image(img) and self._content_hwnd:
                    try:
                        if win32gui.IsWindow(self._content_hwnd):
                            img = self._capture_with_flag(self._content_hwnd, 0x00000003)
                            if self._is_blank_image(img):
                                img = self._capture_with_flag(self._content_hwnd, 0x00000001)
                    except Exception:
                        pass
        if img is None:
            raise RuntimeError("Capture failed (BitBlt + PrintWindow all paths)")

        buf = io.BytesIO()
        if fmt.lower() == "png":
            img.save(buf, format="PNG")
        else:
            img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()

    # ── 입력 ─────────────────────────────────────────────────────────
    # 입력 방식: SendInput 만 사용 — UWP/WinUI3/MMC/일반 Win32 앱 모두 호환.
    # 단점: 대상 윈도우에 포커스가 일시적으로 가져가짐 (SetForegroundWindow + 가상
    # 데스크탑 절대 좌표로 마우스 이동 후 클릭). 백그라운드 입력은 지원하지 않음.
    def _check(self) -> int:
        if not self.is_attached():
            raise RuntimeError("No window attached")
        return self._hwnd  # type: ignore[return-value]

    def _focus(self) -> None:
        """대상 윈도우를 전면으로 + 포커스. SendInput 모드 전제 조건."""
        hwnd = self._hwnd
        if not hwnd:
            return
        try:
            # 최소화 상태면 복원 — 복원 직후엔 페인팅 시간이 필요하므로 약간 더 대기.
            was_iconic = False
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    was_iconic = True
            except Exception:
                pass
            # 포어그라운드 락 회피 — AttachThreadInput 트릭
            try:
                fg = windll.user32.GetForegroundWindow()
                cur_thread = win32api.GetCurrentThreadId()
                fg_thread, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
                if fg_thread and fg_thread != cur_thread:
                    windll.user32.AttachThreadInput(cur_thread, fg_thread, True)
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                        # BringWindowToTop / SetFocus 보강 — SetForegroundWindow 단독으로
                        # 거부되는 경우(다른 스레드가 포어그라운드 락 보유)에 대비.
                        try:
                            win32gui.BringWindowToTop(hwnd)
                            win32gui.SetFocus(hwnd)
                        except Exception:
                            pass
                    finally:
                        windll.user32.AttachThreadInput(cur_thread, fg_thread, False)
                else:
                    win32gui.SetForegroundWindow(hwnd)
                    try:
                        win32gui.SetFocus(hwnd)
                    except Exception:
                        pass
            except Exception:
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            # 안정화 대기 — 포커스 전환 + 메시지 큐 처리 완료까지.
            # 0.05s 는 일부 환경(다중 모니터, 원격 데스크톱 등)에서 부족 → 0.15s 로 증가.
            # 최소화에서 복원했으면 페인팅까지 추가 대기.
            time.sleep(0.20 if was_iconic else 0.15)
        except Exception as e:
            logger.debug("WinControl focus failed: %s", e)

    # ── 액션 전후 컨텍스트(이전 활성 창 + 마우스 위치) 보존 ──────────
    def _save_context(self) -> dict:
        """현재 포어그라운드 hwnd + 마우스 커서 위치 캡처. 액션 후 복원에 사용."""
        ctx: dict = {"prev_fg": None, "cursor": None}
        try:
            fg = windll.user32.GetForegroundWindow()
            if fg and fg != self._hwnd and win32gui.IsWindow(fg):
                ctx["prev_fg"] = int(fg)
        except Exception:
            pass
        try:
            ctx["cursor"] = win32api.GetCursorPos()
        except Exception:
            pass
        return ctx

    def _restore_context(self, ctx: dict) -> None:
        """액션 후 이전 활성 창 + 마우스 커서 위치 복원."""
        if not ctx:
            return
        # 1) 이전 활성 창으로 포커스 복귀 (AttachThreadInput 트릭)
        prev_fg = ctx.get("prev_fg")
        if prev_fg:
            try:
                if win32gui.IsWindow(prev_fg):
                    cur_thread = win32api.GetCurrentThreadId()
                    target_thread, _ = win32process.GetWindowThreadProcessId(prev_fg)
                    attached = False
                    try:
                        if target_thread and target_thread != cur_thread:
                            attached = bool(
                                windll.user32.AttachThreadInput(cur_thread, target_thread, True)
                            )
                        win32gui.SetForegroundWindow(prev_fg)
                    except Exception:
                        try:
                            windll.user32.SetForegroundWindow(prev_fg)
                        except Exception:
                            pass
                    finally:
                        if attached:
                            try:
                                windll.user32.AttachThreadInput(cur_thread, target_thread, False)
                            except Exception:
                                pass
            except Exception as e:
                logger.debug("WinControl FG restore failed: %s", e)
        # 2) 마우스 커서 원위치 — SetCursorPos 직접 호출 (mouse_event 보다 깔끔)
        cursor = ctx.get("cursor")
        if cursor:
            try:
                win32api.SetCursorPos((int(cursor[0]), int(cursor[1])))
            except Exception as e:
                logger.debug("WinControl cursor restore failed: %s", e)

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

    # ── tap/click (FG = SendInput) ──────────────────────────────
    # 모든 send_* 는 액션 전 컨텍스트(이전 활성 창 + 마우스 위치)를 저장하고
    # finally 에서 복원 — 사용자가 작업 중이던 다른 창과 커서 위치를 방해하지 않는다.
    def send_tap(self, x: int, y: int, button: str = "left") -> None:
        self._check()
        ctx = self._save_context()
        try:
            self._focus()
            sx, sy = self._client_to_screen(int(x), int(y))
            self._send_input_mouse_move(sx, sy)
            # 마우스 이동 후 hover 인식 시간 — UWP/WinUI 컨트롤은 mousemove 처리 후
            # 클릭을 받아야 정상 동작.
            time.sleep(0.04)
            self._send_input_button(button, True)
            time.sleep(0.04)
            self._send_input_button(button, False)
            # OS 가 클릭을 처리할 시간 — 다음 액션(또는 컨텍스트 복원) 전 대기.
            time.sleep(0.06)
        finally:
            self._restore_context(ctx)

    def send_double_click(self, x: int, y: int) -> None:
        self._check()
        ctx = self._save_context()
        try:
            self._focus()
            sx, sy = self._client_to_screen(int(x), int(y))
            self._send_input_mouse_move(sx, sy)
            time.sleep(0.04)
            for _ in range(2):
                self._send_input_button("left", True)
                time.sleep(0.04)
                self._send_input_button("left", False)
                time.sleep(0.04)
            time.sleep(0.06)
        finally:
            self._restore_context(ctx)

    def send_long_press(self, x: int, y: int, duration_ms: int = 500, button: str = "left") -> None:
        """버튼을 누른 채로 duration_ms 만큼 유지 후 떼기.

        button: "left"(기본) / "right" / "middle".
        예) 우클릭 길게 = right 메뉴 트리거 (대부분의 앱은 mouse-up 시 컨텍스트 메뉴).
        """
        self._check()
        ctx = self._save_context()
        try:
            self._focus()
            sx, sy = self._client_to_screen(int(x), int(y))
            self._send_input_mouse_move(sx, sy)
            time.sleep(0.04)
            self._send_input_button(button, True)
            try:
                time.sleep(max(0.0, duration_ms / 1000.0))
            finally:
                self._send_input_button(button, False)
            time.sleep(0.06)
        finally:
            self._restore_context(ctx)

    def send_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._check()
        ctx = self._save_context()
        try:
            self._focus()
            steps = max(2, int(max(50, duration_ms) / 25))
            delay = max(0.0, duration_ms / 1000.0 / steps)
            sx1, sy1 = self._client_to_screen(int(x1), int(y1))
            self._send_input_mouse_move(sx1, sy1)
            time.sleep(0.04)
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
            time.sleep(0.04)
            self._send_input_button("left", False)
            time.sleep(0.06)
        finally:
            self._restore_context(ctx)

    def send_text(self, text: str) -> None:
        self._check()
        ctx = self._save_context()
        try:
            self._focus()
            for ch in text:
                # KEYEVENTF_UNICODE=0x0004
                win32api.keybd_event(0, ord(ch), 0x0004, 0)
                time.sleep(0.005)
                win32api.keybd_event(0, ord(ch), 0x0004 | 0x0002, 0)
                time.sleep(0.005)
            time.sleep(0.03)
        finally:
            self._restore_context(ctx)

    def send_key(self, key: str) -> None:
        """가상키 한 번 누르고 떼기 (modifier 미지원 — 단일 키만)."""
        self._check()
        vk = _resolve_vk(key)
        ctx = self._save_context()
        try:
            self._focus()
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            win32api.keybd_event(vk, 0, 0x0002, 0)  # KEYEVENTF_KEYUP
            time.sleep(0.03)
        finally:
            self._restore_context(ctx)
