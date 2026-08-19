"""
屏幕截图模块

使用 mss 进行高性能截图，支持全屏和区域截图。
截图后转为 base64 编码返回，超过最大宽度时等比缩放。
自动检测并压缩过大的图片以提升传输速度。
"""

from __future__ import annotations
import base64
import io
import logging
import time

logger = logging.getLogger("local_agent.screen")


class ScreenCapture:
    """屏幕截图器"""

    def __init__(self, max_width: int = 1280, quality: int = 70):
        """
        Args:
            max_width: 图片最大宽度（超过则等比缩放）
            quality: JPEG 压缩质量（1-100）
        """
        self.max_width = max_width
        self.quality = quality
        self._mss = None  # 延迟初始化

    def _ensure_mss(self):
        """延迟初始化 mss 实例"""
        if self._mss is None:
            try:
                import mss
                self._mss = mss.mss()
            except ImportError:
                logger.error("mss 库未安装，请运行: pip install mss")
                raise
            except Exception as e:
                logger.error(f"初始化 mss 失败: {e}")
                raise
        return self._mss

    def capture_full(self) -> str:
        """
        全屏截图

        Returns:
            base64 编码的 JPEG 图片字符串
            失败时返回空字符串
        """
        start = time.time()
        try:
            sct = self._ensure_mss()
            monitor = sct.monitors[1]  # 主显示器
            raw = sct.grab(monitor)
            result = self._process_and_encode(raw)
            elapsed = (time.time() - start) * 1000
            img_size = len(result) if result else 0
            logger.info(f"全屏截图完成: {img_size} bytes (base64), 耗时 {elapsed:.1f}ms")
            return result
        except ImportError:
            return ""
        except Exception as e:
            logger.error(f"全屏截图失败: {e}")
            return ""

    def capture_region(self, x: int, y: int, width: int, height: int) -> str:
        """
        区域截图

        Args:
            x, y: 区域左上角坐标
            width, height: 区域宽高

        Returns:
            base64 编码的 JPEG 图片字符串
            失败时返回空字符串
        """
        start = time.time()
        try:
            sct = self._ensure_mss()
            monitor = {"top": y, "left": x, "width": width, "height": height}
            raw = sct.grab(monitor)
            result = self._process_and_encode(raw)
            elapsed = (time.time() - start) * 1000
            img_size = len(result) if result else 0
            logger.info(f"区域截图完成: {img_size} bytes (base64), 耗时 {elapsed:.1f}ms")
            return result
        except ImportError:
            return ""
        except Exception as e:
            logger.error(f"区域截图失败: {e}")
            return ""

    def _process_and_encode(self, raw) -> str:
        """
        处理截图并编码为 base64

        1. mss 原始数据 → PIL Image
        2. 超过 max_width 时等比缩放
        3. 根据图片大小自适应调整质量
        4. 转 JPEG → base64
        """
        try:
            from PIL import Image
        except ImportError:
            logger.error("Pillow 库未安装，请运行: pip install Pillow")
            return ""

        try:
            # mss 原始数据 → PIL Image
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

            # 等比缩放
            if img.width > self.max_width:
                ratio = self.max_width / img.width
                new_height = int(img.height * ratio)
                img = img.resize((self.max_width, new_height), Image.LANCZOS)

            # 自适应质量：如果图片仍然很大，逐步降低质量
            quality = self.quality
            max_bytes = 300 * 1024  # 目标 300KB 以内

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=quality)

            # 如果图片太大，降低质量重试
            while buffer.tell() > max_bytes and quality > 40:
                quality -= 10
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=quality)

            encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
            logger.debug(
                f"截图已编码: {img.width}x{img.height}, "
                f"quality={quality}, {len(encoded)} bytes (base64)"
            )
            return encoded

        except Exception as e:
            logger.error(f"图片处理失败: {e}")
            return ""
