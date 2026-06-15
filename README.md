# MoviePilot-Plugins - Transmission Tracker 标签

MoviePilot V2 插件：根据种子 tracker 地址，按配置规则自动为 Transmission 添加标签。

## 插件功能

- 按 tracker 关键字匹配规则，为 Transmission 种子追加标签
- 支持多标签：`ourbits.club  我堡/ob/十二大` → 添加 `我堡`、`ob`、`十二大`
- 支持定时任务、立即运行、远程命令 `/tr_tracker_label`
- 仅支持 Transmission 下载器

## 仓库结构

```text
.
├── package.v2.json
└── plugins.v2/
    └── transmissiontrackerlabel/
        └── __init__.py
```

## 发布到 GitHub

1. 在 GitHub 新建仓库（例如 `MoviePilot-Plugins`）
2. 将本仓库内容推送上去：

```bash
git init
git add package.v2.json plugins.v2 README.md
git commit -m "Add TransmissionTrackerLabel plugin v1.0.0"
git branch -M main
git remote add origin https://github.com/你的用户名/MoviePilot-Plugins.git
git push -u origin main
```

3. 确认 GitHub 上能访问：
   - `https://raw.githubusercontent.com/你的用户名/MoviePilot-Plugins/main/package.v2.json`
   - `https://raw.githubusercontent.com/你的用户名/MoviePilot-Plugins/main/plugins.v2/transmissiontrackerlabel/__init__.py`

## 在 MoviePilot 中加载

1. 打开 **设置 → 插件 → 插件市场设置**
2. 在「输入插件仓库地址」中填入：

```text
https://github.com/你的用户名/MoviePilot-Plugins
```

3. 点击 **+** 添加，然后 **保存**
4. 回到 **插件市场**，刷新列表
5. 搜索 **Transmission Tracker 标签** 或 **TransmissionTrackerLabel**
6. 点击 **安装**，配置下载器和标签规则后启用

## 配置说明

规则格式（每行一条）：

```text
tracker关键字    标签1/标签2/标签3
ourbits.club     我堡/ob/十二大
tracker.hdsky.me 空/十二大
```

- 空行和以 `#` 开头的行会被忽略
- tracker 为子串匹配
- 新标签会追加到现有标签，不会删除已有标签

## 更新插件

1. 修改 `plugins.v2/transmissiontrackerlabel/__init__.py` 中的 `plugin_version`
2. 同步更新 `package.v2.json` 中的 `version` 和 `history`
3. 推送到 GitHub
4. 在 MoviePilot 插件市场点击更新/重装
