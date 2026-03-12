import serial
import serial.tools.list_ports
from robot.api.deco import keyword
import time
import vxi11
import pyvisa
import socket
import sys
import traceback

class BENCH:

    def __init__(self):
        self.HOST = None
        self.PORT = None
        self.socket = None
        # self.data = data
        # self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # self.socket.connect((self.HOST, self.PORT))

    def __del__(self):
        self.socket_close()

    @keyword('Connect Socket')
    def socket_connect(self, host):
        try:
            self.HOST = host.strip()
            port = 8000
            self.PORT = port
        except:
            print(self.PORT)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.connect((host, port))
            self.socket.sendall(bytes('CONNECT' + "\n", "utf-8"))
            received = str(self.socket.recv(1024), "utf-8")
            print("Received: {}".format(received))
            # print("client address : {}".format(self.socket))

            print('*INFO* HOST : ' + host + ', PORT : ' + str(port) + ' is Connected')
        except:
            print('*ERROR* HOST : ' + host + ', PORT : ' + str(port) + ' connected fail')
            traceback.print_exc()

        return "CONNECTED"


    @keyword('Close Socket')
    def socket_close(self):
        received = ''
        try:
            if self.socket != None:
                self.socket.sendall(bytes('DISCONNECT' + "\n", "utf-8"))
                received = str(self.socket.recv(1024), "utf-8")
                print("Received: {}".format(received))
                # print("client address : {}".format(self.socket))
                self.socket.close()
                self.socket = None

                print('*INFO* HOST : ' + self.HOST + ', PORT : ' + str(self.PORT) + ' is Closed')
        except:
            print('*ERROR* HOST : ' + self.HOST + ', PORT : ' + str(self.PORT) + ' close fail')
            traceback.print_exc()

        return received

    @keyword('Transmit Data')
    def SendData(self, data='Test'):

        self.socket.sendall(bytes(data + "\n", "utf-8"))
        received = str(self.socket.recv(1024), "utf-8")

        print("Sent:     {}".format(data))
        print("Received: {}".format(received))

        if received == 'CURRENT_RUN':
            received = str(self.socket.recv(1024), "utf-8")
            print("Received: {}".format(received))

        return received

    @keyword('Check Current')
    def check_current(self, min_current, max_current, meas_time):
        recv = self.SendData('current-{}'.format(meas_time))

        valCurrent= float(recv.split(';')[1]) / 1000.0
        valCurrentList = str(recv.split(';')[2])

        if valCurrentList.find('[') > -1:
            valCurrentList = valCurrentList.replace('[', '')
        if valCurrentList.find(']') > -1:
            valCurrentList = valCurrentList.replace(']', '')

        temp = []
        for i in valCurrentList.split(','):
            try:
                temp.append(round(float(i) / 1000, 3))
            except:
                pass

        ret = 'fail'
        for t in temp:
            if t > float(min_current) and t < float(max_current):
                ret = 'pass'
                break
        return ret, str(round(valCurrent, 3)), temp