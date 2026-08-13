"""macOS AppKit shell for cohelper."""

from __future__ import annotations

import copy
import subprocess
import threading
from pathlib import Path

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSColor,
    NSFont,
    NSLinkAttributeName,
    NSMenu,
    NSMenuItem,
    NSModalResponseOK,
    NSOpenPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSScrollView,
    NSSecureTextField,
    NSStatusBar,
    NSStatusItem,
    NSTextField,
    NSTextView,
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
from Foundation import NSObject, NSString, NSTimer, NSURL
from PyObjCTools import AppHelper

from cohelper_core import APP_SUPPORT, CONFIG_PATH, Config, ConfigError, TaskCallbacks, TaskCoordinator
from cohelper_setup import EnvironmentDoctor, KeychainStore, SetupInstaller, SetupState, resolve_command


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
        self.status_item: NSStatusItem | None = None
        self.setup_installer = None
        self.setup_thread = None
        return self

    def applicationDidFinishLaunching_(self, notification):
        NSApp().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.last_change_count = NSPasteboard.generalPasteboard().changeCount()
        self._build_status_item()
        self._start_clipboard_timer()
        if self.startup_error:
            self._show_config_error()
            return
        if self.first_run:
            if not CONFIG_PATH.exists():
                self.config.save()
            self.runDiagnostics_(None)

    def _callbacks(self):
        return TaskCallbacks(
            on_started=lambda text: AppHelper.callAfter(self._show_started, text),
            on_translation=lambda result: AppHelper.callAfter(self._append_result, "翻译", result.text or result.error),
            on_knowledge=lambda hits: AppHelper.callAfter(self._append_sources, hits),
            on_summary=lambda result: AppHelper.callAfter(self._append_result, "知识总结", result.text or result.error),
            on_error=lambda error: AppHelper.callAfter(self._append_result, "错误", error),
            on_rejected=lambda reason: AppHelper.callAfter(self._show_rejected, reason),
            on_finished=lambda: AppHelper.callAfter(self._set_status, "cohelper"),
        )

    def _build_status_item(self):
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1)
        self.status_item.button().setTitle_("cohelper")
        menu = NSMenu.alloc().init()
        menu.addItemWithTitle_action_keyEquivalent_("暂停监听", "togglePause:", "")
        menu.addItemWithTitle_action_keyEquivalent_("环境诊断与设置", "runDiagnostics:", "")
        menu.addItemWithTitle_action_keyEquivalent_("模型设置", "configureModels:", "")
        menu.addItemWithTitle_action_keyEquivalent_("取消环境设置", "cancelSetup:", "")
        menu.addItemWithTitle_action_keyEquivalent_("打开配置目录", "openConfig:", "")
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItemWithTitle_action_keyEquivalent_("退出", "terminate:", "q")
        self.status_item.setMenu_(menu)

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
            self.coordinator.submit(text)

    def applicationWillTerminate_(self, notification):
        self.coordinator.cancel()
        if self.setup_installer is not None:
            self.setup_installer.cancel()
        if self.debounce_timer is not None:
            self.debounce_timer.invalidate()

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

    def _show_started(self, text):
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

    def _show_rejected(self, reason):
        self._ensure_window()
        self.text_view.setString_(reason)
        self.window.makeKeyAndOrderFront_(None)

    def _append_result(self, title, content):
        if self.text_view is None:
            return
        current = self.text_view.string() or ""
        self.text_view.setString_(current + f"\n\n===== {title} =====\n{content}")

    def _append_sources(self, hits):
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
        else:
            self._append_result("知识库来源", "未找到可靠知识库来源")

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

    def _set_setup_complete(self, complete):
        state = SetupState.load()
        state.setup_complete = complete
        state.save()
        self.setup_state = state

    def _collect_setup_preferences(self, report):
        candidate = Config(copy.deepcopy(self.config.values))
        pending_credentials = []
        labels = {"translation": "翻译", "summary": "总结"}
        for kind, feature in (("translation", "translation"), ("summary", "knowledge_summary")):
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
