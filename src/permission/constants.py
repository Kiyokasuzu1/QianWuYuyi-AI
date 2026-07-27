PERMISSION_LEVEL = {
    "NONE": "none",
    "READ": "read",
    "VIEW": "view",
    "CONTROL": "control",
}

PERMISSION_STAGE = {
    "STAGE_1": "stage_1",
    "STAGE_2": "stage_2",
    "STAGE_3": "stage_3",
}

PERMISSION_TYPE = {
    "INTERNAL_STATE": "internal_state",
    "SCREEN_VIEW": "screen_view",
    "SYSTEM_NOTIFICATION": "system_notification",
    "KEYBOARD": "keyboard",
    "MOUSE": "mouse",
    "FILE_OPERATION": "file_operation",
    "SYSTEM_COMMAND": "system_command",
}

STAGE_PERMISSIONS = {
    PERMISSION_STAGE["STAGE_1"]: [
        PERMISSION_TYPE["INTERNAL_STATE"],
    ],
    PERMISSION_STAGE["STAGE_2"]: [
        PERMISSION_TYPE["INTERNAL_STATE"],
        PERMISSION_TYPE["SCREEN_VIEW"],
        PERMISSION_TYPE["SYSTEM_NOTIFICATION"],
    ],
    PERMISSION_STAGE["STAGE_3"]: [
        PERMISSION_TYPE["INTERNAL_STATE"],
        PERMISSION_TYPE["SCREEN_VIEW"],
        PERMISSION_TYPE["SYSTEM_NOTIFICATION"],
        PERMISSION_TYPE["KEYBOARD"],
        PERMISSION_TYPE["MOUSE"],
        PERMISSION_TYPE["FILE_OPERATION"],
        PERMISSION_TYPE["SYSTEM_COMMAND"],
    ],
}

PERMISSION_DESCRIPTIONS = {
    PERMISSION_TYPE["INTERNAL_STATE"]: "查看内部状态（人格、情绪、记忆）",
    PERMISSION_TYPE["SCREEN_VIEW"]: "查看屏幕画面",
    PERMISSION_TYPE["SYSTEM_NOTIFICATION"]: "查看系统通知",
    PERMISSION_TYPE["KEYBOARD"]: "控制键盘输入",
    PERMISSION_TYPE["MOUSE"]: "控制鼠标操作",
    PERMISSION_TYPE["FILE_OPERATION"]: "文件操作（读写移动）",
    PERMISSION_TYPE["SYSTEM_COMMAND"]: "执行系统命令",
}