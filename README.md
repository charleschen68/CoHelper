# cohelper

macOS Apple Silicon 悬浮知识助手：用户复制文本后，应用可并行调用本地翻译模型和 QMD 知识库，并在检索完成后生成带来源的总结。

## 当前实现

- `NSPasteboard.changeCount` 监听，不拦截全局快捷键，也不申请辅助功能权限。
- PyObjC/AppKit 菜单栏应用与置顶结果窗口。
- 翻译、QMD 检索、知识总结独立开关；关闭功能不会启动对应模块。
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

菜单栏的“模型设置”可修改翻译、总结及 QMD 三类模型；“取消环境设置”会终止当前安装子进程。高级 provider、API URL 和功能开关在 `config.yaml` 中配置。

API 凭据使用 macOS Keychain。例如为总结 provider 保存凭据：

```bash
security add-generic-password -U -s com.charleschen68.cohelper -a summary -w
```

命令会交互式读取凭据，不要把密钥直接写在命令行参数中。

## 构建 `.app`

```bash
python3 -m pip install -e '.[dev]'
pyinstaller --clean --noconfirm cohelper.spec
./scripts/build_dmg.sh
open dist/cohelper.app
```

本地构建仅使用 ad-hoc 签名，不具备 Apple 公证信任链。对外分发前仍必须完成 Developer ID 签名、notarization 和干净 Mac 安装验证。

## 已知边界

当前版本已实现核心服务、AppKit 壳、模型设置、环境诊断、可取消的受控安装、Keychain、PyInstaller `.app` 和测试版 `.dmg` 构建。尚未完成完整可视化配置编辑器、Developer ID 签名/notarization 和干净 Mac 安装验证，不能把本地构建误报为可公开分发的软件。
