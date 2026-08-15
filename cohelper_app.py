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
            on_summary=lambda result: AppHelper.callAfter(self._append_result, "知识回答 / 总结", result.text or result.error),
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
        menu.addItemWithTitle_action_keyEquivalent_("高级配置", "configureAdvanced:", "")
        menu.addItemWithTitle_action_keyEquivalent_("请求视觉操作权限", "requestVisionPermissions:", "")
        menu.addItemWithTitle_action_keyEquivalent_("取消环境设置", "cancelSetup:", "")
        menu.addItemWithTitle_action_keyEquivalent_("打开配置目录", "openConfig:", "")
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItemWithTitle_action_keyEquivalent_("退出", "terminate:", "q")
        self.status_item.setMenu_(menu)

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
        for key, title in (("translation", "启用翻译"), ("knowledge_search", "启用知识库检索"), ("knowledge_answer", "启用知识回答/总结")):
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
                elif key in {"translation", "knowledge_search", "knowledge_answer", "process_plain_text_only"}:
                    target = "features" if key in {"translation", "knowledge_search", "knowledge_answer"} else "clipboard"
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
        self.config = candidate
        self.coordinator.config = candidate
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
