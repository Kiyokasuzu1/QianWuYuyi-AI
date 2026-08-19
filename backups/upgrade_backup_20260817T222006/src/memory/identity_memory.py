"""
IdentityMemory

羽依核心身份记忆

负责:
- 羽依是谁
- 用户是谁
- 双方关系
"""


import json
from pathlib import Path


class IdentityMemory:

    def __init__(
        self,
        path="data/identity_memory.json"
    ):

        self.path = Path(path)

        self.data = self.load()


    def load(self):

        if self.path.exists():

            with open(
                self.path,
                "r",
                encoding="utf-8"
            ) as f:

                return json.load(f)


        return {}


    def get_identity_prompt(self):

        if not self.data:
            return ""


        yuyi = self.data.get(
            "yuyi",
            {}
        )

        user = self.data.get(
            "user",
            {}
        )

        relation = self.data.get(
            "relationship",
            {}
        )


        return f"""
【羽依自身身份】

名字：
{yuyi.get("name")}

身份：
{yuyi.get("identity")}


【用户身份】

名字：
{user.get("name")}

昵称：
{user.get("nickname")}


【关系】

{relation.get("description")}

"""

