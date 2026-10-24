# -*- coding: utf-8 -*-
"""download_models.py —— 一次性下载本地语音识别模型（约 950MB，走 hf-mirror 国内镜像）
下载到 <本目录>/models/：
  paraformer-zh/   阿里 Paraformer 中文识别（sherpa-onnx int8，约 232MB）
  paraformer-en/  阿里 Paraformer 英语识别（sherpa-onnx int8，约 220MB）
  whisper-en-ct2/  faster-whisper small.en（英文后备，约 480MB）
用法：python download_models.py          （重复运行会跳过已下载的文件）
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
MIRROR = "https://hf-mirror.com"

FILES = [
    ("paraformer-zh", "csukuangfj/sherpa-onnx-paraformer-zh-2023-09-14",
     ["model.int8.onnx", "tokens.txt"]),
    ("paraformer-en", "csukuangfj/sherpa-onnx-paraformer-en-2024-03-09",
     ["model.int8.onnx", "tokens.txt"]),
    ("whisper-en-ct2", "Systran/faster-whisper-small.en",
     ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]),
]

def fetch(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print("  已存在，跳过:", os.path.basename(dest))
        return
    print("  下载:", url)
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                sys.stdout.write("\r  %5.1f%%  %d/%d MB" % (got * 100.0 / total, got >> 20, total >> 20))
                sys.stdout.flush()
    os.replace(tmp, dest)
    print()

def main():
    for sub, repo, names in FILES:
        d = os.path.join(MODELS, sub)
        os.makedirs(d, exist_ok=True)
        print("[%s]" % sub)
        for n in names:
            fetch("%s/%s/resolve/main/%s" % (MIRROR, repo, n), os.path.join(d, n))
    print("\n完成！模型目录：", MODELS)

if __name__ == "__main__":
    main()
