import struct
import subprocess
import os
import time

d = open('/tmp/hkmc_test.bmp', 'rb').read()
off = struct.unpack_from('<I', d, 10)[0]
w = struct.unpack_from('<i', d, 18)[0]
h = abs(struct.unpack_from('<i', d, 22)[0])
rs = (w * 3 + 3) & ~3
rows = []
for y in range(h - 1, -1, -1):
    s = off + y * rs
    rows.append(d[s:s + w * 3])
raw = b''.join(rows)
print('BMP: %dx%d, raw size: %d bytes' % (w, h, len(raw)))

# raw BGR 데이터를 파일로 저장
with open('/tmp/hkmc_raw.bgr', 'wb') as f:
    f.write(raw)

# GStreamer: filesrc로 raw 읽기 -> JPEG 인코딩
caps = 'video/x-raw,format=BGR,width=%d,height=%d,framerate=0/1' % (w, h)
t0 = time.time()
p = subprocess.run(
    ['gst-launch-1.0', '-e', '-q',
     'filesrc', 'location=/tmp/hkmc_raw.bgr', '!',
     caps, '!',
     'videoconvert', '!',
     'jpegenc', 'quality=70', '!',
     'filesink', 'location=/tmp/hkmc_test.jpg'],
    capture_output=True,
)
elapsed = time.time() - t0

jpg_size = os.path.getsize('/tmp/hkmc_test.jpg') if os.path.exists('/tmp/hkmc_test.jpg') else 0
print('JPEG: %d bytes (%.1f KB)' % (jpg_size, jpg_size / 1024))
print('변환 시간: %.3f초' % elapsed)
if p.stderr:
    print('ERR:', p.stderr.decode(errors='replace')[:500])
if jpg_size > 0:
    print('SUCCESS')
else:
    print('FAILED')

# 정리
os.unlink('/tmp/hkmc_raw.bgr')
