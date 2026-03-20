"""harvesters + Vimba GenTL로 카메라 연결 테스트."""
import os
import glob

# 1) Vimba X GenTL producer (.cti) 파일 찾기
search_paths = [
    r"C:\Program Files\Allied Vision\Vimba X",
    r"C:\Program Files\Allied Vision\VimbaX",
    r"C:\Program Files (x86)\Allied Vision",
    r"C:\Program Files\Allied Vision",
    os.environ.get("VIMBA_X_HOME", ""),
    os.environ.get("VIMBA_HOME", ""),
]

cti_files = []
for base in search_paths:
    if base and os.path.isdir(base):
        found = glob.glob(os.path.join(base, "**", "*.cti"), recursive=True)
        cti_files.extend(found)

# 환경변수 GENICAM_GENTL64_PATH 도 확인
gentl_path = os.environ.get("GENICAM_GENTL64_PATH", "")
if gentl_path:
    for d in gentl_path.split(os.pathsep):
        if os.path.isdir(d):
            found = glob.glob(os.path.join(d, "*.cti"))
            cti_files.extend(found)

cti_files = list(set(cti_files))
print(f"발견된 .cti 파일 ({len(cti_files)}개):")
for f in cti_files:
    print(f"  {f}")
print()

if not cti_files:
    print("[FAIL] GenTL producer (.cti) 파일을 찾을 수 없습니다.")
    print("  GENICAM_GENTL64_PATH:", os.environ.get("GENICAM_GENTL64_PATH", "(없음)"))
    print("  Vimba X SDK 설치 경로를 확인하세요.")
    exit(1)

# 2) harvesters로 카메라 탐색
from harvesters.core import Harvester
h = Harvester()

for cti in cti_files:
    try:
        h.add_file(cti)
        print(f"[OK] CTI 로드: {os.path.basename(cti)}")
    except Exception as e:
        print(f"[FAIL] CTI 로드 실패: {os.path.basename(cti)} — {e}")

h.update()
print(f"\n카메라 {len(h.device_info_list)}대 발견:")
for i, info in enumerate(h.device_info_list):
    print(f"  [{i}] {info.id_} / {info.model} / {info.serial_number}")

if not h.device_info_list:
    print("[FAIL] 카메라를 찾지 못했습니다.")
    h.reset()
    exit(1)

# 3) 첫 번째 카메라로 캡처 테스트
print(f"\n첫 번째 카메라로 캡처 테스트...")
try:
    ia = h.create(0)
    ia.start()

    with ia.fetch(timeout=5) as buffer:
        component = buffer.payload.components[0]
        w = component.width
        h_val = component.height
        print(f"[OK] 캡처 성공: {w}x{h_val}")

        # numpy array로 변환
        import numpy as np
        img = component.data.reshape(h_val, w, -1) if component.data.ndim == 1 else component.data
        print(f"     shape: {img.shape}, dtype: {img.dtype}")

    ia.stop()
    ia.destroy()
    print("[OK] 테스트 완료!")
except Exception as e:
    print(f"[FAIL] 캡처 실패: {e}")

h.reset()
