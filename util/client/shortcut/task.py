# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

import asyncio
import time
from platform import system
from threading import Event
from typing import TYPE_CHECKING, Optional

from . import logger
from util.tools.my_status import Status

if TYPE_CHECKING:
    from util.client.shortcut.shortcut_config import Shortcut
    from util.client.state import ClientState
    from util.client.audio.recorder import AudioRecorder



class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    def __init__(self, shortcut: 'Shortcut', state: 'ClientState', recorder_class=None):
        """
        初始化快捷键任务

        Args:
            shortcut: 快捷键配置
            state: 客户端状态实例
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.shortcut = shortcut
        self.state = state
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[asyncio.Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # 线程池（用于 countdown）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

        # 锁定键（CapsLock/NumLock/ScrollLock）状态恢复：记录触发前的状态
        self._toggle_state_at_press: Optional[bool] = None

    def _get_recorder(self) -> 'AudioRecorder':
        """获取 AudioRecorder 实例"""
        if self._recorder_class is None:
            from util.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        return self._recorder_class(self.state)

    def launch(self) -> None:
        """启动录音任务"""
        logger.info(f"[{self.shortcut.key}] 触发：开始录音")

        if self.shortcut.is_toggle_key() and system() != 'Windows':
            try:
                import keyboard as keyboard_lib
                from util.client.shortcut.key_mapper import KeyMapper
                lib_name = KeyMapper.internal_to_keyboard_lib_name(self.shortcut.key)
                self._toggle_state_at_press = bool(keyboard_lib.is_toggled(lib_name))
            except Exception:
                self._toggle_state_at_press = None

        # 记录开始时间
        self.recording_start_time = time.time()
        self.is_recording = True

        # 将开始标志放入队列
        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({'type': 'begin', 'time': self.recording_start_time, 'data': None}),
            self.state.loop
        )

        # 更新录音状态
        self.state.start_recording(self.recording_start_time)

        # 打印动画：正在录音
        self._status.start()

        # 启动识别任务
        recorder = self._get_recorder()
        self.task = asyncio.run_coroutine_threadsafe(
            recorder.record_and_send(),
            self.state.loop,
        )

    def cancel(self) -> None:
        """取消录音任务（时间过短）"""
        logger.debug(f"[{self.shortcut.key}] 取消录音任务（时间过短）")

        self.is_recording = False
        self.state.stop_recording()
        self._status.stop()

        self.task.cancel()
        self.task = None

        if self.shortcut.is_toggle_key():
            self._restore_key_if_needed()

    def finish(self) -> None:
        """完成录音任务"""
        logger.info(f"[{self.shortcut.key}] 释放：完成录音")

        self.is_recording = False
        self.state.stop_recording()
        self._status.stop()

        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({
                'type': 'finish',
                'time': time.time(),
                'data': None
            }),
            self.state.loop
        )

        if self.shortcut.is_toggle_key():
            self._restore_key_if_needed()

    def _restore_key_if_needed(self) -> None:
        """根据当前平台与锁定键状态决定是否需要恢复。"""
        if system() == 'Windows':
            # Windows 下 suppress 能可靠阻塞 CapsLock，维持原逻辑
            if not self.shortcut.suppress:
                self._restore_key()
            return

        # Linux/Wayland 下 suppress 可能无效，必须以实际 toggled 状态为准
        try:
            import keyboard as keyboard_lib
            from util.client.shortcut.key_mapper import KeyMapper
            lib_name = KeyMapper.internal_to_keyboard_lib_name(self.shortcut.key)
            current = bool(keyboard_lib.is_toggled(lib_name))
        except Exception:
            current = None

        if self._toggle_state_at_press is None or current is None:
            # 无法读取状态时，宁可恢复一次，避免“按一下后状态被切走”的体验
            self._restore_key()
            return

        if current != self._toggle_state_at_press:
            self._restore_key()

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        # 通知管理器执行 restore
        # 防自捕获：管理器会设置 flag 再发送按键
        manager = self._manager_ref()
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")
