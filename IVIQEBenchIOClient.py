# -*- coding: utf-8 -*-
'''
Created on 2016. 9. 20.
@author: baekho.choi

modified on 2024.01.10
@author : Seungwon.Jung (sw125.jung@lgepartner.com)
'''

from robot.api.deco import keyword
from IVIQEBenchIOReceive import *
import time
import threading
import os
import datetime

from serial import Serial

import queue

IVIQEBENCHIO_PORT = "IVIQEBenchIOPluginInfo"

event = threading.Event()
event_sd = threading.Event()

class IVIQEBenchIOClient:
    '''
    APIs for QE Bench Client
    '''
    def __init__(self, port, bps=115200, msTimeout=1000):
        self._conn = None
        self._port = port
        self._bps = bps
        self._ending = '\r\n'
        self._timeout = msTimeout / 1000.
        self.SetSystemTime()
        self.ReceiveThread = 0
        self.LCDWatchThread = 0
        self.bSoundUpdated = 0
        self.bSound = 0
        self.bLCDUpdated = 0
        self.bLCD = 0
        self.nLCDValue = 0
        self.nVoltageVal = 0
        self.nCurrentVal = 0
        self.nTelltaleRed = -1
        self.nTelltaleGreen = -1
        self.nTelltaleBlue = -1
        self.nONCountG0 = -1
        self.nOFFCountG0 = -1
        self.nONCountG1 = -1
        self.nOFFCountG1 = -1
        self.nONCountG2 = -1
        self.nOFFCountG2 = -1
        self.nONCountF4 = -1
        self.nOFFCountF4 = -1
        self.nExtCurrentVal = 0
        self.nECallActive = -1
        self.nECallBacklight = -1
        self.nBCallActive = -1
        self.nBCallBacklight = -1
        self.listKeyOutVoltage = []
        self.listExtCurrent = []
        self.nlsBlackVal = None
        self.nlsWhiteVal = None
        self.nlsResult = None
        self.bRelaySet = 1
        self.nRelayCfg = 0      # 0 : RELAY_STATUS_FIX(OLD BENCH), 1 : RELAY_STATUS_COMMON
        self.nSD_errorCount = 0

    
    def WriteRawString(self, string):
        if self._conn is None:
            return "ERROR"

        try:
            print(string)
            self._conn.write(string)
            self._conn.flush()
            return "SUCCESS"
        except:
            return "ERROR"

    # Public Function -----------------------------------------------------------------
    
    def ReceiveStart(self, bStart, queue=None):
        if bStart is True:
            self.ReceiveThread = IVIQEBenchIOReceive(self, queue)
            self.ReceiveThread.daemon = True
            self.ReceiveThread.start()
        else:
            self.ReceiveThread.ReceiveExit()

        return "SUCCESS"

    
    def RequestSystemVersion(self):
        listData = []
        for i in range(2):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_VERSION

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def SystemReset(self, nMs=1000):
        listData = []
        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CFG_ATS_SYSTEM_RESET

        if 0 < nMs:
            listData[ATS_DATA_BYTE_INDEX + 0] = ATS_SYSTEM_RESET
            listData[ATS_DATA_BYTE_INDEX + 1] = (nMs >> 8) & 0xff
            listData[ATS_DATA_BYTE_INDEX + 2] = nMs & 0xff
        else:
            listData[ATS_DATA_BYTE_INDEX + 0] = ATS_SYSTEM_RESET
            listData[ATS_DATA_BYTE_INDEX + 1] = 0
            listData[ATS_DATA_BYTE_INDEX + 2] = 0

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def RelayInitialization(self, **init_value):            # bAcc, bBattery
        listData = []
        if self.nRelayCfg == 0:         # RELAY_STATUS_FIX(OLD BENCH)
            for _ in range(eATS_RELAY_MAX):
                listData.append(0)

            listData[ATS_DATA_BYTE_INDEX + eATS_RELAY_B_PLUS] = init_value['bBattery']
            listData[ATS_DATA_BYTE_INDEX + eATS_RELAY_ACC] = init_value['bAcc']
        else:                           # RELAY_STATUS_COMMON
            for _ in range(COMMON_SWITCH_MAX):
                listData.append(0)

            # SIGNAL_BATTERY, SIGNAL_ACC�� COMMON_SWITCH_1, COMMON_SWITCH_2�� ����
            listData[ATS_DATA_BYTE_INDEX + COMMON_SWITCH_1] = init_value['bBattery']
            listData[ATS_DATA_BYTE_INDEX + COMMON_SWITCH_2] = init_value['bAcc']

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CFG_ATS_RELAY_INITIAL

        self.Send(listData, len(listData))
        return "SUCCESS"
    
    def RequestRelayInitialStatus(self):
        listData = []
        for i in range(2):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_ATS_RELAY_STATUS

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def CalibrationStartOrStop(self, sensitivity, threshold):
        listData = []
        for i in range(6):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_INFO_REQ_AUTOCALIBRATION

        listData[ATS_DATA_BYTE_INDEX + 0]   = (sensitivity >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 1]   = sensitivity & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2]   = (threshold >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3]   = threshold & 0xff

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def AnnouncesAutoTestStartOrEnd(self, bisStart):
        listData = []
        for i in range(2):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        if bisStart == 1:
            listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_AUTOTEST_START
        else:
            listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_AUTOTEST_END

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def BatteryControl(self, nStatus, bench_info):
        if nStatus == 1 or 0 == nStatus:
            self.BatteryOnOff(nStatus, bench_info)
            event.clear()
            event.wait(2)
        else:
            self.BatteryVoltage(nStatus)
            event.clear()
            event.wait(2)

        return "SUCCESS"

    
    def BatterySinglePulse(self, nVoltage, nDelay, nCnt):
        listData = []
        for i in range(10):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_SINGLE_PULSE_CONTROL
        listData[ATS_DATA_BYTE_INDEX + 0] = 0x04 # Battery
        listData[ATS_DATA_BYTE_INDEX + 1] = 1
        listData[ATS_DATA_BYTE_INDEX + 2] = (int((nVoltage*10.0)) & 0xff)

        listData[ATS_DATA_BYTE_INDEX + 3] = ((nDelay >> 24) & 0xff)
        listData[ATS_DATA_BYTE_INDEX + 4] = ((nDelay >> 16) & 0xff)
        listData[ATS_DATA_BYTE_INDEX + 5] = ((nDelay >> 8) & 0xff)
        listData[ATS_DATA_BYTE_INDEX + 6] = ((nDelay) & 0xff)

        listData[ATS_DATA_BYTE_INDEX + 7] = nCnt

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def BatteryMinusControl(self, nStatus):
        if nStatus == 1 or 0 == nStatus:
            listData = []
            for i in range(4):
                listData.append(0)

            listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
            listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
            listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_B_GROUND

            if nStatus == 1:
                listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
            else:
                listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

            self.Send( listData, len(listData) )
            return "SUCCESS"
        else:
            return "ERROR"

    
    def AccControl(self, nStatus, bench_info):
        if nStatus == 1 or 0 == nStatus:
            self.ACCOnOff(nStatus, bench_info)
        else:
            self.AccVoltage(nStatus)

        return "SUCCESS"

    
    def ACCSinglePulse(self, nVoltage, nDelay, nCnt):
        listData = []
        for i in range(10):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_SINGLE_PULSE_CONTROL
        listData[ATS_DATA_BYTE_INDEX + 0] = 0x02 # ACC
        listData[ATS_DATA_BYTE_INDEX + 1] = 1
        listData[ATS_DATA_BYTE_INDEX + 2] = (int((nVoltage*10.0)) & 0xff)

        listData[ATS_DATA_BYTE_INDEX + 3] = ((nDelay >> 24) & 0xff)
        listData[ATS_DATA_BYTE_INDEX + 4] = ((nDelay >> 16) & 0xff)
        listData[ATS_DATA_BYTE_INDEX + 5] = ((nDelay >> 8) & 0xff)
        listData[ATS_DATA_BYTE_INDEX + 6] = (nDelay & 0xff)

        listData[ATS_DATA_BYTE_INDEX + 7] = nCnt

        self.Send( listData, len(listData) )
        return "SUCCESS"

    def ALTControl(self, status):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0] = eATS_RELAY_ALT

        if status == "ON":
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_OFF

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def FUELControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_FUEL

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    def IGNControl(self, status):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0] = eATS_RELAY_IGN

        if status == "ON":
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_OFF

        self.Send(listData, len(listData))
        return "SUCCESS"

    def IGN3Control(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_IGN3

        if nStatus == "ON":
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def AmpControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_AMP

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def ILLControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_ILLUMINATION_PLUS

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def ILLControlMinus(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_RELAY_ILLUMINATION_MINUS
        listData[ATS_DATA_BYTE_INDEX + 0] = (nStatus >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 1] = nStatus & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def ReverseControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_REAR_CAMERA

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def ParkingControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_PARKING

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def DoorControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_DOOR

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def DoorUnLockControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_DOOR_UNLOCK

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RcInControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_RC_IN

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def ComEnableControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_COM_ENABLE

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def AlarmDetectControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_ALARM_DETECT

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def KeylessBootingControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_KEYLESS_BOOT

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def AutolightControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_AUTOLIGHT

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def SpeedControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_CMD_SPEED
        listData[ATS_DATA_BYTE_INDEX + 0]   = (nStatus >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 1]   = nStatus & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def ANTControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_ANT

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def MICControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_MIC

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    def USB1Control(self, Status):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_USB1

        if Status == "ON":
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send(listData, len(listData))
        return "SUCCESS"


    def IPODControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_USB2

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    def AUXControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0] = eATS_RELAY_AUX

        if nStatus == "ON":
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_OFF

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def CDControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]    = eATS_RELAY_CDINSERT

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    #External Device Control-----
    
    def ShieldBoxControl(self, nStatus):
        listData = []
        for i in range(3):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_INFO_REQ_SHIELD_BOX_CONTROL

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 0]   = eATS_SHIELD_BOX_OPEN & 0xff
        else:
            listData[ATS_DATA_BYTE_INDEX + 0]   = eATS_SHIELD_BOX_CLOSE & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def USBSelectorControl(self, nOnPortNum):
        listData = []
        for i in range(3):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_CMD_USB_SELECTOR
        listData[ATS_DATA_BYTE_INDEX + 0]   = nOnPortNum & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def USBSelector10CHSwitch5CH(self, nType, nPortNum, nOnOffStatus):
        print("USBSelector10CHSwitch5CH :", nType, nPortNum, nOnOffStatus)

        listData = []
        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_CMD_USB_SELECTOR_10CH_SWITCH_5CH
        listData[ATS_DATA_BYTE_INDEX + 0]   = nPortNum & 0xff
        listData[ATS_DATA_BYTE_INDEX + 1]   = nType & 0xff

        listData[ATS_DATA_BYTE_INDEX + 2] = nOnOffStatus & 0xff
        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def USBFrontSwitchControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY

        if self.nRelayCfg == 0 :        # RELAY_STATUS_FIX(OLD BENCH)
            front_1 = eATS_RELAY_BAT_USB_FRONT_1
            front_2 = eATS_RELAY_BAT_USB_FRONT_2
            front_off = eATS_RELAY_BAT_USB_FRONT_OFF
        else:                           # RELAY_STATUS_COMMON
            front_1 = BAT_USB_FRONT_1
            front_2 = BAT_USB_FRONT_2
            front_off = BAT_USB_FRONT_OFF

        if nStatus == 1:
            # FRONT 2 OFF
            listData[ATS_DATA_BYTE_INDEX + 0] = front_2
            listData[ATS_DATA_BYTE_INDEX + 1] = 0x00
            self.Send(listData, len(listData))
            # FRONT 1 ON
            listData[ATS_DATA_BYTE_INDEX + 0] = front_1
            listData[ATS_DATA_BYTE_INDEX + 1] = 0x01
            self.Send(listData, len(listData))
        elif nStatus == 2:
            # FRONT 1 OFF
            listData[ATS_DATA_BYTE_INDEX + 0] = front_1
            listData[ATS_DATA_BYTE_INDEX + 1] = 0x00
            self.Send(listData, len(listData))
            # FRONT 2 ON
            listData[ATS_DATA_BYTE_INDEX + 0] = front_2
            listData[ATS_DATA_BYTE_INDEX + 1] = 0x01
            self.Send(listData, len(listData))
        else:
            listData[ATS_DATA_BYTE_INDEX + 0]    = front_off
            # USB Front Switch OFF
            listData[ATS_DATA_BYTE_INDEX + 1]    = 0x00
            self.Send(listData, len(listData))

        # self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def USBRearSwitchControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY

        if self.nRelayCfg == 0 :        # RELAY_STATUS_FIX(OLD BENCH)
            rear_1 = eATS_RELAY_BAT_USB_REAR_1
            rear_2 = eATS_RELAY_BAT_USB_REAR_2
            rear_off = eATS_RELAY_BAT_USB_REAR_OFF
        else:                           # RELAY_STATUS_COMMON
            rear_1 = BAT_USB_REAR_1
            rear_2 = BAT_USB_REAR_2
            rear_off = BAT_USB_REAR_OFF

        if nStatus == 1:
            # REAR 2 OFF
            listData[ATS_DATA_BYTE_INDEX + 0] = rear_2
            listData[ATS_DATA_BYTE_INDEX + 1] = 0x00
            self.Send(listData, len(listData))
            # REAR 1 ON
            listData[ATS_DATA_BYTE_INDEX + 0]    = rear_1
            listData[ATS_DATA_BYTE_INDEX + 1]    = 0x01
            self.Send(listData, len(listData))
        elif nStatus == 2:
            # REAR 1 OFF
            listData[ATS_DATA_BYTE_INDEX + 0] = rear_1
            listData[ATS_DATA_BYTE_INDEX + 1] = 0x00
            self.Send(listData, len(listData))
            # REAR 2 ON
            listData[ATS_DATA_BYTE_INDEX + 0]    = rear_2
            listData[ATS_DATA_BYTE_INDEX + 1]    = 0x01
            self.Send(listData, len(listData))
        else:
            listData[ATS_DATA_BYTE_INDEX + 0]    = rear_off
            # USB Rear Switch OFF
            listData[ATS_DATA_BYTE_INDEX + 1]    = 0x01
            self.Send(listData, len(listData))
        # self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def PPositonControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_AUTO_PARKING

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def NPositionControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0] = eATS_RELAY_N_POSITION

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_OFF

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def TransDetectControl(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0] = eATS_RELAY_TRANS_DETECT

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_OFF

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def BATSwitchUSBtoPC(self):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY

        if self.nRelayCfg == 0:  # RELAY_STATUS_FIX(OLD BENCH)
            listData[ATS_DATA_BYTE_INDEX + 0]    = eATS_RELAY_BAT_USB_TO_PC
        else:                   # RELAY_STATUS_COMMON
            listData[ATS_DATA_BYTE_INDEX + 0]    = BAT_USB_TO_PC

        listData[ATS_DATA_BYTE_INDEX + 1]    = 0x01 # ON

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def BATSwitchSETtoPC(self):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY

        if self.nRelayCfg == 0:  # RELAY_STATUS_FIX(OLD BENCH)
            listData[ATS_DATA_BYTE_INDEX + 0]    = eATS_RELAY_BAT_SET_TO_PC
        else:  # RELAY_STATUS_COMMON
            listData[ATS_DATA_BYTE_INDEX + 0] = BAT_SET_TO_PC

        listData[ATS_DATA_BYTE_INDEX + 1]    = 0x01 # ON

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestSoundFileInfo(self, nSoundType):
        listData = []
        for i in range(3):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_INFO_REQ_VOICE_FILE_INFO

        listData[ATS_DATA_BYTE_INDEX]       = nSoundType & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestSoundFileList(self, nSoundType):
        listData = []
        for i in range(3):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_INFO_REQ_VOICE_FILE_LIST

        listData[ATS_DATA_BYTE_INDEX]       = nSoundType & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def PlaySound(self, strFileName, nFileNameLen):
        listData = []
        for i in range(4+nFileNameLen):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_CMD_VR
        listData[ATS_DATA_BYTE_INDEX + 0]   = (nFileNameLen >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 1]   = nFileNameLen & 0xff

        idx = 4
        for ch in strFileName:
            listData[idx] = ord(ch)
            idx += 1

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestVoltagePeriodicallyNoti(self, bStart, nPeriodMs):
        listData = []
        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_CFG_VOLTAGE_STATUS_CHECK_TIME

        listData[ATS_DATA_BYTE_INDEX]       = bStart
        listData[ATS_DATA_BYTE_INDEX+1]     = (nPeriodMs>>8) & 0xff
        listData[ATS_DATA_BYTE_INDEX+2]     = nPeriodMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestCurrentPeriodicallyNoti(self, bStart, nPeriodMs):
        listData = []
        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_CFG_CURRENT_STATUS_CHECK_TIME

        listData[ATS_DATA_BYTE_INDEX]       = bStart
        listData[ATS_DATA_BYTE_INDEX+1]     = (nPeriodMs>>8) & 0xff
        listData[ATS_DATA_BYTE_INDEX+2]     = nPeriodMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestLCDStatus(self, bStart, nMs ):
        listData = []
        for i in range(5):
            listData.append(0)

        nMs = nMs/100

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        #0x62 -> Noti 0x63
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_LCD_STATUS

        listData[ATS_DATA_BYTE_INDEX + 0] = bStart
        listData[ATS_DATA_BYTE_INDEX + 1] = (nMs >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2] = nMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestLCDIllumiSensorValue(self, bStart, nMs):
        listData = []
        for i in range(6):
            listData.append(0)

        nMs = nMs/100

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        #0x12, -> Noti 0xE0
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CFG_LCD_STATUS_CHECK_TIME

        listData[ATS_DATA_BYTE_INDEX + 0] = bStart
        listData[ATS_DATA_BYTE_INDEX + 1] = 0
        listData[ATS_DATA_BYTE_INDEX + 2] = (nMs >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3] = nMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestLCDStatusAndIllumiSensorValuePeriodicallyNoti(self, bStart, nPeriodMs):
        listData = []
        for i in range(6):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        #0x17, -> Noti 0xE9
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CFG_LCDCHANGE_STATUS

        listData[ATS_DATA_BYTE_INDEX + 0] = bStart
        listData[ATS_DATA_BYTE_INDEX + 1] = 1
        listData[ATS_DATA_BYTE_INDEX + 2] = (nPeriodMs >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3] = nPeriodMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestAudioFrequency(self, bStart, nMs):
        listData = []
        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        #0x64 -> Noti 0x65
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_AUDIO_COMPARE_STATUS

        listData[ATS_DATA_BYTE_INDEX + 0] = bStart
        listData[ATS_DATA_BYTE_INDEX + 1] = (nMs >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2] = nMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestAudioStatusAndADCValue(self, nMs):
        listData = []
        for i in range(4):
            listData.append(0)

        nMs = nMs/100

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        #0x66 -> Noti 0x67
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_AUDIO_ONOFF_STATUS
        listData[ATS_DATA_BYTE_INDEX + 0] = (nMs >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 1] = nMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestAudioStatusPeriodicallyNoti(self, bStart, nPeriodMs):
        listData = []
        for i in range(6):
            listData.append(0)

        nPeriodMs = nPeriodMs/100

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        #0x13 -> Noti 0xE1
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CFG_AUDIO_STATUS_CHECK_TIME

        listData[ATS_DATA_BYTE_INDEX + 0] = bStart
        listData[ATS_DATA_BYTE_INDEX + 1] = 0
        listData[ATS_DATA_BYTE_INDEX + 2] = (nPeriodMs >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3] = nPeriodMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def RequestAudioStatusAndADCValuePeriodicallyNoti(self, bStart, nPeriodMs):
        listData = []
        for i in range(5):
            listData.append(0)

        #0x13 after
        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        #0x1E -> Noti 0xEB
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CFG_AUDIO_DATA_SET_TIME

        listData[ATS_DATA_BYTE_INDEX] = bStart
        listData[ATS_DATA_BYTE_INDEX+1] = (nPeriodMs>>8) & 0xff
        listData[ATS_DATA_BYTE_INDEX+2] = nPeriodMs & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def InitSoundUpdate(self):
        self.bSoundUpdated = 0
        return "SUCCESS"

    
    def WaitSoundUpdate(self, timeout):
        for _ in range(int(timeout) * 5):
            if self.bSoundUpdated:
                self.InitSoundUpdate()
                if self.bSound:
                    return CHECK_RESULT_OK
                else:
                    return CHECK_RESULT_FAIL
            else:
                time.sleep(0.2)

        return CHECK_RESULT_TIMEOUT


    # Test Result Check
    
    def CheckSound(self, nStatus):
        # self.CalibrationStartOrStop(0x00)
        # time.sleep(1)
        mea_ms = 200 # measurement duration 200 ms
        self.bSoundUpdated = 0
        self.RequestAudioStatusAndADCValue(mea_ms * 100)
        time.sleep(mea_ms/1000 + 0.5)
        for i in range(4):      # wait margin 2 sec
            if self.bSoundUpdated:
                self.bSoundUpdated = 0
                if self.bSound == nStatus:
                    return CHECK_RESULT_OK
                else:
                    return CHECK_RESULT_FAIL
            else:
                time.sleep(0.5)

        return CHECK_RESULT_TIMEOUT

    
    def CheckLCDDimming(self, nStatus):
        nLCDValue = self.GetLCDValue()
        #print("[CheckLCDDimming] LCDValue = " + str(nLCDValue))
        #logger.debug("nLCDValue = " + str(nLCDValue))

        return nLCDValue

    
    def CheckLCD(self, nStatus):
        self.RequestLCDStatus( 1, 5000 )

        for i in range(30):
            if self.bLCDUpdated == 1:
                if self.bLCD == 2:
                    return CHECK_RESULT_TIMEOUT
                else:
                    if self.bLCD == nStatus:
                        return CHECK_RESULT_OK
                    else:
                        return CHECK_RESULT_FAIL

                self.bLCDUpdated = 0
                break
            else:
                time.sleep(1)
        return CHECK_RESULT_TIMEOUT

    
    def CheckVoltage(self, nMin, nMax):
        nVal = self.nVoltageVal
        if nMin <= nVal <= nMax:
            return CHECK_RESULT_OK, nVal
        else:
            return CHECK_RESULT_FAIL, nVal

    
    def CheckCurrent(self, nMin, nMax):
        nVal = self.nCurrentVal
        if nMin <= nVal <= nMax:
            return CHECK_RESULT_OK, nVal
        else:
            return CHECK_RESULT_FAIL, nVal

    
    def ClearKeyOutVoltageList(self):
        del self.listKeyOutVoltage[:]

    
    def CheckKeyOutVoltage(self, nMin, nMax):
        listVol = self.listKeyOutVoltage
        ret = CHECK_RESULT_FAIL, listVol
        for vol in listVol:            
            if nMin <= vol <= nMax:
                ret = CHECK_RESULT_OK, listVol
                break
        
        return ret

    # def LCDWatchStart(self, nInterval, queue):
    #     if (DEBUG_ENABLE==1):
    #         print ("LCDWatchStart")
    #     self.LCDWatchThread = IVIQEBenchIOLCDWatch(parent=self, nInterval= nInterval, queue=queue)
    #     self.LCDWatchThread.daemon = True
    #     self.LCDWatchThread.start()
    #     return "SUCCESS"
    #
    # def LCDWatchStop(self):
    #     if (DEBUG_ENABLE==1):
    #         print ("LCDWatchStop")
    #     self.LCDWatchThread.LCDWatchExit()
    #     return "SUCCESS"

    # Private Function -----------------------------------------------------------------
    
    def GetLCDValue(self):
        return self.nLCDValue

    
    def SetLCDValue(self, nLCDValue):
        self.nLCDValue = nLCDValue
        return "SUCCESS"

    
    def SetSystemTime(self):
        listData = []
        for i in range(9):
            listData.append(0)

        currtime = time.localtime()

        nYear = currtime.tm_year
        nMonth = currtime.tm_mon
        nDay = currtime.tm_mday
        nHour = currtime.tm_hour
        nMinute = currtime.tm_min
        nSecond = currtime.tm_sec

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CFG_ATS_RTC

        listData[ATS_DATA_BYTE_INDEX + 0]    = (nYear>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 1]    = (nYear) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2]    = nMonth & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3]    = nDay & 0xff
        listData[ATS_DATA_BYTE_INDEX + 4]    = nHour & 0xff
        listData[ATS_DATA_BYTE_INDEX + 5]    = nMinute & 0xff
        listData[ATS_DATA_BYTE_INDEX + 6]    = nSecond & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def BatteryVoltage(self, nState):
        listData = []
        for i in range(3):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]   = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_VOLTAGE
        listData[ATS_DATA_BYTE_INDEX + 0] = (int((nState*10.0)) & 0xff)

        self.Send( listData, len(listData) )
        return "SUCCESS"

    def BatteryOnOff(self, nState, bench_info="OLD_BENCH"):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        if bench_info == 'OLD_BENCH':
            listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_B_PLUS
        else:
            listData[ATS_DATA_BYTE_INDEX + 0] = COMMON_SWITCH_1

        if nState == "ON":
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def AccVoltage(self, nState):
        listData = []
        for i in range(3):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]     = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]   = eATS_CMD_CODE_CMD_ACC_VOLTAGE
        listData[ATS_DATA_BYTE_INDEX + 0]   = (int((nState*10.0)) & 0xff)

        self.Send( listData, len(listData) )
        return "SUCCESS"

    def ACCOnOff(self, state, bench_info="OLD_BENCH"):
        #print ("ACCOnOff :"), (bisOn)

        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_RELAY

        if bench_info == "OLD_BENCH":
            listData[ATS_DATA_BYTE_INDEX + 0] = eATS_RELAY_ACC
        else:
            listData[ATS_DATA_BYTE_INDEX + 0] = COMMON_SWITCH_2

        if state == "ON":
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1] = ATS_RELAY_OFF

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def Detent(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_DETENT

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def Telltale(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_TELLTALE

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def ReverseActiveLow(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_REV_IN_STATE_R16

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def ReverseActiveHigh(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_REV_IN_STATE_R15

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def AVNSDDetect(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_AVNSDDETECT

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def Relay_10(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_10

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def Relay_11(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_11

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def Relay_12(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_12

        if (nStatus == 1):
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def Relay_13(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_13

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def Relay_14(self, nStatus):
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eATS_RELAY_14

        if nStatus == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"


    @staticmethod
    def CalcCRC16(listData, nLen):
        crc16=0xFFFF
        for nData in listData:
            tmp = ((nData & 0xFF) ^ (crc16 & 0x00FF))
            i = 0
            while i < 8:
                if(tmp & 1):
                    tmp = (tmp>>1) ^ ATS_PROTOCOL_CRC_KEY_VALUE
                else:
                    tmp >>= 1
                i += 1
            crc16 = (crc16 >> 8) ^ tmp

        return crc16

    
    def Send(self, listData, nLen):
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
        listSendBuff[nIndex] = (nLen + ATS_TAIL_BYTE_LENGTH) & 0xff
        nIndex += 1

        for nData in listData:
            # DATA
            listSendBuff[nIndex] = nData
            nIndex += 1

        sCRC16 = self.CalcCRC16(listData, nLen)
        # CRC
        listSendBuff[nIndex] = (sCRC16>>8)&0xff
        nIndex += 1
        # CRC
        listSendBuff[nIndex] = sCRC16 & 0xff
        nIndex += 1

        # ATS_TAIL 0x6f
        listSendBuff[nIndex] = ATS_TAIL
        nIndex += 1

        self.WriteRawString(listSendBuff)

        return "SUCCESS"

    def AirbagCrashDetection(self, model, nStatus):
        listData = []
        listData.append(ATS_CMD_ID)
        listData.append(eATS_CMD_CODE_CMD_AIRBAG_CRASH_DETECT)
        listData.append(int(nStatus, 16))
        listData.append(int(model, 16))
        self.Send(listData, len(listData))
        return "SUCCESS"

    def rf_btn_press(self, btnNum, delay):
        print('Common bench ecall button press', btnNum, delay)
        nbtnNum = int(btnNum)
        ndelay = int(float(delay)*1000)
        return self.TenButton(nbtnNum, ndelay)

    
    def TenButton(self, nButton, nDelay):
        listData = []

        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] =  eATS_CMD_CODE_CMD_TENKEY_MODULE_CONTROL
        listData[ATS_DATA_BYTE_INDEX + 0] = nButton
        listData[ATS_DATA_BYTE_INDEX + 1] = (nDelay >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2] = nDelay & 0xff

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def TwoButton(self, nButton, nDelay):
        nVariableResistorData = 0
        if nButton == 0x00:
            nVariableResistorData = 70
        elif nButton == 0x01:
            nVariableResistorData = 40

        listData = []

        for i in range(7):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CMD_NGT_TWO_BUTTON
        listData[ATS_DATA_BYTE_INDEX + 0] = nButton
        listData[ATS_DATA_BYTE_INDEX + 1] = (nDelay >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2] = nDelay & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3] = (nVariableResistorData >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 4] = nVariableResistorData & 0xff

        self.Send(listData, len(listData))
        event.clear()
        event.wait(2)
        return "SUCCESS"

    
    def ThreeButton(self, nButton, nDelay):
        nVariableResistorData = 0
        if nButton == 0x00:
            nVariableResistorData = 3
        elif nButton == 0x01:
            nVariableResistorData = 11
        elif nButton == 0x02:
            nVariableResistorData = 70
        
        listData = []

        for i in range(7):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_THREE_BUTTON
        listData[ATS_DATA_BYTE_INDEX + 0]       = nButton
        listData[ATS_DATA_BYTE_INDEX + 1]       = (nDelay>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 2]       = nDelay & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3]       = (nVariableResistorData>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 4]       = nVariableResistorData & 0xff

        self.Send( listData, len(listData) )
        event.clear()
        event.wait(2)        
        return "SUCCESS"

    
    def ThreeButton_Front(self, nButton, nDelay):
        nVariableResistorData = 0
        if nButton == 0x00:
            nVariableResistorData = 2
        elif nButton == 0x01:
            nVariableResistorData = 10
        elif nButton == 0x02:
            nVariableResistorData = 60
        
        listData = []

        for i in range(7):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_THREE_BUTTON
        listData[ATS_DATA_BYTE_INDEX + 0]       = nButton
        listData[ATS_DATA_BYTE_INDEX + 1]       = (nDelay>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 2]       = nDelay & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3]       = (nVariableResistorData>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 4]       = nVariableResistorData & 0xff

        self.Send( listData, len(listData) )
        event.clear()
        event.wait(2)
        return "SUCCESS"

    
    def ThreeButton_Rear(self, nButton, nDelay):
        nVariableResistorData = 0
        if nButton == 0x00:
            nVariableResistorData = 2
        elif nButton == 0x01:
            nVariableResistorData = 10
        elif nButton == 0x02:
            nVariableResistorData = 60
        
        listData = []

        for i in range(7):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_THREE_BUTTON_REAR
        listData[ATS_DATA_BYTE_INDEX + 0]       = nButton
        listData[ATS_DATA_BYTE_INDEX + 1]       = (nDelay>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 2]       = nDelay & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3]       = (nVariableResistorData>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 4]       = nVariableResistorData & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def LEDPulse(self, nPeriod):
        self.nONCountG0  = -1
        self.nOFFCountG0 = -1
        self.nONCountG1  = -1
        self.nOFFCountG1 = -1
        self.nONCountG2  = -1
        self.nOFFCountG2 = -1
        self.nONCountF4  = -1
        self.nOFFCountF4 = -1

        listData = []

        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_LED_PULSE
        listData[ATS_DATA_BYTE_INDEX + 0] = (nPeriod >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 1] = nPeriod & 0xff

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def GetLEDPulse(self):
        return self.nONCountG0, self.nOFFCountG0, self.nONCountG1, self.nOFFCountG1, self.nONCountG2, self.nOFFCountG2, self.nONCountF4, self.nOFFCountF4

    
    def TelltaleLED(self, nPeriod):
        self.nTelltaleRed = -1
        self.nTelltaleGreen = -1
        self.nTelltaleBlue = -1
            
        listData = []

        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_INFO_REQ_TELLTALE_LED
        listData[ATS_DATA_BYTE_INDEX + 0]       = (nPeriod>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 1]       = nPeriod & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def CheckTelltale(self):
        nGreen = self.nTelltaleGreen
        nRed = self.nTelltaleRed
        nBlue = self.nTelltaleBlue

        return nGreen, nRed, nBlue

    
    def LedTCU4(self, nPeriod):
        
        self.nECallActive = -1
        self.nECallBacklight = -1
        self.nBCallActive = -1
        self.nBCallBacklight = -1
            
        listData = []

        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_TELLTALE_LED
        listData[ATS_DATA_BYTE_INDEX + 0] = (nPeriod>>8)&0xff
        listData[ATS_DATA_BYTE_INDEX + 1] = nPeriod & 0xff

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def CheckLedTCU4(self):
        
        nECallActive = self.nECallActive
        nECallBacklight = self.nECallBacklight
        nBCallActive = self.nBCallActive
        nBCallBacklight = self.nBCallBacklight
        
        return nECallActive, nECallBacklight, nBCallActive, nBCallBacklight

    
    def ClearEXTCurrentList(self):
        del self.listExtCurrent[:]

    
    def GetEXTCurrent(self):
        tout = 0
        while True:
            if self.nExtCurrentVal > 0:
                ret, res = (CHECK_RESULT_OK, self.nExtCurrentVal)
                break
            else:
                ret, res = (CHECK_RESULT_FAIL, -1)
                time.sleep(0.5)
                tout = tout + 1
                if tout > 4: break
        return ret, res

    
    def CheckEXTCurrentList(self, nMin, nMax):
        listCurr = self.listExtCurrent
        ret = CHECK_RESULT_OK
        for curr in listCurr:
            if nMin > curr or nMax < curr:
                ret = CHECK_RESULT_FAIL
                break
            else:
                ret = CHECK_RESULT_OK

        return ret, listCurr


    @staticmethod
    def timer_compare(Time_Init, Time_Check):
        nTime = Time_Check - Time_Init
        mydelta = datetime.timedelta(seconds=nTime)
        mytime = datetime.datetime.min + mydelta
        h, m, s = mytime.hour, mytime.minute, mytime.second
        ret = ("%d hour %02d minuit %02d second" % (h, m, s))
        return ret


    def ExtCheckCurrent(self, fMin, fMax):
        ret = CHECK_RESULT_ERROR
        nCurrlen = len(self.listExtCurrent)

        if nCurrlen == 0:
            self.listExtCurrent.append(-1)
            ret = CHECK_RESULT_ERROR
        else:
            for idx in range(0, nCurrlen):
                if self.listExtCurrent[idx] >= 50 or -1 < self.listExtCurrent[idx] < 0:
                    self.listExtCurrent[idx] = 0.0

            if fMin <= self.listExtCurrent[nCurrlen-1] <= fMax:
                ret = CHECK_RESULT_OK
            else:
                ret = CHECK_RESULT_FAIL

        return ret, self.listExtCurrent

    def EXT_CURRENT(self, min_current, max_current, check_delay):
        self.ClearEXTCurrentList()
        self.RequestExtCurrentPeriodicallyNoti(1, 200)

        self.result_queue = queue.Queue()
        self.ReceiveStart(True, {'q', self.result_queue})

        Check_Count = 0
        Error_Count = 0

        fMin = float(min_current)
        fMax = float(max_current)
        nWaitDelay = int(check_delay)

        if fMin is not None and fMax is not None:
            cnt_sec = nWaitDelay / 1000
            aTime = time.time()
            while True:
                time.sleep(0.2)
                bTime = time.time()
                nTime = bTime - aTime
                nCheckResult, nVal = self.ExtCheckCurrent(fMin, fMax)

                nlen = len(nVal)
                if nCheckResult == CHECK_RESULT_ERROR:
                    Error_Count += 1
                    Check_Count = 0
                elif nCheckResult == CHECK_RESULT_OK:
                    Check_Count += 1
                    Error_Count = 0
                else:
                    Check_Count = 0

                if cnt_sec <= nTime:
                    Time_ret = self.timer_compare(aTime, bTime)
                    ret = ("fail", Time_ret, "Current Check Fail!! {0}".format(nVal))
                    break
                elif Error_Count >= 20:
                    Time_ret = self.timer_compare(aTime, bTime)
                    ret = ("error", Time_ret, "Current get error!!! {0}".format(nVal))
                    break
                elif Check_Count >= 10:
                    Time_ret = self.timer_compare(aTime, bTime)
                    ret = ("pass", Time_ret, "Current Check OK! {0}".format(nVal[nlen-1]))
                    break
        else:
            ret = 'error', 'invalid data: {0}~{1}'.format(fMin, fMax)

        self.ReceiveStart(False)

        self.RequestExtCurrentPeriodicallyNoti(0, 0)
        self.ClearEXTCurrentList()

        return ret

    
    def RequestExtCurrentPeriodicallyNoti(self, bStart, nPeriodMs):
        self.nExtCurrentVal = 0

        listData = []
        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_MT4N_CURRENT_CONTROL

        listData[ATS_DATA_BYTE_INDEX] = bStart
        listData[ATS_DATA_BYTE_INDEX + 1] = (nPeriodMs >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2] = nPeriodMs & 0xff

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def RequestKeyOutVoltagePeriodicallyNoti(self, bStart, nPeriodMs):
        listData = []
        for i in range(5):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_INFO_REQ_KEY_OUT_VOLTAGE_CONTROL

        listData[ATS_DATA_BYTE_INDEX] = bStart
        listData[ATS_DATA_BYTE_INDEX + 1] = (nPeriodMs >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2] = nPeriodMs & 0xff

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def RequestLightSensorControl(self,nStatus, pbData, twData):
        self.nlsResult = None
        listData = []
        listData.append(ATS_CMD_ID)
        listData.append(eATS_CMD_CODE_INFO_REQ_LIGHTSENSOR_RESULT)
        listData.append(nStatus)
        if nStatus == 0x00 or nStatus == 0x02:
            listData.append((pbData >> 24) & 0xff)
            listData.append((pbData >> 16) & 0xff)
            listData.append((pbData >> 8) & 0xff)
            listData.append(pbData & 0xff)
            listData.append((twData >> 24) & 0xff)
            listData.append((twData >> 16) & 0xff)
            listData.append((twData >> 8) & 0xff)
            listData.append(twData & 0xff)
        else:
            pass

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def CheckLightSensorResult(self):
        return self.nlsResult

    
    def GetLightSensorData(self):
        return self.nlsBlackVal, self.nlsWhiteVal

    
    def SetRelayConfig(self, nRelayCfg):
        listData = []
        for i in range(3):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = eATS_CMD_CODE_CFG_COMMON_RELAY_SET

        listData[ATS_DATA_BYTE_INDEX] = nRelayCfg

        self.nRelayCfg = nRelayCfg

        self.Send(listData, len(listData))
        return "SUCCESS"

    
    def RequestRelaySet(self, nCmdList):
        for cmd in nCmdList:
            while True:
                if self.bRelaySet == 1:
                    break
                time.sleep(0.05)

            listData = []
            listData.append(ATS_CMD_ID)
            listData.append(eATS_CMD_CODE_INFO_REQ_COMMON_RELAY_NAME_SET)

            # relay_no
            listData.append(eval(cmd[1]))
            s = list(cmd[0])

            for ch in s:
                listData.append(int(ord(ch)))

            self.bRelaySet = 0
            self.Send(listData, len(listData))

            # print ("RequestRelaySet :"), (listData)

        return "SUCCESS"

    
    def CallRelayFunc(self, func_index, data):
        if func_index == '' or func_index is None:
            return  'error', 'Invalid command : This command is not supported in this bench''s version'
        listData = []
        for i in range(4):
            listData.append(0)

        listData[ATS_CMD_ID_BYTE_INDEX]         = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX]       = eATS_CMD_CODE_CMD_RELAY
        listData[ATS_DATA_BYTE_INDEX + 0]       = eval(func_index)

        if data == 1:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_ON
        else:
            listData[ATS_DATA_BYTE_INDEX + 1]   = ATS_RELAY_OFF

        self.Send( listData, len(listData) )
        return "SUCCESS"

    
    def FileToSD(self, userFilePath, sdCardFileName):
        
        if os.path.isfile(userFilePath) == False:
            return 'error', 'No user file'
        if sdCardFileName == '':
            return 'error', 'wrtie sdCardFileName'
        
        start = 0
        trans = 1
        end = 2
        totalLineCount = 0
        self.nSD_errorCount = -1
        
        #start
        listData = []
        for i in range(4 + len(sdCardFileName)):
            listData.append(0)
        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = Request_File_To_SD_Card
        listData[ATS_DATA_BYTE_INDEX] = start
        listData[ATS_DATA_BYTE_INDEX + 1] = (len(sdCardFileName)) & 0xff
        for i in range(len(sdCardFileName)):
            listData[ATS_DATA_BYTE_INDEX + 2 +i] = ord(sdCardFileName[i]) & 0xff
        
        self.Send(listData, len(listData))
        event_sd.clear()
        event_sd.wait(2)
        del listData[:]
        if self.nSD_errorCount != 0:
            print('start error code : %d' %self.nSD_errorCount)
            return 'error','error code : %d' %self.nSD_errorCount
        
        #trans
        f = open(userFilePath,'r')
        while True:
            readByte = f.read(230)
            if len(readByte) == 0:
                break
        
            lineLength = len(readByte)
            listData = [0,0,0,0]
            listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
            listData[ATS_CMD_TYPE_BYTE_INDEX] = Request_File_To_SD_Card
            listData[ATS_DATA_BYTE_INDEX] = trans
            listData[ATS_DATA_BYTE_INDEX + 1] = lineLength
            for i in range(lineLength):
                listData.append(ord(readByte[i]) & 0xff)
            self.Send(listData, len(listData))
            event_sd.clear()
            event_sd.wait(2)
            del listData[:]
            if self.nSD_errorCount != 0:
                print('trans error code : %d' %self.nSD_errorCount)
                return 'error','error code : %d' %self.nSD_errorCount
            totalLineCount += 1
        
        #end
        listData = []
        for i in range(7):
            listData.append(0)
        listData[ATS_CMD_ID_BYTE_INDEX] = ATS_CMD_ID
        listData[ATS_CMD_TYPE_BYTE_INDEX] = Request_File_To_SD_Card
        listData[ATS_DATA_BYTE_INDEX] = end
        listData[ATS_DATA_BYTE_INDEX + 1] = (totalLineCount >> 24) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 2] = (totalLineCount >> 16) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 3] = (totalLineCount >> 8) & 0xff
        listData[ATS_DATA_BYTE_INDEX + 4] = totalLineCount & 0xff
        self.Send(listData, len(listData))
        event_sd.clear()
        event_sd.wait(2)
        #print listData
        del listData[:]
        if self.nSD_errorCount != 0:
            print('end error code : %d' %self.nSD_errorCount)
            return 'error', 'error code : %d' %self.nSD_errorCount
        
        
        return "SUCCESS"

    def Connect(self):
        port = None

        port = self._port

        if port is None:
            return "ERROR"

        try:
            self._conn = Serial(port, self._bps, timeout=self._timeout)
        except Exception:
            return "ERROR"

        return "CONNECTED"

    def Disconnect(self):
        self._conn.close()
        self._conn = None

    def Read(self):
        if self._conn is None:
            return "ERROR", None

        char = self._conn.read()

        if not char:
            return "ERROR", None

        return "SUCCESS", "".join(map(chr, char))