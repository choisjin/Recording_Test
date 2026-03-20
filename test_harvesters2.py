"""harvesters 캡처 테스트 (타임아웃 + 에러 처리 강화)."""
import os, glob, sys
import numpy as np

# CTI 찾기
cti_paths = []
gentl = os.environ.get("GENICAM_GENTL64_PATH", "")
for d in [r"C:\Program Files\Allied Vision\Vimba X\cti"] + (gentl.split(os.pathsep) if gentl else []):
    if os.path.isdir(d):
        cti_paths.extend(glob.glob(os.path.join(d, "*.cti")))

from harvesters.core import Harvester
h = Harvester()
for cti in set(cti_paths):
    h.add_file(cti)
h.update()

# MAC으로 카메라 찾기
TARGET = "DEV_AC4FFC00A43C"
idx = None
for i, info in enumerate(h.device_info_list):
    print(f"  [{i}] {info.id_} / {info.model}")
    if TARGET in info.id_:
        idx = i

if idx is None:
    print(f"[FAIL] {TARGET} 카메라 없음")
    h.reset()
    sys.exit(1)

print(f"\n[{idx}] 카메라 열기: {TARGET}")
try:
    ia = h.create(idx)
    print("[OK] create 완료")

    # 노드맵 확인 (픽셀 포맷)
    node_map = ia.remote_device.node_map
    try:
        fmt = node_map.PixelFormat.value
        print(f"     PixelFormat: {fmt}")
    except:
        pass
    try:
        w = node_map.Width.value
        h_val = node_map.Height.value
        print(f"     Resolution: {w}x{h_val}")
    except:
        pass

    print("start acquisition...")
    ia.start()
    print("[OK] acquisition started, fetching frame (timeout=10s)...")

    with ia.fetch(timeout=10) as buffer:
        comp = buffer.payload.components[0]
        print(f"[OK] 캡처 성공: {comp.width}x{comp.height}")

        # 이미지 저장 테스트
        from PIL import Image
        data = comp.data
        if data.ndim == 1:
            # Mono
            if comp.width * comp.height == len(data):
                img_arr = data.reshape(comp.height, comp.width)
            else:
                # RGB/BGR packed
                img_arr = data.reshape(comp.height, comp.width, -1)
        else:
            img_arr = data

        print(f"     shape: {img_arr.shape}, dtype: {img_arr.dtype}")

        if img_arr.ndim == 2:
            img = Image.fromarray(img_arr, 'L')
        elif img_arr.shape[2] == 3:
            img = Image.fromarray(img_arr, 'RGB')
        else:
            img = Image.fromarray(img_arr[:,:,:3], 'RGB')

        img.save("test_capture.png")
        print("[OK] test_capture.png 저장 완료!")

    ia.stop()
    ia.destroy()
    print("[OK] 완료!")

except Exception as e:
    print(f"[FAIL] {e}")
    import traceback
    traceback.print_exc()

h.reset()
