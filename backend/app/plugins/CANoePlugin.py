# -*- coding: utf-8 -*-
# Code provided by Cluster team , Suhee.jun
from py_canoe import CANoe, wait
import time
import pythoncom
from robot.api.deco import keyword
import os
from datetime import datetime
import subprocess

import shutil
SIG_STATE = {0: 'The default value of the signal is returned.',
             1: 'The measurement is not running. the value set by the application is returned.',
             2: 'The measurement is not running. the value of the last measurement is returned.',
             3: 'The signal has been received in the current measurement; the current value is returned.'}


class CANoePlugin:
    def __init__(self):  # r'..\Logs'
        self.canoe_inst = None
        self.cfg_file = None
        self.canoe_log_dir = None

    # def __del__(self):
    #     try:
    #         self.canoe_inst.quit()
    #     except Exception as e:
    #         print("Exit CANoe...fail!!![{}]".format(e))
    #         raise

    def _kill_canoe_process(self, force=True):
        """
        Kill Vector CANoe background process on Windows.
        """
        try:
            exe_name = "CANoe.exe"

            cmd = ["taskkill", "/IM", exe_name]
            if force:
                cmd.append("/F")

            print("[CANoe] Killing background CANoe.exe process...")
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
                shell=False
            )

            # 프로세스 정리 대기
            time.sleep(2)
            print("[CANoe] CANoe.exe process terminated")

        except Exception as e:
            print(f"[CANoe] Failed to kill CANoe.exe: {e}")

    def make_timestamp_log_dir(self,base_dir):
        """
        base_dir 하위에 py_canoe_YYYYMMDD_HHMMSS 폴더 생성
        """
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = os.path.join(base_dir, f"py_canoe_{ts}")

        os.makedirs(log_dir, exist_ok=True)
        return log_dir

    @keyword('Init CANoe Via RBS')
    def Init_CANoe_Plugin(self, cfg_file, canoe_log_dir=r'.\Log'):
        # ✅ 1. 기존 CANoe 강제 종료 (중요)
        self._kill_canoe_process()

        # ✅ 2. COM 잔여 메시지 정리
        time.sleep(2)
        pythoncom.CoInitialize()

        self.cfg_file = cfg_file
        self.canoe_log_dir = self.make_timestamp_log_dir(canoe_log_dir)
        print(f"[CANoe] Log directory: {self.canoe_log_dir}")

        # ✅ 3. 새 COM 인스턴스
        self.canoe_inst = CANoe(py_canoe_log_dir=self.canoe_log_dir)

        # ✅ 4. cfg load (이때는 measurement 안 돌고 있음)
        self.canoe_inst.open(
            canoe_cfg=cfg_file,
            visible=False,
            auto_save=False,
            prompt_user=False
        )

    # @keyword('Init CANoe Via RBS')
    # def Init_CANoe_Plugin(self, cfg_file, canoe_log_dir=r'.\Log'):
    #     try:
    #         self.canoe_inst = None
    #         self.cfg_file = cfg_file
    #         # ✅ timestamp 기반 로그 디렉토리 생성
    #         self.canoe_log_dir = self.make_timestamp_log_dir(canoe_log_dir)
    #         print(f"[CANoe] Log directory: {self.canoe_log_dir}")
    #         self.canoe_inst = CANoe(
    #             py_canoe_log_dir=self.canoe_log_dir
    #         )
    #         self.canoe_inst.open(canoe_cfg=cfg_file, visible=False, auto_save=False)
    #     except Exception as e:
    #         print(f"Run CANoe Fail!!!![{e}]")
    #         self._kill_canoe_process()
    #         self.canoe_inst = None
    #         raise

    def wait_s(self, seconds=0.1):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            pythoncom.PumpWaitingMessages()
        return 1

    ###############################################[UTIL]########################################################

    def is_number(self, s):
        try:
            is_num = True
            if s == None: return False
            float(s)
        except ValueError:
            is_num = False

        return is_num

    def type_casting(self, s):
        try:
            if type(s) is not str: return s
            # if s is None: return s

            if s.isdigit():
                ori_len = len(s)
                value = int(s)
                value_str = str(value)
                if len(value_str) != ori_len:
                    value = s
            else:
                value = int(s)
        except ValueError:
            try:
                value = float(s)
            except ValueError:
                try:
                    if s == '()' or s == '{}': return eval(s)
                    if s[0] == '(' and s[-1] == ')' and s.find(',') > 1:
                        return eval(s)  # tuple
                    elif s[0] == '{' and s[-1] == '}' and s.find(':') > 1:
                        return eval(s)  # dict
                    else:
                        value = s
                except Exception as e:
                    value = s
        return value

    def CompareValue(self, get_value, expect_value):
        try:
            get_value_type = type(get_value)
            expect_value_type = type(expect_value)
            print('(CompareValue)Getvalue_type({}) -- expect_value_type({})'.format(get_value_type, expect_value_type))

            if get_value_type in [int, float]:
                ret = (float(expect_value) == get_value)
            elif get_value_type is tuple:
                if len(get_value) > 0:

                    if expect_value_type in [int, float, str]:
                        ret = expect_value in get_value
                    else:
                        raise Exception('The expected value type({}) is not compare!!'.format(expect_value_type))
            elif get_value_type is dict:
                if len(get_value) > 0:
                    value_list = list(get_value.values())

                    if expect_value_type in [int, float, str]:
                        ret = expect_value in value_list
                    else:
                        raise Exception('The expected value type({}) is not compare!!'.format(expect_value_type))
            elif get_value_type is str:
                if expect_value_type in [int, float]:
                    ret = (str(expect_value) == get_value)
                else:
                    raise Exception('The expected value type({}) is not compare!!'.format(expect_value_type))
            else:
                raise ValueError('The expected value input format is incorrect!!')
        except Exception as e:
            raise

        return ret

    def CompareString(self, get_value, expect_value):
        try:
            cmp_result = list()
            get_value_type = type(get_value)
            expect_value_type = type(expect_value)
            print('Getvalue_type({}) -- expect_value_type({})'.format(get_value_type, expect_value_type))

            if get_value_type == expect_value_type:
                return get_value == expect_value
            elif expect_value_type is str:
                if expect_value[0] == '|' or expect_value[0] == '&' or expect_value[-1] == '|' or expect_value[
                    -1] == '&': raise Exception("Incorrect use of operator in expect_value!")

                if expect_value.find('|') > 0:
                    cur_cmp_op = '|'
                    expect_value_list = [data.strip() for data in expect_value.split(cur_cmp_op)]
                elif expect_value.find('&') > 0:
                    cur_cmp_op = '&'
                    expect_value_list = [data.strip() for data in expect_value.split(cur_cmp_op)]
                else:
                    cur_cmp_op = ''
                    expect_value_list = [expect_value.strip()]

                for condition_value in expect_value_list:
                    # '[XX~YY]'  ==>  XX, YY
                    if condition_value.find('~') >= 0:
                        if condition_value[0] == '~' or condition_value[-1] == '~': raise Exception(
                            "Incorrect use of operator in expect_value!")
                        if get_value_type in [int, float]:
                            minVal = float(condition_value.split('~')[0])
                            maxVal = float(condition_value.split('~')[1])
                            ret = get_value >= minVal and get_value <= maxVal
                        else:
                            raise Exception("Get value type({}) <> [int or float]".format(get_value_type))
                    # > >= < <=
                    elif (condition_value.startswith('>') or condition_value.startswith(
                            '<') or condition_value.startswith('>=') or condition_value.startswith('<=')):
                        if condition_value[-1] in ['>', '<', '=']: raise Exception(
                            "Incorrect use of operator in expect_value!")
                        ret = eval('get_value {}'.format(condition_value))
                    # '[XX]' ==> XX
                    else:
                        ret = self.CompareValue(get_value, condition_value)

                    cmp_result.append(ret)

                if cur_cmp_op == '|':
                    ret = any(cmp_result)
                elif cur_cmp_op == '&':
                    ret = all(cmp_result)
                else:
                    ret = any(cmp_result)
            else:
                ret = self.CompareValue(get_value, expect_value)

        except Exception as e:
            raise

        return ret

    def GetByteDataList(self, source_str, find_start_idx_list):
        byte_data_list = []
        byte_source_list = source_str.split(' ')

        byte_src_len = len(byte_source_list)

        for idx in find_start_idx_list:
            if byte_src_len <= idx:
                return False, []
            byte_data_list.append(int(byte_source_list[idx], 16))

        return True, byte_data_list

    def compareDIAG(self, get_value, expect_value):
        try:
            cmp_result = list()
            get_value_type = type(get_value)
            expect_value_type = type(expect_value)
            print('compareDIAG:Getvalue_type({}) -- expect_value_type({})'.format(get_value_type, expect_value_type))

            if get_value_type is str:
                if expect_value[0] == '|' or expect_value[0] == '&' or expect_value[-1] == '|' or expect_value[
                    -1] == '&': raise Exception("Incorrect use of operator in expect_value!")

                if expect_value.find('|') > 0:
                    cur_cmp_op = '|'
                    expect_value_list = [data.strip() for data in expect_value.split(cur_cmp_op)]
                elif expect_value.find('&') > 0:
                    cur_cmp_op = '&'
                    expect_value_list = [data.strip() for data in expect_value.split(cur_cmp_op)]
                else:
                    cur_cmp_op = ''
                    expect_value_list = [expect_value.strip()]

                for comp_cmd in expect_value_list:
                    pos_idx_list = comp_cmd.find('(')
                    if pos_idx_list == 0: raise Exception("command format does not match!")
                    if pos_idx_list >= 0 and comp_cmd[-1] == ')':
                        comp_string = comp_cmd[:pos_idx_list]
                        idx_list = [int(val) for val in comp_cmd[pos_idx_list + 1:-1].split(':')]
                        print('idx_list={}'.format(idx_list))

                        # 'XX~YY'  ==>  XX, YY
                        if comp_string.find('~') >= 0:
                            if comp_string[0] == '~' or comp_string[-1] == '~': raise Exception(
                                "Incorrect use of operator in expect_value!")
                            minVal = int(comp_string.split('~')[0], 16)
                            maxVal = int(comp_string.split('~')[1], 16)

                            ret_pf, idx_val_list = self.GetByteDataList(get_value, idx_list)

                            sub_result = []
                            if ret_pf:
                                for val in idx_val_list:
                                    ret_val = val >= minVal and val <= maxVal
                                    sub_result.append(ret_val)

                            ret = all(sub_result) if len(sub_result) > 0 else False
                        # XX(Startidx, Staridx,...)
                        else:
                            sub_result = []
                            for start_idx in idx_list:
                                start_idx = start_idx * 3
                                print("({})[{}] -- [{}]exp".format(start_idx, get_value[start_idx:], comp_string))
                                ret_val = get_value[start_idx:].startswith(comp_string)
                                sub_result.append(ret_val)
                            ret = all(sub_result) if len(sub_result) > 0 else False
                    else:  # in
                        sub_result = []
                        for idx, val in enumerate(expect_value):
                            if val in ['X', ' ']: continue
                            ret_val = (get_value[idx] == val)
                            sub_result.append(ret_val)

                        ret = all(sub_result) if len(sub_result) > 0 else False

                    cmp_result.append(ret)

                if cur_cmp_op == '|':
                    ret = any(cmp_result)
                elif cur_cmp_op == '&':
                    ret = all(cmp_result)
                else:
                    ret = any(cmp_result)
            # elif get_value_type is dict:
            else:
                raise Exception("Responded DIAG messages type({}) is not support".format(get_value_type))
        except Exception as e:
            raise

        return ret

    ###############################################[CANoe Control Funtion]########################################################

    # def Run(self):
    #     try:
    #         self.canoe_inst = CANoe(py_canoe_log_dir=self.canoe_log_dir)  # user_capl_functions=('addition_function', 'hello_world')
    #         self.canoe_inst.open(canoe_cfg=self.cfg_file, visible=True, auto_save=False)
    #     except Exception as e:
    #         raise
    #
    # def Quit(self):
    #     try:
    #         self.canoe_inst = CANoe(py_canoe_log_dir=self.canoe_log_dir)  # user_capl_functions=('addition_function', 'hello_world')
    #         self.canoe_inst.open(canoe_cfg=self.cfg_file, visible=True, auto_save=False)
    #     except Exception as e:
    #         raise

    def Open(self):
        try:
            self.canoe_inst.open(canoe_cfg=self.cfg_file, visible=False, auto_save=False, prompt_user=False)
        except Exception as e:
            raise

    @keyword('CANoe Start Measurement')
    def Start(self):
        try:
            ret = self.canoe_inst.start_measurement()
            if ret:  wait(2)
            return ret

        except Exception as e:
            raise

    @keyword('CANoe Stop Measurement')
    def Stop(self):
        try:
            ret = self.canoe_inst.stop_measurement()
            if ret:  wait(1)
            return ret
        except Exception as e:
            raise

    @keyword('CANoe Quit')
    def Quit(self):
        try:
            if self.canoe_inst:
                self.canoe_inst.quit()
            print("[CANoe] Quit done")
        except Exception as e:
            print(f"[CANoe] Quit failed, force kill: {e}")
            self._kill_canoe_process()

    ###############################################[System Variable]########################################################
    def SetSysVar(self, namespace_name, sys_var_name, value):
        try:
            assert type(value) in [int, float, str], 'Type error({})'.format(type(value))

            self.canoe_inst.set_system_variable_value(namespace_name + "::" + sys_var_name, value)
            retmsg = "system variable({}) value set to ({})".format(namespace_name + '::' + sys_var_name, value)
            print(retmsg)
            return [True, None, retmsg]
        except Exception as e:
            retmsg = "system variable({}) value set to ({}) - [{}]".format(namespace_name + '::' + sys_var_name, value,
                                                                           e)
            print(retmsg)
            return [None, None, retmsg]

    def GetSysVar(self, namespace_name, sys_var_name, return_symbolic_name=False, islog=True):
        try:
            ret_value = self.canoe_inst.get_system_variable_value(namespace_name + "::" + sys_var_name,
                                                                  return_symbolic_name)
            retmsg = "system variable({}) value = ({})".format(namespace_name + '::' + sys_var_name, ret_value)
            if islog: print(retmsg)
            if ret_value is None: raise Exception('Unable to get value')
            return [True, ret_value, retmsg]
        except Exception as e:
            # retmsg = "system variable({}) value - [{}]".format(messageName + '::' + signalName, e)
            # self.logger.exception(retmsg)
            raise

    def CheckSysVar(self, namespace_name, sys_var_name, expect_value):
        try:
            ret_value = self.GetSysVar(namespace_name, sys_var_name, islog=False)

            ret = self.CompareString(ret_value[1], expect_value)
            # assert type(ret_value[1]) == type(value), 'value({})[{}] <> ({})[{}]<expected> - [Does not match value type!!!]'.format(ret_value[1], type(ret_value[1]), value, type(value))

            # ret = (value == ret_value[1])
        except Exception as e:
            # self.logger.exception("Check system variable({}) - [{}]".format(messageName + '::' + signalName, e))
            raise

        return [ret, ret_value[1], ret_value[2]]

    ############################################[Environment Variable]######################################################
    def SetEnvVar(self, env_var_name, value):
        try:
            assert type(value) in [int, float, str, tuple], 'Type error({})'.format(type(value))

            self.canoe_inst.set_environment_variable_value(env_var_name, value)
            retmsg = "environment variable({}) value set to ({})".format(env_var_name, value)
            print(retmsg)
            return [True, None, retmsg]
        except Exception as e:
            retmsg = "environment variable({}) value set to ({}) - [{}]".format(env_var_name, value, e)
            print(retmsg)
            return [None, None, retmsg]

    def GetEnvVar(self, env_var_name, islog=True):
        try:
            ret_value = self.canoe_inst.get_environment_variable_value(env_var_name)
            retmsg = "environment variable({}) value = ({})".format(env_var_name, ret_value)
            if islog: print(retmsg)
            if ret_value is None: raise Exception('Unable to get value')
            return [True, ret_value, retmsg]
        except Exception as e:
            # retmsg = "environment variable({}) value - [{}]".format(signalName, e)
            # self.logger.exception(retmsg)
            raise

    def CheckEnvVar(self, env_var_name, expect_value):
        try:
            ret_value = self.GetEnvVar(env_var_name, islog=False)

            ret = self.CompareString(ret_value[1], expect_value)
            # assert type(ret_value[1]) == type(value), 'value({})[{}] <> ({})[{}]<expected> - [Does not match value type!!!]'.format(ret_value[1], type(ret_value[1]), value, type(value))

            # ret = (value == ret_value[1])
        except Exception as e:
            # self.logger.exception("Check environment variable({}) - [{}]".format(signalName, e))
            raise

        return [ret, ret_value[1], ret_value[2]]

    ############################################[CAN, LIN, Flexray]######################################################
    def SendSignal(self, bus, channel, messageName, signalName, value):
        try:
            assert type(value) in [int, float], 'Type error({})'.format(type(value))

            self.canoe_inst.set_signal_value(bus, channel, messageName, signalName, value)
            retmsg = "{}({}) value set to ({})".format(bus, messageName + '::' + signalName, value)
            print(retmsg)
            wait(1)
            return [True, None, retmsg]
        except Exception as e:
            retmsg = "{}({}) value set to ({}) - [{}]".format(bus, messageName + '::' + signalName, value, e)
            print(retmsg)
            return [None, None, retmsg]

    def GetSignal(self, bus, channel, messageName, signalName, islog=True, get_delay_time=1000.):
        try:
            # bRunning = self.canoe_inst.get_measurement_running_status()
            # #ret_value = self.canoe_inst.get_signal_value(bus='CAN', channel=channel, message=messageName, signal=signalName)
            # is_online = self.canoe_inst.check_signal_online(bus='CAN', channel=channel, message=messageName, signal=signalName)
            # if not is_online:
            # #     #self.canoe_inst.start_measurement()
            # #     # ret_value = self.canoe_inst.get_signal_value(bus='CAN', channel=channel, message=messageName,
            # #     #                                              signal=signalName)
            # #     #wait(1)
            #       self.wait_s(1)
            #
            #
            # state = self.canoe_inst.check_signal_state(bus='CAN', channel=channel, message=messageName, signal=signalName)
            # ret_value = self.canoe_inst.get_signal_value(bus='CAN', channel=channel, message=messageName,
            #                                              signal=signalName)
            #
            # if state == 3:
            #     retmsg = "value of {}({}) = ({})_isonlie({})_bRunning({})".format(bus,messageName + '::' + signalName, ret_value, is_online, bRunning)
            #     if islog: self.logger.info(retmsg)
            #     if ret_value is None: raise Exception('Unable to get value')
            #     return [True, ret_value, retmsg]
            # else:
            #     raise Exception('Read value({})_isonline({})_bRunning({}), Check_signal_state({}) - ({})'.format(ret_value, is_online, bRunning, state, SIG_STATE.get(state)))

            is_online = self.canoe_inst.check_signal_online(bus, channel, messageName, signalName)
            if not is_online:
                if get_delay_time < 1000.: get_delay_time = 1000.
                self.wait_s(get_delay_time / 1000.)
                if islog: print('{}ms wait time......(retry)'.format(get_delay_time))

            state = self.canoe_inst.check_signal_state(bus, channel, messageName, signalName)

            if state == 3:
                ret_value = self.canoe_inst.get_signal_value(bus, channel, messageName, signalName)
                retmsg = "value of {}({}) = ({})".format(bus, messageName + '::' + signalName, ret_value)
                if islog: print(retmsg)
                if ret_value is None: raise Exception('Unable to get value')
                return [True, ret_value, retmsg]
            else:
                raise Exception('Check_signal_state({}) - ({})'.format(state, SIG_STATE.get(state)))

        except Exception as e:
            # retmsg = "value of {}({}) - [{}])".format(bus,messageName + '::' + signalName, e)
            # if islog: self.logger.error(retmsg)
            raise

    def CheckSignal(self, bus, channel, messageName, signalName, expect_value, get_delay_time=1000.):
        try:
            if type(expect_value) is str and expect_value == '': raise Exception('expect_value was not entered!')
            ret_value = self.GetSignal(bus, channel, messageName, signalName, islog=True, get_delay_time=get_delay_time)

            ret = self.CompareString(ret_value[1], expect_value)

        except Exception as e:
            # self.logger.exception("Check {} signal({}) - [{}]".format(bus, signalName, e))
            raise

        return [ret, ret_value[1], ret_value[2]]

    ############################################[DIAG]######################################################
    @keyword('DIAG Test Present via RBS')
    def DIAG_TestPresent(self, diag_ecu_qualifier_name, value):
        try:
            assert type(value) is bool, 'Value: Type error({})'.format(type(value))

            self.canoe_inst.control_tester_present(diag_ecu_qualifier_name, value)
            retmsg = "[{}] Tester Present {}".format(diag_ecu_qualifier_name, 'activated.' if value else 'deactivated.')
            print(retmsg)
            return [True, None, retmsg]
        except Exception as e:
            retmsg = "[{}] Tester Present {} - [{}]".format(diag_ecu_qualifier_name,
                                                            'activated.' if value else 'deactivated.', e)
            print(retmsg)
            return [None, None, retmsg]

    @keyword('DIAG Request via RBS')
    def DIAG_Request(self, diag_ecu_qualifier_name, request, request_in_bytes=True, return_sender_name=False,
                     timeout=None, islog=True):
        try:
            resp = self.canoe_inst.send_diag_request(diag_ecu_qualifier_name, request,
                                                     request_in_bytes=request_in_bytes,
                                                     return_sender_name=return_sender_name)
            retmsg = "[{}] DIAG Request({}) ===> ({})".format(diag_ecu_qualifier_name, request, resp)
            if islog:
                print(retmsg)
            if type(resp) is str and resp == '': raise Exception('Unable to get value(str)')
            if type(resp) is dict and resp == {}: raise Exception('Unable to get value(dict)')
            return [True, resp, retmsg]
        except Exception as e:
            raise

    @keyword('Check Diag Request via RBS')
    def CheckDIAG(self, diag_ecu_qualifier_name, request, expect_value, request_in_bytes=True,
                  return_sender_name=False):
        try:
            if type(expect_value) is str and expect_value == '': raise Exception('expect_value was not entered!')
            resp = self.DIAG_Request(diag_ecu_qualifier_name, request, request_in_bytes, return_sender_name, islog=True)

            ret = self.compareDIAG(resp[1], expect_value)
        except Exception as e:
            msg = "Check [{}] DIAG Request({}) - [{}])".format(diag_ecu_qualifier_name, request, e)
            print(msg)
            raise

        return [ret, resp[1], resp[2]]

    @keyword('Send Diag Message Via RBS')
    def SendDiagMsg(self, diag_ecu_qualifier_name, request, timeout=30.0, request_in_bytes=True,
                    return_sender_name=False, max_retries=5, retry_delay=10.0):
        """
        Send diagnostic request and parse response for PRC/NRC status, with retry on timeout

        Args:
            diag_ecu_qualifier_name: ECU qualifier name
            request: Request data (hex string with spaces, e.g., '10 01')
            timeout: Not used (py-canoe doesn't support timeout)
            request_in_bytes: Whether request is in bytes (default: True)
            return_sender_name: Whether to return sender name (default: False)
            max_retries: Maximum number of retries on timeout (default: 5)
            retry_delay: Delay between retries in seconds (default: 10.0)

        Returns:
            dict with 'status': 'PRC'/'NRC'/'TIMEOUT'/'FAIL', 'response': hex string, 'service_id': hex
        """
        for attempt in range(max_retries + 1):
            try:
                # Send request
                resp = self.canoe_inst.send_diag_request(diag_ecu_qualifier_name, request,
                                                         request_in_bytes=request_in_bytes,
                                                         return_sender_name=return_sender_name)

                if resp and not ((type(resp) is str and resp.strip() == '') or (type(resp) is dict and resp == {})):
                    # Parse response
                    if type(resp) is str:
                        response_hex = resp.strip()
                        response_bytes = [int(x, 16) for x in response_hex.split()]
                    elif type(resp) is dict:
                        response_hex = resp.get('response', '')
                        response_bytes = [int(x, 16) for x in response_hex.split()]
                    else:
                        if attempt < max_retries:
                            print(f"Attempt {attempt + 1} failed, retrying in {retry_delay}s...")
                            time.sleep(retry_delay)
                            continue
                        return {'status': 'FAIL', 'response': str(resp), 'service_id': '00'}

                    if len(response_bytes) == 0:
                        if attempt < max_retries:
                            print(f"Attempt {attempt + 1} timeout, retrying in {retry_delay}s...")
                            time.sleep(retry_delay)
                            continue
                        return {'status': 'TIMEOUT', 'response': '', 'service_id': '00'}

                    # Get service ID from request
                    request_clean = request.replace(' ', '')
                    request_bytes = bytes.fromhex(request_clean)
                    service_id = request_bytes[0] if len(request_bytes) > 0 else 0x00

                    # Check response
                    if response_bytes[0] == 0x7F:  # Negative Response
                        if len(response_bytes) >= 3:
                            requested_sid = response_bytes[1]
                            nrc_code = response_bytes[2]
                            return {
                                'status': 'NRC',
                                'response': response_hex,
                                'service_id': f'{requested_sid:02X}',
                                'nrc_code': f'{nrc_code:02X}'
                            }
                        else:
                            return {'status': 'FAIL', 'response': response_hex, 'service_id': f'{service_id:02X}'}
                    elif response_bytes[0] == (service_id + 0x40):  # Positive Response
                        return {
                            'status': 'PRC',
                            'response': response_hex,
                            'service_id': f'{service_id:02X}'
                        }
                    else:
                        return {
                            'status': 'UNEXPECTED',
                            'response': response_hex,
                            'service_id': f'{service_id:02X}'
                        }
                else:
                    if attempt < max_retries:
                        print(f"Attempt {attempt + 1} no response, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        continue
                    return {'status': 'TIMEOUT', 'response': '', 'service_id': '00'}

            except Exception as e:
                if attempt < max_retries:
                    print(f"Attempt {attempt + 1} error: {e}, retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    continue
                return {'status': 'FAIL', 'response': str(e), 'service_id': '00'}

        return {'status': 'TIMEOUT', 'response': '', 'service_id': '00'}



if __name__ == "__main__":

    cfg_file = r"D:\3.RBS\VCU 21.62_0318/ICB.cfg"
    log_path = r'D:\jh\workspace\canoe_rbs'
    # CANoePlugin 인스턴스 생성 및 프로젝트 오픈
    canoe = CANoePlugin()
    canoe.Init_CANoe_Plugin(cfg_file,log_path)
    canoe.Start()
    time.sleep(5)
    # 예시: 메시지 Body_Information_12B 안의 Signal "IDDAjrSwAtv" = 1
    canoe.SendSignal(
        bus='CAN',
        channel=1,
        messageName='Body_Information_12B',
        signalName='IDDAjrSwAtv',
        value=1
    )
    time.sleep(3)
    canoe.GetSignal('CAN',1,'Body_Information_12B','IDDAjrSwAtv')
    time.sleep(3)

    print(canoe.SetSysVar(namespace_name="Cluster",sys_var_name="IgnitionState",value=1))

    print('CanTp Check : ',canoe.CheckSysVar("CanTp", "BlockSize", 0))


    print(canoe.SetEnvVar("IGNITION_ON", 3))

    print(canoe.CheckEnvVar("IGNITION_ON", 3))

    canoe.Stop()
    canoe.Quit()
