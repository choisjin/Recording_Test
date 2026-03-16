# -*- coding: utf-8 -*-
import CCIC_BASIC_LIBRARY_new
import CCIC_DEFINITION_LIBRARY
from CCIC_BASIC_LIBRARY_new import *
from CCIC_DEFINITION_LIBRARY import *

import socket
import threading
from time import sleep
import binascii
import cv2
import win32gui
from PIL import ImageGrab, ImageChops, Image
from pywinauto.win32defines import BM_CLICK
from win32gui import FindWindowEx, SendMessage, FindWindow
import ctypes
import subprocess
import xml_report
from xml_report import *

import inspect
from inspect import *

import init_config

import SmartBenchClient

'''
    UDP Control
'''
global UDP_SOCKET


def print_console(str_text):
    # global AUTO_LOGGER
    # AUTO_LOGGER.info("[#{}] {} - {}".format(CCIC_DEFINITION_LIBRARY.TEST_CYCLE, data, xml_report.REPORT.get_test_time()))
    if xml_report.REPORT != None:
        CCIC_BASIC_LIBRARY_new.AUTO_LOGGER.info(
            "[#{}] {} - {}".format(xml_report.REPORT.test_cycle, str_text, xml_report.REPORT.get_test_time()))
    else:
        if xml_report.REPORT == None:
            CCIC_BASIC_LIBRARY_new.print_console_random(str_text)
        else:
            print("check report module")


def UDP_INIT():
    global UDP_SOCKET

    try:
        UDP_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        print_console("UDP Socket created.")
    except socket.error as msg:
        print_console("Failed to create socket. Error Code : " + str(msg[0]) + " Message " + str(msg[1]))
        sys.exit()

    try:
        UDP_SOCKET.connect((BENCH_IP, BENCH_PORT))
        print_console("UDP Socket connected.")
    except socket.error as msg:
        print_console("Connect failed. Error Code : " + str(msg[0]) + " Message " + str(msg[1]))
        sys.exit()

    return UDP_SOCKET


def UDP_DEINIT():
    global UDP_SOCKET
    UDP_SOCKET.close()
    print_console("UDP Socket closed.")


def UDP_SEND(data, recv=True):
    global UDP_SOCKET

    packet = []

    data_len = len(data) - 2

    packet.append(0x55)  # Start 0x55AA
    packet.append(0xAA)

    packet.append(100)  # sender :100 , receiver : 205

    # packet.append(seq_cnt)  # sequence count 0 ~ 255 (not used)

    packet.append(0)  # sequence count 0 ~ 255 (not used)
    packet.append(data[0])  # cmd code
    packet.append(data[1])

    packet.append((data_len >> 8) & 0xFF)
    packet.append((data_len) & 0xFF)

    for i in range(data_len):
        packet.append(data[2 + i])

    encode_packet = bytearray(packet)
    hex_array = str.join("", (" 0x%02X" % i for i in packet))

    try:
        send_res = UDP_SOCKET.sendto(encode_packet, (BENCH_IP, BENCH_PORT))

        if send_res == len(encode_packet):
            print_console("[{}] Send UDP Packet -{}".format(currentframe().f_code.co_name, hex_array))
            if recv == False:
                return True
            recv_timeout = 60
            current_time = time.time()
            res = True
            while((time.time()-current_time)<recv_timeout):
                UDP_SOCKET.settimeout(1)
                UDP_RETURN_DATA = UDP_SOCKET.recv(16)
                UDP_SOCKET.settimeout(None)
                udp_return_data_list = [int(c) for c in UDP_RETURN_DATA]
                print("packet: {}\nrecv  : {}".format(packet, udp_return_data_list))
                for idx, packet_value in enumerate(packet):
                    if idx == 2:
                        continue
                    elif packet_value != udp_return_data_list[idx]:
                        res = False
                        break
                    # print(packet_value, udp_return_data_list[idx])
                    if idx == 5:
                        res = True
                        break
                if res:
                    break

            if not res:
                print("check time out!!!!!!!!!!!!!!!!")
                UDP_RETURN_DATA = None

            if UDP_RETURN_DATA is not None:
                # recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(ord(c)) for c in UDP_RETURN_DATA]))  # Python2.7
                recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(c) for c in UDP_RETURN_DATA]))  # Python3.x
                print_console("[{}] Recv UDP Packet -{}".format(currentframe().f_code.co_name, recv_hex_array))
                # return [int(ord(c)) for c in UDP_RETURN_DATA]  # Python2.7
                # print("return data: \n{}{}\n{}{}".format(udp_return_data_list,type(udp_return_data_list)
                #                                          ,packet,type(packet)))
                return udp_return_data_list  # [int(c) for c in UDP_RETURN_DATA]  # Python3.x
            else:
                print_console("[{}] Abnormal recv Data".format(currentframe().f_code.co_name))
                return True

            return True
        else:
            print_console("[{}] Send UDP Packet Failed".format(currentframe().f_code.co_name))
            return False

    except Exception as e:
        print(e)
        print_console("[{}] Please Initialize UDP Socket!!".format(currentframe().f_code.co_name))
        return False


def UDP_CANFD_INIT(baudrate=0x1F4, databit_time=0x7D0):  # 1F4 = 500, 7D0 = 2000
    global UDP_SOCKET

    START_WORD_1 = 0x55
    START_WORD_2 = 0xAA
    sender = 100
    seq_count = 0  # 0~255
    cmd_code_1st = 0x04
    cmd_code_open_write = 0x10
    cmd_code_tx_write = 0x30

    can_type_1 = 0
    can_type_2 = 0
    baudrate_1 = (baudrate >> (8)) & 0x00ff
    baudrate_2 = (baudrate) & 0x00ff
    databit_time_1 = (databit_time >> (8)) & 0x00ff
    databit_time_2 = (databit_time) & 0x00ff
    data = []
    data.append(can_type_1)
    data.append(can_type_2)
    data.append(baudrate_1)
    data.append(baudrate_2)
    data.append(databit_time_1)
    data.append(databit_time_2)

    packet = []

    packet.append(START_WORD_1)
    packet.append(START_WORD_2)
    packet.append(sender)
    packet.append(seq_count)
    packet.append(cmd_code_1st)
    packet.append(cmd_code_open_write)

    # data size
    packet.append(len(data) >> 8 & 0xff)
    packet.append(len(data) & 0xff)  # append length
    packet.extend(data)

    encode_packet = bytearray(packet)
    send_res = UDP_SOCKET.sendto(encode_packet, (BENCH_IP, BENCH_PORT))

    # print('[{}]'.format(', '.join(hex(x) for x in packet)))


def UDP_CANFD_SEND(canid, payload):
    global UDP_SOCKET

    START_WORD_1 = 0x55
    START_WORD_2 = 0xAA
    sender = 100
    seq_count = 0  # 0~255
    cmd_code_1st = 0x04
    cmd_code_tx_write = 0x30

    can_type_1 = 0
    can_type_2 = 0
    NOT_USE = 0
    DLC = len(payload)
    '''DLC Code
    DLC / Payload size
    0       0
    1       1
    ...       ...
    8       8
    9       12
    10      16
    11      20
    12      24
    13      32
    14      48
    15      64
    '''
    DLC_code = {
        9: 12,
        10: 16,
        11: 20,
        12: 24,
        13: 32,
        14: 48,
        15: 64, }
    if DLC > 8:
        print("DLC: ", DLC)
        DLC = [k for k, v in DLC_code.items() if v == DLC][0]
        print("DLC2: ", DLC)
    data = []  # CAN id, frame, not use

    # get can id
    can_id_1 = (canid >> (3 * 8)) & 0x00ff
    can_id_2 = (canid >> (2 * 8)) & 0x00ff
    can_id_3 = (canid >> (1 * 8)) & 0x00ff
    can_id_4 = (canid) & 0x00ff

    # get CAN frame infomation
    can_frame = 0
    can_frame += 0b00000000  # FD set
    can_frame += 0b00000000  # bitrate swtich
    can_frame += 0b00000000  # can extend
    can_frame += DLC  # DLC

    data.append(can_id_1)
    data.append(can_id_2)
    data.append(can_id_3)
    data.append(can_id_4)
    data.append(can_frame)
    data.append(NOT_USE)
    for value in payload:
        data.append(value)

    packet = []

    packet.append(START_WORD_1)
    packet.append(START_WORD_2)
    packet.append(sender)
    packet.append(seq_count)
    packet.append(cmd_code_1st)
    packet.append(cmd_code_tx_write)

    # data size
    packet.append(len(data) >> 8 & 0xff)
    packet.append(len(data) & 0xff)  # append length
    packet.extend(data)

    encode_packet = bytearray(packet)
    send_res = UDP_SOCKET.sendto(encode_packet, (BENCH_IP, BENCH_PORT))

    print('[{}]'.format(', '.join(hex(x) for x in packet)))


'''
    Bench tool control
'''

global TCP_SOCKET


def TCP_INIT():
    global TCP_SOCKET
    try:
        TCP_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        TCP_SOCKET.connect((BENCH_TOOL_IP, BENCH_TOOL_PORT))
        print_console("TCP Socket created.")
    except:
        print_console("[{}] Please Bench Tool Check".format(currentframe().f_code.co_name))
        CCIC_DEFINITION_LIBRARY.TCP_FLAG = False
        return False

    CCIC_DEFINITION_LIBRARY.TCP_FLAG = True
    return TCP_SOCKET


def TCP_DEINIT():
    global TCP_SOCKET

    TCP_SOCKET.close()
    print_console("TCP Socket closed.")


def EnumWindowsHandler(hwnd, extra):
    wintext = win32gui.GetWindowText(hwnd)

    if wintext.find("VS_BASE") == 0:
        CCIC_DEFINITION_LIBRARY.BENCH_TOOL_CAPTION = wintext
        CCIC_DEFINITION_LIBRARY.BENCH_TOOL_HANDLE = hwnd


def winfun(hwnd, lparam):
    s = win32gui.GetWindowText(hwnd)
    if len(s) > 3:
        if s == "Connect SIM" or s == "CONNECT SIM":
            CCIC_DEFINITION_LIBRARY.BENCH_TOOL_CONNECT_SIM_BUTTON_HANDLE = hwnd
        elif s == "DISCONNECT":
            CCIC_DEFINITION_LIBRARY.BENCH_TOOL_DISCONNECT_SIM_BUTTON_HANDLE = hwnd


def TOOL_CONNECT_SIM():
    win32gui.EnumWindows(EnumWindowsHandler, None)
    win32gui.EnumChildWindows(CCIC_DEFINITION_LIBRARY.BENCH_TOOL_HANDLE, winfun, None)

    SendMessage(CCIC_DEFINITION_LIBRARY.BENCH_TOOL_CONNECT_SIM_BUTTON_HANDLE, BM_CLICK, 0, 0)
    return True


def TOOL_DISCONNECT_SIM():
    win32gui.EnumWindows(EnumWindowsHandler, None)
    win32gui.EnumChildWindows(CCIC_DEFINITION_LIBRARY.BENCH_TOOL_HANDLE, winfun, None)

    # DISCONNECT SIM BUTTON Handle : 1180718
    SendMessage(1180718, BM_CLICK, 0, 0)
    return True


def TOOL_ETHCC_CONNECT_DISCONNECT():
    win32gui.EnumWindows(EnumWindowsHandler, None)
    win32gui.EnumChildWindows(CCIC_DEFINITION_LIBRARY.BENCH_TOOL_HANDLE, winfun, None)

    # EthCC CONNECT/DISCONNECT BUTTON Handle : 1314996
    SendMessage(1314996, BM_CLICK, 0, 0)
    return True


def TOOL_SIGNAL_ALL_STOP():
    global TCP_SOCKET
    # 0x66AA 0x10 0~255 0x2006 0x0000
    signal_stop_packet = bytearray([0x66, 0xAA, 0x10, 0x00, 0x20, 0x06, 0x00, 0x00])
    send_hex_array = str.join("", (" 0x%02X" % i for i in signal_stop_packet))

    try:
        TCP_SOCKET.sendall(signal_stop_packet)
        print_console("[{}] Send TCP Packet -{}".format(currentframe().f_code.co_name, send_hex_array))
        TCP_SOCKET.settimeout(1)
        recvData = TCP_SOCKET.recv(100)
        TCP_SOCKET.settimeout(None)

        # recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(ord(c)) for c in recvData]))  # Python2.7
        recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(c) for c in recvData]))  # Python3.x
        print_console("[{}] Recv TCP Packet -{}".format(currentframe().f_code.co_name, recv_hex_array))
        return int(recv_hex_array[-2:], 16)
    except:
        return False

    # insert report
    xml_report.REPORT.set_action(currentframe().f_code.co_name)
    xml_report.REPORT.set_result(xml_report.REPORT.STR_PASS)
    xml_report.REPORT.param = ["", "", ""]
    xml_report.REPORT.insert_ITEM()
    xml_report.REPORT.save_xml()
    return True


def TOOL_ICON_CHECK(btnID):
    global TCP_SOCKET

    btnID1 = btnID >> 8 & 0XFF
    btnID2 = btnID & 0XFF
    readPacket = bytearray([0x66, 0xAA, 0x10, 0x01, 0x10, 0x01, 0x00, 0x02, btnID1, btnID2])
    send_hex_array = str.join("", (" 0x%02X" % i for i in readPacket))

    try:
        TCP_SOCKET.sendall(readPacket)
        print_console("[{}] Send TCP Packet -{}".format(currentframe().f_code.co_name, send_hex_array))
        TCP_SOCKET.settimeout(1)
        recvData = TCP_SOCKET.recv(100)
        TCP_SOCKET.settimeout(None)

        # recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(ord(c)) for c in recvData]))  # Python2.7
        recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(c) for c in recvData]))  # Python3.x
        print_console("[{}] Recv TCP Packet -{}".format(currentframe().f_code.co_name, recv_hex_array))
        return int(recv_hex_array[-2:], 16)
    except:
        print_console("Re-execute Bench tool")
        # subprocess.Popen("C:\Users\SWTA\Desktop\VS_BASE_20200508_1\ETHCC\VS_BASE_20200508_1.exe")
        subprocess.Popen("D:\\VS\\VS_BASE_20210825_1\\HKMC_6.5th_ATS\\VS_BASE_2IN1-20210813_1.exe")
        time.sleep(5)
        win32gui.EnumWindows(EnumWindowsHandler, None)
        time.sleep(2)
        TOOL_CONNECT_SIM()


def TOOL_ICON_CONTROL(btnID, value):
    global TCP_SOCKET

    btnID1 = btnID >> 8 & 0XFF
    btnID2 = btnID & 0XFF

    writePacket = bytearray([0x66, 0xAA, 0x10, 0x03, 0x10, 0x02, 0x00, 0x03, btnID1, btnID2, value])
    send_hex_array = str.join("", (" 0x%02X" % i for i in writePacket))
    while (1):
        try:
            TCP_SOCKET.sendall(writePacket)
            print_console("[{}] Send TCP Packet -{}".format(currentframe().f_code.co_name, send_hex_array))
            TCP_SOCKET.settimeout(1)
            recvData = TCP_SOCKET.recv(100)
            TCP_SOCKET.settimeout(None)
            break
        except Exception as e:
            print(e)
            TCP_DEINIT()
            TCP_INIT()

    # recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(ord(c)) for c in recvData]))  # Python2.7
    recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(c) for c in recvData]))  # Python3.x
    print_console("[{}] Recv TCP Packet -{}".format(currentframe().f_code.co_name, recv_hex_array))

    if int(recv_hex_array[-4:], 16) == 0xFF:
        print_console("[{}] Please execute the control UI".format(currentframe().f_code.co_name))
    else:
        if TOOL_ICON_CHECK(btnID) == value:
            print_console("[{}] SUCCESS".format(currentframe().f_code.co_name))
            return True
        else:
            print_console("[{}] FAILED".format(currentframe().f_code.co_name))
            return False

    # insert report
    xml_report.REPORT.set_action(currentframe().f_code.co_name)
    xml_report.REPORT.set_result(xml_report.REPORT.STR_PASS)
    xml_report.REPORT.param = [str(btnID), str(value), ""]
    xml_report.REPORT.insert_ITEM()
    xml_report.REPORT.save_xml()
    return True


def TOOL_SEQ_MODE_SET(on_off=ON):
    global TCP_SOCKET

    if on_off == 1:
        STAT = "ON"
    else:
        STAT = "OFF"

    writePacket = bytearray([0x66, 0xAA, 0x10, 0x00, 0x10, 0x04, 0x00, 0x01, on_off])

    send_hex_array = str.join("", (" 0x%02X" % i for i in writePacket))
    TCP_SOCKET.sendall(writePacket)
    print_console("[{}] Send TCP Packet -{}".format(currentframe().f_code.co_name, send_hex_array))
    TCP_SOCKET.settimeout(1)
    recvData = TCP_SOCKET.recv(100)
    TCP_SOCKET.settimeout(None)

    # recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(ord(c)) for c in recvData]))  # Python2.7
    recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(c) for c in recvData]))  # Python3.x
    print_console("[{}] Recv TCP Packet -{}".format(currentframe().f_code.co_name, recv_hex_array))

    if int(recv_hex_array[-4:], 16) == 0xFF:
        print_console("[{}] Please execute the control UI".format(currentframe().f_code.co_name))
    elif int(recv_hex_array[-4:], 16) == 0x01:
        print_console("[{}] SEQUENTIAL MODE {} SUCCESS".format(currentframe().f_code.co_name, STAT))
        return True
    elif int(recv_hex_array[-4:], 16) == 0x00:
        print_console("[{}] SEQUENTIAL MODE {} FAILED".format(currentframe().f_code.co_name, STAT))
        return False


def TOOL_SEQ_MODE_STATUS_GET():
    global TCP_SOCKET
    writePacket = bytearray([0x66, 0xAA, 0x10, 0x00, 0x10, 0x03, 0x00, 0x00])

    send_hex_array = str.join("", (" 0x%02X" % i for i in writePacket))
    TCP_SOCKET.sendall(writePacket)
    print_console("[{}] Send TCP Packet -{}".format(currentframe().f_code.co_name, send_hex_array))
    TCP_SOCKET.settimeout(1)
    recvData = TCP_SOCKET.recv(100)
    TCP_SOCKET.settimeout(None)

    # recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(ord(c)) for c in recvData]))  # Python2.7
    recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(c) for c in recvData]))  # Python3.x
    print_console("[{}] Recv TCP Packet -{}".format(currentframe().f_code.co_name, recv_hex_array))

    if int(recv_hex_array[-9:-5], 16) == 0x01:
        seq_stat = "ON"
    elif int(recv_hex_array[-9:-5], 16) == 0x00:
        seq_stat = "OFF"

    if int(recv_hex_array[-4:], 16) == 0x01:
        run_stat = "RUN"
    elif int(recv_hex_array[-4:], 16) == 0x00:
        run_stat = "STOP"
    elif int(recv_hex_array[-4:], 16) == 0x02:
        run_stat = "PAUSE"
    elif int(recv_hex_array[-4:], 16) == 0x03:
        run_stat = "RESUME"

    print_console(
        "[{}] SEQUENTIAL MODE : {} / PLAY STATUS : {}".format(currentframe().f_code.co_name, seq_stat, run_stat))

    return True


def TOOL_SEQ_MODE_CONTROL(status_val=0):
    # Stop - 0, Run - 1, Pause - 2, Resume - 3
    global TCP_SOCKET

    if status_val == 0:
        STAT = "STOP"
    elif status_val == 1:
        STAT = "RUN"
    elif status_val == 2:
        STAT = "PAUSE"
    elif status_val == 3:
        STAT = "RESUME"

    writePacket = bytearray([0x66, 0xAA, 0x10, 0x00, 0x10, 0x05, 0x00, 0x01, status_val])

    send_hex_array = str.join("", (" 0x%02X" % i for i in writePacket))
    TCP_SOCKET.sendall(writePacket)
    print_console("[{}] Send TCP Packet -{}".format(currentframe().f_code.co_name, send_hex_array))
    TCP_SOCKET.settimeout(1)
    recvData = TCP_SOCKET.recv(100)
    TCP_SOCKET.settimeout(None)

    # recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(ord(c)) for c in recvData]))  # Python2.7
    recv_hex_array = str.join("", (" 0x%02X" % i for i in [int(c) for c in recvData]))  # Python3.x
    print_console("[{}] Recv TCP Packet -{}".format(currentframe().f_code.co_name, recv_hex_array))

    if int(recv_hex_array[-4:], 16) == 0xFF:
        print_console("[{}] Please execute the control UI".format(currentframe().f_code.co_name))
    elif int(recv_hex_array[-4:], 16) == 0x01:
        print_console("[{}] SEQUENTIAL MODE {} SUCCESS".format(currentframe().f_code.co_name, STAT))
        return True
    elif int(recv_hex_array[-4:], 16) == 0x00:
        print_console("[{}] SEQUENTIAL MODE {} FAILED".format(currentframe().f_code.co_name, STAT))
        return False


'''
    Bench Power Control
'''
"""250807
우현벤치 사용 중인 프로젝트는 아래 내용 확인하시고 적용 부탁드립니다.
한 번 적용하면 윈도우 포맷 전까지는 영구적으로 유지된다고 하니 참고해주세요.

0. Test PC에서 우현 벤치 네트워크 이름 확인
1. cmd 창 열기
2. route print 입력
3. 인터페이스 목록에서 0의 우현벤치 네트워크 이름 확인 후 번호 기억하기
4. 아래 명령어에 번호 입력 후 "확인!" 문구 뜨면 성공 
   route -p add 192.168.1.101 mask 255.255.255.255 192.168.1.1 if [번호]
"""


def WOOHYUN_IGN2(on_off):
    data = []
    STAT = ""
    #Cmd Code
    data.append(0x24)
    if on_off == "Read":
        data.append(0x38)
        # data size: 0
        STAT = "Read"
    else:
        data.append(0x28)
        data.append(on_off) #data size: 1
        if on_off == 1:
            STAT = "ON"
        else:
            STAT = "OFF"

    VALUE_NAME = inspect.getframeinfo(inspect.getouterframes(inspect.currentframe())[1][0]).code_context[0].strip()[(
                                                                                                                        inspect.getframeinfo(
                                                                                                                            inspect.getouterframes(
                                                                                                                                inspect.currentframe())[
                                                                                                                                1][
                                                                                                                                0]).code_context[
                                                                                                                            0].strip()).find(
        '(') + 1:-1]

    if ")" in VALUE_NAME:  # 'if' exception
        VALUE_NAME = VALUE_NAME.replace(')', '')

    # insert report
    xml_report.REPORT.set_action(currentframe().f_code.co_name)
    xml_report.REPORT.set_result(xml_report.REPORT.STR_PASS)
    xml_report.REPORT.param = [STAT, "", ""]
    xml_report.REPORT.insert_ITEM()
    xml_report.REPORT.save_xml()

    # UDP_SEND(data)
    # print("IGN2 success")

    res = UDP_SEND(data)

    if res:
        print_console("[{}] {}({})".format(currentframe().f_code.co_name, VALUE_NAME, STAT))
        if on_off == "Read":
            return res[-1]
        elif on_off == WOOHYUN_IGN2("Read"):
            return True
        else:
            return WOOHYUN_IGN2(on_off)
    else:
        print_console("[{}] FAIL".format(currentframe().f_code.co_name))
        return WOOHYUN_IGN2(on_off)


def WOOHYUN_IGN1(on_off):
    data = []
    STAT = ""
    #Cmd Code
    data.append(0x24)
    if on_off == "Read":
        data.append(0x32)
        # data size: 0
        STAT = "Read"
    else:
        data.append(0x22)
        data.append(on_off) #data size: 1
        if on_off == 1:
            STAT = "ON"
        else:
            STAT = "OFF"

    VALUE_NAME = inspect.getframeinfo(inspect.getouterframes(inspect.currentframe())[1][0]).code_context[0].strip()[(
                                                                                                                        inspect.getframeinfo(
                                                                                                                            inspect.getouterframes(
                                                                                                                                inspect.currentframe())[
                                                                                                                                1][
                                                                                                                                0]).code_context[
                                                                                                                            0].strip()).find(
        '(') + 1:-1]

    if ")" in VALUE_NAME:  # 'if' exception
        VALUE_NAME = VALUE_NAME.replace(')', '')

    # insert report
    xml_report.REPORT.set_action(currentframe().f_code.co_name)
    xml_report.REPORT.set_result(xml_report.REPORT.STR_PASS)
    xml_report.REPORT.param = [STAT, "", ""]
    xml_report.REPORT.insert_ITEM()
    xml_report.REPORT.save_xml()

    res = UDP_SEND(data)

    if res:
        print_console("[{}] {}({})".format(currentframe().f_code.co_name, VALUE_NAME, STAT))
        if on_off == "Read":
            return res[-1]
        elif on_off == WOOHYUN_IGN1("Read"):
            return True
        else:
            return WOOHYUN_IGN1(on_off)
    else:
        print_console("[{}] FAIL".format(currentframe().f_code.co_name))
        return WOOHYUN_IGN1(on_off)


def WOOHYUN_ACC(on_off):
    data = []
    STAT = ""
    #Cmd Code
    data.append(0x24)
    if on_off == "Read":
        data.append(0x31)
        # data size: 0
        STAT = "Read"
    else:
        data.append(0x21)
        data.append(on_off) #data size: 1
        if on_off == 1:
            STAT = "ON"
        else:
            STAT = "OFF"

    VALUE_NAME = inspect.getframeinfo(inspect.getouterframes(inspect.currentframe())[1][0]).code_context[0].strip()[(
                                                                                                                        inspect.getframeinfo(
                                                                                                                            inspect.getouterframes(
                                                                                                                                inspect.currentframe())[
                                                                                                                                1][
                                                                                                                                0]).code_context[
                                                                                                                            0].strip()).find(
        '(') + 1:-1]

    if ")" in VALUE_NAME:  # 'if' exception
        VALUE_NAME = VALUE_NAME.replace(')', '')

    # insert report
    xml_report.REPORT.set_action(currentframe().f_code.co_name)
    xml_report.REPORT.set_result(xml_report.REPORT.STR_PASS)
    xml_report.REPORT.param = [STAT, "", ""]
    xml_report.REPORT.insert_ITEM()
    xml_report.REPORT.save_xml()

    res = UDP_SEND(data)

    if res:
        print_console("[{}] {}({})".format(currentframe().f_code.co_name, VALUE_NAME, STAT))
        if on_off == "Read":
            return res[-1]
        elif on_off == WOOHYUN_ACC("Read"):
            return True
        else:
            return WOOHYUN_ACC(on_off)
    else:
        print_console("[{}] FAIL".format(currentframe().f_code.co_name))
        return WOOHYUN_ACC(on_off)


def WOOHYUN_BATTERY(on_off):
    data = []
    STAT = ""
    #Cmd Code
    data.append(0x24)
    if on_off == "Read":
        data.append(0x33)
        # data size: 0
        STAT = "Read"
    else:
        data.append(0x23)
        data.append(on_off) #data size: 1
        if on_off == 1:
            STAT = "ON"
        else:
            STAT = "OFF"

    VALUE_NAME = inspect.getframeinfo(inspect.getouterframes(inspect.currentframe())[1][0]).code_context[0].strip()[(
                                                                                                                        inspect.getframeinfo(
                                                                                                                            inspect.getouterframes(
                                                                                                                                inspect.currentframe())[
                                                                                                                                1][
                                                                                                                                0]).code_context[
                                                                                                                            0].strip()).find(
        '(') + 1:-1]

    if ")" in VALUE_NAME:  # 'if' exception
        VALUE_NAME = VALUE_NAME.replace(')', '')

    # insert report
    if xml_report.REPORT != None:
        xml_report.REPORT.set_action(currentframe().f_code.co_name)
        xml_report.REPORT.set_result(xml_report.REPORT.STR_PASS)
        xml_report.REPORT.param = [STAT, "", ""]
        xml_report.REPORT.insert_ITEM()
        xml_report.REPORT.save_xml()

    res = UDP_SEND(data)

    if res:
        print_console("[{}] {}({})".format(currentframe().f_code.co_name, VALUE_NAME, STAT))
        if on_off == "Read":
            return res[-1]
        elif on_off == WOOHYUN_BATTERY("Read"):
            return True
        else:
            return WOOHYUN_BATTERY(on_off)
    else:
        print_console("[{}] FAIL".format(currentframe().f_code.co_name))
        return WOOHYUN_BATTERY(on_off)


def BATTERY_CHECK():
    data = []
    data.append(0x20)
    data.append(0x02)

    return float(UDP_SEND(data)[-1]) / 10


def AMPERE_CHECK():
    # BENCH TOOL - MAIN CONTROL - VBAT IC : ON STATUS
    data = []
    data.append(0x20)
    data.append(0x03)
    recv = UDP_SEND(data)

    recv_last = recv[-1] << 8
    recv_last2 = recv[-2]
    swap_data = float(recv_last | recv_last2)

    if CCIC_DEFINITION_LIBRARY.STRESS_FLAG:
        # ELASPED = STR_ELPASED(CCIC_DEFINITION_LIBRARY.TEST_START_TIME)
        # CUR_DATE = STR_CURRENT_DATE()
        pass  # WRITE_REPORT_DATA(CCIC_DEFINITION_LIBRARY.TEST_CYCLE, "AMPERE_CHECK", str(float(swap_data / 1000)), "-", "-", "-", ELASPED, CUR_DATE)
        # WRITE_REPORT_DATA() -> CCIC_EXCEL_REPORT_LIBRARY.py

    return float(swap_data / 1000)

    # jw


def AMPERE_0_CHECK():
    check_current_result = False
    for _ in range(8):
        ampere_res = AMPERE_CHECK()
        if ampere_res < 0.6:
            check_current_result = True
            break

    if check_current_result:
        print_console("[{}] Ampere check success! ({})".format(currentframe().f_code.co_name, ampere_res))
        xml_report.REPORT.set_action(currentframe().f_code.co_name)
        xml_report.REPORT.set_result(xml_report.REPORT.STR_PASS)
        xml_report.REPORT.param = [str(ampere_res), "", ""]
        xml_report.REPORT.insert_ITEM()
        xml_report.REPORT.save_xml()
    else:
        print_console("[{}] Ampere check failed! ({})".format(currentframe().f_code.co_name, ampere_res))
        xml_report.REPORT.set_action(currentframe().f_code.co_name)
        xml_report.REPORT.set_result(xml_report.REPORT.STR_FAIL)
        xml_report.REPORT.param = [str(ampere_res), "", ""]
        xml_report.REPORT.insert_ITEM()
        xml_report.REPORT.save_xml()
    return check_current_result


def BATTERY_SET(voltage=14.4):
    # Main Power (5.3~16V Cover)
    # if voltage < 5.3 or voltage > 16:
    #    print_console("[{}] {}V is bench over spec".format(currentframe().f_code.co_name,voltage))
    #    return False
    # else:
    data = []
    data.append(0x20)
    data.append(0x01)
    data.append(int(voltage * 10))

    return UDP_SEND(data)


def DRIVER_DOOR(open_close):
    status = ""
    VALUE_NAME = inspect.getframeinfo(inspect.getouterframes(inspect.currentframe())[1][0]).code_context[0].strip()[(
                                                                                                                        inspect.getframeinfo(
                                                                                                                            inspect.getouterframes(
                                                                                                                                inspect.currentframe())[
                                                                                                                                1][
                                                                                                                                0]).code_context[
                                                                                                                            0].strip()).find(
        '(') + 1:-1]

    if ")" in VALUE_NAME:  # 'if' exception
        VALUE_NAME = VALUE_NAME.replace(')', '')

    '''
    if CCIC_DEFINITION_LIBRARY.TCP_FLAG:
        if not TOOL_ICON_CONTROL(11004, open_close):
            print_console("[{}] {} FAIL".format(currentframe().f_code.co_name,VALUE_NAME))
            return False
        else:
            print_console("[{}] {} SUCCESS".format(currentframe().f_code.co_name,VALUE_NAME))
            return True
    else:
        print_console("[{}] Please execute the bench tool panel".format(currentframe().f_code.co_name,VALUE_NAME))
        return False
    '''
    if open_close:
        payload = [0, 0, 0, 1, 0, 0, 0, 0]  # open
    else:
        payload = [0, 0, 0, 0, 0, 0, 0, 0]  # close

    for i in range(5):
        UDP_CANFD_SEND(0x411, payload)
        time.sleep(0.2)  # ICU_02_200ms

    if open_close:
        status = "OPEN"
    else:
        status = "CLOSE"

    # insert report
    xml_report.REPORT.set_action(currentframe().f_code.co_name)
    xml_report.REPORT.set_result(xml_report.REPORT.STR_PASS)
    xml_report.REPORT.param = [status, "", ""]
    xml_report.REPORT.insert_ITEM()
    xml_report.REPORT.save_xml()


def CLU_CARD_OK():
    press = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x0E]  # OK press
    release = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x00]  # OK release

    UDP_CANFD_SEND(0x448, press)
    time.sleep(0.2)  # SWRC_01_200ms
    UDP_CANFD_SEND(0x448, release)


def CLU_CARD_SWIPE():
    press = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x04]  # Card swipe
    release = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x00]  # swipe release

    # Card Press
    CLU_CARD_OK()
    DELAY(2)

    # Card Swipe
    UDP_CANFD_SEND(0x448, press)
    time.sleep(0.2)  # SWRC_01_200ms
    UDP_CANFD_SEND(0x448, release)


'''UDP_CANFD_SEND 테스트중...'''
def send_signal(id:int, DLC:int, start_bit:int, value:int):
    '''
    start_bit = 33 # 5 1
    bit_length = 10 #
    value = 511 # 01 1111 1111
    0000 0000 7 ~ 0     1
    0000 0000 15 ~ 8    2
    0000 0000 23 ~ 16   3
    0000 0000 31 ~ 24   4
    1111 1110 39 ~ 32   5
    0000 0011 47 ~ 40   6
    0000 0000 55 ~ 48   7
    0000 0000 63 ~ 56   8
    '''
    data = [0]*DLC
    byte_idx = start_bit // 8 # 33 // 8 = 4
    bit_idx = start_bit % 8   # 33 % 8  = 1
    i = 0
    bit_length = 0
    while(True):
        if(value << bit_idx) == (2 ** i):
            bit_length = i
            break
        elif(value << bit_idx) < (2 ** i):
            bit_length = i-1
            break
        i += 1
    range_ = 0  # ?
    data[byte_idx] = (value << bit_idx) & 0xFF
    for i in range(range_):
        if((8 * i) - bit_idx) > 0:
            data[byte_idx + i] = (value >> (8*i)-bit_idx) & 0xFF

    print('data list',data)
    UDP_CANFD_SEND(id, data)


def SVM_ViewSta_New():
    '''
    0000 0000 7 ~ 0     1
    0000 0000 15 ~ 8    2
    0000 0000 23 ~ 16   3
    0000 0000 31 ~ 24   4
    0000 0000 39 ~ 32   5
    0000 0000 47 ~ 40   6
    0011 0110 55 ~ 48   7
    0000 0000 63 ~ 56   8
    '''
    # Start Bit = 48
    # Bit Size = 8
    # SVM_Rear_View = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x36, 0x00]  # RS4 SVM Rear View
    # SVM_Off = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00]  # RS4 SVM Off
    SVM_Rear_View = [0, 0, 0, 0, 0, 0, 54, 0]  # RS4 SVM Rear View
    SVM_Off = [0, 0, 0, 0, 0, 0, 1, 0]  # RS4 SVM Off
    # Start Bit = 32
    # Bit Size = 4
    gear_R = [0x00, 0x00, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00]  # RS4 SVM Rear View
    gear_P = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]  # RS4 SVM Off
    # 0x04760030, SVM_ViewSta_New On: 0x24, Off: 0x01
    # 0x00400020, TCU_GearSlctDis R: 7, P: 0
    # '''
    UDP_CANFD_SEND(0x476, SVM_Rear_View)
    time.sleep(10)
    UDP_CANFD_SEND(0x476, SVM_Off)
    '''
    UDP_CANFD_SEND(0x040, gear_R)
    time.sleep(10)
    UDP_CANFD_SEND(0x040, gear_P)
    #'''


def set_cluster_initial():
    # ccIC
    # """
    ccIC_cluster_initial = [
        ['BCM_12_200ms', '', '0x442', '0', '8', '00 00 00 00 00 00 00 00', '1', '200', '1'],
        # Request_Door_Telltale_250424 - Window Closed signal
        ['ICU_08_200ms', '', '0x418', '0', '8', '0 0 0 0 10 0 0 0', '1', '200', '1'],
        ['ECS_01_100ms', '', '0x220', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'],
        ['AHLS_01_300ms', '', '0x471', '0', '8', '0 0 0 0 0 0 0 0', '1', '300', '1'],
        ['PSB_01_200ms', '', '0x3ab', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['ADAS_CMD_40_50ms', '', '0x1e5', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '50', '1'],
        ['MFSW_01_200ms', '', '0x3c1', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['MFSW_02_200ms', '', '0x3c2', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['ICU_05_200ms', '', '0x414', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['CMS_RH_02_200ms', '', '0x2a8', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['CMS_LH_02_200ms', '', '0x2de', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['LSD_01_20ms', '', '0x1d0', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'],
        ['ROA_01_1000ms', '', '0x3c0', '0', '8', '0 0 0 0 0 0 0 0', '1', '1000', '1'],
        ['ICU_04_200ms', '', '0x413', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],  # Turn Signal off
        # ['DATC_01_20ms', '', '0x145', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'],  # temp -40
        ['DATC_01_20ms', '', '0x145', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 59 0 0 0 0 0 0 0 0 0 0',
         '1',
         '20', '1'],  # temp 4.5?
        ['WHL_01_10ms', '', '0xa0', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'],
        ['ECALL_CLU_PE_01', '', '0x3bf', '0', '8', '0 0 0 0 0 0 0 0', '1', '1000', '1'],  # ECALL(SOS], off
        ['VCU_01_10ms', '', '0x35', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'],
        # Gear P
        ['VCU_03_100ms', '', '0x2e0', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'],
        ['VCU_01_10ms', '', '0x35', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '10',
         '1'],  # Gear P
        ['VCU_03_100ms', '', '0x2e0', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '100', '1'],
        ['BCM_07_200ms', '', '0x41a', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],  # High beam off
        ['ADAS_CMD_10_20ms', '', '0x160', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'],
        ['SMK_04_200ms', '', '0x425', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['ADAS_CMD_30_10ms', '', '0x12a', '0', '16', '0 0 0 18 80 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'],  # LKA
        ['ADAS_CMD_31_50ms', '', '0x1e0', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '50', '1'],
        ['BCM_03_200ms', '', '0x3e1', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['TPMS_01_200ms', '', '0x3a0', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '200', '1'],  # TPMS
        ['VCU_05_100ms', '', '0x2b5', '0', '32', '0 0 0 0 0 0 0 40 64 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0',
         '1',
         '100', '1'],
        ['ABS_ESC_01_10ms', '', '0x6f', '0', '8', '0 0 0 0 0 0 0 0', '1', '10', '1'],
        ['FR_CMR_01_10ms', '', '0x11a', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'],
        ['MDPS_01_10ms', '', '0xea', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'],
        ['ESC_01_10ms', '', '0x60', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '10',
         '1'],
        ['IEB_01_10ms', '', '0x65', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '10',
         '1'],
        ['ADAS_PRK_10_20ms', '', '0x170', '0', '16', '0 0 0 4 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'],
        ['ADAS_CMD_20_20ms', '', '0x1a0', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0',
         '1',
         '20', '1'],
        ['EPB_01_50ms', '', '0x1f0', '0', '16', '0 0 0 30 0 0 0 0 0 0 0 0 0 0 0 0', '1', '50', '1'],
        ['RR_C_RDR_02_50ms', '', '0x1ba', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '50', '1'],
        # ['ADAS_CMD_32_50ms', '', '0x1ea', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 46 0 0 0 0 0 0 0 0 8 7 0 1 0 0 0 0 0 15 15 0', '1', '50', '1'],
        ['ADAS_CMD_32_50ms', '', '0x1ea', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 f f 0',
         '1',
         '50', '1'],
        ['ACU_01_100ms', '', '0x20a', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'],
        ['LDC_01_100ms', '', '0x255', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '100', '1'],
        ['BMS_04_100ms', '', '0x25a', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '100', '1'],
        ['BMS_02_100ms', '', '0x2fa', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 64 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0',
         '1',
         '100', '1'],
        ['CDM_01_200ms', '', '0x3aa', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['ICU_02_200ms', '', '0x411', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['ICU_06_200ms', '', '0x416', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['ILCU_LH_01_200ms', '', '0x421', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['ILCU_RH_01_200ms', '', '0x426', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'],
        ['CLU_02_100ms', '', '0x225', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'],  # DTE ---km 관련
        ['TCU_01_10ms', '', '0x40', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '10',
         '1'],  # RS4 Gear
        ['EMS_05_100ms', '', '0x260', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '100', '1'],  # RS4 Check Engine
        ['AWD_01_20ms', '', '0x1a5', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '20', '1'],  # RS4 4WD
        ['EMS_02_10ms', '', '0x100', '0', '32', '0 0 0 0 0 80 0 60 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0',
         '1',
         '10', '1'],  # RS4 Battery Charge
        # ['EMS_01_10ms', '', '0xb5', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 4e 0 0 0 0 0 0', '1', '10', '1'],  # RS4 ISG
        ['EMS_01_10ms', '', '0xb5', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '10',
         '1'],  # RS4 ISG, remove consume fuel
        ['VCMS_02_100ms', '', '0x30a', '0', '32', '0 0 0 0 44 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0',
         '1',
         '100', '1'],  # JW External Charge Cable
        ['AMP_HU_PE_01', '', '0x60c', '0', '8', '0 0 0 0 0 0 0 40', '1', '1000', '1'],
        ['AMP_HU_P_01', '', '0x60a', '0', '8', '0 0 0 0 0 0 0 80', '1', '200', '1'],
        ['MCU_03_100ms', '', '0x250', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'],
        ['FR_CMR_02_100ms', '', '0x1fa', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0',
         '1',
         '100', '1'],  # remove ISLW popup
        ['VCU_06_200ms', '', '0x2c0', '0', '32', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
         '200', '1'],  # Boost mode off
        ['SBW_01_10ms', '', '0x130', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'], ]
    # """
    # ccIC2
    """
    ccIC2_cluster_initial = [('EMS_02_10ms', '', '0x100', '0', '32',
                        '00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00',
                        '1', '10', '1'),
                       ('LDC48V_01_100ms', '', '0x2F5', '0', '32',
                        '00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00',
                        '1', '100', '1'),
                       ('LDC_01_100ms', '', '0x255', '0', '32',
                        '00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00',
                        '1', '100', '1'),
                       ('BCM_12_200ms', ' ', '0x442', '0', '8', '00 00 00 00 00 00 00 00', '1', '200', '1'),
                       # Request_Door_Telltale_250424 - Window Closed signal
                       ('ICU_08_200ms', '', '0x418', '0', '8', '0 0 0 0 10 0 0 0', '1', '200', '1'),
                       ('ECS_01_100ms', '', '0x220', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       ('AHLS_01_300ms', '', '0x471', '0', '8', '0 0 0 0 0 0 0 0', '1', '300', '1'),
                       ('PSB_01_200ms', '', '0x3ab', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('ADAS_CMD_40_50ms', '', '0x1e5', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '50', '1'),
                       ('MFSW_01_200ms', '', '0x3c1', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('MFSW_02_200ms', '', '0x3c2', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('ICU_05_200ms', '', '0x414', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('CMS_RH_02_200ms', '', '0x2a8', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('CMS_LH_02_200ms', '', '0x2de', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('LSD_01_20ms', '', '0x1d0', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'),
                       ('ROA_01_1000ms', '', '0x3c0', '0', '8', '0 0 0 0 0 0 0 0', '1', '1000', '1'),
                       ('ICU_04_200ms', '', '0x413', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),  # Turn Signal off
                       ('DATC_01_20ms', '', '0x145', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 59 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'),
                       # temp 4.5?
                       ('WHL_01_10ms', '', '0xa0', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
                        '10', '1'),
                       ('ECALL_CLU_PE_01', '', '0x3bf', '0', '8', '0 0 0 0 0 0 0 0', '1', '1000', '1'),
                       # ECALL(SOS), off
                       ('VCU_01_10ms', '', '0x35', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
                        '10', '1'),  # Gear P
                       ('VCU_03_100ms', '', '0x2e0', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
                        '100', '1'),
                       ('VCU_01_10ms', '', '0x35', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'),  # Gear P
                       ('VCU_03_100ms', '', '0x2e0', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       ('BCM_07_200ms', '', '0x41a', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),  # High beam off
                       ('ADAS_CMD_10_20ms', '', '0x160', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'),
                       ('SMK_04_200ms', '', '0x425', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       (
                           'ADAS_CMD_30_10ms', '', '0x12a', '0', '16', '0 0 0 18 80 0 0 0 0 0 0 0 0 0 0 0', '1', '10',
                           '1'),
                       # LKA
                       ('ADAS_CMD_31_50ms', '', '0x1e0', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '50', '1'),
                       ('BCM_03_200ms', '', '0x3e1', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('TPMS_01_200ms', '', '0x3a0', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '200', '1'),
                       # TPMS
                       ('VCU_05_100ms', '', '0x2b5', '0', '32',
                        '0 0 0 0 0 0 0 40 64 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       ('ABS_ESC_01_10ms', '', '0x6f', '0', '8', '0 0 0 0 0 0 0 0', '1', '10', '1'),
                       ('FR_CMR_01_10ms', '', '0x11a', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'),
                       ('MDPS_01_10ms', '', '0xea', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
                        '10', '1'),
                       ('ESC_01_10ms', '', '0x60', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'),
                       ('IEB_01_10ms', '', '0x65', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'),
                       ('ADAS_PRK_10_20ms', '', '0x170', '0', '16', '0 0 0 4 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'),
                       ('ADAS_CMD_20_20ms', '', '0x1a0', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'),
                       ('EPB_01_50ms', '', '0x1f0', '0', '16', '0 0 0 30 0 0 0 0 0 0 0 0 0 0 0 0', '1', '50', '1'),
                       ('RR_C_RDR_02_50ms', '', '0x1ba', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0',
                        '1', '50', '1'),
                       ('ADAS_CMD_32_50ms', '', '0x1ea', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 f f 0', '1', '50', '1'),
                       ('ACU_01_100ms', '', '0x20a', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       ('LDC_01_100ms', '', '0x255', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       ('BMS_04_100ms', '', '0x25a', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       ('BMS_02_100ms', '', '0x2fa', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 64 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       ('CDM_01_200ms', '', '0x3aa', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('ICU_02_200ms', '', '0x411', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('ICU_06_200ms', '', '0x416', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('ILCU_LH_01_200ms', '', '0x421', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('ILCU_RH_01_200ms', '', '0x426', '0', '8', '0 0 0 0 0 0 0 0', '1', '200', '1'),
                       ('CLU_02_100ms', '', '0x225', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       # DTE ---km 관련
                       ('TCU_01_10ms', '', '0x40', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'),  # RS4 Gear
                       ('EMS_05_100ms', '', '0x260', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       # RS4 Check Engine
                       ('AWD_01_20ms', '', '0x1a5', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '20', '1'),  # RS4 4WD
                       ('EMS_02_10ms', '', '0x100', '0', '32',
                        '0 0 0 0 0 80 0 60 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'),
                       # RS4 Battery Charge
                       ('EMS_01_10ms', '', '0xb5', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1'),
                       # RS4 ISG, remove consume fuel
                       ('VCMS_02_100ms', '', '0x30a', '0', '32',
                        '0 0 0 0 44 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       # JW External Charge Cable
                       ('AMP_HU_PE_01', '', '0x60c', '0', '8', '0 0 0 0 0 0 0 40', '1', '1000', '1'),
                       ('AMP_HU_P_01', '', '0x60a', '0', '8', '0 0 0 0 0 0 0 80', '1', '200', '1'),
                       ('MCU_03_100ms', '', '0x250', '0', '24', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1',
                        '100', '1'),
                       ('FR_CMR_02_100ms', '', '0x1fa', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '100', '1'),
                       # remove ISLW popup
                       ('VCU_06_200ms', '', '0x2c0', '0', '32',
                        '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '200', '1'),
                       # Boost mode off
                       ('SBW_01_10ms', '', '0x130', '0', '16', '0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0', '1', '10', '1')]
    # """

    for can in ccIC_cluster_initial:
        canid_ = int(can[2], 16)
        nDataList = [int(n, 16) for n in can[5].split()]
        payload_ = nDataList
        print("payload_", payload_)
        UDP_CANFD_SEND(canid=canid_, payload=payload_)
        time.sleep(1)

'''
#-- QEBench Control --#
'''

from IVIQEBenchIOProtocol import *
event = threading.Event()

def Send(listData, nLen):
    listSendBuff = []
    i = 0
    while i < (nLen+ATS_EXCLUDE_BYTE_LENGTH_FIELD+ATS_TAIL_BYTE_LENGTH):
        listSendBuff.append(0)
        i += 1

    nIndex = 0
    # ATS_SYNC 0x61
    listSendBuff[nIndex] = ATS_SYNC
    nIndex += 1
    # ATS_SYNC 0x61
    listSendBuff[nIndex] = ATS_SYNC
    nIndex += 1

    listSendBuff[nIndex] = ((nLen+ATS_TAIL_BYTE_LENGTH)>>24)&0xff
    nIndex += 1
    listSendBuff[nIndex] = ((nLen+ATS_TAIL_BYTE_LENGTH)>>16)&0xff
    nIndex += 1
    listSendBuff[nIndex] = ((nLen+ATS_TAIL_BYTE_LENGTH)>>8)&0xff
    nIndex += 1
    # Len : Command ID + Command Type + Data + Crc + Tail
    listSendBuff[nIndex] = ((nLen+ATS_TAIL_BYTE_LENGTH))&0xff
    nIndex += 1

    for nData in listData:
        # DATA
        listSendBuff[nIndex] = nData
        nIndex += 1

    sCRC16 = CalcCRC16(listData, nLen)
    # CRC
    listSendBuff[nIndex] = (sCRC16>>8)&0xff
    nIndex += 1
    # CRC
    listSendBuff[nIndex] = (sCRC16)&0xff
    nIndex += 1

    # ATS_TAIL 0x6f
    listSendBuff[nIndex] = ATS_TAIL
    nIndex += 1

    WriteRawString(listSendBuff)

    return True
@staticmethod
def CalcCRC16(listData, nLen):
    crc16=0xFFFF
    for nData in listData:
        tmp = ((nData&0xFF) ^ ((crc16) & 0x00FF))
        i = 0
        while i < 8:
            if(tmp & 1):
                tmp = (tmp>>1) ^ ATS_PROTOCOL_CRC_KEY_VALUE
            else:
                tmp >>= 1
            i += 1
        crc16 = ((crc16) >> 8) ^ tmp

    return crc16
def WriteRawString(listSendBuff):
    print(bytes(listSendBuff))
    SERIAL_SEND(bytes(listSendBuff))

def BatteryControl(nStatus, bench_info):
    print("BatteryControl :", nStatus)

    if (nStatus == 1 or nStatus == 0):
        BatteryOnOff(nStatus, bench_info)
        event.clear()
        event.wait(2)
    else:
        BatteryVoltage(nStatus)
        event.clear()
        event.wait(2)
    return True

def BatteryOnOff(nState, bench_info):
    #print ("BatteryOnOff :"), (isOn)

    listData = []
    for i in range(4):
        listData.append(0)

    listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
    listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
    if bench_info == 'OLD_BENCH':
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_B_PLUS
    else:
        listData[ATS_DATA_BYTE_INDEX + 0] = COMMON_SWITCH_1

    if (nState == 1):
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
    else:
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

    Send( listData, len(listData) )
    return True

def BatteryVoltage(nState):
    #print ("BatteryVoltage :"), (nState)

    listData = []
    for i in range(3):
        listData.append(0)

    listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
    listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_VOLTAGE
    listData[ATS_DATA_BYTE_INDEX + 0] = (int((nState*10.0)) & 0xff)

    Send( listData, len(listData) )
    return True

def AccControl(self, nStatus, bench_info):
    print("AccControl :",nStatus)

    if (nStatus == 1 or nStatus == 0):
        self.ACCOnOff(nStatus, bench_info)
    else:
        self.AccVoltage(nStatus)

    return True

def ACCOnOff(nState, bench_info):
    #print ("ACCOnOff :"), (bisOn)

    listData = []
    for i in range(4):
        listData.append(0)

    listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
    listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY
    if bench_info == 'OLD_BENCH':
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_ACC
    else:
        listData[ATS_DATA_BYTE_INDEX + 0]       = COMMON_SWITCH_2

    if (nState == 1):
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
    else:
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

    Send( listData, len(listData) )
    return True

def AccVoltage(nState):
    #print ("AccVoltage :"), (nState)

    listData = []
    for i in range(3):
        listData.append(0)

    listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
    listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_CMD_ACC_VOLTAGE
    listData[ATS_DATA_BYTE_INDEX + 0]   = (int((nState*10.0)) & 0xff)

    Send( listData, len(listData) )
    return True

def IGNControl(nStatus):
    print ("IGNControl :", nStatus)

    listData = []
    for i in range(4):
        listData.append(0)

    listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
    listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
    listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_IGN

    if (nStatus == 1):
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
    else:
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

    Send( listData, len(listData) )
    return True

def IGN3Control(nStatus):
    print ("IGNControl :", nStatus)

    listData = []
    for i in range(4):
        listData.append(0)

    listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
    listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
    listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_IGN3

    if (nStatus == 1):
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
    else:
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

    Send( listData, len(listData) )
    return True

def AmpControl(nStatus):
    print ("AmpControl :", nStatus)

    listData = []
    for i in range(4):
        listData.append(0)

    listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
    listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
    listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_AMP

    if( nStatus == 1 ):
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
    else:
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

    Send( listData, len(listData) )
    return True

def DoorControl(nStatus):
    print ("DoorControl :", nStatus)

    listData = []
    for i in range(4):
        listData.append(0)

    listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
    listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
    listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_DOOR

    if (nStatus == 1):
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
    else:
        listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

    Send( listData, len(listData) )
    return True
