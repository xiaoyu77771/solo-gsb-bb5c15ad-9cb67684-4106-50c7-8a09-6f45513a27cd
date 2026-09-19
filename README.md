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
- `POST /business-occurrences` 在上述展开之上增加**营业日调整**：先按
  `following` / `preceding` / `modified_following` 约定把原始墙钟实例移到营业日
  （跳过周末与节假日、保持时分秒），再进行 DST 定位；调整后落到同一 UTC 时刻的多个
  原始实例会合并，并在 `originals` 中列出
- 无效时区、无效规则、无效区间、无效营业日参数返回**结构化 JSON 错误**

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

### `POST /business-occurrences`

复用 `/occurrences` 的全部字段（`timezone`、`start_local`、`rrule`、`exdate`、
`rdate`、`interval_start`、`interval_end`、`dst_policy`），并额外接收营业日参数：

| 字段 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `holidays` | 否 | `[]` | 节假日（非营业日）日期列表，ISO-8601 日期如 `"2025-01-01"`；也可给日期时间，按目标时区的**墙钟日期**取日 |
| `weekend_days` | 否 | `[6, 7]` | 非营业的 ISO 星期：`1`=周一 … `7`=周日；如周五/周六休息传 `[5, 6]` |
| `business_day_convention` | 否 | `"following"` | 原始实例落在非营业日时的移动约定：`following`（向后）、`preceding`（向前）、`modified_following`（向后移动一旦跨月则改为向前） |

处理顺序：

1. 按 RRULE/RDATE 生成**原始墙钟实例**（与 `/occurrences` 相同）；
2. 每个原始实例只移动**日期**到营业日，**时分秒（含微秒）保持不变**；
   - `following`：顺移到下一个营业日（原本就是营业日则不动）；
   - `preceding`：倒移到上一个营业日；
   - `modified_following`：先尝试顺移，若顺移结果与原始日期**不在同一个月**，
     则改为倒移到上一个营业日；
3. 对调整后的墙钟时间按 `dst_policy` 定位真实时刻（gap 跳过/顺延、fold 双值/单侧）；
4. 多个**原始实例**调整后落到同一 UTC 时刻时**合并为一条**，`originals` 列出调整前的
   原始墙钟时间（升序、去重）；
5. **EXDATE 仍按调整前的原始墙钟时间排除**——排除周六的原始时间可以阻止它并入周一，
   排除周一（调整后时间）则不影响。

返回的每个实例（无 `source` 字段）：

| 字段 | 说明 |
| --- | --- |
| `local` | 调整后的营业日墙钟时间（ISO-8601） |
| `utc` | UTC 时刻（ISO-8601，末尾 `Z`） |
| `utc_offset` | 该时刻的 UTC 偏移 |
| `dst` | 该时刻是否处于夏令时 |
| `fold` | `0` = 普通/第一次；`1` = 重复墙钟时间的第二次 |
| `originals` | 合并到该时刻的**原始**墙钟时间列表（调整前） |

#### 营业日示例：周末实例合并到周一

周四起每天一条，周六、周日都顺移到周一，合并为一条，`originals` 列出两个原始时间：

```bash
curl -s -X POST http://localhost:8000/business-occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "UTC",
    "start_local": "2025-01-02T09:00:00",
    "rrule": "FREQ=DAILY;COUNT=4",
    "interval_start": "2025-01-01T00:00:00Z",
    "interval_end": "2025-01-10T00:00:00Z"
  }'
```

```json
{
  "timezone": "UTC",
  "count": 3,
  "occurrences": [
    {"local": "2025-01-02T09:00:00", "utc": "2025-01-02T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "originals": ["2025-01-02T09:00:00"]},
    {"local": "2025-01-03T09:00:00", "utc": "2025-01-03T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "originals": ["2025-01-03T09:00:00"]},
    {"local": "2025-01-06T09:00:00", "utc": "2025-01-06T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0,
     "originals": ["2025-01-04T09:00:00", "2025-01-05T09:00:00"]}
  ]
}
```

#### 节假日 + modified_following

2025-02-17（周一）为节假日：周五、周六、周日、周一全部顺移到周二 2 月 18 日（同月，
正常顺移）并合并：

```bash
curl -s -X POST http://localhost:8000/business-occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "UTC",
    "start_local": "2025-02-14T09:00:00",
    "rrule": "FREQ=DAILY;COUNT=4",
    "business_day_convention": "modified_following",
    "holidays": ["2025-02-17"],
    "interval_start": "2025-02-13T00:00:00Z",
    "interval_end": "2025-02-20T00:00:00Z"
  }'
```

```json
{
  "timezone": "UTC",
  "count": 2,
  "occurrences": [
    {"local": "2025-02-14T09:00:00", "utc": "2025-02-14T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "originals": ["2025-02-14T09:00:00"]},
    {"local": "2025-02-18T09:00:00", "utc": "2025-02-18T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0,
     "originals": ["2025-02-15T09:00:00", "2025-02-16T09:00:00", "2025-02-17T09:00:00"]}
  ]
}
```

跨月时 modified_following 改向前：2025-05-31（周六）顺移会进入 6 月，于是倒移回
5 月 30 日（周五），与当天的原始实例合并；6 月 1 日（周日）顺移到 6 月 2 日不跨月：

```bash
curl -s -X POST http://localhost:8000/business-occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "UTC",
    "start_local": "2025-05-30T09:00:00",
    "rrule": "FREQ=DAILY;COUNT=3",
    "business_day_convention": "modified_following",
    "interval_start": "2025-05-29T00:00:00Z",
    "interval_end": "2025-06-03T00:00:00Z"
  }'
```

```json
{
  "timezone": "UTC",
  "count": 2,
  "occurrences": [
    {"local": "2025-05-30T09:00:00", "utc": "2025-05-30T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0,
     "originals": ["2025-05-30T09:00:00", "2025-05-31T09:00:00"]},
    {"local": "2025-06-02T09:00:00", "utc": "2025-06-02T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0,
     "originals": ["2025-06-01T09:00:00"]}
  ]
}
```

#### 营业日调整后再做 DST 定位

纽约 2025-03-08（周六，设置 `weekend_days: [6]` 即仅周六休息）先顺移到 3 月 9 日
（周日），而该日凌晨 `02:30` 不存在；默认 `gap: "skip"` 丢弃，设置
`gap: "shift_forward"` 则顺延到 `03:30`，`originals` 仍记录原始的周六时间：

```bash
curl -s -X POST http://localhost:8000/business-occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "America/New_York",
    "start_local": "2025-03-08T02:30:00",
    "weekend_days": [6],
    "dst_policy": {"gap": "shift_forward"},
    "interval_start": "2025-03-07T00:00:00Z",
    "interval_end": "2025-03-11T00:00:00Z"
  }'
```

```json
{
  "timezone": "America/New_York",
  "count": 1,
  "occurrences": [
    {"local": "2025-03-09T03:30:00", "utc": "2025-03-09T07:30:00Z", "utc_offset": "-04:00", "dst": true, "fold": 0,
     "originals": ["2025-03-08T02:30:00"]}
  ]
}
```

#### preceding 与 EXDATE

`preceding` 把非营业日实例倒移到上一个营业日；EXDATE 按**原始**时间排除（下例排除
周六原始时间后，并入周五的只剩周日原始时间）：

```bash
curl -s -X POST http://localhost:8000/business-occurrences \
  -H 'Content-Type: application/json' \
  -d '{
    "timezone": "UTC",
    "start_local": "2025-01-02T09:00:00",
    "rrule": "FREQ=DAILY;COUNT=4",
    "business_day_convention": "preceding",
    "exdate": ["2025-01-04T09:00:00"],
    "interval_start": "2025-01-01T00:00:00Z",
    "interval_end": "2025-01-10T00:00:00Z"
  }'
```

```json
{
  "timezone": "UTC",
  "count": 2,
  "occurrences": [
    {"local": "2025-01-02T09:00:00", "utc": "2025-01-02T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0, "originals": ["2025-01-02T09:00:00"]},
    {"local": "2025-01-03T09:00:00", "utc": "2025-01-03T09:00:00Z", "utc_offset": "+00:00", "dst": false, "fold": 0,
     "originals": ["2025-01-03T09:00:00", "2025-01-05T09:00:00"]}
  ]
}
```

## 错误响应

所有错误均为统一结构：`{"error": {"code": ..., "message": ..., "details": [...]}}`。

| HTTP | code | 触发场景 |
| --- | --- | --- |
| 400 | `invalid_timezone` | 非合法 IANA 时区（如 `Not/AZone`） |
| 400 | `invalid_rrule` | 规则无法解析：缺 `FREQ`、非法频率、`INTERVAL=0`、`COUNT<=0`、非法日期值、`UNTIL` 格式错误等 |
| 400 | `invalid_interval` | `interval_start >= interval_end` |
| 400 | `invalid_datetime` | 时间字段语法错误，或查询区间缺少 UTC 偏移 |
| 400 | `invalid_business_calendar` | `weekend_days` 为空/含越界值/覆盖全部七天，或 `holidays` 条目非法（仅 `/business-occurrences`） |
| 400 | `too_many_occurrences` | 展开实例数/扫描量超过 `APP_MAX_OCCURRENCES` |
| 422 | `validation_error` | 请求体不是合法 JSON、缺少必填字段或字段值非法（如 `dst_policy` 的 `gap`/`fold`、`business_day_convention` 取值不在允许列表内）（含字段级 details） |

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
│   └── test_api.py   # pytest + TestClient 端到端测试
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
