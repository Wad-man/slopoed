#!/usr/bin/env python3
"""slopoed: профиль качества текста. Без зависимостей, Python 3.9+.

Считает то, что поддаётся счёту, и показывает рядом типичные значения живых
русских текстов того же типа. Смысл, угол и честность скрипт не видит: их
оценивает читатель по рубрике в references/quality.md.

    python3 quality.py черновик.md --type expert
    python3 quality.py черновик.md --type story --json
    cat черновик.md | python3 quality.py - --type seo

Типы: expert (экспертный материал), story (история, пост), seo (статья под
запрос), other (письмо, продукт, документ).

Ориентиры — 25-й и 75-й персантиль живых текстов этого типа, а не норма.
Выход за ориентир — повод посмотреть, а не ошибка.

Скрипт ничего не записывает на диск. Коды возврата: 0 — профиль построен,
2 — ошибка использования.
"""
import argparse
import json
import os
import re
import statistics
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lint import NUMBER, WORD, UsageError, mask_regions, measure, norm, prose_blocks, read_text, setup_stdout  # noqa: E402

QUALITY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quality.json")
TYPES = ("expert", "story", "seo", "other")
TYPE_NAME = {"expert": "экспертный материал", "story": "история, пост", "seo": "статья под запрос",
             "other": "письмо, продукт, документ"}
UNIT_NUMBER = re.compile(
    r"\d\s?(?:%|руб|₽|долл|евр|тыс|млн|млрд|км|кг|метр|литр|мм|см|мб|гб|сек|мин|час|дн|нед|мес|"
    r"год[ауы]?|лет|раз[а-я]*|шт|чел|градус)\b", re.I)
YEAR = re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\s?г(?:од|\.|од[ауы])?")
DIALOGUE = re.compile(r"(?m:^\s*[—–]\s)|«[^»]{3,}»")
SECOND = re.compile(r"\b(?:ты|тебе|тебя|тво[йяеё]|твои|вы|вам|вас|ваш|ваша|ваше|ваши)\b", re.I)
FENCE = re.compile(r"^```[^\n]*\n(.*?)(?:^```|\Z)", re.S | re.M)
TABLE_LINE = re.compile(r"(?m)^\s*\|.*\|\s*$")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
HEADING = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
LIST_ITEM = re.compile(r"(?m)^\s{0,3}(?:[-*+]|\d+[.)])\s+")
# Заголовок, набранный жирной строкой без решётки, — тот же заголовок, а не первый абзац.
BOLD_TITLE = re.compile(r"\A\s*(?:\*\*|__)([^\n*]{1,200}?)(?:\*\*|__)[ \t]*(?=\r?\n|\Z)")
# Плейсхолдер скилла в квадратных скобках; текст ссылки в квадратных скобках перед круглыми не задевается.
# При подсчёте ориентиров по живым текстам не применяется: там квадратные скобки — часть текста.
PLACEHOLDER = re.compile(r"\[(?!\])([^\[\]\n]{1,160})\](?!\()")

# key, название, формат числа, раздел. Числа «на 1000 слов» и «на 100 слов» отмечены в названии.
ROWS = [
    ("words", "Слов", "int", "Объём и структура"),
    ("sentences", "Предложений", "int", "Объём и структура"),
    ("lead", "Слов в первом абзаце", "int", "Объём и структура"),
    ("headings", "Заголовков", "int", "Объём и структура"),
    ("lists", "Пунктов списков", "int", "Объём и структура"),
    ("tables", "Таблиц", "int", "Объём и структура"),
    ("code", "Блоков кода", "int", "Объём и структура"),
    ("images", "Картинок", "int", "Объём и структура"),
    ("links", "Внешних ссылок", "int", "Объём и структура"),
    ("numbers", "Числа, на 1000 слов", "num1", "Доказательства"),
    ("units", "Числа с единицами и процентами, на 1000 слов", "num1", "Доказательства"),
    ("years", "Годы, на 1000 слов", "num1", "Доказательства"),
    ("sent_mean", "Средняя длина предложения, слов", "num1", "Читаемость"),
    ("short", "Предложения до 4 слов, %", "num1", "Читаемость"),
    ("long", "Предложения от 25 слов, %", "num1", "Читаемость"),
    ("spread", "Разброс длин предложений", "num2", "Читаемость"),
    ("word_len", "Средняя длина слова, букв", "num2", "Читаемость"),
    ("nominal", "Отглагольные существительные, на 100 слов", "num2", "Читаемость"),
    ("mattr", "Разнообразие лексики", "num3", "Разнообразие"),
    ("first", "Первое лицо, на 1000 слов", "num1", "Голос"),
    ("second", "Второе лицо, на 1000 слов", "num1", "Голос"),
    ("dialogue", "Реплики и цитаты, на 1000 слов", "num1", "Голос"),
    ("questions", "Вопросы, на 1000 слов", "num1", "Голос"),
    ("excl", "Восклицания, на 1000 слов", "num1", "Голос"),
    ("particles", "Частицы, на 1000 слов", "num1", "Голос"),
    ("title_len", "Длина заголовка, знаков", "int", "Заголовок"),
]
# Признаки, которые бывают не в каждом тексте: рядом с ориентиром показывается доля живых текстов, где он есть.
SHARE_KEYS = ("headings", "lists", "tables", "code", "images", "links")


def count_structure(raw):
    """Считает то, что живёт в разметке: код, таблицы, картинки, ссылки, заголовки."""
    code_lines = 0
    blocks = 0
    for match in FENCE.finditer(raw):
        blocks += 1
        code_lines += sum(1 for line in match.group(1).split("\n") if line.strip())
    body = FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), raw)
    table_starts = 0
    previous_was_table = False
    for line in body.split("\n"):
        is_table = bool(TABLE_LINE.match(line))
        if is_table and not previous_was_table:
            table_starts += 1
        previous_was_table = is_table
    headings = HEADING.findall(body)
    title = next((text.strip() for level, text in headings if len(level) == 1), None)
    plain = IMAGE.sub("", body)
    external = sum(1 for _, url in LINK.findall(plain) if url.startswith("http") and "]" not in url)
    return {
        "code": blocks, "code_lines": code_lines, "tables": table_starts,
        "images": len(IMAGE.findall(body)), "links": external,
        "headings": len(headings), "lists": len(LIST_ITEM.findall(body)),
        "title": title,
    }


def mattr(tokens, window=40):
    if len(tokens) < window:
        return 0.0
    values = (len(set(tokens[i:i + window])) / window for i in range(len(tokens) - window + 1))
    return statistics.mean(values)


def profile(original):
    """Словарь метрик текста. title_len равен None, если заголовка первого уровня нет.

    Слова считаются так же, как в карте линтера: проза и списки, без заголовков, таблиц и кода.
    """
    raw = BOLD_TITLE.sub(lambda m: "# " + m.group(1).strip(), original)
    bold_title = raw != original
    masked = mask_regions(IMAGE.sub("", raw))
    m = measure(masked)
    structure = count_structure(raw)
    blocks = prose_blocks(masked)
    body = "\n".join(text for _, text, _ in blocks)
    per_k = 1000.0 / max(m["words"], 1)
    tokens = [w.lower().replace("ё", "е") for w in WORD.findall(body)
              if re.fullmatch(r"[а-яё]+(?:-[а-яё]+)*", w.lower())]
    title = structure["title"]
    return {
        "words": m["words"],
        "sentences": m["sentences"],
        "lead": len(WORD.findall(blocks[0][1])) if blocks else 0,
        "headings": structure["headings"],
        "lists": structure["lists"],
        "tables": structure["tables"],
        "code": structure["code"],
        "images": structure["images"],
        "links": structure["links"],
        "numbers": per_k * len(NUMBER.findall(body)),
        "units": per_k * len(UNIT_NUMBER.findall(body)),
        "years": per_k * len(YEAR.findall(body)),
        "sent_mean": m["mean_len"],
        "short": m["short4"],
        "long": m["long25"],
        "spread": m["len_cv"],
        "word_len": m["word_len"],
        "nominal": m["nom100"],
        "mattr": mattr(tokens),
        "first": m["firstperson"],
        "second": per_k * len(SECOND.findall(body)),
        # Реплики «— …» считаются по строкам, а не по склеенным абзацам: в посте диалог пишут в один абзац.
        "dialogue": per_k * len(DIALOGUE.findall(masked)),
        "questions": m["questions"],
        "excl": per_k * body.count("!"),
        "particles": m["particles"],
        "title": title or "",
        "title_len": len(title) if title is not None else None,
        "title_bold": 1 if bold_title else 0,
        "title_question": 1 if title and title.endswith("?") else 0,
        "title_digit": 1 if title and re.search(r"\d", title) else 0,
    }


def fmt(value, kind):
    if value is None:
        return "—"
    if kind == "int":
        return "{:,}".format(int(round(value))).replace(",", "\u00a0")
    return "{:.{d}f}".format(value, d={"num0": 0, "num1": 1, "num2": 2, "num3": 3}[kind]).replace(".", ",")


def guide_text(low, high, kind, share):
    if low is None:
        return ""
    text = fmt(low, kind) if low == high else "{}–{}".format(fmt(low, kind), fmt(high, kind))
    if share is not None:
        text = "{} ({}%)".format(text, fmt(share, "num0"))
    return text


def render(result):
    out = ["slopoed · качество текста · тип: {} · слов: {}".format(TYPE_NAME[result["type"]], fmt(result["words"], "int"))]
    if result["source"]:
        out.append("Ориентир — 25-й и 75-й персантиль живых текстов этого типа: {}. В скобках — доля живых "
                   "текстов, где признак есть.".format(result["source"]))
        out.append("Выход за ориентир — повод посмотреть, а не ошибка: у живого текста около половины "
                   "строк выходит за ориентир в одну или другую сторону.")
    else:
        out.append("Для этого типа ориентиры не собраны: только значения текста.")
    if result["placeholders"]:
        out.append("Плейсхолдеры вида [что именно?] в расчёт не входили: {}.".format(result["placeholders"]))
    section = None
    out.append("")
    for row in result["rows"]:
        if row["section"] != section:
            section = row["section"]
            out.append(section.upper())
        out.append("  {:<46}{:>8}   {:<22}{}".format(
            row["name"][:46], fmt(row["value"], row["kind"]), row["guide"], row["flag"]).rstrip())
    if result["metrics"].get("title_bold"):
        out.append("")
        out.append("Заголовок набран жирной строкой без решётки: для линтера это первое предложение текста. "
                   "Оформи его решёткой, если формат позволяет.")
    if result["title"]:
        notes = []
        if result["metrics"]["title_question"]:
            notes.append("вопрос в заголовке")
        if result["metrics"]["title_digit"]:
            notes.append("цифра в заголовке")
        if result["type"] == "story" and notes:
            out.append("")
            out.append("Заголовок: {} слабее связаны с рейтингом постов. Проверь, не обещает ли заголовок "
                       "больше текста.".format(" и ".join(notes)))
    return "\n".join(out)


def analyze(raw, kind, quality_path=QUALITY_PATH):
    if kind not in TYPES:
        raise UsageError("тип должен быть одним из: {}".format(", ".join(TYPES)))
    with open(quality_path, encoding="utf-8") as fh:
        guide = json.load(fh)
    # Плейсхолдер вида «[сколько?]» — рабочая пометка, а не текст: в расчёт не идёт.
    placeholders = len(PLACEHOLDER.findall(raw))
    metrics = profile(PLACEHOLDER.sub("", raw) if placeholders else raw)
    spec = guide["types"].get(kind) or {}
    ranges = spec.get("ranges", {})
    shares = spec.get("share", {})
    rows = []
    for key, name, number_format, section in ROWS:
        low, high = ranges.get(key, [None, None])
        value = metrics[key]
        flag = ""
        if value is None:
            flag = "заголовка нет" if low is not None else ""
        elif low is not None and value < low:
            flag = "ниже обычного"
        elif high is not None and value > high:
            flag = "выше обычного"
        rows.append({"key": key, "name": name, "kind": number_format, "section": section, "value": value,
                     "guide": guide_text(low, high, number_format, shares.get(key)), "flag": flag})
    return {"type": kind, "words": metrics["words"], "title": metrics["title"], "metrics": metrics, "rows": rows,
            "source": (guide.get("sources") or {}).get(kind, ""), "placeholders": placeholders}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Профиль качества русского текста.")
    parser.add_argument("path", help="Файл с текстом или - для stdin.")
    parser.add_argument("--type", choices=TYPES, default="other", help="Тип текста (по умолчанию other).")
    parser.add_argument("--root", action="append", default=[], metavar="DIR",
                        help="Дополнительная разрешённая папка (по умолчанию только текущая).")
    parser.add_argument("--json", action="store_true", help="Вывод в JSON.")
    args = parser.parse_args(argv)
    try:
        setup_stdout()
        roots = [norm(os.getcwd())] + [norm(r) for r in args.root]
        result = analyze(read_text(args.path, roots), args.type)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(render(result))
        return 0
    except UsageError as exc:
        sys.stderr.write("Ошибка: {}\n".format(exc))
        return 2
    except (OSError, ValueError) as exc:
        sys.stderr.write("Ошибка: {}\n".format(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
