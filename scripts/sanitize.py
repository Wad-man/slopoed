#!/usr/bin/env python3
"""slopoed: находит и убирает невидимые символы, оставшиеся после копирования из чата.

Нулевая ширина, знаки направления письма, служебные теги: их не видно, но они
ломают поиск, сравнение версий и подсчёт слов. Кавычки, тире и многоточия
скрипт не трогает: это редакторские решения, а не мусор.

Скрипт ничего не записывает на диск: очищенный текст идёт на стандартный вывод,
отчёт — на стандартный вывод ошибок. Файл заменяет тот, кто запустил команду.

    python3 sanitize.py черновик.md               # только отчёт
    python3 sanitize.py черновик.md --fix         # очищенный текст на stdout
    cat черновик.md | python3 sanitize.py - --fix > чистый.md

Коды возврата: 0 — чисто или текст выдан, 1 — найдено и текст не выдан, 2 — ошибка.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lint import INVISIBLE, UsageError, norm, resolve_inside  # noqa: E402

NAMES = {
    0x00AD: "мягкий перенос", 0x061C: "знак арабского письма", 0x180E: "разделитель гласных", 0x200B: "нулевая ширина пробел",
    0x200C: "нулевая ширина не-соединитель", 0x200D: "нулевая ширина соединитель", 0x200E: "слева направо", 0x200F: "справа налево",
    0x202F: "узкий неразрывный пробел", 0x2060: "соединитель слов", 0x3164: "заполнитель хангыль", 0xFEFF: "неразрывный пробел нулевой ширины",
    0xFFA0: "полуширинный заполнитель хангыль",
}
SPACES = set(range(0x2000, 0x200C)) | {0x202F, 0x205F, 0x3000}


def is_emoji(ch):
    cp = ord(ch)
    return 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF or 0xFE00 <= cp <= 0xFE0F


def describe(cp):
    if cp in NAMES:
        return NAMES[cp]
    if 0x202A <= cp <= 0x202E:
        return "управление направлением"
    if 0x2066 <= cp <= 0x2069:
        return "изоляция направления"
    if 0xE0000 <= cp <= 0xE007F:
        return "служебный тег"
    if 0x2060 <= cp <= 0x2064:
        return "невидимый математический знак"
    return "служебный символ"


def clean(text):
    """Возвращает (очищенный текст, находки). Соединитель внутри эмодзи остаётся.

    Пробел нулевой ширины внутри слова удаляется, между словами превращается
    в обычный пробел: так «сло­во» склеивается, а «слово­другое» не слипается.
    """
    out, found = [], []
    for i, ch in enumerate(text):
        cp = ord(ch)
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if cp == 0x200D and prev and nxt and is_emoji(prev) and is_emoji(nxt):
            out.append(ch)
            continue
        if cp == 0x200B and prev.isalnum() and nxt.isalnum():
            found.append((cp, text.count("\n", 0, i) + 1))
        elif cp in SPACES:
            out.append(" ")
            found.append((cp, text.count("\n", 0, i) + 1))
        elif cp in INVISIBLE:
            found.append((cp, text.count("\n", 0, i) + 1))
        else:
            out.append(ch)
    return "".join(out), found


def report(found, stream):
    if not found:
        stream.write("Чисто: невидимых символов нет.\n")
        return
    by_cp = {}
    for cp, line in found:
        by_cp.setdefault(cp, []).append(line)
    stream.write("Невидимые символы:\n")
    for cp, lines in sorted(by_cp.items()):
        uniq = sorted(set(lines))
        more = ", …" if len(uniq) > 8 else ""
        stream.write("  U+{:04X} {:<34} {:>3} шт.  строки {}{}\n".format(
            cp, describe(cp), len(lines), ", ".join(str(x) for x in uniq[:8]), more))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Находит и убирает невидимые символы в тексте.")
    parser.add_argument("path", help="Файл с текстом или - для stdin.")
    parser.add_argument("--fix", action="store_true",
                        help="Выдать очищенный текст на stdout (отчёт уходит на stderr).")
    parser.add_argument("--root", action="append", default=[], metavar="DIR",
                        help="Дополнительная разрешённая папка (по умолчанию только текущая).")
    args = parser.parse_args(argv)
    try:
        if not sys.stdout.isatty():
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
        roots = [norm(os.getcwd())] + [norm(r) for r in args.root]
        if args.path == "-":
            original = sys.stdin.read()
        else:
            with open(resolve_inside(args.path, roots), encoding="utf-8-sig") as fh:
                original = fh.read()
        cleaned, found = clean(original)
        if args.fix:
            sys.stdout.write(cleaned)
            report(found, sys.stderr)
            return 0
        report(found, sys.stdout)
        if found:
            sys.stdout.write("Запусти с --fix, чтобы получить очищенный текст.\n")
            return 1
        return 0
    except UsageError as exc:
        sys.stderr.write("Ошибка: {}\n".format(exc))
        return 2
    except OSError as exc:
        sys.stderr.write("Ошибка: {}\n".format(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
