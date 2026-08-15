# CoHelper / AI Drive

仅供本机使用的 macOS Apple Silicon AI 助手基础设施。当前包含剪贴板知识助手、主屏幕视觉分析、安全单击动作和 Telegram Bridge，可供后续 Agent 接入复用。

## 当前实现

- `apps/clipboard_helper` 监听 `NSPasteboard.changeCount`，不拦截全局快捷键。
- PyObjC/AppKit 菜单栏应用与置顶结果窗口。
- 问题、短术语和普通段落都会翻译；术语会改写为“什么是 X？”。
- 翻译、QMD 检索、知识回答/总结独立开关；关闭功能不会调用对应资源。
- 回答模型固定为本机 Ollama `qwen3:8b`，配置校验拒绝远程回答端点和其他回答模型；回答只能依据 QMD 来源。
- `src/ai_drive/vision` 使用 Quartz 截取主显示器，并由本机 `qwen2.5vl:7b` 返回严格坐标 JSON。
- `src/ai_drive/actions` 使用 Accessibility 二次校验、应用白名单、截图时效和一次性确认保护 Quartz 单击。
- `apps/telegram_bridge` 只接受 `/click`、`/confirm` 和 `/cancel` 动作协议，不接受任意 Python 或 Shell。
- Ollama 与 OpenAI-compatible provider。
- 默认 QMD collection 为 `jarvis-wiki`，不读取 `raw/`。
- 新任务使旧任务失效，旧结果不会覆盖最新剪贴板内容。
- 首次运行和菜单栏均可执行环境诊断；安装动作必须由用户确认。
- OpenAI-compatible API Key 从 macOS Keychain 读取，不写入 YAML。
- QMD 的 embedding、reranking、generation 模型可独立配置。

## 开发运行

```bash
python3 -m pytest -q
cp config.example.yaml "$HOME/Library/Application Support/cohelper/config.yaml"
python3 cohelper.py
```

首次运行前应确认：

```bash
qmd status
ollama list
```

模型下载由用户明确确认后执行；应用不会把模型打进安装包，也不会在后台自动下载模型。

首次运行流程：

1. 应用保持暂停，不处理打开前已有的剪贴板内容。
2. 检查 macOS、Node.js、QMD collection、Ollama/API 和所选模型。
3. 用户确认模型名称与知识库目录后，才执行下载、建库、embedding 和健康查询。
4. 设置失败或用户选择稍后时保持未完成状态；下次启动继续诊断。

菜单栏的“模型设置”可执行受控的模型安装；“高级配置”可修改剪贴板、知识库、模型、视觉、安全动作和 Telegram 设置。保存时会执行完整配置校验；“取消环境设置”会终止当前安装子进程。

API 凭据使用 macOS Keychain。例如为总结 provider 保存凭据：

```bash
security add-generic-password -U -s com.charleschen68.cohelper -a summary -w
```

命令会交互式读取凭据，不要把密钥直接写在命令行参数中。

## Telegram 视觉点击

1. 在“高级配置”中设置 `telegram.enabled=true` 和你自己的 Telegram User ID。
2. 交互式保存 Bot Token：

```bash
security add-generic-password -U -s com.charleschen68.cohelper -a telegram -w
```

3. 在菜单栏选择“请求视觉操作权限”，然后在“系统设置 → 隐私与安全性”中向 CoHelper 授予“屏幕录制”和“辅助功能”权限并重启应用。
4. 启动本机 Bridge：

```bash
ai-drive-telegram
```

5. 发送：

```text
/click Safari 的刷新按钮
/confirm A7K3
```

操作编号由 Bot 生成，固定 30 秒后失效且只能使用一次。新 `/click` 到达时会立即撤销旧操作。Safari 与 TextEdit 仅通过应用门禁，Accessibility 元素还必须精确匹配 `actions.allowed_capabilities` 中的“应用 + 角色 + 标题 + 原生层级 + 可选标识符”；安全默认仅启用 Safari 原生工具栏刷新，不启用任何 TextEdit 操作，网页 `AXWebArea` 中的同名伪造按钮始终拒绝。视觉推理后和确认时都会重新截图并要求屏幕摘要、应用、显示器和 Accessibility 语义完全一致。敏感按钮和无法确认的目标会被拒绝。Telegram 预览会经过 Telegram 服务器，不应在敏感桌面上使用。

Bridge 是独立的手动进程，不嵌入菜单栏线程，也不随登录自动启动。运行期间若 Telegram、视觉或动作安全配置改变，Bridge 会停止；重新执行 `ai-drive-telegram` 才会加载新配置。

## 构建 `.app`

```bash
python3 -m pip install -e '.[dev]'
pyinstaller --clean --noconfirm cohelper.spec
./scripts/build_dmg.sh
open dist/cohelper.app
```

本地构建仅使用 ad-hoc 签名，不具备 Apple 公证信任链。对外分发前仍必须完成 Developer ID 签名、notarization 和干净 Mac 安装验证。

## 已知边界

视觉动作第一版只支持主显示器和单次左键单击，不支持多屏、键盘、拖拽、双击或盲点回退。Telegram Bridge 不自动登录启动。Developer ID 签名/notarization 和干净 Mac 安装验证仍未完成，不能把本地构建误报为可公开分发的软件。

详细设计见 [架构](docs/architecture.md)、[安全模型](docs/security-model.md) 和 [进度日志](docs/progress.md)。
