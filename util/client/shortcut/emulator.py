# coding: utf-8
"""
按键模拟器

负责异步模拟键盘和鼠标按键输入
"""

import keyboard as keyboard_lib
from . import logger
from util.client.shortcut.key_mapper import KeyMapper



class ShortcutEmulator:
    """
    快捷键模拟器

    使用常驻的 controller 对象，避免重复创建开销
    """

    def __init__(self):
        """初始化模拟器"""
        self._emulating_keys = set()

    def is_emulating(self, key_name: str) -> bool:
        """检查是否正在模拟指定按键"""
        return key_name in self._emulating_keys

    def clear_emulating_flag(self, key_name: str) -> None:
        """清除模拟标志"""
        self._emulating_keys.discard(key_name)

    def emulate_key(self, key_name: str) -> None:
        """
        异步模拟键盘按键

        Args:
            key_name: 按键名称（如 'caps_lock', 'f12'）
        """
        self._emulating_keys.add(key_name)

        lib_name = KeyMapper.internal_to_keyboard_lib_name(key_name)
        try:
            keyboard_lib.press_and_release(lib_name)
            logger.debug(f"[{key_name}] 补发按键成功")
        except Exception as e:
            logger.warning(f"[{key_name}] 补发按键失败: {e}")

    def emulate_mouse_click(self, button_name: str) -> None:
        """
        异步模拟鼠标按键

        Args:
            button_name: 鼠标按键名称（'x1' 或 'x2'）
        """
        self._emulating_keys.add(button_name)

        logger.warning(f"[{button_name}] keyboard 库不支持鼠标按键模拟，跳过补发")
