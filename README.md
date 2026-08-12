# Daily Apple Music Indie Playlist

每天自动生成一份 `YYYY-MM-DD Daily Indie Mix`，并输出到 `output/YYYY-MM-DD.md`。

第一阶段不需要 Apple Music Developer Token。项目使用免费数据源生成可点击 Apple Music 链接：

- iTunes Search API：主要歌曲检索与 Apple Music 链接来源
- MusicBrainz API：备用元数据发现
- Last.fm API：可选，用于更好的热门/高评分倾向候选
- OpenAI API：可选，用于更高质量的候选歌曲规划

## 歌单规则

- 总数：20 首
- Indie Pop / Indie Rock / Alternative：约 12 首
- 新歌/高评分/近年优秀作品：约 4 首
- 经典 Alternative / Indie：约 4 首
- 配置 `OPENAI_API_KEY` 后，AI curator 会先生成 100 首候选，再筛选 20 首
- 自动读取 `data/songs_history.json`
- 历史中出现过的歌曲不会再次加入
- 同一天不会加入多个相同艺术家的歌曲
- 最近 30 天内出现过的歌曲不会再次加入
- 每次生成后自动更新历史

## 项目结构

```text
.
├── src/
│   ├── generator.py
│   ├── ai_curator.py
│   ├── music_api.py
│   ├── recommender.py
│   └── history.py
├── data/
│   └── songs_history.json
├── output/
├── .github/
│   └── workflows/
│       └── daily_playlist.yml
├── requirements.txt
├── README.md
└── .env.example
```

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m src.generator
```

运行后会生成：

```text
output/YYYY-MM-DD.md
data/songs_history.json
```

## 环境变量

```text
OPENAI_API_KEY=
LASTFM_API_KEY=
PLAYLIST_COUNTRY=US
PLAYLIST_OUTPUT_DIR=output
SONGS_HISTORY_PATH=data/songs_history.json
```

`OPENAI_API_KEY` 启用 AI curator：

- 生成 100 首候选
- 60 首 primary：indie pop / indie rock / alternative
- 20 首 recent：最近高评分或高口碑新歌
- 20 首 classic：经典 indie / alternative
- 本地筛选出最终 20 首：12 primary、4 recent、4 classic

`LASTFM_API_KEY` 是可选增强项。没有任何 key 时，项目会使用内置艺术家池和 iTunes Search API fallback。

## GitHub Actions 自动运行

Workflow 文件：

```text
.github/workflows/daily_playlist.yml
```

触发方式：

- 每天北京时间 09:00 自动运行
- 支持 `workflow_dispatch` 手动触发

GitHub Actions 使用 UTC，所以 cron 为：

```yaml
- cron: "0 1 * * *"
```

## GitHub Secrets

进入仓库：

```text
Settings -> Secrets and variables -> Actions
```

可选添加：

- `OPENAI_API_KEY`
- `LASTFM_API_KEY`
- `MAIL_USERNAME`
- `MAIL_PASSWORD`
- `MAIL_TO`

可选 Variables：

- `PLAYLIST_COUNTRY`，默认 `US`

## 每日邮件通知

GitHub Actions 生成并提交每日歌单后，会尝试通过 Gmail SMTP 发送邮件。邮件发送失败不会影响歌单生成。

需要在 GitHub Actions Secrets 中配置：

- `MAIL_USERNAME`：Gmail 邮箱地址
- `MAIL_PASSWORD`：Gmail App Password
- `MAIL_TO`：接收邮箱，可以是你的手机邮箱

SMTP 配置固定为：

```text
server: smtp.gmail.com
port: 465
```

邮件内容包含：

- 今日歌单标题
- 20 首歌曲列表
- Artist - Song
- Apple Music 链接
- `output/YYYY-MM-DD.md` 的 GitHub 文件链接

## 输出示例

```markdown
# 2026-08-12 Daily Indie Mix

## Tracklist

1. Alvvays - Archie, Marry Me
   Album: Alvvays
   Genre: Indie Pop / Indie Rock / Alternative
   Apple Music: https://music.apple.com/...
   Reason: Core indie/alternative recommendation from the curated artist pool.
```

## 自动提交

GitHub Actions 运行后会执行：

```bash
git add .
git commit -m "Generate daily indie playlist"
git push
```

如果当天没有变化，commit 会自动跳过。

## 未来扩展：Apple Music API

`src/music_api.py` 中预留了 `AppleMusicPlaylistClient`。下一阶段可以加入：

- `APPLE_MUSIC_DEVELOPER_TOKEN`
- `APPLE_MUSIC_USER_TOKEN`

然后通过 Apple Music API 自动创建真实的用户资料库 Playlist。
