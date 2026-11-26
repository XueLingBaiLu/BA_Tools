# native/cp312 · 自带原生扩展池

放**为 5.0/5.1（未实测）（Python 3.12）编译**的原生扩展：

- lz4（块压缩，必需）
- texture2ddecoder（含 crn 解码，必需）
- etcpak（写回压 DXT，必需）
- brotli（可选：本作无 .unityweb）

⚠ 原生 .pyd **跨 Python 版本不能互换** —— 这就是要按解释器分池的原因。
池子为空时 runtime.py 会显式回落并告警，⛔ 不静默。

W2 未放实际 wheel：见 ../VERSIONS.md §4（挂账）。
