"""
羽依 Live2D 桌面小部件 - VTube Studio 版

Neuro Sama 同款方案：
1. VTube Studio 渲染 Live2D 模型（眨眼/呼吸/物理模拟）
2. 通过 WebSocket API 控制表情/动作
3. TTS 语音播放 → VTS 自动嘴型同步
4. 周期性触发物理晃动

VTS 设置步骤（首次使用必读）：
  https://github.com/Neuro-Sama/neuro-sama/wiki
"""
import sys
import json
import time
import random
import asyncio
import threading
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

from websockets.sync.client import connect as ws_connect
from PySide6.QtWidgets import (
    QApplication, QWidget, QSystemTrayIcon, QMenu,
    QLabel, QVBoxLayout, QPushButton, QScrollArea
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread, QUrl
from PySide6.QtGui import QIcon, QPainter, QColor, QPixmap, QFont
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

try:
    import edge_tts
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    print("[TTS] edge-tts 未安装，运行: pip install edge-tts")

PROJECT_ROOT = Path(__file__).parent.parent
MODEL_DIR = PROJECT_ROOT / "A.雪芽2.0"

API_BASE = "http://127.0.0.1:5000"
API_CHARACTER = f"{API_BASE}/admin/api/character"

VTS_WS_URL = "ws://localhost:8001"
PLUGIN_NAME = "QianWuYuyi-AI"
PLUGIN_DEVELOPER = "Yuyi"

TTS_VOICE = "zh-CN-XiaoxiaoNeural"


class ExpressionMapper:
    """情绪 → VTS 表情文件映射"""

    def __init__(self):
        self.expressions = {}
        self.expression_files = {}
        self._scan()
        self._map()

    def _scan(self):
        if not MODEL_DIR.exists():
            return
        for f in sorted(MODEL_DIR.glob("*.exp3.json")):
            display = f.stem.replace(".exp3", "")
            self.expression_files[display] = f.name
        print(f"[Expression] 发现 {len(self.expression_files)} 个表情文件")
        for k in list(self.expression_files.keys())[:15]:
            print(f"  - {k}")

    def _map(self):
        mapping = {
            "开心": ["猫猫嘴", "笑脸"],
            "害羞": ["脸红"],
            "哇塞": ["星星眼", "惊讶"],
            "生气": ["生气"],
            "难过": ["哭泣", "泪"],
            "好奇": ["疑问"],
            "委屈": ["脸黑"],
            "平静": [""],
        }
        for emotion, candidates in mapping.items():
            self.expressions[emotion] = ""
            for name in candidates:
                if name and name in self.expression_files:
                    self.expressions[emotion] = self.expression_files[name]
                    break


class VTSClientThread(QThread):
    """VTS WebSocket 客户端线程 — 使用同步 websockets API"""

    connected_changed = Signal(bool)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ws = None
        self.running = False
        self._token = None
        self._lock = threading.Lock()

    def run(self):
        self._try_connect()

    def stop(self):
        self.running = False
        with self._lock:
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None

    def connect(self):
        if not self.isRunning():
            self.start()
        else:
            self._try_connect()

    def _try_connect(self):
        self.status_changed.emit("连接 VTube Studio...")
        try:
            with ws_connect(VTS_WS_URL, open_timeout=5) as ws:
                self.ws = ws
                self.connected_changed.emit(True)
                self.status_changed.emit("已连接")
                self._do_auth()

                self.running = True
                while self.running:
                    time.sleep(0.3)
                    try:
                        ws.ping()
                    except Exception:
                        break

                self.connected_changed.emit(False)
                self.status_changed.emit("已断开")
                self.ws = None
        except Exception as e:
            msg = str(e)
            if "10061" in msg or "refused" in msg.lower() or "connection" in msg.lower():
                msg = "请先启动 VTube Studio 并启用插件 API（设置→插件→允许插件API访问）"
            elif "timeout" in msg.lower():
                msg = "连接超时，请确认 VTube Studio 正在运行"
            self.error_occurred.emit(msg)
            self.connected_changed.emit(False)
            self.ws = None

    def _send(self, msg_data):
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": f"req_{int(time.time() * 1000)}",
        }
        msg.update(msg_data)
        if self.ws:
            self.ws.send(json.dumps(msg))

    def _recv(self, timeout=2.0):
        if not self.ws:
            return {}
        try:
            raw = self.ws.recv(timeout=timeout)
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _do_auth(self):
        try:
            tok_file = Path(__file__).parent / ".vts_token"
            if tok_file.exists():
                self._token = tok_file.read_text().strip()

            if self._token:
                self._send({
                    "messageType": "AuthenticationRequest",
                    "data": {"pluginName": PLUGIN_NAME,
                             "pluginDeveloper": PLUGIN_DEVELOPER,
                             "authenticationToken": self._token}
                })
                resp = self._recv()
                if resp.get("data", {}).get("authenticated"):
                    self.status_changed.emit("已认证")
                    return

            self._send({
                "messageType": "AuthenticationTokenRequest",
                "data": {"pluginName": PLUGIN_NAME,
                         "pluginDeveloper": PLUGIN_DEVELOPER}
            })
            resp = self._recv()
            token = resp.get("data", {}).get("authenticationToken")
            if token:
                self._token = token
                tok_file.write_text(token)
                self.status_changed.emit("等待 VTS 授权...")
                self._send({
                    "messageType": "AuthenticationRequest",
                    "data": {"pluginName": PLUGIN_NAME,
                             "pluginDeveloper": PLUGIN_DEVELOPER,
                             "authenticationToken": token}
                })
                resp2 = self._recv(timeout=10)
                if resp2.get("data", {}).get("authenticated"):
                    self.status_changed.emit("已认证")
                else:
                    self.status_changed.emit("请在 VTS 中同意授权后重试")
                    self.running = False
            else:
                self.status_changed.emit("认证失败，VTS 未返回 token")
                self.running = False
        except Exception as e:
            print(f"[VTS] 认证错误: {e}")
            self.status_changed.emit(f"认证错误: {e}")

    # ---- 对外 API（线程安全）----
    def set_expression(self, file_name, fade=0.5):
        if not file_name or not self.ws:
            return
        with self._lock:
            try:
                self._send({
                    "messageType": "ExpressionActivationRequest",
                    "data": {"expressionFile": "", "fadeTime": fade, "active": False}
                })
                self._recv(timeout=0.5)

                self._send({
                    "messageType": "ExpressionActivationRequest",
                    "data": {"expressionFile": file_name, "fadeTime": fade, "active": True}
                })
                resp = self._recv(timeout=1.0)
                if not resp.get("data", {}).get("success"):
                    err = resp.get("data", {}).get("errorID", "?")
                    print(f"[VTS] 表情 {file_name} 失败: {err}")
            except Exception as e:
                print(f"[VTS] 表情错误: {e}")

    def move_model(self, x=0, y=0, time_s=0.5):
        if not self.ws:
            return
        with self._lock:
            try:
                self._send({
                    "messageType": "MoveModelRequest",
                    "data": {
                        "timeInSeconds": time_s,
                        "valuesAreRelativeToModel": True,
                        "positionX": x, "positionY": y
                    }
                })
                self._recv(timeout=1.0)
            except Exception as e:
                print(f"[VTS] 移动错误: {e}")

    def trigger_hotkey(self, hotkey_name):
        """触发 VTS 热键（在 VTS 模型→热键中配置）"""
        if not self.ws or not hotkey_name:
            return
        with self._lock:
            try:
                self._send({
                    "messageType": "HotkeyTriggerRequest",
                    "data": {"hotkey": hotkey_name}
                })
                self._recv(timeout=0.5)
            except Exception as e:
                print(f"[VTS] 热键错误: {e}")


class TTSPlayer(QThread):
    """TTS 语音播放线程"""

    playback_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self._player = None
        self._output = None
        self._temp_file = None

    def speak(self, text):
        if not text or not TTS_AVAILABLE:
            return
        self.text = text
        self.start()

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._generate_and_play(loop))
            loop.close()
        except Exception as e:
            print(f"[TTS] 播放失败: {e}")

    async def _generate_and_play(self, loop):
        self._cleanup()

        tmp_dir = Path(tempfile.gettempdir())
        self._temp_file = tmp_dir / f"yuyi_tts_{int(time.time())}.mp3"

        communicate = edge_tts.Communicate(self.text, TTS_VOICE)
        await communicate.save(str(self._temp_file))

        self._player = QMediaPlayer()
        self._output = QAudioOutput()
        self._player.setAudioOutput(self._output)
        self._player.setSource(QUrl.fromLocalFile(str(self._temp_file)))
        self._output.setVolume(1.0)
        self._player.play()

        self._player.mediaStatusChanged.connect(self._on_status)

    def _on_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self._cleanup()
            self.playback_finished.emit()

    def _cleanup(self):
        if self._player:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
        if self._output:
            self._output.deleteLater()
            self._output = None
        if self._temp_file and self._temp_file.exists():
            try:
                self._temp_file.unlink()
            except Exception:
                pass
            self._temp_file = None


class VTSWindow(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(340, 560)
        self.setWindowTitle("羽依 VTS")

        self.mapper = ExpressionMapper()
        self._build_ui()

        # VTS 客户端
        self.vts = VTSClientThread(self)
        self.vts.connected_changed.connect(lambda c: self._on_status("已连接" if c else "已断开"))
        self.vts.status_changed.connect(self._on_status)
        self.vts.error_occurred.connect(self._on_error)
        self.vts.start()
        self.vts.connect()

        # TTS 播放器
        self.tts = TTSPlayer(self)

        # 状态
        self.current_emotion = "平静"
        self.current_message = ""
        self.last_idle_time = time.time()
        self.last_message_time = 0

        # 定时器
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._tick)
        self.poll_timer.start(3000)

        # 拖拽
        self.drag_pos = None
        self._init_tray()

        print("[羽依] VTS 控制面板已启动")
        if not TTS_AVAILABLE:
            print("[羽依] 提示: 安装 edge-tts 可启用语音 → 嘴型同步")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🌸 羽依 VTube Studio")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color: white; background: rgba(167,139,250,0.9);"
            "border-radius: 8px; padding: 6px;"
        )
        layout.addWidget(title)

        self.state_label = QLabel("状态: 等待连接 VTube Studio...")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setStyleSheet(
            "color: white; background: rgba(0,0,0,0.4);"
            "border-radius: 6px; padding: 4px;"
        )
        layout.addWidget(self.state_label)

        # TTS 状态
        tts_status = "✅ TTS 已启用" if TTS_AVAILABLE else "⚠️ 安装 edge-tts 启用语音"
        tts_label = QLabel(tts_status)
        tts_label.setFont(QFont("Microsoft YaHei", 8))
        tts_label.setAlignment(Qt.AlignCenter)
        tts_label.setStyleSheet(
            "color: rgba(255,255,255,0.6); padding: 2px;"
        )
        layout.addWidget(tts_label)

        label = QLabel("表情 / 情绪（点击切换）")
        label.setFont(QFont("Microsoft YaHei", 9))
        label.setStyleSheet("color: rgba(255,255,255,0.7); padding: 2px;")
        layout.addWidget(label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { width: 5px; background: rgba(0,0,0,0.3); }"
        )
        c = QWidget()
        sl = QVBoxLayout(c)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(4)

        emotions = ["平静", "开心", "害羞", "哇塞", "生气", "难过", "好奇", "委屈"]
        colors = {
            "平静": (150, 150, 150), "开心": (255, 200, 50),
            "害羞": (255, 150, 180), "哇塞": (100, 200, 255),
            "生气": (255, 80, 80), "难过": (80, 120, 200),
            "好奇": (150, 220, 150), "委屈": (180, 150, 255),
        }
        for e in emotions:
            fname = self.mapper.expressions.get(e) or ""
            text = f"  {e}  {('(' + fname[:10] + ')') if fname else ''}"
            r, g, b = colors.get(e, (150, 150, 150))
            btn = QPushButton(text)
            btn.setFont(QFont("Microsoft YaHei", 9))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: rgba({r},{g},{b},0.8); color: white;"
                f"border: none; border-radius: 6px; padding: 6px; }}"
                f"QPushButton:hover {{ opacity: 0.9; }}"
            )
            btn.clicked.connect(lambda ch, em=e: self._apply(em))
            sl.addWidget(btn)

        sl.addStretch()
        scroll.setWidget(c)
        layout.addWidget(scroll, 1)

        btn = QPushButton("🔄 重连 VTube Studio")
        btn.setFont(QFont("Microsoft YaHei", 9))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { background: rgba(100,100,150,0.8); color: white;"
            "border: none; border-radius: 6px; padding: 6px; }"
            "QPushButton:hover { background: rgba(120,120,170,0.9); }"
        )
        btn.clicked.connect(self._reconnect)
        layout.addWidget(btn)

        tip = QLabel("✨ 眨眼/呼吸/物理 → VTS 内置\n"
                     "🎤 嘴型同步 → TTS 语音触发\n"
                     "📜 详细设置见 VTS_ANIMATION_SETUP.py")
        tip.setFont(QFont("Microsoft YaHei", 8))
        tip.setStyleSheet("color: rgba(255,255,255,0.5); padding: 2px;")
        tip.setAlignment(Qt.AlignCenter)
        tip.setWordWrap(True)
        layout.addWidget(tip)

        hint = QLabel("快捷键: 1-8 表情 | Esc 隐藏 | Ctrl+Q 退出")
        hint.setFont(QFont("Microsoft YaHei", 8))
        hint.setStyleSheet("color: rgba(255,255,255,0.4);")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

    # ---- 核心逻辑 ----
    def _apply(self, emotion, message=None):
        if emotion == self.current_emotion:
            return
        self.current_emotion = emotion
        print(f"[羽依] 情绪: {emotion}")

        fname = self.mapper.expressions.get(emotion)
        if fname and self.vts.isRunning():
            self.vts.set_expression(fname, fade=0.6)

        # 情绪对应的身体动作
        motions = {
            "开心": (0.03, 0.02, 0.4),
            "害羞": (-0.02, 0.01, 0.6),
            "哇塞": (0.04, 0.03, 0.3),
            "生气": (0.02, 0.01, 0.2),
            "难过": (-0.02, -0.01, 0.5),
            "好奇": (0.03, 0.02, 0.4),
            "委屈": (-0.03, -0.02, 0.7),
            "平静": (0.01, 0.0, 0.5),
        }
        if self.vts.isRunning():
            dx, dy, t = motions.get(emotion, (0.02, 0.01, 0.4))
            self.vts.move_model(dx, dy, t)

        self.state_label.setText(f"当前: {emotion}")
        self.state_label.setStyleSheet(
            "color: white; background: rgba(76,175,80,0.7);"
            "border-radius: 6px; padding: 4px;"
        )

    def _speak(self, text):
        """播放 TTS 语音（触发 VTS 嘴型同步）"""
        if not text or not TTS_AVAILABLE:
            return
        now = time.time()
        if now - self.last_message_time < 2:
            return
        self.last_message_time = now
        self.tts.speak(text)

    def _tick(self):
        self._fetch_state()
        self._idle_animation()

    def _fetch_state(self):
        try:
            req = urllib.request.Request(
                API_CHARACTER,
                headers={"User-Agent": "Yuyi-VTS"}
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                emotion = data.get("emotion", "") or data.get("mood", "")
                message = data.get("message", "")

                if emotion and emotion != self.current_emotion:
                    self._apply(emotion)

                if message and message != self.current_message:
                    self.current_message = message
                    self._speak(message)
        except Exception:
            pass

    def _idle_animation(self):
        now = time.time()
        if now - self.last_idle_time < 10:
            return
        self.last_idle_time = now

        if not self.vts.isRunning():
            return

        # 身体晃动（物理模拟）
        dx = random.uniform(-0.06, 0.06)
        dy = random.uniform(-0.03, 0.03)
        self.vts.move_model(dx, dy, random.uniform(0.4, 0.8))

        # 随机触发表情（眨眼/好奇）
        r = random.random()
        if r < 0.4:
            blink = self.mapper.expression_files.get("眨眼")
            if blink:
                self.vts.set_expression(blink, fade=0.1)
                QTimer.singleShot(200, lambda: self.vts.set_expression("", fade=0.1))
        elif r < 0.55:
            curious = self.mapper.expression_files.get("疑问")
            if curious:
                self.vts.set_expression(curious, fade=0.2)
                QTimer.singleShot(500, lambda: self.vts.set_expression("", fade=0.3))

    def _on_status(self, status):
        if "当前" not in self.state_label.text():
            self.state_label.setText(f"状态: {status}")

    def _on_error(self, err):
        self.state_label.setText(f"错误: {err[:35]}")
        self.state_label.setStyleSheet(
            "color: white; background: rgba(200,50,50,0.7);"
            "border-radius: 6px; padding: 4px;"
        )

    def _reconnect(self):
        self.vts.running = False
        self.vts.stop()
        QTimer.singleShot(1500, self.vts.start)

    # ---- 鼠标/键盘 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self.drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self.drag_pos = None

    def keyPressEvent(self, e):
        km = {
            Qt.Key_1: "平静", Qt.Key_2: "开心", Qt.Key_3: "害羞",
            Qt.Key_4: "哇塞", Qt.Key_5: "生气", Qt.Key_6: "难过",
            Qt.Key_7: "好奇", Qt.Key_8: "委屈",
        }
        if e.key() in km:
            self._apply(km[e.key()])
        elif e.key() == Qt.Key_Escape:
            self.hide()
        elif e.key() == Qt.Key_Q and e.modifiers() == Qt.ControlModifier:
            self.quit_app()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("显示", self.show)
        m.addAction("隐藏", self.hide)
        m.addSeparator()
        for em in ["开心", "害羞", "哇塞", "生气", "难过"]:
            m.addAction(f"表情: {em}", lambda c, x=em: self._apply(x))
        m.addSeparator()
        m.addAction("退出", self.quit_app)
        m.exec(e.globalPos())

    def quit_app(self):
        self.vts.stop()
        self.vts.wait(1500)
        self.tts._cleanup()
        QApplication.quit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(30, 30, 40, 230))
        p.setPen(QColor(167, 139, 250, 200))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)

    def _init_tray(self):
        pm = QPixmap(32, 32)
        pm.fill(QColor(167, 139, 250))
        p = QPainter(pm)
        p.setPen(Qt.white)
        p.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        p.drawText(pm.rect(), Qt.AlignCenter, "羽")
        p.end()

        self.tray = QSystemTrayIcon(QIcon(pm), self)
        self.tray.setToolTip("羽依 VTS")
        tm = QMenu()
        tm.addAction("显示", self.show)
        tm.addAction("隐藏", self.hide)
        tm.addSeparator()
        tm.addAction("表情: 开心", lambda: self._apply("开心"))
        tm.addAction("表情: 害羞", lambda: self._apply("害羞"))
        tm.addSeparator()
        tm.addAction("退出", self.quit_app)
        self.tray.setContextMenu(tm)
        self.tray.activated.connect(
            lambda r: self.show() if r == QSystemTrayIcon.DoubleClick else None
        )
        self.tray.show()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    w = VTSWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
