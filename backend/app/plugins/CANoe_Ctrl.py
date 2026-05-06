import datetime
import os
import glob
import time
import threading

from can import Message, Logger, Notifier, broadcastmanager
from can.interfaces.vector import VectorBus
from robot.api.deco import keyword
from isotp import Address, NotifierBasedCanStack, AddressingMode, BlockingSendFailure

broadcastmanager.USE_WINDOWS_EVENTS = False  # For Periodic msg


class CANoe_Ctrl:
    def __init__(self, device_info):
        self.bus = []
        dev_dict = eval(device_info)
        # print(dev_dict)
        for rp in range(0, len(dev_dict)):
            if dev_dict[rp]['is_fd'] is False:
                self.bus.append(VectorBus(channel=dev_dict[rp]['channel'], app_name=dev_dict[rp]['app_name'],
                                          bitrate=dev_dict[rp]['bitrate'], data_bitrate=dev_dict[rp]['data_bitrate'],
                                          fd=False, receive_own_messages=True))
            else:
                self.bus.append(VectorBus(channel=dev_dict[rp]['channel'], app_name=dev_dict[rp]['app_name'],
                                          bitrate=dev_dict[rp]['bitrate'], data_bitrate=dev_dict[rp]['data_bitrate'],
                                          sjw_abr=2, tseg1_abr=6, tseg2_abr=3, rx_queue_size=2 ** 16,
                                          receive_own_messages=True))
        self.CANoe_logger = None
        self.CANoe_logger_full = None
        self.CANoe_recv = None
        self.addr = None
        self.stack = None

        self.diag_ch = 0
        self.nTx_id = 0
        self.nRx_id = 0

        self.tester_present_thread = None
        self.tester_present_running = False

    @keyword('CANoe Full Log Save Start')
    def canoe_full_log_save_start(self, path, file_name):
        file_name_SD = file_name.split('.')
        print('canoe_full_log_save_start ',path)
        print('canoe_full_log_save_start ',file_name_SD[0])
        print('canoe_full_log_save_start ',file_name_SD[1])
        log_path = path + "/" + file_name_SD[0] + "_{0}.".format(datetime.datetime.now().strftime("%y%m%d_%H%M%S")) + file_name_SD[1]
        # log_path = path + file_name_SD[0] + "_{0}.".format(datetime.datetime.now().strftime("%y%m%d_%H%M%S")) + file_name_SD[1]
        print('canoe_full_log_save_start ',log_path)
        self.CANoe_logger_full = Logger(log_path)
        self.CANoe_recv = Notifier(self.bus, [self.CANoe_logger_full])
        return log_path

    @keyword('CANoe Full Log Save Stop')
    def canoe_full_log_save_stop(self):
        # Stop periodic tasks first
        for busNum in range(0, len(self.bus)):
            try:
                self.bus[busNum].stop_all_periodic_tasks()
            except:
                pass

        # Stop notifier BEFORE logger to prevent writing to closed file
        if self.CANoe_recv:
            try:
                self.CANoe_recv.stop()
            except:
                pass

        # Now safe to stop logger
        if self.CANoe_logger_full:
            try:
                self.CANoe_logger_full.stop()
                self.CANoe_logger_full = None
            except:
                pass

    @keyword('CANoe Log Save Start')
    def canoe_log_save_start(self, path, file_name):
        file_name_SD = file_name.split('.')
        log_path = path + file_name_SD[0] + "_{0}.".format(datetime.datetime.now().strftime("%y%m%d_%H%M%S")) + \
                   file_name_SD[1]
        self.CANoe_logger = Logger(log_path)
        self.CANoe_recv.add_listener(self.CANoe_logger)

    @keyword('CANoe Log Save Stop')
    def canoe_log_save_stop(self):
        if self.CANoe_logger:
            try:
                # Remove from notifier first
                if self.CANoe_recv:
                    self.CANoe_recv.remove_listener(self.CANoe_logger)
                time.sleep(0.1)
                # Then stop logger
                self.CANoe_logger.stop()
                self.CANoe_logger = None
            except:
                pass

    @keyword('CANoe Send Message')
    def canoe_send_message(self, message_id, cycle_time, can_message, bus_channel, message_type='FD'):
        _message_id = int(message_id, 16)
        _bus_ch = int(bus_channel)

        if message_type == 'FD':
            _is_fd = True
        else:
            _is_fd = False

        if _message_id <= 0x7FF:
            _is_extended_id = False
        else:
            _is_extended_id = True

        _can_message = [int(n, 16) for n in can_message.split()]
        _cycle_time = int(cycle_time)

        if _cycle_time > 0:
            self.bus[_bus_ch].send_periodic(
                Message(arbitration_id=_message_id,
                        data=_can_message,
                        is_fd=_is_fd,
                        dlc=len(_can_message),
                        is_extended_id=_is_extended_id,
                        is_rx=False), _cycle_time / 1000)
        else:
            self.bus[_bus_ch].send(
                Message(arbitration_id=_message_id,
                        data=_can_message,
                        is_fd=_is_fd,
                        dlc=len(_can_message),
                        is_extended_id=_is_extended_id,
                        is_rx=False))

    @keyword('CANoe Send Message All Stop')
    def canoe_send_msg_all_stop(self, bus_channel):
        _bus_ch = int(bus_channel)
        self.bus[_bus_ch].stop_all_periodic_tasks()

    @keyword('Find CAN Data From Latest Log')
    def find_msg_from_latest_log(self, log_file, message_id, can_message):
        msg_id = message_id.replace("0x", "").upper()
        data_tokens = can_message.upper().split()

        with open(log_file, 'r', errors='ignore') as f:
            for line in f:
                line_u = line.upper()

                # ID 체크
                if f" {msg_id} " not in line_u:
                    continue

                # DATA 토큰 전부 포함되는지 확인
                if all(tok in line_u for tok in data_tokens):
                    return "pass", line.strip()

        return "fail", ""

    # @keyword('Find CAN Data From Latest Log')
    # def find_msg_from_latest_log(self, log_path, message_id, can_message):
    #     find_data = ""
    #     list_of_files = glob.glob(log_path + '*')
    #     latest_file = max(list_of_files, key=os.path.getmtime)
    #
    #     with open(latest_file) as log_file:
    #         datafile = log_file.readlines()
    #     for line in datafile:
    #         if message_id[2:] in line and can_message in line:
    #             find_data = line.replace('\n', '')
    #
    #     log_file.close()
    #
    #     if find_data != "":
    #         find_result = "pass"
    #     else:
    #         find_result = "fail"
    #
    #     return find_result, find_data

    @keyword('CANoe Set Diag Env')
    def canoe_set_diag_env(self, bus_ch, tx_id, rx_id, can_type='CC'):

        self.diag_ch = int(bus_ch)
        self.nTx_id = int(tx_id, 16)
        self.nRx_id = int(rx_id, 16)

        self.addr = Address(AddressingMode.Normal_29bits, txid=self.nTx_id, rxid=self.nRx_id)

        params = {
            'stmin': 0,
            'blocksize': 0,
            'rx_flowcontrol_timeout': 1000,
            'rx_consecutive_frame_timeout': 1000,
            'tx_padding': 0x55,
            'blocking_send': False
        }

        if can_type == 'FD':
            params['tx_data_length'] = 64
            params['can_fd'] = True
            params['bitrate_switch'] = True
        else:
            params['tx_data_length'] = 8
            params['can_fd'] = False
            params['bitrate_switch'] = False

        self.stack = NotifierBasedCanStack(
            bus=self.bus[self.diag_ch],
            notifier=self.CANoe_recv,
            address=self.addr,
            params=params
        )

        self.stack.start()

    @keyword('CANoe Start Tester Present')
    def canoe_start_tester_present(self, interval=2.0):
        """
        Start sending periodic Tester Present (3E 00) messages

        Args:
            interval: Interval in seconds between messages (default: 2.0)
        """
        if self.tester_present_running:
            print("Tester Present already running")
            return

        interval_sec = float(interval)
        self.tester_present_running = True

        def _send_tester_present():
            while self.tester_present_running:
                try:
                    self.canoe_send_diag_msg("3E 00")
                    time.sleep(interval_sec)
                except Exception as e:
                    print(f"Tester Present error: {e}")
                    break

        self.tester_present_thread = threading.Thread(target=_send_tester_present, daemon=True)
        self.tester_present_thread.start()
        print(f"Tester Present started (interval: {interval_sec}s)")

    @keyword('CANoe Stop Tester Present')
    def canoe_stop_tester_present(self):
        """
        Stop sending periodic Tester Present messages
        """
        if not self.tester_present_running:
            print("Tester Present not running")
            return

        self.tester_present_running = False
        if self.tester_present_thread:
            self.tester_present_thread.join(timeout=3)
        print("Tester Present stopped")

    @keyword('CANoe Send Diag Message')
    def canoe_send_diag_msg(self, can_message, timeout="5", wait_response=True):
        """
        Send TP (Transport Protocol) data using ISO-TP stack and check response

        Args:
            can_message: Data to send (hex string with spaces, e.g., '22 F1 90 01 02 03 ...')
            wait_response: Wait for response and check PRC/NRC (default: True)
            timeout: Timeout for waiting response in seconds (default: 5.0)

        Returns:
            dict with 'status': 'PRC'/'NRC'/'TIMEOUT'/'FAIL', 'response': hex string, 'service_id': hex
        """
        f_timeout = float(timeout)

        # Remove spaces and convert to bytes
        tp_data_clean = can_message.replace(' ', '')
        _can_message = bytes.fromhex(tp_data_clean)

        # Get service ID from request
        service_id = _can_message[0] if len(_can_message) > 0 else 0x00

        try:
            self.stack.send(_can_message, send_timeout=f_timeout)
            print(f"TX: {can_message} ({len(_can_message)} bytes)")

            if not wait_response:
                return {'status': 'SENT', 'response': '', 'service_id': f'{service_id:02X}'}

            # Wait for response
            start_time = time.time()
            pending_count = 0
            max_pending_time = f_timeout * 3  # Extend timeout for pending responses

            while time.time() - start_time < max_pending_time:
                try:
                    remaining_time = max_pending_time - (time.time() - start_time)
                    response = self.stack.recv(timeout=min(0.5, remaining_time))
                    if response:
                        response_hex = ' '.join(f'{b:02X}' for b in response)
                        print(f"RX: {response_hex}")

                        if len(response) > 0:
                            # Check if it's a Negative Response (7F)
                            if response[0] == 0x7F:
                                if len(response) >= 3:
                                    requested_sid = response[1]
                                    nrc_code = response[2]

                                    # Check for Response Pending (0x78)
                                    if nrc_code == 0x78:
                                        pending_count += 1
                                        print(f"NRC 0x78 (Response Pending) - waiting... (count: {pending_count})")
                                        # Continue waiting for final response
                                        continue
                                    else:
                                        print(f"NRC: Service 0x{requested_sid:02X}, Code 0x{nrc_code:02X}")
                                        return {
                                            'status': 'NRC',
                                            'response': response_hex,
                                            'service_id': f'{requested_sid:02X}',
                                            'nrc_code': f'{nrc_code:02X}',
                                            'pending_count': pending_count
                                        }
                            # Check if it's a Positive Response (Service ID + 0x40)
                            elif response[0] == (service_id + 0x40):
                                print(f"PRC: Service 0x{service_id:02X}")
                                result = {
                                    'status': 'PRC',
                                    'response': response_hex,
                                    'service_id': f'{service_id:02X}'
                                }
                                if pending_count > 0:
                                    result['pending_count'] = pending_count
                                    print(f"Received after {pending_count} pending response(s)")
                                return result
                            else:
                                print(f"Unexpected response: {response_hex}")
                                return {
                                    'status': 'UNEXPECTED',
                                    'response': response_hex,
                                    'service_id': f'{service_id:02X}',
                                    'pending_count': pending_count
                                }
                except Exception as e:
                    continue

            print(f"Response timeout ({max_pending_time}s, pending count: {pending_count})")
            return {
                'status': 'TIMEOUT',
                'response': '',
                'service_id': f'{service_id:02X}',
                'pending_count': pending_count
            }

        except BlockingSendFailure as e:
            print(f"TP transmission failed: {e}")
            return {
                'status': 'FAIL',
                'response': str(e),
                'service_id': f'{service_id:02X}'
            }
        except Exception as e:
            print(f"TP transmission error: {e}")
            return {
                'status': 'FAIL',
                'response': str(e),
                'service_id': f'{service_id:02X}'
            }

    def __del__(self):
        try:
            # Stop tester present thread first
            if self.tester_present_running:
                self.stop_tester_present()

            # Stop all periodic tasks
            for busNum in range(0, len(self.bus)):
                try:
                    self.bus[busNum].stop_all_periodic_tasks()
                except:
                    pass

            # Stop isotp stack
            if self.stack:
                try:
                    self.stack.stop()
                except:
                    pass

            # Stop notifier FIRST to stop message flow
            if self.CANoe_recv:
                try:
                    self.CANoe_recv.stop()
                except:
                    pass

            # Small delay to ensure no more messages
            time.sleep(0.1)

            # Now stop loggers safely
            if self.CANoe_logger:
                try:
                    self.CANoe_logger.stop()
                except:
                    pass

            if self.CANoe_logger_full:
                try:
                    self.CANoe_logger_full.stop()
                except:
                    pass

            # Finally shutdown buses
            for busNum in range(0, len(self.bus)):
                try:
                    self.bus[busNum].shutdown()
                except:
                    pass

        except Exception as e:
            pass  # Silently ignore cleanup errors


if __name__ == '__main__':
    ch_dict = "[{'channel': 0, 'app_name': 'CANoe', 'bitrate': 500000, 'data_bitrate': None, 'is_fd': False}, {'channel': 1, 'app_name': 'CANoe', 'bitrate': 500000, 'data_bitrate': 2000000, 'is_fd': True}]"
    test = CANoe_Ctrl(ch_dict)
    file_path = r'D:\jh\workspace\canoe_test'
    log_path = test.canoe_full_log_save_start(file_path, "Auto.asc")
    time.sleep(5)
    #def canoe_send_message(self, message_id, cycle_time, can_message, bus_channel, message_type='FD'):
    print(test.canoe_send_message('0x7f2',100,'00 00 00 00 00 00 00 00',0,'noneFD'))
    time.sleep(3)
    print(test.canoe_send_message('0x288',100,'01 02 03 04 05 06 07 08',0,'noneFD'))
    time.sleep(5)
    test.canoe_full_log_save_stop()

    print(log_path)
    # def find_msg_from_latest_log(self, log_path, message_id, can_message):
    print(test.find_msg_from_latest_log(log_path, '0x7f2', '00 00 00 00 00 00 00 00'))

    # def find_msg_from_latest_log(self, log_path, message_id, can_message):
    print(test.find_msg_from_latest_log(log_path, '0x288', '01 02 03 04 05 06 07 08'))
