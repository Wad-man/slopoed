"""Признаки текста для собственного замера качества (Пикабу, Хабр).

Запускается через tools/study.py, описание в docs/quality-research.md. Здесь только чистые
функции: текст и счётчики структуры превращаются в словарь признаков. Сеть не используется,
на диск ничего не пишется.
"""
import collections
import csv
import os
import re
import statistics
import sys
from html.parser import HTMLParser

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import lint  # noqa: E402

WORD = lint.WORD
NUMBER = lint.NUMBER
UNIT_NUMBER = re.compile(
    r"\d+\s?(?:%|руб|₽|долл|евр|тыс|млн|млрд|км|кг|метр|литр|мм|см|мб|гб|сек|мин|час|дн|нед|мес|"
    r"год[ауы]?|лет|раз[а-я]*|шт|чел|градус|квартир|километр)\b", re.I)
YEAR = re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\s?г(?:од|\.|од[ауы])?")
FIRST = re.compile(r"\b(?:я|мне|меня|мо[йяеё]|мои|мы|нам|нас|наш|наша|наше|наши)\b", re.I)
SECOND = re.compile(r"\b(?:ты|тебе|тебя|тво[йяеё]|твои|вы|вам|вас|ваш|ваша|ваше|ваши)\b", re.I)
CAPS = re.compile(r"\b[А-ЯЁ]{3,}\b")
DIALOGUE = re.compile(r"(?m:^\s*[—–-]\s)|«[^»]{3,}»")
EMOJI = lint.EMOJI


class NoSentiment:
    """Замена словаря тональности: каждое слово нейтрально, колонки эмоций остаются нулями."""

    @staticmethod
    def polarity(word):
        return 0


class Sentiment:
    """Полярность слова по интегрированному русскому словарю тональности.

    Словарь содержит леммы; для согласования окончаний от прилагательных и
    глаголов оставляются основы. Слова, подходящие к основам разной полярности,
    считаются нейтральными.
    """

    def __init__(self, path):
        self.stem = {}
        ambiguous = set()
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                polarity = 1 if row.get("integrated_list_pos") else -1 if row.get("integrated_list_neg") else 0
                if not polarity:
                    continue
                word = row["entry"].strip().lower()
                if not word.isalpha():
                    continue
                for stem in Sentiment.stems(word):
                    if stem in self.stem and self.stem[stem] != polarity:
                        ambiguous.add(stem)
                    self.stem.setdefault(stem, polarity)
        for stem in ambiguous:
            self.stem[stem] = 0
        self.cache = {}

    @staticmethod
    def stems(word):
        out = {word}
        for suffix in ("ться", "тись"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                out.add(word[:-len(suffix)])
        if word.endswith("ть") and len(word) - 2 >= 3:
            out.add(word[:-2])
        for suffix in ("ый", "ий", "ой"):
            if word.endswith(suffix) and len(word) - 2 >= 3:
                out.add(word[:-2])
        return out

    def polarity(self, word):
        cached = self.cache.get(word)
        if cached is None:
            cached = self.stem.get(word, 0)
            # Неточное совпадение: основа не короче 5 букв и не дальше 4 букв от конца слова,
            # иначе короткие основы цепляют чужие слова («место», «пробиться»).
            if not cached:
                shortest = max(5, len(word) - 4)
                for size in range(len(word) - 1, shortest - 1, -1):
                    found = self.stem.get(word[:size])
                    if found:
                        cached = found
                        break
            self.cache[word] = cached
        return cached


class HabrHTML(HTMLParser):
    """Текст статьи Хабра и счётчики структуры: код, таблицы, картинки, ссылки."""
    BLOCKS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "tr",
              "figure", "figcaption", "ul", "ol", "table", "hr", "spoiler", "section", "aside"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.counts = collections.Counter()
        self.pre = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "pre":
            self.pre += 1
            self.counts["code_blocks"] += 1
        elif tag == "table":
            self.counts["tables"] += 1
        elif tag == "img":
            source = (attrs.get("src") or "") + " " + (attrs.get("class") or "")
            self.counts["formulas" if ("tex" in source or "formula" in source) else "images"] += 1
        elif tag == "a":
            href = attrs.get("href") or ""
            if href.startswith("http"):
                self.counts["links_in" if "habr.com" in href else "links_ext"] += 1
        elif tag in ("h2", "h3", "h4"):
            self.counts["headings"] += 1
        elif tag in ("ul", "ol"):
            self.counts["lists"] += 1
        elif tag == "li":
            self.counts["list_items"] += 1
        elif tag == "blockquote":
            self.counts["quotes"] += 1
        elif tag == "figure":
            self.counts["figures"] += 1
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "pre":
            self.pre = max(0, self.pre - 1)
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.pre:
            self.counts["code_lines"] += data.count("\n")
        else:
            self.parts.append(data)


def habr_text(html):
    parser = HabrHTML()
    parser.feed(html or "")
    return "".join(parser.parts), parser.counts


def plain_text(text):
    return re.sub(r"[ \t\u00a0]+", " ", text)


def mattr(tokens, window=40):
    if len(tokens) < window:
        return None
    values = []
    for i in range(0, len(tokens) - window + 1):
        values.append(len(set(tokens[i:i + window])) / window)
    return statistics.mean(values)


def featurize(text, title, counts, sentiment):
    """Словарь признаков или None для текста без слов. counts — счётчик структуры (Counter)."""
    text = plain_text(text)
    words = WORD.findall(text)
    if not words:
        return None
    nw = max(len(words), 1)
    k = 1000.0 / nw
    lower = [w.lower().replace("ё", "е") for w in words]
    cyr = [w for w in lower if re.fullmatch(r"[а-яе]+(?:-[а-яе]+)*", w)]

    sentences = []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    for paragraph in paragraphs:
        for a, b in lint.split_sentences(paragraph):
            piece = paragraph[a:b].strip()
            if WORD.search(piece):
                sentences.append(piece)
    lens = [len(WORD.findall(s)) for s in sentences]
    ns = len(lens)

    positives = negatives = flips = 0
    values = []
    for sentence in sentences:
        found = [sentiment.polarity(w) for w in (x.lower() for x in WORD.findall(sentence))]
        plus = sum(1 for p in found if p > 0)
        minus = sum(1 for p in found if p < 0)
        positives += plus
        negatives += minus
        values.append((plus - minus) / (plus + minus) if plus + minus else 0.0)
    slopes = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    for first, second in zip(slopes, slopes[1:]):
        if (first > 0 and second < 0) or (first < 0 and second > 0):
            flips += 1

    mean = statistics.mean(lens) if lens else 0
    deviation = statistics.pstdev(lens) if len(lens) > 1 else 0
    par_lens = [len(WORD.findall(p)) for p in paragraphs] or [0]
    title = plain_text(title or "")
    f = {
        "n_words": len(words),
        "n_sent": ns,
        "n_par": len(paragraphs),
        "num_k": k * len(NUMBER.findall(text)),
        "unit_k": k * len(UNIT_NUMBER.findall(text)),
        "year_k": k * len(YEAR.findall(text)),
        "code_k": k * counts.get("code_blocks", 0),
        "code_lines_k": k * counts.get("code_lines", 0),
        "tables_k": k * counts.get("tables", 0),
        "images_k": k * counts.get("images", 0),
        "video_k": k * counts.get("video", 0),
        "links_k": k * (counts.get("links_ext", 0) + counts.get("links_in", 0)),
        "links_ext_k": k * counts.get("links_ext", 0),
        "formulas_k": k * counts.get("formulas", 0),
        "sent_mean": mean,
        "sent_cv": deviation / mean if mean else 0,
        "sent_short": 100.0 * sum(1 for x in lens if x <= 5) / ns if ns else 0,
        "sent_long": 100.0 * sum(1 for x in lens if x >= 25) / ns if ns else 0,
        "word_len": statistics.mean(len(w) for w in words),
        "long_word": 100.0 * sum(1 for w in words if len(w) >= 13) / nw,
        "mattr": mattr(cyr),
        "headings_k": k * counts.get("headings", 0),
        "lists_k": k * counts.get("lists", 0),
        "list_items_k": k * counts.get("list_items", 0),
        "quotes_k": k * counts.get("quotes", 0),
        "par_len": statistics.mean(par_lens),
        "par_cv": statistics.pstdev(par_lens) / statistics.mean(par_lens) if len(par_lens) > 1 and statistics.mean(par_lens) else 0,
        "pos_k": k * positives,
        "neg_sent_k": k * negatives,
        "flips": flips / ns if ns else 0,
        "excl_k": k * text.count("!"),
        "quest_k": k * text.count("?"),
        "ellipsis_k": k * len(re.findall("…|\\.\\.\\.", text)),
        "first_k": k * len(FIRST.findall(text)),
        "second_k": k * len(SECOND.findall(text)),
        "particles_k": k * len(lint.PARTICLE.findall(text)),
        "not_k": k * len(lint.NEGATION.findall(text)),
        "caps_k": k * len(CAPS.findall(text)),
        "dialogue_k": k * len(DIALOGUE.findall(text)),
        "emoji_k": k * len(EMOJI.findall(text)),
        "title_len": len(title),
        "title_digit": 1 if re.search(r"\d", title) else 0,
        "title_question": 1 if title.strip().endswith("?") else 0,
        "title_first": 1 if FIRST.search(title) else 0,
    }
    return f
