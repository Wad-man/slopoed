"""Ориентиры для scripts/quality.json: 25-й и 75-й персантиль живых текстов по типам.

Запуск (из корня репозитория), результат на stdout в JSON:
    python3 -I tools/quality_ref.py --habr data/habr --pikabu data/pikabu.jsonl \
        --llmtrace data/test.jsonl --root data > guide.json

Источники живых текстов:
  expert  — личные блоги Хабра, русский, от 300 слов (статьи авторов, не компании);
  story   — текстовые посты Пикабу без картинок и видео, от 50 слов;
  seo     — статьи из корпуса LLMTrace (человеческая половина), от 300 слов.
Для типа other ориентиры не собраны. Профиль считается функцией profile() из scripts/quality.py,
то есть тем же кодом, что увидит пользователь скрипта. Скрипт ничего не скачивает и не записывает.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
from html.parser import HTMLParser

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import quality  # noqa: E402
from lint import resolve_inside  # noqa: E402

QUANTILES = (0.25, 0.75)


class HabrToMarkdown(HTMLParser):
    """Статья Хабра в лёгкий markdown: заголовки, код, таблицы, картинки, ссылки."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.href = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "pre":
            self.out.append("\n\n```\n")
        elif tag == "img":
            self.out.append("\n\n![]({})\n\n".format(attrs.get("src") or ""))
        elif tag == "a" and (attrs.get("href") or "").startswith("http"):
            self.href = attrs.get("href")
            self.out.append("[")
        elif tag in ("h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n{} ".format("#" * int(tag[1])))
        elif tag == "table":
            self.out.append("\n\n")
        elif tag == "tr":
            self.out.append("\n| ")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag == "hr":
            self.out.append("\n\n---\n\n")
        elif tag in ("p", "div", "blockquote", "figure", "figcaption", "ul", "ol", "spoiler"):
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag == "pre":
            self.out.append("\n```\n\n")
        elif tag == "a" and self.href:
            self.out.append("]({})".format(self.href))
            self.href = None
        elif tag in ("h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n")
        elif tag == "table":
            self.out.append("\n\n")

    def handle_data(self, data):
        self.out.append(data)


def strip_tags(markup):
    return re.sub(r"<[^>]+>", "", markup or "").strip()


def habr_texts(folder):
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                row = json.load(fh)
        except (OSError, ValueError):
            continue
        if row.get("lang") != "ru" or row.get("isCorporative") or not row.get("id"):
            continue
        parser = HabrToMarkdown()
        parser.feed(row.get("textHtml") or "")
        title = strip_tags(row.get("titleHtml"))
        lead = strip_tags((row.get("leadData") or {}).get("textHtml"))
        yield "\n\n".join(["# " + title if title else "", lead, "".join(parser.out)])


def pikabu_texts(source):
    with open(source, encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            blocks = row.get("blocks") or {}
            if blocks.get("image") or blocks.get("video"):
                continue
            text = row.get("text") or ""
            if len(text) < 300:
                continue
            yield "# " + (row.get("title") or "") + "\n\n" + text


def llmtrace_articles(source):
    with open(source, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.endswith("}"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("lang") == "ru" and row.get("label") == "human" and row.get("data_type") == "article":
                yield row["text"]


def ranges(texts, min_words, skip=()):
    values = collections.defaultdict(list)
    count = 0
    for text in texts:
        metrics = quality.profile(text)
        if metrics["words"] < min_words:
            continue
        count += 1
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[key].append(value)
    out = {"n": count, "ranges": {}, "share": {}}
    for key, _, _, _ in quality.ROWS:
        if key in skip:
            continue
        column = sorted(values[key])
        # Ряд заполнен меньше чем наполовину (заголовка нет у большинства текстов): ориентир не выводится.
        if len(column) < max(30, count // 2):
            continue
        out["ranges"][key] = [round(column[int(q * (len(column) - 1))], 2) for q in QUANTILES]
        if key in quality.SHARE_KEYS:
            out["share"][key] = round(100.0 * sum(1 for v in column if v > 0) / len(column), 1)
    return out


# В корпусе LLMTrace нет абзацев, заголовков и разметки: ряды, которые на них опираются, для seo не считаются.
PLAIN_TEXT_ROWS = ("lead", "headings", "lists", "tables", "code", "images", "links", "title_len")
# Заголовок поста Пикабу лежит отдельным полем и подставляется в текст как единственный H1, а сама выборка
# по условию отбора без картинок, видео, кода и таблиц: считать эти ряды нельзя.
STORY_ROWS = ("headings", "lists", "tables", "code", "images", "links")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Ориентиры профиля качества по живым текстам.")
    parser.add_argument("--habr", help="Папка с ответами API Хабра.")
    parser.add_argument("--pikabu", help="Выгрузка постов Пикабу (jsonl).")
    parser.add_argument("--llmtrace", help="test.jsonl из LLMTrace.")
    parser.add_argument("--root", action="append", default=[], metavar="DIR", help="Разрешённая папка.")
    args = parser.parse_args(argv)
    roots = [os.path.normcase(os.path.realpath(os.getcwd()))] + [os.path.normcase(os.path.realpath(r)) for r in args.root]
    result = {}
    if args.habr:
        result["expert"] = ranges(habr_texts(resolve_inside(args.habr, roots)), 300)
    if args.pikabu:
        result["story"] = ranges(pikabu_texts(resolve_inside(args.pikabu, roots)), 50, STORY_ROWS)
    if args.llmtrace:
        result["seo"] = ranges(llmtrace_articles(resolve_inside(args.llmtrace, roots)), 300, PLAIN_TEXT_ROWS)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
