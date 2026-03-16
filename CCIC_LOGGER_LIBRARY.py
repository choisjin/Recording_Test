# -*- coding: utf-8 -*-
'''
Created on 2021. 07. 02.

@author: juwoong.back
'''
import datetime
import logging
import os

class testing_logger():
    def __init__(self):
        """
        asctime	    %(asctime)s 	�씤媛꾩씠 �씫�쓣 �닔 �엳�뒗 �떆媛� �몴�떆
        created	    %(created)f	    logRecord媛� 留뚮뱾�뼱吏� �떆媛�
        filename	%(filename)s	pathname�쓽 file �씠由� 遺�遺�
        funcName	%(funcName)s	logging call�쓣 �룷�븿�븯�뒗 function�쓽 �씠由�
        levelname	%(levelname)s	硫붿떆吏��쓽 Text logging level: �삁) INFO
        lineno	    %(lineno)d	    logging call�씠 諛쒖깮�븳 肄붾뱶�쓽 line �닽�옄
        module	    %(module)s	    filename�쓽 紐⑤뱢 �씠由� 遺�遺�
        message	    %(message)s	    硫붿떆吏�
        name	    %(name)s	    logger�쓽 �씠由�
        pathname	%(pathname)s	full pathname
        thread	    %(thread)d	    thread ID
        threadName	%(threadName)s	thread �씠由�
        """
        self.root_log_folder = "D:/Output_Log"
        self.logging_file_path = None
        self.logging_file_path = self.set_logging_file()        #init file path
        self.main_logger = logging.getLogger("test_logger")
        self.main_logger.setLevel(logging.DEBUG)  # or whatever
        logging_handler = logging.FileHandler(self.logging_file_path, 'w', 'utf-8')  # or whatever
        logging_handler.setFormatter(logging.Formatter(
            '[%(asctime)s] [%(levelname)-8s] [%(message)s] [%(module)s::%(funcName)s (LINE:%(lineno)d)]'))  # or whatever
        self.main_logger.addHandler(logging_handler)
        console_logger = logging.StreamHandler()
        console_logger.setLevel(logging.DEBUG)          #set console(output)logging
        console_logger.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)-8s] [%(message)s]'))
        #self.main_logger.addHandler(console_logger)
        self.check_logging_folder()
    def set_logging_file(self):
        if not self.check_logging_folder():     #check logging folder
            return False
        today_str = datetime.datetime.now().strftime("%y%m%d_%H%M%S")
        root_log_folder = self.root_log_folder
        if os.path.exists(root_log_folder) == False:
            os.makedirs(root_log_folder)
        logging_file_name = ("{}/{}_testing_log.log".format(root_log_folder, today_str))
        return logging_file_name

    def check_logging_folder(self):
        """
        create logging folder (D:/Output_Log)
        :return:
        """
        root_folder = self.root_log_folder
        if os.path.exists(root_folder) == False:
            try:
                os.makedirs(root_folder)
            except OSError as e:
                print('{0}'.format(e))
                return False

    def debug(self,log_str):
        """
        :param log_str: String to logging debug
        :return:
        """
        self.main_logger.debug(log_str)
    def info(self,log_str):
        """

        :param log_str: String to logging info
        :return:
        """
        self.main_logger.info(log_str)
    def warning(self,log_str):
        """
        :param log_str: String to logging warning
        :return:
        """
        self.main_logger.warning(log_str)
    def error(self,log_str):
        """
        :param log_str: String to logging error
        :return:
        """
        self.main_logger.error(log_str)
    def exception(self,log_str):
        """
        :param log_str: String to logging exception
        :return:
        """
        self.main_logger.exception(log_str)

    def func_test(self):
        """
        debugging level type example
        :return:
        """
        self.debug('This message is at func_test()')
        self.debug('This message should go to the log file')
        self.info('So should this')
        self.warning('And this, too')
        self.error('And non-ASCII stuff, too, like 횠resund and Malm철')

    def cal(self, a, b):
        """
        except case
        :param a: 
        :param b: 
        :return: 
        """"""
         :param a:
         :param b:
         :return:
         """
        try:
            result = a / b
        except Exception:
            self.exception("Division by zero is not possible")

    def repeat_print(self):
        """
        loop print example
        :return:
        """
        for i in range(5):
            self.info('{i} : 諛섎났')
# if __name__ == "__main__":
#     # Script �떆�옉 �떆 module �깮�꽦,
#     # �깮�꽦�븯硫� D:/logging/�떆媛�_紐⑤뱢.log濡� �뙆�씪�씠 �깮�꽦�맗�땲�떎.
#     logging_module = testing_logger()
#     #媛� type蹂� log 異쒕젰諛⑸쾿 debug��, error留� �옒�뜥�룄 寃��깋�븯湲� �렪�빀�땲�떎.
#     logging_module.main_logger.debug("debug type")
#     logging_module.main_logger.info("info type")
#     logging_module.main_logger.warning("warning type")
#     logging_module.main_logger.error("error type")
#     """
#         exception case
#     """
#     try:
#         res = 10/0      #0 devide
#     except:
#         logging_module.main_logger.exception("exception type")



