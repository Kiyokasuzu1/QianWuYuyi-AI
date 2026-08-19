"""
屏幕分析模块

负责 OCR 文字提取和描述生成。
Tesseract 不可用时降级返回空，不报错。
"""

from __future__ import annotations
import base64
import io
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ScreenAnalyzer:
    """屏幕分析器 —— OCR 提取 + 描述生成"""

    def __init__(self, lang: str = "chi_sim+eng", max_text_length: int = 2000):
        """
        Args:
            lang: OCR 语言（需要安装对应的 Tesseract 语言包）
            max_text_length: 提取文字的最大长度（超出截断）
        """
        self.lang = lang
        self.max_text_length = max_text_length
        self._tesseract_available = None  # None = 还没检测过

    def _check_tesseract(self) -> bool:
        """检测 Tesseract 是否可用"""
        if self._tesseract_available is not None:
            return self._tesseract_available

        try:
            import pytesseract
            import os

            possible_paths = [
                os.environ.get("TESSERACT_CMD"),
                r"C:\Users\29553\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\bin\tesseract.cmd",
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                r"C:\Users\29553\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
            ]

            for path in possible_paths:
                if path and os.path.exists(path):
                    pytesseract.pytesseract.tesseract_cmd = path
                    break

            pytesseract.get_tesseract_version()
            self._tesseract_available = True
            logger.info(f"Tesseract OCR 可用，路径: {pytesseract.pytesseract.tesseract_cmd}")
        except ImportError:
            self._tesseract_available = False
            logger.warning("pytesseract 库未安装，OCR 功能不可用")
        except Exception as e:
            self._tesseract_available = False
            logger.warning(f"Tesseract 程序未安装或配置错误: {e}")
            logger.warning("OCR 功能将降级为返回空文本")

        return self._tesseract_available

    def analyze(self, image_base64: str) -> Dict:
        """
        分析截图，提取文字并生成描述

        Args:
            image_base64: base64 编码的图片

        Returns:
            {
                "text": "提取的文字",
                "description": "供 prompt 注入的描述",
                "ocr_success": True/False,
            }
        """
        if not image_base64:
            return self._empty_result("无图片数据")

        # 解码图片
        try:
            from PIL import Image
            image_data = base64.b64decode(image_base64)
            image = Image.open(io.BytesIO(image_data))
        except ImportError:
            logger.warning("Pillow 库未安装，无法处理图片")
            return self._empty_result("Pillow 未安装")
        except Exception as e:
            logger.warning(f"图片解码失败: {e}")
            return self._empty_result(f"图片解码失败: {e}")

        # OCR 提取文字
        text = self.extract_text(image)

        # 生成描述
        description = self.generate_description(text)

        return {
            "text": text,
            "description": description,
            "ocr_success": bool(text),
        }

    def extract_text(self, image) -> str:
        """
        OCR 提取文字

        Args:
            image: PIL.Image 对象

        Returns:
            提取的文字字符串，失败时返回空
        """
        if not self._check_tesseract():
            return ""

        try:
            import pytesseract
            text = pytesseract.image_to_string(image, lang=self.lang)

            # 清理多余空白
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            text = "\n".join(lines)

            # 截断过长文本
            if len(text) > self.max_text_length:
                text = text[:self.max_text_length] + "...(已截断)"

            return text

        except Exception as e:
            logger.warning(f"OCR 提取失败: {e}")
            return ""

    def generate_description(self, text: str) -> str:
        """
        根据提取的文字生成简短描述

        供 orchestrator 注入 prompt，让羽依知道屏幕上大概有什么。
        """
        if not text:
            return "屏幕文字识别不可用或屏幕上没有可识别的文字。"

        # 统计信息
        line_count = text.count("\n") + 1
        char_count = len(text)

        # 取前几行作为摘要
        preview_lines = text.split("\n")[:5]
        preview = "\n".join(preview_lines)

        if len(preview) > 300:
            preview = preview[:300] + "..."

        desc = f"屏幕上检测到约 {line_count} 行文字（{char_count} 字符）。主要内容：\n{preview}"
        return desc

    def _empty_result(self, reason: str = "") -> Dict:
        """生成空结果"""
        return {
            "text": "",
            "description": f"屏幕文字识别不可用。{reason}" if reason else "屏幕文字识别不可用。",
            "ocr_success": False,
        }
