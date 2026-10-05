# NoteBloom

AnkiWeb 图片笔记制卡 MVP：服务端维护 AnkiWeb 会话，上传单张图片后识别中英条目，编辑并逐条写入指定 deck。

## 启动

1. 创建环境并安装 `requirements.txt`。
2. 将 `.env.example` 复制为项目根目录 `.env` 并填写 `OPENAI_API_KEY`（也可设置兼容 OpenAI API 的 `OPENAI_BASE_URL`）；应用启动时会自动加载该文件。
3. 在仓库根目录运行 `uvicorn app.main:app --reload`，打开 `http://127.0.0.1:8000`。

AnkiWeb 登录凭据只在登录请求中传给服务端客户端，不会进入前端状态；session 使用内存存储和 httpOnly cookie。服务端复用了 `anki-research/ankiweb.py` 的研究客户端。该客户端访问的是 AnkiWeb 内部接口，请仅在获得授权的个人环境中使用，并注意 AnkiWeb 服务条款。

每条识别结果都有独立的卡片方向，默认是“双向”，也可以改为“中文 → 英文”或“英文 → 中文”。

## 测试

运行 `pytest`。