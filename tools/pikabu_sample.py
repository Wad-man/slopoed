"""Отбор постов Пикабу из открытого набора IlyaGusev/pikabu (zstd-поток со stdin).

Запуск (из корня репозитория):
    curl -sL https://huggingface.co/datasets/IlyaGusev/pikabu/resolve/main/00.jsonl.zst \
      | python3 -I tools/pikabu_sample.py --max 60000 > pikabu.jsonl

Скрипт ничего не скачивает: читает stdin, отбирает посты с текстом от 300 знаков
и печатает на stdout JSON-lines с урезанными полями. Комментарии читателей
не сохраняются, только их число. На одного автора не больше пяти постов.
Нужен Python 3.14+ с модулем compression.zstd.
"""
import argparse
import collections
import json
import re
import sys
import time

try:
    from compression import zstd
except ImportError:  # Python младше 3.14
    zstd = None

CYRILLIC = re.compile("[А-Яа-яЁё]")


def thin(data):
    types = collections.Counter(block.get("type", "?") for block in data.get("blocks") or [])
    return {
        "id": data.get("id"),
        "ts": data.get("timestamp"),
        "rating": data.get("rating"),
        "pluses": data.get("pluses"),
        "minuses": data.get("minuses"),
        "title": (data.get("title") or "")[:300],
        "text": (data.get("text_markdown") or "")[:6000],
        "tags": (data.get("tags") or [])[:6],
        "author_id": data.get("author_id"),
        "n_comments": len(data.get("comments") or []),
        "blocks": dict(types),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Отбор постов Пикабу.")
    parser.add_argument("--max", type=int, default=60000, help="Сколько постов сохранить.")
    parser.add_argument("--min-chars", type=int, default=300, help="Минимальная длина текста.")
    parser.add_argument("--per-author", type=int, default=5, help="Максимум постов на одного автора.")
    parser.add_argument("--every", type=int, default=1, help="Брать каждый k-й подходящий пост.")
    args = parser.parse_args(argv)
    if zstd is None:
        raise SystemExit("Нужен Python 3.14+ с модулем compression.zstd")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    out = sys.stdout
    log = sys.stderr

    kept = seen = suitable = 0
    per_author = collections.Counter()
    first_ts = last_ts = None
    started = time.time()
    stream = zstd.ZstdFile(sys.stdin.buffer)
    for line in stream:
        seen += 1
        try:
            data = json.loads(line)
        except ValueError:
            continue
        text = data.get("text_markdown") or ""
        if len(text) < args.min_chars or len(text) > 80000 or not CYRILLIC.search(text):
            continue
        suitable += 1
        if args.every > 1 and suitable % args.every:
            continue
        author = data.get("author_id")
        if per_author[author] >= args.per_author:
            continue
        stamp = data.get("timestamp")
        first_ts = stamp if first_ts is None else min(first_ts, stamp or first_ts)
        last_ts = stamp if last_ts is None else max(last_ts, stamp or last_ts)
        per_author[author] += 1
        out.write(json.dumps(thin(data), ensure_ascii=False) + "\n")
        kept += 1
        if kept % 5000 == 0:
            out.flush()
            log.write("прочитано {}, подходящих {}, сохранено {}, {:.0f} с\n".format(
                seen, suitable, kept, time.time() - started))
            log.flush()
        if kept >= args.max:
            break
    log.write("готово: прочитано {}, подходящих {}, сохранено {}; даты {} – {}\n".format(
        seen, suitable, kept,
        time.strftime("%Y-%m-%d", time.gmtime(first_ts)) if first_ts else "—",
        time.strftime("%Y-%m-%d", time.gmtime(last_ts)) if last_ts else "—"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
