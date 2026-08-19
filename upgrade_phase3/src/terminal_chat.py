# src/terminal_chat.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from src.core import YuyiCore

def run_terminal():
    print("=" * 50)
    print("  浅雾羽依 (Asagiri Yui) 终端模拟器")
    print("  输入 'exit' 或 'quit' 退出")
    print("  输入 'clear' 清屏")
    print("=" * 50 + "\n")
    
    yuyi = YuyiCore()
    
    while True:
        try:
            user_input = input("你: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                print("羽依: 嗯，下次再聊。我会记得今天。")
                break
            if user_input.lower() == "clear":
                print("\033[2J\033[H")  # 清屏
                continue
                
            print("羽依: ", end="")
            reply = yuyi.chat(user_input)
            print(reply + "\n")
            
        except KeyboardInterrupt:
            print("\n羽依: 突然中断了... 下次见。")
            break
        except Exception as e:
            print(f"\n[错误] {e}\n")

if __name__ == "__main__":
    run_terminal()