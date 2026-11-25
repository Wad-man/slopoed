"""Собственный замер: какие свойства текста связаны с оценкой сообщества.

Сначала признаки считаются и сохраняются, затем анализируется готовый дамп (из корня репозитория):
    python3 -I tools/study.py pikabu pikabu.jsonl --root data --lexicon lexicon.csv --dump > features.jsonl
    python3 -I tools/study.py pikabu x --root data --features features.jsonl > report.md

Без --lexicon колонки эмоций остаются нулями. Скрипт читает файлы только внутри рабочей папки
и папок из --root, на диск ничего не пишет: результат печатается на stdout.

Метод: оценка берётся логарифмом; сравниваются верхние 10% с остальными. Одномерно —
ранговая корреляция Спирмена (сырая и с вычитанием контроля объёма и года).
Многомерно — логистическая регрессия на стандартизованных признаках с проверкой
на отложенной трети; вклад группы — падение AUC без неё. Результат — markdown.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time

import numpy as np

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from features import NoSentiment, Sentiment, featurize, habr_text  # noqa: E402
from lint import resolve_inside  # noqa: E402

GROUPS = {
    "Доказательства": ["num_k", "unit_k", "year_k", "code_lines_k", "tables_k", "images_k", "links_ext_k", "formulas_k"],
    "Читаемость": ["sent_mean", "sent_cv", "sent_short", "sent_long", "word_len", "long_word"],
    "Разнообразие": ["mattr"],
    "Структура": ["headings_k", "lists_k", "list_items_k", "quotes_k", "par_len", "par_cv"],
    "Эмоции": ["pos_k", "neg_sent_k", "flips", "excl_k", "quest_k", "ellipsis_k"],
    "Голос": ["first_k", "second_k", "particles_k", "not_k", "caps_k", "dialogue_k"],
    "Заголовок": ["title_len", "title_digit", "title_question", "title_first"],
}
OUTCOMES = {
    # имя: (ключ в дампе, подпись, это доля на просмотр)
    "pluses": ("y", "плюсы", False), "rating": ("y2", "рейтинг (плюсы минус минусы)", False),
    "minuses": ("minuses", "минусы", False), "comments": ("comments", "комментарии", False),
    "rate": ("y", "голоса на просмотр", True), "fav": ("y2", "избранное (Хабр)", False),
    "fav_rate": ("y2", "избранное на просмотр", True),
}
ROOTS = [os.getcwd()]


def load_pikabu(source, sentiment):
    for line in open(resolve_inside(source, ROOTS), encoding="utf-8"):
        row = json.loads(line)
        if row["ts"] < 1483228800:  # с 2017 года
            continue
        blocks = row.get("blocks") or {}
        counts = collections.Counter({"images": blocks.get("image", 0), "video": blocks.get("video", 0)})
        f = featurize(row["text"], row["title"], counts, sentiment)
        if f is None or f["n_words"] < 50:
            continue
        f.update({"y": row["pluses"], "y2": row["rating"], "year": time.gmtime(row["ts"]).tm_year,
                  "comments": row["n_comments"], "minuses": row["minuses"],
                  "media": 1 if (blocks.get("image") or blocks.get("video")) else 0,
                  "author": None, "tags": row.get("tags") or []})
        yield f


def strip_tags(markup):
    return re.sub(r"<[^>]+>", "", markup or "")


def load_habr(folder, sentiment):
    """Статьи Хабра в схеме API kek/v2: textHtml, leadData.textHtml, statistics, author, hubs."""
    for path in sorted(glob.glob(os.path.join(resolve_inside(folder, ROOTS), "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                row = json.load(fh)
        except (OSError, ValueError):
            continue
        if row.get("lang") != "ru" or row.get("isCorporative") or not row.get("id"):
            continue
        text, counts = habr_text(row.get("textHtml") or "")
        title = strip_tags(row.get("titleHtml"))
        lead = strip_tags((row.get("leadData") or {}).get("textHtml"))
        f = featurize("\n".join([title, lead, text]), title, counts, sentiment)
        if f is None or f["n_words"] < 300:
            continue
        statistics = row.get("statistics") or {}
        published = str(row.get("timePublished") or "0000")
        f.update({"y": statistics.get("score") or 0, "y2": statistics.get("favoritesCount") or 0,
                  "year": int(published[:4]), "month": published[:7],
                  "comments": statistics.get("commentsCount") or 0,
                  "minuses": statistics.get("votesCountMinus") or 0, "reads": statistics.get("readingCount") or 0,
                  "media": 1 if counts.get("images") else 0,
                  "author": (row.get("author") or {}).get("rating"),
                  "labels": [label.get("type") for label in row.get("postLabels") or []],
                  "tags": [hub.get("title") for hub in row.get("hubs") or []]})
        yield f


def ranks(values):
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    result = np.empty(len(values))
    result[order] = np.arange(len(values))
    return result


def spearman(a, b, controls=()):
    """Корреляция Спирмена; при controls — ранговая частная (ранги контролей вычитаются линейно)."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if np.ptp(a) == 0 or np.ptp(b) == 0:
        return 0.0
    x, y = ranks(a), ranks(b)
    if len(controls):
        matrix = np.column_stack([np.ones(len(x))] + [ranks(np.asarray(c, dtype=float)) for c in controls])
        x = x - matrix @ np.linalg.lstsq(matrix, x, rcond=None)[0]
        y = y - matrix @ np.linalg.lstsq(matrix, y, rcond=None)[0]
    return float(np.corrcoef(x, y)[0, 1])


def auc_score(scores, labels):
    """Вероятность того, что случайный положительный объект ранжируется выше случайного отрицательного."""
    scores, labels = np.asarray(scores, dtype=float), np.asarray(labels, dtype=float)
    order = np.argsort(scores, kind="mergesort")
    position = np.empty(len(scores))
    sorted_scores = scores[order]
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        position[order[i:j + 1]] = (i + j) / 2.0 + 1
        i = j + 1
    pos = labels.sum()
    neg = len(labels) - pos
    if not pos or not neg:
        return float("nan")
    return float((position[labels == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def fit_logistic(x, y, l2=0.01, steps=800, rate=0.5):
    w = np.zeros(x.shape[1])
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-np.clip(x @ w, -30, 30)))
        w -= rate * (x.T @ (p - y) / len(y) + l2 * w)
    return w


def controversy_table(rows, min_votes=10):
    """Спорность против обсуждаемости: проверка перевернутой U из Chen and Berger (2013).

    Спорность — доля минусов среди голосов. Число комментариев в наборе Пикабу обрезано
    на 11, поэтому обсуждаемость считается только по широкой грани: доля постов,
    набравших 3 и 10 комментариев.
    """
    pluses = np.array([r["y"] for r in rows], dtype=float)
    minuses = np.array([r["minuses"] for r in rows], dtype=float)
    comments = np.array([r["comments"] for r in rows], dtype=float)
    votes = pluses + minuses
    mask = votes >= min_votes
    level = minuses / np.maximum(votes, 1)
    edges = [0, 0.01, 0.03, 0.07, 0.12, 0.2, 0.3, 0.4, 0.55, 1.01]
    print("| Доля минусов | Постингов | Доля с 3+ комментариями | Доля с 10+ комментариями |")
    print("|---|---|---|---|")
    for low, high in zip(edges, edges[1:]):
        select = mask & (level >= low) & (level < high)
        if select.sum() < 50:
            continue
        print("| {:.0f}–{:.0f}% | {} | {:.1f}% | {:.1f}% |".format(
            100 * low, min(100 * high, 100), int(select.sum()),
            100.0 * (comments[select] >= 3).mean(), 100.0 * (comments[select] >= 10).mean()))
    print()


def main(argv=None):
    global ROOTS
    parser = argparse.ArgumentParser(description="Замер связи свойств текста с оценкой сообщества.")
    parser.add_argument("dataset", choices=["pikabu", "habr"])
    parser.add_argument("path", help="Выборка jsonl (Пикабу) или папка с ответами API (Хабр); x при --features.")
    parser.add_argument("--root", action="append", default=[], metavar="DIR", help="Разрешённая папка.")
    parser.add_argument("--lexicon", help="Словарь тональности (нужен только при расчёте признаков).")
    parser.add_argument("--features", help="Готовый дамп признаков (анализ без пересчёта).")
    parser.add_argument("--dump", action="store_true", help="Напечатать признаки в JSON-lines и выйти.")
    parser.add_argument("--outcome", default="auto", help="auto | " + " | ".join(OUTCOMES))
    parser.add_argument("--text-only", action="store_true", help="Только посты без картинок и видео.")
    parser.add_argument("--tag", action="append", default=[], help="Оставить посты с этим тегом (или хабом).")
    parser.add_argument("--top", type=float, default=90, help="Персантиль верхней группы.")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    ROOTS = [os.path.normcase(os.path.realpath(os.getcwd()))] + [os.path.normcase(os.path.realpath(r)) for r in args.root]

    if args.features:
        with open(resolve_inside(args.features, ROOTS), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
    else:
        sentiment = Sentiment(resolve_inside(args.lexicon, ROOTS)) if args.lexicon else NoSentiment()
        rows = list(load_pikabu(args.path, sentiment) if args.dataset == "pikabu" else load_habr(args.path, sentiment))
        if args.dump:
            for row in rows:
                print(json.dumps(row, ensure_ascii=False))
            return 0

    share = int(100 - args.top)
    name = "pluses" if args.outcome == "auto" else args.outcome
    if name not in OUTCOMES:
        raise SystemExit("Неизвестная оценка результата: " + name)
    _, label, rate = OUTCOMES[name]
    if args.text_only:
        rows = [r for r in rows if not r.get("media")]
    for tag in args.tag:
        rows = [r for r in rows if tag in (r.get("tags") or [])]
    if not rows:
        raise SystemExit("После фильтров не осталось текстов")

    if rate:  # доли: голос или избранное на просмотр, чтобы убрать популярность темы
        raw_y = np.array([(max(r["y" if name == "rate" else "y2"] or 0, 0) + 1.0) / ((r.get("reads") or 0) + 1.0) for r in rows])
        y = np.log(raw_y)
        shown = 1000.0 * raw_y
        unit = "на 1000 просмотров"
    else:
        raw_y = np.array([max(r[OUTCOMES[name][0]], 0) for r in rows], dtype=float)
        y = np.log1p(raw_y)
        shown = raw_y
        unit = ""
    year = np.array([r["year"] for r in rows], dtype=float)
    log_words = np.log(np.array([r["n_words"] for r in rows], dtype=float))
    media = np.array([r.get("media") or 0 for r in rows], dtype=float)
    author = np.log1p(np.maximum(np.array([r.get("author") or 0 for r in rows], dtype=float), 0))
    controls = {"log_words": log_words, "year": year, "media": media}
    if args.dataset == "habr":
        controls["author"] = author
    partial_controls = [log_words, year] + ([author] if args.dataset == "habr" else [])

    top = (y >= np.percentile(y, args.top)).astype(float)
    low = y <= np.percentile(y, 50)
    keep = (top == 1) | low

    years = collections.Counter(r["year"] for r in rows)
    print("## Выборка")
    print()
    print("Текстов: {}. Годы: {}. {}: медиана {:.1f}, верхние {}% от {:.1f}, максимум {:.1f} {}.".format(
        len(rows), ", ".join("{}: {}".format(year_, years[year_]) for year_ in sorted(years)),
        label.capitalize(), np.median(shown), share, np.percentile(shown, args.top), shown.max(), unit))
    print()

    names = [n for group in GROUPS.values() for n in group]
    data, used = {}, []
    for feature in names:
        column = np.array([r.get(feature) if r.get(feature) is not None else np.nan for r in rows], dtype=float)
        if np.isnan(column).all() or np.nanmax(column) == np.nanmin(column):
            continue
        if feature.endswith("_k"):  # частоты сильно скошены: берётся логарифм
            column = np.log1p(column)
        data[feature] = np.where(np.isnan(column), np.nanmedian(column), column)
        used.append(feature)

    matrix = np.column_stack([controls[n] for n in controls] + [data[n] for n in used])
    matrix = (matrix - matrix.mean(0)) / np.where(matrix.std(0) == 0, 1, matrix.std(0))
    n_controls = len(controls)
    index = {n: n_controls + i for i, n in enumerate(used)}
    split = np.random.RandomState(20261125).rand(len(rows)) < 0.7
    train_x, test_x = matrix[split], matrix[~split]
    train_y, test_y = top[split], top[~split]

    def test_auc(columns):
        columns = list(range(n_controls)) + columns
        return auc_score(test_x[:, columns] @ fit_logistic(train_x[:, columns], train_y), test_y)

    baseline = test_auc([])
    full = test_auc([index[n] for n in used])
    print("## Многомерная модель: верхние {}% против остальных (проверка на отложенной трети)".format(share))
    print()
    print("AUC базовой модели (длина, год{}) и полной модели (базовые плюс все признаки текста):".format(
        ", рейтинг автора" if args.dataset == "habr" else ", наличие медиа"))
    print()
    print("| Модель | AUC |")
    print("|---|---|")
    print("| Базовая | {:.3f} |".format(baseline))
    print("| Полная | {:.3f} |".format(full))
    print()
    print("| Группа признаков | AUC группы поверх базовой | Падение AUC полной модели без группы |")
    print("|---|---|---|")
    for group, members in GROUPS.items():
        columns = [index[n] for n in members if n in index]
        if not columns:
            print("| {} | нет данных | — |".format(group))
            continue
        rest = [index[n] for n in used if index[n] not in columns]
        print("| {} | {:.3f} | {:+.3f} |".format(group, test_auc(columns), full - test_auc(rest)))
    print()
    weights = fit_logistic(train_x, train_y)
    print("| Признак | Стандартизованный коэффициент в полной модели |")
    print("|---|---|")
    for name in list(controls) + used:
        print("| {} | {:+.3f} |".format(name, weights[list(controls).index(name) if name in controls else index[name]]))
    print()

    print("## Одномерные связи с логарифмом {}".format(label))
    print()
    print("| Признак | Спирмен | Спирмен с контролем объёма и года | AUC: верхние {}% против нижней половины |".format(share))
    print("|---|---|---|---|")
    for name in used:
        column = data[name]
        print("| {} | {:+.3f} | {:+.3f} | {:.3f} |".format(
            name, spearman(column, y), spearman(column, y, partial_controls), auc_score(column[keep], top[keep])))
    print()

    print("## Медианы признаков: нижняя половина против верхних {}%".format(share))
    print()
    print("| Признак | Нижняя половина | Верхние {}% |".format(share))
    print("|---|---|---|")
    for name in used:
        print("| {} | {:.2f} | {:.2f} |".format(name, np.median(data[name][low]), np.median(data[name][top == 1])))
    print()

    if args.dataset == "pikabu" and not args.text_only:
        print("## Спорность и обсуждаемость (посты с 10+ голосами; число комментариев в наборе обрезано на 11)")
        print()
        controversy_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
