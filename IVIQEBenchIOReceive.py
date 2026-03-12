# -*- coding: utf-8 -*-
'''
Created on 2016. 9. 20.
@author: baekho.choi

Modified for using on robot framework on 2024.01.10
@author : Seungwon.Jung (sw125.jung@lgepartner.com)
'''
import time
import threading
from dqa_common_rbf.plugin_lib.common_bench.IVIQEBenchIOProtocol import *

IVIQEBENCHIO_PORT = "IVIQEBenchIOPluginInfo"

DEBUG_ENABLE = 1

event = threading.Event()
event_sd = threading.Event()

class IVIQEBenchIOReceive(threading.Thread):
    '''
        Receive the log data using thread from QE Bench
    '''
    def __init__(self, parent, queue):
        threading.Thread.__init__(self)
        self.__suspend = False
        self.__exit = False
        self.parent = parent
        self.relayQueue = queue
        print((queue, type(queue)))

    def run(self):
        # print ("IVIQEBenchIOReceive Thread Start")
        print("bench thread run")
        arReceivedData = []
        preRetByte = None
        bStartBit = False
        totalLen = None
        count = 0

        while True:
            ### Suspend ###
            while self.__suspend:
                time.sleep(0.5)

            retCode, retChar = self.parent.Read()
            # print ("Read "), (retChar)

            if preRetByte == 0x61:
                bStartBit = True

            if retCode == "SUCCESS":
                retByte = ord(retChar)
                preRetByte = retByte
                if retByte == 0x61 and bStartBit is True and count == 0:
                    del arReceivedData[0:len(arReceivedData)]
                    count = 0
                    arReceivedData.append(0x61)
                    arReceivedData.append(0x61)
                    count += 2
                elif len(arReceivedData) >= 2:
                    arReceivedData.append(retByte)
                    count += 1

                if count == 6:
                    nPackLen = ((arReceivedData[2] << 24) & 0xff000000)
                    nPackLen |= ((arReceivedData[3] << 16) & 0xff0000)
                    nPackLen |= ((arReceivedData[4] << 8) & 0xff00)
                    nPackLen |= ((arReceivedData[5]) & 0xff)

                    totalLen = 6 + nPackLen

                    if nPackLen >= 0xFF:
                        # Packet is worng maybe
                        del arReceivedData[0:len(arReceivedData)]
                        preRetByte = None
                        bStartBit = False
                        totalLen = None
                        count = 0

                if totalLen is not None and count == totalLen:
                    if arReceivedData[count - 1] == 0x6f:
                        self.ProcessReceivedData(arReceivedData)
                    else:
                        print('Packet is wrong.')

                    del arReceivedData[0:len(arReceivedData)]
                    preRetByte = None
                    bStartBit = False
                    totalLen = None
                    count = 0

            if self.__exit:
                break

    #    def run(self):
    #        b61 = 0
    #        arReceivedData = []
    #
    #        #print ("IVIQEBenchIOReceive Thread Start")
    #
    #        while True:
    #            ### Suspend ###
    #            while self.__suspend:
    #                time.sleep(0.5)
    #
    #            retCode, retChar = self.parent.Read()
    #            #print ("Read "), (retChar)
    #
    #            if (retCode == "SUCCESS"):
    #                retByte = ord(retChar)
    #                #print (hex(retByte))
    #                if (retByte == 0x61):
    #                    nLen = len(arReceivedData)
    #                    if (b61 == 1):
    #                        if(2 < nLen):
    #                            arReceivedData.pop(nLen-1)
    #                            self.ProcessReceivedData(arReceivedData)
    #
    #                            b61 = 0
    #                            del arReceivedData[0:len(arReceivedData)]
    #                            arReceivedData.append(0x61)
    #                            #print ("append1 :"), ("0x61")
    #                            arReceivedData.append(0x61)
    #                            #print ("append2 :"), ("0x61")
    #                        else:
    #                            b61 = 0
    #                            arReceivedData.append(retByte)
    #                            #print ("append3 :"), (hex(retByte))
    #                    else:
    #                        b61 = 1
    #                        arReceivedData.append(retByte)
    #                        #print ("append4 :"), (hex(retByte))
    #                else:
    #                    b61 = 0
    #                    arReceivedData.append(retByte)
    #                    #print ("append5 :"), (hex(retByte))
    #            """
    #            else:
    #                print ("Read Error")
    #            """
    #
    #            time.sleep(0)
    #
    #            ### Exit ###
    #            if self.__exit:
    #                break
    #
    #        #print ("IVIQEBenchIOReceive Thread End")

    def ReceiveSuspend(self):
        self.__suspend = True

    def ReceiveResume(self):
        self.__suspend = False

    def ReceiveExit(self):
        self.__exit = True

    def ProcessReceivedData(self, arData):
        # print ("<< "), (len(arData)), (' '.join([hex(i) for i in arData]))

        nDataLen = len(arData)
        if 9 <= nDataLen:
            # ///////////////////////////////////////////////////////////////////////////////
            #
            # Start  Start  PackLen   CommandID   CommandType   Data   Crc   End
            #  bit    bit                                                    bit
            # -----------------------------------------------------------------------------
            #  0x61   0x61      n         n           n           n     n    0x6f    val
            #   1      1        4         1           1           n     2     1      byte
            #
            #  PackLen : CommandID + CommandType + Data + Crc + Endbit
            # ///////////////////////////////////////////////////////////////////////////////
            nIdx = ATS_HEADER_BYTE_LENGTH

            nPackLen = ((arData[ATS_LEN1_BYTE_INDEX] << 24) & 0xff000000)
            nPackLen |= ((arData[ATS_LEN2_BYTE_INDEX] << 16) & 0xff0000)
            nPackLen |= ((arData[ATS_LEN3_BYTE_INDEX] << 8) & 0xff00)
            nPackLen |= (arData[ATS_LEN4_BYTE_INDEX] & 0xff)
            nIdx += 4

            if nPackLen + 6 == nDataLen:
                nCommandID = arData[nIdx]
                nIdx += 1
                if nCommandID == ATS_CMD_ID:
                    nCommandType = arData[nIdx]
                    nIdx += 1
                    crcData = arData[nIdx - 2:(nDataLen - ATS_TAIL_BYTE_LENGTH)]
                    nIdx += (nPackLen - ATS_TAIL_BYTE_LENGTH - 2)
                    nCRC = ((arData[nIdx] << 8) & 0xff00)
                    nCRC |= (arData[nIdx + 1] & 0xff)
                    nIdx += 2
                    nCalCrc = self.parent.CalcCRC16(crcData, len(crcData))

                    if nCRC == nCalCrc:
                        if nCommandType == eATS_CMD_CODE_INFO_ACK_VERSION:
                            # print ("Receive : eATS_CMD_CODE_INFO_ACK_VERSION")
                            self.ResponseSystemVersion(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_ST_ACKNOWLEDGE:
                            # print ("Receive : eATS_CMD_CODE_ST_ACKNOWLEDGE")
                            self.ProcessResponseAcknowledge(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_ST_VOLTAGE_STATUS:
                            # print ("Receive : eATS_CMD_CODE_ST_VOLTAGE_STATUS")
                            self.ResponseVoltagePeriodicallyNoti(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_ST_CURRENT_STATUS:
                            # print ("Receive : eATS_CMD_CODE_ST_CURRENT_STATUS")
                            self.ResponseCurrentPeriodicallyNoti(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_LCD_STATUS:
                            # print ("Receive : eATS_CMD_CODE_INFO_ACK_LCD_STATUS")
                            self.ResponseLCDStatus(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_LCDCHANGE_STATUS:
                            # print ("Receive : eATS_CMD_CODE_INFO_ACK_LCDCHANGE_STATUS")
                            self.ResponseLCDStatusAndIllumiSensorValuePeriodicallyNoti(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_AUDIO_DATA_STATUS:
                            # print ("Receive : eATS_CMD_CODE_AUDIO_DATA_STATUS")
                            self.ResponseAudioStatusAndADCValuePeriodicallyNoti(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_AUDIO_COMPARE_STATUS:
                            # print ("Receive : eATS_CMD_CODE_INFO_ACK_AUDIO_COMPARE_STATUS")
                            self.ResponseAudioFrequency(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_AUDIO_ONOFF_STATUS:
                            # print ("Receive : eATS_CMD_CODE_INFO_ACK_AUDIO_ONOFF_STATUS")
                            self.ResponseAudioStatusAndADCValue(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_ST_AUDIO_STATUS:
                            # print ("Receive : eATS_CMD_CODE_ST_AUDIO_STATUS")
                            self.ResponseAudioStatusPeriodicallyNoti(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_ATS_RELAY_STATUS:
                            # print ("Receive : eATS_CMD_CODE_INFO_ACK_ATS_RELAY_STATUS")
                            self.ResponseRelayInitialStatus(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_AUTOCALIBRATION:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_AUTOCALIBRATION")
                            self.ResponseAutoCalibration(crcData, len(crcData))
                            # ProcessResponseAutoCalibration( &pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_CAN1_RX_DATA:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_CAN1_RX_DATA")
                            # ProcessResponseCAN1RXReceive( (BYTE*)&pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_CAN2_RX_DATA:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_CAN2_RX_DATA")
                            # ProcessResponseCAN2RXReceive( (BYTE*)&pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_CAN1_TX_DATA:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_CAN1_TX_DATA")
                            # ProcessResponseCAN1TXReceive( (BYTE*)&pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_CAN2_TX_DATA:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_CAN2_TX_DATA")
                            # ProcessResponseCAN2TXReceive( (BYTE*)&pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_TELLTALE_LED:
                            # print ("Receive : eATS_CMD_CODE_INFO_ACK_TELLTALE_LED")
                            self.ResponseTelltaleStatus(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_LED_PULSE:
                            # print ("Receive : eATS_CMD_CODE_INFO_ACK_LED_PULSE")
                            self.ResponseLEDStatus(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_RES_MT4N_CURRENT_CONTROL:
                            # print ("Receive : eATS_CMD_CODE_INFO_RES_MT4N_CURRENT_CONTROL")
                            self.ResponseExtCurrentPeriodicallyNoti(crcData, len(crcData))
                        # Option ------------------------------------------------------------------------------------------------------------
                        # 1 Relay
                        elif nCommandType == eATS_CMD_CODE_INFO_STATUS_RELAY:
                            print ("Receive : eATS_CMD_CODE_INFO_STATUS_RELAY")
                            # self.ResponseRelayStatus(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_USERPIN1_STATUS:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_USERPIN1_STATUS")
                            # ProcessResponseUserPin1Status( &pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_VOICE_FILE_INFO:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_VOICE_FILE_INFO")
                            # ProcessResponseVoiceFileInfo( &pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_VOICE_FILE_LIST:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_VOICE_FILE_LIST")
                            # ProcessResponseVoiceFileList( &pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_SHIELD_BOX_CONTROL:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_SHIELD_BOX_CONTROL")
                            # ProcessResponseShildBoxStatus( &pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_SEND_CSV_DATA:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_SEND_CSV_DATA")
                            # ProcessResponseSendCsvData( &pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_VDT_LIST_CONTROL:
                            print ("Receive : eATS_CMD_CODE_INFO_ACK_VDT_LIST_CONTROL")
                            # ProcessResponseVdtFileInfo( &pBuffer[ATS_LENGTH_BYTE_LENGTH+2], nPackLen - ATS_TAIL_BYTE_LENGTH - 2 )
                        elif nCommandType == eATS_CMD_CODE_INFO_RES_KEY_OUT_VOLTAGE_CONTROL:
                            # print ("Receive : eATS_CMD_CODE_INFO_RES_KEY_OUT_VOLTAGE_CONTROL")
                            self.ResponseKeyOutVoltagePeriodicallyNoti(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_LIGHTSENSOR_RESULT:
                            self.ResponseLightSensor(crcData, len(crcData))
                        elif nCommandType == eATS_CMD_CODE_INFO_ACK_COMMON_RELAY_NAME_SET:
                            self.ResponseRelaySet(crcData, len(crcData))
                        elif nCommandType == Response_File_To_SD_Card:
                            self.ResponseFileToSDCard(crcData, len(crcData))
                        else:
                            if DEBUG_ENABLE == 1:
                                print("Receive : Unknown Command", (hex(nCommandType)))
                    else:
                        if DEBUG_ENABLE == 1:
                            print ("CRC Error!")
            else:
                if DEBUG_ENABLE == 1:
                    print("Mismatch nDataLen/nPacketLen", nDataLen, (nPackLen + 6))
        else:
            if DEBUG_ENABLE == 1:
                print ("Invalid Data Error!")

    @staticmethod
    def ResponseSystemVersion(arData, nLen):
        if 2 + 5 <= nLen:
            strData = []

            strData.append(arData[2])
            strData.append(arData[2 + 1])
            strData.append(arData[2 + 2])
            strData.append(arData[2 + 3])
            strData.append(arData[2 + 4])
            print("response version")
            strVal = "%c%c%c%c%c" % (strData[0], strData[1], strData[2], strData[3], strData[4])
            if DEBUG_ENABLE == 1:
                print("System Version :", strVal)

    def ProcessResponseAcknowledge(self, arData, nLen):
        if 2 + 5 < nLen:
            nResult = arData[2]
            if nResult == 0x00:
                nCommandType = arData[3]
                if nCommandType == eATS_CMD_CODE_CMD_VR:
                    relayValue = arData[5]
                    print("eATS_CMD_CODE_CMD_VR", arData)
                elif nCommandType == eATS_CMD_CODE_CMD_RELAY:
                    relayType = arData[4]
                    relayValue = arData[5]
                    if self.parent.nRelayCfg == 0:  # RELAY_STATUS_FIX(OLD BENCH)
                        if relayType == eATS_RELAY_ILLUMINATION_PLUS:
                            # print ("eATS_RELAY_ILLUMINATION_PLUS"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_ILL_PLUS': relayValue})
                            pass
                        elif relayType == eATS_RELAY_ACC:
                            # print ("eATS_RELAY_ACC"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_ACC': relayValue})
                            pass
                        elif relayType == eATS_RELAY_ALT:
                            # print ("eATS_RELAY_ALT"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_ALT': relayValue})
                            pass
                        elif relayType == eATS_RELAY_B_PLUS:
                            # print ("eATS_RELAY_B_PLUS"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_BPLUS': relayValue})
                            event.set()
                            event.clear()
                            pass
                        elif relayType == eATS_RELAY_REAR_CAMERA:
                            # print ("eATS_RELAY_REAR_CAMERA"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_REVERSE_GEAR': relayValue})
                            pass
                        elif relayType == eATS_RELAY_PARKING:
                            # print ("eATS_RELAY_PARKING"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_PARKING': relayValue})
                            pass
                        elif relayType == eATS_RELAY_DOOR:
                            # print ("eATS_RELAY_DOOR"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_DOOR': relayValue})
                            pass
                        elif relayType == eATS_RELAY_AUTOLIGHT:
                            # print ("eATS_RELAY_AUTOLIGHT"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_AUTO_LIGHT': relayValue})
                            pass
                        elif relayType == eATS_RELAY_AUX:
                            # print ("eATS_RELAY_AUX"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'EXT_AUX': relayValue})
                            pass
                        elif relayType == eATS_RELAY_IGN:
                            # print ("eATS_RELAY_IGN"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_IGN': relayValue})
                            pass
                        elif relayType == eATS_RELAY_USB1:
                            # print ("eATS_RELAY_USB1"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'EXT_USB1': relayValue})
                            pass
                        elif relayType == eATS_RELAY_USB2:
                            # print ("eATS_RELAY_USB2"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'EXT_USB2': relayValue})
                            pass
                        elif relayType == eATS_RELAY_ALARM_DETECT:
                            # print ("eATS_RELAY_ALARM_DETECT"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_ALARM_DETECT': relayValue})
                            pass
                        elif relayType == eATS_RELAY_KEYLESS_BOOT:
                            # print ("eATS_RELAY_KEYLESS_BOOT"), (arData)
                            # if self.relayQueue != None:
                            #    self.relayQueue.put_nowait({'SIGNAL_KEYLESS_BOOT': relayValue})
                            pass
                    else:  # RELAY_STATUS_COMMON
                        if relayType == COMMON_SWITCH_1:
                            event.set()
                            event.clear()
                    # if self.relayQueue != None:
                    #    print ("self.relayQueue"), (self.relayQueue.get_nowait())
                elif nCommandType == eATS_CMD_CODE_CMD_VOLTAGE:
                    event.set()
                    event.clear()
                elif (nCommandType == eATS_CMD_CODE_CMD_THREE_BUTTON) or (
                        nCommandType == eATS_CMD_CODE_CMD_THREE_BUTTON_REAR) or (
                        nCommandType == eATS_CMD_CODE_CMD_NGT_TWO_BUTTON):
                    event.set()
                    event.clear()
            elif nResult == 0xAC:
                nCommandType = arData[3]
                if nCommandType == eATS_CMD_CODE_CMD_THREE_BUTTON:
                    event.set()
                    event.clear()
                elif nCommandType == eATS_CMD_CODE_CMD_THREE_BUTTON_REAR:
                    event.set()
                    event.clear()
                elif nCommandType == eATS_CMD_CODE_CMD_NGT_TWO_BUTTON:
                    event.set()
                    event.clear()

    @staticmethod
    def ResponseRelayInitialStatus(arData, nLen):
        # 0xA0 -> Noti 0xA1
        if 2 + 14 <= nLen:
            nIllPlus = arData[2]
            nIllMinus = arData[2 + 1]
            nACC = arData[2 + 2]
            nALT = arData[2 + 3]
            nBatt = arData[2 + 4]
            nRear = arData[2 + 5]
            nParking = arData[2 + 6]
            nDoor = arData[2 + 7]
            nAutolight = arData[2 + 8]
            nAux = arData[2 + 9]
            nMic = arData[2 + 10]
            nAnt = arData[2 + 11]
            nUsb1 = arData[2 + 12]
            nIpod = arData[2 + 13]

            if DEBUG_ENABLE == 1:
                print ("Relay Initial Status :")
                print("      IllPlus    =", nIllPlus)
                print("      nIllMinus  =", nIllMinus)
                print("      nACC       =", nACC)
                print("      nALT       =", nALT)
                print("      nBatt      =", nBatt)
                print("      nRear      =", nRear)
                print("      nParking   =", nParking)
                print("      nDoor      =", nDoor)
                print("      nAutolight =", nAutolight)
                print("      nAux       =", nAux)
                print("      nMic       =", nMic)
                print("      nAnt       =", nAnt)
                print("      nUsb1      =", nUsb1)
                print("      nIpod      =", nIpod)

    def ResponseVoltagePeriodicallyNoti(self, arData, nLen):
        if 2 + 5 <= nLen:
            strData = []

            strData.append(arData[2])
            strData.append(arData[2 + 1])
            strData.append(arData[2 + 2])
            strData.append(arData[2 + 3])
            strData.append(arData[2 + 4])

            strVal = "%c%c%c%c%c" % (strData[0], strData[1], strData[2], strData[3], strData[4])

            self.parent.nVoltageVal = float(strVal)

            # print ("Voltage :"), (strVal)

    def ResponseCurrentPeriodicallyNoti(self, arData, nLen):
        if 2 + 5 <= nLen:
            strData = []

            strData.append(arData[2])
            strData.append(arData[2 + 1])
            strData.append(arData[2 + 2])
            strData.append(arData[2 + 3])
            strData.append(arData[2 + 4])

            if strData[4] == 0:
                strData[4] = '0'

            strVal = "%s" % str(strData[0]) + str(strData[1]) + str(strData[2]) + str(strData[3]) + str(strData[4])

            self.parent.nCurrentVal = float(strVal)

            # print ("Current :"), (strVal)

    def ResponseLCDStatus(self, arData, nLen):
        # 0x62 -> Noti 0x63
        if 2 + 1 <= nLen:
            nData = arData[2]

            self.parent.bLCD = nData
            self.parent.bLCDUpdated = 1

            if DEBUG_ENABLE == 1:
                print("LCD Status :", nData)

        """
    def ResponseLCDIllumiSensorValue(self, arData, nLen):
        #0x12 -> Noti 0xE0
        """

    def ResponseLCDStatusAndIllumiSensorValuePeriodicallyNoti(self, arData, nLen):
        # 0x17 -> Noti 0xE9
        if 2 + 3 <= nLen:
            nLCDOnOff = arData[2]
            nLCDValue = ((arData[3] << 8) & 0xff00)
            nLCDValue |= arData[4] & 0xff

            self.parent.SetLCDValue(nLCDValue)

        """
            print "LCD Status And IllumiSensor Value :",; print nLCDOnOff, nLCDValue
        else:
            print "LCD Status And IllumiSensor Value : Error"
        """

    @staticmethod
    def ResponseAudioFrequency(arData, nLen):
        # 0x64 -> Noti 0x65
        if 2 + 2 <= nLen:
            nFreq = (arData[2] << 8) & 0xff00
            nFreq |= (arData[3]) & 0xff
            if DEBUG_ENABLE == 1:
                print("Audio Frequency :", nFreq)

    def ResponseAudioStatusAndADCValue(self, arData, nLen):
        # 0x66 -> Noti 0x67
        if 2 + 3 <= nLen:
            nAudioOnOff = arData[2] & 0xff
            nAudioADCValue = (arData[3] << 8) & 0xff00
            nAudioADCValue |= (arData[4]) & 0xff

            self.parent.bSound = nAudioOnOff
            self.parent.bSoundUpdated = 1

            if DEBUG_ENABLE == 1:
                print("Audio Status And ADC Value(1) :", nAudioOnOff, nAudioADCValue)

    @staticmethod
    def ResponseAudioStatusPeriodicallyNoti(arData, nLen):
        # 0x13 -> Noti 0xE1
        if 2 + 1 <= nLen:
            nAudioOnOff = arData[2]
            if DEBUG_ENABLE == 1:
                print("Audio Status :", nAudioOnOff)

    @staticmethod
    def ResponseAudioStatusAndADCValuePeriodicallyNoti(arData, nLen):
        # 0x1E -> Noti 0xEB
        if 2 + 3 <= nLen:
            nAudioOnOff = arData[2] & 0xff
            nAudioADCValue = (arData[3] << 8) & 0xff00
            nAudioADCValue |= (arData[4]) & 0xff
            # print ("Audio Status And ADC Value(2) :"), (nAudioOnOff), (nAudioADCValue)

    def ResponseTelltaleStatus(self, arData, nLen):
        if 2 + 2 <= nLen:
            strData = []

            strData.append(arData[2 + 0])
            strData.append(arData[2 + 1])

            self.parent.nTelltaleRed = strData[0]
            self.parent.nTelltaleGreen = strData[1]

            if 2 + 3 <= nLen:  # Telltale blue led
                strData.append(arData[2 + 2])
                self.parent.nTelltaleBlue = strData[2]
            else:
                self.parent.nTelltaleBlue = None

            if 2 + 4 <= nLen:  # Telltale TCU4
                self.parent.nECallActive = arData[2 + 0]
                self.parent.nECallBacklight = arData[2 + 1]
                self.parent.nBCallActive = arData[2 + 2]
                self.parent.nBCallBacklight = arData[2 + 3]

            # print ("Green : "), (self.parent.nTelltaleGreen), ("Red : "), (self.parent.nTelltaleRed), ("Blue : "), (self.parent.nTelltaleBlue)

    def ResponseLEDStatus(self, arData, nLen):
        nByte = 16
        # print nLen
        if 2 + nByte <= nLen:
            nData = []

            for b in range(nByte):
                nData.append(arData[2 + b])

            # print nData
            self.parent.nONCountG0 = (((nData[0] << 8) & 0xff00) | (nData[1] & 0xff))
            self.parent.nOFFCountG0 = (((nData[2] << 8) & 0xff00) | (nData[3] & 0xff))
            self.parent.nONCountG1 = (((nData[4] << 8) & 0xff00) | (nData[5] & 0xff))
            self.parent.nOFFCountG1 = (((nData[6] << 8) & 0xff00) | (nData[7] & 0xff))
            self.parent.nONCountG2 = (((nData[8] << 8) & 0xff00) | (nData[9] & 0xff))
            self.parent.nOFFCountG2 = (((nData[10] << 8) & 0xff00) | (nData[11] & 0xff))
            self.parent.nONCountF4 = (((nData[12] << 8) & 0xff00) | (nData[13] & 0xff))
            self.parent.nOFFCountF4 = (((nData[14] << 8) & 0xff00) | (nData[15] & 0xff))

        # print "G0 - ON : {}, OFF : {}".format(self.parent.nONCountG0, self.parent.nOFFCountG0)
        # print "G1 - ON : {}, OFF : {}".format(self.parent.nONCountG1, self.parent.nOFFCountG1)
        # print "G2 - ON : {}, OFF : {}".format(self.parent.nONCountG2, self.parent.nOFFCountG2)
        # print "F4 - ON : {}, OFF : {}".format(self.parent.nONCountF4, self.parent.nOFFCountF4)

    def ResponseExtCurrentPeriodicallyNoti(self, arData, nLen):
        if 2 + 4 <= nLen:
            nData = []

            nData.append(arData[2])
            nData.append(arData[2 + 1])
            nData.append(arData[2 + 2])
            nData.append(arData[2 + 3])

            # strVal = "%s%s" % (hex(nData[0])[2:], hex(nData[1])[2:])
            dot_loc = nData[3]
            strVal = 0
            if dot_loc == 1 or dot_loc == 0:  # Milli Ampere Current
                strVal = '0.' + str("%04d" % (((nData[0] << 8) & 0xff00) | (nData[1] & 0xff)))
            else:  # Ampere Current
                digit = str("%04d" % (((nData[0] << 8) & 0xff00) | (nData[1] & 0xff)))
                strVal = digit[:-dot_loc] + '.' + digit[-dot_loc:]

            reScale = 10. ** (len(strVal) - strVal.find('.') - 1)
            floatVal = float(strVal)
            intVal = int(floatVal * reScale)  # 16 bit , -32768 ~ 32767

            if int(hex(intVal & 0x8000), 16):  # sign bit
                c = hex((~intVal & 0xffff) + 0x0001)
                self.parent.nExtCurrentVal = -int(c, 16) / reScale
            else:
                self.parent.nExtCurrentVal = floatVal

            # dot = ((nData[2] << 8) & 0xff00) | (nData[3] & 0xff)
            # strFlotVal = strVal[:-dot] + '.' + strVal[-dot:]

            # self.parent.nExtCurrentVal = float(strVal)
            self.parent.listExtCurrent.append(self.parent.nExtCurrentVal)

            # print ("Ext Current :"), (strVal)

    def ResponseKeyOutVoltagePeriodicallyNoti(self, arData, nLen):
        if 2 + 2 <= nLen:
            nData = []

            nData.append(arData[2])
            nData.append(arData[2 + 1])

            vol = (((nData[0] << 8) & 0xff00) | (nData[1] & 0xff))
            qVol = vol / 1000
            rVol = vol % 1000
            strRVol = str("%03d" % rVol)
            strVol = str(qVol) + '.' + strRVol
            fVol = float(strVol)
            self.parent.listKeyOutVoltage.append(fVol)
            # print strVol

    def ResponseLightSensor(self, arData, nLen):
        status = arData[2]
        if status == 0x01:
            self.parent.nlsBlackVal = (arData[3] << 24) & 0xff000000
            self.parent.nlsBlackVal |= (arData[4] << 16) & 0xff0000
            self.parent.nlsBlackVal |= (arData[5] << 8) & 0xff00
            self.parent.nlsBlackVal |= (arData[6]) & 0xff

            self.parent.nlsWhiteVal = (arData[7] << 24) & 0xff000000
            self.parent.nlsWhiteVal |= (arData[8] << 16) & 0xff0000
            self.parent.nlsWhiteVal |= (arData[9] << 8) & 0xff00
            self.parent.nlsWhiteVal |= (arData[10]) & 0xff

        elif status == 0x02:
            self.parent.nlsBlackVal = (arData[3] << 24) & 0xff000000
            self.parent.nlsBlackVal |= (arData[4] << 16) & 0xff0000
            self.parent.nlsBlackVal |= (arData[5] << 8) & 0xff00
            self.parent.nlsBlackVal |= (arData[6]) & 0xff

            self.parent.nlsWhiteVal = (arData[7] << 24) & 0xff000000
            self.parent.nlsWhiteVal |= (arData[8] << 16) & 0xff0000
            self.parent.nlsWhiteVal |= (arData[9] << 8) & 0xff00
            self.parent.nlsWhiteVal |= (arData[10]) & 0xff
        else:
            self.parent.nlsResult = arData[3]

    def ResponseRelaySet(self, arData, nLen):
        # print(arData)

        if arData[0] == ATS_CMD_ID and arData[1] == eATS_CMD_CODE_INFO_ACK_COMMON_RELAY_NAME_SET:
            if COMMON_SWITCH_0 <= arData[
                2] <= COMMON_SWITCH_MAX:  # Request�� relay num�� ��Ȯ�ϰ� check�� �ʿ� ���� ��ü relay num ����(0~30)�ȿ� ACK�� ���� ���� �������� �����Ѵ�.
                self.parent.bRelaySet = 1

    def ResponseAutoCalibration(self, arData, nLen):
        if 2 + 2 <= nLen:
            nCalibtratonValue = (arData[2] << 8) & 0xff00
            nCalibtratonValue |= arData[3] & 0xff

            print("Auto Calibration Value :", nCalibtratonValue)

    def ResponseFileToSDCard(self, arData, nLen):
        status = arData[2]
        error = arData[3]
        self.parent.nSD_errorCount = error
        event_sd.set()
        event_sd.clear()