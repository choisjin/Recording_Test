import time
import can
import threading
from robot.api.deco import keyword

class CAN:
    def __init__(self):
        self._channel = None
        self._periodic_tx_list = []
        self._bus = None
        self._rx_thread = None
        self._interface = None
        self._logpath = ''

    class read_thread(threading.Thread):
        def __init__(self, bus, logpath):
            threading.Thread.__init__(self)
            self._exit = threading.Event()
            self._bus = bus
            self._log_path = logpath
            self._status = False

        def run(self) -> None:
            while not self._exit.is_set():
                try:
                    if self._status and self._bus is not None:
                        recive_msg = self._bus.recv()
                        #print(recive_msg)
                        log_file = open(self._log_path, 'a', encoding='utf-8')

                        if recive_msg.is_rx:
                            rx_tx = 'Rx'
                        else:
                            rx_tx = 'Tx'

                        data = []
                        for bit in recive_msg.data:
                            data.append(f'{bit:02x}'.upper())

                        line_format = '%-15s %s %-12s %s %s %s %s\n'
                        #print(line_format %(str(recive_msg.timestamp), '1', hex(recive_msg.arbitration_id)[2:].upper(), rx_tx, 'd', str(len(recive_msg)), ' '.join(data)))
                        log_file.write(line_format % (str(recive_msg.timestamp), '1', hex(recive_msg.arbitration_id)[2:].upper(), rx_tx, 'd', str(len(recive_msg)), ' '.join(data)))
                        log_file.close()
                except:
                    #import traceback
                    #traceback.print_exc()
                    pass

        def stop(self):
            self._exit.set()

    @keyword('Start Log Save')
    def start_log_save(self, logpath):
        self._rx_thread = self.read_thread(self._bus, logpath)
        self._rx_thread._status = True
        self._rx_thread.start()

    @keyword('Stop Log Save')
    def stop_log_save(self):
        self._rx_thread._status = False
        self._rx_thread.stop()
        self._rx_thread.join()

    @keyword('Connect Device')
    def Bus(self, **kwargs):
        print(kwargs)
        if 'interface' in kwargs:
            self._interface = kwargs['interface']

        if 'channel' in kwargs:
            self._channel = kwargs['channel']

        self._bus = can.Bus(**kwargs)
        return self._bus

    @keyword('Disconnect Device')
    def shutdown(self):
        if self._rx_thread is not None:
            if self._rx_thread._status:
                self.stop_log_save()

        if self._bus is not None:
            try:
                self._bus.shutdown()
            except:
                pass
            self._bus = None


    def get_device_number(self):
        if self._bus is not None:
            return self._bus.get_device_number()

    def status(self):
        if self._bus is not None:
            return self._bus.status()

    def status_is_ok(self):
        if self._bus is not None:
            return self._bus.status_is_ok()

    def status_string(self):
        if self._bus is not None:
            return self._bus.status_string()

    @keyword('Stop All CAN Message')
    def stop_all_periodic_tasks(self):
        #if self._bus is not None:
        #    return self._bus.stop_all_periodic_tasks(True)
        for msg in self._periodic_tx_list:
            msg.stop()

    def flush_tx_buffer(self):
        if self._bus is not None:
            return self._bus.flush_tx_buffer()

    @keyword('Send CAN Message')
    def send_msg(self, message_id, cycle_time, can_message, bus_channel, message_type='FD'):
        _message_id = int(message_id, 16)

        if message_type == 'FD':
            _is_fd = True
        else:
            _is_fd = False

        if _message_id & 0x10000000 == 0:
            _is_extended_id = False
        else:
            _is_extended_id = True

        _can_message = [int(n, 16) for n in can_message.split()]
        _cycle_time = int(cycle_time)

        if _cycle_time > 0:
            if self._bus is not None:
                msg = self._bus.send_periodic(
                    can.Message(arbitration_id=_message_id,
                                data=_can_message,
                                is_fd=_is_fd,
                                dlc=len(_can_message),
                                channel=bus_channel,
                                is_extended_id=_is_extended_id,
                                is_rx=False), _cycle_time/1000)

                self._periodic_tx_list.append(msg)

        else:
            if self._bus is not None:
                self._bus.send(
                    can.Message(arbitration_id=_message_id,
                                data=_can_message,
                                is_fd=_is_fd,
                                dlc=len(_can_message),
                                channel=bus_channel,
                                is_extended_id=_is_extended_id,
                                is_rx=False))

if __name__ == "__main__":
    pcan = CAN()

    pcan.Bus(interface='pcan', channel='PCAN_USBBUS1', fd='False',
            f_clock='80000000',nom_brp='2',nom_tseg1='63',nom_tseg2='16',nom_sjw='16', bitrate='500000')


    pcan.send_msg(message_id='0xB6',
                  can_message='0x00 0x00 0x04 0x00 0x00 0x00 0x00 0x00',
                  message_type='CC',
                  cycle_time='100', 
                  bus_channel='PCAN_USBBUS1')


    pcan.send_msg(message_id='0x510',
                  can_message='0x40 0x10 0x10 0x00 0x00 0x02 0x01 0x00',
                  message_type='CC',
                  cycle_time='1000',
                  bus_channel='PCAN_USBBUS1')

    time.sleep(10)

    #print(_can.stop_all_periodic_tasks())
    #print(_can.flush_tx_buffer())
    #send

    pcan.shutdown()

