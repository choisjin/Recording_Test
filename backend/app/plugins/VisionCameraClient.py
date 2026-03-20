# -*- coding: utf-8 -*-
"""VisionCamera DLL 제어 클라이언트.

MATVisionLib.dll을 통해 비전 카메라 연결/캡처/비교 등을 수행.
References/VisionCameraClient.py 기반으로 Robot Framework 의존성을 제거하고
플러그인 구조에 맞게 재구성.
"""

import os
import sys
import shutil
import logging
import ctypes
from ctypes import c_wchar_p
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)

# modules/ 디렉토리 (DLL 원본 위치)
_MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"

# use_last_error=True 로 kernel32 로드해야 GetLastError가 정확함
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# 64bit Python에서 반드시 restype/argtypes 선언 필요 (기본 c_int는 포인터 잘림)
_kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
_kernel32.LoadLibraryExW.restype = ctypes.c_void_p

_kernel32.LoadLibraryW.argtypes = [ctypes.c_wchar_p]
_kernel32.LoadLibraryW.restype = ctypes.c_void_p

_kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
_kernel32.FreeLibrary.restype = ctypes.c_int

_kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
_kernel32.SetDllDirectoryW.restype = ctypes.c_int


def _try_load_dll(dll_path: str, modules_dir: str) -> ctypes.CDLL:
    """단계별 진단과 함께 DLL 로드를 시도한다."""
    diag = []

    # --- 진단 1: 파일 존재 확인 ---
    if not os.path.isfile(dll_path):
        raise FileNotFoundError(f"DLL 파일 없음: {dll_path}")
    diag.append(f"파일 존재: OK ({os.path.getsize(dll_path)} bytes)")

    # --- 진단 2: DONT_RESOLVE_DLL_REFERENCES 로 PE 유효성 확인 ---
    # 의존성 해석 없이 DLL 파일 자체만 로드 — 파일 손상/아키텍처 불일치 검출
    DONT_RESOLVE = 0x00000001
    handle = _kernel32.LoadLibraryExW(dll_path, None, DONT_RESOLVE)
    if not handle:
        err = ctypes.get_last_error()
        diag.append(f"PE 유효성: FAIL (LoadLibraryExW DONT_RESOLVE error={err})")
        detail = "\n".join(diag)
        raise OSError(
            f"DLL 파일 자체가 유효하지 않음 (손상 또는 아키텍처 불일치).\n"
            f"Python: {'64bit' if sys.maxsize > 2**32 else '32bit'}\n"
            f"{detail}"
        )
    _kernel32.FreeLibrary(handle)
    diag.append("PE 유효성: OK")

    # --- 진단 3: SetDllDirectory 설정 후 정상 로드 시도 ---
    _kernel32.SetDllDirectoryW(modules_dir)
    try:
        dll = ctypes.CDLL(dll_path, winmode=0)
        diag.append("로드: OK (SetDllDirectory + winmode=0)")
        logger.info("DLL loaded: %s\n%s", dll_path, "\n".join(diag))
        return dll
    except Exception as e:
        err = ctypes.get_last_error()
        diag.append(f"로드 실패: {e} (GetLastError={err})")
    finally:
        _kernel32.SetDllDirectoryW(None)

    # --- 진단 4: os.add_dll_directory 시도 ---
    if hasattr(os, "add_dll_directory"):
        try:
            cookie = os.add_dll_directory(modules_dir)
            try:
                dll = ctypes.CDLL(dll_path)
                diag.append("로드: OK (add_dll_directory)")
                logger.info("DLL loaded: %s\n%s", dll_path, "\n".join(diag))
                return dll
            except Exception as e:
                diag.append(f"add_dll_directory 로드 실패: {e}")
            finally:
                cookie.close()
        except Exception as e:
            diag.append(f"add_dll_directory 등록 실패: {e}")

    # 모든 방법 실패 → 상세 진단 출력
    detail = "\n  ".join(diag)
    raise OSError(
        f"DLL 의존성 해석 실패: {Path(dll_path).name}\n"
        f"  {detail}\n"
        f"  → DLL 파일은 유효하지만 의존하는 다른 DLL을 찾을 수 없습니다.\n"
        f"  → 테스트 PC에 GigE Vision / SVGigE SDK가 설치되어 있는지 확인하세요.\n"
        f"  → 또는 누락된 DLL을 modules/ 디렉토리에 복사하세요."
    )


def _diagnose_dependency(dll_name: str, modules_dir: str) -> str:
    """개별 DLL 로드를 시도하고 결과를 문자열로 반환."""
    dll_path = os.path.join(modules_dir, dll_name)
    if not os.path.isfile(dll_path):
        return f"{dll_name}: 파일 없음"

    # PE 유효성만 확인
    DONT_RESOLVE = 0x00000001
    handle = _kernel32.LoadLibraryExW(dll_path, None, DONT_RESOLVE)
    if not handle:
        err = ctypes.get_last_error()
        return f"{dll_name}: PE 로드 실패 (error={err}) — 아키텍처 불일치 또는 파일 손상"
    _kernel32.FreeLibrary(handle)

    # 의존성 포함 로드
    _kernel32.SetDllDirectoryW(modules_dir)
    handle = _kernel32.LoadLibraryW(dll_path)
    err = ctypes.get_last_error()
    _kernel32.SetDllDirectoryW(None)
    if handle:
        _kernel32.FreeLibrary(handle)
        return f"{dll_name}: OK (로드 성공)"
    else:
        return f"{dll_name}: 의존성 실패 (error={err}) — 이 DLL이 필요로 하는 다른 DLL이 없음"


class VisionCameraClient:
    """MATVisionLib.dll 기반 비전 카메라 제어."""

    def __init__(self, model: str, port: dict, context=None):
        self._device = model
        self._isConnected = False
        self._port = port.get("Port", "")
        self._macaddress = port.get("MACAddress", "")

        modules_str = str(_MODULES_DIR)

        # PATH에 modules 추가
        path_env = os.environ.get("PATH", "")
        if modules_str not in path_env:
            os.environ["PATH"] = modules_str + os.pathsep + path_env

        # DLL 복사본 생성
        original_dll = _MODULES_DIR / "MATVisionLib.dll"
        if not original_dll.exists():
            original_dll = Path(__file__).parent / "MATVisionLib.dll"
        if not original_dll.exists():
            raise FileNotFoundError(f"MATVisionLib.dll not found in {_MODULES_DIR}")

        self.myDllPath = str(_MODULES_DIR / f"MATVisionLib_{self._macaddress}.dll")
        if not os.path.exists(self.myDllPath):
            shutil.copyfile(str(original_dll), self.myDllPath)

        # 의존 DLL 개별 진단 후 메인 DLL 로드
        dep_results = []
        for dll_name in ["LikLGATSImgLib64.dll", "MATVisionLib.dll"]:
            result = _diagnose_dependency(dll_name, modules_str)
            dep_results.append(result)
            logger.info("[VisionCamera DLL 진단] %s", result)

        try:
            self.myDll = _try_load_dll(self.myDllPath, modules_str)
        except OSError as e:
            # 의존성 진단 결과를 에러 메시지에 포함
            dep_detail = "\n  ".join(dep_results)
            raise OSError(f"{e}\n\n[의존 DLL 진단]\n  {dep_detail}") from None

        self._context = context
        logger.info("VisionCameraClient initialized: model=%s mac=%s", model, self._macaddress)

    def md_VisionConnect(self) -> tuple[bool, str]:
        if self._isConnected:
            return True, "[VisionCamera] Already connected"
        result = self.myDll.Vision_Connect(c_wchar_p(self._macaddress))
        if result == 0:
            self._isConnected = True
            return True, "[VisionCamera] Connect OK"
        else:
            self._isConnected = False
            return False, f"[VisionCamera] Connect fail (code={result})"

    def md_VisionDisconnect(self) -> tuple[bool, str]:
        if not self._isConnected:
            return True, "[VisionCamera] Already disconnected"
        result = self.myDll.Vision_Disconnect()
        if result == 0:
            self._isConnected = False
            return True, "[VisionCamera] Disconnect OK"
        else:
            return False, f"[VisionCamera] Disconnect fail (code={result})"

    def md_IsConnect(self) -> tuple[bool, str]:
        if not self._isConnected:
            return False, "[VisionCamera] Not connected"
        result = self.myDll.isConnect()
        if result == 0:
            return True, "[VisionCamera] Connected"
        elif result == -2:
            self._isConnected = False
            return False, f"[VisionCamera] Connection lost (code={result})"
        else:
            self._isConnected = False
            return False, f"[VisionCamera] Error (code={result})"

    def md_VisionCapture(self, szPath: str, left=-1, top=-1, right=-1, bottom=-1) -> tuple[bool, str]:
        if not self._isConnected:
            return False, "[VisionCamera] Not connected"
        result = self.myDll.Vision_Capture(c_wchar_p(szPath))
        if result == 0:
            if left >= 0 and top >= 0 and right >= 0 and bottom >= 0:
                img = Image.open(szPath)
                cropped = img.crop((left, top, right, bottom))
                cropped.save(szPath)
            return True, "[VisionCamera] Capture OK"
        elif result == -2:
            self._isConnected = False
            return False, f"[VisionCamera] Connection lost (code={result})"
        else:
            return False, f"[VisionCamera] Capture fail (code={result})"

    @property
    def is_connected(self) -> bool:
        return self._isConnected

    def dispose(self):
        try:
            if self.myDll is not None:
                self.md_VisionDisconnect()
                del self.myDll
                self.myDll = None
                if os.path.exists(self.myDllPath):
                    os.remove(self.myDllPath)
        except Exception:
            pass
