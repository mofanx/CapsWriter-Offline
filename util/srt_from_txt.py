"""
脚本介绍：
    用 sherpa-onnx 生成的字幕，总归是会有一些缺陷
    例如有错字，分句不准

    所以除了自动生成的 srt 文件
    还额外生成了 txt 文件（每行一句），和 json 文件（包含每个字的时间戳）

    用户可以在识别完成后，手动修改 txt 文件，更正少量的错误，正确地分行
    然后调用这个脚本，处理 txt 文件

    脚本会找到同文件名的 json 文件，从里面得到字级时间戳，再按照 txt 里面的分行，
    生成正确的 srt 字幕
"""

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import List
import unicodedata
import srt
from rich import print


class Scout:
    def __init__(self):
        self.hit = 0
        self.miss = 0
        self.score = 0
        self.start = 0
        self.text = ""


def get_scout(line, words, cursor):
    """非递归方式匹配文本行与词语列表的最佳起始位置
    
    Args:
        line: 要匹配的文本行
        words: 词语列表
        cursor: 当前游标位置
        
    Returns:
        Scout对象或False（如果匹配失败）
    """
    words_num = len(words)
    
    # 预处理行文本，移除多余空格但保留一个空格
    processed_line = re.sub(r'\s+', ' ', line.strip())
    
    # 如果文本为空或游标越界，返回false
    if not processed_line or cursor >= words_num:
        return False
    
    # 使用迭代而非递归实现回退机制
    original_cursor = cursor
    max_attempts = 3  # 最大尝试次数
    attempt = 0
    
    while attempt < max_attempts:
        scout_list = []
        scout_num = 5  # 每次尝试的侦察兵数量
        
        # 生成多个侦察兵并评分
        for _ in range(scout_num + 1):
            # 新建一个侦察兵
            scout = Scout()
            scout.text = processed_line.lower()
            
            # 记录初始游标位置
            current_cursor = cursor + _
            
            # 找到起始点，尝试匹配第一个词
            start_found = False
            search_range = min(current_cursor + 20, words_num)
            
            for i in range(current_cursor, search_range):
                if i < words_num and words[i]["word"].lower().strip() and words[i]["word"].lower().strip() in scout.text:
                    current_cursor = i
                    start_found = True
                    break
            
            # 如果没找到起始点，尝试更宽松的匹配
            if not start_found:
                # 向前搜索直到找到匹配或越界
                while current_cursor < words_num and scout.text and words[current_cursor]["word"].lower().strip() not in scout.text:
                    current_cursor += 1
                
                # 如果越界了仍然没有找到匹配，跳过这个侦察兵
                if current_cursor >= words_num:
                    continue
            
            scout.start = current_cursor
            
            # 开始侦察，容错增加到8个词
            tolerance = 8
            temp_text = scout.text
            temp_cursor = current_cursor
            
            while temp_cursor < words_num and tolerance > 0:
                word = words[temp_cursor]["word"].lower().strip()
                if not word:  # 跳过空词
                    temp_cursor += 1
                    continue
                    
                if word in temp_text:
                    # 使用正则替换，确保只替换一次完整的词
                    temp_text = re.sub(re.escape(word), "", temp_text, 1)
                    scout.hit += 1
                    temp_cursor += 1
                    tolerance = 8  # 重置容错
                else:
                    tolerance -= 1
                    scout.miss += 1
                    temp_cursor += 1
                
                # 如果文本已经匹配完成，提前结束
                if not temp_text.strip():
                    break
            
            # 计算得分，增加命中权重
            scout.score = scout.hit * 2 - scout.miss
            
            # 只添加有效的侦察兵（至少有一个命中）
            if scout.hit > 0:
                scout_list.append(scout)
        
        # 如果有侦察兵找到了匹配，返回最佳的一个
        if scout_list:
            # 找到得分最好的侦察员
            best = scout_list[0]
            for scout in scout_list:
                if scout.score > best.score:
                    best = scout
            
            return best
        
        # 如果没有侦察兵找到匹配，尝试回退并重新尝试
        cursor = max(0, cursor - 30)  # 大幅回退
        attempt += 1
    
    # 如果所有尝试都失败，返回false
    if attempt >= max_attempts:
        print(f"[bold red]字幕匹配出现严重错误，经过{max_attempts}次尝试仍无法探察[/bold red]")
        return False
    
    return False


def match_words_to_line(line, words, start_index=0, max_window=50, max_search=100):
    """使用滑动窗口方法匹配文本行与词语列表
    
    Args:
        line: 要匹配的文本行
        words: 词语列表
        start_index: 开始搜索的索引
        max_window: 最大窗口大小
        max_search: 最大搜索范围
        
    Returns:
        (匹配开始索引, 匹配结束索引, 匹配分数) 的元组，如果匹配失败则返回 (None, None, -1)
    """
    if not line or not words or start_index >= len(words):
        return None, None, -1
    
    # 预处理行文本，移除多余空格但保留一个空格
    processed_line = re.sub(r'\s+', ' ', line.strip().lower())
    if not processed_line:
        return None, None, -1
    
    best_match = (None, None, -1)  # (开始索引, 结束索引, 分数)
    
    # 限制搜索范围，避免过度搜索
    search_end = min(start_index + max_search, len(words))
    
    # 尝试不同的起始位置
    for start_pos in range(start_index, search_end):
        # 跳过空词
        if not words[start_pos]["word"].lower().strip():
            continue
            
        # 检查第一个词是否在文本中
        first_word = words[start_pos]["word"].lower().strip()
        if first_word not in processed_line:
            continue
        
        # 找到潜在的起始位置，开始匹配
        temp_text = processed_line
        end_pos = start_pos
        hit_count = 0
        miss_count = 0
        
        # 在窗口范围内尝试匹配
        while end_pos < min(start_pos + max_window, len(words)) and miss_count < 8:
            word = words[end_pos]["word"].lower().strip()
            
            if not word:  # 跳过空词
                end_pos += 1
                continue
                
            if word in temp_text:
                # 使用正则替换，确保只替换一次完整的词
                temp_text = re.sub(re.escape(word), "", temp_text, 1)
                hit_count += 1
                end_pos += 1
                
                # 如果文本已经匹配完成，提前结束
                if not temp_text.strip():
                    break
            else:
                miss_count += 1
                end_pos += 1
        
        # 计算匹配分数
        score = hit_count * 2 - miss_count
        
        # 更新最佳匹配
        if score > best_match[2]:
            best_match = (start_pos, end_pos, score)
    
    return best_match


def lines_match_words(text_lines: List[str], words: List) -> List[srt.Subtitle]:
    """将文本行与词语列表匹配，生成字幕列表
    
    Args:
        text_lines: 文本行列表
        words: 词语列表，每个词包含start, end, word字段
        
    Returns:
        字幕列表
    """
    # 空的字幕列表
    subtitle_list = []

    cursor = 0  # 索引，指向最新已确认的下一个
    words_num = len(words)  # 词数，结束条件
    subtitle_index = 1
    last_end_time = 0  # 记录上一个字幕的结束时间，避免时间重叠
    
    # 预处理文本行，去除空行
    valid_text_lines = [line for line in text_lines if line.strip()]
    
    # 如果没有有效文本行或没有词语，返回空列表
    if not valid_text_lines or not words:
        print("[bold red]没有有效的文本行或词语列表为空[/bold red]")
        return []
    
    # 计算总时长，用于估算字幕时间
    total_duration = words[-1]["end"] - words[0]["start"]
    avg_duration = total_duration / len(valid_text_lines)
    
    for index, line in enumerate(valid_text_lines):
        # 先清除空行
        if not line.strip():
            continue

        # 记录原始游标位置，用于回溯
        original_cursor = cursor
        
        try:
            # 使用新的match_words_to_line函数进行匹配
            start_idx, end_idx, score = match_words_to_line(line, words, cursor, max_window=50, max_search=100)
            
            if start_idx is None or score <= 0:  # 匹配失败
                # 尝试回溯并重试
                # 如果是最后几行，可能需要更大范围回溯
                if index > len(valid_text_lines) * 0.7:
                    cursor = max(0, cursor - 50)  # 大幅回溯
                else:
                    cursor = max(0, cursor - 20)  # 适度回溯
                    
                # 重试一次
                start_idx, end_idx, score = match_words_to_line(line, words, cursor, max_window=70, max_search=150)
                
                # 如果仍然失败，尝试使用旧的get_scout函数
                if start_idx is None or score <= 0:
                    # 尝试使用旧的方法
                    scout = get_scout(line, words, cursor)
                    
                    if not scout:  # 如果仍然失败，使用估计的时间
                        print(f"[bold red]字幕行内容不匹配，使用估计时间: {line}[/bold red]")
                        
                        # 估计开始时间和结束时间
                        est_start = words[0]["start"] + index * avg_duration
                        est_end = est_start + min(len(line) * 0.1, 5.0)  # 根据文本长度估计持续时间
                        
                        # 确保时间不重叠
                        if est_start < last_end_time:
                            est_start = last_end_time + 0.1
                            est_end = est_start + min(len(line) * 0.1, 5.0)
                        
                        # 创建字幕
                        subtitle = srt.Subtitle(
                            index=subtitle_index,
                            content=line,
                            start=timedelta(seconds=est_start),
                            end=timedelta(seconds=est_end),
                        )
                        subtitle_list.append(subtitle)
                        subtitle_index += 1
                        
                        # 更新上一个字幕结束时间
                        last_end_time = est_end
                        
                        # 尝试向前移动游标
                        cursor = min(cursor + 10, words_num - 1)
                        continue
                    else:
                        # 使用get_scout的结果
                        start_idx = scout.start
                        score = scout.score
                        # 估计结束索引
                        end_idx = min(start_idx + 15, words_num)
            
            cursor = start_idx

            # 避免越界
            if cursor >= words_num:
                print(f"[bold red]字幕匹配越界，{cursor} >= {words_num}，使用估计时间[/bold red]")
                # 使用估计的时间
                est_start = words[0]["start"] + index * avg_duration
                est_end = est_start + min(len(line) * 0.1, 5.0)
                
                # 确保时间不重叠
                if est_start < last_end_time:
                    est_start = last_end_time + 0.1
                    est_end = est_start + min(len(line) * 0.1, 5.0)
                
                # 创建字幕
                subtitle = srt.Subtitle(
                    index=subtitle_index,
                    content=line,
                    start=timedelta(seconds=est_start),
                    end=timedelta(seconds=est_end),
                )
                subtitle_list.append(subtitle)
                subtitle_index += 1
                
                # 更新上一个字幕结束时间
                last_end_time = est_end
                
                # 尝试回溯并继续
                cursor = max(0, original_cursor - 10)
                continue

            # 初始化
            processed_line = re.sub(r'\s+', ' ', line.strip().lower())
            
            # 使用match_words_to_line的结果计算时间戳
            if start_idx is not None and end_idx is not None:
                # 使用匹配到的第一个词的开始时间作为字幕开始时间
                t1 = words[start_idx]["start"]
                
                # 使用匹配到的最后一个词的结束时间作为字幕结束时间
                # 确保 end_idx - 1 不会越界
                last_word_idx = min(end_idx - 1, len(words) - 1)
                t2 = words[last_word_idx]["end"]
                
                # 如果匹配分数低，可能需要向后多匹配几个词来确保字幕时间合理
                if score < 5 and last_word_idx < len(words) - 1:
                    # 尝试再匹配几个词
                    extra_match_count = 0
                    for i in range(last_word_idx + 1, min(last_word_idx + 5, words_num)):
                        if extra_match_count >= 3:  # 最多再匹配3个词
                            break
                        t2 = words[i]["end"]  # 更新结束时间
                        extra_match_count += 1
            else:
                # 如果没有有效的匹配结果，使用默认时间
                if cursor < words_num:
                    t1 = words[cursor]["start"]
                    t2 = words[min(cursor + 5, words_num - 1)]["end"]  # 使用后几个词的结束时间
                else:
                    # 如果游标越界，使用最后一个词的时间
                    t1 = words[max(0, words_num - 1)]["start"]
                    t2 = words[max(0, words_num - 1)]["end"]

            # 确保字幕时间不重叠
            if t1 < last_end_time:
                # 如果当前字幕的开始时间小于上一个字幕的结束时间
                # 则将当前字幕的开始时间设置为上一个字幕的结束时间加0.1秒
                t1 = last_end_time + 0.1
                # 同时调整结束时间，保持原有持续时间
                t2 = t2 + (t1 - last_end_time)
            
            # 确保字幕持续时间合理（至少0.8秒，最多8秒）
            duration = t2 - t1
            if duration < 0.8:
                t2 = t1 + 0.8  # 增加最小持续时间到0.8秒
            elif duration > 8:
                t2 = t1 + 8  # 减少最大持续时间到8秒
                
            # 更新上一个字幕结束时间
            last_end_time = t2

            # 新建字幕
            subtitle = srt.Subtitle(
                index=subtitle_index,
                content=line,
                start=timedelta(seconds=t1),
                end=timedelta(seconds=t2),
            )
            subtitle_list.append(subtitle)
            subtitle_index += 1

            # 如果本轮侦察评分不优秀，或者是最后几行，下一句应当适度回溯
            if score <= 0 or index > len(valid_text_lines) * 0.7:
                cursor = max(0, cursor - 10)  # 适度回溯
                
        except Exception as e:
            # 捕获所有异常，确保程序不会崩溃
            print(f"[bold red]处理字幕行时发生错误: {str(e)}[/bold red]")
            print(f"[bold red]问题行: {line}[/bold red]")
            
            # 使用估计的时间
            est_start = words[0]["start"] + index * avg_duration
            est_end = est_start + min(len(line) * 0.1, 5.0)
            
            # 确保时间不重叠
            if est_start < last_end_time:
                est_start = last_end_time + 0.1
                est_end = est_start + min(len(line) * 0.1, 5.0)
            
            # 创建字幕
            subtitle = srt.Subtitle(
                index=subtitle_index,
                content=line,
                start=timedelta(seconds=est_start),
                end=timedelta(seconds=est_end),
            )
            subtitle_list.append(subtitle)
            subtitle_index += 1
            
            # 更新上一个字幕结束时间
            last_end_time = est_end
            
            # 尝试向前移动游标
            cursor = min(cursor + 10, words_num - 1)

    return subtitle_list


def get_words(json_file: Path) -> list:
    # 读取分词 json 文件
    with open(json_file, "r", encoding="utf-8") as f:
        json_info = json.load(f)

    # 获取带有时间戳的分词列表
    # 预处理tokens，处理可能的特殊符号
    processed_tokens = []
    for token in json_info["tokens"]:
        # 处理特殊符号，如@、#等
        processed_token = token.replace("@", "").strip()
        processed_tokens.append(processed_token)

    # 创建words列表，包含时间戳信息
    words = [
        {"word": token, "start": timestamp, "end": timestamp + 0.2}
        for (timestamp, token) in zip(json_info["timestamps"], processed_tokens)
    ]

    # 确保时间戳的连续性和合理性
    for i in range(len(words) - 1):
        # 确保当前词的结束时间不超过下一个词的开始时间
        words[i]["end"] = min(words[i]["end"], words[i + 1]["start"])
        
        # 确保每个词至少有一个最小持续时间（例如0.1秒）
        min_duration = 0.1
        if words[i]["end"] - words[i]["start"] < min_duration:
            words[i]["end"] = words[i]["start"] + min_duration
            # 确保不会超过下一个词的开始时间
            if i < len(words) - 1:
                words[i]["end"] = min(words[i]["end"], words[i + 1]["start"])

    return words


def get_lines(txt_file: Path) -> List[str]:
    # 读取分好行的字幕
    with open(txt_file, "r", encoding="utf-8") as f:
        text_lines = f.readlines()
    return text_lines


def one_task(media_file: Path):
    """处理一个媒体文件，生成字幕
    
    Args:
        media_file: 媒体文件路径
        
    Returns:
        bool: 处理是否成功
    """
    print(f"\n[bold]处理文件: {media_file}[/bold]")

    # 解决路径问题，确保使用绝对路径
    try:
        media_file = Path(media_file).resolve()
    except Exception as e:
        print(f"[bold red]无效的文件路径: {media_file}, 错误: {str(e)}[/bold red]")
        return False

    # 获取同名的 txt 和 json 文件
    txt_file = media_file.with_suffix(".txt")
    json_file = media_file.with_suffix(".json")
    srt_file = media_file.with_suffix(".srt")

    # 检查文件是否存在
    if not txt_file.exists():
        print(f"[bold red]错误: {txt_file} 不存在[/bold red]")
        return False
    if not json_file.exists():
        print(f"[bold red]错误: {json_file} 不存在[/bold red]")
        return False

    try:
        # 读取 txt 文件内容，按行分割
        try:
            with open(txt_file, "r", encoding="utf-8") as f:
                text_content = f.read()
                # 确保使用统一的换行符
                text_content = text_content.replace('\r\n', '\n')
                text_lines = text_content.splitlines()
                
            if not text_lines:
                print(f"[bold red]警告: {txt_file} 文件为空[/bold red]")
                return False
        except UnicodeDecodeError:
            # 尝试使用其他编码
            try:
                with open(txt_file, "r", encoding="gbk") as f:
                    text_content = f.read()
                    text_content = text_content.replace('\r\n', '\n')
                    text_lines = text_content.splitlines()
            except Exception as e:
                print(f"[bold red]读取 {txt_file} 时发生编码错误: {str(e)}[/bold red]")
                return False
        except Exception as e:
            print(f"[bold red]读取 {txt_file} 时发生错误: {str(e)}[/bold red]")
            return False

        # 获取带有时间戳的分词列表
        try:
            words = get_words(json_file)
            
            if not words:
                print(f"[bold red]错误: {json_file} 中没有有效的词语数据[/bold red]")
                return False
        except json.JSONDecodeError as e:
            print(f"[bold red]JSON 解析错误: {json_file} 不是有效的 JSON 文件, 错误: {str(e)}[/bold red]")
            return False
        except Exception as e:
            print(f"[bold red]读取 {json_file} 时发生错误: {str(e)}[/bold red]")
            return False

        # 匹配行与词，生成字幕列表
        try:
            subtitle_list = lines_match_words(text_lines, words)
            
            if not subtitle_list:
                print(f"[bold red]错误: 未能生成有效的字幕列表[/bold red]")
                return False
        except Exception as e:
            print(f"[bold red]生成字幕时发生错误: {str(e)}[/bold red]")
            return False

        # 写入 srt 文件
        try:
            with open(srt_file, "w", encoding="utf-8") as f:
                f.write(srt.compose(subtitle_list))
        except Exception as e:
            print(f"[bold red]写入 {srt_file} 时发生错误: {str(e)}[/bold red]")
            return False

        print(f"[bold green]成功生成字幕文件: {srt_file}[/bold green]")
        print(f"生成了 {len(subtitle_list)} 条字幕")
        return True
        
    except Exception as e:
        print(f"[bold red]处理文件时发生未知错误: {str(e)}[/bold red]")
        import traceback
        print(traceback.format_exc())
        return False


def main(files: List[Path]):
    """处理多个媒体文件，生成字幕
    
    Args:
        files: 媒体文件路径列表
    """
    if not files:
        print("[bold red]错误: 没有提供文件路径[/bold red]")
        return
        
    success_count = 0
    fail_count = 0
    
    for file in files:
        try:
            result = one_task(file)
            if result:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"[bold red]处理 {file} 时发生未捕获的错误: {str(e)}[/bold red]")
            fail_count += 1
    
    # 打印总结信息
    print("\n[bold]===== 处理完成 =====[/bold]")
    print(f"总共处理: {len(files)} 个文件")
    print(f"[bold green]成功: {success_count} 个[/bold green]")
    if fail_count > 0:
        print(f"[bold red]失败: {fail_count} 个[/bold red]")
    else:
        print("[bold green]全部处理成功！[/bold green]")


def split_text_for_subtitle(text, max_length=42):
    """
    根据字幕规范分段多语言文本，返回双换行符连接的分段结果
    
    参数:
    text (str): 输入文本
    max_length (int): 单行最大字符宽度（默认40）
    
    返回:
    str: 用两个换行符分隔的字幕段落
    """
    punctuation = r'[\.\?\!\。\？\！\,\，\;\；\:\：\—\…\-\–]'
    word_break_pattern = re.compile(r'(\s+|{})'.format(punctuation))
    
    def get_char_width(char):
        # 统一字符宽度计算 [[1]][[10]]
        return 2 if unicodedata.east_asian_width(char) in ('F', 'W') else 1

    def find_optimal_split(text):
        # 查找最佳分割点 [[3]][[9]]
        positions = []
        current_width = 0
        
        for i, char in enumerate(text):
            current_width += get_char_width(char)
            
            # 记录可能的分割点
            if word_break_pattern.match(char):
                positions.append((i, current_width))
                
            # 超过最大长度时触发分割
            if current_width > max_length:
                if positions:
                    return positions[-1][0] + 1
                # 回退到最大长度一半的分割 [[3]][[7]]
                return max(1, i - (current_width - max_length//2)//get_char_width(char))
        
        return len(text)

    segments = []
    remaining = text.strip()
    
    while remaining:
        # 优先标点分割策略 [[1]][[10]]
        first_punct = re.search(punctuation, remaining)
        if first_punct:
            punct_pos = first_punct.start() + 1
            candidate = remaining[:punct_pos]
            # 检查宽度是否符合 [[4]]
            if sum(get_char_width(c) for c in candidate) <= max_length:
                segments.append(candidate)
                remaining = remaining[punct_pos:].lstrip()
                continue
        
        # 执行智能分割 [[3]][[9]]
        split_pos = find_optimal_split(remaining)
        segments.append(remaining[:split_pos].strip())
        remaining = remaining[split_pos:].lstrip()
    
    return '\n\n'.join(segments)




if __name__ == "__main__":
    main([Path(r"C:\Users\user0\Downloads\武林外传.E01-E04.DVDRip.x264.AC3-CMCT.txt")])

    # 将一个合并的文本文件按标点符号拆分成多行，并保存到新的 .txt 文件中

    # merge_filename = Path(
    #     r"C:\Users\user0\Downloads\武林外传.E01-E04.DVDRip.x264.AC3-CMCT.merge.txt"
    # )
    # txt_filename = Path(
    #     r"C:\Users\user0\Downloads\武林外传.E01-E04.DVDRip.x264.AC3-CMCT.txt"
    # )
    # with open(merge_filename, "r", encoding="utf-8") as f:
    #     text_merge = f.read()
    # # text_split = re.sub("[，。？]", "\n", text_merge)
    # text_split = re.sub("([，。？])", r"\1\n", text_merge)

    # with open(txt_filename, "w", encoding="utf-8") as f:
    #     f.write(text_split)