"""GVCP Discovery - GigE Vision cameras IP scan via UDP broadcast."""
import socket, struct, time

GVCP_PORT = 3956
DISCOVERY_CMD = b'\x42\x11\x00\x02\x00\x00\x00\x01'  # key=0x42, flag=0x11, cmd=0x0002, len=0, req_id=1

def parse_discovery_ack(data):
    """Parse GVCP Discovery ACK payload."""
    if len(data) < 248 + 8:
        return None
    # Header: 8 bytes (status 2 + ack_cmd 2 + length 2 + req_id 2)
    payload = data[8:]

    mac_high = struct.unpack(">H", payload[10:12])[0]
    mac_low = struct.unpack(">I", payload[12:16])[0]
    mac_int = (mac_high << 32) | mac_low
    mac_str = "{:012X}".format(mac_int)

    ip = socket.inet_ntoa(payload[36:40])
    subnet = socket.inet_ntoa(payload[52:56])
    gateway = socket.inet_ntoa(payload[68:72])

    manufacturer = payload[72:104].split(b'\x00')[0].decode('utf-8', errors='replace')
    model = payload[104:136].split(b'\x00')[0].decode('utf-8', errors='replace')
    serial = payload[216:232].split(b'\x00')[0].decode('utf-8', errors='replace')
    user_name = payload[232:248].split(b'\x00')[0].decode('utf-8', errors='replace')

    return {
        "mac": mac_str,
        "ip": ip,
        "subnet": subnet,
        "gateway": gateway,
        "manufacturer": manufacturer,
        "model": model,
        "serial": serial,
        "user_name": user_name,
    }

# Send discovery broadcast on all interfaces
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.settimeout(2.0)
sock.bind(('', 0))
sock.sendto(DISCOVERY_CMD, ('255.255.255.255', GVCP_PORT))

print("=== GVCP Discovery (UDP broadcast port 3956) ===")
cameras = []
deadline = time.time() + 2.0
while time.time() < deadline:
    try:
        data, addr = sock.recvfrom(4096)
        cam = parse_discovery_ack(data)
        if cam:
            cam["source_ip"] = addr[0]
            cameras.append(cam)
            print(f"\nCamera from {addr[0]}:")
            for k, v in cam.items():
                print(f"  {k}: {v}")
    except socket.timeout:
        break
sock.close()

if not cameras:
    print("  No GigE cameras found.")
print(f"\nTotal: {len(cameras)} camera(s)")
