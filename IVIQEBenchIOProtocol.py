'''
Defines for QEBench Plug-in
'''
ATS_SYNC                    = 0x61
ATS_CMD_ID                  = 0x10
ATS_TAIL                    = 0x6f

ATS_PROTOCOL_CRC_KEY_VALUE  = 0xC659

#//Rx Receive index
ATS_SYNC_BYTE_INDEX         = 0
ATS_SYNC2_BYTE_INDEX        = 1
ATS_LEN1_BYTE_INDEX         = 2
ATS_LEN2_BYTE_INDEX         = 3
ATS_LEN3_BYTE_INDEX         = 4
ATS_LEN4_BYTE_INDEX         = 5

#//Rx Buffer Data Pasing index

#//Rx Buffer Frist Count Index
ATS_CMD_ID_BYTE_INDEX       = 0
ATS_CMD_TYPE_BYTE_INDEX     = 1
ATS_DATA_BYTE_INDEX         = 2
#//Rx Buffer Last Count Index
ATS_CRC16_HI_BYTE_INDEX     = 3
ATS_CRC16_LOW_BYTE_INDEX    = 2
ATS_TAIL_BYTE_INDEX         = 1


#/* LEN123 = 0 Not Used */
ATS_MAX_LENGTH              = 0xff

#/* 5 = Command ID(1) + Command Type(1) + CRC16(2) + Tail(1)*/
ATS_INCLUDE_BYTE_LENGTH_FIELD   = 5
#/* 6 = Header(2) + Length(4)  */
ATS_EXCLUDE_BYTE_LENGTH_FIELD   = 6

#/* 5 = Command ID(1) + Command Type(1) + CRC16(2) + Tail(1)*/
ATS_MIN_LENGTH                  = ATS_INCLUDE_BYTE_LENGTH_FIELD

ATS_MAX_DATA_LENGTH             = (ATS_MAX_LENGTH - ATS_MIN_LENGTH)


ATS_HEADER_BYTE_LENGTH          = 2
ATS_LENGTH_BYTE_LENGTH          = 4
#/* CRC16(2) + TAIL(1) */
ATS_TAIL_BYTE_LENGTH            = 3


PCTORX_FIFO_SIZE                = 5
LOGRX_FIFO_SIZE                 = 5
LOGRX_LINE_LENGTH_MAX           = 255



#/*0x12 Data-0*/
ATS_LCDSTATUSCHECK_STOP         = 0
ATS_LCDSTATUSCHECK_START        = 1

#/*0x12 Data-1*/
ATS_LCDSTATUSCHECK_TIME_RES_OFF = 0
ATS_LCDSTATUSCHECK_TIME_RES_ON  = 1


#/*0x13 Data-0*/
ATS_SOUNDOnOff_STOP             = 0
ATS_SOUNDOnOff_START            = 1

#/*0x13 Data-1*/
ATS_SOUNDOnOff_TIME_RES_OFF     = 0
ATS_SOUNDOnOff_TIME_RES_ON      = 1


#/*0x1f Data - 0*/
ATS_SYSTEM_RESET                = 1



#/* 0x30 Data-1 */
ATS_RELAY_OFF                   = 0
ATS_RELAY_ON                    = 1

#/* 0x33 Data-1 */
ATS_RELEASE_KEY                 = 0
ATS_SHORT_KEY                   = 1
ATS_LONG_KEY                    = 2
ATS_PRESS_KEY                   = 3

#/*0x36 Data - 1*/
ATS_CAN_KEY                     = 1
ATS_CAN_CLUPE                   = 2

#/* 0x62 Data-0 */
ATS_LCDCHK_STOP                 = 0
ATS_LCDCHK_START                = 1

#/* 0x63 Data-0 */
ATS_LCD_OFF_RES                 = 0
ATS_LCD_ON_RES                  = 1
ATS_LCD_CHECK_FAIL              = 2

#/* 0x64 Data-0 */
ATS_SOUNDCMP_STOP               = 0
ATS_SOUNDCMP_START              = 1

#/*0xE1 Data-0*/
ATS_SOUND_CHECK_OFF             = 0
ATS_SOUND_CHECK_ON              = 1

ATS_USERPIN1_CHECK_OFF          = 0
ATS_USERPIN1_CHECK_ON           = 1

ATS_USERPIN1_OFF_RES            = 0
ATS_USERPIN1_ON_RES             = 1
ATS_USERPIN1_CHECK_FAIL         = 2

ATS_VDT_MAX_DATA_BIT            = 247

#/***************************************************
#*                Enumeration                *
#****************************************************/

#typedef enum{
eATS_CMD_CODE_CONFIGURATION_GROUP_START         = 0x00
eATS_CMD_CODE_CFG_ATS_RELAY_INITIAL             = 0x10
eATS_CMD_CODE_CFG_KEY_RELEASE_TIME              = 0x11
eATS_CMD_CODE_CFG_LCD_STATUS_CHECK_TIME         = 0x12
eATS_CMD_CODE_CFG_AUDIO_STATUS_CHECK_TIME       = 0x13
eATS_CMD_CODE_CFG_ATS_RTC                       = 0x14
eATS_CMD_CODE_CFG_VOLTAGE_STATUS_CHECK_TIME     = 0x15
eATS_CMD_CODE_CFG_CURRENT_STATUS_CHECK_TIME     = 0x16
eATS_CMD_CODE_CFG_LCDCHANGE_STATUS              = 0x17
eATS_CMD_CODE_CFG_ATMS_VERSION                  = 0x18
eATS_CMD_CODE_CFG_ATS_CAN_TRNS_SET              = 0x1d
eATS_CMD_CODE_CFG_AUDIO_DATA_SET_TIME           = 0x1e
eATS_CMD_CODE_CFG_ATS_SYSTEM_RESET              = 0x1f
eATS_CMD_CODE_CFG_COMMON_RELAY_SET              = 0x22
eATA_CMD_CODE_CFG_COMMON_RELAY_GET              = 0x23
eATS_CMD_CODE_CONFIGURATION_GROUP_END           = 0x2F

eATS_CMD_CODE_CONTROL_COMMAND_GROUP_START       = 0x30
eATS_CMD_CODE_CMD_RELAY                         = 0x30
eATS_CMD_CODE_CMD_VOLTAGE                       = 0x31
eATS_CMD_CODE_CMD_USB_SELECTOR                  = 0x32
# eATS_CMD_CODE_CMD_KEY                           = 0x33
eATS_CMD_CODE_CMD_USB_SELECTOR_10CH_SWITCH_5CH  = 0x33
eATS_CMD_CODE_CMD_SPEED                         = 0x34
eATS_CMD_CODE_CMD_VR                            = 0x35
eATS_CMD_CODE_CMD_CAN_CONTROL                   = 0x36
eATS_CMD_CODE_CMD_ACC_VOLTAGE                   = 0x39
eATS_CMD_CODE_CMD_SINGLE_PULSE_CONTROL          = 0x40

eATS_CMD_CODE_CMD_THREE_BUTTON                  = 0x43
eATS_CMD_CODE_CMD_AUTOTEST_END                  = 0x44
eATS_CMD_CODE_CMD_AUTOTEST_START                = 0x45
eATS_CMD_CODE_CMD_THREE_BUTTON_REAR             = 0x46
eATS_CMD_CODE_CMD_NGT_TWO_BUTTON                = 0x47
eATS_CMD_CODE_CMD_TENKEY_MODULE_CONTROL         = 0x48
eATS_CMD_CODE_CMD_AIRBAG_CRASH_DETECT           = 0x4A

eATS_CMD_CODE_CONTROL_COMMAND_GROUP_END         = 0x5F

eATS_CMD_CODE_INFORMATION_GROUP_START           = 0x60
eATS_CMD_CODE_INFO_REQ_VERSION                  = 0x60
eATS_CMD_CODE_INFO_ACK_VERSION                  = 0x61
eATS_CMD_CODE_INFO_REQ_LCD_STATUS               = 0x62
eATS_CMD_CODE_INFO_ACK_LCD_STATUS               = 0x63
eATS_CMD_CODE_INFO_REQ_AUDIO_COMPARE_STATUS     = 0x64
eATS_CMD_CODE_INFO_ACK_AUDIO_COMPARE_STATUS     = 0x65

eATS_CMD_CODE_INFO_REQ_AUDIO_ONOFF_STATUS       = 0x66
eATS_CMD_CODE_INFO_ACK_AUDIO_ONOFF_STATUS       = 0x67

eATS_CMD_CODE_INFO_REQ_USERPIN1_STATUS          = 0x68
eATS_CMD_CODE_INFO_ACK_USERPIN1_STATUS          = 0x69

eATS_CMD_CODE_INFO_REQ_AUTOCALIBRATION          = 0x6a
eATS_CMD_CODE_INFO_ACK_AUTOCALIBRATION          = 0x6b

eATS_CMD_CODE_INFO_REQ_ATS_RELAY_STATUS         = 0xa0
eATS_CMD_CODE_INFO_ACK_ATS_RELAY_STATUS         = 0xa1
#eATS_CMD_CODE_INFO_REQ_KEY_RELEASE_TIME         = 0xa2
#eATS_CMD_CODE_INFO_ACK_KEY_RELEASE_TIME         = 0xa3
Request_File_To_SD_Card                         = 0xa2
Response_File_To_SD_Card                        = 0xa3
eATS_CMD_CODE_INFO_REQ_LCD_CHECK_STATUS         = 0xa4
eATS_CMD_CODE_INFO_ACK_LCD_CHECK_STATUS         = 0xa5
eATS_CMD_CODE_INFO_REQ_AUDIO_CHECK_STATUS       = 0xa6
eATS_CMD_CODE_INFO_ACK_AUDIO_CHECK_STATUS       = 0xa7

eATS_CMD_CODE_INFO_REQ_CAN1_RX_DATA             = 0xb0
eATS_CMD_CODE_INFO_ACK_CAN1_RX_DATA             = 0xb1
eATS_CMD_CODE_INFO_REQ_CAN2_RX_DATA             = 0xb2
eATS_CMD_CODE_INFO_ACK_CAN2_RX_DATA             = 0xb3
eATS_CMD_CODE_INFO_REQ_CAN1_TX_DATA             = 0xb4
eATS_CMD_CODE_INFO_ACK_CAN1_TX_DATA             = 0xb5
eATS_CMD_CODE_INFO_REQ_CAN2_TX_DATA             = 0xb6
eATS_CMD_CODE_INFO_ACK_CAN2_TX_DATA             = 0xb7
eATS_CMD_CODE_INFO_REQ_SEND_CSV_DATA            = 0xb8
eATS_CMD_CODE_INFO_ACK_SEND_CSV_DATA            = 0xb9
eATS_CMD_CODE_INFO_REQ_TELLTALE_LED             = 0xbc
eATS_CMD_CODE_INFO_ACK_TELLTALE_LED             = 0xbd


eATS_CMD_CODE_INFO_REQ_VOICE_FILE_INFO          = 0xa8
eATS_CMD_CODE_INFO_ACK_VOICE_FILE_INFO          = 0xa9
eATS_CMD_CODE_INFO_REQ_VOICE_FILE_LIST          = 0xaa
eATS_CMD_CODE_INFO_ACK_VOICE_FILE_LIST          = 0xab
eATS_CMD_CODE_INFO_REQ_SHIELD_BOX_CONTROL       = 0xac
eATS_CMD_CODE_INFO_ACK_SHIELD_BOX_CONTROL       = 0xad

eATS_CMD_CODE_INFO_REQ_VDT_LIST_CONTROL         = 0xae
eATS_CMD_CODE_INFO_ACK_VDT_LIST_CONTROL         = 0xaf

eATS_CMD_CODE_INFO_REQ_MT4N_CURRENT_CONTROL     = 0xc0
eATS_CMD_CODE_INFO_RES_MT4N_CURRENT_CONTROL     = 0xc1
eATS_CMD_CODE_INFO_REQ_KEY_OUT_VOLTAGE_CONTROL  = 0xc2
eATS_CMD_CODE_INFO_RES_KEY_OUT_VOLTAGE_CONTROL  = 0xc3
eATS_CMD_CODE_INFO_REQ_LIGHTSENSOR_RESULT       = 0xc4
eATS_CMD_CODE_INFO_ACK_LIGHTSENSOR_RESULT       = 0xc5
eATS_CMD_CODE_INFO_REQ_COMMON_RELAY_NAME_SET    = 0xc6
eATS_CMD_CODE_INFO_ACK_COMMON_RELAY_NAME_SET    = 0xc7
eATS_CMD_CODE_INFO_REQ_LED_PULSE                = 0xc8
eATS_CMD_CODE_INFO_ACK_LED_PULSE                = 0xc9

eATS_CMD_CODE_INFORMATION_GROUP_END             = 0xdF

eATS_CMD_CODE_STATUS_GROUP_START                = 0xe0
eATS_CMD_CODE_ST_LCD_STATUS                     = 0xe0
eATS_CMD_CODE_ST_AUDIO_STATUS                   = 0xe1
eATS_CMD_CODE_ST_VOLTAGE_STATUS                 = 0xe7
eATS_CMD_CODE_ST_CURRENT_STATUS                 = 0xe8
eATS_CMD_CODE_INFO_ACK_LCDCHANGE_STATUS         = 0xe9
eATS_CMD_CODE_INFO_STATUS_RELAY                 = 0xea
eATS_CMD_CODE_AUDIO_DATA_STATUS                 = 0xeb

eATS_CMD_CODE_ST_ACKNOWLEDGE                    = 0xFF
eATS_CMD_CODE_STATUS_GROUP_END                  = 0xFF
#} eATS_CMD_Type_t;

#/* 0x30  Data-0 */
#typedef enum{
eATS_RELAY_ILLUMINATION_PLUS                = 0
eATS_RELAY_ILLUMINATION_MINUS               = 1
eATS_RELAY_ACC                              = 2
eATS_RELAY_ALT                              = 3
eATS_RELAY_B_PLUS                           = 4
eATS_RELAY_REAR_CAMERA                      = 5
eATS_RELAY_PARKING                          = 6
eATS_RELAY_DOOR                             = 7
eATS_RELAY_AUTOLIGHT                        = 8
eATS_RELAY_AUX                              = 9
eATS_RELAY_MIC                              = 10 # 0xA
eATS_RELAY_ANT                              = 11 # 0xB
eATS_RELAY_USB1                             = 12 # 0xC
eATS_RELAY_USB2                             = 13 # 0xD
eATS_RELAY_ALARM_DETECT                     = 14 # 0xE
eATS_RELAY_KEYLESS_BOOT                     = 15 # 0xF
eATS_RELAY_IGN                              = 16 # 0x10
eATS_RELAY_AMP                              = 17 # 0x11
eATS_RELAY_CDINSERT                         = 18 # 0x12
# eATS_RELAY_USB_SW_FRONT1                    = 19 # 0x13
# eATS_RELAY_USB_SW_FRONT2                    = 20 # 0x14
# eATS_RELAY_USB_SW_REAR1                     = 21 # 0x15
# eATS_RELAY_USB_SW_REAR2                     = 22 # 0x16
# eATS_RELAY_USB_SW_OFF                       = 23 # 0x17
eATS_RELAY_AVNSDDETECT                      = 24 # 0x18
eATS_RELAY_B_GROUND                         = 25 # 0x19
# 0x1a SET - FRONT_USB_1
eATS_RELAY_BAT_USB_FRONT_1                  = 26
# 0x1b SET - FRONT_USB_2
eATS_RELAY_BAT_USB_FRONT_2                  = 27
# 0x1c OFF FRONT_USB
eATS_RELAY_BAT_USB_FRONT_OFF                = 28
# 0x1d SET - REAR_USB_1
eATS_RELAY_BAT_USB_REAR_1                   = 29
# 0x1e SET - REAR_USB_2
eATS_RELAY_BAT_USB_REAR_2                   = 30
# 0x1f OFF REAR_USB
eATS_RELAY_BAT_USB_REAR_OFF                 = 31
# 0x20 USB - PC
eATS_RELAY_BAT_USB_TO_PC                    = 32
# 0x21 SET - PC
eATS_RELAY_BAT_SET_TO_PC                    = 33
#} eATS_Relay_Type_t;
eATS_RELAY_FUEL                            = 34
eATS_RELAY_IGN3                            = 35
# eATS_RELAY_MPARKING                        = 36
eATS_RELAY_TELLTALE                        = 37
eATS_RELAY_DETENT                          = 38
eATS_RELAY_AUTO_PARKING                    = 39
eATS_RELAY_MTS                             = 40
eATS_RELAY_COM_ENABLE                      = 41
eATS_RELAY_N_POSITION                      = 42 #add 5th, relay7
eATS_RELAY_TRANS_DETECT                    = 43 #add 5th, relay8
eATS_RELAY_BUTTON_POWER_ON                 = 44 #//add 5th = MTS //44
eATS_RELAY_REV_IN_STATE_R15                = 45 #add 5th relay 15, only called by internal function.(Active High) //45
eATS_RELAY_REV_IN_STATE_R16                = 46 #add 5th relay 16, only called by internal function.(Active Low) //46
eATS_RELAY_DOOR_UNLOCK                     = 47 #add 5th, door unlock //47
eATS_RELAY_RC_IN                           = 48 #add 5th, for GEN11, relay9, 170718
eATS_RELAY_10                              = 49
eATS_RELAY_11                              = 50
eATS_RELAY_12                              = 51
eATS_RELAY_13                              = 52
eATS_RELAY_14                              = 53
# eATS_RELAY_E2                              = 54
# eATS_RELAY_E4                              = 55
eATS_RELAY_SWRC                            = 56
eATS_RELAY_MAX                             = 57

#/* 0x31 Data-0 */
#typedef enum{
eATS_STATUS_BATT5_0V        = 0
eATS_STATUS_BATT5_15V       = 1
eATS_STATUS_BATT5_25V       = 2
eATS_STATUS_BATT5_3V        = 3
eATS_STATUS_BATT5_4V        = 4
eATS_STATUS_BATT5_5V        = 5
eATS_STATUS_BATT5_6V        = 6
eATS_STATUS_BATT5_7V        = 7
eATS_STATUS_BATT5_8V        = 8
eATS_STATUS_BATT6_0V        = 9
eATS_STATUS_BATT6_1V        = 10
eATS_STATUS_BATT6_2V        = 11
eATS_STATUS_BATT6_4V        = 12
eATS_STATUS_BATT6_55V       = 13
eATS_STATUS_BATT6_7V        = 14
eATS_STATUS_BATT6_85V       = 15
eATS_STATUS_BATT7_0V        = 16
eATS_STATUS_BATT7_7V        = 17
eATS_STATUS_BATT7_8V        = 18
eATS_STATUS_BATT8_0V        = 19
eATS_STATUS_BATT8_2V        = 20
eATS_STATUS_BATT8_5V        = 21
eATS_STATUS_BATT8_7V        = 22
eATS_STATUS_BATT8_9V        = 23
eATS_STATUS_BATT9_2V        = 24
eATS_STATUS_BATT9_6V        = 25
eATS_STATUS_BATT9_9V        = 26
eATS_STATUS_BATT10_2V       = 27
eATS_STATUS_BATT10_6V       = 28
eATS_STATUS_BATT11_1V       = 29
eATS_STATUS_BATT11_6V       = 30
eATS_STATUS_BATT12_2V       = 31
eATS_STATUS_BATT12_8V       = 32
eATS_STATUS_BATT14_0V       = 33
eATS_STATUS_BATT14_7V       = 34
eATS_STATUS_BATT15_6V       = 35
eATS_STATUS_BATT15_65V      = 36
eATS_STATUS_BATT15_7V       = 37
eATS_STATUS_BATT15_8V       = 38
eATS_STATUS_BATT15_9V       = 39
eATS_STATUS_BATT16_0V       = 40
eATS_STATUS_BATT16_2V       = 41
eATS_STATUS_BATT16_3V       = 42
#} eATS_Volatage_t;

#/* 0x33 */
#typedef enum{
eATS_VOLUME_UP      = 0
eATS_VOLUME_DOWN    = 1
eATS_SEEK_UP        = 2
eATS_SEEK_DOWN      = 3
eATS_MODE           = 4
eATS_MUTE           = 5
eATS_ROTARYLEFT     = 6
eATS_ROTARYRIGHT    = 7
eATS_ROTARYPUSHUP   = 8
#}eATS_Key_t;

#/* 0xff */
#typedef enum{
eATS_PRTC_NO_ERROR                          = 0x00
eATS_PRTC_CRC_ERROR                         = 0x01
eATS_PRTC_LENGTH_ERROR                      = 0x02
eATS_PRTC_TAIL_ERROR                        = 0x03
eATS_PRTC_BAD_CMDID_ERROR                   = 0x04
eATS_PRTC_BAD_CMDTYPE_ERROR                 = 0x05
eATS_PRTC_BAD_PARAMETER_ERROR               = 0x06
#}eATS_PRTC_ERROR_t;

#typedef enum{
eATS_CAN_VALUE_VOLUME_ENCODER               = 0x01
eATS_CAN_VALUE_VOLUME_UP                    = 0x02
eATS_CAN_VALUE_VOLUME_DOWN                  = 0x03
eATS_CAN_VALUE_BT_CALL                      = 0x04
eATS_CAN_VALUE_BT_HANG_UP                   = 0x05
eATS_CAN_VALUE_MODE                         = 0x06
eATS_CAN_VALUE_MUTE                         = 0x07
eATS_CAN_VOICE_RECOGNITION                  = 0x08
eATS_CAN_VALUE_SEEK_UP                      = 0x09
eATS_CAN_VALUE_SEEK_DOWN                    = 0x0a
eATS_CAN_VALUE_MTS                          = 0x0b

#}eATS_CAN_CONTROL_KEY_VALUE;

#typedef enum{
eATS_CAN_TYPE_RELEASE                       = 0x00
eATS_CAN_TYPE_SHORT                         = 0x01
eATS_CAN_TYPE_LONG                          = 0x02
#}eATS_CAN_CONTROL_KEY_TYPE;


#/*0xac*/
#typedef enum{
eATS_SHIELD_BOX_REVERSE                         = 0
eATS_SHIELD_BOX_OPEN                            = 1
eATS_SHIELD_BOX_CLOSE                           = 2
eATS_SHIELD_BOX_STATUS                          = 3
eATS_SHIELD_BOX_PASS                            = 4
eATS_SHIELD_BOX_FAIL                            = 5
eATS_SHIELD_BOX_DUAL_HAND_ON                    = 6
eATS_SHIELD_BOX_DUAL_HAND_OFF                   = 7
eATS_SHIELD_BOX_DUAL_HAND_STATUS                = 8
eATS_SHIELD_BOX_MODEL                           = 9
eATS_SHIELD_BOX_SYSTEM_INIT                     = 10 # 0xA
eATS_SHIELD_BOX_COUNT                           = 11 # 0xB
eATS_SHIELD_BOX_PROBE_UPWARD                    = 12 # 0xC
eATS_SHIELD_BOX_PROBE_DOWNWARD                  = 13 # 0xD
eATS_SHIELD_BOX_PROBE_STATUS                    = 14 # 0xE
eATS_SHIELD_BOX_HANDLER_INWARD                  = 15 # 0xF
eATS_SHIELD_BOX_HANDLER_OUTWARD                 = 16 # 0x10
eATS_SHIELD_BOX_HANDLER_STATUS                  = 17 # 0x11
#} eATS_SHIELD_BOX_CONTROL_STATUS;

#/*0x ac -res */
#typedef enum{
eATS_SHIELD_BOX_RES_REVERSE                 = 0
eATS_SHIELD_BOX_RES_OK                      = 1
eATS_SHIELD_BOX_RES_LID_OPEN                = 2
eATS_SHIELD_BOX_RES_LID_CLOSE               = 3
eATS_SHIELD_BOX_RES_DUAL_HAND_ON            = 4
eATS_SHIELD_BOX_RES_DUAL_HAND_OFF           = 5
eATS_SHIELD_BOX_RES_LID_COUNT_NUMBER        = 6
eATS_SHIELD_BOX_RES_PROBE_UPWARD            = 7
eATS_SHIELD_BOX_RES_PROBE_DOWNWARD          = 8
eATS_SHIELD_BOX_RES_HANDLER_INWARD          = 9
eATS_SHIELD_BOX_RES_HANDLER_OUTWARD         = 10
eATS_SHIELD_BOX_RES_MODEL_NAME              = 11
eATS_SHIELD_BOX_RES_REVERSE_1               = 12
eATS_SHIELD_BOX_RES_REVERSE_2               = 13
eATS_SHIELD_BOX_RES_REVERSE_3               = 14
eATS_SHIELD_BOX_RES_REVERSE_4               = 15
eATS_SHIELD_BOX_RES_PROBE_DOWNWARD_ERROR    = 16
eATS_SHIELD_BOX_RES_HANDLER_INWARD_ERROR    = 17
eATS_SHIELD_BOX_RES_STATUS_ERROR            = 18
eATS_SHIELD_BOX_RES_SAFETY_ERROR            = 19
eATS_SHIELD_BOX_RES_COMMAND_ERROR           = 20
#} eATS_SHIELD_BOX_CONTROL_RES;

#typedef enum{
ATS_CAN_BAUDRATE_0          = 0x00
#//EU2.0 MIBPQ KO_ECN
ATS_CAN_BAUDRATE_100K       = 0x01
#//???
ATS_CAN_BAUDRATE_250K       = 0x00
#//MM2014(12) CID MIBMQB
ATS_CAN_BAUDRATE_500K       = 0x02
#//GEN10(1)
ATS_CAN_BAUDRATE_1000K      = 0x03
#//GEN10(2) BYOM1 BYOM2 (Single Wire)
ATS_CAN_BAUDRATE_33K        = 0x04
#}eATS_CAN_BAUDRATE;

CHECK_RESULT_TIMEOUT        = 0x1FFF
CHECK_RESULT_FAIL           = 0x1000
CHECK_RESULT_ERROR          = 0x1001  # add SeungWon.Jung
CHECK_RESULT_OK             = 0x1002
NOTI_LCD_FREEZ              = 0x2000
NOTI_LCD_UNFREEZ            = 0x2001


WHITE_VAL_MAX               = 0xFFFF
WHITE_VAL_MIN               = 160

BLACK_VAL_MAX               = 25
BLACK_VAL_MIN               = 2

LCDSTATSPROC_INTERVAL       = 2000

#typedef enum{
LCD_VALTYPE_UNKNOWN         = 0
LCD_VALTYPE_OFF             = 1
LCD_VALTYPE_BLACK           = 2
LCD_VALTYPE_MIDDLE          = 3
LCD_VALTYPE_WHITE           = 4
LCD_VALTYPE_MAX             = 5
#}LCD_VALTYPE;

# typdef enum{
COMMON_SWITCH_0             = 0
COMMON_SWITCH_1             = 1
COMMON_SWITCH_2             = 2
COMMON_SWITCH_3             = 3
COMMON_SWITCH_4             = 4
COMMON_SWITCH_5             = 5
COMMON_SWITCH_6             = 6
COMMON_SWITCH_7             = 7
COMMON_SWITCH_8             = 8
COMMON_SWITCH_9             = 9
COMMON_SWITCH_10             = 10
COMMON_SWITCH_11             = 11
COMMON_SWITCH_12             = 12
COMMON_SWITCH_13             = 13
COMMON_SWITCH_14             = 14
COMMON_SWITCH_15             = 15
COMMON_SWITCH_16             = 16
COMMON_SWITCH_17             = 17
COMMON_SWITCH_18             = 18
COMMON_SWITCH_19             = 19
COMMON_SWITCH_20             = 20
COMMON_SWITCH_21             = 21
COMMON_SWITCH_22             = 22
COMMON_SWITCH_23             = 23
COMMON_SWITCH_24             = 24
COMMON_SWITCH_25             = 25
COMMON_SWITCH_26             = 26
COMMON_SWITCH_27             = 27
COMMON_SWITCH_28             = 28
COMMON_SWITCH_29             = 29
COMMON_SWITCH_30             = 30

BAT_USB_FRONT_1              = 50
BAT_USB_FRONT_2              = 51
BAT_USB_FRONT_OFF            = 52
BAT_USB_REAR_1               = 53
BAT_USB_REAR_2               = 54
BAT_USB_REAR_OFF             = 55
BAT_USB_TO_PC                = 56
BAT_SET_TO_PC                = 57
COMMON_SWITCH_MAX            = 58
# }eCommon_Switch_Type_t

# typedef enum {
RELAY_STATUS_FIX            = 0
RELAY_STATUS_COMMON         =1
# }


