# -*- coding: utf-8 -*-

r"""Python(py_canoe) 실행 시 win32com gen_py 관련 오류 발생
CANoe cfg open 실패
Signal 조회 시 "Signal not found" 오류 발생

원인
win32com COM 캐시(gen_py) 손상 또는 불완전 생성
CANoe 강제 종료 또는 Python 비정상 종료 시 캐시 생성 중단
이후 깨진 캐시 참조로 COM 인터페이스 로딩 실패

조치
gen_py 캐시 삭제 (C:\\Users\\<계정>\\AppData\\Local\\Temp\\gen_py)
win32com 캐시 재생성 (gencache.Rebuild())
CANoe 관련 프로세스 완전 종료 (CANoe.exe, CANw32.exe)
cfg 실행 경로 기준으로 작업 디렉토리 변경 (os.chdir 적용)

결과
CANoe COM 인터페이스 정상 생성 확인
cfg 정상 Open 수행
Measurement Start/Stop 정상 동작
Signal 조회 및 제어 정상 동작 확인

결론
win32com gen_py 캐시 손상으로 인한 환경 문제였으며, 캐시 초기화를 통해 정상 복구됨
"""

from py_canoe import CANoe, wait
import time
import pythoncom
from robot.api.deco import keyword
import os
from datetime import datetime
import subprocess
import shutil
import win32com.client


SIG_STATE = {
    0: 'Default value returned',
    1: 'Measurement not running (app value)',
    2: 'Measurement not running (last value)',
    3: 'Signal received in current measurement'
}


class CANoe_RBS:
    def __init__(self):
        self.canoe_inst = None
        self.cfg_file = None
        self.canoe_log_dir = None

    # gen_py 캐시 삭제
    def _clean_gen_py(self):
        gen_py_path = os.path.join(os.environ['LOCALAPPDATA'], 'Temp', 'gen_py')
        if os.path.exists(gen_py_path):
            print("[CANoe] Cleaning win32com gen_py cache...")
            shutil.rmtree(gen_py_path, ignore_errors=True)

    # CANoe 완전 종료
    def _kill_canoe_process(self):
        processes = ["CANoe.exe", "CANw32.exe"]

        print("[CANoe] Killing CANoe processes...")

        for proc in processes:
            subprocess.run(
                ["taskkill", "/IM", proc, "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

        time.sleep(3)
        print("[CANoe] Process cleanup done")

    # 로그 폴더 생성
    def make_timestamp_log_dir(self, base_dir):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = os.path.join(base_dir, f"py_canoe_{ts}")
        os.makedirs(log_dir, exist_ok=True)
        return log_dir

    # 초기화 (핵심)
    @keyword('Init CANoe Via RBS')
    def Init_CANoe_Plugin(self, cfg_file, canoe_log_dir=r'.\Log'):

        print("[CANoe] ===== Init Start =====")

        # 1. CANoe 종료
        self._kill_canoe_process()

        # 2. cfg 기준 경로로 이동 (핵심)
        cfg_dir = os.path.dirname(cfg_file)
        os.chdir(cfg_dir)
        print(f"[CANoe] Working dir changed to: {cfg_dir}")

        # 3. 캐시 삭제
        self._clean_gen_py()

        # 4. COM 초기화
        pythoncom.CoInitialize()
        time.sleep(1)

        # 5. win32com 캐시 재생성 — CANoe 전용 stub만 생성.
        # 주의: gencache.Rebuild()는 등록된 모든 TLB를 한꺼번에 재빌드하는데, 그 과정에서
        # CANoe 이벤트 인터페이스 stub이 깨진 상태로 생성될 수 있고 그러면 py_canoe가
        # DispatchWithEvents()로 이벤트 싱크를 붙일 때 "This COM object does not support
        # events" 에러가 난다. EnsureDispatch는 지정 ProgID의 typelib만 정확히 생성하므로
        # 더 안정적이다.
        print("[CANoe] Ensuring CANoe gen_py stub...")
        try:
            win32com.client.gencache.EnsureDispatch("CANoe.Application")
        except Exception as e:
            # EnsureDispatch가 실패해도 py_canoe 자체가 lazy Dispatch로 회복할 수 있어
            # 치명적이지 않음. 단 로그는 남김.
            print(f"[CANoe] EnsureDispatch warning (non-fatal): {e}")

        self.cfg_file = cfg_file
        self.canoe_log_dir = self.make_timestamp_log_dir(canoe_log_dir)

        print(f"[CANoe] Log directory: {self.canoe_log_dir}")
        print(f"[CANoe] cfg exists: {os.path.exists(cfg_file)}")

        # 6. CANoe 인스턴스
        self.canoe_inst = CANoe(py_canoe_log_dir=self.canoe_log_dir)

        # 7. cfg open
        print("[CANoe] Opening configuration...")
        self.canoe_inst.open(
            canoe_cfg=self.cfg_file,
            visible=True,   # 디버깅시 True
            auto_save=False,
            prompt_user=False
        )

        time.sleep(3)

        print("[CANoe] ===== Init Done =====\n")

    # Start
    @keyword('CANoe Start Measurement')
    def Start(self):
        print("[CANoe] Start measurement")
        ret = self.canoe_inst.start_measurement()

        if ret:
            wait(2)

        return ret

    # Stop
    @keyword('CANoe Stop Measurement')
    def Stop(self):
        print("[CANoe] Stop measurement")
        ret = self.canoe_inst.stop_measurement()

        if ret:
            wait(1)

        return ret

    # Quit
    @keyword('CANoe Quit')
    def Quit(self):
        try:
            if self.canoe_inst:
                self.canoe_inst.quit()
            print("[CANoe] Quit done")
        except Exception as e:
            print(f"[CANoe] Quit failed: {e}")
            self._kill_canoe_process()

    # Signal Send
    def SendSignal(self, bus, channel, messageName, signalName, value):
        try:
            print(f"[CANoe] Set Signal {messageName}::{signalName} = {value}")
            self.canoe_inst.set_signal_value(bus, channel, messageName, signalName, value)
        except Exception as e:
            print(f"[CANoe] SendSignal ERROR: {e}")
            raise

    # Signal Get
    def GetSignal(self, bus, channel, messageName, signalName):
        try:
            state = self.canoe_inst.check_signal_state(bus, channel, messageName, signalName)

            if state == 3:
                val = self.canoe_inst.get_signal_value(bus, channel, messageName, signalName)
                print(f"[CANoe] Signal Value = {val}")
                return val
            else:
                raise Exception(f"Signal state error: {SIG_STATE.get(state)}")

        except Exception as e:
            print(f"[CANoe] GetSignal ERROR: {e}")
            raise

    # ###############################################[System Variable]########################################################
    def SetSysVar(self, namespace_name, sys_var_name, value):
        try:
            assert type(value) in [int, float, str], 'Type error({})'.format(type(value))

            self.canoe_inst.set_system_variable_value(namespace_name + "::" + sys_var_name, value)
            retmsg = "system variable({}) value set to ({})".format(namespace_name + '::' + sys_var_name, value)
            print(retmsg)
            return [True, None, retmsg]
        except Exception as e:
            retmsg = "system variable({}) value set to ({}) - [{}]".format(namespace_name + '::' + sys_var_name, value, e)
            print(retmsg)
            return [None, None, retmsg]

    def _get_canoe_app_com(self):
        """py_canoe 인스턴스 안의 raw CANoe.Application COM 객체를 찾아 반환.

        py_canoe 버전별로 속성명이 다름:
          - 일부 버전: self.canoe_inst.application
          - 다른 버전: self.canoe_inst.app
          - 내부 접근: self.canoe_inst._app / canoe_inst._CANoe__app 등
        모두 실패하면 win32com으로 새 핸들을 얻어 활성 인스턴스에 붙음.
        """
        candidates = ["application", "app", "_app", "_CANoe__app", "ApplicationCOMObject"]
        for attr in candidates:
            obj = getattr(self.canoe_inst, attr, None)
            if obj is not None and hasattr(obj, "System"):
                return obj
        # 마지막 폴백: 활성 CANoe.Application 가져오기
        return win32com.client.GetActiveObject("CANoe.Application")

    def _get_sysvar_via_com(self, namespace_name, sys_var_name):
        """py_canoe 우회: 원본 COM API를 직접 호출해 실제 에러 surfacing.

        py_canoe의 get_system_variable_value()는 내부에서 try/except로 모든 예외를
        삼키고 None을 리턴함. 그래서 변수 못 찾는 진짜 이유(잘못된 namespace,
        변수 미등록, measurement 미시작 등)를 확인하려면 COM 객체에 직접 접근해야 함.
        """
        app = self._get_canoe_app_com()
        # Application.System.Namespaces("XXX").Variables("YYY").Value
        sys_namespaces = app.System.Namespaces
        ns_obj = sys_namespaces(namespace_name)
        var_obj = ns_obj.Variables(sys_var_name)
        return var_obj.Value

    def GetSysVar(self, namespace_name, sys_var_name, return_symbolic_name=False, islog=True):
        full_name = f"{namespace_name}::{sys_var_name}"
        if self.canoe_inst is None:
            raise Exception(
                f"CANoe instance not initialized — call Init_CANoe_Plugin first (var={full_name})"
            )
        try:
            ret_value = self.canoe_inst.get_system_variable_value(full_name, return_symbolic_name)
            retmsg = f"system variable({full_name}) value = ({ret_value})"
            if islog:
                print(retmsg)
            if ret_value is None:
                # py_canoe는 내부에서 모든 예외를 삼키고 None 리턴 → 진짜 원인을
                # 알 수 없음. COM API 직접 호출로 실제 에러를 끄집어냄.
                com_error_detail = None
                try:
                    direct_value = self._get_sysvar_via_com(namespace_name, sys_var_name)
                    # 직접 호출은 성공했는데 py_canoe만 None 리턴한 경우 → 그대로 사용
                    if direct_value is not None:
                        retmsg = f"system variable({full_name}) value = ({direct_value}) [via direct COM]"
                        if islog:
                            print(retmsg)
                        return [True, direct_value, retmsg]
                except Exception as com_err:
                    com_error_detail = str(com_err)

                hint = (
                    f"check (1) Start() measurement was called, "
                    f"(2) namespace/name spelled exactly (case-sensitive), "
                    f"(3) cfg fully loaded"
                )
                if com_error_detail:
                    raise Exception(
                        f"system variable '{full_name}' lookup failed — "
                        f"direct COM error: {com_error_detail} | {hint}"
                    )
                raise Exception(
                    f"py_canoe returned None for system variable '{full_name}' — {hint}"
                )
            return [True, ret_value, retmsg]
        except Exception:
            raise

    def CompareValue(self, get_value, expect_value):
        try:
            get_value_type = type(get_value)
            expect_value_type = type(expect_value)

            print('(CompareValue)Getvalue_type({}) -- expect_value_type({})'.format(
                get_value_type, expect_value_type))

            ret = False  # 기본값 초기화

            if get_value_type in [int, float]:
                ret = (float(expect_value) == get_value)

            elif get_value_type is tuple:
                if len(get_value) > 0:
                    if expect_value_type in [int, float, str]:
                        ret = expect_value in get_value
                    else:
                        raise Exception(f'The expected value type({expect_value_type}) is not compare!!')
                else:
                    print("[CANoe] WARNING: Empty tuple received")
                    ret = False

            elif get_value_type is dict:
                if len(get_value) > 0:
                    value_list = list(get_value.values())

                    if expect_value_type in [int, float, str]:
                        ret = expect_value in value_list
                    else:
                        raise Exception(f'The expected value type({expect_value_type}) is not compare!!')
                else:
                    ret = False

            elif get_value_type is str:
                if expect_value_type in [int, float]:
                    ret = (str(expect_value) == get_value)
                else:
                    raise Exception(f'The expected value type({expect_value_type}) is not compare!!')

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
                cur_cmp_op, expect_value_list = self._parse_operator_expr(expect_value)

                for condition_value in expect_value_list:
                    # '[XX~YY]'  ==>  XX, YY
                    if condition_value.find('~') >= 0:
                        if condition_value[0] == '~' or condition_value[-1] == '~':
                            raise Exception("Incorrect use of operator in expect_value!")
                        if get_value_type in [int, float]:
                            minVal = float(condition_value.split('~')[0])
                            maxVal = float(condition_value.split('~')[1])
                            ret = get_value >= minVal and get_value <= maxVal
                        else:
                            raise Exception("Get value type({}) <> [int or float]".format(get_value_type))
                    # > >= < <=
                    elif (condition_value.startswith('>') or condition_value.startswith('<')
                          or condition_value.startswith('>=') or condition_value.startswith('<=')):
                        if condition_value[-1] in ['>', '<', '=']:
                            raise Exception("Incorrect use of operator in expect_value!")
                        ret = eval('get_value {}'.format(condition_value))
                    # '[XX]' ==> XX
                    else:
                        ret = self.CompareValue(get_value, condition_value)

                    cmp_result.append(ret)

                ret = self._aggregate_cmp_result(cmp_result, cur_cmp_op)
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

    # 연산자 검증 및 분리 (CompareString/compareDIAG 공통)
    def _parse_operator_expr(self, expect_value):
        if expect_value[0] in ('|', '&') or expect_value[-1] in ('|', '&'):
            raise Exception("Incorrect use of operator in expect_value!")
        if expect_value.find('|') > 0:
            cur_cmp_op = '|'
        elif expect_value.find('&') > 0:
            cur_cmp_op = '&'
        else:
            cur_cmp_op = ''
        expect_value_list = [data.strip() for data in expect_value.split(cur_cmp_op)] if cur_cmp_op else [expect_value.strip()]
        return cur_cmp_op, expect_value_list

    # cmp_result 집계 (CompareString/compareDIAG 공통)
    def _aggregate_cmp_result(self, cmp_result, cur_cmp_op):
        if cur_cmp_op == '&':
            return all(cmp_result)
        return any(cmp_result)

    # 빈/에러 DIAG 응답 체크 (DIAG_Request/SendDiagMsg 공통)
    def _is_empty_response(self, resp):
        if resp is None:
            return True
        if type(resp) is str:
            return resp.strip() == ''
        if type(resp) is dict:
            return resp == {} or 'error' in resp
        return False

    # 재시도 판단 (SendDiagMsg 공통)
    def _should_retry(self, attempt, max_retries, retry_delay, msg):
        if attempt < max_retries:
            print(msg)
            time.sleep(retry_delay)
            return True
        return False

    def compareDIAG(self, get_value, expect_value):
        try:
            cmp_result = list()
            get_value_type = type(get_value)
            expect_value_type = type(expect_value)
            print('compareDIAG:Getvalue_type({}) -- expect_value_type({})'.format(get_value_type, expect_value_type))

            # 잠재 1: dict 응답(return_sender_name=True)은 str로 변환 후 비교
            if get_value_type is dict:
                get_value = get_value.get('response', '') if 'response' in get_value else str(get_value)
                get_value_type = str

            if get_value_type is str:
                # 버그 3: 대소문자 정규화 (응답/기대값 모두 대문자로)
                get_value = get_value.upper()
                expect_value = expect_value.upper()
                cur_cmp_op, expect_value_list = self._parse_operator_expr(expect_value)

                for comp_cmd in expect_value_list:
                    pos_idx_list = comp_cmd.find('(')
                    if pos_idx_list == 0:
                        raise Exception("command format does not match!")
                    if pos_idx_list >= 0 and comp_cmd[-1] == ')':
                        comp_string = comp_cmd[:pos_idx_list]
                        idx_list = [int(val) for val in comp_cmd[pos_idx_list + 1:-1].split(':')]
                        print('idx_list={}'.format(idx_list))

                        # 'XX~YY'  ==>  XX, YY
                        if comp_string.find('~') >= 0:
                            if comp_string[0] == '~' or comp_string[-1] == '~':
                                raise Exception("Incorrect use of operator in expect_value!")
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
                            if val in ['X', ' ']:
                                continue
                            if idx >= len(get_value):
                                sub_result.append(False)
                                continue
                            ret_val = (get_value[idx] == val)
                            sub_result.append(ret_val)

                        ret = all(sub_result) if len(sub_result) > 0 else False

                    cmp_result.append(ret)

                ret = self._aggregate_cmp_result(cmp_result, cur_cmp_op)
            else:
                raise Exception("Responded DIAG messages type({}) is not support".format(get_value_type))
        except Exception as e:
            raise

        return ret

    def CheckSysVar(self, namespace_name, sys_var_name, expect_value):
        try:
            ret_value = self.GetSysVar(namespace_name, sys_var_name, islog=False)

            ret = self.CompareString(ret_value[1], expect_value)
        except Exception as e:
            raise

        return [ret, ret_value[1], ret_value[2]]

    # ############################################[Environment Variable]######################################################
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

    def _get_envvar_via_com(self, env_var_name):
        """py_canoe 우회: Environment Variable을 raw COM으로 직접 조회."""
        app = self._get_canoe_app_com()
        # Application.Environment.GetVariable("XXX").Value
        var_obj = app.Environment.GetVariable(env_var_name)
        return var_obj.Value

    def GetEnvVar(self, env_var_name, islog=True):
        if self.canoe_inst is None:
            raise Exception(
                f"CANoe instance not initialized — call Init_CANoe_Plugin first (var={env_var_name})"
            )
        try:
            ret_value = self.canoe_inst.get_environment_variable_value(env_var_name)
            retmsg = f"environment variable({env_var_name}) value = ({ret_value})"
            if islog:
                print(retmsg)
            if ret_value is None:
                # py_canoe가 예외 삼켰을 가능성 — direct COM 호출로 실제 에러 확인.
                com_error_detail = None
                try:
                    direct_value = self._get_envvar_via_com(env_var_name)
                    if direct_value is not None:
                        retmsg = f"environment variable({env_var_name}) value = ({direct_value}) [via direct COM]"
                        if islog:
                            print(retmsg)
                        return [True, direct_value, retmsg]
                except Exception as com_err:
                    com_error_detail = str(com_err)

                hint = (
                    f"check (1) Start() measurement was called, "
                    f"(2) variable exists in Environment Variables panel "
                    f"(if it's under System Variables namespace, use GetSysVar instead), "
                    f"(3) name spelled exactly (case-sensitive)"
                )
                if com_error_detail:
                    raise Exception(
                        f"environment variable '{env_var_name}' lookup failed — "
                        f"direct COM error: {com_error_detail} | {hint}"
                    )
                raise Exception(
                    f"py_canoe returned None for environment variable '{env_var_name}' — {hint}"
                )
            return [True, ret_value, retmsg]
        except Exception:
            raise

    def CheckEnvVar(self, env_var_name, expect_value):
        try:
            ret_value = self.GetEnvVar(env_var_name, islog=False)

            ret = self.CompareString(ret_value[1], expect_value)
        except Exception as e:
            raise

        return [ret, ret_value[1], ret_value[2]]

    # ############################################[DIAG]######################################################
    @keyword('DIAG Test Present via RBS')
    def DIAG_TestPresent(self, diag_ecu_qualifier_name, value):
        try:
            assert type(value) is bool, 'Value: Type error({})'.format(type(value))

            self.canoe_inst.control_tester_present(diag_ecu_qualifier_name, value)
            retmsg = "[{}] Tester Present {}".format(diag_ecu_qualifier_name,
                                                     'activated.' if value else 'deactivated.')
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
            if self._is_empty_response(resp):
                raise Exception('Unable to get value')
            return [True, resp, retmsg]
        except Exception as e:
            raise

    @keyword('Check Diag Request via RBS')
    def CheckDIAG(self, diag_ecu_qualifier_name, request, expect_value, request_in_bytes=True,
                  return_sender_name=False):
        try:
            if type(expect_value) is str and expect_value == '':
                raise Exception('expect_value was not entered!')
            resp = self.DIAG_Request(diag_ecu_qualifier_name, request, request_in_bytes, return_sender_name,
                                     islog=True)

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

                if resp and not self._is_empty_response(resp):
                    # Parse response
                    if type(resp) is str:
                        response_hex = resp.strip().upper()
                        response_bytes = [int(x, 16) for x in response_hex.split()]
                    elif type(resp) is dict:
                        raw_hex = resp.get('response', None)
                        if raw_hex is None:
                            if self._should_retry(attempt, max_retries, retry_delay, f"Attempt {attempt + 1} missing 'response' key, retrying in {retry_delay}s..."):
                                continue
                            return {'status': 'FAIL', 'response': str(resp), 'service_id': '00'}
                        response_hex = raw_hex.strip().upper()
                        response_bytes = [int(x, 16) for x in response_hex.split()]
                    else:
                        if self._should_retry(attempt, max_retries, retry_delay, f"Attempt {attempt + 1} failed, retrying in {retry_delay}s..."):
                            continue
                        return {'status': 'FAIL', 'response': str(resp), 'service_id': '00'}

                    if len(response_bytes) == 0:
                        if self._should_retry(attempt, max_retries, retry_delay, f"Attempt {attempt + 1} timeout, retrying in {retry_delay}s..."):
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
                    if self._should_retry(attempt, max_retries, retry_delay, f"Attempt {attempt + 1} no response, retrying in {retry_delay}s..."):
                        continue
                    return {'status': 'TIMEOUT', 'response': '', 'service_id': '00'}

            except Exception as e:
                if self._should_retry(attempt, max_retries, retry_delay, f"Attempt {attempt + 1} error: {e}, retrying in {retry_delay}s..."):
                    continue
                return {'status': 'FAIL', 'response': str(e), 'service_id': '00'}

        return {'status': 'TIMEOUT', 'response': '', 'service_id': '00'}


# 실행 테스트
if __name__ == "__main__":

    cfg_file = r"D:\3.RBS\PAG_Cluster_LGE_RBS_V3.9\PAG_Kombi_LGE_RBS_CAN_9.cfg"
    log_path = r"D:\jh\workspace\canoe_rbs"

    canoe = CANoe_RBS()

    # Init
    canoe.Init_CANoe_Plugin(cfg_file, log_path)

    # Start
    canoe.Start()

    time.sleep(5)

    print(canoe.SetEnvVar("A_ASG_15_1_0_31_1", 3))

    print(canoe.CheckEnvVar("A_ASG_15_1_0_31_1", 3))

    print(canoe.SetEnvVar("A_ASG_15_1_0_31_1", 0))

    canoe.DIAG_TestPresent('UDS_Diagnostic_Services_Generic', True)

    request_data = '10 01'
    ecu_name = 'UDS_Diagnostic_Services_Generic'

    canoe.DIAG_Request(ecu_name, request_data)
    time.sleep(3)
    ret = canoe.CheckDIAG(ecu_name, request_data, '50 01 00 32 01 F4')
    print(f"Check Diagnostic Session Control Result: {ret}")

    # Stop / Quit
    canoe.Stop()
    canoe.Quit()
