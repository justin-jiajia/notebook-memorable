# AnkiWeb 登录 / 列出牌组 / 添加笔记 接口研究报告

- 研究日期:2026-10-02
- 研究对象:`https://ankiweb.net`(网页版 AnkiWeb)
- 研究方法:在 Headless Chromium(Playwright)中模拟真实用户操作,再从页面内用同源 `fetch` 重放接口;用临时邮箱注册一次性测试账号
- 测试账号:`ankitesta0f95de1@uberip.com`(mail.tm 临时邮箱),**研究结束后已通过官方"Remove Account"功能删除**(响应 `status=3 FINISHED`)

---

## 0. 结论摘要(TL;DR)

| 问题 | 结论 |
|---|---|
| AnkiWeb 有公开 REST/JSON API 吗? | **没有**。前端是 SvelteKit 单页应用,通过内部 RPC 通信:`POST /svc/<模块>/<方法>`,**Protobuf 二进制**(`Content-Type: application/octet-stream`),不是 JSON。 |
| 登录怎么做? | `POST /svc/account/login`(protobuf: username+password)→ 返回一次性 `token` → 浏览器访问 `https://ankiuser.net/account/ankiuser-login?t=<token>` 在第二个域种 cookie。会话靠名为 `ankiweb` 的 HttpOnly 签名 cookie(两个域各一份)。 |
| 怎么列出 deck? | `POST ankiweb.net/svc/decks/deck-list-info`,返回牌组树(含 deck_id、层级、新卡/复习数)。 |
| 怎么给指定 deck 加笔记? | `POST ankiuser.net/svc/editor/add-or-update`,请求体 `{fields[], tags, add{notetype_id, deck_id}}`。deck_id 来自 `deck-list-info` 或 `editor/get-info-for-adding`。 |
| 是否被允许自动化调用? | **条款明确不允许**:AnkiWeb 服务条款写明不允许浏览器扩展或其他第三方客户端访问,官方建议用 AnkiConnect(见第 8 节)。本报告只用于技术研究;生产用途请走官方同步协议 / AnkiConnect / Anki 桌面端。 |

> ⚠️ 重要的域名拆分:`/svc/account|decks|search|study|shared` 在 **ankiweb.net**;`/svc/editor/*`(添加笔记)在 **ankiuser.net**。在另一个域调用会返回 `404 Not Found`。

---

## 1. 研究过程

### 1.1 环境
- 沙箱:Python 3.13 + Playwright 1.x + Chrome Headless Shell 153。
- 临时邮箱:`api.mail.tm`(创建账号 → 取 JWT → 轮询收件箱),域名 `uberip.com`,AnkiWeb 未拒绝该域名(注册枚举里有 `SPAM_EMAIL` 状态,但此域未触发)。

### 1.2 步骤时间线
1. **抓取登录页**:`/account/login` 的 HTML 只有 SvelteKit 外壳(`<noscript>JavaScript is required.</noscript>`),curl 拿不到表单 → 必须用浏览器。用 Playwright 加载并记录 HAR,共 23 个 `/_app/immutable/*.mjs|css` 文件,以及首个 XHR `POST /svc/account/get-account-status`。
2. **静态分析前端 bundle**(`frontend.*.mjs`,68 KB):
   - 通用 RPC 封装:`fetch(path, {method:'POST', headers:{'Content-Type':'application/octet-stream'}, body: msg.toBinary()})`;状态码 403 时前端直接跳 `/account/login`。
   - 使用 `@bufbuild/protobuf`(protobuf-es)运行时,所有消息用 `makeMessageType("", [{no, name, kind, T}...])` 定义 → 我写脚本(`scripts/extract_proto2.py`)把 **174 个消息类型、13 个枚举、约 70 个接口**还原成 proto 风格 schema(`data/proto_dump.txt`、`data/svc_map.json`)。
   - 登录页逻辑(`nodes/5.*.mjs`):登录成功 → `window.location = <ankiuser域>/account/ankiuser-login?t=<token>`。
3. **在浏览器里走完整注册流程**:`/account/signup` → 条款确认 `/account/terms` → 邮箱验证 `/account/verify-email` → 进入 `/decks`。
4. **邮箱验证**:从 mail.tm 收件箱取到 `noreply@ankiweb.net` 的邮件,在同一浏览器会话里打开验证链接。
5. **用 UI 创建 deck、添加笔记**,同时用注入的 `fetch` 钩子记录每个 `/svc/` 请求/响应的原始字节(hex)。
6. **在页面内重放/扩展**:手写 protobuf 编码器 `AW`(`scripts/pbjs.js`),在已登录页面中直接调用 `search`、`add-or-update`(新增 + 编辑)、`get-note-info`、`select-deck` 等,并测试若干边界情况。
7. **用全新浏览器上下文**测试正确登录、错误密码、不存在用户、未登录访问。
8. **删除测试账号**,清理含凭据的文件。

---

## 2. 架构:两个域、三类 cookie/令牌

```
 ankiweb.net  (登录页、牌组、搜索、学习、共享、账号设置)
 ankiuser.net (登录后的编辑器:/add 页面及 /svc/editor/*)
```
前端用 `page.*.mjs` 里的 `p(path, toUserDomain)` 在两个域间切换(把主机名里的 `ankiweb` ↔ `ankiuser` 互换)。

| 项 | 内容 |
|---|---|
| 会话 cookie | 名 `ankiweb`,`HttpOnly; Secure; Path=/; Max-Age=630720000`(≈20 年),无显式 SameSite(Chromium 视为 Lax)。**在 ankiweb.net 和 ankiuser.net 各发一份**。 |
| cookie 结构 | `base64url(JSON).base64url(HMAC 签名, 43 字符)`;JSON 形如 `{"op":"ck","iat":<秒>,"jv":0,"k":"<随机16字符>","c":1,"t":<秒>}`。(已确认为签名 token,密钥在服务端) |
| 前端标志 cookie | `has_auth=1`(非 HttpOnly),前端用它判断是否已登录 |
| 一次性登录 token | `base64url({"op":"aul","iat":<秒>,"jv":1,"email":"<邮箱>"}).<HMAC>`,用于跨域写 cookie;是否单次有效、有效期多久 **未测试** |
| 邮箱验证 token | 邮件链接 `?t=` 参数,形如 `{"op":"ev","iat":..,"jv":1,"e":"<邮箱>"}.<HMAC>` |

---

## 3. 通用调用约定

```
POST https://<域>/svc/<模块>/<方法>
Content-Type: application/octet-stream
Cookie: ankiweb=...(登录后)
Body: protobuf 编码的请求消息(无请求参数的接口发空 body)
→ 200 + protobuf 响应(无字段则 body 为空)
→ 400 "Bad Request" / 403 "Forbidden" / 404 "Not Found" / 405(GET 请求)——纯文本错误
```

- 没有 CSRF token、没有自定义请求头;依赖 `Content-Type: application/octet-stream`(跨站会触发 CORS 预检)和 cookie 的 SameSite 策略。(跨站场景**没有**实测。)
- 少数 GET 风格接口使用 query string(代码里的 `K()` 封装),主要是 `shared/list-decks`、`shared/list-addons`、`shared/item-info`。
- int32/int64 全部是标准 varint;`deck_id`、`note_id`、`notetype_id` 都是毫秒时间戳(如 `1790901108211`),在 JS 里 < 2^53 可直接用 number。

---

## 4. 登录流程(已在浏览器中验证)

```
[1] POST ankiweb.net/svc/account/get-account-status      (空body)
      ← { logged_in=false }                                  # 页面加载时检查是否已登录
[2] POST ankiweb.net/svc/account/login
      → { username=1:string, password=2:string }
      ← { status=1:enum, token=2:string }
        status: 0 UNKNOWN, 1 AUTHENTICATED, 2 INVALID_USER, 3 INVALID_PASS
        同时 Set-Cookie: ankiweb=...; has_auth=1   (ankiweb.net 域)
[3] GET  https://ankiuser.net/account/ankiuser-login?t=<token>
      ← 303 Location: https://ankiweb.net/account/login?afterAuth=1
        Set-Cookie: ankiweb=...; has_auth=1          (ankiuser.net 域)
[4] 重新加载 login?afterAuth=1 → get-account-status → { logged_in=true }
      → 前端跳转 /decks
      (若此时仍没有 cookie,页面提示 "Cookies are required to log in…")
```

### 实测原始字节
| 场景 | 请求 body (hex) | 响应 (hex) |
|---|---|---|
| 登录(正确) | `0a1b <email 27B> 1216 <password 22B>` | `0801 12<len><token>`(status=1 + token) |
| 登录(错误密码) | 同上 | `0803` → UI 提示 "the password you provided does not match our records" |
| 登录(不存在用户) | 同上 | `0802` → UI 提示 "no account was found with that email address" |

**安全观察**:登录接口对"用户不存在"与"密码错误"返回不同状态 → 可枚举已注册邮箱(这是账号体系的设计取舍,仅作记录)。

### 注册与账号状态门槛(实测顺序)
1. `POST /svc/account/signup` `{username=1, password=2}` → `{status: 1 SUCCESS | 2 USERNAME_INVALID | 3 USERNAME_TAKEN | 4 SPAM_EMAIL, ankiuser_login_token=2}`。前端注册成功后会**紧接着自动调一次 login**。
2. `POST /svc/account/check-terms` → `{needs_to_confirm}`(新号为 `0801`);`confirm-terms {agree_to_terms=true}` → 空。
3. 邮箱验证前:`get-account-status` 返回 `{logged_in=true, redirect_to="/account/verify-email"}`,并且 `decks/deck-list-info` 返回 **403 Forbidden**。
4. `POST /svc/account/verify-email {token}` → `{token_verified=true}`(`0801`);空 token 则返回发送状态 `{sending_status:{updated, progress(1 SENDING/2 DELIVERED/…), email}}`;`resend-verification {hkey}` 最多重发 3 次(页面文案)。
5. 验证完毕后各接口才返回 200。

---

## 5. 列出 deck:`POST ankiweb.net/svc/decks/deck-list-info`

```proto
message DeckListInfoRequest  { int32 minutes_west_of_utc = 1; }   // 浏览器实际发 0800(=0);用于计算"今日"边界
message DeckListInfoResponse {
  DeckTreeNode top_node = 1;          // 虚拟根节点(无名,level=0),children 才是真实顶层 deck
  int64  current_deck_id = 2;
  uint32 collection_size_bytes = 3;
  uint64 media_size_bytes = 4;
}
message DeckTreeNode {
  int64 deck_id = 1;  string name = 2;  repeated DeckTreeNode children = 3;
  uint32 level = 4;   bool collapsed = 5;
  uint32 review_count = 6, learn_count = 7, new_count = 8, intraday_learning = 9,
         interday_learning_uncapped = 10, new_uncapped = 11, review_uncapped = 12,
         total_in_deck = 13, total_including_children = 14;
  bool filtered = 16;
}
```

**实测**(新号):`0a11 1a0f 0801 12 07 "Default" 2001 2801 | 1001` → 一个 `Default`(id=1,level=1)+ `current_deck_id=1`。
创建 `Research::Test Deck` 后,解码结果:

```json
{"top_node":{"children":[{"deck_id":1790901108210,"name":"Research","level":1,
   "children":[{"deck_id":1790901108211,"name":"Test Deck","level":2,"new_count":7,"total_in_deck":7}]}]},
 "current_deck_id":1790901108211,"collection_size_bytes":139264}
```

要点:
- **`name` 是层级中的单段名**(`Test Deck`),完整名是用 `::` 拼接的路径(`Research::Test Deck`);`editor/get-info-for-adding` 里的 `decks[]` 则直接给出完整路径名与 id,**更适合做"名字→id"映射**。
- 空的 `Default` deck 在有其它 deck 时会被隐藏(与桌面版 Anki 行为一致),笔记落入 Default 后会再次出现。
- 相关接口:`decks/create-deck {name}`(`0a13 "Research::Test Deck"`,支持 `::` 自动创建父级)、`decks/rename-deck {deck_id,new_name}→{error}`、`decks/remove-deck {deck_id}`、`decks/select-deck {deck_id}`(设置"当前 deck",影响 `current_deck_id` 与 /add 默认选中项)、`decks/deck-share*`。

---

## 6. 给指定 deck 添加笔记

### 6.1 准备信息:`POST ankiuser.net/svc/editor/get-info-for-adding`(空 body)
```proto
message AddInfo {
  repeated IdName notetypes = 1;    // {id, name}
  repeated IdName decks = 2;        // {id, name(完整 :: 路径)}
  int64 current_deck_id = 3;
  int64 current_notetype_id = 4;
  repeated Field fields = 5;        // 当前 notetype 的字段(ord.val, name, config{font, sticky, rtl, plain_text…})
}
```
新号默认含 6 个 notetype:Basic、Basic (and reversed card)、Basic (optional reversed card)、Basic (type in the answer)、Cloze、Image Occlusion(id 为 `1790901080363…368`,**每个账号不同**,不能硬编码)。

按 notetype 取字段:`POST editor/get-notetype-fields {notetype_id}` → `{fields[]}`(Cloze:`Text`、`Back Extra`)。

### 6.2 添加:`POST ankiuser.net/svc/editor/add-or-update`
```proto
message AddOrUpdateRequest {
  repeated string fields = 1;     // 按字段顺序,值是 HTML
  string tags = 2;                // 空格分隔
  oneof mode {
    AddMode  add  = 3;            // { int64 notetype_id = 1; int64 deck_id = 2; }
    EditMode edit = 4;            // { int64 note_id = 1; }
  }
}
// 响应:空消息,HTTP 200 即成功
```

**UI 添加时抓到的原始请求**(Basic,deck=`Research::Test Deck`):
```
0a1e "What is the capital of France?"          # fields[0]
0a24 "Paris &lt;b&gt;(test note)&lt;/b&gt;"    # fields[1]  —— 注意 contenteditable 把 < > 转成了实体
1211 "research api-test"                        # tags
1a0e 08 aba297d08f34  10 f3fb98d08f34           # add{notetype_id=1790901080363, deck_id=1790901108211}
```
→ 响应 200,空 body;页面显示 "Added"。

### 6.3 在浏览器内直接调用(已验证可用的最小示例)
```js
// 在已登录的 https://ankiuser.net/add 页面里执行(scripts/pbjs.js 提供 AW 帮助函数)
const r = await AW.call('editor/add-or-update', AW.cat(
  AW.str(1, 'API question 1'), AW.str(1, 'API answer 1'),      // fields
  AW.str(2, 'via-api'),                                        // tags
  AW.msg(3, [...AW.int(1, 1790901080363), ...AW.int(2, 1790901108211)])  // add{notetype_id, deck_id}
));  // → {status:200, hex:""}
```
Python+Playwright 的完整驱动脚本见 `scripts/step_api_add.py`。

### 6.4 验证:`POST ankiweb.net/svc/search/search {search}`
```proto
message SearchResponse { repeated NoteHit notes = 1; string error = 2; }   // NoteHit {int64 note_id=1; string joined_fields=2}
```
- 支持完整的 Anki 搜索语法:`deck:"Research::Test Deck"`、`tag:api-test`、`note:Cloze` 等均已验证。
- `joined_fields` 是**去 HTML 标签并解码实体后**、用 `" / "` 拼接的字段预览(如 `你好 世界 & émoji 😀 / hello`)。
- 语法错误**不是** HTTP 错误,而是 200 + `error` 字段(如 `Invalid search: an opening double quote ... `)。
- 响应只有 note_id 和字段预览;没有 deck/tags,要知道笔记在哪个 deck 只能用 `deck:` 条件反查。
- 未测试结果条数上限 / 分页(请求消息里没有分页字段,可能是服务端截断)。

读取单条笔记:`POST editor/get-note-info {note_id}` → `{fields[], notetype_fields[], tags}`;**编辑**:`add-or-update` 用 `edit{note_id}`(已验证:字段与标签更新生效)。

---

## 7. 边界行为测试(均在测试账号上)

| 测试 | 结果 |
|---|---|
| 正常 Basic 笔记 | 200 |
| Cloze 笔记(`{{c1::France}} … {{c2::Paris}}`,第二字段空) | 200,生成 2 张卡(deck 计数 +2) |
| HTML + Unicode + emoji | 200,存储正常 |
| 字段数不对(Basic 只给 1 个字段) | **400 `Bad Request`** |
| `notetype_id` 不存在 | **404 `Not Found`** |
| **`deck_id` 不存在(999999)** | **200,笔记静默落入 `Default`(id=1)** ⚠️ |
| **第一个字段为空** | **200,服务端不校验**(Anki 桌面会拒绝)⚠️ |
| **重复内容(同 Front)** | **200,服务端不去重** ⚠️ |
| `get-note-info` 不存在的 id | 404 |
| `edit{note_id}` 更新 | 200,`get-note-info` 返回新内容 |
| 未登录调用 `deck-list-info` / `search` | **403 Forbidden** |
| 未登录调用 `get-account-status` | 200,空 body(`logged_in=false`) |
| GET 方式请求 `/svc/decks/deck-list-info` | 405 |
| 在 ankiuser.net 调 `decks/*` `search/*` `account/*`;在 ankiweb.net 调 `editor/*` | 404(域名拆分) |

**对"添加到指定 deck"的实际含义**:如果你的程序传错 `deck_id`,**不会报错**而是进了 Default;所以调用前务必用 `get-info-for-adding` 的 `decks[]` 校验 id 存在;空字段/重复要在客户端自己检查。

---

## 8. 条款、合规与风险

AnkiWeb《Terms and Conditions》(`/account/terms`,最后更新 2018-10-17)原文要点:

> "You may access AnkiWeb directly through your browser, and through the synchronization functionality incorporated into the approved clients (Anki, AnkiMobile, AnkiDroid, and AnkiUniversal). **Because other clients can cause problems, AnkiWeb does not currently allow access from browser extensions or other third-party clients.** Instead, please use AnkiConnect, which lets you modify your local connection over a web socket without any negative impact on AnkiWeb."
> "…we reserve the right to suspend or remove your access to the service at our sole discretion."
> 账号 6 个月未访问,其中的 deck 会被清理。

因此:
- 本研究仅在一次性测试账号上、通过真实浏览器、小规模手动触发完成;**未**在浏览器之外(如 requests/curl)带 cookie 访问 `/svc/*`,**未**测试频率限制、暴力破解或越权。
- `/svc/` 是**内部、无文档、随前端发布而变**的接口(bundle 文件名带内容 hash,字段名/编号可能随时变化),且不保证稳定;依赖它的自动化脚本随时可能失效,并有被封号的风险。
- bundle 中还包含一批 `/svc/radmin/*` 管理员接口名(ban-user、become-user 等)。这些是前端代码里的**管理后台接口名,本研究没有调用也不建议探测**。

### 推荐的合规替代方案
| 需求 | 官方/社区推荐做法 |
|---|---|
| 程序化往指定 deck 加卡 | **AnkiConnect**(本地 Anki 桌面版插件,`addNote` 指定 `deckName`、`modelName`),再用 Anki 自带同步推到 AnkiWeb |
| 批量导入 | Anki 桌面版 导入 CSV/TXT/`.apkg`,或用 Python `anki` 库操作本地集合后同步 |
| 手机/桌面互通 | 官方客户端的同步协议(Anki 开源:`ankitects/anki`,`rslib/src/sync`) |

---

## 9. 研究局限
- 注册/登录成功时的**响应 body** 在页面跳转时被浏览器丢弃,未能原样抓到(用 schema 与后续行为推断:`{status=1, token}`);其余接口响应均是原始字节抓取并用提取到的 schema 解码。
- 未测试:`study/*`(学习、deck 限额;`get-deck-limits` 在 ankiweb.net 用空请求体得到 404,可能在另一个域)、`shared/*`(共享牌组)、`rename-deck`/`remove-deck`、改密/改邮箱、token 有效期与一次性、速率限制、跨站 CSRF、多笔记批量、大文件/媒体上传(媒体不走 `/svc/`)。
- 验证用的是 2026-10-02 当天的前端版本(`frontend.CGtXrk30.mjs`,入口 `start.CnY86XB6.mjs`)。

---

## 附录 A:接口总表(从 bundle 提取,✅ = 本次实测)

**ankiweb.net**
| 路径 | 请求 | 响应 | |
|---|---|---|---|
| `/svc/account/get-account-status` | – | `{logged_in, redirect_to}` | ✅ |
| `/svc/account/login` | `{username, password}` | `{status, token}` | ✅ |
| `/svc/account/signup` | `{username, password}` | `{status, ankiuser_login_token}` | ✅ |
| `/svc/account/check-terms` / `confirm-terms` | – / `{agree_to_terms}` | `{needs_to_confirm}` / – | ✅ |
| `/svc/account/verify-email` | `{token}` | `{token_verified \| sending_status}` | ✅ |
| `/svc/account/resend-verification` | `{hkey}` | `{throttled}` | |
| `/svc/account/get-settings` / `update-settings` / `total-media` / `reset-password` | … | … | |
| `/svc/account/remove-account` | `{current_password}` | `{status(3=FINISHED), media_removed}` | ✅ |
| `/svc/decks/deck-list-info` | `{minutes_west_of_utc}` | 牌组树 | ✅ |
| `/svc/decks/create-deck` | `{name}` | – | ✅ |
| `/svc/decks/select-deck` | `{deck_id}` | – | ✅ |
| `/svc/decks/rename-deck` / `remove-deck` | `{deck_id,new_name}` / `{deck_id}` | `{error}` / – | |
| `/svc/decks/deck-share` / `deck-share-info` / `deck-share-state` | … | … | |
| `/svc/search/search` | `{search}` | `{notes[], error}` | ✅ |
| `/svc/study/{study-cards,get-deck-limits,set-deck-limits,upgrade-scheduler}` | … | … | |
| `/svc/shared/*`(list-decks、item-info、list-mine、upload-addon …) | … | … | |

**ankiuser.net**
| 路径 | 请求 | 响应 | |
|---|---|---|---|
| `/account/ankiuser-login?t=` (GET) | token | 303 + Set-Cookie | ✅ |
| `/svc/editor/get-info-for-adding` | – | notetypes/decks/current/fields | ✅ |
| `/svc/editor/get-notetype-fields` | `{notetype_id}` | `{fields[]}` | ✅ |
| `/svc/editor/add-or-update` | 见 6.2 | – | ✅ |
| `/svc/editor/get-note-info` | `{note_id}` | `{fields[], notetype_fields[], tags}` | ✅ |

## 附录 B:产物清单(`/home/user/anki-research/`)
- `REPORT.md` — 本报告
- `data/proto_dump.txt`、`data/svc_map.json` — 从前端 bundle 还原的 protobuf schema 与接口表
- `data/replay1~4.json`、`data/login_flow.json`、`data/*_log.json`、`data/login_page.har` — 原始请求/响应字节(密码、token、cookie 已脱敏)
- `js/` — 下载的前端 bundle(23 个文件)
- `scripts/` — 复现脚本:`pbjs.js`(浏览器内 protobuf 客户端)、`pbdecode.py`(schema 解码器)、`extract_proto2.py`(schema 提取器)、`step_*.py`(各步骤 Playwright 脚本)、`mailtm.py`(临时邮箱)
- `screenshots/` — 各步骤截图(登录页、注册、条款、deck 列表、Add 页、删除账号等)
