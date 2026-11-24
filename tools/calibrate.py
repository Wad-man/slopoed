#!/usr/bin/env python3
"""Калибровка линтера на корпусе «человек против машины».

Показывает, что каждое правило делает на живых текстах: сколько раз срабатывает
у человека, сколько у модели, и как при этом распределяется балл.

    python3 tools/calibrate.py <LLMTrace test.jsonl> --root ПАПКА

Корпус не входит в репозиторий. Скачай test.jsonl из
https://huggingface.co/datasets/iitolstykh/LLMTrace_classification
(первые 80 МБ достаточно) и передай путь. Данные — Apache 2.0.
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import lint  # noqa: E402

DOMAIN_GENRE = {"article": "article", "factual": "formal", "news": "formal",
                "question": "post", "review": "post", "short_form": "post", "story": "post"}
FAMILY = (
    ("o1", "openai-new"), ("o3", "openai-new"), ("gpt", "gpt"), ("GigaChat", "gigachat"), ("gigachat", "gigachat"),
    ("yandex", "yandex"), ("yagpt", "yandex"), ("Qwen", "qwen"), ("google", "gemma"), ("gemma", "gemma"),
    ("Meta-Llama", "llama"), ("unsloth/Llama", "llama"), ("llama-7b", "llama"), ("deepseek", "deepseek"),
    ("CohereForAI", "cohere"), ("01-ai", "yi"), ("databricks", "dbrx"), ("mistralai", "mistral"),
    ("WizardLM", "wizardlm"), ("ruGPT", "rugpt"), ("microsoft/Phi", "phi"), ("Phi-3", "phi"), ("Jamba", "jamba"),
)
GATES = (70, 75, 80, 85, 90)


def inside(raw, roots):
    """Пропускает путь только без «..» и только внутри разрешённых папок."""
    if ".." in raw.replace("\\", "/").split("/"):
        raise lint.UsageError("в пути есть «..»: {}".format(raw))
    full = os.path.normcase(os.path.realpath(raw))
    for root in roots:
        if full == root or full.startswith(root + os.sep):
            return os.path.realpath(raw)
    raise lint.UsageError("файл вне разрешённой папки: {}. Передай --root ПАПКА.".format(raw))


def family(model):
    for key, fam in FAMILY:
        if model.startswith(key) or key in model:
            return fam
    return "other"


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else float("nan")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Калибровка линтера на корпусе LLMTrace.")
    parser.add_argument("corpus", help="Файл test.jsonl из LLMTrace.")
    parser.add_argument("--root", action="append", default=[], metavar="DIR", help="Разрешённая папка с корпусом.")
    args = parser.parse_args(argv)
    try:
        roots = [os.path.normcase(os.path.realpath(os.getcwd()))] + [os.path.normcase(os.path.realpath(r)) for r in args.root]
        path = inside(args.corpus, roots)
        with open(lint.RULES_PATH, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (lint.UsageError, OSError) as exc:
        sys.stderr.write("Ошибка: {}\n".format(exc))
        return 2
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(errors="replace")

    n = collections.Counter()
    by_genre = collections.Counter()
    hit = collections.Counter()
    hit_genre = collections.Counter()
    scores = collections.defaultdict(list)
    metrics = collections.defaultdict(lambda: collections.defaultdict(list))

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.endswith("}"):
                break  # обрезанная последняя строка неполной выгрузки
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("lang") != "ru" or row.get("data_type") not in DOMAIN_GENRE:
                continue
            text = row["text"]
            masked = lint.mask_regions(text)
            starts = [0] + [x.end() for x in re.finditer("\n", text)]
            m = lint.measure(masked)
            if m["words"] < 30:
                continue
            side, genre = row["label"], DOMAIN_GENRE[row["data_type"]]
            n[side] += 1
            by_genre[(side, genre)] += 1
            findings = lint.invisible_findings(text, starts)
            findings += lint.check_rules(lint.active_rules(spec, genre), masked, text, starts, m, len(masked),
                                         spec, spec["reference"][genre])
            value, _ = lint.score(findings, spec)
            scores[side].append(value)
            scores[(side, genre)].append(value)
            if side == "ai":
                scores[("model", family(row.get("model") or ""))].append(value)
            for key in m:
                if isinstance(m[key], (int, float)):
                    metrics[(side, genre)][key].append(m[key])
            for rid in {f["rule"] for f in findings}:
                hit[(side, rid)] += 1
                hit_genre[(side, genre, rid)] += 1
            for gid in {f["group"] for f in findings if f["group"]}:
                hit[(side, gid)] += 1

    print("Текстов от 30 слов: человек {}, машина {}.".format(n["human"], n["ai"]))
    print()
    print("## Что делает каждое правило")
    print()
    print("| правило | у человека | у машины | во сколько чаще у машины |")
    print("|---|---|---|---|")
    table = []
    for rule in spec["rules"]:
        rid = rule["id"]
        h = 100.0 * hit[("human", rid)] / max(n["human"], 1)
        a = 100.0 * hit[("ai", rid)] / max(n["ai"], 1)
        table.append((a / h if h else float("inf"), rid, rule["name"], h, a))
    for lift, rid, name, h, a in sorted(table, key=lambda t: (-t[0], t[1])):
        print("| {} {} | {:.2f}% | {:.2f}% | {} |".format(
            rid, name, h, a, "{:.1f}".format(lift) if lift < 1000 else "только у машины"))
    for gid in ("turn-strong", "turn-weak"):
        h = 100.0 * hit[("human", gid)] / max(n["human"], 1)
        a = 100.0 * hit[("ai", gid)] / max(n["ai"], 1)
        print("| группа {} (хотя бы один оборот) | {:.2f}% | {:.2f}% | {} |".format(
            gid, h, a, "{:.1f}".format(a / h) if h else "—"))

    print()
    print("## Правило по жанрам: доля текстов, где оно сработало")
    print()
    print("| правило | человек: post | article | formal | машина: post | article | formal |")
    print("|---|---|---|---|---|---|---|")
    for _, rid, name, h, a in sorted(table, key=lambda t: t[1]):
        cells = ["{:.1f}%".format(100.0 * hit_genre[(s, g, rid)] / max(by_genre[(s, g)], 1))
                 for s in ("human", "ai") for g in lint.GENRES]
        print("| {} {} | {} |".format(rid, name, " | ".join(cells)))

    print()
    print("## Балл: что не проходит порог")
    print()
    print("| порог | человек не прошёл (ложные тревоги) | машина не прошла |")
    print("|---|---|---|")
    for gate in GATES:
        print("| {} | {:.1f}% | {:.1f}% |".format(
            gate, 100.0 * sum(1 for x in scores["human"] if x < gate) / len(scores["human"]),
            100.0 * sum(1 for x in scores["ai"] if x < gate) / len(scores["ai"])))
    gate = spec["scoring"]["gate"]
    print()
    print("| жанр (порог {}) | человек не прошёл | машина не прошла | медиана балла: человек / машина |".format(gate))
    print("|---|---|---|---|")
    for genre in lint.GENRES:
        h, a = scores[("human", genre)], scores[("ai", genre)]
        if h and a:
            print("| {} | {:.1f}% (n={}) | {:.1f}% (n={}) | {:.0f} / {:.0f} |".format(
                genre, 100.0 * sum(1 for x in h if x < gate) / len(h), len(h),
                100.0 * sum(1 for x in a if x < gate) / len(a), len(a), pct(h, .5), pct(a, .5)))

    print()
    print("## Машина по семействам (порог {})".format(gate))
    print()
    print("| семейство | текстов | не прошло | медиана балла |")
    print("|---|---|---|---|")
    fams = [key[1] for key in scores if key[0] == "model"]
    for fam in sorted(fams, key=lambda name: pct(scores[("model", name)], .5)):
        xs = scores[("model", fam)]
        print("| {} | {} | {:.0f}% | {:.0f} |".format(
            fam, len(xs), 100.0 * sum(1 for x in xs if x < gate) / len(xs), pct(xs, .5)))

    print()
    keys = [r["key"] for r in spec["map"]] + ["traits"]
    for side, title, qs in (("human", "Норма человека по жанрам: p5, p10, p25, p50, p75, p90, p95",
                             (.05, .1, .25, .5, .75, .9, .95)),
                            ("ai", "Те же замеры у машины: p10, p25, p50, p75, p90", (.1, .25, .5, .75, .9))):
        print("## " + title)
        print()
        print("| метрика | post | article | formal |")
        print("|---|---|---|---|")
        for key in keys:
            cells = []
            for genre in lint.GENRES:
                xs = metrics[(side, genre)][key]
                cells.append(", ".join("{:.2f}".format(pct(xs, q)) for q in qs) if xs else "—")
            print("| {} | {} |".format(key, " | ".join(cells)))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
