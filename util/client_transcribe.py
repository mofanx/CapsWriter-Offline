import asyncio
import base64
import json
import re
import sys
import time
import uuid
from pathlib import Path

import websockets

from util import srt_from_txt
from util.client_check_websocket import check_websocket
from util.client_cosmic import Cosmic, console
from util.client_hot_sub import hot_sub
from util.client_hot_update import observe_hot, update_hot_all
from config import ClientConfig as Config
from util.srt_from_txt import split_text_for_subtitle

async def transcribe_check(file: Path):
    # 检查连接
    if not await check_websocket():
        console.print("无法连接到服务端", style="bright_red")
        sys.exit()

    if not file.exists():
        console.print(f"文件不存在：{file}", style="bright_red")
        return False


async def transcribe_send(file: Path):
    # 获取连接
    websocket = Cosmic.websocket

    # 生成任务id
    task_id = str(uuid.uuid1())
    console.print(f"\n任务标识：{task_id}")
    console.print(f"    处理文件：{file}")

    # 获取音频数据，ffmpeg输出采样率16000，单声道，float32格式
    ffmpeg_cmd = [
        "ffmpeg",
        "-i",
        str(file),
        "-f",
        "f32le",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-",
    ]

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    console.print("    正在提取音频", end="\r")

    # 计算音频总长度
    audio_data = await process.stdout.read()
    audio_duration = len(audio_data) / 4 / 16000
    console.print(f"    音频长度：{audio_duration:.2f}s")

    # 分块大小，例如60秒
    chunk_size = 16000 * 4 * 60  # 16000采样率，4字节每个样本，60秒

    offset = 0
    time_start = time.time()
    while offset < len(audio_data):
        chunk_end = offset + chunk_size
        if chunk_end > len(audio_data):
            chunk_end = len(audio_data)
        is_final = True if chunk_end >= len(audio_data) else False
        data_chunk = audio_data[offset:chunk_end]
        time_frame = time_start + (offset / 4 / 16000)
        message = {
            "task_id": task_id,  # 任务ID
            "seg_duration": Config.file_seg_duration,  # 分段长度
            "seg_overlap": Config.file_seg_overlap,  # 分段重叠
            "is_final": is_final,  # 是否结束
            "time_start": time_start,  # 录音起始时间
            "time_frame": time_frame,  # 该帧时间
            "source": "file",  # 数据来源：从文件读的数据
            "data": base64.b64encode(data_chunk).decode("utf-8"),
        }
        offset = chunk_end
        progress = min(offset / 4 / 16000, audio_duration)
        try:
            await websocket.send(json.dumps(message))
            console.print(f"    发送进度：{progress:.2f}s", end="\r")
        except websockets.exceptions.ConnectionClosed as e:
            console.print(f"    连接断开，错误：{e}")
            # 处理连接断开的情况，例如重新连接或终止任务
            break
        if is_final:
            break

    # 等待ffmpeg进程结束
    await process.wait()


async def transcribe_recv(file: Path):
    # 更新热词
    update_hot_all()
    # 实时更新热词
    observer = observe_hot()

    # 获取连接
    websocket = Cosmic.websocket

    # 接收结果
    async for message in websocket:
        message = json.loads(message)
        console.print(f"    转录进度: {message['duration']:.2f}s", end="\r")
        if message["is_final"]:
            break

    # 解析结果
    text_merge = message["text"]
    # 热词替换
    text_merge = hot_sub(text_merge)
    # text_split = re.sub("[，。？]", "\n", text_merge)
    # text_split = re.sub("([，。？])", r"\1\n", text_merge)

    # 分割文本
    text_split = split_text_for_subtitle(text_merge)

    timestamps = [round(timestamp, 3) for timestamp in message["timestamps"]]
    tokens = message["tokens"]

    # 得到文件名
    json_filename = Path(file).with_suffix(".json")
    txt_filename = Path(file).with_suffix(".txt")
    merge_filename = Path(file).with_suffix(".merge.txt")

    # 写入结果
    with open(merge_filename, "w", encoding="utf-8") as f:
        f.write(text_merge)
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write(text_split)
    with open(json_filename, "w", encoding="utf-8") as f:
        json.dump({"timestamps": timestamps, "tokens": tokens}, f, ensure_ascii=False)
    srt_from_txt.one_task(txt_filename)

    process_duration = message["time_complete"] - message["time_start"]
    console.print(f"\033[K    处理耗时：{process_duration:.2f}s")
    # console.print(f"    识别结果：\n[green]{message['text']}")
    console.print(f"    识别结果：\n[green]{text_merge}")