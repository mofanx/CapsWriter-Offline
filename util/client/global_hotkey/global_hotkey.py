# coding: utf-8
"""
全局快捷键管理器

使用 pynput GlobalHotKeys 实现全局快捷键监听，替代 keyboard 库。

使用示例:
    from util.client.global_hotkey import GlobalHotkeyManager

    manager = GlobalHotkeyManager()
    manager.register('<esc>', lambda: print('ESC pressed'))
    manager.start()
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

import keyboard as keyboard_lib

from . import logger



class GlobalHotkeyManager:
    """
    全局快捷键管理器

    使用 pynput GlobalHotKeys 实现，支持动态注册/注销快捷键。
    
    对比 keyboard 库的优势：
    - 与 pynput 的其他功能兼容
    - 不需要额外的依赖
    - 更好的跨平台支持
    """

    # 单例实例
    _instance: Optional['GlobalHotkeyManager'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'GlobalHotkeyManager':
        """单例模式"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self._hotkeys: Dict[str, Callable] = {}
        self._handles: Dict[str, int] = {}
        self._running = False
        self._initialized = True
        logger.debug("GlobalHotkeyManager 初始化完成")

    @staticmethod
    def _normalize_hotkey(key_str: str) -> str:
        key_str = (key_str or '').strip().lower()
        if not key_str:
            return ''

        key_str = key_str.replace('<', '').replace('>', '')
        key_str = key_str.replace('cmd', 'windows')
        key_str = key_str.replace('command', 'windows')
        key_str = key_str.replace('control', 'ctrl')
        key_str = key_str.replace(' ', '')
        key_str = key_str.replace('+', '+')
        return key_str

    def register(self, key_str: str, callback: Callable) -> None:
        """
        注册全局快捷键

        Args:
            key_str: 快捷键字符串，pynput 格式，如 '<esc>', '<ctrl>+<alt>+h'
            callback: 按下快捷键时的回调函数
        """
        self._hotkeys[key_str] = callback
        logger.debug(f"注册全局快捷键: {key_str}")
        
        # 如果已经在运行，重启监听器以应用新的快捷键
        if self._running:
            self._restart_listener()

    def unregister(self, key_str: str) -> bool:
        """
        注销全局快捷键

        Args:
            key_str: 快捷键字符串

        Returns:
            是否成功注销
        """
        if key_str in self._hotkeys:
            del self._hotkeys[key_str]
            logger.debug(f"注销全局快捷键: {key_str}")
            
            if self._running:
                self._restart_listener()
            return True
        return False

    def start(self) -> None:
        """启动快捷键监听"""
        if self._running:
            logger.debug("GlobalHotkeyManager 已在运行")
            return
        
        if not self._hotkeys:
            logger.warning("没有注册的快捷键，跳过启动")
            return
        
        self._running = True
        self._start_listener()
        logger.info(f"GlobalHotkeyManager 已启动，注册了 {len(self._hotkeys)} 个快捷键")

    def stop(self) -> None:
        """停止快捷键监听"""
        self._running = False
        for k, handle in list(self._handles.items()):
            try:
                keyboard_lib.remove_hotkey(handle)
            except Exception:
                pass
        self._handles.clear()
        logger.info("GlobalHotkeyManager 已停止")

    def _start_listener(self) -> None:
        """启动监听器"""
        if not self._hotkeys:
            return

        for key_str, cb in self._hotkeys.items():
            normalized = self._normalize_hotkey(key_str)
            if not normalized:
                continue
            try:
                handle = keyboard_lib.add_hotkey(normalized, cb, suppress=False, trigger_on_release=False)
                self._handles[key_str] = handle
            except Exception as e:
                logger.warning(f"注册全局快捷键失败: {key_str} ({normalized}) {e}")

        logger.debug(f"全局快捷键监听已启动: {list(self._handles.keys())}")

    def _restart_listener(self) -> None:
        """重启监听器（用于更新快捷键后）"""
        for k, handle in list(self._handles.items()):
            try:
                keyboard_lib.remove_hotkey(handle)
            except Exception:
                pass
        self._handles.clear()
        
        if self._running and self._hotkeys:
            self._start_listener()


# 全局单例实例
_global_hotkey_manager: Optional[GlobalHotkeyManager] = None


def get_global_hotkey_manager() -> GlobalHotkeyManager:
    """获取全局快捷键管理器单例"""
    global _global_hotkey_manager
    if _global_hotkey_manager is None:
        _global_hotkey_manager = GlobalHotkeyManager()
    return _global_hotkey_manager
