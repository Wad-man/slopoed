#!/usr/bin/env python3
"""Механическая вычитка русских текстов скилла.

Ищет в прозе репозитория то, что легко не заметить при большом объёме текста:
латинские буквы внутри русских слов (омоглифы) и случайные английские слова.

    python3 tools/prooftext.py                # напечатать находки
    python3 tools/prooftext.py --fail-on-hit  # код возврата 1, если находки есть (для CI)

Код в обратных кавычках, блоки разметки и разделы «English» не проверяются. Английские
слова-связки в строках таблиц не считаются находкой: там бывают названия работ.
"""
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PROSE = ["SKILL.md", "README.md", "references", "docs"]
MIXED = re.compile(r"\b(?=[A-Za-zА-ЯЁа-яё]*[а-яё])(?=[A-Za-zА-ЯЁа-яё]*[a-z])[A-Za-zА-ЯЁа-яё]{3,}\b")
STRAY = re.compile(r"\b(?:the|and|with|from|which|this|that)\b", re.I)
HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def prose_files():
    for entry in PROSE:
        path = os.path.join(ROOT, entry)
        if os.path.isfile(path):
            yield path
            continue
        for folder, _, names in os.walk(path):
            for name in sorted(names):
                if name.endswith(".md"):
                    yield os.path.join(folder, name)


def scan(path):
    """Возвращает (файл, номер строки, слово, строка) для каждой находки."""
    in_code, english = False, False
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if line.lstrip().startswith("```"):
                in_code = not in_code
                continue
            heading = HEADING.match(line)
            if heading:
                english = heading.group(1).strip().lower() == "english"
                continue
            if in_code or english:
                continue
            text = re.sub(r"`[^`]*`", "", line)
            # В таблицах бывают названия работ на английском: там ищутся только омоглифы.
            stray = [] if line.lstrip().startswith("|") else STRAY.findall(text)
            for word in MIXED.findall(text) + stray:
                yield os.path.relpath(path, ROOT), number, word, line.strip()[:100]


def main(argv=None):
    found = [hit for path in prose_files() for hit in scan(path)]
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")
    if not found:
        print("Вычитка чистая: омоглифов и случайных английских слов в прозе нет.")
        return 0
    for rel, number, word, line in found:
        print("{}:{}: {!r} | {}".format(rel, number, word, line))
    return 1 if "--fail-on-hit" in (argv or sys.argv[1:]) else 0


if __name__ == "__main__":
    sys.exit(main())
