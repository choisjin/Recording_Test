"""Vimba Python API 확인 스크립트."""
import sys
print(f"Python: {sys.version}")

# VimbaX (vmbpy) 확인
try:
    import vmbpy
    print(f"[OK] vmbpy (VimbaX) version: {vmbpy.__version__}")
    with vmbpy.VmbSystem.get_instance() as vmb:
        cams = vmb.get_all_cameras()
        print(f"     카메라 {len(cams)}대 발견:")
        for c in cams:
            print(f"       - {c.get_id()} / {c.get_name()} / {c.get_interface_id()}")
except ImportError:
    print("[SKIP] vmbpy not installed")
except Exception as e:
    print(f"[FAIL] vmbpy: {e}")

# Vimba (구버전) 확인
try:
    from vimba import Vimba
    print(f"[OK] vimba (legacy)")
    with Vimba.get_instance() as vmb:
        cams = vmb.get_all_cameras()
        print(f"     카메라 {len(cams)}대 발견:")
        for c in cams:
            print(f"       - {c.get_id()} / {c.get_name()}")
except ImportError:
    print("[SKIP] vimba not installed")
except Exception as e:
    print(f"[FAIL] vimba: {e}")

# harvesters 확인
try:
    from harvesters.core import Harvester
    print("[OK] harvesters installed")
except ImportError:
    print("[SKIP] harvesters not installed")

print()
print("위 결과를 알려주세요.")
