# coding: utf-8
"""
文本输出模块

提供 TextOutput 类用于将识别结果输出到当前窗口。
"""

from __future__ import annotations

import asyncio
import platform
from typing import Optional
import re

import keyboard
import pyclip

from config_client import ClientConfig as Config
from . import logger



class TextOutput:
    """
    文本输出器
    
    提供文本输出功能，支持模拟打字和粘贴两种方式。
    """
    
    @staticmethod
    def strip_punc(text: str) -> str:
        """
        消除末尾最后一个标点
        
        Args:
            text: 原始文本
            
        Returns:
            去除末尾标点后的文本
        """
        if not text or not Config.trash_punc:
            return text
        clean_text = re.sub(f"(?<=.)[{Config.trash_punc}]$", "", text)
        return clean_text
    
    async def output(self, text: str, paste: Optional[bool] = None) -> None:
        """
        输出识别结果
        
        根据配置选择使用模拟打字或粘贴方式输出文本。
        
        Args:
            text: 要输出的文本
            paste: 是否使用粘贴方式（None 表示使用配置值）
        """
        if not text:
            return
        
        # 确定输出方式
        if paste is None:
            paste = Config.paste
        
        if paste:
            await self._paste_text(text)
        else:
            self._type_text(text)
    
    async def _paste_text(self, text: str) -> None:
        """
        通过粘贴方式输出文本
        
        Args:
            text: 要粘贴的文本
        """
        logger.debug(f"使用粘贴方式输出文本，长度: {len(text)}")
        
        # 保存剪贴板
        try:
            temp = pyclip.paste().decode('utf-8')
        except Exception:
            temp = ''
        
        # 复制结果
        pyclip.copy(text)
        
        if platform.system() == 'Darwin':
            # macOS: Command+V
            keyboard.press_and_release('command+v')
        else:
            # Windows/Linux: Ctrl+V
            keyboard.press_and_release('ctrl+v')
        
        logger.debug("已发送粘贴命令 (Ctrl+V)")
        
        # 还原剪贴板
        if Config.restore_clip:
            await asyncio.sleep(0.1)
            pyclip.copy(temp)
            logger.debug("剪贴板已恢复")
    
    def _type_text(self, text: str) -> None:
        """
        通过模拟打字方式输出文本

        使用 keyboard.write 替代 pynput.keyboard.Controller.type()，
        避免与中文输入法冲突。

        Args:
            text: 要输出的文本
        """
        logger.debug(f"使用打字方式输出文本，长度: {len(text)}")
        try:
            keyboard.write(text)
        except Exception as e:
            logger.warning(f"keyboard.write failed, fallback to paste. err={e}")
            pyclip.copy(text)
            if platform.system() == 'Darwin':
                keyboard.press_and_release('command+v')
            else:
                keyboard.press_and_release('ctrl+v')
