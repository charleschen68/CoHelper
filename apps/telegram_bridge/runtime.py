"""Local Telegram polling runtime for explicit visual click commands."""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from io import BytesIO

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from ai_drive.actions import AccessibilityCapability, ActionService
from ai_drive.macos import MacAccessibilityInspector, QuartzDesktopObserver, QuartzPointerController, QuartzScreenCapture
from ai_drive.vision import OllamaVisionClient, VisionAnalyzer
from ai_drive.workflow import VisualClickWorkflow
from cohelper_core import Config
from cohelper_setup import KeychainStore

from .service import Reply, TelegramCommandHandler


class HelpChatResponder:
    def answer(self, text: str) -> str:
        return "视觉操作请使用 /click <目标描述>。普通消息不会触发电脑操作。"


def build_handler(config: Config) -> tuple[TelegramCommandHandler, int]:
    telegram = config.section("telegram")
    allowed_user_id = int(telegram["allowed_user_id"])
    if allowed_user_id <= 0:
        raise ValueError("telegram.allowed_user_id 必须配置为正整数")
    vision = config.section("vision")
    actions = config.section("actions")
    capture = QuartzScreenCapture()
    analyzer = VisionAnalyzer(
        OllamaVisionClient(str(vision["base_url"]), int(vision["timeout_seconds"])),
        str(vision["model"]),
    )
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
    workflow = VisualClickWorkflow(capture, analyzer, action_service)
    return TelegramCommandHandler(workflow, HelpChatResponder()), allowed_user_id


async def _send_reply(update: Update, reply: Reply) -> None:
    message = update.effective_message
    if message is None:
        return
    if reply.image:
        image = BytesIO(reply.image)
        image.name = "ai-drive-preview.jpg"
        await message.reply_photo(photo=image, caption=reply.text)
    else:
        await message.reply_text(reply.text)


class TelegramRuntime:
    def __init__(self, config: Config | None = None):
        self._config = config or Config.load()
        self._loop = None
        self._application = None

    def run(self, *, stop_signals=(signal.SIGINT, signal.SIGTERM, signal.SIGABRT)) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        telegram_config = self._config.section("telegram")
        if not telegram_config["enabled"]:
            raise RuntimeError("telegram.enabled=false；未启动 Telegram 模块")
        token = KeychainStore().get(str(telegram_config["credential_account"]))
        if not token:
            raise RuntimeError("macOS Keychain 中没有 Telegram Token")
        handler, allowed_user_id = build_handler(self._config)

        async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            del context
            user = update.effective_user
            chat = update.effective_chat
            message = update.effective_message
            if user is None or chat is None or message is None or message.text is None:
                return
            if user.id != allowed_user_id:
                await message.reply_text("权限不足。")
                return
            try:
                reply = await asyncio.to_thread(handler.handle, message.text, user_id=user.id, chat_id=chat.id)
            except Exception as exc:
                reply = Reply(f"操作被拒绝：{type(exc).__name__}: {exc}")
            await _send_reply(update, reply)

        original_runtime_config = _runtime_config(self._config)

        watcher_task = None

        async def post_init(application) -> None:
            nonlocal watcher_task
            watcher_task = asyncio.create_task(
                _watch_runtime_config(application, original_runtime_config),
                name="ai-drive-config-watch",
            )

        async def post_stop(application) -> None:
            del application
            await _cancel_watcher(watcher_task)

        application = (
            ApplicationBuilder()
            .token(token)
            .post_init(post_init)
            .post_stop(post_stop)
            .build()
        )
        self._application = application
        application.add_handler(MessageHandler(filters.TEXT, on_message))
        application.run_polling(drop_pending_updates=True, stop_signals=stop_signals)

    def stop(self) -> None:
        if self._loop is not None and self._application is not None:
            self._loop.call_soon_threadsafe(self._application.stop_running)


def run() -> None:
    TelegramRuntime().run()


def _runtime_config(config: Config) -> tuple[object, ...]:
    """Return security-relevant values that require a clean Bridge restart."""
    telegram = config.section("telegram")
    vision = config.section("vision")
    actions = config.section("actions")
    return (
        telegram["enabled"],
        telegram["allowed_user_id"],
        telegram["credential_account"],
        tuple(sorted(vision.items())),
        tuple(actions["allowed_bundle_ids"]),
        tuple(actions["allowed_capabilities"]),
        actions["minimum_confidence"],
        actions["screenshot_max_age_seconds"],
        actions["confirmation_ttl_seconds"],
    )


async def _watch_runtime_config(
    application,
    original_runtime_config: tuple[object, ...],
    *,
    load_config=Config.load,
    pause=asyncio.sleep,
) -> None:
    """Stop polling when security configuration changes or becomes unreadable."""
    while True:
        await pause(1)
        try:
            current = load_config()
        except Exception:
            application.stop_running()
            return
        if _runtime_config(current) != original_runtime_config:
            application.stop_running()
            return


async def _cancel_watcher(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
