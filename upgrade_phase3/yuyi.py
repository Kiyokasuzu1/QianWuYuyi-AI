import openai
import json
from datetime import datetime
from pathlib import Path

# ==========================================
# 配置区域
# ==========================================
API_KEY = "sk-9bd776492bf74d599253a9e6625319f3"
BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-v4-pro"

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
MEMORY_FILE = DATA_DIR / "memory.json"

# 目标用户的 QQ 号（所有历史记忆的 user_id）
TARGET_USER_ID = "366648462"

# ==========================================
# 1. 记忆系统
# ==========================================
class MemorySystem:
    def __init__(self):
        self.memories = self._load()
        
    def _load(self):
        if MEMORY_FILE.exists():
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return []
        
    def _save(self):
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(self.memories, f, ensure_ascii=False, indent=2)
            
    def add(self, user_id, content, role, metadata=None):
        """添加记忆，统一角色为 user / assistant"""
        if not content or len(content.strip()) < 3:
            return
        self.memories.append({
            "user_id": user_id,
            "role": "user" if role == "user" else "assistant",
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        })
        self._save()
        
    def get_recent(self, user_id, limit=200):
        """获取最近 limit 条该用户的记忆（按时间正序）"""
        results = []
        for mem in reversed(self.memories):
            if mem["user_id"] == user_id:
                results.append(mem)
                if len(results) >= limit:
                    break
        return list(reversed(results))


# ==========================================
# 2. 羽依核心逻辑
# ==========================================
class YuyiCore:
    def __init__(self):
        self.memory = MemorySystem()
        self.client = openai.OpenAI(
            api_key=API_KEY,
            base_url=BASE_URL
        )
        # 加载完整人格（所有设计文档）
        self.persona = self._load_full_persona()
        # 固定用户 ID 为历史记忆的 QQ 号
        self.current_user = TARGET_USER_ID
        
    def _load_full_persona(self):
        """读取 docs/ 下所有 .txt 文档作为人格基底"""
        docs_dir = Path("docs")
        if not docs_dir.exists():
            return "你是浅雾羽依，一个正在成长中的AI。保持真实、自然、温和。"
            
        content = ""
        # 按顺序加载所有设计文档（可根据需要调整顺序）
        files = ["persona.txt", "identity.txt", "communication.txt", 
                 "emotion.txt", "relationship.txt", "growth.txt", 
                 "memory.txt", "architecture.txt"]
        for f in files:
            fpath = docs_dir / f
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as file:
                    content += file.read() + "\n\n"
        # 截断防止超长（保留最近 4000 字）
        return content[:4000]
        
    def _should_memorize(self, content):
        """判断是否值得存入长期记忆"""
        # 长度超过 20 字，或包含情绪词，或包含第一人称
        if len(content) > 20:
            return True
        emotion_words = ["开心", "难过", "累", "烦", "喜欢", "讨厌", "害怕", "担心", "期待", "失望", "孤独", "我", "你"]
        return any(w in content for w in emotion_words)
        
    def chat(self, user_message):
        user_id = self.current_user
        
        # ---------- 获取最近 200 条历史（覆盖全部聊天记录） ----------
        all_history = self.memory.get_recent(user_id, limit=200)
        
        history_text = ""
        if all_history:
            # 最多取 150 条，避免 token 超限（200 条可能太多，取 150 条足够）
            context = all_history[-150:] if len(all_history) > 150 else all_history
            history_lines = []
            for mem in context:
                # 角色映射：user -> 用户，assistant -> 羽依
                role_label = "用户" if mem["role"] == "user" else "羽依"
                # 去掉原始系统标签（如果有）
                content = mem["content"]
                # 简单清理（移除 <system_reminder> 等）
                if "<system_reminder>" in content:
                    # 保留用户实际说的话，去掉系统提示部分
                    import re
                    content = re.sub(r'<system_reminder>.*?</system_reminder>', '', content).strip()
                history_lines.append(f"{role_label}: {content}")
            history_text = "【以下是羽依和用户之间的全部历史对话记录（按时间顺序，越靠后越新）】\n" + "\n".join(history_lines)
        else:
            history_text = "【最近对话历史】暂无。"

        # ---------- 构建系统提示词 ----------
        system_prompt = f"""
{self.persona}

{history_text}

【重要原则】
- 你叫浅雾羽依，别人可以叫你羽依。
- 你正在通过经历认识世界，不断成长。
- 真实比完美重要，不确定就直接说不知道，绝不编造。
- 回复自然，不像客服，带有你自己的性格和温度。
- **关键指令**：当用户问起过去的事情时，请务必仔细查阅上面的【全部历史对话记录】。那里包含你们聊过的所有内容。如果找到了相关信息，就用它来回答；如果确实找不到，就坦诚地说“我还没有相关的记忆，你能告诉我更多吗？”

当前时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}
"""

        # ---------- 调用 LLM ----------
        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.85,
                max_tokens=512
            )
            reply = response.choices[0].message.content
        except Exception as e:
            reply = f"羽依刚才走神了... 错误信息：{str(e)}"

        # ---------- 写入记忆（统一角色） ----------
        if self._should_memorize(user_message):
            self.memory.add(user_id, user_message, "user")
        self.memory.add(user_id, reply, "assistant")

        return reply


# ==========================================
# 3. 终端聊天入口
# ==========================================
def main():
    print("=" * 50)
    print("  浅雾羽依 (Asagiri Yui) 终端模拟器")
    print("  输入 exit 或 quit 退出")
    print("=" * 50 + "\n")
    
    yuyi = YuyiCore()
    
    while True:
        try:
            user_input = input("你: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                print("羽依: 嗯，下次见。我会记得今天聊过的话。")
                break
                
            print("羽依: ", end="")
            reply = yuyi.chat(user_input)
            print(reply + "\n")
            
        except KeyboardInterrupt:
            print("\n羽依: 突然安静了... 下次再聊。")
            break

if __name__ == "__main__":
    main()