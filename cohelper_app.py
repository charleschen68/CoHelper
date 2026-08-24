"""macOS AppKit shell for cohelper."""

from __future__ import annotations

import copy
import subprocess
import threading
import time
import uuid
from pathlib import Path

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationDidChangeScreenParametersNotification,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSLinkAttributeName,
    NSMenu,
    NSMenuItem,
    NSModalResponseOK,
    NSOpenPanel,
    NSPopUpButton,
    NSPasteboard,
    NSPasteboardTypeString,
    NSScrollView,
    NSSecureTextField,
    NSStatusBar,
    NSStatusItem,
    NSTextField,
    NSTextView,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskResizable,
    NSWorkspace,
    NSBackingStoreBuffered,
    NSFloatingWindowLevel,
    NSMakeRect,
)
from Foundation import NSNotificationCenter, NSObject, NSString, NSTimer, NSURL
from PyObjCTools import AppHelper

from ai_drive.output import (
    OutputEvent,
    OutputEventSocketProtocol,
    OutputEventUnixSocketServer,
    OutputKind,
    OutputSeverity,
    OutputSource,
)
from apps.overlay import OutputOverlayController
from apps.voice import MacMicrophoneCapture, MacPushToTalkMonitor, MicrophoneCaptureError
from cohelper_core import APP_SUPPORT, CONFIG_PATH, Config, ConfigError, TaskCallbacks, TaskCoordinator
from ai_drive.voice import (
    VoiceActivityDetector,
    VoiceInputCoordinator,
    VoiceInputError,
    AnswerSentenceBuffer,
    MacSpeechOutput,
    VoiceCommandRouter,
    VoiceCommandRouterError,
    VoiceRouteKind,
    VoiceCommandActionBridge,
    VoiceActionSafetyGate,
    WhisperCppWorker,
    WhisperCppWorkerConfig,
)
from cohelper_setup import EnvironmentDoctor, KeychainStore, SetupInstaller, SetupState, resolve_command


OUTPUT_SOCKET_PATH = APP_SUPPORT / "output" / "events.sock"


class CohelperApp(NSObject):
    def init(self):
        self = objc.super(CohelperApp, self).init()
        if self is None:
            return None
        self.setup_state = SetupState.load()
        self.first_run = not CONFIG_PATH.exists() or not self.setup_state.setup_complete
        self.startup_error = None
        try:
            self.config = Config.load()
        except ConfigError as exc:
            self.config = Config({})
            self.startup_error = str(exc)
        self.coordinator = TaskCoordinator(self.config, self._callbacks())
        self.last_change_count = -1
        self.last_text = ""
        self.pending_text = ""
        self.debounce_timer = None
        self.paused = self.first_run or self.startup_error is not None
        self.window = None
        self.text_view = None
        self.output_generation = 0
        self.output_overlay = None
        self.output_overlay_timer = None
        self.output_event_server = None
        self.output_screen_observer_registered = False
        self.voice_input = None
        self.voice_capture = None
        self.voice_hotkey = None
        self.voice_permission_pending = False
        self.voice_vad = None
        self.voice_speech = None
        self.voice_sentence_buffer = None
        self.direct_screen_capture = None
        self.voice_action_bridge = None
        self.voice_action_safety = None
        self.pending_voice_action = None
        try:
            self.voice_router = VoiceCommandRouter(self.config.section("voice")["command_aliases"])
        except VoiceCommandRouterError as exc:
            self.startup_error = self.startup_error or f"语音命令配置无效：{exc}"
            self.voice_router = VoiceCommandRouter({})
        self.status_item: NSStatusItem | None = None
        self.setup_installer = None
        self.setup_thread = None
        return self

    def _current_overlay_mask(self):
        if self.output_overlay is None:
            return None
        return self.output_overlay.current_mask()

    def applicationDidFinishLaunching_(self, notification):
        NSApp().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.last_change_count = NSPasteboard.generalPasteboard().changeCount()
        self._build_status_item()
        if self.startup_error is None and self.config.enabled("overlay"):
            self._start_output_feature()
        if self.startup_error is None and self.config.enabled("voice_input"):
            self._start_voice_feature()
        if self.startup_error is None and self.config.enabled("voice_output"):
            self._start_speech_feature()
        if self.startup_error is None and self.config.enabled("voice_direct_actions"):
            self._start_voice_direct_action_feature()
        self._start_clipboard_timer()
        if self.startup_error:
            self._show_config_error()
            return
        if self.first_run:
            if not CONFIG_PATH.exists():
                self.config.save()
            self.runDiagnostics_(None)

    def _start_output_feature(self):
        if self.output_overlay is not None or not self.config.enabled("overlay"):
            return
        self.output_overlay = OutputOverlayController()
        self.output_overlay_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self, "tickOutputOverlay:", None, True
        )
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self,
            b"screenParametersChanged:",
            NSApplicationDidChangeScreenParametersNotification,
            None,
        )
        self.output_screen_observer_registered = True
        try:
            self.output_event_server = OutputEventUnixSocketServer(
                OUTPUT_SOCKET_PATH,
                OutputEventSocketProtocol(
                    lambda event: AppHelper.callAfter(self._publish_received_output, event)
                ),
            )
            self.output_event_server.start()
        except Exception as exc:
            self.output_event_server = None
            self._publish_output(
                OutputKind.ERROR,
                OutputSource.SYSTEM,
                "输出事件通道不可用",
                f"{type(exc).__name__}: {exc}",
                severity=OutputSeverity.ERROR,
            )

    def _stop_output_feature(self):
        if self.output_screen_observer_registered:
            NSNotificationCenter.defaultCenter().removeObserver_name_object_(
                self,
                NSApplicationDidChangeScreenParametersNotification,
                None,
            )
            self.output_screen_observer_registered = False
        if self.output_overlay_timer is not None:
            self.output_overlay_timer.invalidate()
            self.output_overlay_timer = None
        server, self.output_event_server = self.output_event_server, None
        overlay, self.output_overlay = self.output_overlay, None
        try:
            if server is not None:
                server.stop()
        finally:
            if overlay is not None:
                overlay.close()

    def screenParametersChanged_(self, notification):
        if self.output_overlay is not None:
            self.output_overlay.reposition()

    def _callbacks(self):
        return TaskCallbacks(
            on_started=lambda generation, text: AppHelper.callAfter(
                self._show_started, generation, text
            ),
            on_translation=lambda generation, result: AppHelper.callAfter(
                self._append_task_result,
                generation,
                "翻译",
                result.text or result.error,
                OutputKind.TRANSLATION,
                OutputSource.CLIPBOARD,
                OutputSeverity.INFO if result.text else OutputSeverity.ERROR,
            ),
            on_knowledge=lambda generation, hits: AppHelper.callAfter(
                self._append_sources, generation, hits
            ),
            on_summary=lambda generation, result: AppHelper.callAfter(
                self._append_task_result,
                generation,
                "知识回答 / 总结",
                result.text or result.error,
                OutputKind.ANSWER_FINAL,
                OutputSource.KNOWLEDGE,
                OutputSeverity.INFO if result.text else OutputSeverity.ERROR,
            ),
            on_summary_delta=lambda generation, delta: AppHelper.callAfter(
                self._append_answer_delta, generation, delta
            ),
            on_error=lambda generation, error: AppHelper.callAfter(
                self._append_task_result,
                generation,
                "错误",
                error,
                OutputKind.ERROR,
                OutputSource.SYSTEM,
                OutputSeverity.ERROR,
            ),
            on_rejected=lambda generation, reason: AppHelper.callAfter(
                self._show_rejected, generation, reason
            ),
            on_finished=lambda generation: AppHelper.callAfter(self._finish_task, generation),
        )

    def _build_status_item(self):
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
        self.status_item.button().setTitle_("cohelper")
        menu = NSMenu.alloc().init()
        menu.addItemWithTitle_action_keyEquivalent_("暂停监听", "togglePause:", "")
        menu.addItemWithTitle_action_keyEquivalent_("开始语音", "startVoice:", "")
        menu.addItemWithTitle_action_keyEquivalent_("结束并提交语音", "finishVoice:", "")
        menu.addItemWithTitle_action_keyEquivalent_("取消语音", "cancelVoice:", "")
        menu.addItemWithTitle_action_keyEquivalent_("确认语音操作", "confirmVoiceAction:", "")
        menu.addItemWithTitle_action_keyEquivalent_("紧急停止语音操作", "emergencyStopVoiceActions:", "")
        menu.addItemWithTitle_action_keyEquivalent_("恢复语音操作", "resumeVoiceActions:", "")
        menu.addItemWithTitle_action_keyEquivalent_("环境诊断与设置", "runDiagnostics:", "")
        menu.addItemWithTitle_action_keyEquivalent_("模型设置", "configureModels:", "")
        menu.addItemWithTitle_action_keyEquivalent_("高级配置", "configureAdvanced:", "")
        menu.addItemWithTitle_action_keyEquivalent_("请求视觉操作权限", "requestVisionPermissions:", "")
        menu.addItemWithTitle_action_keyEquivalent_("取消环境设置", "cancelSetup:", "")
        menu.addItemWithTitle_action_keyEquivalent_("打开配置目录", "openConfig:", "")
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItemWithTitle_action_keyEquivalent_("退出", "terminate:", "q")
        self.status_item.setMenu_(menu)

    def _start_voice_feature(self):
        if self.voice_input is not None or not self.config.enabled("voice_input"):
            return
        voice = self.config.section("voice")
        executable = resolve_command(str(voice["server_executable"])) or str(voice["server_executable"])
        worker = WhisperCppWorker(
            WhisperCppWorkerConfig(
                executable=executable,
                model_path=str(Path(str(voice["model_path"])).expanduser()),
                port=int(voice["server_port"]),
                language=str(voice["language"]),
            ),
            lambda event: AppHelper.callAfter(self._accept_voice_worker_transcript, event),
            on_error=lambda error: AppHelper.callAfter(self._handle_voice_error, error),
        )
        self.voice_input = VoiceInputCoordinator(
            worker,
            on_transcript=lambda event: AppHelper.callAfter(self._handle_voice_transcript, event),
        )
        self.voice_vad = VoiceActivityDetector(
            sample_rate=int(voice["sample_rate"]),
            threshold=int(voice["vad_threshold"]),
            silence_seconds=float(voice["silence_seconds"]),
        )
        self.voice_capture = MacMicrophoneCapture(
            lambda pcm: self._send_voice_pcm(pcm),
            on_error=lambda error: AppHelper.callAfter(self._handle_voice_error, error),
        )
        self.voice_hotkey = MacPushToTalkMonitor(self.startVoice_, self.finishVoice_)
        try:
            self.voice_hotkey.start()
        except Exception as exc:
            self._handle_voice_error(exc)

    def _start_speech_feature(self):
        if self.voice_speech is not None or not self.config.enabled("voice_output"):
            return
        self.voice_sentence_buffer = AnswerSentenceBuffer(max_pending=1)
        self.voice_speech = MacSpeechOutput(on_error=lambda error: AppHelper.callAfter(self._handle_speech_error, error))

    def _stop_speech_feature(self):
        speech, self.voice_speech = self.voice_speech, None
        self.voice_sentence_buffer = None
        if speech is not None:
            speech.interrupt()

    def _interrupt_speech(self):
        if self.voice_sentence_buffer is not None:
            self.voice_sentence_buffer.clear()
        if self.voice_speech is not None:
            self.voice_speech.interrupt()

    def _speak_answer(self, generation: int, text: str):
        if self.voice_speech is None or self.voice_sentence_buffer is None:
            return
        for sentence_generation, sentence in self.voice_sentence_buffer.feed(generation, text):
            self.voice_speech.speak(sentence_generation, sentence)
        for sentence_generation, sentence in self.voice_sentence_buffer.finish(generation):
            self.voice_speech.speak(sentence_generation, sentence)

    def _speak_answer_delta(self, generation: int, delta: str):
        if self.voice_speech is None or self.voice_sentence_buffer is None:
            return
        for sentence_generation, sentence in self.voice_sentence_buffer.feed(generation, delta):
            self.voice_speech.speak(sentence_generation, sentence)

    def _finish_answer_speech(self, generation: int):
        if self.voice_speech is None or self.voice_sentence_buffer is None:
            return
        for sentence_generation, sentence in self.voice_sentence_buffer.finish(generation):
            self.voice_speech.speak(sentence_generation, sentence)

    def _handle_speech_error(self, error):
        self._publish_output(OutputKind.ERROR, OutputSource.SYSTEM, "语音输出错误", str(error), severity=OutputSeverity.ERROR)

    def _stop_voice_feature(self):
        hotkey, self.voice_hotkey = self.voice_hotkey, None
        capture, self.voice_capture = self.voice_capture, None
        voice, self.voice_input = self.voice_input, None
        self.voice_vad = None
        if hotkey is not None:
            hotkey.stop()
        if capture is not None:
            capture.stop()
        if voice is not None:
            voice.cancel()

    def _start_voice_direct_action_feature(self):
        if self.direct_screen_capture is not None or not self.config.enabled("voice_direct_actions"):
            return
        if self.output_overlay is None or self.voice_input is None:
            self._handle_voice_error(RuntimeError("voice direct actions require active voice input and overlay"))
            return
        try:
            from ai_drive.actions import AccessibilityCapability, ActionService
            from ai_drive.macos import (
                MacAccessibilityInspector,
                QuartzDesktopObserver,
                QuartzPointerController,
                QuartzScreenCapture,
            )
            from ai_drive.vision import OllamaVisionClient, VisionAnalyzer
            from ai_drive.workflow import VisualClickWorkflow

            self.direct_screen_capture = QuartzScreenCapture(self._current_overlay_mask)
            actions = self.config.section("actions")
            action_service = ActionService(
                MacAccessibilityInspector(),
                QuartzDesktopObserver(),
                QuartzPointerController(),
                allowed_bundle_ids=frozenset(actions["allowed_bundle_ids"]),
                allowed_capabilities=frozenset(
                    AccessibilityCapability(*str(value).split("|"))
                    for value in actions["allowed_capabilities"]
                ),
                minimum_confidence=float(actions["minimum_confidence"]),
                screenshot_max_age=float(actions["screenshot_max_age_seconds"]),
                confirmation_ttl=float(actions["confirmation_ttl_seconds"]),
            )
            vision = self.config.section("vision")
            workflow = VisualClickWorkflow(
                self.direct_screen_capture,
                VisionAnalyzer(OllamaVisionClient(str(vision["base_url"]), int(vision["timeout_seconds"])), str(vision["model"])),
                action_service,
            )
            self.voice_action_safety = VoiceActionSafetyGate()
            self.voice_action_bridge = VoiceCommandActionBridge(
                workflow,
                self.config.section("voice")["command_instructions"],
                safety_gate=self.voice_action_safety,
            )
        except Exception as exc:
            self.direct_screen_capture = None
            self.voice_action_bridge = None
            self.voice_action_safety = None
            self._handle_voice_error(exc)

    def _stop_voice_direct_action_feature(self):
        self.direct_screen_capture = None
        self.voice_action_bridge = None
        self.voice_action_safety = None
        self.pending_voice_action = None

    def confirmVoiceAction_(self, sender):
        if self.voice_action_bridge is not None and self.pending_voice_action is not None:
            pending = self.pending_voice_action
            try:
                self.voice_action_bridge.confirm(
                    pending,
                    user_id=pending.user_id,
                    chat_id=pending.chat_id,
                    overlay_masked=self._current_overlay_mask() is not None,
                )
                self.pending_voice_action = None
                self._publish_output(
                    OutputKind.ACTION,
                    OutputSource.ACTIONS,
                    "语音操作",
                    f"已确认并执行：{pending.command}",
                    severity=OutputSeverity.INFO,
                )
                return
            except Exception as exc:
                self.pending_voice_action = None
                self._publish_output(
                    OutputKind.ERROR,
                    OutputSource.ACTIONS,
                    "语音操作被拒绝",
                    str(exc),
                    severity=OutputSeverity.ERROR,
                )
                return
        self._publish_output(
            OutputKind.ERROR,
            OutputSource.VOICE,
            "语音操作确认",
            "当前没有可确认的语音操作。",
            severity=OutputSeverity.WARNING,
        )

    def emergencyStopVoiceActions_(self, sender):
        if self.voice_action_safety is not None:
            self.voice_action_safety.emergency_stop()
        if self.pending_voice_action is not None and self.voice_action_bridge is not None:
            pending = self.pending_voice_action
            try:
                self.voice_action_bridge.cancel(pending, user_id=pending.user_id, chat_id=pending.chat_id)
            except Exception:
                pass
            self.pending_voice_action = None
        self.output_generation += 1
        self._publish_output(
            OutputKind.EMERGENCY_STOP,
            OutputSource.ACTIONS,
            "语音操作紧急停止",
            "所有待确认语音操作已停止；需要手动恢复。",
            severity=OutputSeverity.CRITICAL,
            generation=self.output_generation,
        )

    def resumeVoiceActions_(self, sender):
        if self.voice_action_safety is None:
            return
        try:
            self.voice_action_safety.resume(manual=True)
        except Exception as exc:
            self._handle_voice_error(exc)
            return
        self.output_generation += 1
        self._publish_output(
            OutputKind.EMERGENCY_CLEARED,
            OutputSource.ACTIONS,
            "语音操作安全状态",
            "已手动恢复语音操作。",
            severity=OutputSeverity.INFO,
            generation=self.output_generation,
        )

    def startVoice_(self, sender):
        if self.voice_input is None or self.voice_capture is None:
            self._show_error("语音输入不可用", "请先在高级配置中启用语音输入，并确认本机已安装 Whisper worker。")
            return
        if self.voice_input.state in {"listening", "finalizing"}:
            return
        if self.voice_permission_pending:
            return
        self._interrupt_speech()
        self.voice_permission_pending = True
        self.voice_capture.request_permission(
            lambda granted: AppHelper.callAfter(self._start_voice_after_permission, bool(granted))
        )

    def _start_voice_after_permission(self, granted):
        self.voice_permission_pending = False
        if self.voice_input is None or self.voice_capture is None:
            return
        if not granted:
            self._handle_voice_error(MicrophoneCaptureError("microphone permission was denied"))
            return
        try:
            self.voice_input.start(time.time(), session_id=f"voice-{uuid.uuid4().hex}", long_input=False)
            self.voice_capture.start()
            self._publish_output(OutputKind.VOICE, OutputSource.SYSTEM, "语音输入", "正在聆听", severity=OutputSeverity.INFO)
        except (VoiceInputError, MicrophoneCaptureError) as exc:
            self._stop_voice_feature()
            self._handle_voice_error(exc)

    def finishVoice_(self, sender):
        if self.voice_input is None or self.voice_capture is None:
            return
        try:
            self.voice_capture.stop()
            self.voice_input.finish_recording(time.time())
        except VoiceInputError as exc:
            self._handle_voice_error(exc)

    def cancelVoice_(self, sender):
        if self.voice_capture is not None:
            self.voice_capture.stop()
        if self.voice_input is not None:
            self.voice_input.cancel()
        self._publish_output(OutputKind.ERROR, OutputSource.SYSTEM, "语音输入", "已取消", severity=OutputSeverity.WARNING)

    def _send_voice_pcm(self, pcm):
        if self.voice_input is None:
            return
        try:
            if self.voice_vad is not None:
                transition = self.voice_vad.accept(pcm)
                if transition == "speech_started":
                    AppHelper.callAfter(self._publish_voice_activity, "检测到语音")
                elif transition == "speech_silence":
                    AppHelper.callAfter(self._publish_voice_activity, "检测到短暂停顿")
            self.voice_input.send_pcm(pcm)
        except VoiceInputError as exc:
            AppHelper.callAfter(self._handle_voice_error, exc)

    def _publish_voice_activity(self, message):
        self._publish_output(OutputKind.VOICE, OutputSource.SYSTEM, "语音活动", message, severity=OutputSeverity.INFO)

    def _accept_voice_worker_transcript(self, event):
        if self.voice_input is None:
            return
        try:
            self.voice_input.accept_worker_transcript(event)
        except VoiceInputError as exc:
            self._handle_voice_error(exc)

    def _handle_voice_transcript(self, event):
        kind = OutputKind.TRANSCRIPT_FINAL if event.finalized else OutputKind.TRANSCRIPT_PARTIAL
        self._publish_output(kind, OutputSource.SYSTEM, "语音转写", event.text, generation=event.sequence)
        if not event.finalized:
            return
        try:
            route = self.voice_router.route(event.text, finalized=True)
        except VoiceCommandRouterError as exc:
            self._publish_output(
                OutputKind.ERROR,
                OutputSource.VOICE,
                "语音请求已拒绝",
                str(exc),
                severity=OutputSeverity.WARNING,
            )
            return
        if route.kind is VoiceRouteKind.COMMAND:
            if self.voice_action_bridge is None:
                self._publish_output(OutputKind.STATUS, OutputSource.VOICE, "语音命令已识别", "动作能力尚未启用；未执行任何操作。", severity=OutputSeverity.WARNING)
                return
            try:
                self.pending_voice_action = self.voice_action_bridge.prepare(
                    route,
                    utterance_id=f"{event.session_id}:{event.sequence}",
                    user_id=0,
                    chat_id=0,
                    overlay_masked=self._current_overlay_mask() is not None,
                )
                self._publish_output(
                    OutputKind.ACTION,
                    OutputSource.ACTIONS,
                    "待确认语音操作",
                    f"已准备：{self.pending_voice_action.command}；请从菜单确认。",
                    severity=OutputSeverity.WARNING,
                )
            except Exception as exc:
                self._publish_output(OutputKind.ERROR, OutputSource.ACTIONS, "语音操作准备失败", str(exc), severity=OutputSeverity.ERROR)
            return
        generation = self.coordinator.submit(route.text)
        if generation is not None:
            self.output_generation = max(self.output_generation, generation)

    def _handle_voice_error(self, error):
        self._publish_output(OutputKind.ERROR, OutputSource.SYSTEM, "语音输入错误", str(error), severity=OutputSeverity.ERROR)
        if self.voice_capture is not None:
            self.voice_capture.stop()
        if self.voice_input is not None:
            self.voice_input.cancel()

    def requestVisionPermissions_(self, sender):
        try:
            from ai_drive.macos import MacAccessibilityInspector, QuartzScreenCapture

            screen = QuartzScreenCapture()
            accessibility = MacAccessibilityInspector()
            if not screen.has_permission():
                screen.request_permission()
            if not accessibility.has_permission():
                accessibility.request_permission()
            self._show_info(
                "视觉操作权限",
                "已请求“屏幕录制”和“辅助功能”权限。请在系统设置中允许 cohelper，然后完全退出并重新打开应用。",
            )
        except Exception as exc:
            self._show_error("无法请求视觉操作权限", f"{type(exc).__name__}: {exc}")

    def _start_clipboard_timer(self):
        interval = int(self.config.section("clipboard")["poll_interval_ms"]) / 1000
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(interval, self, "pollClipboard:", None, True)

    def pollClipboard_(self, timer):
        if self.paused:
            return
        pasteboard = NSPasteboard.generalPasteboard()
        count = pasteboard.changeCount()
        if count == self.last_change_count:
            return
        self.last_change_count = count
        text = pasteboard.stringForType_(NSPasteboardTypeString)
        if not text or text == self.last_text:
            return
        self.last_text = text
        if self.config.section("clipboard")["process_plain_text_only"]:
            self.pending_text = text
            if self.debounce_timer is not None:
                self.debounce_timer.invalidate()
            delay = int(self.config.section("clipboard")["debounce_ms"]) / 1000
            self.debounce_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                delay, self, "processPendingClipboard:", None, False
            )

    def processPendingClipboard_(self, timer):
        text = self.pending_text
        self.pending_text = ""
        self.debounce_timer = None
        if text:
            generation = self.coordinator.submit(text)
            if generation is not None:
                self.output_generation = max(self.output_generation, generation)

    def applicationWillTerminate_(self, notification):
        self.coordinator.cancel()
        if self.setup_installer is not None:
            self.setup_installer.cancel()
        if self.debounce_timer is not None:
            self.debounce_timer.invalidate()
        self._stop_output_feature()
        self._stop_voice_feature()
        self._stop_speech_feature()

    def tickOutputOverlay_(self, timer):
        if self.output_overlay is not None:
            self.output_overlay.tick()

    def _publish_received_output(self, event):
        if self.output_overlay is not None:
            try:
                self.output_overlay.publish(event)
            except Exception:
                self._set_status("cohelper (输出错误)")

    def _ensure_window(self):
        if self.window is not None:
            return
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(900, 80, 650, 600), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable, NSBackingStoreBuffered, False)
        self.window.setTitle_("cohelper")
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setReleasedWhenClosed_(False)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16, 16, 618, 530))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.text_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 600, 520))
        self.text_view.setEditable_(False)
        self.text_view.setSelectable_(True)
        self.text_view.setAutomaticLinkDetectionEnabled_(True)
        self.text_view.setFont_(NSFont.fontWithName_size_("Menlo", 12))
        self.text_view.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.1, 0.8, 0.3, 1))
        scroll.setDocumentView_(self.text_view)
        self.window.contentView().addSubview_(scroll)

    def _show_started(self, generation, text):
        if generation < self.output_generation:
            return
        self.output_generation = generation
        self._ensure_window()
        modules = []
        if self.config.enabled("translation"):
            modules.append("翻译")
        if self.config.enabled("knowledge_search"):
            modules.append("知识库检索")
        status = "正在处理：" + "、".join(modules) if modules else "所有处理模块均已关闭"
        self.text_view.setString_(f"原文\n{text}\n\n{status}……")
        self.window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)
        self._publish_output(
            OutputKind.TEXT_INPUT,
            OutputSource.CLIPBOARD,
            "剪贴板输入",
            text,
            generation=self.output_generation,
        )

    def _show_rejected(self, generation, reason):
        if generation < self.output_generation:
            return
        self.output_generation = generation
        self._ensure_window()
        self.text_view.setString_(reason)
        self.window.makeKeyAndOrderFront_(None)
        self._publish_output(
            OutputKind.ERROR,
            OutputSource.CLIPBOARD,
            "已跳过剪贴板内容",
            reason,
            severity=OutputSeverity.WARNING,
            generation=self.output_generation,
        )

    def _append_result(self, title, content):
        if self.text_view is None:
            return
        current = self.text_view.string() or ""
        self.text_view.setString_(current + f"\n\n===== {title} =====\n{content}")

    def _append_task_result(self, generation, title, content, kind, source, severity):
        if generation != self.output_generation:
            return
        self._append_result(title, content)
        if kind is OutputKind.ANSWER_FINAL and severity is OutputSeverity.INFO:
            self._finish_answer_speech(generation)
        self._publish_output(
            kind,
            source,
            title,
            content or "",
            severity=severity,
            generation=generation,
        )

    def _append_answer_delta(self, generation, delta):
        if generation != self.output_generation:
            return
        self._publish_output(
            OutputKind.ANSWER_DELTA,
            OutputSource.KNOWLEDGE,
            "知识回答",
            delta,
            severity=OutputSeverity.INFO,
            generation=generation,
        )
        self._speak_answer_delta(generation, delta)

    def _append_sources(self, generation, hits):
        if generation != self.output_generation:
            return
        if self.text_view is None:
            return
        if hits:
            uris = []
            for hit in hits:
                path = Path(hit.path).as_uri() if hit.path.startswith("/") else hit.path
                uris.append(path)
            current = self.text_view.string() or ""
            content = current + "\n\n===== 知识库来源 =====\n" + "\n".join(f"- {uri}" for uri in uris)
            self.text_view.setString_(content)
            ns_content = NSString.stringWithString_(content)
            for uri in uris:
                link_range = ns_content.rangeOfString_(uri)
                self.text_view.textStorage().addAttribute_value_range_(NSLinkAttributeName, NSURL.URLWithString_(uri), link_range)
            self._publish_output(
                OutputKind.KNOWLEDGE_SOURCES,
                OutputSource.KNOWLEDGE,
                "知识库来源",
                "\n".join(uris),
                generation=self.output_generation,
            )
        else:
            self._append_result("知识库来源", "未找到可靠知识库来源")
            self._publish_output(
                OutputKind.KNOWLEDGE_SOURCES,
                OutputSource.KNOWLEDGE,
                "知识库来源",
                "未找到可靠知识库来源",
                severity=OutputSeverity.WARNING,
                generation=self.output_generation,
            )

    def _finish_task(self, generation):
        if generation == self.output_generation:
            self._set_status("cohelper")

    def _publish_output(
        self,
        kind,
        source,
        title,
        message,
        *,
        severity=OutputSeverity.INFO,
        generation=None,
        metadata=None,
    ):
        if self.output_overlay is None:
            return
        bounded_message = str(message)
        if len(bounded_message) > 16_384:
            bounded_message = bounded_message[:16_383] + "…"
        event = OutputEvent(
            event_id=uuid.uuid4().hex,
            kind=kind,
            source=source,
            occurred_at=time.time(),
            title=str(title),
            message=bounded_message,
            severity=severity,
            generation=generation,
            metadata=dict(metadata or {}),
        )
        self.output_overlay.publish(event)

    def _set_status(self, title):
        if self.status_item:
            self.status_item.button().setTitle_(title)

    def togglePause_(self, sender):
        self.paused = not self.paused
        self._set_status("cohelper (暂停)" if self.paused else "cohelper")

    def openConfig_(self, sender):
        if not CONFIG_PATH.exists():
            Config({}).save()
        subprocess.Popen(["/usr/bin/open", str(APP_SUPPORT)])

    def _show_config_error(self):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("cohelper 配置无效，监听已暂停")
        alert.setInformativeText_(self.startup_error)
        alert.addButtonWithTitle_("打开配置目录")
        alert.addButtonWithTitle_("退出")
        if alert.runModal() == NSAlertFirstButtonReturn:
            self.openConfig_(None)
        else:
            NSApp().terminate_(None)

    def runDiagnostics_(self, sender):
        self._set_status("cohelper (诊断中)")
        threading.Thread(target=self._diagnose, daemon=True).start()

    def _diagnose(self):
        try:
            report = EnvironmentDoctor(self.config.values).run()
            report.save(APP_SUPPORT / "diagnostics.json")
            AppHelper.callAfter(self._show_diagnostics, report)
        except Exception as exc:
            AppHelper.callAfter(self._set_status, "cohelper (诊断失败)")
            AppHelper.callAfter(self._show_error, "环境诊断失败", f"{type(exc).__name__}: {exc}")

    def _show_diagnostics(self, report):
        self._set_status("cohelper")
        alert = NSAlert.alloc().init()
        alert.setMessageText_("cohelper 环境已就绪" if report.ready else "cohelper 需要完成环境设置")
        details = report.as_text()
        models = sorted(EnvironmentDoctor(self.config.values).required_ollama_models())
        if models and not report.ready:
            details += "\n\n模型可能占用大量磁盘和统一内存：\n" + "\n".join(f"• {model}" for model in models)
        alert.setInformativeText_(details)
        if report.ready:
            self.paused = False
            self._set_setup_complete(True)
            alert.addButtonWithTitle_("完成")
            alert.runModal()
            return
        alert.addButtonWithTitle_("确认并开始设置")
        alert.addButtonWithTitle_("稍后")
        self._set_setup_complete(False)
        if alert.runModal() == NSAlertFirstButtonReturn:
            if self._collect_setup_preferences(report):
                self._start_setup()

    def configureModels_(self, sender):
        if self._collect_setup_preferences(None):
            self._set_setup_complete(False)
            self._start_setup()

    def configureAdvanced_(self, sender):
        """Open the complete configuration editor in one transaction."""
        self._build_advanced_config_window()
        self.advanced_window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)

    def _build_advanced_config_window(self):
        if getattr(self, "advanced_window", None) is not None:
            return
        self.advanced_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(760, 60, 720, 780),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        self.advanced_window.setTitle_("cohelper 高级配置")
        self.advanced_window.setReleasedWhenClosed_(False)
        self.advanced_controls = {}

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(16, 64, 688, 696))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        document = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 660, 1880))
        scroll.setDocumentView_(document)
        self.advanced_window.contentView().addSubview_(scroll)
        self._advanced_document = document

        y = 1840
        y = self._advanced_section(document, y, "运行模块", "控制剪贴板内容会经过哪些处理，以及是否允许访问外部 API。")
        for key, title in (
            ("translation", "启用翻译"),
            ("knowledge_search", "启用知识库检索"),
            ("knowledge_answer", "启用知识回答/总结"),
            ("overlay", "启用左侧输出浮层与本机输出接口"),
            ("voice_input", "启用本地语音输入（需要 Whisper 与麦克风权限）"),
            ("voice_output", "启用本地答案朗读（macOS 系统语音）"),
            ("voice_direct_actions", "启用受保护的语音直行动作（默认关闭）"),
        ):
            y = self._advanced_switch(document, y, key, title, self.config.enabled(key))
        y = self._advanced_switch(document, y, "allow_external_api", "允许外部 API（默认关闭）", bool(self.config.section("privacy")["allow_external_api"]))

        y -= 12
        y = self._advanced_section(document, y, "剪贴板", "轮询和去抖参数以毫秒为单位；最小/最大字符数用于拒绝不适合处理的内容。")
        clipboard = self.config.section("clipboard")
        for key, title in (("min_chars", "最小字符数"), ("max_chars", "最大字符数"), ("poll_interval_ms", "轮询间隔（毫秒）"), ("debounce_ms", "去抖延迟（毫秒）")):
            y = self._advanced_text(document, y, f"clipboard.{key}", title, str(clipboard[key]))
        y = self._advanced_switch(document, y, "process_plain_text_only", "只处理纯文本", bool(clipboard["process_plain_text_only"]))

        y -= 12
        y = self._advanced_section(document, y, "知识库与 QMD", "collection 必须与 QMD 索引中的名称一致；修改 embedding 模型后保存并重新运行环境设置。")
        knowledge = self.config.section("knowledge")
        qmd = self.config.section("qmd")
        y = self._advanced_text(document, y, "knowledge.collection", "Collection", str(knowledge["collection"]))
        y = self._advanced_path(document, y, "knowledge.source_path", "知识库目录", str(knowledge.get("source_path", "")))
        for key, title in (("limit", "检索结果数"), ("query_timeout_seconds", "查询超时（秒）"), ("max_summary_source_chars", "总结最大来源字符数")):
            y = self._advanced_text(document, y, f"knowledge.{key}", title, str(knowledge[key]))
        y = self._advanced_text(document, y, "qmd.command", "QMD 命令", str(qmd["command"]))
        y = self._advanced_text(document, y, "qmd.index", "QMD 索引", str(qmd["index"]))
        y = self._advanced_switch(document, y, "qmd.no_rerank", "禁用 reranking", bool(qmd["no_rerank"]))
        for key, title in (("embedding", "Embedding 模型"), ("reranking", "Reranking 模型"), ("generation", "Generation 模型")):
            y = self._advanced_text(document, y, f"qmd.models.{key}", title, str(qmd["models"].get(key, "")), placeholder="留空使用 QMD 默认值")

        for kind, title in (("translation", "翻译模型"), ("summary", "总结模型")):
            y -= 12
            y = self._advanced_section(document, y, title, "API Key 只保存到 macOS Keychain，不会写入 config.yaml。")
            section = self.config.section(kind)
            providers = ["ollama", "openai-compatible"] if kind == "translation" else ["ollama"]
            y = self._advanced_popup(document, y, f"{kind}.provider", "Provider", providers, str(section["provider"]))
            y = self._advanced_text(document, y, f"{kind}.model", "模型名称", str(section["model"]))
            y = self._advanced_text(document, y, f"{kind}.base_url", "Base URL", str(section["base_url"]))
            y = self._advanced_text(document, y, f"{kind}.timeout_seconds", "超时（秒）", str(section["timeout_seconds"]))
            y = self._advanced_text(document, y, f"{kind}.credential_account", "Keychain Account", str(section.get("credential_account", kind)))
            y = self._advanced_secret(document, y, f"{kind}.api_key", "API Key（留空不修改）")

        y -= 12
        y = self._advanced_section(document, y, "视觉与操作", "视觉模型仅允许本机 Ollama；操作只允许白名单应用中的单次确认点击。")
        vision = self.config.section("vision")
        actions = self.config.section("actions")
        y = self._advanced_text(document, y, "vision.model", "视觉模型", str(vision["model"]))
        y = self._advanced_text(document, y, "vision.base_url", "Ollama URL", str(vision["base_url"]))
        y = self._advanced_text(document, y, "vision.timeout_seconds", "视觉超时（秒）", str(vision["timeout_seconds"]))
        y = self._advanced_text(document, y, "actions.allowed_bundle_ids", "允许的 Bundle ID（逗号分隔）", ",".join(actions["allowed_bundle_ids"]))
        y = self._advanced_text(document, y, "actions.allowed_capabilities", "原生安全能力（逗号分隔）", ",".join(actions["allowed_capabilities"]))
        y = self._advanced_text(document, y, "actions.minimum_confidence", "最低视觉置信度", str(actions["minimum_confidence"]))
        y = self._advanced_text(document, y, "actions.screenshot_max_age_seconds", "截图最大时效（秒）", str(actions["screenshot_max_age_seconds"]))
        y = self._advanced_text(document, y, "actions.confirmation_ttl_seconds", "确认有效期（秒）", str(actions["confirmation_ttl_seconds"]))

        y -= 12
        y = self._advanced_section(document, y, "Telegram Bridge", "Token 只保存到 macOS Keychain；启用后使用 ai-drive-telegram 启动。")
        telegram = self.config.section("telegram")
        y = self._advanced_switch(document, y, "telegram.enabled", "启用 Telegram Bridge", bool(telegram["enabled"]))
        y = self._advanced_text(document, y, "telegram.allowed_user_id", "允许的 Telegram User ID", str(telegram["allowed_user_id"]))
        y = self._advanced_text(document, y, "telegram.credential_account", "Keychain Account", str(telegram["credential_account"]))
        y = self._advanced_secret(document, y, "telegram.token", "Telegram Token（留空不修改）")

        # Keep the document tall enough for the top-most section.  The scroll
        # view, rather than the window, owns the vertical layout.
        document.setFrameSize_((660, 1880))
        cancel = NSButton.alloc().initWithFrame_(NSMakeRect(500, 18, 90, 30))
        cancel.setTitle_("取消")
        cancel.setTarget_(self)
        cancel.setAction_("cancelAdvancedConfig:")
        self.advanced_window.contentView().addSubview_(cancel)
        save = NSButton.alloc().initWithFrame_(NSMakeRect(600, 18, 100, 30))
        save.setTitle_("保存配置")
        save.setKeyEquivalent_("\\r")
        save.setTarget_(self)
        save.setAction_("saveAdvancedConfig:")
        self.advanced_window.contentView().addSubview_(save)

    @staticmethod
    def _advanced_label(parent, y, title, width=210):
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(20, y, width, 24))
        label.setStringValue_(title)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        parent.addSubview_(label)

    def _advanced_section(self, parent, y, title, help_text):
        heading = NSTextField.alloc().initWithFrame_(NSMakeRect(20, y, 620, 28))
        heading.setStringValue_(title)
        heading.setFont_(NSFont.boldSystemFontOfSize_(16))
        heading.setBezeled_(False)
        heading.setDrawsBackground_(False)
        heading.setEditable_(False)
        parent.addSubview_(heading)
        y -= 23
        detail = NSTextField.alloc().initWithFrame_(NSMakeRect(20, y, 620, 18))
        detail.setStringValue_(help_text)
        detail.setFont_(NSFont.systemFontOfSize_(11))
        detail.setTextColor_(NSColor.secondaryLabelColor())
        detail.setBezeled_(False)
        detail.setDrawsBackground_(False)
        detail.setEditable_(False)
        parent.addSubview_(detail)
        return y - 34

    def _advanced_text(self, parent, y, key, title, value, placeholder=""):
        self._advanced_label(parent, y, title)
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(235, y, 405, 24))
        field.setStringValue_(value)
        if placeholder:
            field.setPlaceholderString_(placeholder)
        parent.addSubview_(field)
        self.advanced_controls[key] = field
        return y - 34

    def _advanced_secret(self, parent, y, key, title):
        self._advanced_label(parent, y, title)
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(235, y, 405, 24))
        field.setPlaceholderString_("留空保持现有 Keychain 凭据")
        parent.addSubview_(field)
        self.advanced_controls[key] = field
        return y - 34

    def _advanced_switch(self, parent, y, key, title, value):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(20, y, 620, 24))
        button.setButtonType_(NSButtonTypeSwitch)
        button.setTitle_(title)
        button.setState_(1 if value else 0)
        parent.addSubview_(button)
        self.advanced_controls[key] = button
        return y - 30

    def _advanced_popup(self, parent, y, key, title, values, selected):
        self._advanced_label(parent, y, title)
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(235, y, 405, 26), False)
        popup.addItemsWithTitles_(values)
        if selected in values:
            popup.selectItemWithTitle_(selected)
        parent.addSubview_(popup)
        self.advanced_controls[key] = popup
        return y - 34

    def _advanced_path(self, parent, y, key, title, value):
        y = self._advanced_text(parent, y, key, title, value, placeholder="可留空")
        browse = NSButton.alloc().initWithFrame_(NSMakeRect(570, y + 34, 70, 24))
        browse.setTitle_("选择…")
        browse.setTarget_(self)
        browse.setAction_("browseAdvancedSource:")
        parent.addSubview_(browse)
        return y

    def browseAdvancedSource_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setTitle_("选择知识库目录")
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(False)
        if panel.runModal() == NSModalResponseOK:
            self.advanced_controls["knowledge.source_path"].setStringValue_(str(panel.URL().path()))

    def cancelAdvancedConfig_(self, sender):
        self.advanced_window.orderOut_(None)

    def saveAdvancedConfig_(self, sender):
        candidate = Config(copy.deepcopy(self.config.values))
        try:
            for key, control in self.advanced_controls.items():
                if key == "allow_external_api":
                    candidate.section("privacy")["allow_external_api"] = control.state() == 1
                elif key in {"translation", "knowledge_search", "knowledge_answer", "overlay", "voice_input", "voice_output", "voice_direct_actions", "process_plain_text_only"}:
                    target = "features" if key in {"translation", "knowledge_search", "knowledge_answer", "overlay", "voice_input", "voice_output", "voice_direct_actions"} else "clipboard"
                    candidate.section(target)[key] = control.state() == 1
                elif key in {"qmd.no_rerank", "telegram.enabled"}:
                    section_name, field = key.split(".")
                    candidate.section(section_name)[field] = control.state() == 1
                elif key.endswith(".provider"):
                    kind = key.split(".")[0]
                    candidate.section(kind)["provider"] = str(control.titleOfSelectedItem())
                elif key.endswith(".api_key"):
                    continue
                elif key == "telegram.token":
                    continue
                else:
                    parts = key.split(".")
                    section = candidate.section(parts[0])
                    value = str(control.stringValue()).strip()
                    if len(parts) == 2:
                        field = parts[1]
                        if field in {"allowed_bundle_ids", "allowed_capabilities"}:
                            value = [item.strip() for item in value.split(",") if item.strip()]
                        elif field == "minimum_confidence":
                            value = float(value)
                        elif field in {"min_chars", "max_chars", "poll_interval_ms", "debounce_ms", "limit", "query_timeout_seconds", "max_summary_source_chars", "timeout_seconds", "screenshot_max_age_seconds", "confirmation_ttl_seconds", "allowed_user_id"}:
                            value = int(value)
                        section[field] = value
                    else:
                        section[parts[1]][parts[2]] = value
            candidate._validate()
            keychain = KeychainStore()
            pending = []
            for kind in ("translation", "summary"):
                secret = str(self.advanced_controls[f"{kind}.api_key"].stringValue()).strip()
                if secret:
                    pending.append((str(candidate.section(kind)["credential_account"]), secret))
            telegram_token = str(self.advanced_controls["telegram.token"].stringValue()).strip()
            if telegram_token:
                pending.append((str(candidate.section("telegram")["credential_account"]), telegram_token))
            old = {account: keychain.get(account) for account, _ in pending}
            for account, secret in pending:
                keychain.set(account, secret)
            candidate.save()
        except Exception as exc:
            for account, secret in locals().get("old", {}).items():
                try:
                    keychain.set(account, secret) if secret else keychain.delete(account)
                except Exception:
                    pass
            self._show_error("配置保存失败", str(exc))
            return
        previous_overlay_enabled = self.config.enabled("overlay")
        previous_voice_enabled = self.config.enabled("voice_input")
        previous_voice_output_enabled = self.config.enabled("voice_output")
        previous_voice_direct_enabled = self.config.enabled("voice_direct_actions")
        previous_voice_direct_enabled = self.config.enabled("voice_direct_actions")
        self.config = candidate
        config_generation = self.coordinator.update_config(candidate)
        self.output_generation = max(self.output_generation, config_generation)
        if candidate.enabled("overlay") and not previous_overlay_enabled:
            self._start_output_feature()
        elif previous_overlay_enabled and not candidate.enabled("overlay"):
            self._stop_output_feature()
        if candidate.enabled("voice_input") and not previous_voice_enabled:
            self._start_voice_feature()
        elif previous_voice_enabled and not candidate.enabled("voice_input"):
            self._stop_voice_feature()
        if candidate.enabled("voice_output") and not previous_voice_output_enabled:
            self._start_speech_feature()
        elif previous_voice_output_enabled and not candidate.enabled("voice_output"):
            self._stop_speech_feature()
        if candidate.enabled("voice_direct_actions") and not previous_voice_direct_enabled:
            self._start_voice_direct_action_feature()
        elif previous_voice_direct_enabled and not candidate.enabled("voice_direct_actions"):
            self._stop_voice_direct_action_feature()
        if candidate.enabled("voice_direct_actions") and not previous_voice_direct_enabled:
            self._show_info("语音直行动作", "已启用配置开关；在完成权限、目标复核和紧急停止检查前不会执行动作。")
        if hasattr(self, "timer"):
            self.timer.invalidate()
            self._start_clipboard_timer()
        self.advanced_window.orderOut_(None)
        self._set_status("cohelper")

    def _set_setup_complete(self, complete):
        state = SetupState.load()
        state.setup_complete = complete
        state.save()
        self.setup_state = state

    def _collect_setup_preferences(self, report):
        candidate = Config(copy.deepcopy(self.config.values))
        pending_credentials = []
        labels = {"translation": "翻译", "summary": "总结"}
        for kind, feature in (("translation", "translation"), ("summary", "knowledge_answer")):
            section = candidate.section(kind)
            if candidate.enabled(feature):
                provider_label = "Ollama" if section["provider"] == "ollama" else "OpenAI-compatible"
                value = self._prompt_text(f"选择{labels[kind]}模型", f"{provider_label} 模型名称", str(section["model"]))
                if value is None:
                    return False
                section["model"] = value
                if section["provider"] == "openai-compatible":
                    account = str(section.get("credential_account", kind))
                    api_key = self._prompt_secret(f"设置{labels[kind]} API Key", "密钥只写入 macOS Keychain；留空表示不修改")
                    if api_key:
                        pending_credentials.append((account, api_key))

        if candidate.enabled("knowledge_search"):
            qmd_models = candidate.section("qmd")["models"]
            qmd_labels = {
                "embedding": "QMD embedding 模型",
                "reranking": "QMD reranking 模型",
                "generation": "QMD query expansion 模型",
            }
            for key, title in qmd_labels.items():
                current = str(qmd_models.get(key, "")) or "default"
                value = self._prompt_text(title, "输入 Hugging Face/GGUF URI；输入 default 使用 QMD 默认值", current)
                if value is None:
                    return False
                qmd_models[key] = "" if value.lower() == "default" else value

        qmd_failed = report is not None and any(check.name == "QMD" and not check.ok for check in report.checks)
        knowledge = candidate.section("knowledge")
        if candidate.enabled("knowledge_search") and qmd_failed and not knowledge.get("source_path"):
            panel = NSOpenPanel.openPanel()
            panel.setTitle_("选择包含 Markdown 文件的知识库目录")
            panel.setCanChooseDirectories_(True)
            panel.setCanChooseFiles_(False)
            panel.setAllowsMultipleSelection_(False)
            if panel.runModal() != NSModalResponseOK:
                return False
            knowledge["source_path"] = str(panel.URL().path())
            name = self._prompt_text("设置 QMD collection", "Collection 名称", str(knowledge["collection"]))
            if name is None:
                return False
            knowledge["collection"] = name
        try:
            keychain = KeychainStore()
            old_credentials = {account: keychain.get(account) for account, _ in pending_credentials}
            for account, api_key in pending_credentials:
                keychain.set(account, api_key)
            candidate.save()
        except Exception as exc:
            for account, old_value in locals().get("old_credentials", {}).items():
                try:
                    keychain.set(account, old_value) if old_value else keychain.delete(account)
                except Exception:
                    pass
            self._show_error("配置保存失败", str(exc))
            return False
        self.config = candidate
        self.coordinator.config = candidate
        return True

    @staticmethod
    def _show_error(title, message):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_("确定")
        alert.runModal()

    @staticmethod
    def _show_info(title, message):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_("确定")
        alert.runModal()

    @staticmethod
    def _prompt_text(title, message, current):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 24))
        field.setStringValue_(current)
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("保存")
        alert.addButtonWithTitle_("取消")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return None
        value = str(field.stringValue()).strip()
        return value or None

    @staticmethod
    def _prompt_secret(title, message):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 24))
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("保存")
        alert.addButtonWithTitle_("跳过")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return ""
        return str(field.stringValue()).strip()

    def _start_setup(self):
        if self.setup_thread is not None and self.setup_thread.is_alive():
            self._show_error("环境设置正在运行", "请等待当前任务完成，或从菜单栏选择“取消环境设置”。")
            return
        self._ensure_window()
        self.window.makeKeyAndOrderFront_(None)
        local_models = EnvironmentDoctor(self.config.values).required_ollama_models()
        if local_models and not resolve_command("ollama"):
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_("https://ollama.com/download/mac"))
            self._append_result("环境设置", "已打开 Ollama 官方下载页。安装并启动 Ollama 后，请从菜单栏重新运行环境诊断。")
            return
        if self.config.enabled("knowledge_search") and not resolve_command("node"):
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_("https://nodejs.org/en/download"))
            self._append_result("环境设置", "已打开 Node.js 官方下载页。QMD 需要 Node.js 22+；安装后请重新运行环境诊断。")
            return
        self.text_view.setString_("环境设置已确认，准备安装所选模型和初始化 QMD……")
        installer = SetupInstaller(self.config.values)
        self.setup_installer = installer
        self.setup_thread = threading.Thread(target=self._perform_setup, args=(installer,), daemon=True)
        self.setup_thread.start()

    def cancelSetup_(self, sender):
        if self.setup_installer is not None:
            self.setup_installer.cancel()
            self._append_result("环境设置", "正在取消当前安装任务……")

    def _perform_setup(self, installer):
        progress = lambda message: AppHelper.callAfter(self._append_result, "设置进度", message)
        try:
            if self.config.enabled("knowledge_search") and not resolve_command(str(self.config.section("qmd")["command"])):
                installer.install_qmd(progress)
            installer.pull_ollama_models(progress)
            if self.config.enabled("knowledge_search"):
                installer.initialize_qmd_models(progress)
            AppHelper.callAfter(self._append_result, "环境设置", "安装步骤完成，正在重新诊断……")
            self._diagnose()
        except Exception as exc:
            AppHelper.callAfter(self._append_result, "环境设置失败", f"{type(exc).__name__}: {exc}")
        finally:
            if self.setup_installer is installer:
                self.setup_installer = None


def run() -> None:
    app = NSApplication.sharedApplication()
    delegate = CohelperApp.alloc().init()
    app.setDelegate_(delegate)
    app.run()
