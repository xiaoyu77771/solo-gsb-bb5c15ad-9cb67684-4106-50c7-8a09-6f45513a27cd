# 循环时间规则展开 API（Recurrence Expansion API）

基于 **Python + FastAPI + Pydantic + python-dateutil** 的 REST 服务，把 iCalendar
`RRULE` / `RDATE` / `EXDATE` 规则展开成查询区间内的具体时间实例。

- 使用 **IANA 时区**（如 `Asia/Shanghai`、`America/New_York`）计算本地时间
- 返回结果按 **UTC 时刻升序**排列，同时保留**本地墙钟时间**与 **UTC 偏移**
- 正确处理夏令时，且行为可通过 `dst_policy` 按请求配置：
  - 本地不存在的时刻（春季跳时 gap）：默认**跳过**（`gap: "skip"`），也可**顺延出缺口**
    （`gap: "shift_forward"`，如一小时跳时中 `02:30` 顺延到 `03:30`）
  - 本地重复的时刻（秋季回拨 fold）：默认**返回两条**（`fold: "both"`，分别带
    `fold: 0/1` 与对应偏移），也可只保留较早一侧（`"earlier"`）或较晚一侧（`"later"`）
- 支持月末规则（`BYMONTHDAY=-1`）、带索引星期（`BYDAY=-1FR`）、`COUNT`/`UNTIL`、
  排除日期（EXDATE）与补充日期（RDATE）
- `POST /business-occurrences` 额外支持把每个原始实例按 `following` /
  `preceding` / `modified_following` 约定调整到营业日（自定义节假日与周末、
  保持时分秒），同一 UTC 时刻合并并通过 `originals` 回溯原始实例
- 无效时区、无效规则、无效区间返回**结构化 JSON 错误**

## 一键启动

需要已安装 Docker（含 Compose 插件）。在项目根目录执行：

```bash
docker compose up --build -d
```

服务随后监听：**http://localhost:8000**

- 健康检查：http://localhost:8000/health
- 交互式 API 文档（Swagger UI）：http://localhost:8000/docs
- 停止服务：`docker compose down`

> 不想用容器时也可以本地运行：
> `pip install -r requirements.txt` 然后
> `uvicorn app.main:app --host 0.0.0.0 --port 8000`

## 配置

通过容器环境变量配置（见 `docker-compose.yml`，均有默认值，无需修改即可使用）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `8000` | 监听端口，容器内固定暴露 8000 |
| `APP_MAX_OCCURRENCES` | `10000` | 单次请求允许展开的最大实例数（同时限制窗口前的扫描量为其 5 倍），超出返回 `too_many_occurrences` |

所有依赖（FastAPI、Pydantic、python-dateutil、uvicorn、tzdata）均在镜像构建时安装，
镜像内还包含系统 `tzdata` 以保证 IANA 时区数据库可用。

## API

### `GET /health`

```json
{ "status": "ok", "version": "1.0.0" }
```

### `POST /occurrences`

请求体字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `timezone` | 是 | IANA 时区名称 |
| `start_local` | 是 | DTSTART：目标时区的本地墙钟时间，ISO-8601，如 `2025-03-30T02:30:00`；带偏移（`Z` / `+08:00`）时先换算到目标时区墙钟时间 |
| `rrule` | 否 | RFC 5545 规则，可带 `RRULE:` 前缀；省略时集合仅含 DTSTART 与 RDATE |
| `interval_start` | 是 | 查询区间起点（**含**），必须带 UTC 偏移，如 `2025-01-01T00:00:00Z` |
| `interval_end` | 是 | 查询区间终点（**不含**），必须带 UTC 偏移 |
| `exdate` | 否 | 要排除的本地墙钟时间列表 |
| `rdate` | 否 | 要补充的本地墙钟时间列表 |
| `dst_policy` | 否 | DST 处理策略对象：`gap` = `skip`（默认）/ `shift_forward`，`fold` = `both`（默认）/ `earlier` / `later`；作用于 DTSTART、RRULE、RDATE |

返回的每个实例：

| 字段 | 说明 |
| --- | --- |
| `local` | 目标时区的本地墙钟时间（ISO-8601，不含偏移；`gap: "shift_forward"` 时为顺延后的墙钟时间） |
| `utc` | UTC 时刻（ISO-8601，末尾 `Z`） |
| `utc_offset` | 该时刻的 UTC 偏移，如 `+08:00` / `-05:00` / `+05:30` |
| `dst` | 该时刻是否处于夏令时 |
| `fold` | `0` = 普通时刻/重复时间的第一次；`1` = 重复时间的第二次 |
| `source` | `rrule`（规则产生）或 `rdate`（补充日期产生） |

### `POST /business-occurrences`

在 `/occurrences` 的全部字段与语义之上新增**营业日调整**。每个原始墙钟实例
（DTSTART / RRULE / RDATE）先移动到营业日（**时分秒保持不变**），再按
`dst_policy` 定位真实时刻；EXDATE 始终按**调整前**的原始墙钟时间排除。

新增字段：

| 字段 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `holidays` | 否 | `[]` | 节假日（非营业日）日期列表，如 `["2025-01-01"]`；也可传完整日期时间，仅取其在目标时区的日期部分 |
| `weekend_days` | 否 | `[5, 6]` | 周末（非营业日）的星期：整数 `0`（周一）…`6`（周日），也接受 iCal 缩写 `"MO"`…`"SU"`；传 `[]` 表示一周七天均为营业日 |
| `business_day_convention` | 否 | `"following"` | 营业日调整约定，见下 |

`business_day_convention` 取值：

- `following`：向后移动到最近的营业日
- `preceding`：向前移动到最近的营业日
- `modified_following`：先尝试向后；若向后会跨入另一个自然月，则改为向前

其余语义：

- 调整只改变日期，**时分秒（含微秒）保持不变**；调整完成后才按
  `dst_policy` 处理 gap / fold
- 多个原始实例（含 RRULE 与 RDATE）经调整 + DST 定位后落到**同一 UTC 时刻**
  （及同一 fold）时合并为一条，`source` 按 **RRULE 优先**，并在新增的
  `originals` 字段中按时间序列出全部合并前的原始墙钟时间
- 落入查询区间的调整后实例才返回；扫描原始实例时窗口两侧会预留
  “节假日数 + 7 天”的余量，因此调整后恰好进入区间的实例不会丢失
- 响应在 `/occurrences` 基础上新增顶层 `business_day_convention`，每个实例新增
  `originals: string[]`（未发生合并时仅含调整前的原始墙钟时间自身）

## 使用示例

### 月末规则

```bash
curl -s -X POST http://localhost:8000/occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "UTC",
    "start_local": "2025-01-31T12:00:00",
    "rrule": "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3",
    "interval_start": "2025-01-01T00:00:00Z",
    "interval_end": "2025-04-01T00:00:00Z"
  }'
```

```json
{
  "timezone": "UTC",
  "count": 3,
  "occurrences": [
    {"local": "2025-01-31T12:00:00", "utc": "2025-01-31T12:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "source": "rrule"},
    {"local": "2025-02-28T12:00:00", "utc": "2025-02-28T12:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "source": "rrule"},
    {"local": "2025-03-31T12:00:00", "utc": "2025-03-31T12:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "source": "rrule"}
  ]
}
```

### 不存在的本地时刻被跳过

纽约时间 2025-03-09 凌晨 02:00→03:00 跳时，`02:30` 不存在，当天实例被跳过：

```bash
curl -s -X POST http://localhost:8000/occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "America/New_York",
    "start_local": "2025-03-08T02:30:00",
    "rrule": "FREQ=DAILY;COUNT=3",
    "interval_start": "2025-03-01T00:00:00Z",
    "interval_end": "2025-03-20T00:00:00Z"
  }'
```

返回 3 月 8 日与 3 月 10 日两条（3 月 9 日跳过）。

### 重复的本地时刻返回两个 fold

纽约时间 2025-11-02 凌晨 02:00 回拨到 01:00，`01:30` 发生两次：

```bash
curl -s -X POST http://localhost:8000/occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "America/New_York",
    "start_local": "2025-11-02T01:30:00",
    "interval_start": "2025-11-02T00:00:00Z",
    "interval_end": "2025-11-03T00:00:00Z"
  }'
```

```json
{
  "timezone": "America/New_York",
  "count": 2,
  "occurrences": [
    {"local": "2025-11-02T01:30:00", "utc": "2025-11-02T05:30:00Z", "utc_offset": "-04:00", "dst": true,  "fold": 0, "source": "rrule"},
    {"local": "2025-11-02T01:30:00", "utc": "2025-11-02T06:30:00Z", "utc_offset": "-05:00", "dst": false, "fold": 1, "source": "rrule"}
  ]
}
```

### DST 策略（dst_policy）

`dst_policy` 为可选对象，缺省为 `{"gap": "skip", "fold": "both"}`（即历史默认行为）：

- `gap`：本地墙钟时间不存在（春季跳时缺口）时
  - `skip`（默认）：丢弃该实例
  - `shift_forward`：按缺口大小向后顺延（如一小时跳时中 `02:30` → `03:30`；
    澳大利亚 Lord Howe 的 30 分钟跳时中 `02:15` → `02:45`）
- `fold`：本地墙钟时间重复出现（秋季回拨）时
  - `both`（默认）：两次各返回一条（`fold: 0` 与 `fold: 1`）
  - `earlier`：只保留较早一侧（`fold: 0`，转换前偏移）
  - `later`：只保留较晚一侧（`fold: 1`，转换后偏移）

策略作用于 DTSTART、RRULE 与 RDATE；EXDATE 始终按**原始**本地墙钟时间匹配。

跳时顺延示例（纽约 2025-03-09 凌晨 02:00→03:00 跳时，`02:30` 顺延为 `03:30`）：

```bash
curl -s -X POST http://localhost:8000/occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "America/New_York",
    "start_local": "2025-03-08T02:30:00",
    "rrule": "FREQ=DAILY;COUNT=3",
    "dst_policy": {"gap": "shift_forward"},
    "interval_start": "2025-03-01T00:00:00Z",
    "interval_end": "2025-03-20T00:00:00Z"
  }'
```

```json
{
  "timezone": "America/New_York",
  "count": 3,
  "occurrences": [
    {"local": "2025-03-08T02:30:00", "utc": "2025-03-08T07:30:00Z", "utc_offset": "-05:00", "dst": false, "fold": 0, "source": "rrule"},
    {"local": "2025-03-09T03:30:00", "utc": "2025-03-09T07:30:00Z", "utc_offset": "-04:00", "dst": true,  "fold": 0, "source": "rrule"},
    {"local": "2025-03-10T02:30:00", "utc": "2025-03-10T06:30:00Z", "utc_offset": "-04:00", "dst": true,  "fold": 0, "source": "rrule"}
  ]
}
```

回拨只保留较晚一侧（`01:30` 重复出现，只返回 `fold: 1` 那条）：

```bash
curl -s -X POST http://localhost:8000/occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "America/New_York",
    "start_local": "2025-11-02T01:30:00",
    "dst_policy": {"fold": "later"},
    "interval_start": "2025-11-02T00:00:00Z",
    "interval_end": "2025-11-03T00:00:00Z"
  }'
```

```json
{
  "timezone": "America/New_York",
  "count": 1,
  "occurrences": [
    {"local": "2025-11-02T01:30:00", "utc": "2025-11-02T06:30:00Z", "utc_offset": "-05:00", "dst": false, "fold": 1, "source": "rrule"}
  ]
}
```

### EXDATE / RDATE

```bash
curl -s -X POST http://localhost:8000/occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "UTC",
    "start_local": "2025-01-01T09:00:00",
    "rrule": "FREQ=DAILY;COUNT=3",
    "exdate": ["2025-01-02T09:00:00"],
    "rdate":  ["2025-01-04T12:00:00"],
    "interval_start": "2025-01-01T00:00:00Z",
    "interval_end": "2025-01-10T00:00:00Z"
  }'
```

语义说明：

- EXDATE 按**本地墙钟时间**匹配（RFC 5545），命中即排除，对 RRULE 与 RDATE 都生效；
  重复墙钟时间的两个 fold 会一并排除。即使 `gap: "shift_forward"` 把实例顺延到了其他
  墙钟时间，EXDATE 仍按**原始**墙钟时间匹配（如排除 `02:30` 可排除被顺延到 `03:30` 的实例，
  而排除 `03:30` 则不影响它）
- 同一 UTC 时刻被规则和 RDATE 同时命中时，**RRULE 优先**，不重复返回；
  `shift_forward` 顺延后的实例若与其他实例落到同一 UTC 时刻，同样按 RRULE 优先去重
- RDATE 命中 fold 时同样返回两个实例（受 `fold` 策略限制）；命中 gap 时默认跳过，
  `gap: "shift_forward"` 时同样顺延
- 查询区间为半开区间 `[interval_start, interval_end)`
- `UNTIL` 同时接受 UTC（`...Z`）、显式偏移与目标时区本地时间，
  内部统一换算为目标时区墙钟时间后比较；`COUNT` 与 `UNTIL` 互斥

### 营业日：following 调整与合并

上海时间每日 09:30，共 5 个原始实例（2024-12-31 至 2025-01-04），把元旦
`2025-01-01` 设为节假日、周末为周六/周日（默认）：

- 12-31（周二）不变
- 01-01（周三，节假日）与 01-02（周四）都落到 **01-02**，合并为一条
- 01-03（周五）不变
- 01-04（周六）顺延到 **01-06（周一）**

```bash
curl -s -X POST http://localhost:8000/business-occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "Asia/Shanghai",
    "start_local": "2024-12-31T09:30:00",
    "rrule": "FREQ=DAILY;COUNT=5",
    "holidays": ["2025-01-01"],
    "interval_start": "2024-12-30T00:00:00Z",
    "interval_end": "2025-01-08T00:00:00Z"
  }'
```

```json
{
  "timezone": "Asia/Shanghai",
  "count": 4,
  "business_day_convention": "following",
  "occurrences": [
    {"local": "2024-12-31T09:30:00", "utc": "2024-12-31T01:30:00Z", "utc_offset": "+08:00", "dst": false, "fold": 0, "source": "rrule",
     "originals": ["2024-12-31T09:30:00"]},
    {"local": "2025-01-02T09:30:00", "utc": "2025-01-02T01:30:00Z", "utc_offset": "+08:00", "dst": false, "fold": 0, "source": "rrule",
     "originals": ["2025-01-01T09:30:00", "2025-01-02T09:30:00"]},
    {"local": "2025-01-03T09:30:00", "utc": "2025-01-03T01:30:00Z", "utc_offset": "+08:00", "dst": false, "fold": 0, "source": "rrule",
     "originals": ["2025-01-03T09:30:00"]},
    {"local": "2025-01-06T09:30:00", "utc": "2025-01-06T01:30:00Z", "utc_offset": "+08:00", "dst": false, "fold": 0, "source": "rrule",
     "originals": ["2025-01-04T09:30:00"]}
  ]
}
```

### 营业日：modified_following 跨月改为向前

月末规则（每月最后一天 17:00）：2025-05-31 是周六，向后会跨入 6 月，因此
modified_following 改为向前到 **05-30（周五）**；而 2025-06-30、2025-07-31
本身就是工作日，无需调整。

```bash
curl -s -X POST http://localhost:8000/business-occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "UTC",
    "start_local": "2025-05-30T17:00:00",
    "rrule": "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=3",
    "business_day_convention": "modified_following",
    "interval_start": "2025-05-01T00:00:00Z",
    "interval_end": "2025-08-15T00:00:00Z"
  }'
```

```json
{
  "timezone": "UTC",
  "count": 3,
  "business_day_convention": "modified_following",
  "occurrences": [
    {"local": "2025-05-30T17:00:00", "utc": "2025-05-30T17:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "source": "rrule",
     "originals": ["2025-05-31T17:00:00"]},
    {"local": "2025-06-30T17:00:00", "utc": "2025-06-30T17:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "source": "rrule",
     "originals": ["2025-06-30T17:00:00"]},
    {"local": "2025-07-31T17:00:00", "utc": "2025-07-31T17:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "source": "rrule",
     "originals": ["2025-07-31T17:00:00"]}
  ]
}
```

### 营业日 + DST + EXDATE

营业日调整发生在 DST 定位**之前**，因此调整后的墙钟时间若落在跳时缺口或
回拨重复区间，仍由 `dst_policy` 决定跳过 / 顺延 / fold；EXDATE 只认调整前
的原始墙钟时间。例如把周六 `2025-03-08 02:30`（纽约）调整到周日
`2025-03-09 02:30`——该时刻正处于春季跳时缺口：默认跳过；
`dst_policy: {"gap": "shift_forward"}` 时顺延为 `03:30`，且 `originals`
仍记录原始的 `2025-03-08T02:30:00`。排除该原始墙钟时间即可移除该实例，
排除调整后的 `2025-03-09T03:30:00` 则不起作用。

## 错误响应

所有错误均为统一结构：`{"error": {"code": ..., "message": ..., "details": [...]}}`。

| HTTP | code | 触发场景 |
| --- | --- | --- |
| 400 | `invalid_timezone` | 非合法 IANA 时区（如 `Not/AZone`） |
| 400 | `invalid_rrule` | 规则无法解析：缺 `FREQ`、非法频率、`INTERVAL=0`、`COUNT<=0`、非法日期值、`UNTIL` 格式错误等 |
| 400 | `invalid_interval` | `interval_start >= interval_end` |
| 400 | `invalid_datetime` | 时间字段语法错误，或查询区间缺少 UTC 偏移 |
| 400 | `too_many_occurrences` | 展开实例数/扫描量超过 `APP_MAX_OCCURRENCES` |
| 422 | `validation_error` | 请求体不是合法 JSON、缺少必填字段或字段值非法（如 `dst_policy` 的 `gap`/`fold` 取值不在允许列表内、`weekend_days` 含非法星期或把整周都设为周末、`business_day_convention` 取值非法）（含字段级 details） |

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py       # FastAPI 应用、路由与异常处理
│   ├── schemas.py    # Pydantic 请求/响应模型
│   ├── engine.py     # RRULE 解析、时区/DST gap/fold 展开核心
│   └── errors.py     # 结构化错误类型
├── tests/
│   ├── test_api.py           # pytest + TestClient 端到端测试
│   └── test_business_api.py  # POST /business-occurrences 端到端测试
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 本地开发与测试

```bash
pip install -r requirements.txt
pip install pytest httpx
pytest -q
uvicorn app.main:app --reload --port 8000
```
