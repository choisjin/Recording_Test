"""VisionCamera IP 관련 기능 테스트."""
import os, socket, struct

print("=== 1. vmbpy (Vimba X Python API) 확인 ===")
try:
    import vmbpy
    print(f"  vmbpy OK: {vmbpy.__version__}")
    with vmbpy.VmbSystem.get_instance() as vmb:
        cams = vmb.get_all_cameras()
        print(f"  카메라 수: {len(cams)}")
        for cam in cams:
            print(f"  --- {cam.get_id()} ---")
            with cam:
                for feat_name in ["GevCurrentIPAddress", "GevCurrentSubnetMask",
                                  "GevCurrentDefaultGateway", "DeviceModelName",
                                  "DeviceSerialNumber"]:
                    try:
                        feat = cam.get_feature_by_name(feat_name)
                        val = feat.get()
                        if "IP" in feat_name or "Subnet" in feat_name or "Gateway" in feat_name:
                            ip_str = socket.inet_ntoa(struct.pack(">I", val))
                            print(f"    {feat_name}: {val} ({ip_str})")
                        else:
                            print(f"    {feat_name}: {val}")
                    except Exception as e:
                        print(f"    {feat_name}: N/A ({e})")

        print(f"\n  인터페이스 수: {len(vmb.get_all_interfaces())}")
        for iface in vmb.get_all_interfaces():
            print(f"  --- Interface: {iface.get_id()} ---")
            with iface:
                for feat_name in ["DeviceSelector", "GevDeviceIPAddress",
                                  "GevDeviceSubnetMask", "GevDeviceMACAddress",
                                  "GevDeviceForceIPAddress"]:
                    try:
                        feat = iface.get_feature_by_name(feat_name)
                        val = feat.get()
                        if "IP" in feat_name or "Subnet" in feat_name:
                            ip_str = socket.inet_ntoa(struct.pack(">I", val))
                            print(f"    {feat_name}: {val} ({ip_str})")
                        else:
                            print(f"    {feat_name}: {val}")
                    except Exception as e:
                        print(f"    {feat_name}: N/A ({e})")
except ImportError:
    print("  vmbpy 미설치")
    vimba_home = os.environ.get("VIMBA_X_HOME", r"C:\Program Files\Allied Vision\Vimba X")
    vmbpy_path = os.path.join(vimba_home, "api", "python")
    if os.path.isdir(vmbpy_path):
        print(f"  설치 가능 경로 발견: {vmbpy_path}")
        for item in os.listdir(vmbpy_path):
            print(f"    {item}")
    else:
        print(f"  vmbpy 경로 없음: {vmbpy_path}")
except Exception as e:
    print(f"  에러: {e}")

print("\n=== 2. PC 네트워크 인터페이스 ===")
try:
    import ifaddr
    adapters = ifaddr.get_adapters()
    for adapter in adapters:
        ips = [ip for ip in adapter.ips if ip.is_IPv4]
        if ips:
            for ip in ips:
                print(f"  {adapter.nice_name}: {ip.ip}/{ip.network_prefix}")
except ImportError:
    print("  ifaddr 미설치, socket 방식:")
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            print(f"  {info[4][0]}")
    except Exception:
        pass

print("\n=== 3. Harvester device_info 상세 ===")
try:
    from harvesters.core import Harvester
    import glob
    cti_files = []
    for d in [r"C:\Program Files\Allied Vision\Vimba X\cti"]:
        if os.path.isdir(d):
            cti_files.extend(glob.glob(os.path.join(d, "*.cti")))

    if cti_files:
        h = Harvester()
        for c in cti_files:
            h.add_file(c)
        h.update()
        print(f"  카메라 수: {len(h.device_info_list)}")
        for info in h.device_info_list:
            print(f"  --- device ---")
            for attr in dir(info):
                if not attr.startswith("_"):
                    try:
                        val = getattr(info, attr)
                        if not callable(val):
                            print(f"    {attr}: {val}")
                    except Exception:
                        pass
        h.reset()
except ImportError:
    print("  harvesters 미설치")
except Exception as e:
    print(f"  에러: {e}")

input("\n아무 키나 눌러 종료...")
