# coding: utf-8
"""
快捷键管理器（重构版）

统一管理多个快捷键，处理键盘和鼠标事件，支持：
1. 多快捷键并发处理
2. 防止不同按键互相干扰
3. restore 功能的防自捕获逻辑
4. hold_mode 和 click_mode 支持
"""
import time
from concurrent.futures import ThreadPoolExecutor
from platform import system
from typing import TYPE_CHECKING, Dict, List, Optional

import keyboard as keyboard_lib

from . import logger
from util.client.shortcut.key_mapper import *
from util.client.shortcut.key_mapper import KeyMapper
from util.client.shortcut.emulator import ShortcutEmulator
from util.client.shortcut.event_handler import ShortcutEventHandler
from util.client.shortcut.task import ShortcutTask

if TYPE_CHECKING:
    from util.client.shortcut.shortcut_config import Shortcut
    from util.client.state import ClientState



class ShortcutManager:
    """
    快捷键管理器

    统一管理多个快捷键，使用 pynput 监听键盘和鼠标事件。
    所有事件处理都在 win32_event_filter 中完成，确保高性能和低延迟。
    """

    def __init__(self, state: 'ClientState', shortcuts: List['Shortcut']):
        """
        初始化快捷键管理器

        Args:
            state: 客户端状态实例
            shortcuts: 快捷键配置列表
        """
        self.state = state
        self.shortcuts = shortcuts

        self._is_windows = system() == 'Windows'

        self.keyboard_listener = None
        self.mouse_listener = None

        self._keyboard_hooks = []
        self._combo_hook = None
        self._pressed_keys = set()
        self._combo_defs = []

        # 快捷键任务映射（key -> ShortcutTask）
        self.tasks: Dict[str, ShortcutTask] = {}

        # 线程池
        self._pool = ThreadPoolExecutor(max_workers=4)

        # 按键模拟器
        self._emulator = ShortcutEmulator()

        # 按键恢复状态追踪
        self._restoring_keys = set()

        # 事件处理器
        self._event_handler = ShortcutEventHandler(self.tasks, self._pool, self._emulator)

        # 初始化快捷键任务
        self._init_tasks()

    def _init_tasks(self) -> None:
        """初始化所有快捷键任务"""
        from config import ClientConfig as Config

        for shortcut in self.shortcuts:
            if not shortcut.enabled:
                continue

            task = ShortcutTask(shortcut, self.state)
            task._manager_ref = lambda: self  # 弱引用，用于回调
            task.pool = self._pool
            task.threshold = shortcut.get_threshold(Config.threshold)
            self.tasks[shortcut.key] = task

    # ========== 监听器创建 ==========

    def create_keyboard_filter(self):
        """创建键盘事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 KEYDOWN 和 KEYUP 消息
            if msg not in KEYBOARD_MESSAGES:
                return True

            key_name = KeyMapper.vk_to_name(data.vkCode)

            # 防自捕获检查
            if self._check_emulating(key_name, msg):
                return True
            if self._check_restoring(key_name, msg):
                return True

            # 查找匹配的快捷键
            if key_name not in self.tasks:
                return True

            task = self.tasks[key_name]

            # 处理按键事件
            if msg in KEY_DOWN_MESSAGES:
                self._event_handler.handle_keydown(key_name, task)
            elif msg in KEY_UP_MESSAGES:
                self._event_handler.handle_keyup(key_name, task)

            # 阻塞事件
            if task.shortcut.suppress and self.keyboard_listener:
                self.keyboard_listener.suppress_event()

            return True

        return win32_event_filter

    # ========== 非 Windows 监听回调 ==========

    def _keyboard_keydown(self, key_name: str) -> None:
        if self._emulator.is_emulating(key_name):
            return
        if self.is_restoring(key_name):
            return

        task = self.tasks.get(key_name)
        if not task:
            return

        self._event_handler.handle_keydown(key_name, task)

    def _keyboard_keyup(self, key_name: str) -> None:
        if self._emulator.is_emulating(key_name):
            self._emulator.clear_emulating_flag(key_name)
            return

        if self.is_restoring(key_name):
            self.clear_restoring_flag(key_name)
            return

        task = self.tasks.get(key_name)
        if not task:
            return

        self._event_handler.handle_keyup(key_name, task)

    def _setup_keyboard_lib_hooks(self) -> None:
        self._keyboard_hooks = []
        self._combo_defs = []
        self._pressed_keys = set()
        self._combo_hook = None

        for shortcut in self.shortcuts:
            if not shortcut.enabled:
                continue
            if shortcut.type != 'keyboard':
                continue

            key = shortcut.key
            if '+' in key:
                parts = [p.strip() for p in key.split('+') if p.strip()]
                combo_keys = {KeyMapper.internal_to_keyboard_lib_name(p) for p in parts}
                self._combo_defs.append({'key': key, 'keys': combo_keys, 'active': False, 'suppress': shortcut.suppress})
                continue

            lib_name = KeyMapper.internal_to_keyboard_lib_name(key)

            def _press_cb(e, key_name=key):
                _ = e
                self._keyboard_keydown(key_name)

            def _release_cb(e, key_name=key):
                _ = e
                self._keyboard_keyup(key_name)

            press_hook = keyboard_lib.on_press_key(lib_name, _press_cb, suppress=shortcut.suppress)
            release_hook = keyboard_lib.on_release_key(lib_name, _release_cb, suppress=shortcut.suppress)
            self._keyboard_hooks.append(press_hook)
            self._keyboard_hooks.append(release_hook)

        if self._combo_defs:
            if any(c['suppress'] for c in self._combo_defs):
                logger.warning('keyboard 库在组合键模式下无法精确 suppress，将忽略组合键的 suppress 配置')

            def _combo_hook_cb(e):
                if not e.name:
                    return
                name = str(e.name).lower()

                if e.event_type == 'down':
                    self._pressed_keys.add(name)
                elif e.event_type == 'up':
                    self._pressed_keys.discard(name)

                for combo in self._combo_defs:
                    active_now = combo['keys'].issubset(self._pressed_keys)
                    if active_now and not combo['active']:
                        combo['active'] = True
                        self._keyboard_keydown(combo['key'])
                    elif (not active_now) and combo['active']:
                        combo['active'] = False
                        self._keyboard_keyup(combo['key'])

            self._combo_hook = keyboard_lib.hook(_combo_hook_cb, suppress=False)

    def create_mouse_filter(self):
        """创建鼠标事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 XBUTTON 消息
            if msg not in MOUSE_MESSAGES:
                return True

            # 获取按键标识
            xbutton = (data.mouseData >> 16) & 0xFFFF
            button_name = 'x1' if xbutton == XBUTTON1 else 'x2'

            # 防自捕获检查
            if self._check_emulating(button_name, msg, is_mouse=True):
                return True

            # 查找匹配的快捷键
            if button_name not in self.tasks:
                return True

            task = self.tasks[button_name]

            # 处理鼠标事件
            if msg == WM_XBUTTONDOWN:
                self._event_handler.handle_keydown(button_name, task)
            elif msg == WM_XBUTTONUP:
                self._handle_mouse_keyup(button_name, task)

            # 阻塞事件
            if task.shortcut.suppress and self.mouse_listener:
                self.mouse_listener.suppress_event()

            return True

        return win32_event_filter

    def _handle_mouse_keyup(self, button_name: str, task) -> None:
        """处理鼠标按键释放事件"""
        # 单击模式
        if not task.shortcut.hold_mode:
            if task.pressed:
                task.pressed = False
                task.released = True
                task.event.set()
            return

        # 长按模式
        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(f"[{button_name}] 松开按键，持续时间: {duration:.3f}s")

        if duration < task.threshold:
            task.cancel()
            if task.shortcut.suppress:
                logger.debug(f"[{button_name}] 安排异步补发鼠标按键")
                self._pool.submit(self._emulator.emulate_mouse_click, button_name)
        else:
            task.finish()

    # ========== 按键恢复管理 ==========

    def schedule_restore(self, key: str) -> None:
        """
        安排按键恢复（延迟执行，避免在事件处理中阻塞）

        Args:
            key: 要恢复的按键

        注意：标志清除只在按键释放事件中处理（_check_restoring），
        避免在线程中提前清除导致主线程收到重复消息。
        """
        self._restoring_keys.add(key)

        def do_restore():
            import time
            time.sleep(0.05)  # 延迟 50ms
            self._emulator.emulate_key(key)
            self.clear_restoring_flag(key)

        self._pool.submit(do_restore)

    def is_restoring(self, key: str) -> bool:
        """检查是否正在恢复指定按键"""
        return key in self._restoring_keys

    def clear_restoring_flag(self, key: str) -> None:
        """清除恢复标志"""
        self._restoring_keys.discard(key)

    # ========== 防自捕获检查 ==========

    def _check_emulating(self, key_name: str, msg: int, is_mouse: bool = False) -> bool:
        """检查是否正在模拟按键"""
        if not self._emulator.is_emulating(key_name):
            return False

        # 松开时清除标志
        if is_mouse:
            if msg == WM_XBUTTONUP:
                self._emulator.clear_emulating_flag(key_name)
        else:
            if msg in (WM_KEYUP, WM_SYSKEYUP):
                self._emulator.clear_emulating_flag(key_name)

        return True  # 放行

    def _check_restoring(self, key_name: str, msg: int) -> bool:
        """检查是否正在恢复按键"""
        if not self.is_restoring(key_name):
            return False

        if msg in (WM_KEYUP, WM_SYSKEYUP):
            self.clear_restoring_flag(key_name)

        return True  # 放行

    # ========== 公共接口 ==========

    def start(self) -> None:
        """启动所有监听器"""
        has_keyboard = any(s.type == 'keyboard' for s in self.shortcuts if s.enabled)
        has_mouse = any(s.type == 'mouse' for s in self.shortcuts if s.enabled)

        if self._is_windows:
            if has_keyboard:
                from pynput import keyboard
                self.keyboard_listener = keyboard.Listener(
                    win32_event_filter=self.create_keyboard_filter()
                )
                self.keyboard_listener.start()
                logger.info("键盘监听器已启动")

            if has_mouse:
                from pynput import mouse
                self.mouse_listener = mouse.Listener(
                    win32_event_filter=self.create_mouse_filter()
                )
                self.mouse_listener.start()
                logger.info("鼠标监听器已启动")
        else:
            if has_mouse:
                logger.warning('keyboard 库不支持鼠标侧键监听，已忽略鼠标快捷键配置')

            if has_keyboard:
                try:
                    self._setup_keyboard_lib_hooks()
                    logger.info('键盘监听器已启动')
                except Exception as e:
                    logger.error(f'启动 keyboard 快捷键监听失败: {e}')
                    raise

        # 打印所有启用的快捷键
        for shortcut in self.shortcuts:
            if shortcut.enabled:
                mode = "长按" if shortcut.hold_mode else "单击"
                toggle = "可恢复" if shortcut.is_toggle_key() else "普通键"
                logger.info(f"  [{shortcut.key}] {mode}模式, 阻塞:{shortcut.suppress}, {toggle}")

    def stop(self) -> None:
        """停止所有监听器和清理资源"""
        if self._is_windows:
            if self.keyboard_listener:
                self.keyboard_listener.stop()
                logger.debug("键盘监听器已停止")

            if self.mouse_listener:
                self.mouse_listener.stop()
                logger.debug("鼠标监听器已停止")
        else:
            for h in list(self._keyboard_hooks):
                try:
                    keyboard_lib.unhook(h)
                except Exception:
                    pass
            self._keyboard_hooks = []

            if self._combo_hook is not None:
                try:
                    keyboard_lib.unhook(self._combo_hook)
                except Exception:
                    pass
                self._combo_hook = None

        # 取消所有任务
        for task in self.tasks.values():
            if task.is_recording:
                task.cancel()

        # 关闭线程池
        self._pool.shutdown(wait=False)
        logger.debug("快捷键管理器线程池已关闭")
