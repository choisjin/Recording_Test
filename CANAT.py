from ctypes import *
import ctypes
from _ctypes import FreeLibrary
import time
import shutil
import os
from robot.api.deco import keyword

class CANAT:

    def __init__(self):
        self.port = ''
        self.bps = 115200
        try:
            self.dll_path = os.path.join(os.path.split(os.path.abspath(__file__))[0], 'CANatTransportProcDll.dll')
        except:
            print('error')
        self.run_dll_path = ''
        self.hdll = None
        self.log_path = ''

    def __del__(self):
        if self.hdll != None:
            bReturn = self.hdll.ExtDisConnect()
            print('ExtDisConnect : ', bReturn, '__del__')
            libHandle = self.hdll._handle
            del self.hdll
            FreeLibrary(libHandle)
            self.hdll = None
            os.remove(self.run_dll_path)
            self.run_dll_path = ''

    @keyword('Init CANat')
    def init(self, comport, log_path, ch1_fd=True, ch2_fd=False):
        self.port = comport
        port_num = int(comport[3:])

        self.log_path = log_path

        self.run_dll_path = os.path.join('.\\', ''.join(['CANatTransportProcDll', '_', comport, '.dll']))
        shutil.copyfile(self.dll_path, self.run_dll_path)
        self.hdll = windll.LoadLibrary(self.run_dll_path)

        self.hdll.ExtGetClassObject()
        self.hdll.ExtSetCANMonitor(False)
        ret = self.hdll.ExtConnect(None, port_num, self.bps)
        print('Connect : ', ret)
        self.hdll.ExtSetCANFDTickCount(True)
        self.hdll.ExtSetCANMonitor(True)
        self.hdll.ExtFlagCANDataList(True)

        cnt = 0
        while True:
            if cnt > 50:
                print('CANat Initial')
                self.hdll.ExtReqInitial()
                break
            time.sleep(0.1)
            bCANDataList = self.hdll.ExtIsCANDataList()
            if bCANDataList == 0:
                cnt += 2
            elif bCANDataList == 1:
                cnt += 1
            else:
                print('CANat Not Initial')
                break

        self.hdll.ExtSetCANMonitor(False)
        self.hdll.ExtFlagCANDataList(False)
        self.hdll.ExtGetBuadrate()
        self.hdll.ExtSetBaudrate(ch1_fd, ch2_fd)
        self.hdll.ExtSetDeviceNumber(1)

    @keyword('Disconnect CANat')
    def disconnect(self):
        if self.hdll != None:
            bReturn = self.hdll.ExtDisConnect()
            print('ExtDisConnect : ', bReturn, '__del__')
            libHandle = self.hdll._handle
            del self.hdll
            FreeLibrary(libHandle)
            self.hdll = None
            os.remove(self.run_dll_path)
            self.run_dll_path = ''

    @keyword('Start Save CAN Message')
    def can_message_save_start(self, save_path=''):
        _save_path = save_path
        if _save_path == '':
            _save_path = self.log_path

        isDir = os.path.isdir(_save_path)
        if isDir == False:
            os.makedirs(_save_path)

        _save_path_byte = bytes(_save_path, 'utf-8')
        self.hdll.ExtSetCANMonitor(True)
        self.hdll.ExtLogSaveStartASC(_save_path_byte)

    @keyword('Stop Save CAN Message')
    def can_message_save_stop(self):
        self.hdll.ExtLogSaveStop()
        self.hdll.ExtSetCANMonitor(False)

    @keyword('Send All CAN Message Stop')
    def send_cam_message_all_stop(self):
        self.hdll.ExtSendCANatTermanate()

    @keyword('Send CAN Message')
    def send_can_message(self, message_id, cycle_time, can_message, bus_channel, message_type='FD'):
        _message_id = int(message_id, 16)
        _bis_ext_id = True
        if _message_id <= 0x7FF:
            _bis_ext_id = False

        _cycle_time = int(cycle_time)

        _periodic = True
        if _cycle_time == 0:
            _periodic = False

        _can_message = [int(n, 16) for n in can_message.split()]
        _can_message_len = len(_can_message)

        _can_message_ctypes = (ctypes.c_char * _can_message_len)(*_can_message)

        _message_type = ' '

        if _can_message_len == 9:
            _can_message_len = 0

        if message_type.lower() == 'cc':
            _message_type = bytes(b'cc')

        # print('send can : ', message_id, '\t:', can_message)
        self.hdll.ExtSendQECanMessage(True, _message_id, _bis_ext_id, ' ', _message_type, _periodic, _cycle_time,
                                       _can_message_len, _can_message_ctypes, int(bus_channel), None)


if __name__ == "__main__":
    canat = CANAT()
    canat.init('COM10', log_path='D:\\GIT_LAB\\rbf_lib\\plugin\\canat\\lib', ch1_fd=True, ch2_fd=True)

    canat.can_message_save_start()

    canat.send_cam_message_all_stop()
    time.sleep(1)

    canat.send_can_message('0x3C0', '100', '00 00 03 00', '1')
    canat.send_can_message('0x585', '1000', '00 00 00 00 00 00 00 00', '1')
    canat.send_can_message('0x5F0', '200', 'FD 00 7E 00 00 7E 00 00', '1')
    canat.send_can_message('0x12DD54D6', '100', '00 10 00 00 00 00 00 00', '1')
    canat.send_can_message('0x1B000010', '200', '00 00 00 00 00 00 00 00', '1', 'CC')

    time.sleep(5)

    canat.can_message_save_stop()

    canat.disconnect()
    time.sleep(1)


