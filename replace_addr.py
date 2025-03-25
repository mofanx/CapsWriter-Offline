import re
import argparse

def replace_client_addr(file_path, new_addr):
    """
    替换文件中 ClientConfig 类的 addr 值。

    :param file_path: 配置文件的路径
    :param new_addr: 新的 addr 值
    """
    try:
        # 读取文件内容
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()

        # 使用正则表达式匹配 ClientConfig 类中的 addr 值
        pattern = r"(class ClientConfig:\s+addr\s*=\s*')[^']+(?='.*# Server 地址)"
        updated_content = re.sub(pattern, rf"\g<1>{new_addr}", content)

        # 如果内容有变化，则写回文件
        if updated_content != content:
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(updated_content)
            print(f"成功将 addr 替换为 {new_addr}")
        else:
            print("未找到需要替换的 addr 值，文件未修改。")

    except FileNotFoundError:
        print(f"错误：文件 {file_path} 未找到。")
    except Exception as e:
        print(f"发生错误：{e}")

def main():
    # 创建 ArgumentParser 对象
    parser = argparse.ArgumentParser(description="替换配置文件中的客户端 addr 值。")
    
    # 添加命令行参数
    parser.add_argument("new_addr", help="新的客户端 addr 值，例如 127.0.0.1")
    parser.add_argument("--file", default="/home/yan/ubuntu/project/python/CapsWriter-Offline-Linux/config.py",
                        help="配置文件的路径，默认为 config.py 的路径")
    
    # 解析命令行参数
    args = parser.parse_args()

    # 调用函数进行替换
    replace_client_addr(args.file, args.new_addr)

if __name__ == "__main__":
    main()