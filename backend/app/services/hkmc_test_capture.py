#!/usr/bin/env python3
"""HKMC 에이전트 캡처 테스트 — 이미지 포맷 및 크기 확인용.

디바이스에서 실행:
    python3 /tmp/hkmc_test_capture.py
"""
import socket
import struct
import time

HOST = "127.0.0.1"
PORT = 6655
START_BIT = 0x61
END_BIT = 0x6F
CMD_GETIMG = 0x6A
CMD_ATSA_GETVERSION = 0xA0

HANDSHAKE_VALUES = {
    "6161000000035e002185fd6f6f",
    "6161000000035e0000df856f6f",
}


def calc_crc16(data):
    crc = 0xFFFF
    key = 0xC659
    for b in data:
        tmp = (b & 0xFF) ^ (crc & 0x00FF)
        for _ in range(8):
            tmp = (tmp >> 1) ^ key if tmp & 1 else tmp >> 1
        crc = (crc >> 8) ^ tmp
    return crc


def build_packet(cmd, sub_cmd, resp, data):
    agent_cmd = [cmd, sub_cmd, resp] + data
    crc = calc_crc16(agent_cmd)
    plen = len(agent_cmd)
    pkt = [START_BIT, START_BIT]
    pkt += [(plen >> 24) & 0xFF, (plen >> 16) & 0xFF, (plen >> 8) & 0xFF, plen & 0xFF]
    pkt += agent_cmd
    pkt += [(crc >> 8) & 0xFF, crc & 0xFF, END_BIT, END_BIT]
    return bytes(pkt)


def main():
    print(f"[1] {HOST}:{PORT} 연결 중...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.connect((HOST, PORT))

    # 핸드셰이크
    raw = sock.recv(13)
    if raw.hex() not in HANDSHAKE_VALUES:
        print(f"핸드셰이크 실패: {raw.hex()}")
        return
    print("[2] 핸드셰이크 성공")

    # 버전 요청
    sock.send(build_packet(CMD_ATSA_GETVERSION, 0, 0, []))
    time.sleep(0.5)

    # 이미지 요청 (1920x720, front_center=8)
    w, h = 1920, 720
    data = []
    for v in (0, 0, w, h):
        data += [(v >> 8) & 0xFF, v & 0xFF]
    data += [0, 8]  # screen_type_bits = 8 (front_center)
    sock.send(build_packet(CMD_GETIMG, 0, 0, data))

    print("[3] 이미지 요청 전송, 응답 대기...")

    # 응답 수신
    buf = b""
    start = time.time()
    img_data = None
    while time.time() - start < 15:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk

        while len(buf) >= 6:
            if buf[0] != START_BIT or buf[1] != START_BIT:
                idx = buf.find(bytes([START_BIT, START_BIT]), 1)
                buf = buf[idx:] if idx != -1 else b""
                if not buf:
                    break
                continue

            plen = struct.unpack(">I", buf[2:6])[0]
            total = 6 + plen + 4
            if len(buf) < total:
                break

            packet = buf[:total]
            buf = buf[total:]
            cmd = packet[6]

            if cmd == CMD_ATSA_GETVERSION:
                ver = packet[9:9 + plen - 3].decode("iso-8859-1", errors="replace")
                print(f"[*] 에이전트 버전: {ver}")
            elif cmd == CMD_GETIMG:
                img_data = packet[9:9 + plen - 3]
                elapsed = time.time() - start
                break

        if img_data is not None:
            break

    sock.close()

    if img_data:
        print(f"[4] 이미지 수신 완료!")
        print(f"    크기: {len(img_data)} bytes ({len(img_data)/1024:.1f} KB)")
        print(f"    수신 시간: {elapsed:.3f}초")
        print(f"    처음 20바이트 (hex): {img_data[:20].hex()}")

        # 포맷 감지
        if img_data[:2] == b'\xff\xd8':
            print("    포맷: JPEG")
        elif img_data[:2] == b'BM':
            w2 = struct.unpack_from('<i', img_data, 18)[0]
            h2 = struct.unpack_from('<i', img_data, 22)[0]
            bpp = struct.unpack_from('<H', img_data, 28)[0]
            print(f"    포맷: BMP ({w2}x{h2}, {bpp}bpp)")
        elif img_data[:4] == b'\x89PNG':
            print("    포맷: PNG")
        else:
            print("    포맷: 알 수 없음")

        # 파일로 저장
        ext = "jpg" if img_data[:2] == b'\xff\xd8' else "bmp"
        path = f"/tmp/hkmc_test.{ext}"
        with open(path, "wb") as f:
            f.write(img_data)
        print(f"    저장: {path}")
    else:
        print("[!] 이미지 수신 실패")


if __name__ == "__main__":
    main()
