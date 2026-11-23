# 声页：PPT 转口播与静态数字人视频

上传 PPTX，选择音色和语速，由 Dify Workflow 完成「提取文本 → 整理口播稿 → TTS」，网页端实时查看进度、试听并下载音频；音频完成后上传一张数字人图片，由 FFmpeg 合成为 1280×720 的数字人口播 MP4。

> 当前视频是**静态数字人形象 + AI 口播音频的视频合成**：画面保持上传图片不变，仅匹配音频时长，不包含口型同步、面部动作或身体动作。

## 当前实际功能

1. Vue 3 前端上传 PPTX、选择音色与语速。
2. FastAPI 创建音频任务并返回任务 ID，创建后立即响应。
3. 后台 Worker 调用 Dify Workflow 提取文本、生成口播稿与 TTS 音频。
4. MySQL 持久化音频任务、视频任务与状态。
5. 网页在线播放 MP3/WAV，并支持下载。
6. 音频完成后上传 PNG / JPG / JPEG 数字人图片。
7. 独立 `video-worker` 使用 `FFmpegStillImageRenderer` 合成视频。
8. 静态数字人图片 + 口播音频生成 MP4。
9. 网页在线播放 MP4，并支持下载。
10. Docker Compose 一键启动全部服务。
11. Alembic 管理数据库迁移。
12. 校验 PPTX 结构、图片完整解码、上传大小与文件名安全。
13. API、数据库、Docker 服务均带健康检查。
14. 前端每 2 秒轮询任务状态，刷新页面后自动恢复。
15. `MOCK_DIFY=true` 时可在没有 Dify Key 的情况下演示完整流程。

## 完整业务流程

```text
上传 PPTX
   │
   ▼
FastAPI 校验并保存源文件 ──► jobs 表（queued）
   │
   ▼
音频 Worker 抢占任务 ──► Dify Workflow
   │                      ├─ 文档提取
   │                      ├─ LLM 生成口播稿
   │                      └─ TTS 生成音频
   ▼
保存 MP3/WAV + 文稿 ──► jobs 表（completed）
   │
   ├─► 前端播放 / 下载音频
   │
   ▼
上传数字人图片 ──► video_jobs 表（queued）
   │
   ▼
video-worker 抢占任务 ──► FFmpegStillImageRenderer
   │                        静态图片循环 + 音频
   ▼
1280×720 H.264 + AAC MP4 ──► video_jobs 表（completed）
   │
   ▼
前端播放 / 下载 MP4
```

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3.5、Vite 5、原生 CSS |
| API | FastAPI 0.115、Pydantic Settings |
| 数据层 | SQLAlchemy 2、MySQL 8.4、Alembic |
| AI 能力 | Dify Workflow（文档提取 / LLM / TTS） |
| 视频合成 | FFmpeg 7.1（静态二进制）、ffprobe |
| 部署 | Docker Compose、Nginx |

## 项目架构

```text
浏览器（Vue 3）
   │  上传 PPTX / 图片；轮询状态；播放与下载
   ▼
Nginx（web:80 → 宿主机 WEB_PORT）
   │  /api 反向代理
   ▼
FastAPI（api:8000）──────► MySQL（jobs / video_jobs）
   │                          ▲
   │ 写入任务 / 读取状态         │ 条件 UPDATE 抢占任务
   ▼                          │
Docker Volume（/data）◄── worker / video-worker
   │                          │
   │                          ├─► Dify Workflow
   │                          └─► FFmpeg + ffprobe
   ▼
PPTX / 音频 / 数字人图片 / MP4
```

- API 只负责校验、落盘和创建任务，不在请求内等待 TTS 或视频渲染。
- 音频 Worker 与视频 Worker 是独立进程，通过 MySQL 条件 UPDATE 原子抢占任务。
- 视频渲染封装在 `VideoRenderer` 接口后，当前唯一实现为 `FFmpegStillImageRenderer`。
- `web`、`api`、`worker`、`video-worker`、`db` 五个服务；业务文件统一存放在 `app_data` Volume。

## 目录结构

```text
ppt-voice/
├── .env.example              # 环境变量模板（不含真实密钥）
├── .gitignore
├── README.md
├── docker-compose.yml
├── backend/
│   ├── Dockerfile            # 内置静态 ffmpeg / ffprobe
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       ├── 0001_create_jobs.py
│   │       └── 0002_create_video_jobs.py
│   └── app/
│       ├── main.py           # FastAPI 装配
│       ├── config.py         # 全部配置项
│       ├── database.py       # Engine / Session / Base
│       ├── models.py         # Job / VideoJob / JobStatus
│       ├── schemas.py        # API 响应模型
│       ├── storage.py        # 存储抽象与本地实现
│       ├── worker.py         # 音频 Worker
│       ├── api/
│       │   ├── health.py
│       │   ├── jobs.py
│       │   └── videos.py
│       ├── services/
│       │   ├── jobs.py
│       │   ├── dify.py
│       │   ├── mock_dify.py
│       │   ├── video_jobs.py
│       │   └── video_renderer.py
│       └── workers/
│           └── video_worker.py
└── frontend/
    ├── Dockerfile
    ├── nginx.conf
    ├── index.html
    ├── package.json
    ├── package-lock.json
    ├── vite.config.js
    └── src/
        ├── main.js
        ├── App.vue
        └── styles.css
```

## 环境要求

- Docker Desktop 或 Docker Engine，带 Docker Compose v2。
- 一个可用的 Dify Workflow（没有时可用 `MOCK_DIFY=true` 演示）。
- 无需在宿主机安装 FFmpeg：后端镜像已内置静态 `ffmpeg` 与 `ffprobe`。

## 快速开始

```powershell
cd ppt-voice
Copy-Item .env.example .env
```

编辑 `.env`：

- 设置 `MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD`（建议只用字母、数字、下划线，避免拼进连接串时的转义问题）；
- 正式使用填写 `DIFY_API_KEY`；
- 没有 Dify Key 时先设 `MOCK_DIFY=true` 演示完整流程。

启动：

```powershell
docker compose up --build
```

启动后访问：

- 页面：<http://localhost:8080>（端口由 `.env` 的 `WEB_PORT` 控制；被占用时改为 8088 等）
- API 健康检查：<http://localhost:8000/api/health>
- OpenAPI 文档：<http://localhost:8000/api/docs>

容器启动顺序：`db` 健康 → `api` 执行 `alembic upgrade head` 并启动 → 健康后 `worker`、`video-worker`、`web` 启动。

查看状态与日志：

```powershell
docker compose ps
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f video-worker
```

停止与重启：

```powershell
docker compose stop          # 停止容器，保留数据
docker compose restart       # 重启现有容器
docker compose down          # 删除容器，保留 Volume
```

> 不要执行 `docker compose down -v`，除非确认可以丢弃 MySQL 与业务文件。


## 环境变量

`.env.example` 只包含项目真实使用的变量。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MYSQL_PASSWORD` | 必填 | 应用数据库用户密码 |
| `MYSQL_ROOT_PASSWORD` | 必填 | MySQL root 密码 |
| `DIFY_BASE_URL` | `https://api.dify.ai/v1` | Dify API 地址 |
| `DIFY_API_KEY` | 空 | Workflow 应用 Key，仅服务端使用 |
| `DIFY_FILE_INPUT` | `ppt_file` | PPTX 文件输入变量名 |
| `DIFY_VOICE_INPUT` | `voice` | 音色输入变量名 |
| `DIFY_SPEED_INPUT` | `speed` | 语速输入变量名 |
| `DIFY_AUDIO_OUTPUT` | `audio` | 音频输出变量名 |
| `DIFY_TEXT_OUTPUT` | `transcript` | 口播稿输出变量名 |
| `DIFY_RESPONSE_MODE` | `blocking` | `blocking` 或 `streaming`，长任务建议 `streaming` |
| `DIFY_CONNECT_TIMEOUT` | `10` | 连接超时（秒） |
| `DIFY_READ_TIMEOUT` | `600` | 读取超时（秒） |
| `DIFY_TOTAL_TIMEOUT` | `660` | 单任务总超时（秒） |
| `DIFY_MAX_RETRIES` | `3` | 网络错误 / 5xx / 429 的最大尝试次数 |
| `DIFY_RETRY_BACKOFF` | `1.5` | 指数退避基数（秒） |
| `DIFY_AUDIO_ALLOWED_HOSTS` | 空 | 额外允许下载音频的域名，逗号分隔 |
| `MOCK_DIFY` | `false` | `true` 时使用本地演示模式 |
| `MOCK_DIFY_DELAY` | `1.5` | 演示模式模拟处理耗时（秒） |
| `MAX_UPLOAD_BYTES` | `52428800` | PPTX 上传上限（50 MB） |
| `AVATAR_MAX_BYTES` | `10485760` | 数字人图片上限（10 MB） |
| `VIDEO_WIDTH` | `1280` | 输出视频宽度 |
| `VIDEO_HEIGHT` | `720` | 输出视频高度 |
| `VIDEO_MAX_BYTES` | `524288000` | 输出视频大小上限（500 MB） |
| `VIDEO_RENDER_TIMEOUT` | `1800` | FFmpeg 渲染超时（秒） |
| `FFMPEG_BIN` | `ffmpeg` | FFmpeg 可执行文件 |
| `FFPROBE_BIN` | `ffprobe` | ffprobe 可执行文件 |
| `STORAGE_BACKEND` | `local` | 当前仅实现 `local` |
| `STORAGE_DIR` | `./data`（容器内 `/data`） | 业务文件目录 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `WEB_PORT` | `8080` | 前端对外端口 |

`DATABASE_URL` 由 Compose 自动注入；本地开发默认使用 SQLite。

## Dify Workflow 约定

在 Dify 创建并发布 **Workflow** 应用。Start 节点输入变量：

| 变量 | 类型 | 说明 |
| --- | --- | --- |
| `ppt_file` | File（单文件，Document） | 上传的 PPTX |
| `voice` | String | `female` / `male` / `warm` |
| `speed` | Number | `0.75` ~ `1.25` |

推荐节点结构：

```text
Start → Document Extractor → LLM 整理口播稿 → TTS → End
```

End 节点输出变量：

| 变量 | 类型 | 说明 |
| --- | --- | --- |
| `transcript` | String | 口播文稿 |
| `audio` | File 或可下载 URL | 建议 MP3，也支持 WAV 等音频格式 |

后端兼容的 `audio` 结构：

- 字符串 URL；
- `{ "url": "..." }`、`{ "remote_url": "..." }`；
- 文件对象 `{ "id": "...", "filename": "..." }`（无 URL 时走 `/files/{id}/preview`）；
- 上述对象的列表，或 `{ "files": [...] }`。

变量名不同可修改 `.env` 中的 `DIFY_*_INPUT` / `DIFY_*_OUTPUT`，无需改代码。

### 音频域名白名单

下载音频前会校验协议（仅 http/https）和域名：

- Dify API 同源域名始终允许；
- 音频在独立对象存储域名时，把它加入 `DIFY_AUDIO_ALLOWED_HOSTS`（逗号分隔）；
- 重定向逐跳重新校验，最多 3 次；非白名单域名直接拒绝，避免 SSRF；
- 下载大小上限 100 MB，并校验 Content-Type 为 `audio/*` 或 `application/octet-stream`。

## 用户操作流程

1. 打开页面，拖入或选择 `.pptx` 文件（最大 50 MB）。
2. 选择音色：清悦女声 / 沉稳男声 / 温暖旁白。
3. 调整语速（0.75× ~ 1.25×），点击「开始生成口播」。
4. 页面每 2 秒查询任务状态；完成后显示口播文稿、音频播放器与下载按钮。
5. 在「生成数字人口播视频」区域上传 PNG / JPG / JPEG 图片（最大 10 MB）。
6. 点击「生成口播视频」，`video-worker` 使用 FFmpeg 合成 1280×720 MP4。
7. 完成后在线播放，或点击「下载 MP4」保存到本地。
8. 刷新页面后，前端通过 `localStorage` 恢复音频任务和视频任务并继续轮询。

## API 接口

所有接口以 `/api` 为前缀，前端通过 Nginx 同源访问。

### `POST /api/jobs`

`multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | 文件 | 是 | 仅 `.pptx`，≤ 50 MB |
| `voice` | string | 是 | `female` / `male` / `warm` |
| `speed` | number | 是 | `0.75` ~ `1.25` |

返回 `201`：

```json
{
  "id": "0f1c...",
  "filename": "季度汇报.pptx",
  "status": "queued",
  "voice": "female",
  "speed": 1.0,
  "transcript": null,
  "error": null,
  "audio_name": null,
  "audio_url": null,
  "download_url": null,
  "created_at": "2026-11-23T10:00:00",
  "updated_at": "2026-11-23T10:00:00"
}
```

错误：文件格式 / 结构不合法 `422`；文件超限 `413`。

### `GET /api/jobs/{id}`

返回相同任务结构。完成后：

- `transcript`：口播文稿；
- `audio_url`：`/api/jobs/{id}/audio`；
- `download_url`：`/api/jobs/{id}/audio?download=1`。

不存在或 ID 非法统一返回 `404 {"detail": "任务不存在"}`。

### `GET /api/jobs/{id}/audio`

- 默认 `Content-Disposition: inline`，供 HTML5 播放器播放；
- 加 `?download=1` 变为 `attachment`；
- `Content-Type` 按实际音频返回（`audio/mpeg`、`audio/wav` 等）；
- 下载文件名为原 PPTX 名称 + 实际音频扩展名。

### `POST /api/jobs/{audio_job_id}/videos`

`multipart/form-data`，字段 `avatar`：PNG / JPG / JPEG，≤ 10 MB。

只有音频任务 `status=completed` 且已有音频时才能创建。返回 `201`：

```json
{
  "id": "5b2e...",
  "audio_job_id": "0f1c...",
  "status": "queued",
  "progress": 0,
  "error": null,
  "video_url": null,
  "download_url": null,
  "created_at": "2026-11-23T10:00:00",
  "updated_at": "2026-11-23T10:00:00"
}
```

错误：音频任务不存在 `404`；音频未完成 `409`；图片超限 `413`；扩展名 / MIME / 内容不合法 `422`。一个音频任务可以创建多个视频任务。

### `GET /api/video-jobs/{video_job_id}`

返回视频任务状态、`progress`（0～100）、`error`、`video_url`、`download_url`。完成后 `video_url` 为 `/api/video-jobs/{id}/video`。

### `GET /api/video-jobs/{video_job_id}/video`

- 未完成时返回 `409 {"detail": "视频尚未生成完成"}`；
- 文件不存在返回 `404`；
- `Content-Type: video/mp4`，默认 `inline` 供 HTML5 播放器播放；
- 加 `?download=1` 变为 `attachment`，文件名为「原 PPTX 名称-数字人.mp4」。

### `GET /api/health`

```json
{ "status": "ok", "database": "ok", "storage": "ok", "mock_dify": false }
```

数据库或存储不可用时返回 `503`，`status` 为 `degraded`。Compose 的 API 健康检查依赖此接口。

## 任务状态说明

音频任务：

```text
queued ──► processing ──► completed
   │            │
   └────────────┴──────► failed（error 为可读提示）
```

视频任务：

```text
queued ──► processing ──► completed
   │            │
   └────────────┴──────► failed（error 为可读提示）
```

视频任务进度：抢占任务后 `5`，开始渲染 `15`，完成后 `100`。前端每 2 秒轮询，终态后停止。

## 文件格式与大小限制

| 类型 | 允许格式 | 上限 | 校验方式 |
| --- | --- | --- | --- |
| 演示文稿 | `.pptx` | 50 MB | ZIP 结构、`ppt/presentation.xml`、解压体积与条目数 |
| 数字人图片 | `.png` / `.jpg` / `.jpeg` | 10 MB | Pillow 完整解码、像素数上限 2500 万、文件头与扩展名一致 |
| 输出音频 | Dify 返回的 `audio/*` | 100 MB | Content-Type、非空、文件头识别扩展名 |
| 输出视频 | MP4 | 500 MB | 临时文件 + ffprobe 校验 video/audio 流后原子替换 |

文件名只保留 basename，去除控制字符与首尾点并截断；所有保存路径由服务端生成，不接受客户端路径，不向前端返回服务器绝对路径。

## 数据持久化

| Volume | 容器路径 | 内容 |
| --- | --- | --- |
| `mysql_data` | `/var/lib/mysql` | MySQL 数据文件 |
| `app_data` | `/data` | PPTX、音频、数字人图片、MP4 |

业务文件按音频任务 ID 分目录：

```text
/data/<audio_job_id>/
  source.pptx
  audio.mp3
  <video_job_id>_avatar.png
  <video_job_id>.mp4
```

数据库只保存文件名，不保存绝对路径。删除容器不会删除 Volume；只有 `docker compose down -v` 才会删除数据卷。

## 常见错误排查

| 现象 | 排查方向 |
| --- | --- |
| 页面能打开但请求 502 | `docker compose ps` 查看 `api` 是否健康；`docker compose logs api` |
| 音频任务一直 `queued` | `worker` 是否运行；数据库连接是否正常 |
| `AI 服务鉴权失败` | 检查 `DIFY_API_KEY` 是否有效、是否与 Workflow 应用匹配 |
| `AI 工作流没有返回音频` | 检查 End 节点是否输出 `audio`，变量名与 `DIFY_AUDIO_OUTPUT` 是否一致 |
| `音频地址不在允许的域名范围内` | 把对象存储域名加入 `DIFY_AUDIO_ALLOWED_HOSTS` |
| 上传返回 413 | PPTX 超过 50 MB 或图片超过 10 MB；可调整 `MAX_UPLOAD_BYTES` / `AVATAR_MAX_BYTES` |
| 图片返回 422 | 文件损坏、不是 PNG/JPG、像素数过大或扩展名与内容不一致 |
| 视频任务 `failed` | 查看 `docker compose logs video-worker` 中的 FFmpeg stderr；确认图片可正常打开 |
| `视频合成服务不可用` | 镜像内 `ffmpeg` / `ffprobe` 缺失，或 `FFMPEG_BIN` / `FFPROBE_BIN` 配置错误 |
| 8080 端口被占用 | 修改 `.env` 中 `WEB_PORT` 后重新 `docker compose up -d` |
| MySQL 密码导致连接失败 | 密码只使用字母、数字、下划线，避免 URL 转义问题 |
| Dify 长任务超时 | 使用 `DIFY_RESPONSE_MODE=streaming`，并调大 `DIFY_READ_TIMEOUT` / `DIFY_TOTAL_TIMEOUT` |



## 当前功能边界

- 视频是**静态数字人形象 + AI 口播音频**的合成，画面不包含口型同步、表情或肢体动作。
- 输出固定为 1280×720、H.264 + AAC、`+faststart`，视频时长与口播音频一致。
- 不支持多人对话、字幕烧录、背景音乐与视频剪辑。
- 任务队列为轮询模型，存在秒级延迟；不支持任务取消与优先级。
- 无用户体系与多租户隔离，适合内部或小范围使用。
