import os
import sys
import threading
import time


#FILESIZE = 9*1024*1024
FILESIZE = 2*1024*1024

class infoTable:
    def __init__(self):
        self.nCANTransceiverBuadrate = [
            100,
            500,
            1000,
            33,
            33,
            500,
            250,
            125,
            2000,
            2000]

        self.strCANTransceiverName = [
            "TJA1055",
            "TJA1043",
            "TJA1051",
            "MC33897",
            "NCV7356",
            "TJA1145", 
            "TJA1051",
            "TJA1043_125K",
            "TJA1043_FD",
            "TJA1051_FD"]

        self.CAN_TRANSCEIVER_TJA1055    = 0x00 # TJA1055, 100kbps, (EU2.0,KOECN,PQ)
        self.CAN_TRANSCEIVER_TJA1043    = 0x01 # TJA1043, 500kbps, 2014,MQB,CID
        self.CAN_TRANSCEIVER_TJA1051    = 0x02 # TJA1051, 1Mbps, GEN10
        self.CAN_TRANSCEIVER_MC33897    = 0x03 # MC33897, 33.33kbps, BYOM
        self.CAN_TRANSCEIVER_NCV7356    = 0x04 # NCV7356, 33.33kbps, GEN10
        self.CAN_TRANSCEIVER_TJA1145    = 0x05 # TJA1245, 500kbps
        self.CAN_TRANSCEIVER_TJA1051_T  = 0x06 # TJA1051, 250kbps
        self.CAN_TRANSCEIVER_TJA1043_125K = 0x07 # TJA1043, 125kbps
        self.CAN_TRANSCEIVER_TJA1043_FD = 0x09 # TJA1043, 500kbps, 2Mbps FD CAN 2014,MQB,CID 
        self.CAN_TRANSCEIVER_TJA1051_FD = 0x0A # TJA1051, 1Mbps, 2Mbps FD CAN  GEN10 
        self.CAN_TRANSCEIVER_NONE       = 0x0F # MAX

        # Command Types defined 
        self.PRTC_CMD_CAN_VERSION           = 0x02
        self.PRTC_CMD_CAN_GET_BAUDRATE      = 0x04
        self.PRTC_CMD_CAN_CH1_TX_MONITOR    = 0x05
        self.PRTC_CMD_CAN_CH1_RX_MONITOR    = 0x06
        self.PRTC_CMD_CAN_CH2_TX_MONITOR    = 0x07
        self.PRTC_CMD_CAN_CH2_RX_MONITOR    = 0x08
        self.PRTC_CMD_CAN_SET_BOARD_NUMBER  = 0x09
        self.PRTC_CMD_CAN_CH1_TX_ERROR      = 0x1E
        self.PRTC_CMD_CAN_CH2_TX_ERROR      = 0x1F

    def getBaudrateIndex(self, baudrate):
        if baudrate == self.CAN_TRANSCEIVER_TJA1055:
            return 0
        elif baudrate == self.CAN_TRANSCEIVER_TJA1043:
            return 1
        elif baudrate == self.CAN_TRANSCEIVER_TJA1051:
            return 2
        elif baudrate == self.CAN_TRANSCEIVER_MC33897:
            return 3
        elif baudrate == self.CAN_TRANSCEIVER_NCV7356:
            return 4
        elif baudrate == self.CAN_TRANSCEIVER_TJA1145:
            return 5
        elif baudrate == self.CAN_TRANSCEIVER_TJA1051_T:
            return 6
        elif baudrate == self.CAN_TRANSCEIVER_TJA1043_125K:
            return 7
        elif baudrate == self.CAN_TRANSCEIVER_TJA1043_FD:
            return 8
        elif baudrate == self.CAN_TRANSCEIVER_TJA1051_FD:
            return 9
        return 100 # This cause out of index exception error

class procResponse:

    def __init__(self, parent=None):
        self.parent = parent
        self.nTransceiver0Baudrate = 0
        self.nTransceiver1Baudrate = 0
        self.info = infoTable()
        self.list_log = []
        self.bLogging = False
        self.bSaveFile = False
        self.lock_save = threading.Lock()

    def proc(self, commandType, buff, len):
        if commandType == self.info.PRTC_CMD_CAN_VERSION:
            self.procResponseReqVersion(buff, len)
        elif commandType == self.info.PRTC_CMD_CAN_GET_BAUDRATE:
            self.procResponseGetBaudrate(buff, len)
        elif commandType == self.info.PRTC_CMD_CAN_SET_BOARD_NUMBER:
            self.procResponseSetDeviceNumber(buff, len)
        elif commandType == self.info.PRTC_CMD_CAN_CH1_TX_MONITOR:
            self.procResponseCANReceive(buff, len, 1, 'Tx', '')
            self.parent.bTxMonitor = True
        elif commandType == self.info.PRTC_CMD_CAN_CH1_RX_MONITOR:
            self.procResponseCANReceive(buff, len, 1, 'Rx', '')
        elif commandType == self.info.PRTC_CMD_CAN_CH2_TX_MONITOR:
            self.procResponseCANReceive(buff, len, 2, 'Tx', '')
            self.parent.bTxMonitor = True
        elif commandType == self.info.PRTC_CMD_CAN_CH2_RX_MONITOR:
            self.procResponseCANReceive(buff, len, 2, 'Rx', '')
        elif commandType == self.info.PRTC_CMD_CAN_CH1_TX_ERROR:
            self.procResponseCANReceive(buff, len, 1, 'Tx', 'Error')
            self.parent.bTxMonitor = True
        elif commandType == self.info.PRTC_CMD_CAN_CH2_TX_ERROR:
            self.procResponseCANReceive(buff, len, 2, 'Tx', 'Error')
            self.parent.bTxMonitor = True
        #else:
            #print('[W] This CommandType({}) is not processed here'.format(commandType))

    def procResponseGetBaudrate(self, buff, len):
        #print('[I] procResponseGetBaudrate called')
        nTransceiver00Type = self.info.getBaudrateIndex(buff[0])
        nTransceiver01Type = self.info.getBaudrateIndex(buff[1])
        if (self.info.CAN_TRANSCEIVER_TJA1055 <= nTransceiver00Type) and (nTransceiver00Type < self.info.CAN_TRANSCEIVER_NONE):
            self.nTransceiver0Baudrate = self.info.nCANTransceiverBuadrate[nTransceiver00Type]
            #print('[I] Transceiver#0 Name : {}'.format(self.info.strCANTransceiverName[nTransceiver00Type]))
            if self.parent.initDone == False:
                self.parent._setDeviceNumber(self.parent.deviceNumber)
        #else:
        #    print('[W] Transceiver#0 Unknown')

        if (self.info.CAN_TRANSCEIVER_TJA1055 <= nTransceiver01Type) and (nTransceiver01Type < self.info.CAN_TRANSCEIVER_NONE):
            self.nTransceiver1Baudrate = self.info.nCANTransceiverBuadrate[nTransceiver01Type]
        #    print('[I] Transceiver#1 Name : {}'.format(self.info.strCANTransceiverName[nTransceiver01Type]))
        #else:
        #    print('[W] Transceiver#1 Unknown')

    def procResponseSetDeviceNumber(self, buff, len):
        #print('[I] procResponseSetDeviceNumber called')
        nResDevNum = buff[0]
        self.parent.initDone = True
        self.parent._setBaudrate(self.parent.isCANFDSupport_CH1, self.parent.isCANFDSupport_CH2)
        self.parent._reqVersion()
        #print('[I] Device Number({}) Received'.format(nResDevNum))

    def procResponseReqVersion(self, buff, len):
        self.parent.event.set()
        self.parent.event.clear()
        #print('[I] procResponseReqVersion called')
        verLst = list(buff[0:len]) # Get a data parts.
        self.parent.CANatVersion = ''.join(chr(c) for c in verLst)
        #print('[I] CANat Version : {}'.format(self.parent.CANatVersion))

    def procResponseCANReceive(self, buff, len, nCH, direction, strError):
        tick = 0
        tick  = buff[0] << 56
        tick |= buff[1] << 48
        tick |= buff[2] << 40
        tick |= buff[3] << 32
        tick |= buff[4] << 24
        tick |= buff[5] << 16
        tick |= buff[6] << 8
        tick |= buff[7]

        nMessageID  = buff[8] << 24
        nMessageID |= buff[9] << 16
        nMessageID |= buff[10] << 8
        nMessageID |= buff[11]

        nisEXTID = buff[12]
        nRTR     = buff[13]
        nDLC     = buff[14]
        nFMI     = buff[15]

        #print('[M] {} : {} {} {} {} {} {} {} {}'.format(hex(nMessageID), hex(buff[16]), hex(buff[17]), \
        #    hex(buff[18]), hex(buff[19]), hex(buff[20]), hex(buff[21]), hex(buff[22]), hex(buff[23])) )
        
        #print('0x{:X}:{:02X} {:02X} {:02X} {:02X} {:02X} {:02X} {:02X} {:02X}'.format(nMessageID, buff[16], buff[17], \
        #    buff[18], buff[19], buff[20], buff[21],buff[22], buff[23]) )
        

        if self.bLogging == True or self.bSaveFile == True:
            hexMessageID = '0x{:X}'.format(nMessageID)
            canData = ''
            for i in range(nDLC):
                canData += ' {:02X}'.format(buff[i+16])
            canData = canData.replace(' ','',1)
            
            if self.bLogging == True:
                self.list_log.append(hexMessageID + ':' + canData)
            
            self.lock_save.acquire()            
            if self.bSaveFile == True:
                lTime = time.localtime()
                strTime = '%4d%02d%02d %02d%02d%02d' % (
                lTime.tm_year, lTime.tm_mon, lTime.tm_mday, lTime.tm_hour, lTime.tm_min, lTime.tm_sec)
                self.parent.wr.writerow([strTime,tick,self.parent._port, nCH, direction, hexMessageID, nDLC, canData, strError])
                
                #self.parent.file_csv.seek(0, os.SEEK_END) #cursor move
                file_size = self.parent.file_csv.tell()
                if file_size > FILESIZE:
                    self.parent._CloseCSVFile()
                    self.parent._CreateCSVFile(self.parent.dirName)
                    
            self.lock_save.release()
            
            
            # print('{} {} {} {}'.format(canData, nCH, direction, bError))

        
        # nDLC : Message Length
        # nMessageID : Message ID
        # Message Data : In case of 8bytes, buff[16] ~ buff[23]

    
    def DelLog(self, idx):
        del self.list_log[:idx]
    
    def ClearLog(self):
        self.list_log.clear()
        
    def GetLog(self):
        return self.list_log
        
    def LoggingStart(self, bLogging):
        self.bLogging = bLogging
    
    def LogSaveStart(self):
        self.bSaveFile = True

    def LogSaveStop(self):
        self.lock_save.acquire()
        self.bSaveFile = False
        self.lock_save.release()
        
    
