"""VisionCamera DLL 직접 테스트 스크립트.

테스트 PC에서 실행:
  python test_vision_camera.py
"""
import os
import sys
import ctypes
import shutil
from pathlib import Path

# --- 설정 ---
MAC = "AC4FFC00A43C"
MODEL = "eco267CVGE"
MODULES_DIR = Path(__file__).parent / "backend" / "app" / "modules"

print(f"Python: {sys.version} ({'64bit' if sys.maxsize > 2**32 else '32bit'})")
print(f"Modules dir: {MODULES_DIR}")
print(f"DLL files: {[f.name for f in MODULES_DIR.iterdir() if f.suffix == '.dll']}")
print()

# 1) kernel32 준비
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
kernel32.SetDllDirectoryW.restype = ctypes.c_int
kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
kernel32.LoadLibraryExW.restype = ctypes.c_void_p
kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]

# 2) SetDllDirectory
kernel32.SetDllDirectoryW(str(MODULES_DIR))
print(f"[OK] SetDllDirectory: {MODULES_DIR}")

# 3) LikLGATSImgLib64.dll 로드
dep_path = str(MODULES_DIR / "LikLGATSImgLib64.dll")
try:
    dep = ctypes.CDLL(dep_path, winmode=0)
    print(f"[OK] LikLGATSImgLib64.dll loaded")
except Exception as e:
    print(f"[FAIL] LikLGATSImgLib64.dll: {e}")
    sys.exit(1)

# 4) MATVisionLib 복사 + 로드
original = MODULES_DIR / "MATVisionLib.dll"
dll_path = str(MODULES_DIR / f"MATVisionLib_{MAC}.dll")
if not os.path.exists(dll_path):
    shutil.copyfile(str(original), dll_path)

try:
    dll = ctypes.CDLL(dll_path, winmode=0)
    print(f"[OK] MATVisionLib_{MAC}.dll loaded")
except Exception as e:
    print(f"[FAIL] MATVisionLib_{MAC}.dll: {e}")
    sys.exit(1)

# 5) Vision_Connect 호출 (타임아웃 주의 — Ctrl+C로 중단 가능)
print()
print(f"Calling Vision_Connect('{MAC}')...")
print("(30초 이상 걸리면 Ctrl+C로 중단)")
print()

import threading
import time

result = [None]
error = [None]

def _connect():
    try:
        ret = dll.Vision_Connect(ctypes.c_wchar_p(MAC))
        result[0] = ret
    except Exception as e:
        error[0] = e

t = threading.Thread(target=_connect, daemon=True)
t.start()
t.join(timeout=30)

if t.is_alive():
    print("[TIMEOUT] Vision_Connect가 30초 이상 응답 없음")
    print()
    print("원인 가능성:")
    print("  1) SVGigE SDK 런타임이 설치되어 있지 않음")
    print("  2) 카메라가 다른 프로세스에 의해 점유됨")
    print("  3) GigE 네트워크 필터 드라이버 충돌")
    print()
    print("확인 사항:")
    print("  - SVS-VISTEK SVGigE SDK가 설치되어 있는지 확인")
    print("  - 'SVGigE.dll' 파일이 시스템에 존재하는지 검색")
    print("    dir C:\\ /s /b SVGigE*.dll 2>nul")
elif error[0]:
    print(f"[ERROR] {error[0]}")
else:
    ret = result[0]
    if ret == 0:
        print(f"[OK] Vision_Connect returned {ret} (성공!)")
        print("Disconnecting...")
        dll.Vision_Disconnect()
        print("[OK] Disconnected")
    else:
        print(f"[FAIL] Vision_Connect returned {ret} (실패)")
