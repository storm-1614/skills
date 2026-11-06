# B 站登录、字幕 API 与风控

## 终端扫码登录与持久会话

`scripts/bili_login.py` 在无头环境请求 Web 二维码登录接口，并在终端渲染二维码。手机 B 站 App 确认后，脚本轮询登录状态、跟随登录回调并收集 `Set-Cookie`。2026 年 Web 扫码流程可能返回跨域 ticket，不能只从 poll URL 查询参数解析 Cookie。

二维码登录和 Cookie 刷新使用 B 站 Web 接口，可能随站点更新而变化。凭据仅保存在用户配置目录 `~/.config/bili-video-summary/credentials.json`，目录权限为 `0700`、文件权限为 `0600`。文件包含 Cookie 与刷新令牌；禁止输出内容、复制到笔记或提交 Git。

每次抓取前，若使用扫码登录凭据，流程会请求 `/x/passport-login/web/cookie/info`。只有接口返回 `refresh=true` 时才执行刷新：使用当前时间戳经 RSA-OAEP-SHA256 得到 `correspondPath`，从 `/correspond/1/<path>` 提取 `refresh_csrf`，调用 `/cookie/refresh` 后再调用 `/confirm/refresh`，保存新 Cookie 和轮换后的 refresh token。若登录已失效或续期失败，提示重新扫码，不输出令牌。接口细节可能随 B 站调整而变化。

凭据读取顺序：用户配置目录扫码凭据 → 临时环境变量 `BILI_SESSDATA` → 项目根目录 `.sessdata` → 匿名。扫码会话优先于旧环境变量，避免旧 SESSDATA 覆盖刚扫码保存的新会话。`BILI_SESSDATA` 和 `.sessdata` 只包含 SESSDATA，无法自动续期；不要把完整 Cookie 字符串粘贴到这两个位置。

## 官方字幕 API

```text
BVID → /x/web-interface/view → aid、cid、标题、UP 主、时长
     → /x/player/wbi/v2?bvid&cid&wts&w_rid → JSON subtitle.subtitles[]
     → （缺中文轨道时）/x/v2/subtitle/web/view?oid=<cid>&pid=<aid>&context_ext={"video_type":1}&type=1&cur_production_type=0&preferred_language=ai-zh&playlist_switch=0
     → Protobuf track list → subtitle_url → JSON body[] {from,to,content}
```

- 新旧字幕接口都使用当前 Cookie；旧版 JSON 接口返回空列表不等于视频没有 AI 字幕。
- 新版接口在旧列表为空或缺中文轨道时查询；返回 Protobuf 二进制，不能当 JSON 或 UTF-8 文本解析。
- 新版接口请求头需声明 `Accept: application/octet-stream`；参数不全时服务端可能以 HTTP 200 返回 JSON 错误，因此先识别 JSON 错误再解码 Protobuf。
- Protobuf 结构：顶层字段 `1` 为 data message；data 中重复字段 `3` 为字幕轨道；轨道字段 `3/4/5` 分别为语言码、显示名称和字幕 URL。
- 轨道选择优先 CC 中文，其次简体中文 AI (`ai-zh` / `zh-Hans-D`)，再选其它语言。AI 轨道输出 `subtitle_kind=ai`。
- 字幕 URL 是有时效的签名地址，拿到后立即下载；不要写入日志或笔记。
- 仅当两种接口都没有轨道时返回 `no_subtitle`。若用户确认播放器显示 AI 字幕，先检查扫码登录状态并重试，不要依据匿名空列表作结论。

## WBI 签名

`/x/player/wbi/v2` 使用 WBI 签名：

1. `/x/web-interface/nav` 返回 `wbi_img.img_url/sub_url`；取文件名去扩展名作为 keys。
2. 按 `MIXIN_KEY_ENC_TAB` 重排 `img_key + sub_key` 前 32 位得到 mixin key。
3. 过滤参数特殊字符，添加 `wts`，按 key 排序并 urlencode。
4. `w_rid = md5(query + mixin_key)`。

请求头使用常见浏览器 `User-Agent` 与 `Referer: https://www.bilibili.com/`。无效签名可能返回 403 或降级数据。

## 风控与字幕质量

旧版脚本的 `risk_flag` 指示字幕片段可能不完整或风控错配：

- `segment_count_too_low`：字幕条数 < 3
- `timestamps_exceed_duration`：最后时间戳超过视频时长 + 90 秒
- `coverage_too_short`：覆盖时长低于 `min(15 秒, 时长×10%)`
- `avg_segment_too_short`：平均每段少于 2.5 字符
- `zh_track_no_cjk`：中文轨道中含汉字的段落比例低于 50%

AI 字幕是平台自动生成文本，可能有识别、分段和标点错误；保留时间戳，但不要称为人工校对逐字稿。
